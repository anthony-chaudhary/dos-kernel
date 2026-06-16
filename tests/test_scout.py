"""Tests for the pre-dispatch CHOOSER (`dos.scout`).

The incident these pin (the ~30-entry `job` repo MEMORY override log,
2026-06-02): a human, *before* leasing a lane, repeatedly reads the scoreboard /
same-day-unstick-count / open-decision state and overrides the loop's mechanical
Step-0 gate — choosing stop-vs-replan-vs-unstick-vs-dispatch over the full skill
menu. `choose()` is that human override made a pure function: it runs FIRST, with
no lane held, over the full activity menu, and may choose *not to dispatch at
all*.

`choose()` is PURE (no I/O, no clock, no oracle call), so every test is a
one-liner: build a `ScoutState` literal with an explicit `HealthVerdict`, call
`choose`, and assert BOTH the `.activity` (the destination) AND the `.rule_id`
(which of the nine first-match-wins rungs fired). The §6 acceptance tests carry
the name of the MEMORY entry they replay.
"""
from __future__ import annotations

import json

import pytest

from dos.health import HealthAction, HealthVerdict
from dos.scout import (
    ClosedLoopSignal,
    Confidence,
    LaneOutcomeShape,
    LaneYield,
    ScoreboardShape,
    ScoutActivity,
    ScoutDecision,
    ScoutState,
    build_parser,
    check,
    choose,
    cmd_check,
    decision_to_dict,
)


# ── tiny builder helpers (mirror test_health._blocker / _ship) ───────────────
def _hv(action: HealthAction, cause: str = "", reason: str = "",
        overlap_lane: str = "", evidence: tuple[str, ...] = ()) -> HealthVerdict:
    """A HealthVerdict literal — the sibling verdict scout READS as an input
    field (it never recomputes one)."""
    return HealthVerdict(
        action=action,
        reason=reason or f"{action.value} on {cause!r}",
        cause_key=cause,
        overlap_lane=overlap_lane,
        evidence=evidence,
    )


def _unstick(cause: str = "apply_lane") -> HealthVerdict:
    return _hv(HealthAction.ROUTE_UNSTICK, cause=cause)


def _replan(cause: str = "soak-gated") -> HealthVerdict:
    return _hv(HealthAction.ROUTE_REPLAN, cause=cause)


def _overlap(lane: str = "tailor") -> HealthVerdict:
    return _hv(HealthAction.OVERLAP_BLOCK, overlap_lane=lane,
               reason=f"tree overlaps live lease {lane!r}")


def _proceed() -> HealthVerdict:
    return _hv(HealthAction.PROCEED)


# ─────────────────────────── one class per rule ─────────────────────────────
class TestRule0ResourceBlocked:
    """The ONLY condition under which the chooser STOPs (operator directive
    2026-06-03): a measured can't-launch wall the host hit. Every analytic /
    portfolio signal routes forward instead (see the classes below)."""

    def test_resource_blocked_stops(self):
        d = choose(ScoutState(
            scope="apply", health=_proceed(),
            resource_blocked=True,
            resource_block_reason="RAM gate: 0 free apply slots"))
        assert d.activity is ScoutActivity.STOP
        assert d.rule_id == 0
        assert d.cause_key == "resource_blocked"
        assert d.confidence is Confidence.HIGH      # a measured wall, not a judgement call
        assert d.needs_lane is False                # STOP never leases

    def test_resource_blocked_preempts_everything(self):
        # Even a fresh route_unstick + an open decision + a storm yield to a real
        # can't-launch wall (it is rule 0, the top of the order).
        d = choose(ScoutState(
            scope="apply", health=_unstick("apply_lane"),
            open_escalated_decisions=("362",),
            scoreboard=ScoreboardShape(runs_7d=197, zero_ship_7d=165),
            resource_blocked=True))
        assert d.activity is ScoutActivity.STOP
        assert d.rule_id == 0

    def test_not_resource_blocked_dispatches(self):
        # The default (no wall) never STOPs — a clean state dispatches.
        d = choose(ScoutState(scope="apply", health=_proceed()))
        assert d.activity is ScoutActivity.DISPATCH


class TestOpenDecisionNeverStops:
    """An open escalated operator decision is a SURFACED note, not a STOP — it is
    already in the operator's queue; scout dispatches the lane's other work and
    names the decision in evidence (operator directive 2026-06-03; was rule-1 STOP)."""

    def test_open_decision_dispatches_and_surfaces(self):
        d = choose(ScoutState(scope="recruiter", open_escalated_decisions=("362",)))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9
        # the decision rides along as surfaced evidence + appears in the reason
        assert any("362" in e for e in d.evidence)
        assert "362" in d.reason

    def test_open_decision_does_not_preempt_route_unstick(self):
        # A fresh route_unstick now wins over an open decision (the decision no
        # longer pre-empts) — sweep the recurring cause; the decision is surfaced
        # by the eventual dispatch, not by halting.
        d = choose(ScoutState(
            scope="apply", health=_unstick(), open_escalated_decisions=("357",)))
        assert d.activity is ScoutActivity.UNSTICK
        assert d.rule_id == 4


