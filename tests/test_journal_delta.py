"""LVN Phase 2 — the pure lane-journal progress fold (docs/82, journal_delta.py).

`journal_delta.fold_since` grounds the liveness verdict's heartbeat + lease-event
evidence in the lane journal so the SPINNING-vs-STALLED line comes from kernel
evidence, not a caller-supplied number. These tests pin the fold on FROZEN entry
lists (no disk, the `lane_journal.replay()` discipline) — the journal read is the
CLI's boundary job; the fold is pure.

The hard problem the fold resolves: a journal entry carries NO run-id (only a
`(loop_ts, lane)` lease key), so "did THIS run move?" is attributed on two axes —
IDENTITY (scoped to this run's `lease_key`; **required** — no host-wide guess) and
TIME (the entry's own append `ts`, strict `>` the floored run start for the
ADVANCING-gating event rung). Every degrade fails toward STALLED/SPINNING.
"""

from __future__ import annotations

import datetime as dt

from dos import journal_delta
from dos.journal_delta import JournalDelta, _parse_journal_ts, fold_since
from dos.liveness import Liveness, LivenessPolicy, ProgressEvidence, classify

_MIN = 60 * 1000
_POLICY = LivenessPolicy(grace_ms=10 * _MIN, spin_ms=5 * _MIN)

# THIS run's lease identity + a neighbor's, used throughout.
_MINE = ("2026-06-01T14:00Z", "apply")
_NEIGHBOR = ("2026-06-01T14:00Z", "tailor")

# A concrete run-start aligned to a second boundary, and an ISO stamp for it.
_STARTED = 1_780_000_000_000  # ms; % 1000 == 0
_NOW = _STARTED + 40 * _MIN


def _iso(ms: int) -> str:
    """The exact stamp `lane_journal.journal_now_iso` writes for an epoch-ms."""
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _entry(op: str, *, key=_MINE, at_ms: int, heartbeat_at: str | None = None) -> dict:
    """A journal entry shaped like lane_journal writes: op + (loop_ts, lane) + ts."""
    loop_ts, lane = key
    e = {"op": op, "loop_ts": loop_ts, "lane": lane, "ts": _iso(at_ms)}
    if heartbeat_at is not None:
        e["heartbeat_at"] = heartbeat_at
    return e


# ---------------------------------------------------------------------------
# 1. _parse_journal_ts — the tz-aware, two-format stamp parser.
# ---------------------------------------------------------------------------


def test_parse_journal_ts_known_epoch_ms():
    """A known UTC ISO string maps to a known epoch-ms; the tzinfo-replace is
    load-bearing (a naive timestamp() would shift by the host UTC offset)."""
    # 2021-01-01T00:00:00Z == 1609459200000 ms (a fixed, hand-checkable instant).
    assert _parse_journal_ts("2021-01-01T00:00:00Z") == 1_609_459_200_000
    # The minute-only form (a foreign/lease-copied stamp) parses to :00 of the minute.
    assert _parse_journal_ts("2021-01-01T00:00Z") == 1_609_459_200_000
    # Round-trips our own writer's format for an arbitrary instant.
    assert _parse_journal_ts(_iso(_STARTED)) == _STARTED
    # Everything unparseable/missing → None (never raises).
    for bad in (None, "", "garbage", "2021-01-01T00:00:00.5Z", "2021-01-01T00:00:00+00:00"):
        assert _parse_journal_ts(bad) is None


# ---------------------------------------------------------------------------
# 2. Empty / missing journal — the floor.
# ---------------------------------------------------------------------------


def test_fold_empty_is_zero_none():
    """An empty entry list → (0 events, no heartbeat, no corruption) — the
    missing/empty-journal floor the CLI passes through to the commit rung."""
    assert fold_since([], run_started_ms=_STARTED, now_ms=_NOW, lease_key=_MINE) == \
        JournalDelta(0, None, False)


# ---------------------------------------------------------------------------
# 3. The docs/82 2c litmus — corrupt journal degrades safe.
# ---------------------------------------------------------------------------


