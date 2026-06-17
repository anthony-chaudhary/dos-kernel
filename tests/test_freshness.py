"""FRS — the freshness verdict + the pure classifier (the cron dead-man's switch).

`freshness.classify` is the temporal completion of `pulse` turned on the WATCHERS:
a PURE verdict over a recurring job's last-beat age vs its declared cadence, the
`liveness.classify` shape lifted from "is this RUN moving?" to "is this JOB still
firing?". These tests pin the ladder on FROZEN ages (no clock, no ledger) plus the
pure folds (`latest_beats`, `fold`, `parse_cadence`).

The ladder under test:

  1. FRESH   — age <= cadence × grace_factor.
  2. LATE    — grace bound < age <= cadence × dead_factor.
  3. MISSING — age > cadence × dead_factor, OR no beat at all (None).
"""

from __future__ import annotations

import pytest

from dos import freshness
from dos.freshness import (
    CadencePolicy,
    Freshness,
    FreshnessVerdict,
    JobCadence,
    classify,
    fold,
    latest_beats,
    parse_cadence,
)

_MIN = 60 * 1000
_HOUR = 60 * _MIN
# A 6h cadence with the default factors: FRESH <= 9h (×1.5), LATE <= 18h (×3.0),
# MISSING beyond. The numbers below are chosen to sit cleanly inside each band.
_CADENCE = 6 * _HOUR


# ---------------------------------------------------------------------------
# 1. The three rungs, on frozen ages (the core litmus).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "age_ms, expected",
    [
        (0, Freshness.FRESH),                 # just beat
        (5 * _HOUR, Freshness.FRESH),         # within nominal cadence
        (9 * _HOUR, Freshness.FRESH),         # exactly the ×1.5 grace bound (inclusive)
        (9 * _HOUR + 1, Freshness.LATE),      # one ms past grace
        (12 * _HOUR, Freshness.LATE),         # missed a window, not dead
        (18 * _HOUR, Freshness.LATE),         # exactly the ×3.0 dead bound (inclusive)
        (18 * _HOUR + 1, Freshness.MISSING),  # one ms past dead
        (48 * _HOUR, Freshness.MISSING),      # long dead
    ],
)
def test_ladder_boundaries(age_ms, expected):
    v = classify(job_id="pulse", last_beat_age_ms=age_ms, cadence_ms=_CADENCE)
    assert v.verdict is expected
    assert v.job_id == "pulse"
    assert v.cadence_ms == _CADENCE
    assert v.last_beat_age_ms == age_ms


def test_never_beat_is_missing():
    """A declared job with no beat at all is MISSING — the silent-death catch."""
    v = classify(job_id="supervise", last_beat_age_ms=None, cadence_ms=_CADENCE)
    assert v.verdict is Freshness.MISSING
    assert v.last_beat_age_ms is None
    assert "no beat ever recorded" in v.reason


def test_future_beat_is_fresh_not_alarmed():
    """A beat stamped in the future (clock skew) is FRESH, never MISSING.

    A freshness verdict must never be MORE alarmed by a too-new beat than a
    just-right one — the safe direction for cross-host skew.
    """
    v = classify(job_id="pulse", last_beat_age_ms=-3 * _MIN, cadence_ms=_CADENCE)
    assert v.verdict is Freshness.FRESH
    assert "future" in v.reason


def test_is_problem_property():
    fresh = classify(job_id="j", last_beat_age_ms=0, cadence_ms=_CADENCE)
    late = classify(job_id="j", last_beat_age_ms=12 * _HOUR, cadence_ms=_CADENCE)
    missing = classify(job_id="j", last_beat_age_ms=None, cadence_ms=_CADENCE)
    assert not fresh.is_problem
    assert late.is_problem
    assert missing.is_problem


# ---------------------------------------------------------------------------
# 2. Policy — the slack factors are tunable and validated.
# ---------------------------------------------------------------------------


def test_custom_policy_widens_or_tightens_bands():
    # A tight policy (no grace, dead at 2×): age just over the cadence is already LATE.
    tight = CadencePolicy(grace_factor=1.0, dead_factor=2.0)
    v = classify(job_id="j", last_beat_age_ms=_CADENCE + 1, cadence_ms=_CADENCE, policy=tight)
    assert v.verdict is Freshness.LATE
    v2 = classify(job_id="j", last_beat_age_ms=2 * _CADENCE + 1, cadence_ms=_CADENCE, policy=tight)
    assert v2.verdict is Freshness.MISSING


def test_policy_validation():
    with pytest.raises(ValueError):
        CadencePolicy(grace_factor=0.5)  # a beat at the nominal cadence must be FRESH
    with pytest.raises(ValueError):
        CadencePolicy(grace_factor=2.0, dead_factor=1.5)  # MISSING must be past LATE