class TestUnstickSaturationRoutesAwayNotStop:
    """A saturated / non-landing /unstick is the doom-loop signal — scout routes
    AWAY from /unstick (to dispatch), it does NOT STOP (operator directive
    2026-06-03; was the rule-2/rule-3 STOPs). The doom-loop was *caused by*
    re-routing to UNSTICK, so the fix is to break the loop by doing real work."""

    def test_op_owned_saturated_dispatches(self):
        d = choose(ScoutState(
            scope="apply", health=_unstick("apply_lane"),
            same_day_unstick_count=4, operator_owned_cause=True))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9
        assert any("unstick_saturated" in e for e in d.evidence)

    def test_operator_substring_saturated_dispatches(self):
        # The op-owned signal can come from an 'operator' substring of cause_key.
        d = choose(ScoutState(
            scope="apply", health=_unstick("operator_owned_decision"),
            same_day_unstick_count=2, operator_owned_cause=False))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9

    def test_doomloop_not_landing_dispatches(self):
        d = choose(ScoutState(
            scope="apply", health=_unstick("apply_lane"),
            same_day_unstick_count=5, fixes_landing_since_unstick=False,
            operator_owned_cause=False))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9
        assert "not landing" in d.reason

    def test_below_saturation_floor_unsticks_fresh(self):
        # Below the saturation floors a route_unstick is still a FRESH /unstick.
        d = choose(ScoutState(
            scope="apply", health=_unstick("apply_lane"),
            same_day_unstick_count=1, operator_owned_cause=True))
        assert d.activity is ScoutActivity.UNSTICK
        assert d.rule_id == 4

    def test_fixes_landing_unsticks_fresh(self):
        # Saturated count but fixes ARE landing → not a doom-loop → fresh /unstick.
        d = choose(ScoutState(
            scope="apply", health=_unstick("apply_lane"),
            same_day_unstick_count=5, fixes_landing_since_unstick=True,
            operator_owned_cause=False))
        assert d.activity is ScoutActivity.UNSTICK
        assert d.rule_id == 4


class TestRule4FreshUnstick:
    def test_fresh_recurring_cause_unsticks(self):
        d = choose(ScoutState(scope="apply", health=_unstick("renderer_sidecar_drop")))
        assert d.activity is ScoutActivity.UNSTICK
        assert d.rule_id == 4
        assert d.confidence is Confidence.HIGH

    def test_unstick_needs_lane(self):
        d = choose(ScoutState(scope="apply", health=_unstick()))
        assert d.needs_lane is True


class TestRule5OverlapWait:
    def test_overlap_waits(self):
        d = choose(ScoutState(scope="TM", health=_overlap("tailor")))
        assert d.activity is ScoutActivity.WAIT
        assert d.rule_id == 5
        assert d.cause_key == "lane_overlap_collision"


class TestRule6ReplanSoakWait:
    def test_replan_with_open_soak_waits(self):
        d = choose(ScoutState(
            scope="tailor", health=_replan("soak-gated"),
            open_soaks=("docs/63-PHP",)))
        assert d.activity is ScoutActivity.WAIT
        assert d.rule_id == 6
        assert d.cause_key == "lane_soak_or_data_gated"


class TestRule7Replan:
    def test_route_replan_no_soaks_replans(self):
        d = choose(ScoutState(scope="tailor", health=_replan("data-gated")))
        assert d.activity is ScoutActivity.REPLAN
        assert d.rule_id == 7
        # /replan is portfolio-wide: scope + focus are cleared.
        assert d.scope is None
        assert d.focus is None

    def test_replan_due_by_cooldown_replans(self):
        # No route_replan health, but replan otherwise due → rule 7.
        d = choose(ScoutState(
            scope="apply", health=_proceed(),
            replan_due=True, replan_desc="cooldown elapsed"))
        assert d.activity is ScoutActivity.REPLAN
        assert d.rule_id == 7


