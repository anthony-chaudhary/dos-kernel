"""Operator-value severity for a dispatch-family bookkeeping event (noise filter).

The dispatch family (`/dispatch`, `/dispatch-loop`, `/replan`, `/next-up`) commits
and pushes an archive line for *every* iteration — even a 0-pick drain, a repeated
rate-limit, or a no-op gardening sweep. Measured 2026-06-03: 82 of the last 200
commits on `main` (41%) were this bookkeeping, ~20-28/hr at peak, and 67% of
dispatch-loop archives shipped 0 picks. Peers pulling `main` absorb the whole flood.

`classify_event()` is the keystone fix — a **pure** function that turns the event's
already-computed facts (the `verdict=<X>` token the write step holds, the pick count,
whether this is the *first* occurrence of a blocker this loop) into one typed
`Severity`. Each operator-facing *sink* (push, local-commit, terminal, report,
artifact) then admits or suppresses the event by comparing its severity against a
per-sink threshold (an env var) — exactly the way a logging framework filters by
level. The default thresholds make the common case quiet: only `SHIPPED` and a
*newly-surfaced* blocker reach `origin`; a 0-pick drain or no-op replan still commits
locally (audit kept) but never pushes.

    SHIPPED      material change landed (>=1 pick, verdict=LIVE, or a plan
                 promotion) — the thing the operator wants surfaced first
    BLOCKED-NEW  a blocker / operator-decision / crash seen for the FIRST time
                 this loop — actionable, may need a decision -> reaches peers
    NOTICE       state-changing but routine — a STALE-STAMP false-drain, >=1
                 finding/closure, a soft-claim, a --next-up-only packet, or
                 (docs/310) real-but-not-pick-shaped work the iteration's
                 work-kind account witnessed (a caught false claim, advancing
                 commits, grooming, raised decisions)
    NOOP         a non-event — 0-pick DRAIN, a REPEATED blocker/rate-limit, a
                 gardening-only quiet-sweep, a 0-net soft-claim

⚓ Severity is `(verdict, first_occurrence)`, not verdict alone. A blocker the
operator has not seen is high-value; the *same* blocker recurring every iteration is
pure noise. The `first_occurrence` predicate is what collapses the repeated-blocked /
repeated-RATE_LIMITED flood (13 of 16 chained archives on 2026-06-03) into `NOOP`.
`SHIPPED` outranks `BLOCKED-NEW` because a landed pick is what the operator wants on
top.

⚓ Pure kernel, I/O on the edge (the dos composition idiom — mirrors `classify_packet`
in `gate_classify.py`): `classify_event(EventState) -> Severity` is a frozen dataclass
in, an enum out — no subprocess, no file/clock/git call. Every signal (`verdict`,
`picks_shipped`, `first_occurrence`, the replan/next-up counters) is reduced to a
scalar/bool by the caller at the write step, which is also the only place that knows
them. The one concession to I/O is `sink_threshold()`, a single `os.environ.get`
(the `fanout_state` module-level idiom) — kept beside the classifier so a consumer has
one import. The classifier itself stays pure and is the unit-test surface.

⚓ Reuse the verdict vocabulary, never re-list it. The classifier *input* is the
`verdict=<X>` token the archive subject already carries; `normalize_token` (this
package's `tokens.py`) is the one chokepoint that upper-cases it and folds the legacy
`WEDGE -> BLOCKED` alias, so a historical `verdict=WEDGE` event classifies identically
to `BLOCKED`. Do not duplicate the token set here.
"""
from __future__ import annotations

import enum
import os
from dataclasses import dataclass
from typing import Optional

from .tokens import normalize_token
from .work_account import WorkAccount, WorkKind, account_lead_token, classify_work


class Severity(str, enum.Enum):
    """One typed operator-value level. `str`-valued so it round-trips as a bare
    token through an env var and a commit subject (the `GateVerdict` pattern)."""

    SHIPPED = "SHIPPED"
    BLOCKED_NEW = "BLOCKED-NEW"
    NOTICE = "NOTICE"
    NOOP = "NOOP"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# Threshold ordering — a sink with MIN=NOTICE admits NOTICE/BLOCKED-NEW/SHIPPED and