def test_corrupt_journal_degrades_safe():
    """A _CORRUPT sentinel never invents an event or a beat, never raises, and is
    reported via saw_corrupt — corruption can only REDUCE observed progress."""
    entries = [
        _entry("ACQUIRE", at_ms=_STARTED),                  # boundary (excluded, == floor)
        {"op": "_CORRUPT", "_raw": "{bad", "_line": 1},     # mid-file corruption
        _entry("RECONCILE", at_ms=_STARTED + 5 * _MIN),     # one real later lease-work event
    ]
    jd = fold_since(entries, run_started_ms=_STARTED, now_ms=_NOW, lease_key=_MINE)
    assert jd.events_since_start == 1          # only the real later RECONCILE
    assert jd.newest_heartbeat_age_ms is not None  # the boundary ACQUIRE is the beat
    assert jd.saw_corrupt is True
    # An all-corrupt journal → (0, None, True): with 0 commits this is STALLED.
    allc = fold_since(
        [{"op": "_CORRUPT", "_raw": "x", "_line": i} for i in range(3)],
        run_started_ms=_STARTED, now_ms=_NOW, lease_key=_MINE,
    )
    assert allc == JournalDelta(0, None, True)
    ev = ProgressEvidence(run_started_ms=_STARTED, now_ms=_NOW, commits_since_start=0,
                          journal_events_since=allc.events_since_start,
                          last_heartbeat_age_ms=allc.newest_heartbeat_age_ms)
    assert classify(ev, _POLICY).verdict is Liveness.STALLED


# ---------------------------------------------------------------------------
# 4. The docs/82 2a litmus — a journal event (no commit) is ADVANCING.
# ---------------------------------------------------------------------------


def test_journal_event_without_commit_is_advancing():
    """An ACQUIRE at start PLUS a later lease-work op (RECONCILE) for THIS lease →
    events ≥1; with 0 commits, classify → ADVANCING (lease-layer progress). A
    later op that is a *work* transition, not a keepalive HEARTBEAT, is what
    counts as progress."""
    entries = [
        _entry("ACQUIRE", at_ms=_STARTED),                   # excluded (boundary)
        _entry("RECONCILE", at_ms=_STARTED + 3 * _MIN),      # real interval lease work
    ]
    jd = fold_since(entries, run_started_ms=_STARTED, now_ms=_NOW, lease_key=_MINE)
    assert jd.events_since_start >= 1
    ev = ProgressEvidence(run_started_ms=_STARTED, now_ms=_NOW, commits_since_start=0,
                          journal_events_since=jd.events_since_start,
                          last_heartbeat_age_ms=jd.newest_heartbeat_age_ms)
    v = classify(ev, _POLICY)
    assert v.verdict is Liveness.ADVANCING
    assert "lease layer" in v.reason


def test_lone_boundary_acquire_is_not_advancing():
    """A journal whose ONLY entry is the run's own opening ACQUIRE → events 0 via
    the lease-birth exclusion: a held-but-idle lane is never ADVANCING-by-journal
    forever. (Here the ACQUIRE also floors to the start second, but the exclusion
    is by identity, not the floor — see the seconds-after-mint test below.)"""
    jd = fold_since([_entry("ACQUIRE", at_ms=_STARTED)],
                    run_started_ms=_STARTED, now_ms=_NOW, lease_key=_MINE)
    assert jd.events_since_start == 0
    # ...but the ACQUIRE still counts as a heartbeat (proof the lease is alive).
    assert jd.newest_heartbeat_age_ms == 40 * _MIN


def test_lease_birth_acquire_excluded_even_when_seconds_after_mint():
    """ROOT-CAUSE regression (docs/82 false-clear). In a REAL dispatch the run-id
    is minted first and the lane lease is ACQUIREd seconds later (after preflight/
    snapshot/gate), so the establishing ACQUIRE lands strictly AFTER the run-start
    second-floor. The old `> floor` rule counted it as a lease-work event, so a
    held-but-idle lane that did nothing but take its lease + emit keepalives read
    ADVANCING forever and SPINNING was unreachable. The lease-birth exclusion is by
    IDENTITY (the first ACQUIRE for this lease), so it holds regardless of the gap."""
    acquire_at = _STARTED + 7 * 1000  # ACQUIRE 7 s after the run-id was minted
    entries = [
        _entry("ACQUIRE", at_ms=acquire_at),                 # the lease's birth — NOT progress
        _entry("HEARTBEAT", at_ms=_NOW - _MIN),              # a keepalive — alive, not progress
    ]
    jd = fold_since(entries, run_started_ms=_STARTED, now_ms=_NOW, lease_key=_MINE)
    assert jd.events_since_start == 0          # the birth ACQUIRE did NOT count (was 1 before the fix)
    assert jd.newest_heartbeat_age_ms == _MIN  # still alive (the keepalive is the newest beat)
    # End-to-end: alive + past grace + zero progress is SPINNING, the verdict the
    # feature exists to produce and that the false-clear made unreachable.
    ev = ProgressEvidence(run_started_ms=_STARTED, now_ms=_NOW, commits_since_start=0,
                          journal_events_since=jd.events_since_start,
                          last_heartbeat_age_ms=jd.newest_heartbeat_age_ms)
    assert classify(ev, _POLICY).verdict is Liveness.SPINNING


