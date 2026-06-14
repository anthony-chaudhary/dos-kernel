"""The `hook-observation` record family — the kernel-owned per-call telemetry contract (docs/297).

Every hook invocation can append ONE schema-tagged JSONL line to the workspace's
observation log (`.dos/metrics/observations.jsonl`): which verb fired, what it
decided, how long it took. That log is the only surface that knows the
**denominator** — how many tool calls the substrate adjudicated at all — so it is
what turns the absolute "DOS refused N calls" count into the rate an operator
actually wants ("light touch or nanny?", issue #24), split refused-vs-advised so
the rate tells the same story as the `help_summary` headline (docs/285 Phase 3).

The family was born on the plugin's Go binary (docs/276 Part 2). THIS module is
the docs/297 Option-B move: the kernel takes ownership of the contract — the
schema constants, the entry builder, the fail-soft writer, the tolerant reader,
and the pure rate fold all live HERE — and every hook runtime that wants its
calls counted (the Go binary, the Python hook verbs) is a *conforming writer*.
The kernel never knows WHO wrote a record; it reads its own contract. That keeps
the awareness arrow clean: nothing here names a plugin, a vendor, or a binary.

Design rules (the `lane_journal` / `posttool_sensor` postures):

* **Pure where it can be.** `observation_entry()` and `intervention_rate()` are
  data-in / data-out, no disk — the unit-test surface. Only `append()` and
  `read_observations()` touch the file, at the boundary.
* **FAIL-SOFT, ADVISORY (docs/99).** Telemetry about a decision is strictly
  downstream of the decision: `append()` never raises, so a write fault can
  never change an emitted dialect or an exit code. A torn line is "didn't
  happen," never a corruption that derails a read.
* **Byte-clean (docs/138).** Every counted field is env-authored — the hook
  wrote the record downstream of an already-decided verdict. No agent narration
  enters the numerator or the denominator.
* **Like-for-like only (the issue-#24 caveat, made structural).** The rate fold
  takes observation records and nothing else. The lane journal — a different
  log with a different window and scope — has no path into it.

The denominator rule (docs/297): a `delegate` record is a HANDOFF, not an
adjudication — the call's real verdict is (or will be) another record, written
by the runtime that actually decided it. So `adjudicated` excludes delegates,
which is the one rule that counts each call exactly once in all three writer
worlds (binary-only, Python-only, mixed).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional

from dos import durable_schema as _ds

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dos import config as _config


# ---------------------------------------------------------------------------
# The contract constants — one family, one version, one path.
# ---------------------------------------------------------------------------

# The schema family every writer tags and every reader gates on (durable_schema).
SCHEMA_FAMILY = "hook-observation"
SCHEMA_VERSION = 1

# Every record's `op` — the observation log records, it never decides.
OP_OBSERVE = "OBSERVE"

# The pretool `outcome` tokens that mean the call was REFUSED (the kernel stopped
# it), as opposed to `warn` (advised, but let proceed) or `passthrough` (clean).
# `deny` is what the admission rung writes today; `block` is the alias the
# `observation_entry` docstring lists, included so a conforming writer that emits
# the rung name instead of the decision verb still counts as a refusal.
_REFUSED_OUTCOMES = frozenset({"deny", "block"})

# The log location under the workspace's `.dos/` home: a sibling of `streams/`
# and `runs/` (`.dos/metrics/observations.jsonl`).
METRICS_DIRNAME = "metrics"
LOG_BASENAME = "observations.jsonl"

# The durable-append opt-out, shared with every conforming writer: unset or
# anything-but-"0" = on; "0" = off. A `--debug` run always logs (a trace run is
# asking to see everything).
_METRICS_ENV = "DOS_HOOK_METRICS"


def observations_path(cfg: "Optional[_config.SubstrateConfig]" = None) -> Path:
    """The workspace's observation log path. PURE path arithmetic.

    Rides `cfg.paths.dot_dos` (the per-project `.dos/` home), the
    `streams_dir_for` idiom. Never creates anything — `append` is the only
    creator (the read-only-path discipline)."""
    from dos import config as _config_mod

    cfg = _config_mod.ensure(cfg)
    return cfg.paths.dot_dos / METRICS_DIRNAME / LOG_BASENAME


def metrics_enabled(*, debug: bool = False) -> bool:
    """True iff the durable append should run — on by default, `DOS_HOOK_METRICS=0`
    opts out, `--debug` always logs (the same gate the other writers honor)."""
    if debug:
        return True
    return os.environ.get(_METRICS_ENV, "").strip() != "0"


def _now_iso() -> str:
    """Second-resolution UTC ISO-8601 with a `Z` — the journal `ts` grammar."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# The write side — a PURE entry builder + a fail-soft boundary append.