class TestRule10LaneOutcomeReplan:
    """Outcome-driven pre-empt (the 2026-06-09 self-improving-loop goal): a lane
    that shipped 0 of its last K runs BUT has pickable work gets /replan to
    re-prioritize, instead of another non-shipping dispatch. ROUTES, never STOPs.
    Inert when the slice is absent/insufficient (byte-identical to before)."""

    def _lo(self, **kw):
        base = dict(window=5, runs=5, shipped_runs=0, drained_runs=1, blocked_runs=4,
                    top_blocked_cause="gate_wedge_unspecified")
        base.update(kw)
        return LaneOutcomeShape(**base)

    def test_zero_ship_with_work_routes_replan(self):
        d = choose(ScoutState(scope="apply", health=_proceed(),
                              lane_outcome=self._lo()))
        assert d.activity is ScoutActivity.REPLAN
        assert d.rule_id == 10
        # /replan is portfolio-wide → scope + focus cleared (matches rung 7).
        assert d.scope is None and d.focus is None
        # the top blocked cause + ship-rate ride in evidence (operator-facing).
        assert any("lane_ship_rate=0/5" in e for e in d.evidence)

    def test_some_ships_falls_through_to_dispatch(self):
        # 2/5 shipped → the lane IS productive; rung-10 does not fire.
        d = choose(ScoutState(scope="apply", health=_proceed(),
                              lane_outcome=self._lo(shipped_runs=2, blocked_runs=2)))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9

    def test_all_drained_falls_through(self):
        # 0 shipped but ALL drained (no work) → not rung-10 (the yield/empty case,
        # not the "has work but never ships" case). drained_runs == runs.
        d = choose(ScoutState(scope="apply", health=_proceed(),
                              lane_outcome=self._lo(shipped_runs=0, drained_runs=5,
                                                    blocked_runs=0)))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9

    def test_insufficient_window_is_inert(self):
        # window=0 (no data) → is_actionable False → inert → DISPATCH.
        d = choose(ScoutState(scope="apply", health=_proceed(),
                              lane_outcome=LaneOutcomeShape(window=0)))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9

    def test_fewer_runs_than_window_still_fires(self):
        # REGRESSION: is_actionable is a runs FLOOR (>=3), NOT runs>=window. A lane
        # with only 5 runs when window=8 was requested has a real 0-ship history and
        # MUST pre-empt — the e2e-caught bug (runs<window wrongly read as inert).
        d = choose(ScoutState(scope="apply", health=_proceed(),
                              lane_outcome=LaneOutcomeShape(
                                  window=8, runs=5, shipped_runs=0, drained_runs=1,
                                  blocked_runs=4, top_blocked_cause="gate_wedge_unspecified")))
        assert d.activity is ScoutActivity.REPLAN and d.rule_id == 10

    def test_below_floor_is_inert(self):
        # 2 runs (< the _MIN_RUNS=3 floor) → not enough to trust → DISPATCH.
        d = choose(ScoutState(scope="apply", health=_proceed(),
                              lane_outcome=LaneOutcomeShape(
                                  window=8, runs=2, shipped_runs=0, drained_runs=0,
                                  blocked_runs=2)))
        assert d.activity is ScoutActivity.DISPATCH and d.rule_id == 9

    def test_none_is_byte_identical_to_no_field(self):
        # The reserved-input invariant: lane_outcome=None ⇒ identical to omitting it.
        base = dict(scope="apply", health=_proceed())
        a = choose(ScoutState(**base))
        b = choose(ScoutState(**base, lane_outcome=None))
        assert (a.activity, a.rule_id, a.scope, a.focus) == (b.activity, b.rule_id, b.scope, b.focus)

    def test_resource_block_still_beats_rung_10(self):
        # rung-0 (the only STOP) must still win over an actionable lane_outcome.
        d = choose(ScoutState(scope="apply", health=_proceed(),
                              resource_blocked=True, resource_block_reason="RAM wall",
                              lane_outcome=self._lo()))
        assert d.activity is ScoutActivity.STOP
        assert d.rule_id == 0

    def test_health_unstick_beats_rung_10(self):
        # rungs 4-7 are earlier in the ladder → they win over rung-10.
        d = choose(ScoutState(scope="apply", health=_unstick("renderer_sidecar_drop"),
                              lane_outcome=self._lo()))
        assert d.rule_id == 4  # fresh unstick, not the outcome pre-empt

    def test_low_but_nonzero_rate_surfaces_on_dispatch(self):
        # 1/5 shipped → DISPATCH (rung-10 only fires on hard 0), but the weak rate
        # is SURFACED in evidence (no behavior change).
        d = choose(ScoutState(scope="apply", health=_proceed(),
                              lane_outcome=self._lo(shipped_runs=1, blocked_runs=3)))
        assert d.activity is ScoutActivity.DISPATCH and d.rule_id == 9
        assert any("lane_ship_rate=1/5" in e for e in d.evidence)