def test_jobcadence_validation():
    with pytest.raises(ValueError):
        JobCadence(job_id="j", cadence_ms=0)
    with pytest.raises(ValueError):
        JobCadence(job_id="j", cadence_ms=-1)


def test_classify_rejects_nonpositive_cadence():
    with pytest.raises(ValueError):
        classify(job_id="j", last_beat_age_ms=0, cadence_ms=0)


# ---------------------------------------------------------------------------
# 3. latest_beats — the pure ledger fold (max ts per job, torn-line tolerant).
# ---------------------------------------------------------------------------


def test_latest_beats_keeps_newest_per_job():
    records = [
        {"job": "pulse", "ts_ms": 100},
        {"job": "pulse", "ts_ms": 300},     # newer — wins
        {"job": "pulse", "ts_ms": 200},     # older, out of order — ignored
        {"job": "supervise", "ts_ms": 50},
    ]
    assert latest_beats(records) == {"pulse": 300, "supervise": 50}


def test_latest_beats_skips_torn_records():
    records = [
        {"job": "pulse", "ts_ms": 100},
        {"job": "", "ts_ms": 999},          # no job — skip
        {"job": "pulse"},                   # no ts — skip
        {"ts_ms": 999},                     # no job key — skip
        {"job": "pulse", "ts_ms": "nope"},  # unparseable ts — skip
        "garbage",                          # not a mapping — skip
    ]
    assert latest_beats(records) == {"pulse": 100}


# ---------------------------------------------------------------------------
# 4. fold — one verdict per DECLARED job; un-declared beats ignored.
# ---------------------------------------------------------------------------


def test_fold_classifies_each_declared_job():
    now = 1_000_000_000
    cadences = [
        JobCadence(job_id="pulse", cadence_ms=_CADENCE),
        JobCadence(job_id="supervise", cadence_ms=_CADENCE),
        JobCadence(job_id="enforce-tune", cadence_ms=_CADENCE),
    ]
    newest = {
        "pulse": now - 1 * _HOUR,       # FRESH
        "supervise": now - 12 * _HOUR,  # LATE
        # enforce-tune absent → MISSING
        "unknown": now,                 # not declared → ignored
    }
    verdicts = fold(now_ms=now, cadences=cadences, newest_beat_ms=newest)
    assert [v.verdict for v in verdicts] == [Freshness.FRESH, Freshness.LATE, Freshness.MISSING]
    # Declared order is preserved; the un-declared "unknown" produced no verdict.
    assert [v.job_id for v in verdicts] == ["pulse", "supervise", "enforce-tune"]


def test_fold_empty_declaration_is_empty():
    """No declared jobs → no verdicts (the no-config-no-noise rule)."""
    assert fold(now_ms=123, cadences=[], newest_beat_ms={"pulse": 1}) == ()


def test_fold_carries_critical_flag():
    now = 1_000_000_000
    cadences = [JobCadence(job_id="best-effort", cadence_ms=_CADENCE, critical=False)]
    (v,) = fold(now_ms=now, cadences=cadences, newest_beat_ms={})
    assert v.verdict is Freshness.MISSING
    assert v.critical is False


# ---------------------------------------------------------------------------
# 5. parse_cadence — the [heartbeats] table writes "6h", not ms.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected_ms",
    [
        ("90s", 90 * 1000),
        ("30m", 30 * _MIN),
        ("6h", 6 * _HOUR),
        ("1d", 24 * _HOUR),
        ("2w", 14 * 24 * _HOUR),
        ("45", 45 * 1000),     # bare string = seconds
        (45, 45 * 1000),       # bare int = seconds
        (1.5, 1500),           # float seconds
        ("6H", 6 * _HOUR),     # case-insensitive unit
        (" 6h ", 6 * _HOUR),   # surrounding whitespace
    ],
)
def test_parse_cadence_units(value, expected_ms):
    assert parse_cadence(value) == expected_ms


@pytest.mark.parametrize("bad", ["", "abc", "6y", "-5m", "0", 0, -1, True, "6 h h"])
def test_parse_cadence_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_cadence(bad)


# ---------------------------------------------------------------------------
# 6. The verdict is serializable (the --json / pulse-fold consumer).
# ---------------------------------------------------------------------------


def test_verdict_to_dict_round_trips_fields():
    v = classify(job_id="pulse", last_beat_age_ms=12 * _HOUR, cadence_ms=_CADENCE, critical=True)
    d = v.to_dict()
    assert d == {
        "job_id": "pulse",
        "verdict": "LATE",
        "reason": v.reason,
        "last_beat_age_ms": 12 * _HOUR,
        "cadence_ms": _CADENCE,
        "critical": True,
    }
