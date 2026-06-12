"""The operator-decision queue — *what is waiting on a human right now*, as a projection.

DOS's single most common dispatch outcome is a no-pick that needs a decision:
a WEDGE verdict, an arbiter refusal, a preflight refusal, an open soak gate.
But those decisions are **ephemeral and scattered** — each is emitted once, as
prose, into one of several on-disk surfaces, then lost. The `dos` CLI is
one-shot, so an operator has no "what needs me right now" view and no way to act
on one. (The DOM plan names the same pain: "the most common dispatch outcome is
the least observable" — DOM defines what the *tokens* mean; this module is the
queue of live *instances* and the way into each one.)

This module is a **read-only projection**, never a store (DOM Design-rules 1 & 4,
and the `dos.reasons` thesis): it stores nothing of its own. `collect_decisions`
joins five sources that already persist their decisions —

    arbiter refusals    <- lane_journal.jsonl `OP_REFUSE` entries (already journaled)
    WEDGE / gate surfaces <- output/next-up/.verdict-<tag>.json envelopes
    preflight refusals  <- a verdict envelope's refusal shape (FQ-410)
    soak / time gates   <- docs/_soaks/index.yaml open windows
    enforcement storms  <- lane_journal.jsonl `OP_ENFORCE` denies, folded through
                           the docs/223 breaker (issue #14 — a hook deny whose only
                           remedy is a human, recurring, IS an operator decision)

— normalizes each into one `Decision`, and renders. The detail/action text is a
projection of the active `ReasonRegistry` (`config.reasons`), exactly as
`dos man` projects it. Delete this module and you lose the reader, not any data.

**Resolver kinds — the LLM-as-judge intersection.** A decision is not always
"waiting on a human." DOS already has judges: `picker_oracle` is a deterministic
judge (it cross-checks a WEDGE's self-reported cause against on-disk truth and
emits `oracle_disagrees`), and `loop_decide` carries a `packet_judge` LLM
verdict. So each `Decision` carries a `resolver_kind`:

    ORACLE  — a deterministic oracle can cross-check / may auto-clear it
              (a STALE_CLAIM / INFLIGHT reason picker_oracle verifies).
    JUDGE   — an LLM adjudicator could rule before a human spends attention.
    HUMAN   — a genuine operator call (answer the open decision, `--force`).

`collect_decisions(..., resolver="HUMAN")` (the default) returns only the rows
that need *you*; `resolver=None` returns everything so the operator can see what
a judge/oracle already handled or could handle.

Pure-stdlib, read-only — never writes, never mutates a registry. The readers are
the only I/O; the normalization + ranking + `next_steps` mapping are pure and are
the unit-test surface (mirrors `picker_oracle.classify` / `timeline.build_timeline`).
"""

from __future__ import annotations

import datetime as dt
import enum
import io
import json
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # pragma: no cover
        pass
elif not isinstance(sys.stdout, io.TextIOWrapper):  # pragma: no cover
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from dos import breaker as _breaker
from dos import config as _config
from dos import lane_journal
from dos import wedge_reason


# ---------------------------------------------------------------------------
# The closed vocabularies — kind (where the decision came from) and resolver
# (who can clear it). Both `str`-valued so they round-trip through `--json`
# without a lookup table (mirrors `gate_classify.Verdict` / `OutcomeKind`).
# ---------------------------------------------------------------------------


class DecisionKind(str, enum.Enum):
    """Which kernel surface emitted the decision."""

    ARBITER_REFUSE = "ARBITER_REFUSE"      # arbitrate() refused a lane lease
    WEDGE = "WEDGE"                        # a /next-up no-pick verdict envelope
    PREFLIGHT_REFUSE = "PREFLIGHT_REFUSE"  # build_context() refused a packet launch
    SOAK_GATE = "SOAK_GATE"                # an OPERATOR_GATE soak window (time-triggered)
    LIVENESS = "LIVENESS"                  # an OP_HALT: a watchdog proposed stopping a
                                           # SPINNING/hung run (docs/82 3b, docs/101 §4)
    ENFORCE_BREAKER = "ENFORCE_BREAKER"    # repeated OP_ENFORCE denies of the SAME edit
                                           # tripped the docs/223 breaker (issue #14): the
                                           # refusal's only remedy is a human, so N
                                           # identical denies fold to ONE HUMAN decision

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class ResolverKind(str, enum.Enum):
    """Who can resolve this decision — the LLM-as-judge axis.

    Derived from the reason's category + the decision kind (see
    `_resolver_for`): the queue default-filters to HUMAN ("what needs me"),
    and `--all` surfaces ORACLE/JUDGE-resolvable rows too.
    """

    ORACLE = "ORACLE"  # a deterministic oracle (picker_oracle) can cross-check / auto-clear
    JUDGE = "JUDGE"    # an LLM adjudicator could rule before a human looks
    HUMAN = "HUMAN"    # a genuine operator call (answer the decision, --force)
    BACKPRESSURE = "BACKPRESSURE"  # NO ONE — it self-resolves. A lane refusal whose
                                   # lever is "wait / re-pick a disjoint lane", which
                                   # the dispatch loop already does automatically
                                   # (route-replan-nolivepicks / backoff-capacity /
                                   # reroute-sibling): "already held", a class/loop
                                   # budget, a soft-ratio or empty-tree overlap. These
                                   # are healthy mutex contention, not a decision — the
                                   # default queue hides them; `--all` shows them.

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# Categories whose decisions a deterministic oracle can cross-check. These mirror
# `picker_oracle.NoPickCause` values: STALE_CLAIM / INFLIGHT-shaped reasons are
# exactly what `picker_oracle._check_stale_claim_real` adjudicates, so they are
# ORACLE-resolvable (the oracle may confirm the claim is fresh and clear it). A
# TRUE_DRAIN is also oracle-checkable (is the backlog really empty?). An
# OPERATOR_GATE is, by name, the human's call.
_ORACLE_CATEGORIES = frozenset({"STALE_CLAIM", "TRUE_DRAIN"})


@dataclass(frozen=True)
class Decision:
    """One pending operator decision — a row in the queue.

    A pure value normalized from one of the four sources. `reason_token` is the
    closed `WedgeReason`/registry token when the source carried one (so the
    detail pane can project its `ReasonSpec`); it is "" for a source that has no
    token (a bare arbiter refusal). `run_id` is the CID correlation key when the
    source recorded one, so "this decision and everything it touched" is a join.
    """

    kind: DecisionKind
    resolver_kind: ResolverKind
    lane: str
    reason_token: str           # a WedgeReason/registry token, or "" if none
    reason_text: str            # the one-line operator-facing reason (prose)
    run_id: str                 # CID correlation id, or "" if the source had none
    age_seconds: int | None     # age of the decision, or None if untimestamped
    source_path: str            # where this decision was read from (for drill-in)
    evidence: tuple[str, ...] = field(default_factory=tuple)
    run_ts: str = ""            # the chained-run dir name (the `dos judge` key), if known
    proposed_command: str = ""  # a host-supplied stop command (a LIVENESS halt proposal),
                                # surfaced as the paste-to-stop emit-and-exit action
    handle: str = ""            # the opaque stop handle of a LIVENESS halt proposal, if any
    dup_count: int = 1          # how many identical rows this one stands in for (see
                                # `_dedup`): a journal that recorded the SAME refusal N
                                # times collapses to one row carrying dup_count=N.

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "resolver_kind": self.resolver_kind.value,
            "lane": self.lane,
            "reason_token": self.reason_token,
            "reason_text": self.reason_text,
            "run_id": self.run_id,
            "run_ts": self.run_ts,
            "age_seconds": self.age_seconds,
            "source_path": self.source_path,
            "evidence": list(self.evidence),
            "proposed_command": self.proposed_command,
            "handle": self.handle,
            "dup_count": self.dup_count,
        }


# ---------------------------------------------------------------------------
# Resolver derivation — the LLM-as-judge classification.
# ---------------------------------------------------------------------------