class TestRule7ReplanRedundancyRoutesToDispatch:
    """A /replan holds no lane lease (it's portfolio-wide), so two concurrent
    sweeps race the same docs/_plans/ tree — pure waste. And the cooldown arm
    (≥3 fanouts) fires regardless of how recently/uselessly a replan just ran.
    Per the 2026-06-03 directive both are ROUTING signals, not STOPs: a redundant
    replan falls through to DISPATCH (rule 9), carrying the redundancy as evidence.

    Replays the 2026-06-03T14:17Z `/dispatch-loop` incident — scout chose
    first-iter-replan despite (a) a /replan running concurrently (the "178th",
    which iter-1's own child noticed wrote an identical het-rollup) and (b) a
    /replan 42 min earlier that closed 0 / surfaced 0 (a 12-sweep zero-surface
    streak)."""

    def test_in_flight_vetoes_cooldown_replan_dispatches(self):
        # replan_due by cooldown, but a sweep is in flight → DISPATCH not REPLAN.
        d = choose(ScoutState(
            scope="apply", health=_proceed(),
            replan_due=True, replan_desc="fanouts_since=5",
            replan_in_flight=True, recent_replan_desc="178th sweep live"))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9
        assert "replan_in_flight=1" in d.evidence
        assert "already in flight" in d.reason

    def test_in_flight_vetoes_even_health_routed_replan(self):
        # A live health ROUTE_REPLAN is ALSO suppressed when a sweep is in flight —
        # the concurrent sweep gardens whatever the lane needs; a 2nd only collides.
        d = choose(ScoutState(
            scope="tailor", health=_replan("data-gated"),
            replan_in_flight=True))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9
        assert "replan_in_flight=1" in d.evidence

    def test_recent_unproductive_vetoes_cooldown_replan_dispatches(self):
        # replan_due by cooldown + a recent empty sweep → DISPATCH not REPLAN.
        d = choose(ScoutState(
            scope="apply", health=_proceed(),
            replan_due=True, replan_desc="fanouts_since=5",
            recent_replan_unproductive=True,
            recent_replan_desc="13:35Z: 0 closed / 0 surfaced, 12-sweep streak"))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9
        assert "recent_replan_unproductive=1" in d.evidence
        assert "ran recently to no effect" in d.reason

    def test_recent_unproductive_does_NOT_veto_health_routed_replan(self):
        # The asymmetry: a recent empty sweep does NOT suppress a fresh health
        # ROUTE_REPLAN. If the lane is soak/data-gated NOW, dispatch still can't
        # proceed — a recent empty sweep can't clear a live gate → still REPLAN.
        d = choose(ScoutState(
            scope="tailor", health=_replan("data-gated"),
            recent_replan_unproductive=True,
            recent_replan_desc="ran 40m ago, empty"))
        assert d.activity is ScoutActivity.REPLAN
        assert d.rule_id == 7

    def test_no_recent_sweep_replans_on_cooldown(self):
        # The legit cooldown case: NO replan ran recently (neither signal set) and
        # the cooldown is due (≥3 fanouts) → REPLAN. This is the case the cooldown
        # arm exists for — a genuinely-stale portfolio with no recent gardening.
        d = choose(ScoutState(
            scope="apply", health=_proceed(),
            replan_due=True, replan_desc="fanouts_since=5",
            recent_replan_unproductive=False, recent_replan_ran=False))
        assert d.activity is ScoutActivity.REPLAN
        assert d.rule_id == 7

    def test_recent_ran_of_any_yield_vetoes_cooldown_replan_dispatches(self):
        # THE FIX: `recent_replan_ran` is the superset veto — a /replan finished
        # recently REGARDLESS of yield. `fanouts_since_last_run` does NOT reset when
        # a sweep runs, so a PRODUCTIVE recent sweep (the `recent_replan_unproductive`
        # guard is False) still leaves the cooldown armed → the scout re-routed
        # /replan forever (the first-iter-replan loop that BLOCKED a real loop).
        # `recent_replan_ran=True` vetoes the cooldown arm even when unproductive
        # is False → DISPATCH the refilled work, don't re-sweep.
        d = choose(ScoutState(
            scope="AB", health=_proceed(),
            replan_due=True, replan_desc="fanouts_since=3, last_run=0h ago",
            recent_replan_unproductive=False, recent_replan_ran=True,
            recent_replan_desc="5m ago, added 1"))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9
        assert "recent_replan_ran=1" in d.evidence
        assert "already ran this cooldown window" in d.reason

    def test_recent_ran_does_NOT_veto_health_routed_replan(self):
        # Same asymmetry as the unproductive veto: a live health ROUTE_REPLAN means
        # the lane is gated NOW, which a recent sweep cannot clear → still REPLAN.
        d = choose(ScoutState(
            scope="tailor", health=_replan("data-gated"),
            recent_replan_ran=True, recent_replan_desc="ran 5m ago"))
        assert d.activity is ScoutActivity.REPLAN
        assert d.rule_id == 7

    def test_in_flight_alone_without_due_dispatches_clean(self):
        # replan_in_flight set but replan not otherwise due → plain DISPATCH; the
        # in-flight note still surfaces (so the operator sees the concurrent sweep).
        d = choose(ScoutState(
            scope="apply", health=_proceed(), replan_in_flight=True))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9
        assert "replan_in_flight=1" in d.evidence


