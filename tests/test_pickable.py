"""Tests for `dos.pickable` — the pre-dispatch gate (docs/168 Concept 2).

Three groups:

  * HoldReason completeness + the `.to_no_pick_cause` mapping pin + the
    `.is_redispatch_invariant` set (the shared-vocabulary contract docs/168 §2
    requires).
  * `classify()` unit cases — each hold fires on its triggering state, OFFERABLE
    when clean, precedence (SHIPPED beats everything), and the degrade-never-crash
    floor (empty dict → OFFERABLE, never raises).
  * `TestBacktestInvariant` — the measure-then-change gate (docs/168
    §"Falsifiable"): the documented drain-trap lanes must EACH classify HELD with
    a re-dispatch-invariant reason. If any classifies OFFERABLE the gate is wrong
    and the test fails. Mirrors `tests/test_scout.py`'s backtest-invariant shape.
"""
from __future__ import annotations

import pytest

from dos import pickable
from dos.pickable import HoldReason, Pickability, classify
from dos import picker_oracle


NOW = 1_000_000  # arbitrary fixed clock (ms) — classify reads it, never the wall.


# ---------------------------------------------------------------------------
# HoldReason — completeness, mapping pin, invariant set.
# ---------------------------------------------------------------------------


class TestHoldReasonContract:
    def test_expected_members_present(self):
        # The closed set docs/168 §2 names.
        names = {r.name for r in HoldReason}
        assert names == {
            "SHIPPED",
            "IN_FLIGHT",
            "SOFT_CLAIMED_ELSEWHERE",
            "DRAFT_CLASS",
            "OPERATOR_GATED",
            "SOAK_OPEN",
            "DEPENDENCY_UNMET",
            "COOLDOWN",
            "UNPARSEABLE",
            "STALE_CLAIM",
        }

    def test_to_no_pick_cause_is_total_and_real(self):
        # The mapping pin (docs/168 §2): EVERY HoldReason maps to a real
        # picker_oracle.NoPickCause — the gate and the audit share one vocabulary.
        for reason in HoldReason:
            cause = reason.to_no_pick_cause
            assert isinstance(cause, picker_oracle.NoPickCause)
            assert cause in set(picker_oracle.NoPickCause)

    def test_to_no_pick_cause_exact_mapping(self):
        # The documented collapse — pinned so the gate↔audit vocabulary can't drift.
        NPC = picker_oracle.NoPickCause
        assert HoldReason.DRAFT_CLASS.to_no_pick_cause is NPC.OPERATOR_GATE
        assert HoldReason.OPERATOR_GATED.to_no_pick_cause is NPC.OPERATOR_GATE
        assert HoldReason.SOAK_OPEN.to_no_pick_cause is NPC.OPERATOR_GATE
        assert HoldReason.IN_FLIGHT.to_no_pick_cause is NPC.STALE_CLAIM
        assert HoldReason.SOFT_CLAIMED_ELSEWHERE.to_no_pick_cause is NPC.STALE_CLAIM
        assert HoldReason.STALE_CLAIM.to_no_pick_cause is NPC.STALE_CLAIM
        assert HoldReason.SHIPPED.to_no_pick_cause is NPC.TRUE_DRAIN
        assert HoldReason.DEPENDENCY_UNMET.to_no_pick_cause is NPC.TRUE_DRAIN
        assert HoldReason.COOLDOWN.to_no_pick_cause is NPC.TRUE_DRAIN
        assert HoldReason.UNPARSEABLE.to_no_pick_cause is NPC.UNCLASSIFIED

    def test_next_action_is_total_and_nonempty(self):
        # The unblock-action map (docs/168 §2 routing as data): EVERY HoldReason
        # carries a non-empty, operator-facing next step — co-located with the
        # token like reasons.ReasonSpec.fix, so a HELD verdict can surface its own
        # remedy. Total over the enum (a new member without an action fails here).
        for reason in HoldReason:
            action = reason.next_action
            assert isinstance(action, str)
            assert action.strip(), f"{reason.name} has an empty next_action"

    def test_next_action_names_no_host_path(self):
        # Vendor-blind, like every kernel-emitted remedy: the action names a
        # generic verb (dos/replan/promote/wait), never a host-specific path.
        for reason in HoldReason:
            action = reason.next_action.lower()
            assert ".claude" not in action
            assert "cursor" not in action
            assert "\\" not in action  # no Windows path separators

    def test_redispatch_invariant_set(self):
        # The keystone for the loop_decide rung: exactly the four reasons a
        # re-dispatch cannot change.
        invariant = {r for r in HoldReason if r.is_redispatch_invariant}
        assert invariant == {
            HoldReason.DRAFT_CLASS,
            HoldReason.OPERATOR_GATED,
            HoldReason.SOAK_OPEN,
            HoldReason.DEPENDENCY_UNMET,
        }

    def test_curable_reasons_are_not_invariant(self):
        # The re-dispatch-CURABLE reasons must NOT be invariant — they CAN clear,
        # so they must not arm the honest-STOP.
        for r in (
            HoldReason.SHIPPED,
            HoldReason.IN_FLIGHT,
            HoldReason.SOFT_CLAIMED_ELSEWHERE,
            HoldReason.STALE_CLAIM,
            HoldReason.COOLDOWN,
            HoldReason.UNPARSEABLE,
        ):
            assert r.is_redispatch_invariant is False

    def test_str_roundtrips_to_value(self):
        assert str(HoldReason.DRAFT_CLASS) == "DRAFT_CLASS"