# ---------------------------------------------------------------------------


def observation_entry(
    verb: str,
    outcome: str,
    *,
    exit_code: int = 0,
    latency_ms: float = 0.0,
    ts: str = "",
    run_id: str = "",
    rung: str = "",
    reason_class: str = "",
    dialect: str = "",
    tree_known: Optional[bool] = None,
    stream_state: str = "",
    marker_count: int = 0,
    max_markers: int = 0,
    claims_seen: int = 0,
    verify_source: str = "",
    blocked_plan: str = "",
    blocked_phase: str = "",
    panic_recovered: bool = False,
) -> dict:
    """One schema-tagged observation record — the PURE builder (the `_step_entry`
    posture).

    Only `verb` + `outcome` + `exit` + `latency_ms` are always present; every
    verb-specific field is written ONLY when set, so a bare record stays small
    and the schema version never bumps for an absent field (the additive
    contract every conforming writer shares). `outcome` is a short
    verb-specific tag: `deny`/`warn`/`passthrough`/`block`/`let`/`allow`/
    `refuse`/`delegate`/…. An empty `verb`/`outcome` raises — a writer that
    stamps an unnamed record is a kernel bug, not silent data (`durable_schema.tag`).
    `ts` may be left empty for `append` to stamp at write time.
    """
    if not verb:
        raise ValueError("an observation must name its verb")
    if not outcome:
        raise ValueError("an observation must name its outcome")
    e: dict = {
        **_ds.tag(SCHEMA_FAMILY, SCHEMA_VERSION),
        "op": OP_OBSERVE,
        "verb": verb,
        "outcome": outcome,
        "exit": int(exit_code),
        "latency_ms": float(latency_ms),
    }
    if ts:
        e["ts"] = ts
    if run_id:
        e["run_id"] = run_id
    if rung:
        e["rung"] = rung
    if reason_class:
        e["reason_class"] = reason_class
    if dialect:
        e["dialect"] = dialect
    if tree_known is not None:
        e["tree_known"] = bool(tree_known)
    if stream_state:
        e["stream_state"] = stream_state
    if marker_count or max_markers:
        e["marker_count"] = int(marker_count)
        e["max_markers"] = int(max_markers)
    if claims_seen:
        e["claims_seen"] = int(claims_seen)
    if verify_source:
        e["verify_source"] = verify_source
    if blocked_plan:
        e["blocked_plan"] = blocked_plan
        e["blocked_phase"] = blocked_phase
    if panic_recovered:
        e["panic_recovered"] = True
    return e


def append(
    entry: dict,
    *,
    cfg: "Optional[_config.SubstrateConfig]" = None,
    path: Optional[Path] = None,
    debug: bool = False,
) -> bool:
    """Append one observation to the workspace log — best-effort, FAIL-SOFT.

    Stamps `ts` if absent, writes one canonical-JSON line (the
    `lane_journal.append` grammar: sorted keys, `ensure_ascii=False`), creates
    the directory on first use, and `fsync`s so a recorded observation outlives
    the one-shot process that wrote it. Gated by `metrics_enabled` (the
    `DOS_HOOK_METRICS` opt-out; `--debug` always logs).

    NEVER raises: the caller has already decided + emitted, and telemetry about
    a decision must not be able to alter it (docs/99). Returns True iff a line
    was durably written — a test affordance, not a contract the hook verbs read.
    """
    try:
        if not metrics_enabled(debug=debug):
            return False
        p = path or observations_path(cfg)
        e = dict(entry)
        e.setdefault("ts", _now_iso())
        line = json.dumps(e, sort_keys=True, default=str, ensure_ascii=False) + "\n"
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return True
    except Exception:  # noqa: BLE001 — a telemetry write fault never alters a verdict
        return False


# ---------------------------------------------------------------------------
# The read side — a tolerant boundary read + the PURE rate fold.
# ---------------------------------------------------------------------------