# rejects NOOP. Higher rank = higher operator value = harder to suppress.
_RANK: dict[Severity, int] = {
    Severity.NOOP: 0,
    Severity.NOTICE: 1,
    Severity.BLOCKED_NEW: 2,
    Severity.SHIPPED: 3,
}

# The verdict tokens that mean "a blocker / crash / rate-limit happened" — first
# occurrence is BLOCKED-NEW, a repeat is NOOP. Canonical (post-`normalize_token`)
# spellings only; WEDGE folds to BLOCKED upstream so it is covered by "BLOCKED".
_BLOCKER_VERDICTS = frozenset({"BLOCKED", "RACE", "ERROR", "RATE_LIMITED"})


@dataclass(frozen=True)
class EventState:
    """Every input the write step already holds — no I/O happens to build this.

    Mirrors `PickDisposition` / the `classify_packet` input: the caller reduces all
    time/git/file state to these scalars at the I/O edge, then hands them in frozen.
    """

    family: str  # "dispatch-loop" | "dispatch" | "replan" | "next-up"
    verdict: str = ""  # raw token from the archive subject (LIVE/DRAIN/BLOCKED/...)
    picks_shipped: int = 0
    # False = this verdict was already seen earlier this loop -> demote a blocker to
    # NOOP. Defaults True so an unknown caller fails toward surfacing (never hides).
    first_occurrence: bool = True
    # --- replan §-counters (the gardening-sweep shape) -----------------------
    new_findings: int = 0  # findings closed/added this sweep
    substantive_ships: int = 0  # plan phases this sweep marked shipped
    surfaced: int = 0  # inbox promotions this sweep (a real plan change)
    # --- next-up render --------------------------------------------------------
    soft_claims: int = 0  # picks soft-claimed by the rendered packet
    # Did the staged pathspec actually differ from HEAD? False -> a no-op write.
    staged_changed: bool = True
    # --- docs/310: the work-kind account (optional) ---------------------------
    # When the write step gathered a per-iteration WorkAccount (each counter
    # env-witnessed at the I/O edge — the oracle's verified count, git's commit
    # count, the decisions-queue delta), the dispatch family consults it:
    # verified ships reach SHIPPED, and real-but-not-pick-shaped work (a caught
    # false claim, advancing commits, grooming, raised decisions) lifts the
    # event from NOOP to NOTICE. None = un-migrated caller -> byte-identical
    # legacy behavior (the picks_shipped/verdict bits decide alone).
    account: Optional[WorkAccount] = None


def classify_event(ev: EventState) -> Severity:
    """PURE — the event -> severity mapping verbatim. No file/git/clock/env call."""
    fam = (ev.family or "").strip().lower()
    v = normalize_token(ev.verdict) or ""

    if fam in ("dispatch", "dispatch-loop"):
        acct_ships = ev.account.verified_ships if ev.account is not None else 0
        if ev.picks_shipped > 0 or v == "LIVE" or acct_ships > 0:
            return Severity.SHIPPED
        if v in _BLOCKER_VERDICTS:
            # First time this loop -> actionable; a repeat -> noise.
            return Severity.BLOCKED_NEW if ev.first_occurrence else Severity.NOOP
        if v == "STALE-STAMP":
            # A false-drain: routes a /replan (operator-relevant -> NOTICE) but the
            # following /replan, not this stamp, carries the real signal to peers.
            return Severity.NOTICE
        if ev.account is not None and classify_work(ev.account).kind is not WorkKind.IDLE:
            # docs/310 — the work-kind account says real-but-not-pick-shaped work
            # landed (a caught false claim, advancing commits, grooming, raised
            # decisions): state-changing but routine -> NOTICE, no longer a
            # non-event. The push sink's default threshold (BLOCKED-NEW) still
            # keeps it off peers' pulls.
            return Severity.NOTICE
        # DRAIN / 0-pick / unknown-token fallthrough — the dominant non-event.
        return Severity.NOOP

    if fam == "replan":
        if ev.surfaced > 0:
            return Severity.SHIPPED  # an inbox promotion is a real plan change
        if ev.new_findings > 0 or ev.substantive_ships > 0:
            return Severity.NOTICE
        # Gardening-only quiet-sweep (closed==0, added==0, surfaced==0): the §1.5
        # SKIP gate already dropped the truly-empty case upstream; this is the
        # "ran but only touched anchors/stale-claims" sweep — local audit, no push.
        return Severity.NOOP

    if fam == "next-up":
        if not ev.staged_changed:
            return Severity.NOOP  # the renderer staged nothing (already-active picks)
        return Severity.NOTICE if ev.soft_claims > 0 else Severity.NOOP

    # Unknown family — fail safe: surface it rather than silently swallow.
    return Severity.NOTICE


