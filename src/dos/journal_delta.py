"""journal-delta — the lane-journal progress fold for the liveness verdict.

docs/82, LVN **Phase 2** — the journal + heartbeat rungs. Phase 1's heartbeat
age was caller-supplied (`--last-heartbeat-age-ms`); this grounds the heartbeat
and the lease-layer-event signal in the **lane journal** so the
SPINNING-vs-STALLED distinction comes from kernel evidence the agent can't
forge, not a passed number.

This module is `git_delta`'s sibling — the same boundary/evidence split LVN
Phase 1b established (`docs/82` 1b): the file read (`lane_journal.read_all`)
happens at the CLI boundary; the **fold here is PURE** — entries in, two numbers
out, the clock injected, no disk. It is replay-testable on frozen entry lists
exactly like `lane_journal.replay()`, which is what lets the whole liveness
ladder be tested without a live multi-minute agent run (the `loop_decide` design
value, restated for the temporal axis). `liveness.classify` stays byte-pure with
zero journal-schema awareness: Phase 2 only changes WHERE its two journal inputs
(`journal_events_since`, `last_heartbeat_age_ms`) come from.

It imports only stdlib + the lane-journal *op constants + identity helper* it
needs (the `OP_*` names + `_lease_identity`) — a one-way sibling-kernel import
(the same arrow `timeline`→`git_delta` has). It is
**never** imported BY `lane_journal` (whose job is lease correctness + replay,
not `ProgressEvidence`-shaped clock semantics).

THE HARD PROBLEM this fold resolves: a journal entry carries **no run-id** — it
is keyed only by `(loop_ts, lane)` (`lane_journal._lease_identity`). So "did
THIS run move?" cannot be answered from the journal by time alone — a busy
*neighbor* lane would otherwise manufacture a false ADVANCING for a spinning
run. The fold attributes on **two axes**:

  * IDENTITY — every journal rung is scoped to THIS run's lease, passed as
    `lease_key=(loop_ts, lane)`. Only entries whose `_lease_identity` matches
    contribute. **Identity is REQUIRED**: with `lease_key=None` the journal
    rungs do not engage at all (events forced to 0, no heartbeat) — there is no
    host-wide "is *some* lane alive" guess (that signal is too ambiguous to
    certify *this* run). The bare `dos liveness --run-id … --start-sha …`
    North-star form still answers from the commit rung; identity only unlocks
    the *journal* rungs. (Operator choice, 2026-06-01: require identity always.)
  * TIME — among identity-matched entries, an entry's **own append `ts`**
    (never the self-reported, copy-prone `heartbeat_at`) decides whether it
    falls in the run's window.

THE ROUNDING RULE — different per rung, deliberately:

  * EVENT rung (gates ADVANCING; over-counting is FORBIDDEN, docs/82 2c):
    a **bounded window** `(floored start, now + slack]` AND a **lease-birth
    exclusion**. The window is strictly after the run-start floored to its
    containing second (journal `ts` is second-resolution, the run-start is ms) AND
    no later than now plus the same one-second future slack the heartbeat rung
    uses. The lease-birth exclusion drops the FIRST ACQUIRE for this lease — the
    lease coming into existence is not progress on it — by IDENTITY, independent of
    its timestamp (a later re-ACQUIRE after a RELEASE still counts). A same-second
    *pre-start* op is NOT counted (the floor lower bound); the run's own
    establishing ACQUIRE is NOT counted (the birth exclusion); an implausibly
    future-dated op (clock skew / forgery / cross-host merge) is NOT counted (the
    upper bound). Because events ≥1 is the *top-of-ladder* ADVANCING verdict — the
    most consequential — this rung is the BEST-guarded, not the worst: every
    excluded op fails toward SPINNING/STALLED (safe), never invents ADVANCING. This
    fixes "a same-second pre-start op fabricates ADVANCING", "a lone boundary
    ACQUIRE marks a held-but-idle lane ADVANCING forever" (now by identity, so it
    holds even when the ACQUIRE lands seconds after the run-id mint — the real
    dispatch timeline the old `> floor` rule missed), AND "a future-skewed event
    fabricates ADVANCING on a stuck run".
  * HEARTBEAT-freshness rung (alive/dead; the generous direction is safe): the
    start floor does not gate freshness at all — freshness is about *now*, not
    the start window. A future-dated beat (clock skew / forged stamp) beyond the
    one-second slack is dropped (not clamped), failing toward STALLED.

Every degrade path fails toward STALLED/SPINNING and never raises (the ADM
fail-closed analogue): a `_CORRUPT` sentinel, an unparseable `ts`, an empty or
absent journal — none can invent progress or freshness. `saw_corrupt` is carried
for a future renderer's data-quality note (Phase 3); it does NOT flip the
verdict (the count-0/age-None degrade already fails safe) and is not threaded
into the (byte-unchanged) `ProgressEvidence`.
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable, NamedTuple, Optional

from dos.lane_journal import (  # sibling-kernel constants/helper (one-way import)
    OP_ACQUIRE,
    OP_HEARTBEAT,
    OP_RECONCILE,
    OP_RELEASE,
    OP_SCAVENGE,
    _lease_identity,
    evidence_entries,
)

# Ops that prove the lease is alive — a fresh ACQUIRE or HEARTBEAT for THIS
# lease. ACQUIRE stamps the lease's first beat; HEARTBEAT refreshes it (docs/82
# line 69, liveness.py:158).
_HEARTBEAT_OPS = frozenset({OP_ACQUIRE, OP_HEARTBEAT})

# Ops that count as lease-layer *work* (the ADVANCING event rung) — a deliberate
# subset of lane_journal._STATE_MUTATING_OPS that EXCLUDES HEARTBEAT. This is the
# crux of docs/82's ladder (lines 83-85): "fresh heartbeat … but zero …
# state-mutating journal events → SPINNING" explicitly separates the *freshness*
# signal (a heartbeat) from *progress* (state mutation). A HEARTBEAT is a
# keepalive — re-pinging a lease you already hold is the very definition of
# narrating-aliveness-without-moving — so it proves life (a beat) but is NOT
# forward progress (not an event). ACQUIRE/RELEASE/SCAVENGE/RECONCILE are real
# lease transitions: taking, dropping, evicting, or re-asserting a lease is work
# at the lease layer that the commit rung wouldn't see. (REFUSE grants nothing
# and _CORRUPT is not work — both already excluded.)
_EVENT_OPS = frozenset({OP_ACQUIRE, OP_RELEASE, OP_SCAVENGE, OP_RECONCILE})

# The op that BRINGS A LEASE INTO EXISTENCE. A lease is born with an ACQUIRE; that
# birth is the lease starting, NOT forward progress on it — exactly as a process's
# own fork is not "work the process did." The run's establishing ACQUIRE must
# therefore be excluded from the EVENT (ADVANCING) count, or a held-but-idle lane
# that did nothing but take its lease reads ADVANCING forever and SPINNING becomes
# unreachable (the docs/82 false-clear). The exclusion is by IDENTITY — "the first
# ACQUIRE for this lease" — not by timestamp: the prior `> floor` rule only excluded
# it when the ACQUIRE happened to land in the run-start second, which is false in
# every real dispatch (the lease is acquired seconds after the run-id is minted,
# past preflight/snapshot/gate). A LATER ACQUIRE (a genuine re-acquire after a
# RELEASE) is real lease work and still counts — only the establishing one is the
# lease's birth. Sibling `dispatch_top._events_by_lane` makes the same distinction
# by gating on the live lease's `acquired_at`.
_LEASE_BIRTH_OP = OP_ACQUIRE

# One second of slack on the future-beat guard: the journal `ts` is
# second-resolution while `now_ms` is millisecond, so a beat in the current
# second can legitimately decode to up to ~999 ms *after* now. Beyond this a
# beat is clock-skew or a forged future stamp — not credible proof-of-life.
_FUTURE_BEAT_SLACK_MS = 1000


class JournalDelta(NamedTuple):
    """The two numbers `ProgressEvidence` needs from the journal, plus a flag.

      events_since_start    — count of THIS-run lease-*work* ops (ACQUIRE/
                              RELEASE/SCAVENGE/RECONCILE, NOT a keepalive
                              HEARTBEAT) whose own append `ts` is strictly after
                              the floored run start. Flows to
                              `journal_events_since`; ≥1 is the lease-layer
                              ADVANCING rung (liveness.py:252).
      newest_heartbeat_age_ms — `now_ms − newest credible beat ts` for THIS
                              lease; None when there is no credible beat. Flows
                              to `last_heartbeat_age_ms`; None reads as STALLED
                              (the safe direction, liveness.py:303).
      saw_corrupt           — a `_CORRUPT` sentinel was present. Diagnostic
                              only: it does NOT change the verdict and is not
                              carried into `ProgressEvidence`/`to_dict` (those
                              stay byte-unchanged) — reserved for a Phase-3
                              renderer's data-quality note.
    """

    events_since_start: int
    newest_heartbeat_age_ms: Optional[int]
    saw_corrupt: bool


def _parse_journal_ts(s: Optional[str]) -> Optional[int]:
    """Parse a journal stamp to epoch-ms; None on any unparseable/missing input.

    PURE. Accepts both the second-resolution stamp `lane_journal.append` writes
    (`journal_now_iso`, ``%Y-%m-%dT%H:%M:%SZ``) and a minute-only stamp a
    foreign/lease-copied field might carry (``%Y-%m-%dT%H:%MZ``) — the exact
    two-format tolerance `archive_lock._parse_iso` uses. The explicit
    `tzinfo=utc` is LOAD-BEARING: a naive `timestamp()` would shift by the host
    UTC offset (pinned by `test_parse_journal_ts_known_epoch_ms`).

    NOTE: a third tiny copy of this kernel's ISO-parse (after
    `archive_lock._parse_iso` and `decisions._parse_iso`). Kept local — all are
    sibling kernel modules, no layer crossing — but a tz/format fix must land in
    all three; flagged for a possible future shared stdlib-only helper.
    """
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%MZ"):
        try:
            parsed = dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc)
        except (ValueError, TypeError):
            continue
        return int(parsed.timestamp() * 1000)
    return None


def fold_since(
    entries: Iterable[dict],
    *,
    run_started_ms: int,
    now_ms: int,
    lease_key: Optional[tuple[str, str]] = None,
) -> JournalDelta:
    """Fold journal entries into (events-since-start, newest-beat-age) for one run.

    PURE — entries in, numbers out, the clock injected (`now_ms`), no disk. The
    caller (`dos liveness`'s evidence-gather) does the `lane_journal.read_all`
    at the boundary and passes the materialized list here.

    `lease_key=(loop_ts, lane)` is THIS run's lease identity. **Identity is
    required for the journal rungs**: with `lease_key=None` the journal cannot be
    attributed to this run, so both rungs go silent — `JournalDelta(0, None,
    saw_corrupt)` — and the commit rung (plus any explicit
    `--last-heartbeat-age-ms` the caller layers on) decides. `saw_corrupt` is
    still reported so a corrupt journal is observable even without identity.

    The ladder this feeds (`liveness.classify`, unchanged): events ≥1 →
    ADVANCING (lease-layer progress); else a fresh beat age → alive (SPINNING if
    old enough); else None/stale → STALLED.
    """
    saw_corrupt = False
    events = 0
    newest_beat_ms: Optional[int] = None

    # A blank lease_key ('', '') is treated as NO identity (silent rungs), mirroring
    # lane_journal.replay's `if not key[0] and not key[1]: continue` (lane_journal.py
    # :257): the blank identity is the "no real lease" sentinel, not a lane to match.
    # The CLI never builds a blank key (its `if lane and loop_ts` guard yields None),
    # but a library caller could — so the fold itself refuses to attribute the
    # journal to a blank identity rather than match stray blank-keyed entries.
    if lease_key is not None and not lease_key[0] and not lease_key[1]:
        lease_key = None

    # The run-start floored to its containing second — journal `ts` is
    # second-resolution, so this is the coarsest instant an entry's second-stamp
    # can be compared against. STRICT `>` against this floor excludes a same-second
    # *pre-start* op (one stamped in the run-start second but before the run's true
    # sub-second start). It is NOT the boundary-ACQUIRE guard — that is the separate
    # lease-birth exclusion below, which is timestamp-independent.
    run_started_floor_ms = (run_started_ms // 1000) * 1000

    # The lease's establishing ACQUIRE — its BIRTH, not progress. Excluded from the
    # EVENT count by identity (the first ACQUIRE we see for this lease in append
    # order), never by timestamp. `False` until consumed; once we have skipped the
    # birth ACQUIRE, every later lease-work op (incl. a genuine re-ACQUIRE after a
    # RELEASE) counts as real progress. See `_LEASE_BIRTH_OP`.
    seen_lease_birth = False

    for e in evidence_entries(entries):
        op = str(e.get("op") or "")
        if op == "_CORRUPT":
            saw_corrupt = True
            continue  # corruption can only REDUCE observed progress, never invent it

        # IDENTITY axis — every journal rung is scoped to THIS run's lease. With
        # no identity, no entry can be attributed to this run: the rungs go silent.
        if lease_key is None:
            continue
        if _lease_identity(e) != lease_key:
            continue

        # The entry's OWN append ts is the trusted instant (never the
        # self-reported, copy-prone `heartbeat_at` — that is exactly the kind of
        # narration LVN distrusts). Fall back to `heartbeat_at` ONLY when `ts` is
        # missing/unparseable (a defensive last resort for a foreign writer).
        ts_ms = _parse_journal_ts(e.get("ts"))
        if ts_ms is None:
            ts_ms = _parse_journal_ts(e.get("heartbeat_at"))
        if ts_ms is None:
            continue  # can't place this entry in time → drop (the safe direction)

        # LEASE-BIRTH exclusion — the FIRST ACQUIRE for this lease is the lease
        # coming into existence, not forward progress on it. Skip exactly it from
        # the EVENT count (by identity, not timestamp), then mark the birth
        # consumed so a LATER re-ACQUIRE (after a RELEASE) is counted as real lease
        # work. This is the root fix for the docs/82 false-clear: the prior `>
        # floor` rule only excluded the birth ACQUIRE when it happened to land in
        # the run-start second — true in fixtures, false in every real dispatch
        # where the lease is acquired seconds after the run-id is minted, so a
        # held-but-idle lane's lone ACQUIRE was counted and it read ADVANCING
        # forever. The op still flows to the HEARTBEAT rung below (the birth ACQUIRE
        # IS proof the lease is alive — just not proof it moved).
        is_lease_birth = op == _LEASE_BIRTH_OP and not seen_lease_birth
        if op == _LEASE_BIRTH_OP:
            seen_lease_birth = True

        # EVENT rung — a lease-*work* op (ACQUIRE/RELEASE/SCAVENGE/RECONCILE, NOT
        # a HEARTBEAT keepalive, NOT the lease's birth ACQUIRE) for this lease, in
        # the window (floored start, now], is lease-layer forward progress (docs/82
        # 2a). Strict `>` the start floor excludes a same-second *pre-start* op; the
        # birth exclusion above excludes the establishing ACQUIRE regardless of when
        # it landed; the SAME future-credibility upper bound the heartbeat rung uses
        # (`<= now + slack`) drops an implausibly future-dated op (NTP step-back
        # between append and read, or the cross-host merge `lane_journal`
        # anticipates). Events ≥1 is the TOP-of-ladder ADVANCING rung — the most
        # consequential verdict — so it must be the BEST-guarded, not the worst: a
        # future-skewed event must fail toward SPINNING/STALLED, never invent
        # ADVANCING (docs/82 2c "over-counting is FORBIDDEN"; design law: never a
        # false ADVANCING). Excluding HEARTBEAT is what makes SPINNING reachable:
        # a fresh heartbeat proves life (a beat, below) without counting as
        # progress — docs/82's "fresh heartbeat … but zero state-mutating events
        # → SPINNING" ladder.
        if (
            op in _EVENT_OPS
            and not is_lease_birth
            and run_started_floor_ms < ts_ms <= now_ms + _FUTURE_BEAT_SLACK_MS
        ):
            events += 1

        # HEARTBEAT-freshness rung — a fresh ACQUIRE/HEARTBEAT proves the lease
        # is alive NOW (no start-window gate; freshness is about now). Drop a
        # beat dated implausibly in the future (skew/forgery) rather than clamp
        # it to age-0 — that would hide a dead run behind a forged stamp.
        if op in _HEARTBEAT_OPS and ts_ms <= now_ms + _FUTURE_BEAT_SLACK_MS:
            if newest_beat_ms is None or ts_ms > newest_beat_ms:
                newest_beat_ms = ts_ms

    # Age = now − newest credible beat, clamped at 0 (a sub-second-future beat
    # within the slack is the freshest possible, not a negative age — and
    # `ProgressEvidence` documents ages as ≥0).
    age_ms = None if newest_beat_ms is None else max(0, now_ms - newest_beat_ms)
    return JournalDelta(events_since_start=events, newest_heartbeat_age_ms=age_ms,
                        saw_corrupt=saw_corrupt)