def read_observations(path: Optional[Path] = None,
                      cfg: "Optional[_config.SubstrateConfig]" = None) -> tuple[dict, ...]:
    """Every soundly-readable observation in the log, in file order. Boundary I/O.

    Tolerant the way every JSONL fold here is: a blank/torn/corrupt line is
    skipped ("didn't happen"); a record tagged for a different family, tagged
    at a version this kernel predates (refuse-don't-guess, `durable_schema`),
    untagged, or whose `op` is not OBSERVE is skipped. A missing or unreadable
    file degrades to () — a read-only surface shows what it has, never an error.
    """
    p = path or observations_path(cfg)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    out: list[dict] = []
    for line in text.splitlines():
        rec = _decode_observation(line)
        if rec is not None:
            out.append(rec)
    return tuple(out)


def _decode_observation(line: str) -> Optional[dict]:
    """One log line → a soundly-readable OBSERVE record, or None. PURE.

    The single home of the tolerance contract both readers share: a blank or
    torn line, a record tagged for a different family / a version this kernel
    predates (refuse-don't-guess, `durable_schema`), an untagged record, a
    non-dict, or one whose `op` is not OBSERVE all return None ("didn't
    happen"). Lifted out of `read_observations` so the bounded tail reader
    applies the exact same gate — one definition, no drift.
    """
    s = line.strip()
    if not s:
        return None
    try:
        rec = json.loads(s)
    except (ValueError, TypeError):
        return None
    if not isinstance(rec, dict):
        return None
    verdict = _ds.classify(rec, family=SCHEMA_FAMILY, understands=SCHEMA_VERSION)
    if not verdict.readability.is_soundly_readable:
        return None
    if rec.get("op") != OP_OBSERVE:
        return None
    return rec


# The default tail window the watchdog banner reads. `dispatch_top` only needs
# the most-recent fresh SELF_MODIFY deny (TTL 2 h, `latest_self_modify_block`),
# so it never has to parse the whole — possibly tens-of-MB — log: it reads the
# trailing slice newest-first and stops. 1 MiB comfortably covers a 2 h window
# of per-call records at any realistic fleet rate; the bound is on the SCAN, not
# on correctness — a log smaller than the window is read whole.
TAIL_DEFAULT_MAX_BYTES = 1 << 20  # 1 MiB
TAIL_DEFAULT_MAX_RECORDS = 4096


def read_observations_tail(
    path: Optional[Path] = None,
    cfg: "Optional[_config.SubstrateConfig]" = None,
    *,
    max_bytes: int = TAIL_DEFAULT_MAX_BYTES,
    max_records: int = TAIL_DEFAULT_MAX_RECORDS,
) -> tuple[dict, ...]:
    """The trailing observations, NEWEST-FIRST, bounded by bytes and records.

    The cost-bounded sibling of `read_observations` for the readers that only
    care about RECENT records (the `dos top` self-modify banner): instead of
    parsing the whole log to find the newest needle, read only the last
    `max_bytes` of the file and keep at most `max_records`, returned newest
    first. Same tolerance contract as `read_observations` (via
    `_decode_observation`); a missing/unreadable file degrades to (). Pure
    boundary I/O — read-only, takes no lease.

    Bounding rules that keep it correct, not just fast:

    * The trailing-window read can slice mid-line at its FRONT, so the first
      (oldest) line in the window is dropped as possibly-truncated — UNLESS the
      window starts at byte 0 (the whole file fit), where nothing was cut.
    * Records are yielded newest-first by reversing file order, so a caller that
      wants "the latest matching record" stops at the first hit.
    """
    p = path or observations_path(cfg)
    try:
        size = p.stat().st_size
        with open(p, "rb") as fh:
            start = max(0, size - max(0, int(max_bytes)))
            if start:
                fh.seek(start)
            raw = fh.read()
    except OSError:
        return ()
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    # A non-zero start may have sliced the first line mid-record — drop it.
    if start and lines:
        lines = lines[1:]
    out: list[dict] = []
    for line in reversed(lines):
        rec = _decode_observation(line)
        if rec is None:
            continue
        out.append(rec)
        if len(out) >= max(0, int(max_records)):
            break
    return tuple(out)