# ---------------------------------------------------------------------------
# Subject lead-token — the mechanical commit-subject headline.
#
# ⚓ Why this is a kernel function, not a SKILL.md prose rule. The Phase-1 fix
# pinned a *prose* rule to the replan write-step ("lead with the severity token,
# the run ordinal NEVER appears"). The live git log on 2026-06-03 proved prose
# does not fire: `docs/_plans: 185th /replan …` and `… (184th /replan)` still
# leaked the monotonic ordinal AFTER Phase 1 shipped. A model retyping a subject
# every iteration drifts; a function cannot. So the lead token is COMPUTED here —
# the ordinal is structurally absent because this function never takes it as an
# input. The write-step asks `severity_gate.py subject …` for the headline and
# prepends only the immutable family prefix (`docs/_plans: replan <date> — `).
#
# PURE, like `classify_event` — the same `EventState` in, a short headline string
# out. No clock/git/env/file call. The token is operator-facing English keyed off
# the SAME severity the gate computes, so the headline and the gate decision can
# never disagree about what happened.
# ---------------------------------------------------------------------------
def subject_lead_token(ev: EventState) -> str:
    """The severity-shaped headline for `ev`'s commit subject (the lead phrase
    after the immutable `docs/<family>: …` prefix). PURE — derived from the same
    facts `classify_event` reads, so it always agrees with the gate verdict.

    The run ordinal is *structurally* impossible here: it is not a field of
    `EventState`, so a caller building the subject from this token cannot leak it
    (the recurring `(185th /replan)` flood the prose rule failed to stop)."""
    sev = classify_event(ev)
    fam = (ev.family or "").strip().lower()
    v = normalize_token(ev.verdict) or ""

    if fam in ("dispatch", "dispatch-loop"):
        if sev is Severity.SHIPPED:
            if ev.account is not None and ev.account.verified_ships > 0:
                # docs/310 — the composed work-kind headline: every non-zero
                # kind in precedence order ("1 pick shipped · 4 commits
                # advanced"), from the same phrase source as the classifier.
                return account_lead_token(ev.account)
            n = ev.picks_shipped
            return f"{n} pick{'s' if n != 1 else ''} shipped" if n > 0 else "shipped"
        if sev is Severity.BLOCKED_NEW:
            # A FIRST-seen blocker — name the blocker class so the operator can act.
            # A bare "BLOCKED" verdict needs no parenthetical (it would read
            # "blocked (blocked)"); a more specific token (RATE_LIMITED / RACE /
            # ERROR) is surfaced so the operator sees WHICH wall they hit.
            if not v or v == "BLOCKED":
                return "blocked"
            return f"blocked ({v.lower()})"
        if v == "STALE-STAMP":
            return "stale-stamp false-drain (/replan recommended)"
        if sev is Severity.NOTICE and ev.account is not None:
            # docs/310 — a NOTICE earned by the work-kind account (no pick
            # shipped, but real work witnessed): lead with the account, e.g.
            # "1 false claim caught · 3 grooms". The backlog word ("drained")
            # stays the NOOP fallthrough's — work and backlog are two axes.
            return account_lead_token(ev.account)
        return "drained"  # NOOP — the dominant 0-pick non-event

    if fam == "replan":
        if sev is Severity.SHIPPED:
            return f"inbox promoted: {ev.surfaced}"
        if sev is Severity.NOTICE:
            # State-changing gardening — lead with the counts that moved.
            return f"{ev.new_findings} closed / {ev.substantive_ships} shipped"
        return "quiet sweep"  # NOOP — gardening-only; NO ordinal

    if fam == "next-up":
        if sev is Severity.NOTICE:  # a real soft-claim
            n = ev.soft_claims
            return f"soft-claims ({n} pick{'s' if n != 1 else ''})"
        return "no-op (lane drained)"  # NOOP

    # Unknown family — surface the raw severity so nothing is silently swallowed.
    return sev.value.lower()