def _resolver_for(kind: DecisionKind, reason_token: str, config,
                  reason_text: str = "") -> ResolverKind:
    """Decide who can resolve a decision, from its kind + reason category/prose.

    Rules (most-specific first):
      * A SOAK_GATE is always HUMAN — by definition it waits on a human's
        come-back-when-the-window-closes call (an OPERATOR_GATE category).
      * A lane refusal (ARBITER_REFUSE / PREFLIGHT_REFUSE) whose PROSE is a
        backpressure shape ("already held", a class/loop budget, a soft-ratio or
        empty-tree overlap) is BACKPRESSURE — it self-resolves (the loop waits /
        re-picks), so it is not a human decision. Checked on the prose because
        these refusals carry no closed token. An exact-glob hard collision is
        excluded by `_is_backpressure_refusal` and falls through to HUMAN.
      * Otherwise consult the active `ReasonRegistry` for the token's category:
        a STALE_CLAIM / TRUE_DRAIN reason is ORACLE-resolvable (picker_oracle
        can cross-check it); an OPERATOR_GATE reason is HUMAN.
      * A WEDGE with no registry-known token, or an unclassified one, defaults
        to JUDGE — an LLM adjudicator is the right next reader when no
        deterministic oracle owns the category (the `UNCLASSIFIED` shape the
        picker_oracle itself punts on).
      * A bare ARBITER_REFUSE / PREFLIGHT_REFUSE with no token (and not
        backpressure) is HUMAN (the operator picks a lane / --force / fixes it).
    """
    if kind is DecisionKind.SOAK_GATE:
        return ResolverKind.HUMAN
    # Backpressure classification is on the PROSE, and applies only to the lane/
    # packet refusal kinds (a WEDGE/soak is never backpressure). Done before the
    # "no token ⇒ HUMAN" default so a routine "already held" refusal stops being a
    # phantom operator decision (the junk-drawer fix, Layer 1).
    if kind in (DecisionKind.ARBITER_REFUSE, DecisionKind.PREFLIGHT_REFUSE) \
            and _is_backpressure_refusal(reason_text):
        return ResolverKind.BACKPRESSURE
    if not reason_token:
        # No closed token: a bare lane refusal or packet refusal — operator's call.
        return ResolverKind.HUMAN
    category = config.reasons.category_for(reason_token)
    if category in _ORACLE_CATEGORIES:
        return ResolverKind.ORACLE
    if category == "OPERATOR_GATE":
        return ResolverKind.HUMAN
    # MISROUTE / UNCLASSIFIED / anything else with no deterministic owner — an
    # LLM judge is the cheapest next adjudicator before a human is pulled in.
    return ResolverKind.JUDGE


# A closed `reason_class` token is short, single-word, UPPER_SNAKE — never a
# prose sentence. The lane journal's `reason_class` field, however, is sometimes
# written with a whole human sentence (a host emitting an OP_REFUSE with the
# prose reason where the closed token belongs), and a verdict envelope can do the
# same. If we lifted that prose into `reason_token`, `next_steps` would emit
# nonsense like `dos man wedge EVERY CONCURRENCY-FREE LANE …` or
# `python -m dos.drivers.llm_judge EVERY CONCURRENCY-FREE LANE …`. So we admit a
# value as a token only when it LOOKS like one: a member of the active registry
# (any case — the registry normalizes), or a clean UPPER_SNAKE shape (forward-
# compatible with a not-yet-declared token, the same posture as
# `ReasonRegistry.category_for`). Prose falls through to `""` and stays in
# `reason_text` — the prose is still shown, it just doesn't pretend to be a token.
_TOKEN_SHAPE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


def _clean_token(raw: str | None, config) -> str:
    """Return `raw` as an UPPER-cased closed token, or `""` if it is not one.

    Pure. Admits a value iff it is a registry member (case-insensitive) or has the
    clean UPPER_SNAKE token shape; otherwise (prose, whitespace, empty) → `""`.
    """
    if not raw:
        return ""
    candidate = raw.strip().upper()
    if not candidate:
        return ""
    if config.reasons.get(candidate) is not None:
        return candidate
    if _TOKEN_SHAPE.match(candidate):
        return candidate
    return ""


# A curated-cluster scope string is a pre-dos/119 relic. The de-clustering
# (2026-06-02, operator directive "delete the cluster concept, it's bad" — see
# `_job_policy` `concurrent`/`autopick` are empty) made a lane a single DYNAMIC
# handle whose concurrency is gated by tree-disjointness, never a curated set. But
# an OLD verdict envelope (and a host that still writes one) can carry a scope like
#   "apply cluster (AFR, ALO, ANC, APC, CHR, LF, MLP, TFO)"
# or a slash-pathy "a/b/apply". Lifting that verbatim into `Decision.lane` makes
# `next_steps` emit an UNRESOLVABLE action — `/replan --scope apply cluster (AFR,
# ALO, …)` — that the host can only degrade to auto-pick (job finding 2026-06-08:
# the 8-member apply-cluster row with a broken [r]replan). The fix is to normalize
# the lane to its dynamic handle at read time: strip a `cluster (…)` / `(…)`
# decoration and any slash path down to the bare leading handle, so the surfaced
# action is a resolvable `--scope <handle>`. Pure; mirrors `_clean_token`'s posture
# (admit a clean shape, fall back conservatively). A scope that is ALREADY a bare
# handle round-trips unchanged.
_CLUSTER_DECORATION_RE = re.compile(r"\s*\bcluster\b.*$", re.IGNORECASE)
_PAREN_TAIL_RE = re.compile(r"\s*\(.*$")