# ---------------------------------------------------------------------------
# Pickability — the verdict type.
# ---------------------------------------------------------------------------


class TestPickabilityVerdict:
    def test_offerable_constructor(self):
        v = Pickability.OFFERABLE()
        assert v.held is False
        assert v.reason is None
        assert v.is_redispatch_invariant is False

    def test_held_constructor(self):
        v = Pickability.HELD(HoldReason.SOAK_OPEN, "soak thru 2026-06-10")
        assert v.held is True
        assert v.reason is HoldReason.SOAK_OPEN
        assert v.evidence == "soak thru 2026-06-10"
        assert v.is_redispatch_invariant is True

    def test_held_curable_is_not_invariant(self):
        v = Pickability.HELD(HoldReason.COOLDOWN, "cooling down")
        assert v.held is True
        assert v.is_redispatch_invariant is False

    def test_frozen(self):
        v = Pickability.OFFERABLE()
        with pytest.raises(Exception):
            v.held = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# classify() — unit cases.
# ---------------------------------------------------------------------------


class TestClassifyEachHold:
    def test_shipped(self):
        v = classify({"shipped": True}, now_ms=NOW)
        assert v.reason is HoldReason.SHIPPED

    def test_unparseable(self):
        v = classify({"unparseable": True}, now_ms=NOW)
        assert v.reason is HoldReason.UNPARSEABLE

    def test_in_flight(self):
        v = classify({"in_flight": True}, now_ms=NOW)
        assert v.reason is HoldReason.IN_FLIGHT

    def test_soft_claimed_elsewhere(self):
        v = classify({"soft_claimed_elsewhere": True}, now_ms=NOW)
        assert v.reason is HoldReason.SOFT_CLAIMED_ELSEWHERE

    def test_stale_claim(self):
        v = classify({"stale_claim": True}, now_ms=NOW)
        assert v.reason is HoldReason.STALE_CLAIM

    def test_draft_class(self):
        v = classify({"plan_class": "DRAFT"}, now_ms=NOW)
        assert v.reason is HoldReason.DRAFT_CLASS
        assert v.is_redispatch_invariant is True

    def test_draft_class_case_insensitive(self):
        v = classify({"plan_class": "draft"}, now_ms=NOW)
        assert v.reason is HoldReason.DRAFT_CLASS

    def test_active_class_does_not_hold(self):
        v = classify({"plan_class": "ACTIVE"}, now_ms=NOW)
        assert v.held is False

    def test_operator_gated(self):
        v = classify({"operator_gated": True}, now_ms=NOW)
        assert v.reason is HoldReason.OPERATOR_GATED
        assert v.is_redispatch_invariant is True

    def test_soak_open(self):
        v = classify({"soak_open": True}, now_ms=NOW)
        assert v.reason is HoldReason.SOAK_OPEN
        assert v.is_redispatch_invariant is True

    def test_dependency_unmet(self):
        v = classify({"dependency_unmet": True}, now_ms=NOW)
        assert v.reason is HoldReason.DEPENDENCY_UNMET
        assert v.is_redispatch_invariant is True

    def test_cooldown_active(self):
        v = classify({"cooldown_until_ms": NOW + 1}, now_ms=NOW)
        assert v.reason is HoldReason.COOLDOWN
        assert v.is_redispatch_invariant is False

    def test_cooldown_elapsed_does_not_hold(self):
        v = classify({"cooldown_until_ms": NOW - 1}, now_ms=NOW)
        assert v.held is False

    def test_cooldown_exactly_now_does_not_hold(self):
        # Held iff now_ms STRICTLY before the wall.
        v = classify({"cooldown_until_ms": NOW}, now_ms=NOW)
        assert v.held is False

    def test_cooldown_none_does_not_hold(self):
        v = classify({"cooldown_until_ms": None}, now_ms=NOW)
        assert v.held is False