def test_reacquire_after_release_still_counts():
    """The birth exclusion drops ONLY the establishing ACQUIRE. A later RELEASE and
    a genuine RE-ACQUIRE are real lease-layer work and still count — the exclusion
    must not silently swallow every ACQUIRE."""
    entries = [
        _entry("ACQUIRE", at_ms=_STARTED + 7 * 1000),        # birth (excluded)
        _entry("RELEASE", at_ms=_STARTED + 5 * _MIN),        # real work
        _entry("ACQUIRE", at_ms=_STARTED + 9 * _MIN),        # re-acquire = real work
    ]
    jd = fold_since(entries, run_started_ms=_STARTED, now_ms=_NOW, lease_key=_MINE)
    assert jd.events_since_start == 2


def test_same_second_pre_start_op_excluded():
    """A run started mid-second; a same-floored-second op of THIS lease — stamped
    before the run's true sub-second start — is NOT counted (strict `>` floor)."""
    started = _STARTED + 750  # 750 ms into the second
    # The op's ts floors to _STARTED (the second), == the floor, so strict `>` excludes it.
    jd = fold_since([_entry("RELEASE", at_ms=_STARTED)],
                    run_started_ms=started, now_ms=started + 40 * _MIN, lease_key=_MINE)
    assert jd.events_since_start == 0


# ---------------------------------------------------------------------------
# 5. Identity scope — the cross-run bleed defenses (require identity always).
# ---------------------------------------------------------------------------


def test_neighbor_lane_events_do_not_advance_this_run():
    """A neighbor lane's ACQUIRE+HEARTBEAT after this run's start never raise THIS
    run's event count — a busy neighbor can't manufacture ADVANCING."""
    entries = [
        _entry("ACQUIRE", key=_NEIGHBOR, at_ms=_STARTED + 1 * _MIN),
        _entry("HEARTBEAT", key=_NEIGHBOR, at_ms=_STARTED + 2 * _MIN),
    ]
    assert fold_since(entries, run_started_ms=_STARTED, now_ms=_NOW,
                      lease_key=_MINE).events_since_start == 0
    # And with NO identity (lease_key=None) the journal rungs are silent entirely
    # (the "require identity always" decision: no host-wide guess).
    assert fold_since(entries, run_started_ms=_STARTED, now_ms=_NOW,
                      lease_key=None) == JournalDelta(0, None, False)


def test_neighbor_heartbeat_does_not_keep_dead_run_alive():
    """Scoped to THIS lease, a neighbor's fresh HEARTBEAT contributes no beat — a
    dead run with no own beat is None → STALLED, never kept alive by a neighbor."""
    entries = [_entry("HEARTBEAT", key=_NEIGHBOR, at_ms=_NOW - 1 * _MIN)]  # neighbor fresh
    jd = fold_since(entries, run_started_ms=_STARTED, now_ms=_NOW, lease_key=_MINE)
    assert jd.newest_heartbeat_age_ms is None
    ev = ProgressEvidence(run_started_ms=_STARTED, now_ms=_NOW, commits_since_start=0,
                          journal_events_since=jd.events_since_start,
                          last_heartbeat_age_ms=jd.newest_heartbeat_age_ms)
    assert classify(ev, _POLICY).verdict is Liveness.STALLED


# ---------------------------------------------------------------------------
# 6. Heartbeat freshness — own ts, not the copy-prone heartbeat_at; skew guard.
# ---------------------------------------------------------------------------


def test_freshness_uses_own_ts_not_stale_heartbeat_at():
    """A HEARTBEAT's own append ts decides freshness; a stale carried heartbeat_at
    does NOT understate it (LVN distrusts the self-reported field over the append
    record). A pure HEARTBEAT is a beat but NOT a lease-work event (it is a
    keepalive), so it contributes freshness without manufacturing ADVANCING."""
    stale = _iso(_STARTED - 60 * _MIN)  # a stale copied heartbeat_at
    e = _entry("HEARTBEAT", at_ms=_NOW - 1 * _MIN, heartbeat_at=stale)  # own ts fresh
    jd = fold_since([e], run_started_ms=_STARTED, now_ms=_NOW, lease_key=_MINE)
    assert jd.events_since_start == 0                  # HEARTBEAT is not a work event
    assert jd.newest_heartbeat_age_ms == 1 * _MIN      # own ts, not the stale field

    # A RECONCILE (lease-work) whose own ts is after start counts as an event even
    # when its carried heartbeat_at is stale — own ts decides event-membership too.
    work = _entry("RECONCILE", at_ms=_NOW - 2 * _MIN, heartbeat_at=stale)
    assert fold_since([work], run_started_ms=_STARTED, now_ms=_NOW,
                      lease_key=_MINE).events_since_start == 1