class TestScoreboardStormNeverStops:
    """A 7d zero-ship storm is a fleet-stats artefact — it is SURFACED on the
    dispatch decision (so the operator sees it + the concurrency/lane-allocation
    lever) but NEVER blocks launch (operator directive 2026-06-03; was the rule-8
    STOP). The storm says nothing about whether THIS lane has clean isolated work
    right now."""

    def test_zero_ship_storm_dispatches_and_surfaces(self):
        d = choose(ScoutState(
            scope="apply", health=_proceed(),
            scoreboard=ScoreboardShape(runs_7d=197, zero_ship_7d=165,
                                       false_drain_runs=30)))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9
        # surfaced: the storm shape appears in evidence + the reason names the lever
        assert any("165:197" in e for e in d.evidence)
        assert "zero-ship" in d.reason
        assert "concurrency" in d.reason

    def test_below_run_floor_dispatches_clean(self):
        # runs_7d<20 → not even surfaced → a plain clean dispatch.
        d = choose(ScoutState(
            scope="apply", health=_proceed(),
            scoreboard=ScoreboardShape(runs_7d=10, zero_ship_7d=10)))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9
        assert "zero-ship" not in d.reason

    def test_below_zero_ship_frac_dispatches_clean(self):
        # 20 runs but only 50% zero-ship (<0.75) → not a storm → clean dispatch.
        d = choose(ScoutState(
            scope="apply", health=_proceed(),
            scoreboard=ScoreboardShape(runs_7d=20, zero_ship_7d=10)))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9
        assert "zero-ship" not in d.reason


class TestRule9Dispatch:
    def test_clean_state_dispatches(self):
        d = choose(ScoutState(scope="apply", health=_proceed()))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9
        assert d.confidence is Confidence.HIGH

    def test_dispatch_carries_gate(self):
        d = choose(ScoutState(scope="apply", health=_proceed(), gate="soft"))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.gate == "soft"

    def test_dispatch_needs_lane(self):
        d = choose(ScoutState(scope="apply", health=_proceed()))
        assert d.needs_lane is True


# ───────────────── §6 override-log acceptance tests (named for MEMORY) ───────
class TestOverrideLogAcceptance:
    def test_recruiter_362_open_decision_surfaces_does_not_halt(self):
        # feedback_dispatch_loop_recruiter_362_operator_decision_driver_replan_override
        # The open decision is already escalated — under the 2026-06-03 directive
        # the chooser does NOT halt the loop on it. A ROUTE_REPLAN health verdict
        # still routes to /replan (rule 7); the open decision rides along surfaced.
        d = choose(ScoutState(
            scope="recruiter", health=_replan("recruiter_lane_blocked"),
            open_escalated_decisions=("362",)))
        assert d.activity is ScoutActivity.REPLAN
        assert d.rule_id == 7

    def test_step0_operator_owned_unstick_routes_to_dispatch_not_stop(self):
        # feedback_dispatch_loop_step0_gate_honored_operator_stop_not_reunstick
        # Swept ≥2× today on an operator-owned cause: the Nth /unstick won't clear
        # it — so route AWAY from /unstick. Under the 2026-06-03 directive that
        # means DISPATCH (break the loop by doing real work), NOT STOP.
        d = choose(ScoutState(
            scope="apply", health=_unstick("apply_lane"),
            same_day_unstick_count=4, operator_owned_cause=True))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9

    def test_step0_doom_loop_routes_to_dispatch_not_stop(self):
        # feedback_dispatch_loop_exit3_twice_stop_plus_staged_deletion_hazard
        # Sweeps SATURATED (≥3) + fixes NOT landing = the POST-STOP-respawn
        # doom-loop. The doom-loop was *caused by* re-routing to UNSTICK, so the
        # fix is to route away (DISPATCH), not STOP (2026-06-03 directive).
        d = choose(ScoutState(
            scope="apply", health=_unstick("apply_lane"),
            same_day_unstick_count=5, fixes_landing_since_unstick=False,
            operator_owned_cause=False))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9

    def test_force_clean_lane_scoreboard_storm_surfaces_then_dispatches(self):
        # feedback_dispatch_loop_force_clean_lane_escapes_apply_but_not_tag_collision
        # A zero-ship storm is a fleet-stats artefact: the operator must SEE it, but
        # it must NOT block this lane's clean work. Under the 2026-06-03 directive
        # the chooser DISPATCHes and surfaces the storm in evidence (was a LOW STOP).
        d = choose(ScoutState(
            scope="CID", health=_proceed(),
            scoreboard=ScoreboardShape(runs_7d=197, zero_ship_7d=165,
                                       false_drain_runs=30)))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9
        assert d.auto_runnable is True              # HIGH — a normal dispatch
        assert any("165:197" in e for e in d.evidence)

    def test_resource_wall_is_the_only_stop(self):
        # The 2026-06-03 directive's positive case: the chooser DOES still STOP, but
        # ONLY on a measured can't-launch wall — never on an analytic signal.
        d = choose(ScoutState(
            scope="apply", health=_proceed(),
            resource_blocked=True,
            resource_block_reason="usage window spent"))
        assert d.activity is ScoutActivity.STOP
        assert d.rule_id == 0
        assert d.cause_key == "resource_blocked"

    def test_soak_gated_replan_waits(self):
        # feedback_dispatch_loop_health_gate_replan_still_soak_gated_clean_stop
        # (rule 6): /replan no-ops vs an open soak window → WAIT, not replan.
        d = choose(ScoutState(
            scope="tailor", health=_replan("soak-gated"),
            open_soaks=("docs/63-PHP",), replan_due=True))
        assert d.activity is ScoutActivity.WAIT
        assert d.rule_id == 6

    def test_soak_boundary_no_open_soak_replans(self):
        # The 6-vs-7 line: same ROUTE_REPLAN + replan_due, but NO open soak →
        # rule 7 REPLAN. Pins that the open-soak tuple is what splits 6 from 7.
        d = choose(ScoutState(
            scope="tailor", health=_replan("data-gated"),
            open_soaks=(), replan_due=True))
        assert d.activity is ScoutActivity.REPLAN
        assert d.rule_id == 7

    def test_stale_verdict_resolved_decision_dispatches(self):
        # feedback_next_up_stale_side_path_verdict_read_no_freshness_check.
        # Freshness is I/O — it lives on the adapter, NOT in choose(). The kernel
        # side documents the removal-of-bug-class: a RESOLVED decision yields an
        # EMPTY open_escalated_decisions tuple, and the pure core has no stale-
        # sidecar input path at all, so choose() simply DISPATCHes (rule 9). A
        # stale verdict can never become a live WEDGE inside the pure kernel.
        d = choose(ScoutState(
            scope="CD", health=_proceed(), open_escalated_decisions=()))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9