@dataclass(frozen=True)
class InterventionRate:
    """The folded "what share of tool calls did the kernel touch?" value.

    All counts come from ONE observation log — never the lane journal (the
    like-for-like rule). `pretool_records` is every pretool record seen;
    `adjudicated` excludes the `delegate` handoffs (the docs/297 denominator
    rule); `passed` + `intervened` partition `adjudicated`. The percents are
    properties so a renderer never recomputes the arithmetic differently.

    `intervened` itself splits the two ways the operator cares about (the same
    refused-vs-advisory line `help_summary` draws on the lane journal): `refused`
    is a call the kernel actually STOPPED (`deny`/`block` outcome — DOS did
    something for you), `advised` is a call the kernel warned about but LET
    PROCEED (`warn` outcome — a caution that changed nothing). On every outcome
    the kernel emits today `intervened == refused + advised`; a future outcome
    token outside {deny, block, warn} would count in `intervened` but in neither
    sub-bucket (`intervened >= refused + advised` always), the safe direction —
    so a lumped "5% intervened" can no longer hide that most of it was advisory.
    """

    pretool_records: int = 0
    adjudicated: int = 0
    passed: int = 0
    intervened: int = 0
    refused: int = 0
    advised: int = 0
    delegated: int = 0

    @property
    def passed_pct(self) -> float:
        if self.adjudicated <= 0:
            return 0.0
        return self.passed * 100.0 / self.adjudicated

    @property
    def intervened_pct(self) -> float:
        if self.adjudicated <= 0:
            return 0.0
        return self.intervened * 100.0 / self.adjudicated

    @property
    def refused_pct(self) -> float:
        if self.adjudicated <= 0:
            return 0.0
        return self.refused * 100.0 / self.adjudicated

    @property
    def advised_pct(self) -> float:
        if self.adjudicated <= 0:
            return 0.0
        return self.advised * 100.0 / self.adjudicated

    def to_dict(self) -> dict:
        return {
            "adjudicated": self.adjudicated,
            "passed": self.passed,
            "intervened": self.intervened,
            "refused": self.refused,
            "advised": self.advised,
            "delegated": self.delegated,
            "passed_pct": round(self.passed_pct, 1),
            "intervened_pct": round(self.intervened_pct, 1),
            "refused_pct": round(self.refused_pct, 1),
            "advised_pct": round(self.advised_pct, 1),
        }


def intervention_rate(records: Iterable[dict], *, since: str = "") -> InterventionRate:
    """Fold observation records into the intervention rate. PURE.

    One pretool record = one tool call adjudicated, so pretool is the honest
    denominator (posttool/stop/marker firings are not tool-call admissions). A
    `delegate` outcome leaves the denominator: it is a handoff whose real
    verdict is another record (docs/297) — counting both would count the call
    twice. Everything adjudicated that did not pass through untouched was
    intervened on, and that intervention splits two ways: a `deny`/`block`
    outcome REFUSED the call (the kernel stopped it), a `warn` outcome ADVISED
    on it but let it proceed. On the outcomes the kernel emits today
    `intervened == refused + advised`; an unknown future token counts in
    `intervened` but neither sub-bucket (`intervened >= refused + advised`).

    `since` keeps only records with `ts >= since` (ISO-8601 sorts lexically);
    when a window is set, a record with no `ts` is skipped — a windowed fold
    must not count an undatable record (the conservative direction).

    Records in, value out, no disk — the unit-test surface. The signature takes
    observation records ONLY; there is deliberately no parameter through which
    a lane-journal count could enter either side of the ratio.
    """
    pretool_records = adjudicated = passed = refused = advised = delegated = 0
    for rec in records:
        if rec.get("verb") != "pretool":
            continue
        ts = str(rec.get("ts") or "")
        if since and (not ts or ts < since):
            continue
        pretool_records += 1
        outcome = str(rec.get("outcome") or "")
        if outcome == "delegate":
            delegated += 1
            continue
        adjudicated += 1
        if outcome == "passthrough":
            passed += 1
        elif outcome in _REFUSED_OUTCOMES:
            refused += 1
        elif outcome == "warn":
            advised += 1
        # An adjudicated outcome outside these tokens (none today) still counts in
        # `adjudicated` and so in `intervened = adjudicated - passed`, but lands in
        # neither `refused` nor `advised` — the safe direction: we never label an
        # unknown outcome a refusal it might not be.
    return InterventionRate(
        pretool_records=pretool_records,
        adjudicated=adjudicated,
        passed=passed,
        intervened=adjudicated - passed,
        refused=refused,
        advised=advised,
        delegated=delegated,
    )