def test_heartbeat_at_used_only_when_ts_missing():
    """A foreign entry with no own `ts` falls back to heartbeat_at (last resort)."""
    e = {"op": "HEARTBEAT", "loop_ts": _MINE[0], "lane": _MINE[1],
         "heartbeat_at": _iso(_NOW - 2 * _MIN)}  # no "ts" key
    jd = fold_since([e], run_started_ms=_STARTED, now_ms=_NOW, lease_key=_MINE)
    assert jd.newest_heartbeat_age_ms == 2 * _MIN


def test_unparseable_ts_entry_dropped_safely():
    """An entry with an unparseable ts (and no heartbeat_at) is dropped — never
    counted, never a beat, never a crash."""
    e = {"op": "HEARTBEAT", "loop_ts": _MINE[0], "lane": _MINE[1], "ts": "not-a-date"}
    assert fold_since([e], run_started_ms=_STARTED, now_ms=_NOW, lease_key=_MINE) == \
        JournalDelta(0, None, False)


def test_future_dated_beat_is_dropped_not_clamped():
    """A beat dated implausibly in the future (> now + 1s) is EXCLUDED (skew /
    forgery is not credible life), failing toward STALLED — not clamped to age 0.
    A sub-second-future beat within the 1s slack is kept and clamps to age 0."""
    far_future = _entry("HEARTBEAT", at_ms=_NOW + 10 * _MIN)
    jd = fold_since([far_future], run_started_ms=_STARTED, now_ms=_NOW, lease_key=_MINE)
    assert jd.newest_heartbeat_age_ms is None  # dropped, not clamped → STALLED input
    # Within the 1s slack: a beat stamped at second _NOW, evaluated when `now` is
    # 600 ms earlier (mid-previous-second), is 600 ms "future" — inside the 1000
    # ms slack, so it is KEPT and its age clamps to 0 (freshest), not a negative.
    near = _entry("HEARTBEAT", at_ms=_NOW)            # decodes to exactly _NOW
    jd2 = fold_since([near], run_started_ms=_STARTED, now_ms=_NOW - 600, lease_key=_MINE)
    assert jd2.newest_heartbeat_age_ms == 0


def test_future_dated_event_is_not_advancing():
    """A future-dated lease-WORK op (beyond now + 1s slack) is NOT counted as an
    event — the event rung carries the SAME future-credibility upper bound as the
    heartbeat rung. Events ≥1 is the top-of-ladder ADVANCING verdict, so a skewed/
    forged future op must fail toward SPINNING/STALLED, never invent ADVANCING
    (the asymmetry an adversarial review caught: the gate-the-most-consequential-
    verdict rung must be the best-guarded, not the worst)."""
    # A spinning run (fresh beat, 0 real progress) with one future-dated RECONCILE:
    entries = [
        _entry("HEARTBEAT", at_ms=_NOW - 2 * _MIN),         # fresh beat (alive)
        _entry("RECONCILE", at_ms=_NOW + 90 * _MIN),        # implausibly future op
    ]
    jd = fold_since(entries, run_started_ms=_STARTED, now_ms=_NOW, lease_key=_MINE)
    assert jd.events_since_start == 0  # the future op is dropped, not counted
    ev = ProgressEvidence(run_started_ms=_STARTED, now_ms=_NOW, commits_since_start=0,
                          journal_events_since=jd.events_since_start,
                          last_heartbeat_age_ms=jd.newest_heartbeat_age_ms)
    # Without the false ADVANCING, the fresh beat correctly reads SPINNING.
    assert classify(ev, _POLICY).verdict is Liveness.SPINNING
    # A within-slack "future" op (second-flooring slack) IS still counted.
    near = _entry("RECONCILE", at_ms=_NOW)  # decodes to exactly _NOW
    jd2 = fold_since([near], run_started_ms=_STARTED, now_ms=_NOW - 600, lease_key=_MINE)
    assert jd2.events_since_start == 1


# ---------------------------------------------------------------------------
# 7. The SPINNING/STALLED pair, separated purely by the journal-derived age.
# ---------------------------------------------------------------------------