def _dynamic_lane_handle(raw: str) -> str:
    """Normalize a scope/lane string to its bare dynamic lane handle (dos/119).

    Pure. Strips a curated-cluster relic shape to the leading handle so the
    decision's `/replan --scope <lane>` action is resolvable:

      "apply cluster (AFR, ALO, ANC, …)"  -> "apply"
      "apply (AFR, ALO)"                  -> "apply"
      "a/b/apply"                         -> "apply"
      "apply"                             -> "apply"   (already a handle)

    A string with no decoration round-trips unchanged. An empty/whitespace input
    returns "". The result is the last path segment after de-clustering, so a host
    that namespaces a scope with `/` still resolves to the lane name.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    # Drop a `cluster (...)` tail first (the named relic), then any bare `(...)`
    # tail (a parenthesized member list with no "cluster" word).
    s = _CLUSTER_DECORATION_RE.sub("", s)
    s = _PAREN_TAIL_RE.sub("", s)
    # Reduce a slash path to its final segment (mirrors dispatch_top `_lane_from_env`).
    s = s.split("/")[-1].strip()
    return s


# A reason-string a refusing writer leaves on a *mislabeled* op (docs/139). The
# kernel's own arbiter records a denial as `OP_REFUSE`, but an out-of-tree writer
# (a host dispatch loop, a benchmark fixture) sometimes records the refused lease
# as a plain `OP_ACQUIRE` carrying a `REFUSED: …` reason instead. The reader
# cannot trust the self-labeled op, so it reads the *reason* — the more honest
# signal (the docs/103 "distrust the self-report, read the effect" move, applied
# to the op field itself). Matched at the START of the reason, case-insensitive,
# tolerant of leading whitespace.
_ACQUIRE_REFUSED_RE = re.compile(r"^\s*REFUSED\b", re.IGNORECASE)


def _acquire_refusal_reason(entry: dict) -> str:
    """Return the refusal reason on an ACQUIRE row that is *really* a refusal, else "".

    Pure. An ACQUIRE whose `reason` (or nested `lease.reason`) begins with
    `REFUSED` is a denial mislabeled as an acquire (docs/139). We return the
    reason prose so the caller can lift it into a degraded ARBITER_REFUSE row; a
    genuine successful acquire (any other reason, or none) returns "" and is NOT
    surfaced as a decision — the queue must not fill with every granted lease.
    """
    reason = entry.get("reason")
    if not reason:
        lease = entry.get("lease")
        if isinstance(lease, dict):
            reason = lease.get("reason")
    reason = str(reason or "")
    return reason if _ACQUIRE_REFUSED_RE.match(reason) else ""


# ---------------------------------------------------------------------------
# Backpressure — a refusal whose lever is "wait / re-pick", not "decide".
# ---------------------------------------------------------------------------
#
# Most arbiter refusals are NOT operator decisions: they are healthy mutex
# contention the dispatch loop already resolves on its own (it waits, backs off,
# or re-picks a disjoint lane — the route-replan-nolivepicks / backoff-capacity /
# reroute-sibling branches). Surfacing each as a HUMAN decision is the junk-drawer
# bug (job finding 2026-06-07): a ceiling bump multiplied these and 7 of 8 "pending
# decisions" were backpressure. The kernel's OWN refuse prose says so — e.g. the
# CLASS_BUDGET refuse comment: "the lever is 'wait for a slot to free' — NOT /replan
# ... The work exists and the regions are fine; the class is just full."
#
# We classify by matching the kernel/host refuse strings VERBATIM (grounded in
# arbiter.py / admission.py / the host fanout_state.py, not guessed — same posture
# as `_ACQUIRE_REFUSED_RE`). A backpressure refusal is given `resolver_kind =
# BACKPRESSURE`, so the default `resolver="HUMAN"` queue hides it and `--all` shows
# it. NB: an exact-glob HARD collision is deliberately NOT backpressure — two lanes
# claiming the SAME file is a structural lane-definition fact worth attention when it
# RECURS (the Layer-2 gardener feedback loop, docs/_design/arbiter-refuse-root-
# issues-concept.md); a single instance still self-resolves via supersession, but it
# stays a HUMAN row so a persistent collision is visible rather than silently hidden.
_BACKPRESSURE_REFUSE_RES = (
    # "lane 'X' is already held by a live loop" — wait for the holder to release.
    re.compile(r"already held by a live loop", re.IGNORECASE),
    # GLOBAL_LOOP_CEILING — wait for a loop slot (host fanout_state.py).
    re.compile(r"\bGLOBAL_LOOP_CEILING\b"),
    # CLASS_BUDGET_EXHAUSTED — wait for a class slot (arbiter.py).
    re.compile(r"\bCLASS_BUDGET", re.IGNORECASE),
    # A soft RATIO overlap ("overlap too large (N/M = ..% ... threshold ..%)") —
    # re-pick a disjoint lane; NOT a same-file hard collision. (admission.py via
    # lane_overlap.) Excludes the exact-glob string by construction.
    re.compile(r"overlap too large", re.IGNORECASE),
    # An EMPTY-tree refusal ("unknown blast radius") — transient; the lane gets a
    # tree on the next pick. Re-pick, don't decide. (admission.py)
    re.compile(r"unknown blast radius", re.IGNORECASE),
    # Measured-capacity saturation (proposed Layer-3 token) — wait for a lane.
    re.compile(r"\bLANE_CAPACITY_SATURATED\b"),
)

# An exact-glob HARD collision is the one refusal shape that is NOT backpressure
# (it is a structural lane-definition signal). Matched so it can VETO a
# backpressure classification even if another pattern loosely matched.
_EXACT_GLOB_COLLISION_RE = re.compile(r"exact-glob overlap", re.IGNORECASE)


def _is_backpressure_refusal(reason_text: str) -> bool:
    """True when a refusal self-resolves (wait / re-pick), i.e. is NOT a decision.

    Pure. Matches the kernel/host refuse strings verbatim. An exact-glob hard
    collision is explicitly excluded — it stays a HUMAN row so a recurring
    same-file lane overlap is visible (the actionable signal), not hidden.
    """
    text = reason_text or ""
    if _EXACT_GLOB_COLLISION_RE.search(text):
        return False
    return any(rx.search(text) for rx in _BACKPRESSURE_REFUSE_RES)


# ---------------------------------------------------------------------------
# Supersession — an arbiter refusal is RESOLVED the moment its contention clears.
# ---------------------------------------------------------------------------
#
# An ARBITER_REFUSE is not a point-in-time artifact whose staleness is its age
# (the recency filter, finding #476, handles a verdict envelope's age). A refusal
# is a refusal *relative to a live contended lane*: "lane L cannot run because
# lane B is live" / "lane L is already held". The MOMENT B (or the prior holder of
# L) releases or is scavenged, that refusal is **resolved** — re-requesting L now
# would be admitted. The journal RECORDS those resolution events (RELEASE /
# SCAVENGE), but `_from_lane_journal` historically lifted every REFUSE into a
# standing decision and consulted only age — so a 40-min-old refusal whose blocker
# died 39 min ago still showed as "pending operator decision" forever, even with
# zero live leases (job finding: the 8-row arbiter-refuse junk-drawer, 2026-06-07).
#
# The fix is structural, not age-based: a REFUSE at seq S for lane L (blocked by
# the lanes in its `blocking_trees`) is superseded by any LATER journal entry
# (seq > S) that frees the contention — a RELEASE/SCAVENGE of L or of any blocking
# lane, or a genuine (non-REFUSED) ACQUIRE of L (it got in later, so the refusal
# is moot). This reads the SAME journal the refusals come from; it adds no new
# source and no new config knob (mirrors the `_is_stale` posture — drop a decision
# that is no longer live, keep everything when the signal is absent).

# Ops that FREE a lane's contention when they appear AFTER a refusal. RELEASE and
# SCAVENGE both vacate a held lane; ADOPT/RECONCILE re-seat ownership cleanly. A
# later ACQUIRE is handled separately (it must be a GENUINE acquire, not a refusal
# mislabeled as one — docs/139).
_LANE_FREEING_OPS = frozenset({
    lane_journal.OP_RELEASE,
    lane_journal.OP_SCAVENGE,
})


def _refuse_blocking_lanes(entry: dict) -> set[str]:
    """The set of lanes a REFUSE entry is blocked by — the contention to watch.

    Pure. Prefers the structured `blocking_trees` dict (keyed by colliding lane
    name), the authoritative signal the kernel writes on an OP_REFUSE. Always
    includes the refused lane itself: an "already held by a live loop" refusal
    names no other lane in `blocking_trees`, and is resolved when the prior holder
    of *that same lane* frees it — so a later RELEASE/SCAVENGE of L clears it too.
    """
    lanes: set[str] = set()
    bt = entry.get("blocking_trees")
    if isinstance(bt, dict):
        lanes.update(str(k) for k in bt.keys() if k)
    own = entry.get("lane")
    if own:
        lanes.add(str(own))
    return lanes


def _superseded_refuse_seqs(entries: list[dict]) -> set[int]:
    """Seqs of REFUSE entries whose contention a LATER journal event resolved.

    Pure, single forward pass + a per-refusal lookahead via a precomputed index.
    A REFUSE at seq S for lane L blocked by lanes B is superseded iff some entry
    at seq > S is a RELEASE/SCAVENGE of a lane in (B ∪ {L}), or a genuine ACQUIRE
    of L. `read_all` returns journal order (ascending seq); we use the `seq` field
    for the ordering so a torn/duplicated-seq journal still compares correctly.

    Entries without a usable integer `seq` are skipped for ordering (they cannot be
    placed on the timeline) — a refusal we cannot order is left to the recency
    filter, never silently dropped here.
    """
    # Build, per lane, the sorted list of seqs at which a freeing/acquire event
    # happened for that lane. Then a refusal is superseded iff any watched lane has
    # such an event at a seq strictly greater than the refusal's seq.
    freed_at: dict[str, list[int]] = {}
    acquired_at: dict[str, list[int]] = {}

    def _seq_of(e: dict) -> int | None:
        s = e.get("seq")
        if isinstance(s, bool):  # bool is an int subclass — exclude it
            return None
        return s if isinstance(s, int) else None

    for e in entries:
        s = _seq_of(e)
        if s is None:
            continue
        op = e.get("op")
        lane = str(e.get("lane") or "")
        if not lane:
            continue
        if op in _LANE_FREEING_OPS:
            freed_at.setdefault(lane, []).append(s)
        elif op == lane_journal.OP_ACQUIRE and not _acquire_refusal_reason(e):
            # A GENUINE acquire (not a docs/139 mislabeled refusal): the lane got
            # leased, so any earlier refusal of it is moot.
            acquired_at.setdefault(lane, []).append(s)

    def _has_event_after(lane: str, after: int) -> bool:
        for table in (freed_at, acquired_at):
            for s in table.get(lane, ()):  # short lists; linear scan is fine
                if s > after:
                    return True
        return False

    superseded: set[int] = set()
    for e in entries:
        op = e.get("op")
        # A first-class OP_REFUSE, OR a docs/139 refusal mislabeled as an ACQUIRE
        # (reason begins `REFUSED`): both are arbiter refusals whose contention a
        # later event can resolve.
        is_refuse = op == lane_journal.OP_REFUSE
        is_mislabeled = op == lane_journal.OP_ACQUIRE and bool(_acquire_refusal_reason(e))
        if not (is_refuse or is_mislabeled):
            continue
        s = _seq_of(e)
        if s is None:
            continue
        watch = _refuse_blocking_lanes(e)
        # A refusal is resolved if its OWN lane was later acquired, or ANY watched
        # lane (the blockers + itself) was later freed. NB: a mislabeled-ACQUIRE
        # refusal's own seq is NOT in `acquired_at` (the `not _acquire_refusal_reason`
        # guard above excluded it), so it cannot supersede itself.
        if any(_has_event_after(lane, s) for lane in watch):
            superseded.add(s)
    return superseded


# ---------------------------------------------------------------------------
# Enforcement storms — the missing escalation half of a hook deny (issue #14).
# ---------------------------------------------------------------------------
#
# The pretool hook journals every deny as an OP_ENFORCE record (docs/189 §C4) and
# `dos helped` folds it — but a SELF_MODIFY deny never climbed the trust ladder.
# Its own documented unblock is OPERATOR-ONLY (edit between loop runs / the armed
# override window; the PreToolUse ABI deliberately gives the agent no force), so a
# loop retrying the same refused edit burns turns silently: 21 identical refused
# Writes on one runtime file, and `dos decisions` said "(none pending)" all day.
# A refusal whose only remedy is a human, recurring N times from the same holder,
# is the textbook HUMAN-rung decision — and the kernel already ships the "this
# keeps tripping; stop and escalate the rung" primitive (`dos.breaker`, docs/223).
# This fold wires the two together: same (holder, target) denies thread through
# the breaker counters, and an OPEN circuit surfaces as ONE pending decision.
#
# Resolution is structural, twice over: a docs/296 override-admit for the same
# (holder, target) is a SUCCESS (`record_success` resets the consecutive count —
# the operator armed the window and the edit went through), and a storm that
# simply stopped ages out via the same recency filter every point-in-time refusal
# obeys. The deny itself stays unconditional — this adds only the escalation half
# its own doctrine names. Advisory: surfacing takes no lease and stops no run.

# The one reason class folded today: SELF_MODIFY is the deny whose remedy is
# operator-only by design (the storm shape the issue documents). Other deny
# classes self-resolve (a collision clears when the lease frees) — widening this
# set is a deliberate decision, not a default (under-match, like `_clean_token`).
_ENFORCE_STORM_TOKENS = frozenset({"SELF_MODIFY"})

# Trip on 3 identical denies in a row — the docs/223 default consecutive
# threshold, escalating HUMAN. `max_total` is OFF (0): the total counter never
# resets, so a long-resolved storm would otherwise re-trip forever; "the same
# holder was refused again much later" is a NEW consecutive run anyway.
_ENFORCE_STORM_POLICY = _breaker.BreakerPolicy(
    max_consecutive=3, max_total=0, on_trip=_breaker.Escalation.HUMAN)

# The runtime files a SELF_MODIFY reason names, e.g. "… own running code
# (src/dos/arbiter.py) — refusing …" (`self_modify.SelfModifyPredicate` and its
# Go twin emit the same sentence). The parenthetical is the TARGET key the fold
# groups on; a reason without it (the arm-file perimeter deny) falls back to the
# whole reason text — identical retries still group together.
_ENFORCE_TARGET_RE = re.compile(r"own running\s+code \(([^)]*)\)")


def _enforce_reason_class(entry: dict) -> str:
    """The closed reason token on an ENFORCE entry, top-level or nested. Pure.

    Python's `enforce_entry` lifts `reason_class` to the top level; the Go
    fast-path writer historically left it only inside the nested `proposal`
    body. Read both so a Go-written deny is never invisible to the fold.
    """
    rc = entry.get("reason_class")
    if not rc:
        proposal = entry.get("proposal")
        if isinstance(proposal, dict):
            rc = proposal.get("reason_class")
    return str(rc or "").strip().upper()


def _enforce_target(entry: dict) -> str:
    """The edit target an ENFORCE deny refused — the fold's grouping key. Pure."""
    reason = str(entry.get("reason") or "")
    m = _ENFORCE_TARGET_RE.search(reason)
    if m:
        return m.group(1).strip()
    return reason