# ─────────────────────────── boundary / ordering ────────────────────────────
class TestRuleOrdering:
    def test_resource_block_beats_route_replan(self):
        # rule 0 (resource STOP) pre-empts a ROUTE_REPLAN — a measured can't-launch
        # wall wins over every routing decision.
        d = choose(ScoutState(
            scope="recruiter", health=_replan("data-gated"),
            resource_blocked=True, open_escalated_decisions=("362",)))
        assert d.activity is ScoutActivity.STOP
        assert d.rule_id == 0

    def test_open_decision_plus_route_replan_routes_to_replan(self):
        # Open decision + ROUTE_REPLAN, no resource wall: the decision no longer
        # halts, so the health route wins → rule 7 REPLAN (decision surfaced).
        d = choose(ScoutState(
            scope="recruiter", health=_replan("data-gated"),
            open_escalated_decisions=("362",)))
        assert d.activity is ScoutActivity.REPLAN
        assert d.rule_id == 7

    def test_saturated_unstick_with_route_replan_prefers_unstick_suppression(self):
        # op-owned AND saturated-not-landing both hold on a ROUTE_UNSTICK health:
        # neither STOPs anymore — the fresh-unstick rung is suppressed and we fall
        # through to DISPATCH (rule 9).
        d = choose(ScoutState(
            scope="apply", health=_unstick("apply_lane"),
            same_day_unstick_count=5, operator_owned_cause=True,
            fixes_landing_since_unstick=False))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9

    def test_degenerate_none_health_dispatches(self):
        # No health verdict supplied → health_action degrades to PROCEED → rule 9.
        d = choose(ScoutState(scope="apply", health=None))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9


# ─────────────────────────── __post_init__ guards ───────────────────────────
class TestPostInitGuards:
    def test_gate_on_stop_raises(self):
        with pytest.raises(ValueError):
            ScoutDecision(
                activity=ScoutActivity.STOP, rule_id=1,
                confidence=Confidence.HIGH, reason="x", gate="hard")

    def test_cause_key_on_dispatch_raises(self):
        with pytest.raises(ValueError):
            ScoutDecision(
                activity=ScoutActivity.DISPATCH, rule_id=9,
                confidence=Confidence.HIGH, reason="x", cause_key="boom")

    def test_state_bogus_gate_raises(self):
        with pytest.raises(ValueError):
            ScoutState(gate="bogus")

    def test_gate_on_dispatch_is_allowed(self):
        # The legal counterpart — DISPATCH may carry a gate.
        d = ScoutDecision(
            activity=ScoutActivity.DISPATCH, rule_id=9,
            confidence=Confidence.HIGH, reason="x", gate="hard")
        assert d.gate == "hard"

    def test_cause_key_on_stop_is_allowed(self):
        d = ScoutDecision(
            activity=ScoutActivity.STOP, rule_id=1,
            confidence=Confidence.HIGH, reason="x", cause_key="open_operator_decision")
        assert d.cause_key == "open_operator_decision"