class TestClassifyOfferable:
    def test_clean_unit_is_offerable(self):
        v = classify(
            {
                "shipped": False,
                "in_flight": False,
                "soft_claimed_elsewhere": False,
                "plan_class": "ACTIVE",
                "operator_gated": False,
                "soak_open": False,
                "dependency_unmet": False,
                "cooldown_until_ms": None,
                "unparseable": False,
            },
            now_ms=NOW,
        )
        assert v.held is False
        assert v.reason is None


class TestClassifyPrecedence:
    def test_shipped_beats_everything(self):
        # SHIPPED is most-terminal; even with every other gate set it wins.
        v = classify(
            {
                "shipped": True,
                "unparseable": True,
                "in_flight": True,
                "plan_class": "DRAFT",
                "operator_gated": True,
                "soak_open": True,
                "dependency_unmet": True,
                "cooldown_until_ms": NOW + 10,
            },
            now_ms=NOW,
        )
        assert v.reason is HoldReason.SHIPPED

    def test_unparseable_beats_content_gates(self):
        # A gate read off an unparseable declaration is meaningless — surface the
        # parse failure, not the derived hold.
        v = classify(
            {"unparseable": True, "plan_class": "DRAFT", "operator_gated": True},
            now_ms=NOW,
        )
        assert v.reason is HoldReason.UNPARSEABLE

    def test_in_flight_beats_draft(self):
        # A live worker on the unit wins over a class gate — it IS being worked.
        v = classify({"in_flight": True, "plan_class": "DRAFT"}, now_ms=NOW)
        assert v.reason is HoldReason.IN_FLIGHT

    def test_draft_beats_operator_gate(self):
        v = classify({"plan_class": "DRAFT", "operator_gated": True}, now_ms=NOW)
        assert v.reason is HoldReason.DRAFT_CLASS

    def test_operator_gate_beats_soak(self):
        v = classify({"operator_gated": True, "soak_open": True}, now_ms=NOW)
        assert v.reason is HoldReason.OPERATOR_GATED

    def test_soak_beats_dependency(self):
        v = classify({"soak_open": True, "dependency_unmet": True}, now_ms=NOW)
        assert v.reason is HoldReason.SOAK_OPEN

    def test_dependency_beats_cooldown(self):
        v = classify(
            {"dependency_unmet": True, "cooldown_until_ms": NOW + 10}, now_ms=NOW
        )
        assert v.reason is HoldReason.DEPENDENCY_UNMET


class TestClassifyDegrade:
    def test_empty_dict_is_offerable(self):
        v = classify({}, now_ms=NOW)
        assert v.held is False
        assert v.reason is None

    def test_none_state_does_not_raise(self):
        # Degrade-never-crash: a None state coerces to empty → OFFERABLE.
        v = classify(None, now_ms=NOW)  # type: ignore[arg-type]
        assert v.held is False

    def test_unknown_keys_ignored(self):
        v = classify({"totally_unknown_key": True, "another": 42}, now_ms=NOW)
        assert v.held is False

    def test_garbage_cooldown_does_not_raise(self):
        v = classify({"cooldown_until_ms": "not-a-number"}, now_ms=NOW)
        assert v.held is False

    def test_garbage_plan_class_does_not_raise(self):
        v = classify({"plan_class": 12345}, now_ms=NOW)
        # "12345" != "DRAFT" → not held by class.
        assert v.held is False


# ---------------------------------------------------------------------------
# Backtest invariant — the measure-then-change gate (docs/168 §"Falsifiable").
#
# Frozen fixtures encoding the documented drain-trap lanes the host hit in 36h.
# Shapes are taken from the job-repo run READMEs + memory entries (cited inline);
# they are encoded HERE as frozen dicts — the test does NOT read job files at
# runtime (offline replay over committed knowledge, zero cost). The invariant:
# every drain-trap lane MUST classify HELD with a re-dispatch-invariant reason.
# If any classifies OFFERABLE the gate is wrong → the test fails.
# ---------------------------------------------------------------------------