def _enforce_decision_tag(entry: dict) -> str:
    """deny / override-admit / "" for an ENFORCE entry, from its recorded shape. Pure.

    Prefers the nested `proposal.decision` (both writers record it); falls back
    to the lifted `intervention` (BLOCK = a deny) for a minimal/foreign record.
    """
    proposal = entry.get("proposal")
    if isinstance(proposal, dict) and proposal.get("decision"):
        return str(proposal.get("decision"))
    if str(entry.get("intervention") or "").strip().upper() == "BLOCK":
        return "deny"
    return ""


def _from_enforce_storms(config, *, now: dt.datetime | None = None) -> list[Decision]:
    """Recurring hook denies of the SAME edit, escalated through the breaker.

    Reads the same WAL as `_from_lane_journal` (the ENFORCE records are already
    there; no new source, no new store). For each (holder, target) pair whose
    reason class is in `_ENFORCE_STORM_TOKENS`, the records thread through the
    docs/223 breaker IN JOURNAL ORDER — a deny is `record_failure`, a docs/296
    override-admit is `record_success` — and a final OPEN circuit lifts ONE
    Decision naming the target and the count. A single isolated deny (or two)
    stays under the threshold and raises nothing.
    """
    path = config.paths.lane_journal
    try:
        entries = lane_journal.read_all(path)
    except Exception:
        return []
    # (holder, target) -> the threaded breaker counts + the latest deny entry.
    counts: dict[tuple[str, str], _breaker.BreakerCounts] = {}
    last_deny: dict[tuple[str, str], dict] = {}
    deny_total: dict[tuple[str, str], int] = {}
    for e in entries:
        if e.get("op") != lane_journal.OP_ENFORCE:
            continue
        if _enforce_reason_class(e) not in _ENFORCE_STORM_TOKENS:
            continue
        tag = _enforce_decision_tag(e)
        key = (str(e.get("holder") or ""), _enforce_target(e))
        state = counts.get(key, _breaker.BreakerCounts())
        if tag == "deny":
            counts[key] = _breaker.record_failure(state, _ENFORCE_STORM_POLICY).counts
            last_deny[key] = e
            deny_total[key] = deny_total.get(key, 0) + 1
        elif tag == "override-admit":
            # The operator's armed window let the edit through — the storm's
            # sustained-failure signal cleared (the structural "resolving clears it").
            counts[key] = _breaker.record_success(state, _ENFORCE_STORM_POLICY).counts
    out: list[Decision] = []
    for key, state in counts.items():
        verdict = _breaker.classify(state, _ENFORCE_STORM_POLICY)
        if not verdict.is_open:
            continue
        holder, target = key
        e = last_deny.get(key, {})
        n = state.consecutive
        tool = str(e.get("tool") or e.get("lane") or "")
        token = _enforce_reason_class(e) or "SELF_MODIFY"
        reason_text = (
            f"agent {holder or '?'} has been refused {n}x editing {target} "
            f"({token}) — the edit needs you: make it between loop runs, arm the "
            f"override window, or stop the loop"
        )
        out.append(Decision(
            kind=DecisionKind.ENFORCE_BREAKER,
            # The breaker's own trip names the rung (docs/223 Escalation.HUMAN).
            resolver_kind=ResolverKind.HUMAN,
            lane=tool,
            reason_token=token,
            reason_text=reason_text,
            run_id=str(e.get("run_id") or ""),
            # The decision's clock is the LATEST deny: a storm that stopped ages
            # out via the recency filter like any other point-in-time refusal.
            age_seconds=_age_seconds(e.get("ts"), now=now),
            source_path=str(path),
            evidence=(
                f"journal seq #{e.get('seq', '?')} (latest deny)",
                f"{deny_total.get(key, n)} denies for holder={holder or '?'}",
                verdict.reason,
            ),
            dup_count=n,
        ))
    return out


# ---------------------------------------------------------------------------
# Time helpers.
# ---------------------------------------------------------------------------


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_iso(ts: str | None) -> dt.datetime | None:
    """Best-effort parse of an ISO-8601 stamp (tolerant of a trailing Z)."""
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _age_seconds(ts: str | None, *, now: dt.datetime | None = None) -> int | None:
    t = _parse_iso(ts)
    if t is None:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    delta = (now or _now()) - t
    return max(0, int(delta.total_seconds()))


# ---------------------------------------------------------------------------
# Source readers — each returns a list[Decision]. Read-only; the only I/O here.
# Every reader degrades to [] on a missing/malformed source so the queue never
# crashes on a torn file (the same defensive posture as picker_oracle's loaders).
# ---------------------------------------------------------------------------