# ─────────────────────────────── the CLI path ───────────────────────────────
class TestCli:
    def test_resource_block_stop_exit_code_is_6(self):
        # The only STOP path: a measured resource wall → HIGH-confidence STOP →
        # _ACTIVITY_EXIT maps STOP to 6 (surface to the operator).
        blob = json.dumps({
            "scope": "apply",
            "health": {"action": "proceed"},
            "resource_blocked": True,
            "resource_block_reason": "0 free apply slots",
        })
        args = build_parser().parse_args(["--state-json", blob])
        assert cmd_check(args) == 6

    def test_saturated_unstick_exit_code_is_0(self):
        # The former rule-3 STOP now DISPATCHes (route away from the doom-loop) →
        # exit 0, NOT 6. This is the CLI proof of the 2026-06-03 directive.
        blob = json.dumps({
            "scope": "apply",
            "health": {"action": "route_unstick", "cause_key": "apply_lane"},
            "same_day_unstick_count": 5,
            "fixes_landing_since_unstick": False,
        })
        args = build_parser().parse_args(["--state-json", blob])
        assert cmd_check(args) == 0

    def test_dispatch_exit_code_is_0(self):
        blob = json.dumps({"scope": "apply", "health": {"action": "proceed"}})
        args = build_parser().parse_args(["--state-json", blob])
        assert cmd_check(args) == 0

    def test_scoreboard_storm_dispatches_exit_code_is_0(self):
        # The former rule-8 LOW STOP (exit 6) now DISPATCHes → exit 0. A noisy
        # scoreboard never blocks launch.
        blob = json.dumps({
            "scope": "CID",
            "health": {"action": "proceed"},
            "scoreboard": {"runs_7d": 197, "zero_ship_7d": 165, "false_drain_runs": 30},
        })
        args = build_parser().parse_args(["--state-json", blob])
        assert cmd_check(args) == 0

    def test_fresh_unstick_exit_code_is_3(self):
        blob = json.dumps({
            "scope": "apply",
            "health": {"action": "route_unstick", "cause_key": "renderer_sidecar_drop"},
        })
        args = build_parser().parse_args(["--state-json", blob])
        assert cmd_check(args) == 3


class TestClosedLoopFocusBias:
    """Operator directive 2026-06-04 — weight closed-loop primitives.

    When the lane has pickable loop-closing work (convert-observation-into-
    mechanism — the FQ-467 lesson→config promoter is the worked example), the
    rule-9 DISPATCH terminal biases the in-lane `focus` to "closed-loop-first".
    The bias only re-orders work WITHIN an already-chosen DISPATCH; it never
    manufactures a route, never STOPs, and is inert by default (reserved-input
    convention) so every other scout test stays byte-identical.
    """

    def test_default_no_closed_loop_signal_leaves_focus_untouched(self):
        # The byte-identical-by-default invariant: absent the signal, focus is
        # whatever the caller set. (This is why the other 50 tests are unaffected.)
        d = choose(ScoutState(scope="apply", focus="not-started", health=_proceed()))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9
        assert d.focus == "not-started"

    def test_actionable_closed_loop_biases_focus(self):
        d = choose(ScoutState(
            scope="apply", focus="not-started", health=_proceed(),
            closed_loop=ClosedLoopSignal(available=True, count=3, top_item="FQ-467"),
        ))
        assert d.activity is ScoutActivity.DISPATCH
        assert d.rule_id == 9
        assert d.focus == "closed-loop-first"
        # The magnitude + top item ride in evidence (data-trust-floor) and reason.
        assert any("closed_loop=3" in e for e in d.evidence)
        assert "FQ-467" in d.reason

    def test_stale_stamp_focus_is_not_displaced(self):
        # A stale soft-claim BLOCKS picks — that correctness fix must win over the
        # leverage bias. closed-loop yields to stale-stamp.
        d = choose(ScoutState(
            scope="apply", focus="stale-stamp", health=_proceed(),
            closed_loop=ClosedLoopSignal(available=True, count=2),
        ))
        assert d.focus == "stale-stamp"

    def test_count_zero_signal_is_inert(self):
        # available=True but count=0 is not actionable → no bias (the adapter can
        # hand in a present-but-empty slice without changing the decision).
        d = choose(ScoutState(
            scope="apply", focus="priority-first", health=_proceed(),
            closed_loop=ClosedLoopSignal(available=True, count=0),
        ))
        assert d.focus == "priority-first"

    def test_closed_loop_never_overrides_a_higher_gate(self):
        # Closed-loop work in a lane that must UNSTICK first does NOT pull the
        # decision down to DISPATCH — a higher gate wins; the bias only applies
        # once rule 9 (DISPATCH) is already the answer.
        d = choose(ScoutState(
            scope="apply", health=_unstick("renderer_sidecar_drop"),
            closed_loop=ClosedLoopSignal(available=True, count=5, top_item="FQ-467"),
        ))
        assert d.activity is ScoutActivity.UNSTICK
        assert d.rule_id == 4
        assert d.focus != "closed-loop-first"

    def test_closed_loop_rides_through_check_cli(self):
        # The adapter hands the slice in as JSON via --state-json; the CLI exit
        # for a DISPATCH stays 0 and the bias still applies.
        blob = json.dumps({
            "scope": "apply",
            "health": {"action": "proceed"},
            "closed_loop": {"available": True, "count": 2, "top_item": "FQ-467"},
        })
        args = build_parser().parse_args(["--state-json", blob])
        assert cmd_check(args) == 0


