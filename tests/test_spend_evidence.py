"""SPEND × the verdicts — the evidence widening (docs/300 P2).

`EfficiencyEvidence` and `CandidateEvidence` each carry an optional
`SpendBreakdown`: derive-the-scalar-when-absent, refuse-on-mismatch, and the
diagnostics surfaced in the verdict JSON. The ladders are UNCHANGED — a
breakdown explains a verdict, it never flips one — and the scalar paths keep
their exact pre-docs/300 JSON shapes (the byte-identical promise).
"""

from __future__ import annotations

import json

import pytest

from dos import efficiency, improve
from dos.spend import SpendBreakdown


# ---------------------------------------------------------------------------
# Efficiency carries the breakdown (diagnostics, not adjudication).
# ---------------------------------------------------------------------------


def test_evidence_derives_tokens_from_breakdown():
    """tokens=0 + a breakdown → the scalar derives from breakdown.total."""
    b = SpendBreakdown(input=1_000, output=500, cache_read=8_500)
    ev = efficiency.EfficiencyEvidence.of(work=5, tokens=0, breakdown=b)
    assert ev.tokens == 10_000


def test_evidence_refuses_a_mismatched_pair():
    """An explicit tokens that disagrees with breakdown.total is a contract
    error — never silently reconciled."""
    b = SpendBreakdown(input=1_000, output=500)
    with pytest.raises(ValueError):
        efficiency.EfficiencyEvidence.of(work=5, tokens=9_999, breakdown=b)


def test_evidence_accepts_an_agreeing_pair():
    b = SpendBreakdown(input=1_000, output=500)
    ev = efficiency.EfficiencyEvidence.of(work=5, tokens=1_500, breakdown=b)
    assert ev.tokens == 1_500


def test_ladder_is_unchanged_by_a_breakdown():
    """The verdict rides the scalar — a breakdown can only explain, never flip."""
    b = SpendBreakdown(input=70_000, output=10_000)  # total 80,000, zero work
    v = efficiency.classify(
        efficiency.EfficiencyEvidence.of(work=0, tokens=0, breakdown=b),
        efficiency.EfficiencyPolicy(min_tokens=1000, floor=0.01),
    )
    assert v.verdict is efficiency.Efficiency.WASTEFUL


def test_verdict_json_carries_diagnostics_only_when_breakdown_present():
    """Scalar-only evidence keeps the exact pre-docs/300 JSON shape; a breakdown
    adds the typed split + diagnostics under evidence.breakdown."""
    scalar = efficiency.classify(
        efficiency.EfficiencyEvidence.of(work=0, tokens=80_000)
    ).to_dict()
    assert "breakdown" not in scalar["evidence"]

    b = SpendBreakdown(input=10_000, output=20_000, cache_read=50_000)
    rich = efficiency.classify(
        efficiency.EfficiencyEvidence.of(work=0, tokens=0, breakdown=b)
    ).to_dict()
    bd = rich["evidence"]["breakdown"]
    assert bd["total"] == 80_000
    assert bd["cache_hit_ratio"] == pytest.approx(50_000 / 60_000)
    assert json.loads(json.dumps(rich, sort_keys=True)) == rich


# ---------------------------------------------------------------------------
# The improve() pass-through: keep/revert records state their price.
# ---------------------------------------------------------------------------


def _keep_evidence(**overrides):
    base = dict(
        suite_passed=True, truth_clean=True, work=10, baseline_work=5, tokens=0
    )
    base.update(overrides)
    return improve.CandidateEvidence(**base)


def test_improve_scalar_path_json_shape_is_unchanged():
    """No breakdown → no new keys anywhere (the byte-identical promise)."""
    d = improve.classify(_keep_evidence(tokens=50_000)).to_dict()
    assert d["verdict"] == "KEEP"
    assert "efficiency" not in d
    assert "breakdown" not in d["evidence"]


def test_improve_breakdown_derives_tokens_and_surfaces_price_facts():
    """A breakdown-carrying KEEP states its price: the split under
    evidence.breakdown and the efficiency rung's full verdict alongside."""
    b = SpendBreakdown(input=5_000, output=15_000, cache_read=30_000, reasoning=9_000)
    v = improve.classify(_keep_evidence(breakdown=b))
    assert v.is_keep
    assert v.evidence.tokens == 50_000  # derived from the split
    d = v.to_dict()
    assert d["evidence"]["breakdown"]["reasoning_share"] == pytest.approx(0.6)
    assert d["efficiency"]["verdict"] == "EFFICIENT"
    assert d["efficiency"]["evidence"]["breakdown"]["total"] == 50_000


def test_improve_wasteful_revert_carries_the_rung_verdict():
    """An armed floor refusing an overpriced gain still explains itself —
    the revert record carries the rung's COSTLY verdict + diagnostics."""
    b = SpendBreakdown(input=400_000, output=600_000)
    v = improve.classify(
        _keep_evidence(work=6, baseline_work=5, breakdown=b),
        improve.ImprovePolicy(efficiency_floor=0.001),
    )
    assert v.verdict is improve.Candidate.REVERT
    assert v.revert_cause is improve.RevertCause.WASTEFUL
    d = v.to_dict()
    assert d["efficiency"]["verdict"] == "COSTLY"


def test_improve_evidence_refuses_a_mismatched_pair():
    b = SpendBreakdown(input=1_000)
    with pytest.raises(ValueError):
        _keep_evidence(tokens=999, breakdown=b)