def _from_lane_journal(config, *, now: dt.datetime | None = None) -> list[Decision]:
    """Arbiter refusals + watchdog halt proposals — two ops the journal records.

    `lane_journal` appends an `OP_REFUSE` for every arbiter refusal and an
    `OP_HALT` for every watchdog stop proposal (both "recorded, but do NOT mutate
    lease state"), so the journal IS the durable feed for both. We read the tail
    and lift each into a Decision:

      * `OP_REFUSE` -> an ARBITER_REFUSE row (a denied lane request). Carries
        `lane`, `reason` (prose), `ts`, and may carry `run_id` / `reason_class`.
      * `OP_HALT`   -> a LIVENESS row (docs/101 §4): a SPINNING / hung-past-budget
        run a watchdog proposed stopping. Carries the opaque `handle`, the proposed
        `command` (the paste-to-stop), `lane`/`run_id` for correlation, and the
        halt `reason`. resolver_kind is ORACLE — liveness is a DETERMINISTIC
        verdict (the ORACLE rung, like picker_oracle), so the proposal was
        oracle-adjudicated even though enacting the stop is a human/driver act.

    A REFUSE whose contention a LATER journal event already cleared
    (`_superseded_refuse_seqs`: its blocker, or the prior holder of its own lane,
    was released/scavenged, or its lane was later acquired) is **not** emitted — it
    is a resolved decision, not a pending one. This is the structural counterpart
    to the age-based recency filter: a refusal's staleness is its *contention being
    over*, not its clock age.
    """
    path = config.paths.lane_journal
    try:
        entries = lane_journal.read_all(path)
    except Exception:
        return []
    superseded = _superseded_refuse_seqs(entries)
    out: list[Decision] = []
    for e in entries:
        op = e.get("op")
        if op == lane_journal.OP_REFUSE:
            # Skip a refusal whose contention a later RELEASE/SCAVENGE/ACQUIRE
            # already resolved — it is no longer waiting on anyone.
            _seq = e.get("seq")
            if isinstance(_seq, int) and not isinstance(_seq, bool) and _seq in superseded:
                continue
            # Admit `reason_class` as a token only if it LOOKS like a closed token
            # (a host sometimes writes prose here); prose stays in `reason_text`.
            token = _clean_token(e.get("reason_class"), config)
            reason_text = str(e.get("reason") or "lane refused")
            lane = _dynamic_lane_handle(str(e.get("lane") or ""))
            run_id = str(e.get("run_id") or e.get("root_id") or "")
            age = _age_seconds(e.get("ts"), now=now)
            kind = DecisionKind.ARBITER_REFUSE
            out.append(Decision(
                kind=kind,
                resolver_kind=_resolver_for(kind, token, config, reason_text),
                lane=lane,
                reason_token=token,
                reason_text=reason_text,
                run_id=run_id,
                age_seconds=age,
                source_path=str(path),
                evidence=(f"journal seq #{e.get('seq', '?')}",),
            ))
        elif op == lane_journal.OP_HALT:
            # A watchdog stop proposal. The proposed command is carried in
            # `reason_text` so render_detail/next_steps can surface it as the
            # paste-to-stop action; the handle + run go to correlation fields.
            handle = str(e.get("handle") or "")
            command = str(e.get("command") or "")
            lane = str(e.get("lane") or "")
            run_id = str(e.get("run_id") or "")
            age = _age_seconds(e.get("ts"), now=now)
            halt_reason = str(e.get("reason") or "watchdog proposed stop")
            # The detail prose leads with WHY (the liveness reason), and the
            # evidence carries the handle + the proposed command verbatim.
            evidence = [f"handle={handle or '?'}", f"journal seq #{e.get('seq', '?')}"]
            if command:
                evidence.append(f"proposed: {command}")
            out.append(Decision(
                kind=DecisionKind.LIVENESS,
                resolver_kind=ResolverKind.ORACLE,
                lane=lane,
                reason_token="",  # liveness carries no WedgeReason token
                reason_text=halt_reason,
                run_id=run_id,
                age_seconds=age,
                source_path=str(path),
                evidence=tuple(evidence),
                proposed_command=command,
                handle=handle,
            ))
        elif op == lane_journal.OP_ACQUIRE:
            # The reader-side defense (docs/139): an ACQUIRE whose reason says
            # `REFUSED` is a denial a writer mislabeled as an acquire — surface it
            # as a *degraded* ARBITER_REFUSE so a refusal hidden under the wrong op
            # is not silently invisible to the operator. We distrust the op and
            # read the reason (docs/103). A genuine acquire returns "" here and is
            # skipped — the queue stays the "what needs me" projection, not a log
            # of every granted lease.
            recovered = _acquire_refusal_reason(e)
            if not recovered:
                continue
            # A mislabeled refusal (docs/139) is still an arbiter refusal — drop it
            # too when a later event frees its lane / it was later acquired.
            _seq = e.get("seq")
            if isinstance(_seq, int) and not isinstance(_seq, bool) and _seq in superseded:
                continue
            token = _clean_token(e.get("reason_class"), config)
            lane = _dynamic_lane_handle(str(e.get("lane") or ""))
            run_id = str(e.get("run_id") or e.get("root_id") or "")
            age = _age_seconds(e.get("ts"), now=now)
            kind = DecisionKind.ARBITER_REFUSE
            out.append(Decision(
                kind=kind,
                resolver_kind=_resolver_for(kind, token, config, recovered),
                lane=lane,
                reason_token=token,
                reason_text=recovered,
                run_id=run_id,
                age_seconds=age,
                source_path=str(path),
                # The evidence MARKS this as recovered from a mislabeled op, so an
                # operator can tell it apart from a first-class OP_REFUSE row.
                evidence=(
                    f"journal seq #{e.get('seq', '?')}",
                    "recovered: refusal logged under op=ACQUIRE (docs/139)",
                ),
            ))
    return out


# A verdict envelope is a refusal when it is a no-pick / blocked shape. Mirrors
# `preflight._envelope_refusal` exactly (one definition, two readers would drift)
# — but we re-implement the read here against the SAME keys rather than importing
# preflight (which pulls a heavier dependency chain). Launchable = LIVE/ACCEPT/absent.
_LAUNCHABLE_VERDICTS = frozenset({"", "LIVE", "ACCEPT"})


def _envelope_is_refusal(env: dict) -> tuple[bool, str]:
    """(is_refusal, short_reason) for a `.verdict-<tag>.json` envelope.

    Kept in lockstep with `preflight._envelope_refusal` / `wedge_reason.is_refusal`.
    """
    verdict = str(env.get("verdict") or "").strip().upper()
    reason_class = env.get("reason_class")
    all_clear = bool(env.get("all_clear"))
    if env.get("do_not_render"):
        return (True, f"do_not_render verdict={verdict or '?'}")
    if env.get("blocked") and not all_clear:
        return (True, f"blocked verdict={verdict or '?'}")
    if verdict and verdict not in _LAUNCHABLE_VERDICTS:
        return (True, f"verdict={verdict}")
    if reason_class is not None and wedge_reason.is_refusal(str(reason_class)):
        return (True, f"reason_class={reason_class}")
    return (False, "")


def _from_verdict_envelopes(config, *, now: dt.datetime | None = None) -> list[Decision]:
    """WEDGE / gate surfaces + preflight refusals — the verdict envelopes.

    `output/next-up/.verdict-<tag>.json` is written for every /next-up run; a
    refusal-shaped envelope (WEDGE / DRAIN / do_not_render / blocked) is a
    pending decision. We classify the kind by the envelope's own signals: an
    envelope flagged `do_not_render` / `blocked` is the PREFLIGHT_REFUSE shape
    (the packet won't launch); a plain WEDGE/DRAIN verdict is the WEDGE shape.
    """
    next_dir = config.paths.next_packets
    if not next_dir.exists():
        return []
    out: list[Decision] = []
    for p in sorted(next_dir.glob(".verdict-*.json")):
        try:
            env = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(env, dict):
            continue
        is_refusal, short = _envelope_is_refusal(env)
        if not is_refusal:
            continue
        # Same token-hygiene as the journal reader: a `reason_class` that is prose
        # (or absent) yields `""`, so `next_steps` never emits a `man wedge <prose>`.
        token = _clean_token(env.get("reason_class"), config)
        verdict = str(env.get("verdict") or "").strip().upper()
        reason_text = str(env.get("reason") or short or verdict or "no-pick")
        # The tag encodes the lane/scope loosely; prefer an explicit scope label.
        # Normalize to the bare dynamic handle (dos/119) so a curated-cluster relic
        # scope ("apply cluster (AFR, …)") cannot surface an unresolvable action.
        scope = env.get("scope")
        if isinstance(scope, dict):
            lane = _dynamic_lane_handle(str(scope.get("lane") or ""))
        elif isinstance(scope, str):
            lane = _dynamic_lane_handle(scope)
        else:
            lane = _dynamic_lane_handle(str(env.get("lane") or ""))
        run_id = str(env.get("run_id") or env.get("root_id") or "")
        # The chained-run dir name, if the envelope recorded it — this is the
        # `dos judge` key. Often absent on a raw verdict envelope; the [j] action
        # gates on it (a judge with no run_ts degrades to a sweep hint).
        run_ts = str(env.get("run_ts") or "")
        age = _age_seconds(env.get("generated_at") or env.get("ts"), now=now)
        # do_not_render / blocked => the packet was refused at preflight; a bare
        # WEDGE/DRAIN verdict is the no-pick gate surface.
        if env.get("do_not_render") or (env.get("blocked") and not env.get("all_clear")):
            kind = DecisionKind.PREFLIGHT_REFUSE
        else:
            kind = DecisionKind.WEDGE
        out.append(Decision(
            kind=kind,
            resolver_kind=_resolver_for(kind, token, config, reason_text),
            lane=lane,
            reason_token=token,
            reason_text=reason_text[:300],
            run_id=run_id,
            run_ts=run_ts,
            age_seconds=age,
            source_path=str(p),
            evidence=(f"envelope {p.name}", short) if short else (f"envelope {p.name}",),
        ))
    return out


