"""QS — the queue-saturation verdict: is the HUMAN escalation rung still a real target?

`queue_saturation` is the kernel's answer to the load-bearing assumption every other
escalation path silently depends on: `breaker` escalates `on_trip=HUMAN`, `decisions`
queues for the HUMAN resolver, `pulse` counts `pending_human` — all assuming a human
exists who reads them in time. In a many-times-larger always-on fleet, decisions
arrive faster than a human drains them, and "escalate to a human" becomes a silent
no-op (docs/121 §4.1: a refusal routed to an absent operator "blocks forever — and
'blocks forever' silently reads, to anything downstream, as 'didn't happen.'").

These tests pin the verdict (the four scout falsifiers):
  1. arrivals ≈ drains → DRAINING (ρ < 1, the healthy steady state).
  2. arrivals ≫ drains, queue non-empty → SATURATED, echoing the computed ρ.
  3. thin evidence (below min_arrivals) → DRAINING, NEVER SATURATED — the
     fail-to-abstain floor: the verdict never accuses on absent evidence.
  4. the `pulse` consumer: a SATURATED signal makes the digest say "operator rung
     saturated — auto-degrade" at URGENT; a DRAINING signal is byte-identical to
     today's output (the additive-only guarantee).

The verdict is PURE and TIMELESS (no clock, no I/O), so every fixture is frozen
counts — no live fleet, the `breaker.classify` property.
"""

from __future__ import annotations

import pytest

from dos import pulse, queue_saturation
from dos.queue_saturation import (
    DEFAULT_POLICY,
    QueueEvidence,
    Saturation,
    SaturationPolicy,
    classify,
)

# A readable policy: warn at ρ≥0.9, saturated at ρ≥1.0, need ≥5 arrivals to judge.
_POLICY = SaturationPolicy(warn_ratio=0.9, saturated_ratio=1.0, min_arrivals=5)


# ---------------------------------------------------------------------------
# 1. The healthy steady state — drains keep pace.
# ---------------------------------------------------------------------------
def test_balanced_queue_is_draining():
    # Drains comfortably outpace arrivals (ρ=0.5 < warn 0.9) — the healthy steady state.
    v = classify(QueueEvidence(arrivals=10, drains=20, pending=2, window_seconds=3600), _POLICY)
    assert v.verdict is Saturation.DRAINING
    assert not v.is_saturated
    assert not v.under_pressure
    assert v.load_factor == pytest.approx(0.5)
    # ρ exactly at warn (0.9) crosses into SATURATING — the band boundary.
    v2 = classify(QueueEvidence(arrivals=9, drains=10, pending=1), _POLICY)
    assert v2.load_factor == pytest.approx(0.9)
    assert v2.verdict is Saturation.SATURATING


# ---------------------------------------------------------------------------
# 2. The human rung is gone — arrivals far outrun drains, backlog remains.
# ---------------------------------------------------------------------------
def test_overloaded_queue_is_saturated_and_echoes_rho():
    ev = QueueEvidence(arrivals=100, drains=5, pending=40, window_seconds=3600)
    v = classify(ev, _POLICY)
    assert v.verdict is Saturation.SATURATED
    assert v.is_saturated
    assert v.under_pressure
    # Legible distrust: the verdict ECHOES the computed ρ and the counts (never a bare label).
    assert v.load_factor == pytest.approx(20.0)
    assert v.arrivals == 100 and v.drains == 5 and v.pending == 40
    assert "ρ=20.00" in v.reason or "20.00" in v.reason
    assert "auto-degrade" in v.reason
    # The to_dict surface a dashboard / peer reads.
    d = v.to_dict()
    assert d["verdict"] == "SATURATED"
    assert d["load_factor"] == pytest.approx(20.0)
    assert d["schema"] == queue_saturation.QUEUE_SATURATION_SCHEMA


def test_zero_drains_does_not_divide_by_zero():
    # A window where NOTHING drained while many arrived is the most severe reading.
    v = classify(QueueEvidence(arrivals=12, drains=0, pending=12), _POLICY)
    assert v.verdict is Saturation.SATURATED
    assert v.load_factor == pytest.approx(12.0)  # arrivals / max(drains, 1)