# ---------------------------------------------------------------------------
# Per-sink thresholds — the one I/O concession (an env read), kept beside the
# classifier so a consumer has a single import. Defaults make the common case quiet.
# ---------------------------------------------------------------------------
# Each sink: (neutral env key, JOB_-prefixed back-compat key, quiet default). The
# kernel-NEUTRAL `DISPATCH_*_MIN_SEVERITY` is the PRIMARY namespace; the host-branded
# `JOB_DISPATCH_*_MIN_SEVERITY` is a documented BACK-COMPAT fallback (the same
# generic-primary / JOB_-fallback shape `lane_journal` uses for its journal-path env).
# Before the userland-coupling audit 2026-06-08 the JOB_ key was the SOLE namespace,
# so a generic workspace had no neutral surface for these thresholds.
_SINK_ENV: dict[str, tuple[str, str, Severity]] = {
    # what peers pulling main see — the highest bar
    "push": ("DISPATCH_PUSH_MIN_SEVERITY", "JOB_DISPATCH_PUSH_MIN_SEVERITY", Severity.BLOCKED_NEW),
    # local history / audit — keep everything (coalesced, not per-iter, in Phase 2)
    "commit": ("DISPATCH_COMMIT_MIN_SEVERITY", "JOB_DISPATCH_COMMIT_MIN_SEVERITY", Severity.NOOP),
    # the live heartbeat stream
    "terminal": ("DISPATCH_TERMINAL_MIN_SEVERITY", "JOB_DISPATCH_TERMINAL_MIN_SEVERITY", Severity.NOTICE),
    # the end-of-run report block (absence-as-signal Attention line)
    "report": ("DISPATCH_REPORT_MIN_SEVERITY", "JOB_DISPATCH_REPORT_MIN_SEVERITY", Severity.NOTICE),
    # the docs/ README tree (the result.json envelope is EXEMPT — load-bearing)
    "artifact": ("DISPATCH_ARTIFACT_MIN_SEVERITY", "JOB_DISPATCH_ARTIFACT_MIN_SEVERITY", Severity.NOTICE),
}

SINKS: tuple[str, ...] = tuple(_SINK_ENV)


def sink_threshold(sink: str) -> Severity:
    """The configured MIN severity a sink admits. The ONLY I/O — an env read. The
    kernel-neutral `DISPATCH_*_MIN_SEVERITY` wins; the host-branded
    `JOB_DISPATCH_*_MIN_SEVERITY` is checked as a documented back-compat fallback.
    An unset or unparseable value falls back to the (quiet) default for that sink."""
    try:
        neutral_key, job_key, default = _SINK_ENV[sink]
    except KeyError as exc:
        raise ValueError(f"unknown sink {sink!r}; known: {', '.join(SINKS)}") from exc
    raw = (os.environ.get(neutral_key) or os.environ.get(job_key) or "").strip().upper()
    if not raw:
        return default
    try:
        return Severity(raw)
    except ValueError:
        return default


def admits(sink: str, sev: Severity) -> bool:
    """True iff an event of `sev` clears `sink`'s threshold (rank >= threshold rank)."""
    return _RANK[sev] >= _RANK[sink_threshold(sink)]