def _from_soaks(config) -> list[Decision]:
    """Soak / time gates — open windows in docs/_soaks/index.yaml.

    A soak gate is the time-triggered decision: a phase whose `soak_until` is
    still in the future is gating its lane now and will need a re-pick when the
    window closes. We surface windows open *as of today* (closed ones are not
    pending). The index shape isn't pinned in the package, so we read it
    defensively — a list of entries, or a dict keyed by id — and look for a
    `soak_until` / `deadline` / `until` date on each.
    """
    path = config.paths.soaks_index
    if not path.exists():
        return []
    try:
        import yaml  # type: ignore
    except ImportError:
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []

    # Normalize to an iterable of (key, entry-dict).
    items: list[tuple[str, dict]] = []
    if isinstance(data, dict):
        # Either {id: {...}} or {"soaks": [...]} / {"entries": [...]}.
        seq = data.get("soaks") or data.get("entries")
        if isinstance(seq, list):
            for i, e in enumerate(seq):
                if isinstance(e, dict):
                    items.append((str(e.get("id") or e.get("phase") or i), e))
        else:
            for k, e in data.items():
                if isinstance(e, dict):
                    items.append((str(k), e))
    elif isinstance(data, list):
        for i, e in enumerate(data):
            if isinstance(e, dict):
                items.append((str(e.get("id") or e.get("phase") or i), e))

    today = _now().date().isoformat()
    out: list[Decision] = []
    for key, e in items:
        deadline = (
            e.get("soak_until") or e.get("deadline") or e.get("until")
            or e.get("soak_deadline")
        )
        deadline_s = str(deadline)[:10] if deadline else ""
        if not deadline_s or deadline_s < today:
            continue  # no deadline, or already closed — not a pending decision
        lane = str(e.get("lane") or e.get("series") or e.get("plan") or "")
        phase = str(e.get("phase") or key)
        reason_text = f"soak open until {deadline_s} ({phase})"
        out.append(Decision(
            kind=DecisionKind.SOAK_GATE,
            # A soak gate maps onto the closed soak-gated reason so the detail
            # pane projects its ReasonSpec fix; resolver is HUMAN by definition.
            resolver_kind=ResolverKind.HUMAN,
            lane=lane,
            reason_token="LANE_BLOCKED_ON_SOAK_GATED_PHASES",
            reason_text=reason_text,
            run_id="",
            age_seconds=None,  # a soak is forward-dated; age is not meaningful
            source_path=str(path),
            evidence=(f"soak_until={deadline_s}", f"phase={phase}"),
        ))
    return out


# ---------------------------------------------------------------------------
# Collection + ranking.
# ---------------------------------------------------------------------------


# Sort key precedence by kind: a LIVENESS halt proposal (a run hung/spinning RIGHT
# NOW, burning budget) is the most urgent — it outranks even a refusal, because a
# refusal blocks future work while a hung run is actively wasting it. Then the
# failure-refusals (a loop stopped / couldn't launch) outrank a forward-dated soak
# gate. Within a kind, oldest first (an aged decision is the most likely to be
# silently costing).
_KIND_RANK = {
    DecisionKind.LIVENESS: 0,
    DecisionKind.ARBITER_REFUSE: 1,
    # An enforcement storm shares the refusal tier: the retries are burning agent
    # turns RIGHT NOW and the parked edit blocks work — a NOW decision, not a
    # someday one. (Equal rank is fine; the within-rank sort is oldest-first.)
    DecisionKind.ENFORCE_BREAKER: 1,
    DecisionKind.PREFLIGHT_REFUSE: 2,
    DecisionKind.WEDGE: 3,
    DecisionKind.SOAK_GATE: 4,
}


def _dedup(decisions: list[Decision]) -> list[Decision]:
    """Collapse rows that describe the SAME pending decision into one, with a count.

    Pure. The lane journal is an append-only WAL, so a refusal re-emitted on every
    sweep lands as N identical `OP_REFUSE` entries — and a fleet of verdict
    envelopes with no verdict string all normalize to the same `blocked verdict=?`
    row. Either way the operator faces ONE decision, not N, so a chooser/list that
    shows N copies is noise. We group by the identity tuple
    `(kind, lane, reason_token, reason_text)` — the fields that make two rows "the
    same decision to a human" — keep the FIRST-seen representative (callers feed us
    source-order, which the subsequent sort re-orders by age anyway), and stamp it
    with `dup_count` = the group size. A LIVENESS halt is NOT deduped against a
    different `handle`: its handle/command are part of `reason_text`-adjacent
    identity only loosely, but two halts with the same reason text + lane + run are
    the same proposal — acceptable, and the common case (one halt) is unaffected.
    """
    groups: dict[tuple, Decision] = {}
    counts: dict[tuple, int] = {}
    order: list[tuple] = []
    for d in decisions:
        key = (d.kind, d.lane, d.reason_token, d.reason_text)
        if key not in groups:
            groups[key] = d
            counts[key] = 1
            order.append(key)
        else:
            counts[key] += 1
    out: list[Decision] = []
    for key in order:
        rep = groups[key]
        n = counts[key]
        out.append(rep if n == 1 else replace(rep, dup_count=n))
    return out


# Decision kinds whose recency is the time-axis of the decision ITSELF, not the
# age of the artifact that recorded it — these are never aged out by the recency
# filter. A SOAK_GATE is forward-dated (it's pending precisely because its window
# is still open; `_from_soaks` already drops closed ones). A LIVENESS halt names a
# run hung RIGHT NOW; an old OP_HALT for a run that is no longer spinning is still
# the operator's call to enact-or-decline, and the journal compaction (not this
# filter) is what bounds it. Everything else — a WEDGE/refusal envelope — is a
# point-in-time no-pick whose staleness IS its age.
_RECENCY_EXEMPT_KINDS = frozenset({DecisionKind.SOAK_GATE, DecisionKind.LIVENESS})


def _is_stale(decision: Decision, *, max_age_seconds: float | None) -> bool:
    """True when a decision is too old (or un-ageable) to still be 'pending'.

    The fix for the junk-drawer queue (job finding #476): `dos.decisions` read
    EVERY refusal-shaped `.verdict-*.json` on disk and surfaced each as pending,
    with no recency bound — so a WEDGE resolved weeks ago, and especially the
    common envelope that carries NO `generated_at`/`ts` (so `age_seconds is None`),
    showed as "pending operator decision" forever. The honest rule for a
    point-in-time artifact (a verdict has no liveness — see `RetentionPolicy.
    verdicts_keep_last`): past the cutoff it is stale, and an UN-timestamped one is
    treated as stale too (a verdict we cannot date is not a live decision — the
    conservative default). `max_age_seconds is None` disables the filter entirely
    (the keep-everything opt-out, mirroring `journal_max_age_days=None`).
    Recency-exempt kinds (soak/liveness) are never stale here.
    """
    if max_age_seconds is None:
        return False
    if decision.kind in _RECENCY_EXEMPT_KINDS:
        return False
    if decision.age_seconds is None:
        return True  # an un-ageable point-in-time refusal is not a live decision
    return decision.age_seconds > max_age_seconds