def test_high_rho_but_empty_queue_is_not_saturated():
    # Everything that arrived also drained: a busy-but-keeping-up rung, NOT a gone one.
    v = classify(QueueEvidence(arrivals=50, drains=10, pending=0), _POLICY)
    assert v.verdict is not Saturation.SATURATED
    # ρ is high so it is at least SATURATING (growing), but the pending gate blocks SATURATED.
    assert v.verdict is Saturation.SATURATING


# ---------------------------------------------------------------------------
# 3. The fail-to-abstain floor — thin evidence is NEVER an accusation.
#    This test FAILS if the leaf ever calls a quiet window saturated.
# ---------------------------------------------------------------------------
def test_thin_evidence_abstains_to_draining():
    # Only 3 arrivals (< min 5) — even at a crushing ρ, the verdict must abstain.
    ev = QueueEvidence(arrivals=3, drains=0, pending=3)
    v = classify(ev, _POLICY)
    assert v.verdict is Saturation.DRAINING, "thin evidence must never accuse"
    assert not v.is_saturated
    assert "too little evidence" in v.reason


def test_empty_window_is_draining():
    v = classify(QueueEvidence(), _POLICY)
    assert v.verdict is Saturation.DRAINING
    assert v.load_factor == pytest.approx(0.0)


def test_min_arrivals_zero_disables_the_floor():
    # With the floor off, a single severe arrival is judged on ρ alone.
    pol = SaturationPolicy(warn_ratio=0.9, saturated_ratio=1.0, min_arrivals=0)
    v = classify(QueueEvidence(arrivals=1, drains=0, pending=1), pol)
    assert v.verdict is Saturation.SATURATED


# ---------------------------------------------------------------------------
# The policy guards — a contradictory config is refused, not silently built.
# ---------------------------------------------------------------------------
def test_policy_rejects_saturated_below_warn():
    with pytest.raises(ValueError):
        SaturationPolicy(warn_ratio=1.0, saturated_ratio=0.5)


def test_policy_rejects_negative():
    with pytest.raises(ValueError):
        SaturationPolicy(warn_ratio=-0.1)
    with pytest.raises(ValueError):
        SaturationPolicy(min_arrivals=-1)


def test_evidence_rejects_negative_counts():
    with pytest.raises(ValueError):
        QueueEvidence(arrivals=-1)


# ---------------------------------------------------------------------------
# Purity / timelessness — the verdict is a function of the counts alone.
# ---------------------------------------------------------------------------
def test_classify_is_deterministic_and_pure():
    ev = QueueEvidence(arrivals=100, drains=5, pending=40, window_seconds=900)
    a = classify(ev, _POLICY)
    b = classify(ev, _POLICY)
    assert a.to_dict() == b.to_dict()
    # window_seconds does not change the verdict — it is timeless (counts only).
    ev2 = QueueEvidence(arrivals=100, drains=5, pending=40, window_seconds=999999)
    assert classify(ev2, _POLICY).verdict is a.verdict


# ---------------------------------------------------------------------------
# 4. The pulse consumer — a SATURATED signal changes the heartbeat (additive only).
# ---------------------------------------------------------------------------
def test_pulse_surfaces_saturated_rung_at_urgent():
    sat = classify(QueueEvidence(arrivals=100, drains=5, pending=40), _POLICY)
    digest = pulse.fold_pulse(saturation=sat)
    assert not digest.empty
    assert digest.severity == pulse.URGENT
    body = pulse.render_pulse_text(digest)
    assert "saturated" in body.lower()
    # The digest carries the count for the --json consumer / a top chip.
    assert digest.queue_saturated is True


def test_pulse_draining_saturation_is_silent_additive():
    # A DRAINING saturation signal adds NOTHING — the silence rule + additive guarantee.
    draining = classify(QueueEvidence(arrivals=10, drains=20, pending=1), _POLICY)
    with_sig = pulse.fold_pulse(saturation=draining)
    without = pulse.fold_pulse()
    assert with_sig.empty and without.empty
    assert with_sig.to_dict() == without.to_dict()


def test_pulse_saturating_is_warn_not_urgent():
    saturating = classify(QueueEvidence(arrivals=50, drains=10, pending=0), _POLICY)
    assert saturating.verdict is Saturation.SATURATING
    digest = pulse.fold_pulse(saturation=saturating)
    assert not digest.empty
    assert digest.severity == pulse.WARN  # under pressure, not gone — WARN not URGENT