class TestLaneYieldSurfacing:
    """Yield-aware scout, Phase 1 (the `job` repo's
    docs/_design/dispatch-yield-aware-scout-plan.md): the lane scout is about to
    DISPATCH is checked against the arbiter's pick-oracle, reduced to a `LaneYield`
    slice at the I/O edge. Phase 1 SURFACES a CONFIRMED drain (count == 0) as
    exactly one extra evidence line — the decision STAYS dispatch (Phase 2 promotes
    it to a REPLAN-refill rung). The safety asymmetry is the whole contract: ONLY a
    confident 0 changes anything; `None` (uncertain) and `>= 1` (has work) are
    byte-identical to before the field existed.
    """

    def test_none_is_byte_identical_to_no_field(self):
        # The reserved-input invariant: lane_yield=None ⇒ identical to omitting it.
        base = dict(scope="apply", health=_proceed())
        a = choose(ScoutState(**base))
        b = choose(ScoutState(**base, lane_yield=None))
        assert decision_to_dict(a) == decision_to_dict(b)

    def test_has_work_count_is_byte_identical(self):
        # count >= 1 (the lane has pickable work) ⇒ NO surfacing ⇒ byte-identical to
        # the no-field decision. The load-bearing half of the safety asymmetry: a
        # populated, has-work slice must never perturb today's exact decision.
        base = dict(scope="apply", health=_proceed())
        baseline = choose(ScoutState(**base))
        for n in (1, 5, 42):
            d = choose(ScoutState(**base, lane_yield=LaneYield(count=n, basis="x")))
            assert d.activity is ScoutActivity.DISPATCH
            assert decision_to_dict(d) == decision_to_dict(baseline), (
                f"count={n} (has work) must leave the decision byte-identical")

    def test_confirmed_drain_adds_exactly_one_evidence_line(self):
        # count == 0 ⇒ SAME activity (still dispatch, rule 9 — Phase 1 surfaces, it
        # does not reroute) + EXACTLY one extra evidence line vs the no-yield baseline.
        base = dict(scope="apply", health=_proceed())
        baseline = choose(ScoutState(**base))
        d = choose(ScoutState(
            **base,
            lane_yield=LaneYield(count=0, basis="fanout_state._build_pick_oracle")))
        assert d.activity is ScoutActivity.DISPATCH and d.rule_id == 9
        assert len(d.evidence) == len(baseline.evidence) + 1, (
            "a confirmed drain must add EXACTLY one evidence line")
        extra = [e for e in d.evidence if e not in baseline.evidence]
        assert len(extra) == 1
        assert extra[0].startswith("lane-yield:0")
        assert "confirmed drained" in extra[0]
        assert "fanout_state._build_pick_oracle" in extra[0]

    def test_drain_surfacing_only_on_dispatch_not_on_a_route(self):
        # Phase 1 surfaces ONLY when the decision is DISPATCH. A confirmed-0 lane
        # that ALSO routes UNSTICK (an earlier rung wins) gets NO line — the decision
        # is byte-identical to the same UNSTICK without the yield slice.
        base = dict(scope="apply", health=_unstick("renderer_sidecar_drop"))
        routed = choose(ScoutState(**base))
        with_yield = choose(ScoutState(**base, lane_yield=LaneYield(count=0, basis="x")))
        assert routed.activity is ScoutActivity.UNSTICK and routed.rule_id == 4
        assert decision_to_dict(routed) == decision_to_dict(with_yield)

    def test_check_kwarg_threads_lane_yield(self):
        # The thin in-kernel `check()` composition accepts the new kwarg and threads
        # it through to choose() (the exact path the job-side adapter uses).
        d = check(scope="apply", health=_proceed(),
                  lane_yield=LaneYield(count=0, basis="oracle"))
        assert d.activity is ScoutActivity.DISPATCH
        assert any(e.startswith("lane-yield:0") for e in d.evidence)

    def test_default_lane_yield_is_none(self):
        # The dormant default — the reserved-input convention that keeps the whole
        # existing suite byte-identical.
        assert ScoutState().lane_yield is None
        assert LaneYield().count is None and LaneYield().basis == ""