def collect_decisions(
    config=None,
    *,
    resolver: str | None = "HUMAN",
    now: dt.datetime | None = None,
) -> list[Decision]:
    """Gather every pending operator decision from the four sources, ranked.

    `config` defaults to the process-active config. `resolver` filters by
    `ResolverKind`: the default `"HUMAN"` returns only the rows that need a
    person; `None` returns everything (so `--all` can show what a judge/oracle
    owns); a specific value (`"ORACLE"` / `"JUDGE"`) narrows to that kind. `now`
    (the clock for the recency filter + every reader's age) defaults to wall-clock
    UTC; a test passes a frozen value so a seeded date is recency-stable.

    Read-only. A **recency filter** (job finding #476) drops point-in-time
    refusals older than `config.retention.journal_max_age_days` — and any verdict
    envelope with no timestamp at all — so the queue is decision-bound, not
    junk-drawer-bound (the read used to surface every stale `.verdict-*.json` on
    disk as pending forever). Soak/liveness rows are exempt (their recency is the
    decision's own time-axis). Ranking is then by kind precedence (refusals before
    soak gates), then oldest-decision-first within a kind.
    """
    cfg = config if config is not None else _config.active()
    clock = now if now is not None else _now()
    decisions: list[Decision] = []
    decisions.extend(_from_lane_journal(cfg, now=clock))
    decisions.extend(_from_enforce_storms(cfg, now=clock))
    decisions.extend(_from_verdict_envelopes(cfg, now=clock))
    decisions.extend(_from_soaks(cfg))

    # Recency gate — reuse the retention policy's `journal_max_age_days` cutoff as
    # the one staleness number (no new config knob; same seam the WAL compaction +
    # verdict reaper read). A config without a retention policy (a minimal/hand-built
    # one) leaves the filter disabled rather than crashing.
    retention = getattr(cfg, "retention", None)
    max_age_days = getattr(retention, "journal_max_age_days", None) if retention else None
    max_age_seconds = max_age_days * 86400 if max_age_days is not None else None
    if max_age_seconds is not None:
        decisions = [d for d in decisions
                     if not _is_stale(d, max_age_seconds=max_age_seconds)]

    if resolver is not None:
        want = resolver.strip().upper()
        decisions = [d for d in decisions if d.resolver_kind.value == want]

    # Collapse identical rows (an append-only WAL re-records the same refusal every
    # sweep) BEFORE sorting, so the surviving representative carries an accurate
    # dup_count and the list shows one row per real decision.
    decisions = _dedup(decisions)

    def _sort_key(d: Decision) -> tuple[int, float]:
        # Oldest first => negate age; an unknown age sorts after known ages.
        age = d.age_seconds if d.age_seconds is not None else -1
        return (_KIND_RANK.get(d.kind, 99), -age)

    decisions.sort(key=_sort_key)
    return decisions


# ---------------------------------------------------------------------------
# next_steps — the action bar. Maps a decision to its (key, shell-command) list.
# The TUI's emit-and-exit keys print exactly these; the plain list shows them as
# a hint. Sourced from the matched ReasonSpec.fix + the decision kind + resolver.
# ---------------------------------------------------------------------------


def next_steps(decision: Decision, config=None) -> list[tuple[str, str]]:
    """The ordered `(key_label, shell_command)` actions for one decision.

    The TUI binds each key to "print this command and exit" (the locked
    read-only-router model — the TUI never mutates state itself). The commands
    are real, runnable invocations the operator pastes into their shell.

    Always offered: `r` (/replan the lane) and `c` (copy) — except a LIVENESS
    halt and an ENFORCE_BREAKER storm, which carry their own action sets (the
    paste-to-stop; the override/man pair). `f` (force the lane) is offered for
    the lane-refusal kinds. `j` (adjudicate) is offered iff the
    decision is JUDGE-resolvable — it routes to the DETERMINISTIC `dos judge`
    (picker_oracle) when a `run_ts` is known, which cross-checks the verdict
    against on-disk state; the LLM adjudicator that can rule on the rows the
    deterministic judge only abstains on lives outside the kernel
    (`dos.drivers.llm_judge`), which `dos judge` points at on an abstain.
    """
    cfg = config if config is not None else _config.active()
    lane = decision.lane or ""
    scope_arg = f" --scope {lane}" if lane else ""
    steps: list[tuple[str, str]] = []

    # A LIVENESS halt proposal is its own action set: the host-supplied stop
    # command as the primary paste-to-stop (emit-and-exit — the queue NEVER signals
    # a process itself, the locked read-only-router model), plus an explicit
    # "let it ride" no-op. The kernel recorded the OP_HALT and proposed the command;
    # the operator enacts it (or declines). docs/101 §4.
    if decision.kind is DecisionKind.LIVENESS:
        if decision.proposed_command:
            steps.append(("k", decision.proposed_command))
        elif decision.handle:
            # No host command was supplied; surface the handle so the operator can
            # stop it by hand. We name no kill mechanism (domain-free) — just echo
            # the opaque handle the watchdog recorded.
            steps.append(("k", f"# stop the run with handle: {decision.handle}"))
        steps.append(("l", "# let it ride (take no action)"))
        steps.append(("c", "<copy selected command>"))
        return steps

    # An enforcement storm's levers are the SELF_MODIFY doctrine's own: the
    # operator's override window (docs/296) and the man page that documents the
    # between-runs path. NOT /replan (the lane is a tool name, not a plan scope)
    # and NOT `dos arbitrate --force` (the storm is at the hook surface, where
    # no force exists — pointing at one is what fueled the retries, issue #14).
    if decision.kind is DecisionKind.ENFORCE_BREAKER:
        steps.append(("o", "dos override status"))
        if decision.reason_token:
            steps.append(("m", f"dos man wedge {decision.reason_token}"))
        steps.append(("c", "<copy selected command>"))
        return steps

    # /replan is the universal "re-shape this lane" action.
    steps.append(("r", f"/replan{scope_arg}".strip()))

    # Force the lane lease — only meaningful for a lane-level refusal.
    if decision.kind in (DecisionKind.ARBITER_REFUSE, DecisionKind.PREFLIGHT_REFUSE):
        lane_arg = f" --lane {lane}" if lane else ""
        steps.append(("f", f"dos arbitrate{lane_arg} --force".strip()))

    # The adjudicate action: let the judge rule before a human spends attention.
    # The deterministic `dos judge` keys on a chained-run `run_ts`; when we have
    # one, emit the runnable command. Without a run_ts (a bare verdict envelope),
    # the deterministic judge has nothing to classify, so point at the LLM driver
    # that can adjudicate from the envelope/reason alone (outside the kernel).
    if decision.resolver_kind is ResolverKind.JUDGE:
        if decision.run_ts:
            steps.append(("j", f"dos judge wedge {decision.run_ts}"))
        else:
            ref = decision.run_id or decision.reason_token or "?"
            steps.append(("j", f"python -m dos.drivers.llm_judge {ref}"))

    # A soak gate's action is "come back when it closes" — surface the man page
    # so the operator can read the gate definition + its typical fix.
    if decision.reason_token:
        steps.append(("m", f"dos man wedge {decision.reason_token}"))

    steps.append(("c", "<copy selected command>"))
    return steps


# ---------------------------------------------------------------------------
# Rendering — the plain list (the curses-unavailable floor + --no-tui).
# ---------------------------------------------------------------------------


def _fmt_age(age: int | None) -> str:
    """Compact age: 45s / 18m / 2h / 3d / '-' when unknown."""
    if age is None:
        return "-"
    if age < 60:
        return f"{age}s"
    if age < 3600:
        return f"{age // 60}m"
    if age < 86400:
        return f"{age // 3600}h"
    return f"{age // 86400}d"


# ---------------------------------------------------------------------------
# Urgency tiers + inline action hints — the presentation helpers the TUI reads
# to make "what needs me RIGHT NOW" separable from "what can wait" at a glance.
#
# These are PURE (no curses, no I/O) and live here, beside the floor renderers,
# precisely so they are unit-testable and the curses `_draw` stays a thin skin
# over them — the same discipline that keeps the plain list and the TUI in
# lockstep. The tier is anchored on `_KIND_RANK` (the existing sort precedence)
# so the colour the eye reads and the order the list sorts can never disagree:
# a LIVENESS halt (a run burning budget now) is the most urgent thing on screen
# AND the reddest, by construction. docs/211 (the operator's attention is the
# scarce resource) + the n=12 finding that the surface must front the actionable
# fact, not the agent's narration.
# ---------------------------------------------------------------------------