# Each fixture: (lane_id, unit_state, expected_reason). The expected_reason is
# the documented root cause; the load-bearing assertion is that EACH is HELD
# *and* re-dispatch-invariant (an OFFERABLE here is the bug the gate must catch).
_DRAIN_TRAP_LANES = [
    # FMP #493 — DRAFT P32 plan; every phase is DRAFT-class → re-blocks
    # LANE_ALL_INFLIGHT_OR_DEFERRED every iter; /replan structurally cannot
    # un-defer DRAFT (job: docs/_dispatch_loops/20260606T013807Z/README.md iters
    # 4-5; memory: project_active_plans_picker_invisible_remaining_list_gap →
    # reference_fmp_lane_genuine_drain_draft_autopick_trap_493).
    (
        "FMP-493",
        {
            "shipped": False,
            "in_flight": False,
            "soft_claimed_elsewhere": False,
            "plan_class": "DRAFT",
            "operator_gated": False,
            "soak_open": False,
            "dependency_unmet": False,
            "cooldown_until_ms": None,
            "unparseable": False,
        },
        HoldReason.DRAFT_CLASS,
    ),
    # ASI #475 — genuinely drained on operator-gated decision #475 (ASI8↔ASI6.post7
    # FILE_COLLIDE on docs/49); /replan proven UNPRODUCTIVE (can't make the
    # operator decision). Loop 20260605T210216Z STOPPED at iter-2 via honest-STOP
    # override (job memory: reference_asi_lane_genuinely_drained_operator_gated_475).
    (
        "ASI-475",
        {
            "shipped": False,
            "in_flight": False,
            "soft_claimed_elsewhere": False,
            "plan_class": "ACTIVE",
            "operator_gated": True,
            "soak_open": False,
            "dependency_unmet": False,
            "cooldown_until_ms": None,
            "unparseable": False,
        },
        HoldReason.OPERATOR_GATED,
    ),
    # RTN — genuinely drained on an open RTN5 soak; a re-dispatch cannot
    # fast-forward the soak deadline, and /replan cannot close it early (job
    # memory: reference_rtn_lane_genuinely_drained_rtn4_operator_rtn5_soak).
    (
        "RTN-soak",
        {
            "shipped": False,
            "in_flight": False,
            "soft_claimed_elsewhere": False,
            "plan_class": "ACTIVE",
            "operator_gated": False,
            "soak_open": True,
            "dependency_unmet": False,
            "cooldown_until_ms": None,
            "unparseable": False,
        },
        HoldReason.SOAK_OPEN,
    ),
]


class TestBacktestInvariant:
    """docs/168 §Falsifiable — the dozen drain-trap run READMEs must each
    classify HELD with a re-dispatch-invariant reason."""

    @pytest.mark.parametrize(
        "lane_id,unit_state,expected_reason",
        _DRAIN_TRAP_LANES,
        ids=[lane[0] for lane in _DRAIN_TRAP_LANES],
    )
    def test_drain_trap_lane_is_held_invariant(
        self, lane_id, unit_state, expected_reason
    ):
        v = classify(unit_state, now_ms=NOW)
        # The whole point: a drain-trap lane that classifies OFFERABLE is the bug.
        assert v.held is True, (
            f"{lane_id}: classified OFFERABLE — the gate would let a bare loop "
            f"auto-pick it and re-DRAIN every iteration (the drain-trap bug)"
        )
        assert v.is_redispatch_invariant is True, (
            f"{lane_id}: HELD by {v.reason} which is re-dispatch-CURABLE — the "
            f"loop_decide honest-STOP rung would not fire and the loop would spin"
        )
        assert v.reason is expected_reason

    def test_all_drain_trap_lanes_arm_the_honest_stop(self):
        # Aggregate the same invariant once more as a single readable assertion:
        # every drain-trap lane arms the loop_decide PICK_HELD_INVARIANT rung.
        for lane_id, unit_state, _ in _DRAIN_TRAP_LANES:
            v = classify(unit_state, now_ms=NOW)
            assert v.is_redispatch_invariant, f"{lane_id} would not honest-STOP"

    def test_a_clean_active_lane_is_NOT_held(self):
        # The negative control: an ACTIVE lane with real pickable work must NOT
        # classify HELD (otherwise the gate would drop live work — the
        # picker-invisibility direction of the same bug).
        v = classify(
            {
                "shipped": False,
                "in_flight": False,
                "soft_claimed_elsewhere": False,
                "plan_class": "ACTIVE",
                "operator_gated": False,
                "soak_open": False,
                "dependency_unmet": False,
                "cooldown_until_ms": None,
                "unparseable": False,
            },
            now_ms=NOW,
        )
        assert v.held is False
