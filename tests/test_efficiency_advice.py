"""ADV — the efficiency advisor: the recommender capstone (docs/370).

`efficiency_advice.recommend` is the SECOND-ORDER verb of the loop-economics
family: where `efficiency` classifies one measured ratio, it folds a BUNDLE of
already-measured, env-authored signals (the provider spend split, the work count,
the no-op streak, the over-claim count, the cross-run trend verdict, the
serving-window count) and emits a RANKED list of typed, vendor-free
recommendations. It is the only reader that adjudicates the spend KPIs
`dos efficiency --usage-json` computes but never judges.

These tests pin:

  1. each recommendation rung — the structural waste rungs (armed) and the tuned
     ratio rungs (disabled by default, armed via policy);
  2. the withhold floor (min_tokens) and the "not measured ⇒ skipped" semantics;
  3. the severity ranking (CRITICAL first; ties keep catalogue order) and the
     CLEAN / ADVISE / WASTE verdict rollup;
  4. purity — `recommend` makes no I/O (no clock, no file);
  5. the to_dict round-trip (the --output json contract);
  6. construction validation (policy thresholds; signal counts);
  7. policy_from_table (the dos.toml on-ramp);
  8. the catalogue's own integrity (every kind has a severity);
  9. the CLI verb — the verdict IS the exit code (no plan, no git needed).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from dos.efficiency_advice import (
    Advice,
    AdvicePolicy,
    AdviceReport,
    DEFAULT_POLICY,
    EfficiencySignals,
    Recommendation,
    RecommendationKind,
    Severity,
    _KIND_SEVERITY,
    policy_from_table,
    recommend,
)
from dos.spend import SpendBreakdown


def _kinds(report: AdviceReport) -> list[str]:
    return [r.kind.value for r in report.recommendations]


# ---------------------------------------------------------------------------
# 1. The structural waste rungs — armed by default, always-correct.
# ---------------------------------------------------------------------------


def test_wasteful_spend_fires_on_zero_work_with_real_spend():
    r = recommend(EfficiencySignals(work=0, tokens=80000))
    assert RecommendationKind.WASTEFUL_SPEND.value in _kinds(r)
    assert r.verdict is Advice.WASTE  # a CRITICAL recommendation


def test_wasteful_spend_withheld_below_min_tokens():
    """0 work under the spend floor is a run that barely started, not waste."""
    r = recommend(EfficiencySignals(work=0, tokens=500))  # < default min_tokens 1000
    assert not r.recommendations
    assert r.verdict is Advice.CLEAN


def test_wasteful_spend_needs_work_measured():
    """work=None (unmeasured) cannot fire the rung — never guessed to 0."""
    r = recommend(EfficiencySignals(tokens=80000))
    assert RecommendationKind.WASTEFUL_SPEND.value not in _kinds(r)


def test_noop_spin_fires_at_budget():
    assert RecommendationKind.NOOP_SPIN.value in _kinds(
        recommend(EfficiencySignals(noop_turns=4)))
    # one under the budget does not fire
    assert RecommendationKind.NOOP_SPIN.value not in _kinds(
        recommend(EfficiencySignals(noop_turns=3)))


def test_degrading_trend_fires_only_on_true():
    assert RecommendationKind.DEGRADING_TREND.value in _kinds(
        recommend(EfficiencySignals(degrading_trend=True)))
    # False (measured, not degrading) and None (not measured) both stay silent
    assert not recommend(EfficiencySignals(degrading_trend=False)).recommendations
    assert not recommend(EfficiencySignals(degrading_trend=None)).recommendations


def test_overclaim_fires_above_zero():
    assert RecommendationKind.OVERCLAIM.value in _kinds(
        recommend(EfficiencySignals(overclaim=2)))
    assert not recommend(EfficiencySignals(overclaim=0)).recommendations


def test_seat_underutilized_fires_on_idle_windows():
    # 3 serving, fleet used 1 — idle throughput.
    assert RecommendationKind.SEAT_UNDERUTILIZED.value in _kinds(
        recommend(EfficiencySignals(serving_accounts=3, seats_used=1)))
    # all windows used — nothing to advise.
    assert not recommend(
        EfficiencySignals(serving_accounts=3, seats_used=3)).recommendations
    # a single serving window cannot be "under-spread".
    assert not recommend(
        EfficiencySignals(serving_accounts=1, seats_used=0)).recommendations


# ---------------------------------------------------------------------------
# 2. The tuned ratio rungs — DISABLED by default, armed via policy.
# ---------------------------------------------------------------------------


def test_tuned_rungs_disabled_by_default():
    """A heavy-reasoning, low-cache spend draws NOTHING until a host arms a floor
    (the efficiency.floor disabled-by-default discipline, generalized)."""
    sb = SpendBreakdown(input=100, output=5000, cache_read=50, cache_creation=0,
                        reasoning=4500)
    r = recommend(EfficiencySignals(spend=sb, work=5))
    assert r.verdict is Advice.CLEAN
    assert not r.recommendations


def test_cold_cache_fires_when_armed():
    # prefill 1000, only 100 cache hits ⇒ 10% hit, under a 0.5 floor.
    sb = SpendBreakdown(input=900, output=200, cache_read=100, cache_creation=0)
    pol = AdvicePolicy(cache_hit_floor=0.5)
    r = recommend(EfficiencySignals(spend=sb), pol)
    assert RecommendationKind.COLD_CACHE.value in _kinds(r)


def test_overthinking_fires_when_armed():
    sb = SpendBreakdown(input=100, output=5000, reasoning=4000)  # 80% reasoning
    r = recommend(EfficiencySignals(spend=sb), AdvicePolicy(reasoning_ceiling=0.5))
    assert RecommendationKind.OVERTHINKING.value in _kinds(r)


def test_decode_heavy_fires_when_armed():
    sb = SpendBreakdown(input=100, output=5000)  # output_share ~0.98
    r = recommend(EfficiencySignals(spend=sb), AdvicePolicy(output_ceiling=0.8))
    assert RecommendationKind.DECODE_HEAVY.value in _kinds(r)


def test_costly_ratio_fires_when_armed():
    # 1 work / 90000 tokens = 1.1e-5 work/token, under a 1e-4 floor.
    r = recommend(EfficiencySignals(work=1, tokens=90000),
                  AdvicePolicy(efficiency_floor=1e-4))
    assert RecommendationKind.COSTLY_RATIO.value in _kinds(r)
    # nonzero work is required — work=0 is WASTEFUL_SPEND, not COSTLY_RATIO.
    z = recommend(EfficiencySignals(work=0, tokens=90000),
                  AdvicePolicy(efficiency_floor=1e-4))
    assert RecommendationKind.COSTLY_RATIO.value not in _kinds(z)


def test_tuned_rungs_withheld_below_min_tokens():
    """Even armed, a spend-shape rung withholds under the min_tokens floor."""
    sb = SpendBreakdown(input=10, output=500, reasoning=450)  # total 510 < 1000
    r = recommend(EfficiencySignals(spend=sb), AdvicePolicy(reasoning_ceiling=0.5))
    assert not r.recommendations


# ---------------------------------------------------------------------------
# 3. Ranking + verdict rollup.
# ---------------------------------------------------------------------------


def test_ranking_is_critical_first_then_catalogue_order():
    sig = EfficiencySignals(
        work=0, tokens=80000,           # WASTEFUL_SPEND  (CRITICAL)
        noop_turns=5,                   # NOOP_SPIN       (CRITICAL)
        degrading_trend=True,           # DEGRADING_TREND (HIGH)
        overclaim=2,                    # OVERCLAIM       (HIGH)
        serving_accounts=3, seats_used=1,  # SEAT_UNDERUTILIZED (MEDIUM)
    )
    kinds = _kinds(recommend(sig))
    # CRITICAL pair leads, in catalogue order (WASTEFUL before NOOP).
    assert kinds[:2] == [
        RecommendationKind.WASTEFUL_SPEND.value,
        RecommendationKind.NOOP_SPIN.value,
    ]
    # then the HIGH pair, then the MEDIUM.
    assert kinds[2:4] == [
        RecommendationKind.DEGRADING_TREND.value,
        RecommendationKind.OVERCLAIM.value,
    ]
    assert kinds[4] == RecommendationKind.SEAT_UNDERUTILIZED.value


def test_verdict_clean_advise_waste():
    assert recommend(EfficiencySignals()).verdict is Advice.CLEAN
    # an OVERCLAIM (HIGH) alone is ADVISE, not WASTE.
    assert recommend(EfficiencySignals(overclaim=1)).verdict is Advice.ADVISE
    # any CRITICAL flips it to WASTE.
    assert recommend(EfficiencySignals(noop_turns=9)).verdict is Advice.WASTE


def test_top_and_of_severity_projections():
    r = recommend(EfficiencySignals(work=0, tokens=80000, overclaim=1))
    assert r.top.kind is RecommendationKind.WASTEFUL_SPEND
    assert len(r.of_severity(Severity.CRITICAL)) == 1
    assert len(r.of_severity(Severity.HIGH)) == 1
    assert r.of_severity(Severity.LOW) == ()


def test_clean_report_top_is_none():
    r = recommend(EfficiencySignals(work=5, tokens=3000))
    assert r.top is None
    assert "spending well" in r.reason


# ---------------------------------------------------------------------------
# 4. Purity — recommend makes NO I/O.
# ---------------------------------------------------------------------------


def test_recommend_is_pure(monkeypatch):
    """`recommend` is timeless: no clock, no file — a pure fold over the signals."""
    import builtins
    import time as _time

    def _boom(*a, **k):  # pragma: no cover - only fires on a violation
        raise AssertionError("recommend must not perform I/O")

    monkeypatch.setattr(_time, "time", _boom)
    monkeypatch.setattr(builtins, "open", _boom)
    r = recommend(EfficiencySignals(work=0, tokens=80000))
    assert r.verdict is Advice.WASTE


# ---------------------------------------------------------------------------
# 5. to_dict — the legible-distrust JSON shape.
# ---------------------------------------------------------------------------


def test_to_dict_round_trips_and_carries_evidence():
    sb = SpendBreakdown(input=100, output=5000, cache_read=50, reasoning=4000)
    r = recommend(EfficiencySignals(spend=sb, work=0),
                  AdvicePolicy(reasoning_ceiling=0.5))
    d = r.to_dict()
    assert d["verdict"] == r.verdict.value
    assert d["reason"] == r.reason
    # the spend split rides along (the operator sees the bytes behind the advice).
    assert d["signals"]["spend"]["reasoning_share"] == pytest.approx(0.8)
    # each recommendation carries its kind, severity, signal, advice, evidence.
    over = next(x for x in d["recommendations"] if x["kind"] == "OVERTHINKING")
    assert over["severity"] == "MEDIUM"
    assert over["evidence"]["reasoning"] == 4000
    # JSON-serializable, stable (the --output json contract).
    assert json.loads(json.dumps(d, sort_keys=True)) == d


def test_to_dict_omits_unmeasured_signals():
    """A signal the caller did not measure is absent from the JSON, never null=0."""
    d = recommend(EfficiencySignals(overclaim=1)).to_dict()
    assert "overclaim" in d["signals"]
    assert "work" not in d["signals"]
    assert "spend" not in d["signals"]


# ---------------------------------------------------------------------------
# 6. Validation — policy thresholds and signal counts.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kwargs", [
    {"min_tokens": -1},
    {"efficiency_floor": -0.5},
    {"cache_hit_floor": 1.5},
    {"cache_hit_floor": -0.1},
    {"reasoning_ceiling": 2.0},
    {"output_ceiling": -0.1},
    {"noop_budget": -1},
])
def test_bad_policy_rejected(kwargs):
    with pytest.raises(ValueError):
        AdvicePolicy(**kwargs)


@pytest.mark.parametrize("kwargs", [
    {"work": -1},
    {"tokens": -5},
    {"noop_turns": 1.5},
    {"overclaim": "2"},
    {"serving_accounts": True},  # bools are not counts
    {"degrading_trend": 1},      # must be a bool or None
    {"spend": {"input": 1}},     # must be a SpendBreakdown or None
])
def test_bad_signal_rejected(kwargs):
    with pytest.raises(ValueError):
        EfficiencySignals(**kwargs)


def test_tokens_spend_disagreement_is_a_contract_error():
    sb = SpendBreakdown(input=100, output=100)  # total 200
    with pytest.raises(ValueError):
        EfficiencySignals(spend=sb, tokens=999)


def test_tokens_derived_from_spend_when_absent():
    sb = SpendBreakdown(input=100, output=100)  # total 200
    sig = EfficiencySignals(spend=sb)
    assert sig.tokens == 200
    assert sig.ratio is None  # no work measured ⇒ no ratio


# ---------------------------------------------------------------------------
# 7. policy_from_table — the dos.toml on-ramp.
# ---------------------------------------------------------------------------


def test_policy_from_empty_table_is_default():
    assert policy_from_table({}) == DEFAULT_POLICY


def test_policy_from_table_reads_values():
    pol = policy_from_table({
        "min_tokens": 500, "cache_hit_floor": 0.4, "reasoning_ceiling": 0.6,
        "noop_budget": 6,
    })
    assert pol.min_tokens == 500
    assert pol.cache_hit_floor == 0.4
    assert pol.reasoning_ceiling == 0.6
    assert pol.noop_budget == 6


def test_policy_from_malformed_table_raises():
    with pytest.raises(ValueError):
        policy_from_table({"cache_hit_floor": 2.0})


# ---------------------------------------------------------------------------
# 8. Catalogue integrity — every kind has a severity.
# ---------------------------------------------------------------------------


def test_every_kind_has_a_severity():
    for kind in RecommendationKind:
        assert kind in _KIND_SEVERITY
        assert isinstance(_KIND_SEVERITY[kind], Severity)


def test_recommendation_to_dict_shape():
    rec = Recommendation(
        kind=RecommendationKind.OVERCLAIM, severity=Severity.HIGH,
        signal="2 claimed ships unverified", advice="tighten the done-condition",
        evidence={"overclaim": 2})
    d = rec.to_dict()
    assert d == {
        "kind": "OVERCLAIM", "severity": "HIGH",
        "signal": "2 claimed ships unverified",
        "advice": "tighten the done-condition", "evidence": {"overclaim": 2},
    }


# ---------------------------------------------------------------------------
# 9. The CLI verb — the verdict IS the exit code (no plan, no git needed).
# ---------------------------------------------------------------------------


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def test_cli_clean_exit_zero(tmp_path: Path):
    r = _run_cli("efficiency-advice", "--work", "5", "--tokens", "2000", cwd=tmp_path)
    assert r.returncode == 0
    assert r.stdout.startswith("CLEAN")


def test_cli_waste_exit_four(tmp_path: Path):
    r = _run_cli("efficiency-advice", "--work", "0", "--tokens", "80000", cwd=tmp_path)
    assert r.returncode == 4
    assert r.stdout.startswith("WASTE")


def test_cli_advise_exit_three(tmp_path: Path):
    r = _run_cli("efficiency-advice", "--overclaim", "2", cwd=tmp_path)
    assert r.returncode == 3
    assert r.stdout.startswith("ADVISE")


def test_cli_no_plan_no_git(tmp_path: Path):
    """Runs on a bare directory — no .dos state created (the no-plan discipline)."""
    r = _run_cli("efficiency-advice", "--noop-turns", "5", cwd=tmp_path)
    assert r.returncode == 4
    assert not (tmp_path / ".dos").exists()


def test_cli_json_shape(tmp_path: Path):
    r = _run_cli("efficiency-advice", "--work", "0", "--tokens", "80000",
                 "--overclaim", "1", "--json", cwd=tmp_path)
    assert r.returncode == 4
    payload = json.loads(r.stdout)
    assert payload["verdict"] == "WASTE"
    kinds = [rec["kind"] for rec in payload["recommendations"]]
    assert "WASTEFUL_SPEND" in kinds
    assert "OVERCLAIM" in kinds


def test_cli_usage_json_arms_spend_advice(tmp_path: Path):
    usage = tmp_path / "usage.json"
    usage.write_text(json.dumps({
        "input_tokens": 100, "output_tokens": 5000,
        "cache_read_input_tokens": 50, "reasoning_output_tokens": 4000,
    }), encoding="utf-8")
    r = _run_cli("efficiency-advice", "--usage-json", str(usage),
                 "--reasoning-ceiling", "0.5", "--json", cwd=tmp_path)
    assert r.returncode == 3
    payload = json.loads(r.stdout)
    assert "OVERTHINKING" in [rec["kind"] for rec in payload["recommendations"]]


def test_cli_bad_signal_is_contract_error(tmp_path: Path):
    r = _run_cli("efficiency-advice", "--noop-turns", "-1", cwd=tmp_path)
    assert r.returncode == 2
    assert "non-negative" in r.stderr


def test_cli_exit_codes_contract_row(tmp_path: Path):
    r = _run_cli("exit-codes", "efficiency-advice", "--json", cwd=tmp_path)
    assert r.returncode == 0
    row = json.loads(r.stdout)["efficiency-advice"]
    assert row["CLEAN"] == 0
    assert row["ADVISE"] == 3
    assert row["WASTE"] == 4