class Urgency(str, enum.Enum):
    """How loudly a decision should announce itself — a 3-tier collapse of rank.

    NOW   — actively costing (a hung run) or blocking a loop (a refusal): act.
    SOON  — a no-pick / wedge surface: worth a look, not on fire.
    LATER — a forward-dated gate: it will ripen on its own clock.
    """

    NOW = "NOW"
    SOON = "SOON"
    LATER = "LATER"


# rank 0–1 (LIVENESS halt, ARBITER refuse) = NOW; 2–3 (preflight, wedge) = SOON;
# 4+ (soak gate) = LATER. Driven off `_KIND_RANK` so the two never drift.
def urgency_of(decision: Decision) -> Urgency:
    """The urgency tier for one decision — pure, anchored on the sort rank."""
    rank = _KIND_RANK.get(decision.kind, len(_KIND_RANK))
    if rank <= 1:
        return Urgency.NOW
    if rank <= 3:
        return Urgency.SOON
    return Urgency.LATER


# The glyph that prefixes a row by tier: a filled dot shouts, a mid ring is
# neutral, a low dot recedes. ASCII-safe fallbacks for a terminal that cannot
# render the unicode (the floor never assumes a font).
_URGENCY_GLYPH = {Urgency.NOW: "●", Urgency.SOON: "○", Urgency.LATER: "·"}
_URGENCY_GLYPH_ASCII = {Urgency.NOW: "!", Urgency.SOON: "+", Urgency.LATER: "."}


def urgency_glyph(decision: Decision, *, ascii_only: bool = False) -> str:
    """The one-char severity marker for a row (`●`/`○`/`·`, or `!`/`+`/`.`)."""
    tier = urgency_of(decision)
    table = _URGENCY_GLYPH_ASCII if ascii_only else _URGENCY_GLYPH
    return table[tier]


def urgency_tally(decisions: list[Decision]) -> str:
    """A glanceable '3 now · 5 later' split for the title bar.

    Collapses the 3 tiers to the two words an operator triages on: how many
    need me NOW vs everything-else (SOON+LATER folded into 'later'). Empty
    string when nothing is pending (the caller shows its own 'none' line).
    """
    if not decisions:
        return ""
    now = sum(1 for d in decisions if urgency_of(d) is Urgency.NOW)
    later = len(decisions) - now
    parts: list[str] = []
    if now:
        parts.append(f"{now} now")
    if later:
        parts.append(f"{later} later")
    return " · ".join(parts)


# Action keys we never surface as an inline hint: the always-present `c` (copy)
# and the `l` (let-it-ride no-op) carry no triage signal — the operator already
# knows they can copy or ignore. We front the keys that DO something.
_HINT_SKIP_KEYS = frozenset({"c", "l"})

# A short human label per action key, so the inline hint reads `[k]stop` not the
# whole shell command (which lives in the detail pane + is what gets emitted).
_ACTION_LABEL = {
    "k": "stop",
    "r": "replan",
    "f": "force",
    "j": "judge",
    "m": "man",
    "o": "override",
}


def action_hints(decision: Decision, config=None, *, limit: int = 2) -> list[tuple[str, str]]:
    """The top `(key, label)` actions to show inline on the list row.

    Pure projection over `next_steps` — drops the no-signal keys (`c`/`l`) and
    caps to `limit` so the row stays readable. The detail pane still shows the
    full action bar with the real commands; this is just the at-a-glance "what
    can I do to this row" the n=12 study says should be front-and-centre.
    """
    out: list[tuple[str, str]] = []
    for key, _cmd in next_steps(decision, config):
        if key in _HINT_SKIP_KEYS:
            continue
        out.append((key, _ACTION_LABEL.get(key, key)))
        if len(out) >= limit:
            break
    return out


def fmt_action_hints(decision: Decision, config=None, *, limit: int = 2) -> str:
    """Render `action_hints` as a compact `[k]stop [r]replan` string (or '')."""
    return " ".join(f"[{k}]{label}" for k, label in action_hints(decision, config, limit=limit))


# Labels for the keys the dense row-hint hides (copy, let-it-ride) — the footer
# IS allowed to show them, because it describes the ONE focused row, not 20.
_FOOTER_LABEL = dict(_ACTION_LABEL)
_FOOTER_LABEL.update({"c": "copy", "l": "ride"})


def footer_keys(decision: Decision, config=None) -> str:
    """Every action key for the focused row, labelled — the TUI footer.

    Unlike the per-row hint (which drops `c`/`l` to stay scannable across many
    rows), the footer describes the single selected decision, so it shows the
    full set including copy and let-it-ride. Pure over `next_steps`.
    """
    parts = [f"[{k}]{_FOOTER_LABEL.get(k, k)}" for k, _cmd in next_steps(decision, config)]
    return " · ".join(parts)


def render_list_plain(decisions: list[Decision]) -> str:
    """The column-aligned queue table — the `dos decisions --no-tui` output.

    Reuses `timeline.py`'s small-column rendering idiom so it fits a terminal.
    """
    out: list[str] = []
    out.append("# operator decisions")
    if not decisions:
        # ASCII hyphen, not an em-dash: this is the plain floor that prints to a
        # raw console (incl. a cp1252 Windows terminal), and a test pins it
        # byte-equal to the CLI's subprocess stdout. Keep the floor ASCII-clean.
        out.append("  (none pending - nothing is waiting on you)")
        return "\n".join(out)
    tally = urgency_tally(decisions)
    out.append(f"  {len(decisions)} pending" + (f"  ({tally})" if tally else ""))
    header = f"  {' ':1} {'#':>2}  {'age':>4}  {'kind':<16}  {'lane':<10}  waiting on / do"
    out.append(header)
    out.append("  " + "-" * (len(header) - 2))
    for i, d in enumerate(decisions, 1):
        # Reason TEXT first (human-readable), not the raw enum token — the token
        # is for the detail pane's ReasonSpec lookup, not the at-a-glance row.
        waiting = d.reason_text or d.reason_token
        dup = f"  ×{d.dup_count}" if d.dup_count > 1 else ""
        hint = fmt_action_hints(d)
        hint_s = f"   {hint}" if hint else ""
        out.append(
            f"  {urgency_glyph(d, ascii_only=True):1} {i:>2}  {_fmt_age(d.age_seconds):>4}  "
            f"{d.kind.value:<16}  {(d.lane or '-'):<10}  {waiting[:40]}{dup}{hint_s}"
        )
    out.append("")
    by_resolver = {}
    for d in decisions:
        by_resolver[d.resolver_kind.value] = by_resolver.get(d.resolver_kind.value, 0) + 1
    tally = " · ".join(f"{k}:{v}" for k, v in sorted(by_resolver.items()))
    out.append(f"  → {len(decisions)} pending ({tally})  ·  `dos decisions show <#>` to drill in")
    return "\n".join(out)


def render_detail_plain(decision: Decision, config=None) -> str:
    """The non-interactive drill-in for one decision (`dos decisions show <#>`).

    Renders the same projection the TUI detail pane shows: the decision's
    `ReasonSpec` (summary / fix / see-also, from the registry), its evidence,
    and the action bar — but as static text.
    """
    cfg = config if config is not None else _config.active()
    spec = cfg.reasons.get(decision.reason_token) if decision.reason_token else None
    out: list[str] = []
    out.append(f"KIND        {decision.kind.value}")
    out.append(f"RESOLVER    {decision.resolver_kind.value}")
    out.append(f"LANE        {decision.lane or '-'}")
    if decision.reason_token:
        out.append(f"REASON      {decision.reason_token}")
    if spec is not None and spec.summary:
        out.append(f"MEANS       {spec.summary}")
    out.append(f"DETAIL      {decision.reason_text}")
    if decision.run_id:
        out.append(f"RUN         {decision.run_id}")
    out.append(f"AGE         {_fmt_age(decision.age_seconds)}")
    if decision.dup_count > 1:
        out.append(f"SEEN        {decision.dup_count}× (identical rows collapsed)")
    if decision.evidence:
        out.append("EVIDENCE    " + "\n            ".join(decision.evidence))
    if spec is not None and spec.fix:
        out.append(f"TYPICAL FIX {spec.fix}")
    out.append(f"SOURCE      {decision.source_path}")
    out.append("")
    out.append("ACTIONS")
    for key, cmd in next_steps(decision, cfg):
        out.append(f"  [{key}]  {cmd}")
    if spec is not None and spec.see_also:
        out.append("")
        out.append("SEE ALSO    " + " · ".join(spec.see_also))
    return "\n".join(out)