# ---------------------------------------------------------------------------
# The quotable headline (issue #71) — a share-shaped, receipt-linked one-liner
# over the observation log. PURE folds + a renderer; the unit-test surface.
#
# The honesty floor (the issue's two requirements):
#   * Receipt-linked: every nonzero count expands to the env-authored records
#     behind it + the command that REGENERATES the verdict — a count that cannot
#     show its receipts is narration, the thing the kernel exists to refuse.
#   * Honest zeros + honest COVERAGE: a quiet window renders zeros, never
#     suppressed; and a class the observation log structurally cannot witness
#     (an arbitrate-admitted collision lives in the lane journal, not here) is
#     labelled as such instead of implying a real zero it cannot prove.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Receipt:
    """One env-authored record behind a headline count — never narrated (docs/138).

    Every field is bytes the hook sensor wrote downstream of an already-decided
    verdict: `verb`/`outcome`/`ts` plus the verb-specific evidence (`plan`/
    `phase`/`verify_source` for a stop-block, `reason_class` for a pretool deny).
    `regen_command` is the runnable verb that re-derives the verdict from ground
    truth — `dos verify <plan> <phase>` for a stop-block, `dos man wedge <class>`
    for a typed deny — built from those fields, so the count can prove itself.
    """

    verb: str = ""
    outcome: str = ""
    ts: str = ""
    plan: str = ""
    phase: str = ""
    reason_class: str = ""
    target: str = ""
    regen_command: str = ""

    def to_dict(self) -> dict:
        return {
            "verb": self.verb,
            "outcome": self.outcome,
            "ts": self.ts,
            "plan": self.plan,
            "phase": self.phase,
            "reason_class": self.reason_class,
            "target": self.target,
            "regen_command": self.regen_command,
        }


@dataclass(frozen=True)
class HeadlineSummary:
    """The share-shaped rollup over the observation log — counts the log CAN witness.

    `adjudicated` is the denominator (pretool minus delegates, from the SAME fold
    as `intervention_rate` so the two never disagree). The per-class counts are
    each keyed to a concrete (verb, outcome) the sensors write:
      * `false_done_refused` — (stop, block): a claimed "done" that didn't verify.
      * `edits_blocked`      — (pretool, deny): a tool call refused at the boundary.
      * `warned`             — (pretool|posttool, warn): advisory touches.
    `collisions_admitted` is ALWAYS 0 and `collisions_witnessed=False`: the
    observation log does not record arbitrate-admits (those are lane-journal
    facts), so the headline says so rather than claiming a zero it cannot prove.
    `since`/`latest` echo the window. `receipts` (per class) is populated only
    when `with_receipts=True`. Every count is env-authored; none is narration.
    """

    adjudicated: int = 0
    false_done_refused: int = 0
    edits_blocked: int = 0
    warned: int = 0
    collisions_admitted: int = 0
    collisions_witnessed: bool = False
    since: str = ""
    latest: str = ""
    receipts: dict[str, tuple[Receipt, ...]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "adjudicated": self.adjudicated,
            "false_done_refused": self.false_done_refused,
            "edits_blocked": self.edits_blocked,
            "warned": self.warned,
            "collisions_admitted": self.collisions_admitted,
            "collisions_witnessed": self.collisions_witnessed,
            "since": self.since,
            "latest": self.latest,
            "receipts": {
                cls: [r.to_dict() for r in recs]
                for cls, recs in self.receipts.items()
            },
        }


# How many receipts to keep per class (the `dos helped --explain` cap — a
# drill-down shows a few concrete examples, not the whole log).
_RECEIPTS_PER_CLASS = 3


def _receipt_for(rec: dict) -> Receipt:
    """Build the env-authored `Receipt` (+ regen command) for one record. PURE.

    The regen command is derived from the fields the sensor wrote, never the
    agent's narration: a stop-block names a `dos verify <plan> <phase>` when it
    carries the plan/phase that failed; a typed pretool deny names
    `dos man wedge <reason_class>`. The observation log carries no commit SHA, so
    `dos commit-audit` is deliberately NOT offered here — the receipt only claims
    what its own fields can regenerate (honest about its reach)."""
    verb = str(rec.get("verb") or "")
    outcome = str(rec.get("outcome") or "")
    plan = str(rec.get("blocked_plan") or "")
    phase = str(rec.get("blocked_phase") or "")
    reason_class = str(rec.get("reason_class") or "")
    regen = ""
    if verb == "stop" and outcome == "block" and plan and phase:
        regen = f"dos verify {plan} {phase}"
    elif verb == "pretool" and outcome == "deny" and reason_class:
        regen = f"dos man wedge {reason_class}"
    return Receipt(
        verb=verb, outcome=outcome, ts=str(rec.get("ts") or ""),
        plan=plan, phase=phase, reason_class=reason_class,
        target=str(rec.get("verify_source") or ""), regen_command=regen,
    )


