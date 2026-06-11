"""TRD — the cross-run efficiency trend: is work-per-token fading across runs? (docs/300 P3)

The trend completion of the efficiency family: `productivity` re-aimed from
per-step deltas onto cross-run work-per-token ratios. These tests pin the PURE
ladder on frozen samples (the CLI boundary + the journal fossil loop are pinned
in `test_efficiency_trend_cli.py`, docs/300 P4).

The verdict ladder under test:

  1. STEADY    — fewer than min_samples runs (and never fewer than 3): withhold.
  2. DEGRADING — the last TWO ratios both more than `tolerance` under the median
                 of the runs before them (sustained — one outlier cannot trip).
  3. IMPROVING — the last TWO ratios both more than `tolerance` above it.
  4. STEADY    — inside the band (run-to-run noise).
"""

from __future__ import annotations

import json

import pytest

from dos import efficiency_trend
from dos.efficiency_trend import (
    EfficiencyTrend,
    TrendHistory,
    TrendPolicy,
    classify,
)

# A readable policy: judge after 3 runs, quarter-band around the prior median.
_POLICY = TrendPolicy(min_samples=3, tolerance=0.25)


# ---------------------------------------------------------------------------
# The pure ladder, on frozen samples.
# ---------------------------------------------------------------------------


def test_too_little_history_is_steady():
    """Fewer than min_samples runs → STEADY-benign (withhold the judgment)."""
    v = classify(TrendHistory.of([(9, 1000), (8, 1000)]), _POLICY)
    assert v.verdict is EfficiencyTrend.STEADY
    assert "not enough history" in v.reason


def test_min_samples_is_floored_at_three():
    """Even a pathological min_samples=0 policy needs 3 runs (two recent + one
    baseline is the smallest readable shape)."""
    v = classify(TrendHistory.of([(9, 1000), (1, 1000)]), TrendPolicy(min_samples=0))
    assert v.verdict is EfficiencyTrend.STEADY
    assert "not enough history" in v.reason


def test_sustained_fall_is_degrading():
    """The last two runs both far under the prior median → DEGRADING."""
    # Ratios: 0.009, 0.008 (baseline median 0.0085), then 0.0015 and 0.00083.
    v = classify(
        TrendHistory.of([(9, 1000), (8, 1000), (3, 2000), (2, 2400)]), _POLICY
    )
    assert v.verdict is EfficiencyTrend.DEGRADING
    assert "fading" in v.reason


def test_one_outlier_run_does_not_trip():
    """One bad run with a recovered successor is noise, not a trend."""
    # Ratios: 0.009, 0.008, then ONE collapse (0.001) followed by recovery (0.009).
    v = classify(
        TrendHistory.of([(9, 1000), (8, 1000), (1, 1000), (9, 1000)]), _POLICY
    )
    assert v.verdict is EfficiencyTrend.STEADY


def test_sustained_rise_is_improving():
    # Ratios: 0.002, 0.003 (median 0.0025), then 0.008 and 0.009 — both far above.
    v = classify(
        TrendHistory.of([(2, 1000), (3, 1000), (8, 1000), (9, 1000)]), _POLICY
    )
    assert v.verdict is EfficiencyTrend.IMPROVING


def test_inside_the_band_is_steady():
    """Normal run-to-run noise inside ±tolerance → STEADY."""
    v = classify(
        TrendHistory.of([(10, 1000), (9, 1000), (11, 1000), (10, 1000)]), _POLICY
    )
    assert v.verdict is EfficiencyTrend.STEADY


def test_zero_baseline_with_nonzero_recent_is_improving():
    """From nothing to something is improvement, honestly."""
    v = classify(
        TrendHistory.of([(0, 5000), (0, 5000), (4, 1000), (5, 1000)]), _POLICY
    )
    assert v.verdict is EfficiencyTrend.IMPROVING


def test_baseline_is_robust_to_one_historical_outlier():
    """The baseline is the MEDIAN of prior runs — one historic freak ratio
    cannot poison the yardstick."""
    # Priors: 0.009, 0.9 (a freak), 0.008 → median 0.009; last two ~0.0008: DEGRADING.
    v = classify(
        TrendHistory.of(
            [(9, 1000), (900, 1000), (8, 1000), (1, 1200), (1, 1300)]
        ),
        _POLICY,
    )
    assert v.verdict is EfficiencyTrend.DEGRADING


def test_zero_token_sample_reads_as_zero_ratio():
    """A no-spend run is ratio 0.0 (the EfficiencyEvidence rule) — never a
    divide-by-zero."""
    h = TrendHistory.of([(5, 0), (5, 1000), (5, 1000)])
    assert h.ratios[0] == 0.0


def test_samples_reject_negative_counts():
    with pytest.raises(ValueError):
        TrendHistory.of([(-1, 100)])
    with pytest.raises(ValueError):
        TrendHistory.of([(1, -100)])


def test_policy_rejects_negative_thresholds():
    with pytest.raises(ValueError):
        TrendPolicy(min_samples=-1)
    with pytest.raises(ValueError):
        TrendPolicy(tolerance=-0.1)


def test_verdict_to_dict_round_trips_the_trend():
    """`to_dict` carries the verdict AND the falling sequence behind it."""
    v = classify(
        TrendHistory.of([(9, 1000), (8, 1000), (3, 2000), (2, 2400)]), _POLICY
    )
    d = v.to_dict()
    assert d["verdict"] == "DEGRADING"
    assert d["history"]["run_count"] == 4
    assert d["history"]["last_ratio"] == pytest.approx(2 / 2400)
    assert d["history"]["baseline"] == pytest.approx(0.0085)
    assert json.loads(json.dumps(d, sort_keys=True)) == d


def test_classify_makes_no_io(monkeypatch):
    """The fold is PURE — no clock, no file read (the family discipline)."""
    import builtins
    import time as _time

    def _boom(*a, **k):  # pragma: no cover - only fires on a violation
        raise AssertionError("classify must not perform I/O")

    monkeypatch.setattr(_time, "time", _boom)
    monkeypatch.setattr(builtins, "open", _boom)
    v = classify(TrendHistory.of([(9, 1000), (8, 1000), (1, 2000), (1, 2400)]), _POLICY)
    assert v.verdict is EfficiencyTrend.DEGRADING