def test_fresh_heartbeat_no_progress_is_spinning():
    """A fresh HEARTBEAT beat (age ≤ spin_ms), 0 lease-work events, 0 commits,
    run-age ≥ grace → SPINNING, driven by the journal-derived age. The HEARTBEAT
    is a keepalive (a beat, not an event), so it proves life without progress —
    the exact docs/82 SPINNING shape (alive, narrating, not moving)."""
    # HEARTBEAT 2 min ago: alive (≤ 5-min spin), run 40 min old (≥ 10-min grace).
    jd = fold_since([_entry("HEARTBEAT", at_ms=_NOW - 2 * _MIN)],
                    run_started_ms=_STARTED, now_ms=_NOW, lease_key=_MINE)
    assert jd.events_since_start == 0
    assert jd.newest_heartbeat_age_ms == 2 * _MIN
    ev = ProgressEvidence(run_started_ms=_STARTED, now_ms=_NOW, commits_since_start=0,
                          journal_events_since=0,
                          last_heartbeat_age_ms=jd.newest_heartbeat_age_ms)
    assert classify(ev, _POLICY).verdict is Liveness.SPINNING


def test_stale_heartbeat_is_stalled():
    """The newest own-ts beat is older than spin_ms (no later events, 0 commits)
    → STALLED — the same shape as SPINNING but for the journal-derived age."""
    jd = fold_since([_entry("ACQUIRE", at_ms=_NOW - 35 * _MIN)],
                    run_started_ms=_STARTED, now_ms=_NOW, lease_key=_MINE)
    assert jd.newest_heartbeat_age_ms == 35 * _MIN  # > 5-min spin window
    ev = ProgressEvidence(run_started_ms=_STARTED, now_ms=_NOW, commits_since_start=0,
                          journal_events_since=0,
                          last_heartbeat_age_ms=jd.newest_heartbeat_age_ms)
    assert classify(ev, _POLICY).verdict is Liveness.STALLED


# ---------------------------------------------------------------------------
# 8. Purity — the fold touches no disk, no clock, no subprocess.
# ---------------------------------------------------------------------------


def test_fold_is_pure(monkeypatch):
    """`fold_since` makes no subprocess/file/clock call — entries in, numbers out
    (the lane_journal.replay discipline). Poison the I/O surfaces and assert a
    clean fold still comes back across every branch."""
    import builtins
    import subprocess
    import time as _time

    def _boom(*a, **k):  # pragma: no cover - only runs if purity is violated
        raise AssertionError("fold_since performed I/O — it must be pure")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(builtins, "open", _boom)
    monkeypatch.setattr(_time, "time", _boom)

    entries = [
        _entry("ACQUIRE", at_ms=_STARTED),                 # boundary beat (not an event)
        _entry("RECONCILE", at_ms=_STARTED + 5 * _MIN),    # a real lease-work event
        _entry("HEARTBEAT", at_ms=_NOW - 1 * _MIN),        # fresh beat (not an event)
        {"op": "_CORRUPT", "_raw": "x"},
        _entry("RELEASE", key=_NEIGHBOR, at_ms=_NOW),      # neighbor — excluded
    ]
    jd = fold_since(entries, run_started_ms=_STARTED, now_ms=_NOW, lease_key=_MINE)
    assert jd.events_since_start == 1 and jd.newest_heartbeat_age_ms == 1 * _MIN
    assert jd.saw_corrupt is True


# ---------------------------------------------------------------------------
# 9. OP_CHECKPOINT is invisible to the liveness fold — it can never FABRICATE an
#    event or a beat. Genuine evidence is carried separately in metadata.
# ---------------------------------------------------------------------------


def test_checkpoint_is_neither_event_nor_beat():
    """OP_CHECKPOINT is in NEITHER _EVENT_OPS nor _HEARTBEAT_OPS, so a compaction
    snapshot can never FABRICATE an ADVANCING event or a proof-of-life beat — the
    one direction that matters for the false-SPINNING catastrophe. A legacy bare
    checkpoint has no historical evidence; its own timestamp must still contribute
    nothing. New evidence metadata retains original rows rather than refreshing
    their timestamps."""
    from dos.journal_delta import _EVENT_OPS, _HEARTBEAT_OPS
    from dos.lane_journal import OP_CHECKPOINT

    assert OP_CHECKPOINT not in _EVENT_OPS
    assert OP_CHECKPOINT not in _HEARTBEAT_OPS

    # A checkpoint entry with this run's identity contributes nothing to the fold.
    entries = [
        {"op": OP_CHECKPOINT, "loop_ts": _MINE[0], "lane": _MINE[1],
         "ts": _iso(_STARTED + 1 * _MIN), "leases": []},
    ]
    jd = fold_since(entries, run_started_ms=_STARTED, now_ms=_NOW, lease_key=_MINE)
    assert jd.events_since_start == 0
    assert jd.newest_heartbeat_age_ms is None