def headline_summary(records: Iterable[dict], *, since: str = "",
                     with_receipts: bool = False) -> HeadlineSummary:
    """Fold observation records into the quotable headline summary. PURE — no disk.

    Counts each share-shaped class from its (verb, outcome) tuple, reusing the
    `intervention_rate` denominator for `adjudicated`. `since` keeps records with
    `ts >= since` (ISO-8601 lexical compare; an undatable record is skipped under
    a window — the conservative direction `intervention_rate` already takes).
    `with_receipts` banks up to `_RECEIPTS_PER_CLASS` env-authored receipts per
    nonzero class. `collisions_admitted` is structurally 0 (not witnessed here).
    """
    rate = intervention_rate(records if isinstance(records, (list, tuple))
                             else list(records), since=since)
    # Re-list so a one-shot iterator survives both the rate fold above and the
    # class pass below (intervention_rate consumed the first view).
    recs = list(records) if not isinstance(records, (list, tuple)) else list(records)

    false_done = edits_blocked = warned = 0
    receipts: dict[str, list[Receipt]] = {
        "false_done_refused": [], "edits_blocked": [], "warned": [],
    }
    latest = ""
    for rec in recs:
        ts = str(rec.get("ts") or "")
        if since and (not ts or ts < since):
            continue
        if ts > latest:
            latest = ts
        verb = str(rec.get("verb") or "")
        outcome = str(rec.get("outcome") or "")
        cls = ""
        if verb == "stop" and outcome == "block":
            false_done += 1
            cls = "false_done_refused"
        elif verb == "pretool" and outcome == "deny":
            edits_blocked += 1
            cls = "edits_blocked"
        elif verb in ("pretool", "posttool") and outcome == "warn":
            warned += 1
            cls = "warned"
        if with_receipts and cls and len(receipts[cls]) < _RECEIPTS_PER_CLASS:
            receipts[cls].append(_receipt_for(rec))

    frozen_receipts = {
        cls: tuple(rs) for cls, rs in receipts.items() if rs
    } if with_receipts else {}
    return HeadlineSummary(
        adjudicated=rate.adjudicated,
        false_done_refused=false_done,
        edits_blocked=edits_blocked,
        warned=warned,
        collisions_admitted=0,
        collisions_witnessed=False,
        since=since,
        latest=latest,
        receipts=frozen_receipts,
    )


def render_headline_text(summary: HeadlineSummary, *,
                         with_receipts: bool = False) -> str:
    """The quotable one-liner + honest per-class breakdown. PURE.

    Leads with the pasteable headline (the `dos helped` posture), then the
    per-class lines with HONEST ZEROS and the coverage clause. `with_receipts`
    appends each nonzero class's env-authored receipts + regen commands.
    """
    out: list[str] = []
    window = f" since {summary.since}" if summary.since else ""
    # The pasteable one-liner — the artifact the issue is about.
    out.append(
        f"dos: {summary.adjudicated} tool call(s) adjudicated{window} — "
        f"{summary.false_done_refused} false \"done\"(s) refused at stop, "
        f"{summary.edits_blocked} edit(s) blocked at the kernel boundary, "
        f"{summary.collisions_admitted} collisions admitted"
    )
    # The coverage clause — honest about WHAT this number covers (the observation
    # log = the surfaces the hooks gate), and about the one class it cannot
    # witness (arbitrate-admits live in the lane journal, so collisions here is a
    # structural 0, not a measured one).
    out.append("  on the surfaces the hooks gate (the per-call observation log)")
    if not summary.collisions_witnessed:
        out.append("  · collisions admitted is not witnessed here — "
                   "arbitrate-admits live in the lane journal (`dos helped`)")
    if summary.warned:
        out.append(f"  · {summary.warned} advisory warn(s) (recorded, not refused)")
    if with_receipts:
        labels = {
            "false_done_refused": "false \"done\" refused at stop",
            "edits_blocked": "edit blocked at the kernel boundary",
            "warned": "advisory warn",
        }
        for cls, recs in summary.receipts.items():
            if not recs:
                continue
            out.append(f"  receipts — {labels.get(cls, cls)}:")
            for r in recs:
                regen = f"  →  {r.regen_command}" if r.regen_command else ""
                out.append(f"    {r.ts or '-'}  {r.verb}/{r.outcome}{regen}")
    return "\n".join(out)
