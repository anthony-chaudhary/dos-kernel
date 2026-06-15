"""Tests for the truth syscall (`dos.oracle`) and the loop decision kernel
(`dos.loop_decide`) — both pure given injected state.

`is_shipped` is the crown-jewel verify() syscall: artifact-over-narration. These
tests feed it a registry dict directly (the pure path) so they need no git or
filesystem, mirroring the origin's pure-core design.
"""

from __future__ import annotations

from dos import oracle
from dos import loop_decide as ld
from dos.gate_classify import Verdict


def _state(rows):
    return {"recently_completed": rows}


class TestIsShippedPure:
    def test_registry_done_row_is_shipped(self):
        state = _state([
            {"plan": "RS", "phase": "RS1", "status": "done", "sha": "abc123"},
        ])
        v = oracle.is_shipped("RS", "RS1", state=state, grep_fallback=lambda p, ph: oracle.ShipVerdict(p, ph, False))
        assert v.shipped
        assert v.source == "registry"

    def test_failed_row_is_not_shipped(self):
        state = _state([
            {"plan": "RS", "phase": "RS1", "status": "failed"},
        ])
        v = oracle.is_shipped("RS", "RS1", state=state, grep_fallback=lambda p, ph: oracle.ShipVerdict(p, ph, False))
        assert not v.shipped

    def test_missing_row_falls_through_to_grep(self):
        called = {}

        def fake_grep(plan, phase):
            called["hit"] = (plan, phase)
            return oracle.ShipVerdict(plan, phase, True, sha="deadbeef", source="grep")

        v = oracle.is_shipped("RS", "RS9", state=_state([]), grep_fallback=fake_grep)
        assert v.shipped
        assert v.source == "grep"
        assert called["hit"] == ("RS", "RS9")


class TestPlanIdCollisionGate:
    """FQ-390 — a registry `done` row written by a TOMBED plan that shares a series
    id with an ACTIVE plan must NOT clear the active plan's genuinely-unshipped
    phase. The gate keys on the row's `doc_path` vs the queried plan's expected doc.
    The bug this pins: every shipped verify surface left the gate OFF, so the
    collision row false-cleared. The fix wires the gate ON by default in the `cfg=`
    branch; these tests pin the underlying mechanism on the pure path (no git)."""

    # The row the TOMBED `data-labelling` plan wrote for its OWN DL2 ship.
    COLLISION_STATE = {"recently_completed": [
        {"plan": "DL", "phase": "DL2", "status": "done", "commit_sha": "abc123",
         "doc_path": "docs/data-labelling-plan.md"},
    ]}

    def test_gate_off_false_clears_the_collision(self):
        # The pre-fix behavior, as a guard: with no expected_doc the gate is OFF and
        # the tombed plan's row IS (wrongly) reported as a ship of the active plan.
        v = oracle.is_shipped(
            "DL", "DL2", state=self.COLLISION_STATE,
            grep_fallback=lambda p, ph: oracle.ShipVerdict(p, ph, False, source="none"),
        )
        assert v.shipped is True  # the false-clear the gate exists to prevent

    def test_gate_on_rejects_the_collision(self):
        # With the ACTIVE plan's doc as expected_doc, the row (pointing at the TOMBED
        # doc) fails the doc-path match → skipped → the active DL2 is NOT shipped.
        v = oracle.is_shipped(
            "DL", "DL2", state=self.COLLISION_STATE,
            expected_doc="docs/dispatch-lane-canon-plan.md",
            grep_fallback=lambda p, ph: oracle.ShipVerdict(p, ph, False, source="none"),
        )
        assert v.shipped is False  # collision rejected — no false clear

    def test_gate_on_keeps_a_genuine_same_doc_ship(self):
        # The gate must NOT over-reject: a row whose doc_path IS the active plan's
        # own doc is a genuine ship and still clears.
        genuine = {"recently_completed": [
            {"plan": "DL", "phase": "DL2", "status": "done", "commit_sha": "def456",
             "doc_path": "docs/dispatch-lane-canon-plan.md"},
        ]}
        v = oracle.is_shipped(
            "DL", "DL2", state=genuine,
            expected_doc="docs/dispatch-lane-canon-plan.md",
            grep_fallback=lambda p, ph: oracle.ShipVerdict(p, ph, False, source="none"),
        )
        assert v.shipped is True

    def test_doc_map_prefers_live_over_tombed(self, tmp_path):
        # The resolver that the `cfg=` branch uses to turn the gate on by default:
        # when two docs share an id, the LIVE (non-tombed) doc must win so the
        # collision row is the one rejected — not the other way around.
        from dos import config as _config
        docs = tmp_path / "docs"
        docs.mkdir()
        # data-labelling sorts BEFORE dispatch-lane alphabetically — proving the
        # preference is by classification, not glob order.
        (docs / "data-labelling-plan.md").write_text(
            "<!-- plan-meta\nid: DL\nclassification: TOMBED\n-->\n", encoding="utf-8")
        (docs / "dispatch-lane-canon-plan.md").write_text(
            "<!-- plan-meta\nid: DL\nclassification: ACTIVE\n-->\n", encoding="utf-8")
        (tmp_path / "dos.toml").write_text(
            'workspace = "."\n[paths]\nplans_glob = "docs/**/*-plan.md"\n', encoding="utf-8")
        cfg = _config.load_workspace_config(workspace=str(tmp_path))
        doc_map = oracle.default_plan_doc_map(cfg)
        assert doc_map.get("DL", "").replace("\\", "/") == "docs/dispatch-lane-canon-plan.md"

    def test_doc_map_empty_when_no_plans(self, tmp_path):
        # No plan docs → empty map → gate stays OFF → no-plan contract preserved.
        from dos import config as _config
        (tmp_path / "dos.toml").write_text(
            'workspace = "."\n[paths]\nplans_glob = "docs/**/*-plan.md"\n', encoding="utf-8")
        cfg = _config.load_workspace_config(workspace=str(tmp_path))
        assert oracle.default_plan_doc_map(cfg) == {}


class TestSoakSuppressionWiredByDefault:
    """#326 — a registry `done` row for a phase in an OPEN soak window must NOT
    report shipped on the verify surfaces (the pick is the soak follow-up). The
    suppression existed but, like FQ-390, was OFF because no shipped caller passed
    `soaks`; the fix defaults it ON in the is_shipped `cfg=` branch, fail-safe."""

    def test_open_soak_suppresses_registry_done_via_cfg(self, tmp_path):
        from dos import config as _config
        (tmp_path / "dos.toml").write_text('workspace = "."\n', encoding="utf-8")
        cfg = _config.load_workspace_config(workspace=str(tmp_path))
        cfg.paths.soaks_index.parent.mkdir(parents=True, exist_ok=True)
        cfg.paths.soaks_index.write_text(
            "soaks:\n  - plan: RS\n    id: RS1\n    status: in_progress\n", encoding="utf-8")
        state = {"recently_completed": [
            {"plan": "RS", "phase": "RS1", "status": "done", "commit_sha": "abc"}]}
        # Gate ON by default (cfg= loads the soak index) → suppressed.
        v = oracle.is_shipped("RS", "RS1", cfg=cfg, state=state)
        assert v.shipped is False
        # Reference: with no soaks the registry done row clears (the false-clear).
        v_off = oracle.is_shipped(
            "RS", "RS1", state=state,
            grep_fallback=lambda p, ph: oracle.ShipVerdict(p, ph, False, source="none"))
        assert v_off.shipped is True

    def test_closed_soak_does_not_suppress(self, tmp_path):
        # Once the operator flips the soak off in_progress, the phase reports shipped
        # again — the suppression is bounded by status, self-healing.
        from dos import config as _config
        (tmp_path / "dos.toml").write_text('workspace = "."\n', encoding="utf-8")
        cfg = _config.load_workspace_config(workspace=str(tmp_path))
        cfg.paths.soaks_index.parent.mkdir(parents=True, exist_ok=True)
        cfg.paths.soaks_index.write_text(
            "soaks:\n  - plan: RS\n    id: RS1\n    status: passed\n", encoding="utf-8")
        state = {"recently_completed": [
            {"plan": "RS", "phase": "RS1", "status": "done", "commit_sha": "abc"}]}
        v = oracle.is_shipped("RS", "RS1", cfg=cfg, state=state)
        assert v.shipped is True

    def test_no_soak_index_is_byte_identical(self, tmp_path):
        # No soak index → load_soaks_from → [] → suppresses nothing → no-plan/no-soak
        # answer is unchanged (the fail-safe gate-OFF convention).
        from dos import config as _config
        (tmp_path / "dos.toml").write_text('workspace = "."\n', encoding="utf-8")
        cfg = _config.load_workspace_config(workspace=str(tmp_path))
        state = {"recently_completed": [
            {"plan": "RS", "phase": "RS1", "status": "done", "commit_sha": "abc"}]}
        v = oracle.is_shipped("RS", "RS1", cfg=cfg, state=state)
        assert v.shipped is True


class TestLoopDecide:
    def test_single_drain_continues_to_replan(self):
        # A single DRAIN does NOT stop — the loop continues (it routes through
        # /replan to confirm a genuine empty backlog before stopping on a second
        # drained gate). Verified byte-equal against the origin module.
        state = ld.LoopState(iteration=1, max_iterations=10)
        d = ld.decide(state, ld.IterationOutcome(kind=ld.OutcomeKind.GATE, verdict=Verdict.DRAIN))
        assert d.action == "continue"

    def test_blocked_gate_continues_with_mode_escalation(self):
        # A BLOCKED gate in the default hard mode continues (it escalates the
        # gate mode rather than stopping outright). Verified against the origin.
        state = ld.LoopState(iteration=1, max_iterations=10)
        d = ld.decide(state, ld.IterationOutcome(kind=ld.OutcomeKind.GATE, verdict=Verdict.BLOCKED))
        assert d.action == "continue"

    # ---- FQ-452 stale-stamp / BLOCKED non-converging-spin breaker ----

    def _gate(self, verdict):
        return ld.IterationOutcome(kind=ld.OutcomeKind.GATE, verdict=verdict)

    def test_fq452_single_stale_stamp_continues(self):
        # One STALE-STAMP routes to /replan and continues — the gardening sweep
        # usually clears it; we only break a *sustained* spin.
        state = ld.LoopState(iteration=1, max_iterations=10)
        d = ld.decide(state, self._gate(Verdict.STALE_STAMP))
        assert d.action == "continue"
        assert d.next_state.consecutive_stale_stamp == 1

    def test_fq452_third_consecutive_stale_stamp_stops(self):
        # Three consecutive STALE-STAMP gates (no recovery in between) → STOP
        # with the dedicated reason rather than spinning a 4th /replan.
        state = ld.LoopState(iteration=3, max_iterations=10,
                             consecutive_stale_stamp=2)
        d = ld.decide(state, self._gate(Verdict.STALE_STAMP))
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.STALE_STAMP_UNRECONCILED
        assert d.surface is True

    def test_fq452_third_consecutive_blocked_stops(self):
        # BLOCKED rooted in stale-stamp drift is the actual observed verdict
        # (the picker re-derives 0-live) — it counts toward the same breaker.
        state = ld.LoopState(iteration=3, max_iterations=10,
                             consecutive_stale_stamp=2)
        d = ld.decide(state, self._gate(Verdict.BLOCKED))
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.STALE_STAMP_UNRECONCILED

    def test_fq452_streak_survives_intervening_replan(self):
        # The dispatch→/replan→dispatch cycle: a REPLAN_DONE between two
        # STALE-STAMP gates must NOT reset the streak (the /replan is the
        # response to the stale-stamp; if it didn't fix it the streak continues).
        s0 = ld.LoopState(iteration=1, max_iterations=10)
        d1 = ld.decide(s0, self._gate(Verdict.STALE_STAMP))
        assert d1.next_state.consecutive_stale_stamp == 1
        # /replan ran (the hard route's next_mode), came back REPLAN_DONE.
        d2 = ld.decide(d1.next_state,
                       ld.IterationOutcome(kind=ld.OutcomeKind.REPLAN_DONE,
                                           replan_productivity=None))
        assert d2.next_state.consecutive_stale_stamp == 1  # SURVIVED
        # Next /dispatch stale-stamps again → 2, still continues.
        d3 = ld.decide(d2.next_state, self._gate(Verdict.STALE_STAMP))
        assert d3.action == "continue"
        assert d3.next_state.consecutive_stale_stamp == 2
        # And a /replan then a third stale-stamp → STOP.
        d4 = ld.decide(d3.next_state,
                       ld.IterationOutcome(kind=ld.OutcomeKind.REPLAN_DONE,
                                           replan_productivity=None))
        d5 = ld.decide(d4.next_state, self._gate(Verdict.STALE_STAMP))
        assert d5.action == "stop"
        assert d5.stop_reason == ld.StopReason.STALE_STAMP_UNRECONCILED

    def test_fq452_live_verdict_resets_streak(self):
        # A LIVE gate (picks shipped) means the lane moved off the stale-stamp
        # cause → reset the streak so a later isolated stale-stamp starts fresh.
        state = ld.LoopState(iteration=3, max_iterations=10,
                             consecutive_stale_stamp=2)
        d = ld.decide(state, self._gate(Verdict.LIVE))
        assert d.next_state.consecutive_stale_stamp == 0

    def test_fq452_shipped_resets_streak(self):
        # A SHIPPED iteration is genuine forward progress → reset the streak.
        state = ld.LoopState(iteration=3, max_iterations=10,
                             consecutive_stale_stamp=2)
        outcome = ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=1)
        d = ld.decide(state, outcome)
        assert d.next_state.consecutive_stale_stamp == 0

    def test_fq452_drain_resets_streak(self):
        # A genuine DRAIN is a different cause (empty backlog, not stale list) →
        # reset; the drained-twice machinery owns the DRAIN path.
        state = ld.LoopState(iteration=3, max_iterations=10,
                             consecutive_stale_stamp=2)
        d = ld.decide(state, self._gate(Verdict.DRAIN))
        assert d.next_state.consecutive_stale_stamp == 0

    def test_fq452_opt_in_default_state_byte_identical(self):
        # A default LoopState (consecutive_stale_stamp=0) with a single
        # STALE-STAMP behaves exactly as before — continue to /replan. The
        # breaker only changes behavior at the cap, so un-migrated callers are
        # unaffected for the first K-1 stale-stamps.
        state = ld.LoopState(iteration=1, max_iterations=10)
        d = ld.decide(state, self._gate(Verdict.STALE_STAMP))
        assert d.action == "continue"
        assert d.next_mode == "replan"

    # ---- QWD benign-drain breaker (FQ-509-sibling): K UNPRODUCTIVE /replans
    # ----  each bracketed by a DRAIN, on a lane with nothing left to refill.

    def _unprod_replan(self):
        return ld.IterationOutcome(
            kind=ld.OutcomeKind.REPLAN_DONE,
            replan_productivity=ld.ReplanProductivity.UNPRODUCTIVE,
        )

    def _prod_replan(self):
        return ld.IterationOutcome(
            kind=ld.OutcomeKind.REPLAN_DONE,
            replan_productivity=ld.ReplanProductivity.PRODUCTIVE,
        )

    def test_benign_drain_full_spin_stops_on_third_drain(self):
        # The exact QWD trajectory (loop 20260607T195230Z): a benignly-drained
        # lane spins DRAIN → /replan(UNPROD) → DRAIN → /replan(UNPROD) → DRAIN.
        # The drained-twice rung never arms (an UNPRODUCTIVE /replan is not a
        # refill attempt, FQ-240), so without the benign-drain rung this runs to
        # the iteration cap. It must STOP on the DRAIN that would route the 3rd
        # /replan, with the dedicated reason.
        s = ld.LoopState(iteration=1, max_iterations=10)
        d1 = ld.decide(s, self._gate(Verdict.DRAIN))               # 1st DRAIN → /replan
        assert d1.action == "continue" and d1.next_mode == "replan"
        assert d1.next_state.last_gate_was_drain is True
        d2 = ld.decide(d1.next_state, self._unprod_replan())        # UNPROD replan #1
        assert d2.next_state.consecutive_unproductive_replan_drains == 1
        assert d2.next_state.last_gate_was_drain is False           # carry consumed
        d3 = ld.decide(d2.next_state, self._gate(Verdict.DRAIN))    # 2nd DRAIN → /replan
        assert d3.action == "continue" and d3.next_mode == "replan"
        d4 = ld.decide(d3.next_state, self._unprod_replan())        # UNPROD replan #2
        assert d4.next_state.consecutive_unproductive_replan_drains == 2
        d5 = ld.decide(d4.next_state, self._gate(Verdict.DRAIN))    # 3rd DRAIN → STOP
        assert d5.action == "stop"
        assert d5.stop_reason == ld.StopReason.BENIGN_DRAIN
        assert d5.surface is True

    def test_benign_drain_productive_replan_in_middle_resets(self):
        # A PRODUCTIVE /replan means the lane refilled — NOT benignly drained.
        # The streak resets, so the next DRAIN routes to /replan (and arms the
        # ordinary drained-twice machinery), never the benign-drain stop.
        s = ld.LoopState(
            iteration=4, max_iterations=10,
            consecutive_unproductive_replan_drains=1, last_gate_was_drain=True,
        )
        d = ld.decide(s, self._prod_replan())
        assert d.next_state.consecutive_unproductive_replan_drains == 0
        assert d.next_state.last_replan_drained is True             # drained-twice armed
        # The next DRAIN is now drained-twice (productive replan couldn't hold),
        # NOT benign-drain.
        d2 = ld.decide(d.next_state, self._gate(Verdict.DRAIN))
        assert d2.action == "stop"
        assert d2.stop_reason == ld.StopReason.DRAINED_TWICE

    def test_benign_drain_ship_resets_streak(self):
        # A SHIPPED iteration means the lane was not drained at all → reset.
        s = ld.LoopState(
            iteration=4, max_iterations=10,
            consecutive_unproductive_replan_drains=1, last_gate_was_drain=True,
        )
        outcome = ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=1)
        d = ld.decide(s, outcome)
        assert d.next_state.consecutive_unproductive_replan_drains == 0
        assert d.next_state.last_gate_was_drain is False

    def test_benign_drain_nondrain_gate_resets_streak(self):
        # A non-DRAIN gate verdict (LIVE here) means the lane moved off the
        # benign-drain spin → reset both the streak and the prior-DRAIN carry.
        s = ld.LoopState(
            iteration=4, max_iterations=10,
            consecutive_unproductive_replan_drains=1, last_gate_was_drain=True,
        )
        d = ld.decide(s, self._gate(Verdict.LIVE))
        assert d.next_state.consecutive_unproductive_replan_drains == 0
        assert d.next_state.last_gate_was_drain is False

    def test_benign_drain_single_cycle_does_not_trip(self):
        # Threshold is 2: a single DRAIN + UNPRODUCTIVE /replan + DRAIN must NOT
        # stop (only one unproductive-replan-drain has accumulated). The loop
        # routes the second DRAIN to /replan as usual.
        s = ld.LoopState(iteration=1, max_iterations=10)
        d1 = ld.decide(s, self._gate(Verdict.DRAIN))
        d2 = ld.decide(d1.next_state, self._unprod_replan())
        assert d2.next_state.consecutive_unproductive_replan_drains == 1
        d3 = ld.decide(d2.next_state, self._gate(Verdict.DRAIN))
        assert d3.action == "continue"
        assert d3.next_mode == "replan"

    def test_benign_drain_unproductive_replan_without_prior_drain_no_count(self):
        # An UNPRODUCTIVE /replan that did NOT follow a DRAIN (e.g. after a
        # stale-stamp gate) is not part of the benign-drain bracket → the streak
        # must not increment off it.
        s = ld.LoopState(iteration=2, max_iterations=10, last_gate_was_drain=False)
        d = ld.decide(s, self._unprod_replan())
        assert d.next_state.consecutive_unproductive_replan_drains == 0

    def test_benign_drain_opt_in_default_state_byte_identical(self):
        # A default LoopState (counters 0) routes a single DRAIN to /replan
        # exactly as before — the rung only changes behavior once a benign streak
        # has accumulated, so un-migrated callers are unaffected.
        state = ld.LoopState(iteration=1, max_iterations=10)
        d = ld.decide(state, self._gate(Verdict.DRAIN))
        assert d.action == "continue"
        assert d.next_mode == "replan"
        assert d.next_state.consecutive_unproductive_replan_drains == 0

    # ---- #506 / docs/258: the REPLAN_STALLED breaker — K consecutive UNPRODUCTIVE
    # ----  /replans REGARDLESS of the prior gate (the broader sibling of
    # ----  BENIGN_DRAIN), expressed through the dos.breaker primitive. The measured
    # ----  fix: /replan = 45% of loop wall-clock, 43% of replan iters refill 0.

    def test_replan_stalled_trips_on_second_unproductive_replan(self):
        # The #506 trajectory: a /replan refills nothing, the next /dispatch
        # re-derives a non-LIVE gate (here STALE_STAMP — NOT a DRAIN, so this is
        # outside the benign-drain bracket), routes back to /replan, which again
        # refills nothing. The streak must SURVIVE the intervening gate and STOP on
        # the 2nd unproductive /replan with REPLAN_STALLED + surface.
        s = ld.LoopState(iteration=1, max_iterations=10)
        d1 = ld.decide(s, self._unprod_replan())            # unprod replan #1
        assert d1.action == "continue"
        assert d1.next_state.consecutive_unproductive_replan == 1
        d2 = ld.decide(d1.next_state, self._gate(Verdict.STALE_STAMP))  # gate routes to replan
        assert d2.action == "continue" and d2.next_mode == "replan"
        assert d2.next_state.consecutive_unproductive_replan == 1       # survived the gate
        d3 = ld.decide(d2.next_state, self._unprod_replan())            # unprod replan #2
        assert d3.action == "stop"
        assert d3.stop_reason == ld.StopReason.REPLAN_STALLED
        assert d3.surface is True

    def test_replan_stalled_single_unproductive_does_not_trip(self):
        # Threshold is 2: one unproductive /replan continues (a single 0-refill
        # sweep may still recover on the next pass).
        s = ld.LoopState(iteration=1, max_iterations=10)
        d = ld.decide(s, self._unprod_replan())
        assert d.action == "continue"
        assert d.next_state.consecutive_unproductive_replan == 1

    def test_replan_stalled_fires_without_a_prior_drain(self):
        # The exact gap BENIGN_DRAIN leaves (pinned by
        # test_benign_drain_unproductive_replan_without_prior_drain_no_count): an
        # unproductive /replan NOT preceded by a DRAIN does not count toward the
        # benign-drain streak — but it DOES count toward REPLAN_STALLED. After the
        # 2-replan spin above, the benign-drain streak is still 0 (no DRAIN ever
        # bracketed it) yet REPLAN_STALLED has fired.
        s = ld.LoopState(iteration=1, max_iterations=10)
        d1 = ld.decide(s, self._unprod_replan())
        d2 = ld.decide(d1.next_state, self._gate(Verdict.STALE_STAMP))
        d3 = ld.decide(d2.next_state, self._unprod_replan())
        assert d3.stop_reason == ld.StopReason.REPLAN_STALLED
        assert d3.next_state.consecutive_unproductive_replan_drains == 0

    def test_replan_stalled_productive_replan_resets(self):
        # A PRODUCTIVE /replan refilled the backlog → the stall cleared. The streak
        # resets to 0 (via breaker.record_success) and the loop continues.
        s = ld.LoopState(iteration=2, max_iterations=10,
                         consecutive_unproductive_replan=1)
        d = ld.decide(s, self._prod_replan())
        assert d.action == "continue"
        assert d.next_state.consecutive_unproductive_replan == 0

    def test_replan_stalled_ship_resets(self):
        # A SHIPPED iteration means the lane produced work → not a 0-refill stall.
        # The streak resets (like the stale-stamp / benign-drain resets, done in the
        # SHIPPED branch so a REPLAN_DONE does not reset it).
        s = ld.LoopState(iteration=2, max_iterations=10,
                         consecutive_unproductive_replan=1)
        outcome = ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=1)
        d = ld.decide(s, outcome)
        assert d.next_state.consecutive_unproductive_replan == 0

    def test_replan_stalled_survives_intervening_gate(self):
        # The load-bearing reset rule: a GATE always sits between two REPLAN_DONE
        # outcomes (replan → dispatch → gate → replan), so the streak MUST survive a
        # gate to ever reach the threshold. A non-LIVE gate that routes back to
        # /replan leaves the stall streak untouched.
        s = ld.LoopState(iteration=2, max_iterations=10,
                         consecutive_unproductive_replan=1)
        d = ld.decide(s, self._gate(Verdict.STALE_STAMP))
        assert d.next_state.consecutive_unproductive_replan == 1

    def test_replan_stalled_opt_in_unclassified_never_trips(self):
        # Opt-in / byte-identical: a caller that never classifies replan
        # productivity gets the FQ-240 default (PRODUCTIVE-when-None), which records
        # a SUCCESS each replan — so the stall streak never accumulates and an
        # un-migrated caller is unaffected. Two unclassified replans around a gate
        # must NOT trip.
        s = ld.LoopState(iteration=1, max_iterations=10)
        unclassified = ld.IterationOutcome(kind=ld.OutcomeKind.REPLAN_DONE)
        d1 = ld.decide(s, unclassified)
        d2 = ld.decide(d1.next_state, self._gate(Verdict.STALE_STAMP))
        d3 = ld.decide(d2.next_state, unclassified)
        assert d3.action == "continue"
        assert d3.next_state.consecutive_unproductive_replan == 0

    def test_replan_stalled_default_state_byte_identical(self):
        # A default LoopState routes a single unproductive replan to a continue
        # exactly as the pre-#506 loop did — the new rung only changes behavior once
        # the stall streak (fed only by a classified UNPRODUCTIVE replan) reaches 2.
        s = ld.LoopState(iteration=1, max_iterations=10)
        d = ld.decide(s, self._unprod_replan())
        assert d.action == "continue"
        assert d.next_mode == "dispatch"

    def test_replan_stalled_expressed_through_breaker_threshold(self):
        # #506's architectural requirement: the rung is the dos.breaker primitive,
        # not a 6th inline counter. A custom threshold flows straight through to the
        # breaker fold — max_unproductive_replan=3 trips on the 3rd, not the 2nd.
        s = ld.LoopState(iteration=1, max_iterations=10, max_unproductive_replan=3)
        d1 = ld.decide(s, self._unprod_replan())
        d2 = ld.decide(d1.next_state, self._gate(Verdict.STALE_STAMP))
        d3 = ld.decide(d2.next_state, self._unprod_replan())
        assert d3.action == "continue"                       # 2 < 3, not yet
        d4 = ld.decide(d3.next_state, self._gate(Verdict.STALE_STAMP))
        d5 = ld.decide(d4.next_state, self._unprod_replan())
        assert d5.action == "stop"
        assert d5.stop_reason == ld.StopReason.REPLAN_STALLED

    # ---- FQ-510 re-dispatch-INVARIANT BLOCKED stop (first-occurrence honest-STOP
    # ----  for a BLOCKED cause a /replan cannot clear). The post-run analogue of
    # ----  the pre-launch PICK_HELD_INVARIANT rung.

    def _blocked(self, cause):
        return ld.IterationOutcome(
            kind=ld.OutcomeKind.GATE, verdict=Verdict.BLOCKED, blocked_cause=cause)

    def test_fq510_operator_decision_stops_on_first_blocked(self):
        # The QWD2 trajectory: a BLOCKED classified as operator_decision
        # (self_heals_via="") must STOP on the FIRST occurrence — a /replan cannot
        # answer an operator decision, so spinning it is pure churn.
        state = ld.LoopState(iteration=3, max_iterations=10)
        d = ld.decide(state, self._blocked("operator_decision"))
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.BLOCKED_REDISPATCH_INVARIANT
        assert d.surface is True
        # Did NOT increment the FQ-452 stale-stamp streak (a terminal class, not
        # a spin candidate).
        assert d.next_state.consecutive_stale_stamp == 0

    def test_fq510_false_ship_oracle_stops_on_first_blocked(self):
        # A ship_oracle_false_positive (the QWD2 reason_class flavor) self-heals
        # via /unstick, NOT /replan — also invariant to a re-dispatch → STOP first.
        state = ld.LoopState(iteration=2, max_iterations=10)
        d = ld.decide(state, self._blocked("ship_oracle_false_positive"))
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.BLOCKED_REDISPATCH_INVARIANT

    def test_fq510_replan_curable_blocked_falls_through_to_fq452(self):
        # A BLOCKED whose cause IS /replan-curable (lane_all_inflight_or_deferred,
        # a genuine drain/refill condition) must NOT take the invariant stop — it
        # routes to /replan as before and counts toward the FQ-452 spin-breaker.
        state = ld.LoopState(iteration=1, max_iterations=10)
        d = ld.decide(state, self._blocked("lane_all_inflight_or_deferred"))
        assert d.action == "continue"
        assert d.next_mode == "replan"
        assert d.next_state.consecutive_stale_stamp == 1  # FQ-452 counter armed

    def test_fq510_blocked_without_cause_is_byte_identical(self):
        # A BLOCKED with NO classified cause (the driver could not name one)
        # preserves today's behavior exactly: routes to /replan, FQ-452 counts it.
        state = ld.LoopState(iteration=1, max_iterations=10)
        d = ld.decide(state, ld.IterationOutcome(
            kind=ld.OutcomeKind.GATE, verdict=Verdict.BLOCKED))
        assert d.action == "continue"
        assert d.next_mode == "replan"
        assert d.next_state.consecutive_stale_stamp == 1

    def test_fq510_unknown_cause_key_falls_through(self):
        # A blocked_cause that is not in the BLOCKED_REASONS catalog (an
        # un-migrated / typo'd key) resolves to None → fall through to FQ-452,
        # never a spurious invariant stop.
        state = ld.LoopState(iteration=1, max_iterations=10)
        d = ld.decide(state, self._blocked("some_unknown_cause_xyz"))
        assert d.action == "continue"
        assert d.next_mode == "replan"

    def test_fq510_invariant_only_on_blocked_not_stale_stamp(self):
        # A STALE_STAMP gate cannot carry a blocked_cause (post-init guard), and
        # the invariant rung is gated on verdict==BLOCKED — so a STALE_STAMP keeps
        # the FQ-452 path regardless. (Guards the rung from over-firing.)
        state = ld.LoopState(iteration=1, max_iterations=10)
        d = ld.decide(state, self._gate(Verdict.STALE_STAMP))
        assert d.action == "continue"
        assert d.next_state.consecutive_stale_stamp == 1

    def test_fq510_post_init_rejects_blocked_cause_on_non_blocked(self):
        # blocked_cause is only valid on a GATE BLOCKED — a DRAIN/SHIPPED/etc.
        # carrying it is a programming error the dataclass refuses.
        import pytest
        with pytest.raises(ValueError):
            ld.IterationOutcome(
                kind=ld.OutcomeKind.GATE, verdict=Verdict.DRAIN,
                blocked_cause="operator_decision")
        with pytest.raises(ValueError):
            ld.IterationOutcome(
                kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN",
                ship_count=1, blocked_cause="operator_decision")

    def test_shipped_continues(self):
        state = ld.LoopState(iteration=1, max_iterations=10)
        # A SHIPPED outcome carries packet_judge + ship_count together (the
        # post-init contract the ported module enforces).
        outcome = ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=2)
        d = ld.decide(state, outcome)
        assert d.action in ("continue", "retry-same-iter")

    def test_iteration_cap_stops(self):
        state = ld.LoopState(iteration=10, max_iterations=10)
        outcome = ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=1)
        d = ld.decide(state, outcome)
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.ITERATION_CAP

    def test_wait_marker_budget(self):
        assert ld.wait_marker_budget(0, 4).allow
        assert not ld.wait_marker_budget(4, 4).allow


class TestUnmeasuredShippedStall:
    """FQ-420: a SHIPPED self-report whose PJ2 measurement was OWED but is
    missing must STALL, not silently continue on the conservative healthy path.

    A SHIPPED token is the /dispatch child's claim; the packet-judge is the
    kernel's independent measurement of it. When the driver could not resolve
    the fanout run-ts, `packet_judge` comes back None on a head==SHIPPED iter —
    the kernel then has a claimed ship it could not verify. The substrate must
    refuse to trust it. The driver bug that surfaced this (a manual git-log
    check was what caught the lie) is the canonical "believe-the-self-report"
    failure the kernel exists to prevent.
    """

    def test_expected_but_null_judge_stalls_and_surfaces(self):
        # measurement_expected=True + packet_judge=None → STALL (not continue).
        state = ld.LoopState(iteration=1, max_iterations=10)
        outcome = ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, measurement_expected=True)
        d = ld.decide(state, outcome)
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.UNMEASURED_SHIPPED
        assert d.surface is True

    def test_unexpected_null_judge_still_continues(self):
        # The un-migrated caller (no measurement claimed, no judge) keeps the
        # pre-FQ-420 conservative healthy path — byte-identical to before.
        state = ld.LoopState(iteration=1, max_iterations=10)
        outcome = ld.IterationOutcome(kind=ld.OutcomeKind.SHIPPED)
        d = ld.decide(state, outcome)
        assert d.action == "continue"
        assert d.next_mode == "dispatch"

    def test_expected_and_present_judge_continues(self):
        # A measurement WAS owed AND delivered (healthy) → normal continue.
        state = ld.LoopState(iteration=1, max_iterations=10)
        outcome = ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, measurement_expected=True,
            packet_judge="SHIPPED-CLEAN", ship_count=2)
        d = ld.decide(state, outcome)
        assert d.action == "continue"

    def test_unmeasured_stall_beats_iteration_cap(self):
        # The specific STALL reason must win over the bare iteration cap — the
        # operator wants "unmeasured ship", not "reached max_iterations".
        state = ld.LoopState(iteration=10, max_iterations=10)
        outcome = ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, measurement_expected=True)
        d = ld.decide(state, outcome)
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.UNMEASURED_SHIPPED

    def test_measurement_expected_only_valid_on_shipped(self):
        # The flag is SHIPPED-only — a GATE/REPLAN_DONE outcome that sets it is
        # a contract error (those iters owe no packet-judge).
        import pytest
        with pytest.raises(ValueError):
            ld.IterationOutcome(
                kind=ld.OutcomeKind.GATE, verdict=Verdict.DRAIN,
                measurement_expected=True)


# A representative VALID IterationOutcome per kind — respecting the post-init
# contract (GATE needs a verdict; SHIPPED carries packet_judge+ship_count
# together; REPLAN_DONE may carry productivity; the fault kinds carry nothing).
# Used by the byte-identical proof to assert the opt-in liveness field changes
# nothing when absent, across the whole outcome space.
_OUTCOME_BY_KIND = {
    ld.OutcomeKind.SHIPPED: ld.IterationOutcome(
        kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=2),
    ld.OutcomeKind.GATE: ld.IterationOutcome(
        kind=ld.OutcomeKind.GATE, verdict=Verdict.LIVE),
    ld.OutcomeKind.REPLAN_DONE: ld.IterationOutcome(
        kind=ld.OutcomeKind.REPLAN_DONE),
    ld.OutcomeKind.UNCLEAR: ld.IterationOutcome(kind=ld.OutcomeKind.UNCLEAR),
    ld.OutcomeKind.RATE_LIMITED: ld.IterationOutcome(
        kind=ld.OutcomeKind.RATE_LIMITED),
    ld.OutcomeKind.OVERLOADED: ld.IterationOutcome(
        kind=ld.OutcomeKind.OVERLOADED),
    ld.OutcomeKind.LAUNCH_FAILED: ld.IterationOutcome(
        kind=ld.OutcomeKind.LAUNCH_FAILED),
}


class TestLivenessSelfStop:
    """docs/99 / docs/82 Phase-3a: the loop self-stops when the in-flight
    `Liveness` verdict it was given is SPINNING — a ground-truth anti-spin
    breaker reading git/journal, not the caller's outcome token. Opt-in: a
    `LoopState` with no liveness verdict is byte-identical to the pre-3a loop.
    """

    def test_loop_stops_on_spinning(self):
        # SPINNING + an otherwise-healthy SHIPPED outcome → STOP with SPINNING.
        # The ground-truth verdict pre-empts the SHIPPED self-report's continue.
        from dos.liveness import Liveness
        state = ld.LoopState(iteration=1, max_iterations=10,
                             liveness=Liveness.SPINNING)
        outcome = ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=2)
        d = ld.decide(state, outcome)
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.SPINNING
        assert d.surface is True

    def test_no_liveness_verdict_is_byte_identical(self):
        # THE behavior-preservation proof. Over EVERY OutcomeKind, decide()
        # returns an identical LoopDecision whether `liveness` is the default
        # (unset) or explicitly None — the opt-in field changes nothing absent.
        for kind, outcome in _OUTCOME_BY_KIND.items():
            base = ld.LoopState(iteration=2, max_iterations=10)
            explicit_none = ld.LoopState(
                iteration=2, max_iterations=10, liveness=None)
            assert ld.decide(base, outcome) == ld.decide(explicit_none, outcome), (
                f"{kind} is not byte-identical between default and liveness=None")

    def test_advancing_verdict_does_not_stop(self):
        # ADVANCING is the benign verdict — it must produce the SAME decision as
        # no verdict at all (it never stops, never alters the path).
        from dos.liveness import Liveness
        for kind, outcome in _OUTCOME_BY_KIND.items():
            none_state = ld.LoopState(iteration=2, max_iterations=10)
            adv_state = ld.LoopState(
                iteration=2, max_iterations=10, liveness=Liveness.ADVANCING)
            assert ld.decide(none_state, outcome) == ld.decide(adv_state, outcome), (
                f"ADVANCING changed the decision for {kind} (it must not)")

    def test_stalled_verdict_does_not_self_stop(self):
        # STALLED is the SUPERVISOR's reap input, not a live loop's self-stop.
        # A loop making decisions is by construction alive; STALLED reaching
        # decide() is degenerate and must NOT be mapped to a stop here — it
        # produces the same decision as no verdict (SPINNING-only is the rule).
        from dos.liveness import Liveness
        for kind, outcome in _OUTCOME_BY_KIND.items():
            none_state = ld.LoopState(iteration=2, max_iterations=10)
            stalled_state = ld.LoopState(
                iteration=2, max_iterations=10, liveness=Liveness.STALLED)
            d = ld.decide(stalled_state, outcome)
            assert d == ld.decide(none_state, outcome), (
                f"STALLED changed the decision for {kind} (SPINNING-only rule)")
            assert d.stop_reason != ld.StopReason.SPINNING

    def test_spinning_does_not_preempt_rate_limited(self):
        # A RATE_LIMITED outcome with a SPINNING verdict still stops with
        # RATE_LIMITED, not SPINNING — the not-a-fault stop is checked first and
        # an idle-because-backing-off run is not "spinning".
        from dos.liveness import Liveness
        state = ld.LoopState(iteration=1, max_iterations=10,
                             liveness=Liveness.SPINNING)
        d = ld.decide(state, ld.IterationOutcome(kind=ld.OutcomeKind.RATE_LIMITED))
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.RATE_LIMITED

    def test_spinning_does_not_preempt_launch_failed(self):
        # LAUNCH_FAILED is checked before the SPINNING rung too.
        from dos.liveness import Liveness
        state = ld.LoopState(iteration=1, max_iterations=10,
                             liveness=Liveness.SPINNING)
        d = ld.decide(state, ld.IterationOutcome(kind=ld.OutcomeKind.LAUNCH_FAILED))
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.LAUNCH_FAILED

    def test_spinning_does_not_preempt_overloaded_retry(self):
        # An OVERLOADED (529) outcome retries with backoff even under a SPINNING
        # verdict — the transient-overload ladder wins; a run waiting out a 529
        # is not spinning. (Pins the placement after the OVERLOADED block.)
        from dos.liveness import Liveness
        state = ld.LoopState(iteration=1, max_iterations=10,
                             liveness=Liveness.SPINNING)
        d = ld.decide(state, ld.IterationOutcome(kind=ld.OutcomeKind.OVERLOADED))
        assert d.action == "retry-same-iter"
        assert d.backoff_seconds > 0

    def test_spinning_beats_iteration_cap(self):
        # The specific SPINNING reason wins over the bare iteration cap — the
        # operator wants "spinning", not "reached max_iterations".
        from dos.liveness import Liveness
        state = ld.LoopState(iteration=10, max_iterations=10,
                             liveness=Liveness.SPINNING)
        outcome = ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=1)
        d = ld.decide(state, outcome)
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.SPINNING


class TestOuterRatchetSelfStop:
    """docs/351: the OUTER RATCHET — RSI first-class across the loops. The loop
    self-stops with NOT_RATCHETING when the in-flight `improve.CandidateVerdict` it
    was given ESCALATEd: N iterations in a row with no witnessed net gain (the
    breaker fired INSIDE `improve`), i.e. running-but-not-improving. The verdict is
    read VERBATIM — the loop adds no second judgement (the docs/138 non-forgeability
    carried end-to-end). KEEP / a lone REVERT never stop. Opt-in: no verdict →
    byte-identical to the pre-docs/351 loop. The fourth instance of the
    optional-in-flight-evidence pattern (sibling of `liveness`).
    """

    @staticmethod
    def _escalate():
        from dos import improve
        # consecutive_reverts == max → ESCALATE on a no-op (green/clean, flat metric)
        return improve.classify(
            improve.CandidateEvidence(
                suite_passed=True, truth_clean=True, work=0, baseline_work=0,
                consecutive_reverts=3, narrated="loud progress claim"),
            improve.ImprovePolicy(max_consecutive_reverts=3))

    @staticmethod
    def _revert():
        from dos import improve
        # a single no-op REVERT, breaker not open (1 of 3)
        return improve.classify(
            improve.CandidateEvidence(
                suite_passed=True, truth_clean=True, work=0, baseline_work=0,
                consecutive_reverts=0, narrated=""),
            improve.ImprovePolicy(max_consecutive_reverts=3))

    @staticmethod
    def _keep():
        from dos import improve
        return improve.classify(
            improve.CandidateEvidence(
                suite_passed=True, truth_clean=True, work=5, baseline_work=0,
                consecutive_reverts=0, narrated=""),
            improve.ImprovePolicy(max_consecutive_reverts=3))

    def test_escalate_stops_not_ratcheting_even_on_healthy_shipped(self):
        # THE core case: an ESCALATE ratchet verdict stops with NOT_RATCHETING even
        # when this iteration's outcome was a healthy SHIPPED — the ground-truth
        # ratchet beats the SHIPPED self-report (the +31.4pp-over-steps failure: N
        # "SHIPPED" iters that net-ship nothing real).
        from dos import improve
        rat = self._escalate()
        assert rat.verdict is improve.Candidate.ESCALATE
        state = ld.LoopState(iteration=3, max_iterations=10, ratchet=rat)
        outcome = ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=2)
        d = ld.decide(state, outcome)
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.NOT_RATCHETING
        assert d.surface is True

    def test_reason_is_taken_verbatim_from_improve(self):
        # Non-forgeability carried end-to-end: the rung's reason embeds the improve
        # verdict's own reason (the loop adds no second judgement). The forgeable
        # `narrated` ("loud progress claim") never appears — improve parsed it for
        # nothing, so it cannot ride into the stop.
        rat = self._escalate()
        d = ld.decide(ld.LoopState(iteration=3, max_iterations=10, ratchet=rat),
                      ld.IterationOutcome(kind=ld.OutcomeKind.SHIPPED,
                                          packet_judge="SHIPPED-CLEAN", ship_count=1))
        assert rat.reason in d.reason
        assert "loud progress claim" not in d.reason

    def test_lone_revert_does_not_stop(self):
        # A single REVERT (breaker not open) is one tick, NOT a stop — it produces
        # the SAME decision as no ratchet verdict at all, over every outcome kind.
        rat = self._revert()
        from dos import improve
        assert rat.verdict is improve.Candidate.REVERT
        for kind, outcome in _OUTCOME_BY_KIND.items():
            none_state = ld.LoopState(iteration=2, max_iterations=10)
            rev_state = ld.LoopState(iteration=2, max_iterations=10, ratchet=rat)
            assert ld.decide(none_state, outcome) == ld.decide(rev_state, outcome), (
                f"a lone REVERT changed the decision for {kind} (it must not)")

    def test_keep_does_not_stop(self):
        # A KEEP ratchet verdict never stops — same decision as no verdict.
        rat = self._keep()
        from dos import improve
        assert rat.verdict is improve.Candidate.KEEP
        for kind, outcome in _OUTCOME_BY_KIND.items():
            none_state = ld.LoopState(iteration=2, max_iterations=10)
            keep_state = ld.LoopState(iteration=2, max_iterations=10, ratchet=rat)
            assert ld.decide(none_state, outcome) == ld.decide(keep_state, outcome), (
                f"a KEEP changed the decision for {kind} (it must not)")

    def test_no_ratchet_verdict_is_byte_identical(self):
        # The opt-in / behavior-preservation proof: over EVERY OutcomeKind, decide()
        # is identical whether `ratchet` is the default (unset) or explicitly None.
        for kind, outcome in _OUTCOME_BY_KIND.items():
            base = ld.LoopState(iteration=2, max_iterations=10)
            explicit_none = ld.LoopState(iteration=2, max_iterations=10, ratchet=None)
            assert ld.decide(base, outcome) == ld.decide(explicit_none, outcome), (
                f"{kind} is not byte-identical between default and ratchet=None")

    def test_ratchet_verdict_never_survives_into_next_state(self):
        # In-flight evidence, not carried state: the verdict is cleared up front, so
        # it never lingers in a returned next_state (terminal or continuing).
        rat = self._keep()  # KEEP continues, so next_state is meaningful
        d = ld.decide(ld.LoopState(iteration=2, max_iterations=10, ratchet=rat),
                      ld.IterationOutcome(kind=ld.OutcomeKind.SHIPPED,
                                          packet_judge="SHIPPED-CLEAN", ship_count=1))
        assert d.next_state.ratchet is None

    def test_complete_beats_not_ratcheting(self):
        # Precedence: a provably-FINISHED run is not "not ratcheting". COMPLETE is
        # checked before the ratchet rung, so it wins even when the ratchet ESCALATEd.
        from dos.completion import Completion, CompletionVerdict
        comp = CompletionVerdict(state=Completion.COMPLETE, reason="all units verified",
                                 run_id="r-test")
        state = ld.LoopState(iteration=3, max_iterations=10,
                             ratchet=self._escalate(), completion=comp)
        d = ld.decide(state, ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=1))
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.COMPLETE

    def test_spinning_wins_the_reason_over_ratchet(self):
        # Precedence: when SPINNING and the ratchet-ESCALATE both fire (the same
        # disease — a run landing 0 delta AND escalating), the ground-truth git-delta
        # reason wins. SPINNING is checked first.
        from dos.liveness import Liveness
        state = ld.LoopState(iteration=3, max_iterations=10,
                             ratchet=self._escalate(), liveness=Liveness.SPINNING)
        d = ld.decide(state, ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=1))
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.SPINNING


class TestDescendantProgressAdoptWait:
    """FQ-509: a parent `-p` that PARKED while a descendant it spawned is still
    committing the registered picks lands as UNCLEAR (the ancestry check ran the
    instant the parent exited, 0 picks committed yet). When the host supplies
    `descendant_progress == ADVANCING` (a real forward delta — the corpse-guard is
    the host's job), the UNCLEAR is a parked-but-PRODUCTIVE child and `decide()`
    adopt-waits (continue WITHOUT charging the UNCLEAR breaker), bounded by
    `consecutive_adopt_wait`. DEAD / NONE_OBSERVED / None take today's exact path.
    Opt-in: no signal → byte-identical to the pre-FQ-509 loop.
    """

    def test_advancing_continues_without_charging_unclear_breaker(self):
        # The headline: an UNCLEAR with a FORWARD-PROGRESSING descendant continues
        # (re-dispatch / adopt-wait), does NOT charge consecutive_unclear, and bumps
        # the adopt-wait counter. A prior UNCLEAR streak is RESET (live != fault).
        state = ld.LoopState(
            iteration=2, max_iterations=10, consecutive_unclear=2,
            descendant_progress=ld.DescendantProgress.ADVANCING)
        d = ld.decide(state, ld.IterationOutcome(kind=ld.OutcomeKind.UNCLEAR))
        assert d.action == "continue"
        assert d.next_mode == "dispatch"
        assert d.next_state.consecutive_unclear == 0, "live descendant must reset the UNCLEAR breaker"
        assert d.next_state.consecutive_adopt_wait == 1
        assert d.stop_reason is None

    def test_no_signal_is_byte_identical(self):
        # THE behavior-preservation proof. Over EVERY OutcomeKind, decide() returns
        # an identical LoopDecision whether descendant_progress is the default
        # (unset), explicitly None, NONE_OBSERVED, or DEAD — only ADVANCING+UNCLEAR
        # changes anything. (DEAD/NONE_OBSERVED collapse to None on the UNCLEAR path
        # and are a no-op on every other kind.)
        for kind, outcome in _OUTCOME_BY_KIND.items():
            base = ld.LoopState(iteration=2, max_iterations=10)
            for dp in (None, ld.DescendantProgress.NONE_OBSERVED,
                       ld.DescendantProgress.DEAD):
                variant = ld.LoopState(
                    iteration=2, max_iterations=10, descendant_progress=dp)
                assert ld.decide(base, outcome) == ld.decide(variant, outcome), (
                    f"{kind} not byte-identical between default and descendant_progress={dp}")

    def test_advancing_is_a_noop_on_non_unclear_kinds(self):
        # ADVANCING must change NOTHING for any kind other than UNCLEAR — the rung
        # lives strictly inside the UNCLEAR branch.
        for kind, outcome in _OUTCOME_BY_KIND.items():
            if kind is ld.OutcomeKind.UNCLEAR:
                continue
            base = ld.LoopState(iteration=2, max_iterations=10)
            adv = ld.LoopState(
                iteration=2, max_iterations=10,
                descendant_progress=ld.DescendantProgress.ADVANCING)
            assert ld.decide(base, outcome) == ld.decide(adv, outcome), (
                f"ADVANCING changed the decision for {kind} (it must not)")

    def test_descendant_progress_cleared_from_next_state(self):
        # The verdict is in-flight evidence: it must NEVER survive into next_state
        # (cleared up-front), so a stale verdict can't fire spuriously next iter.
        state = ld.LoopState(
            iteration=2, max_iterations=10,
            descendant_progress=ld.DescendantProgress.ADVANCING)
        d = ld.decide(state, ld.IterationOutcome(kind=ld.OutcomeKind.UNCLEAR))
        assert d.next_state.descendant_progress is None

    def test_adopt_wait_bound_degrades_to_unclear_breaker(self):
        # The clock-free bound: once consecutive_adopt_wait reaches max_adopt_wait,
        # an ADVANCING UNCLEAR FALLS THROUGH to today's UNCLEAR breaker path (it
        # charges consecutive_unclear), NOT a new terminal and NOT an infinite
        # adopt-wait. With max_adopt_wait=1, the FIRST adopt-wait trips the bound.
        state = ld.LoopState(
            iteration=2, max_iterations=10, max_adopt_wait=1,
            consecutive_adopt_wait=1, consecutive_unclear=0,
            descendant_progress=ld.DescendantProgress.ADVANCING)
        d = ld.decide(state, ld.IterationOutcome(kind=ld.OutcomeKind.UNCLEAR))
        # Tripped the adopt-wait bound → normal UNCLEAR path: charges the breaker,
        # continues (one more UNCLEAR is not yet the 3-strike stop).
        assert d.action == "continue"
        assert d.next_state.consecutive_unclear == 1
        assert d.next_state.consecutive_adopt_wait == 0

    def test_dead_descendant_takes_todays_unclear_path(self):
        # A genuinely DEAD descendant (the corpse the host maps to DEAD, or a real
        # death) must take the normal UNCLEAR breaker — it must NEVER adopt-wait.
        state = ld.LoopState(
            iteration=2, max_iterations=10, consecutive_unclear=2,
            descendant_progress=ld.DescendantProgress.DEAD)
        d = ld.decide(state, ld.IterationOutcome(kind=ld.OutcomeKind.UNCLEAR))
        # Same as no signal: charges the breaker (2→3) and STOPs at max_unclear=3.
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.CONSECUTIVE_UNCLEAR

    def test_flapping_alive_quiet_still_reaches_unclear_cap(self):
        # FLAPPING defense: a child that reads ADVANCING on odd iters and quiet on
        # even iters must still reach the UNCLEAR cap — consecutive_adopt_wait
        # resets on the non-advancing iter, but consecutive_unclear is NOT reset
        # there, so the quiet iters accrue toward max_unclear. Simulate two quiet
        # UNCLEARs bracketing an advancing one; the quiet streak survives.
        # iter A: quiet UNCLEAR (no signal) → unclear=1
        sA = ld.LoopState(iteration=1, max_iterations=10)
        dA = ld.decide(sA, ld.IterationOutcome(kind=ld.OutcomeKind.UNCLEAR))
        assert dA.next_state.consecutive_unclear == 1
        # iter B: ADVANCING UNCLEAR → resets unclear to 0 (this is the cost of the
        # rung; a live committing child legitimately clears the fault streak).
        import dataclasses
        sB = dataclasses.replace(
            dA.next_state, descendant_progress=ld.DescendantProgress.ADVANCING)
        dB = ld.decide(sB, ld.IterationOutcome(kind=ld.OutcomeKind.UNCLEAR))
        assert dB.next_state.consecutive_unclear == 0
        assert dB.next_state.consecutive_adopt_wait == 1

    def test_advancing_does_not_preempt_not_a_fault_stops(self):
        # ADVANCING lives inside the UNCLEAR rung, which is AFTER the not-a-fault
        # stops — so a RATE_LIMITED/LAUNCH_FAILED outcome is unaffected even with an
        # ADVANCING descendant verdict present.
        for kind, stop in (
            (ld.OutcomeKind.RATE_LIMITED, ld.StopReason.RATE_LIMITED),
            (ld.OutcomeKind.LAUNCH_FAILED, ld.StopReason.LAUNCH_FAILED),
        ):
            state = ld.LoopState(
                iteration=1, max_iterations=10,
                descendant_progress=ld.DescendantProgress.ADVANCING)
            d = ld.decide(state, ld.IterationOutcome(kind=kind))
            assert d.action == "stop"
            assert d.stop_reason == stop


# ── docs/117 Phase 3: the loop stops-on-DONE (completion) / no-fixpoint (convergence)
def _completion(state, **kw):
    """A bare `CompletionVerdict` with the given `Completion` state, for feeding
    `decide()`. The verdict's own derivation is tested in test_completion.py; here
    we only need the typed object the gate reads."""
    from dos.completion import Completion, CompletionVerdict
    return CompletionVerdict(state=state, reason=kw.get("reason", f"{state.value} (test)"),
                             run_id=kw.get("run_id", "r-test"),
                             residual=kw.get("residual", ()),
                             verified=kw.get("verified", ()),
                             declared=kw.get("declared", ()))


def _convergence(state, **kw):
    from dos.completion import ConvergenceVerdict
    return ConvergenceVerdict(state=state, reason=kw.get("reason", f"{state.value} (test)"),
                              window=kw.get("window", ()))


class TestCompletionSelfStop:
    """docs/117 Phase 3: the loop self-stops when the in-flight `CompletionVerdict`
    the caller gathered is COMPLETE — the FIRST stop reason that means "finished,"
    not "gave up" (the anti-`ITERATION_CAP`). UNDERDECLARED and a surfacing
    `ConvergenceVerdict` (THRASHING/STARVED) stop AND surface. Opt-in: a `LoopState`
    with no completion/convergence verdict is byte-identical to the pre-Phase-3 loop.
    The completion gate is checked AFTER the not-a-fault stops and BEFORE SPINNING.
    """

    def test_loop_stops_on_complete_no_surface(self):
        # COMPLETE + an otherwise-healthy SHIPPED outcome → STOP with COMPLETE and
        # NO surface (a clean finish is not an operator decision). This is the
        # stop-on-done behaviour: the work is verifiably finished, not out-of-budget.
        from dos.completion import Completion
        state = ld.LoopState(iteration=2, max_iterations=10,
                             completion=_completion(Completion.COMPLETE,
                                                    verified=("a", "b"), declared=("a", "b")))
        outcome = ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=2)
        d = ld.decide(state, outcome)
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.COMPLETE
        assert d.surface is False

    def test_underdeclared_stops_and_surfaces(self):
        # UNDERDECLARED → stop AND surface (run thinks it's done; scope in doubt;
        # a human must reconcile). Routed through the THRASHING terminal.
        from dos.completion import Completion
        state = ld.LoopState(iteration=2, max_iterations=10,
                             completion=_completion(Completion.UNDERDECLARED))
        outcome = ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=1)
        d = ld.decide(state, outcome)
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.THRASHING
        assert d.surface is True

    def test_incomplete_does_not_stop_here(self):
        # INCOMPLETE means "continue, re-dispatch the residual" — the caller owns
        # that actuation; decide() must NOT stop on it. It produces the same decision
        # as no completion verdict (the gate falls through).
        from dos.completion import Completion
        for kind, outcome in _OUTCOME_BY_KIND.items():
            none_state = ld.LoopState(iteration=2, max_iterations=10)
            inc_state = ld.LoopState(
                iteration=2, max_iterations=10,
                completion=_completion(Completion.INCOMPLETE, residual=("c",)))
            d = ld.decide(inc_state, outcome)
            assert d == ld.decide(none_state, outcome), (
                f"INCOMPLETE changed the decision for {kind} (it must not stop here)")
            assert d.stop_reason != ld.StopReason.COMPLETE

    def test_indeterminate_does_not_assert_done(self):
        # INDETERMINATE ("can't tell from an unsound fold") must NOT assert done —
        # it falls through to the existing logic, same decision as no verdict.
        from dos.completion import Completion
        for kind, outcome in _OUTCOME_BY_KIND.items():
            none_state = ld.LoopState(iteration=2, max_iterations=10)
            ind_state = ld.LoopState(
                iteration=2, max_iterations=10,
                completion=_completion(Completion.INDETERMINATE))
            d = ld.decide(ind_state, outcome)
            assert d == ld.decide(none_state, outcome), (
                f"INDETERMINATE changed the decision for {kind} (must not assert done)")
            assert d.stop_reason != ld.StopReason.COMPLETE

    def test_no_completion_verdict_is_byte_identical(self):
        # THE behaviour-preservation proof. Over EVERY OutcomeKind, decide() returns
        # an identical LoopDecision whether completion/convergence are the default
        # (unset) or explicitly None — the opt-in fields change nothing when absent.
        for kind, outcome in _OUTCOME_BY_KIND.items():
            base = ld.LoopState(iteration=2, max_iterations=10)
            explicit_none = ld.LoopState(
                iteration=2, max_iterations=10, completion=None, convergence=None)
            assert ld.decide(base, outcome) == ld.decide(explicit_none, outcome), (
                f"{kind} is not byte-identical between default and completion/convergence=None")

    def test_convergence_thrashing_stops_and_surfaces(self):
        # A THRASHING convergence verdict (no completion verdict) → stop AND surface:
        # the residual churns but won't reach a fixpoint; don't burn the cap silently.
        from dos.completion import Convergence
        state = ld.LoopState(iteration=2, max_iterations=10,
                             convergence=_convergence(Convergence.THRASHING, window=(4, 3, 4, 3)))
        outcome = ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=1)
        d = ld.decide(state, outcome)
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.THRASHING
        assert d.surface is True

    def test_convergence_starved_stops_and_surfaces(self):
        # STARVED also surfaces (it `should_surface`): residual flat and non-empty.
        from dos.completion import Convergence
        state = ld.LoopState(iteration=2, max_iterations=10,
                             convergence=_convergence(Convergence.STARVED, window=(5, 5, 5)))
        d = ld.decide(state, ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=1))
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.THRASHING
        assert d.surface is True

    def test_convergence_converging_does_not_stop(self):
        # CONVERGING / INSUFFICIENT never stop — "no fixpoint yet" is not a stop
        # signal. Same decision as no convergence verdict.
        from dos.completion import Convergence
        for st in (Convergence.CONVERGING, Convergence.INSUFFICIENT):
            for kind, outcome in _OUTCOME_BY_KIND.items():
                none_state = ld.LoopState(iteration=2, max_iterations=10)
                conv_state = ld.LoopState(
                    iteration=2, max_iterations=10, convergence=_convergence(st))
                assert ld.decide(conv_state, outcome) == ld.decide(none_state, outcome), (
                    f"{st.value} changed the decision for {kind} (it must not stop)")

    def test_complete_beats_convergence(self):
        # If BOTH a COMPLETE completion and a THRASHING convergence are supplied,
        # COMPLETE wins — a converged run is done, not thrashing (the gate checks
        # COMPLETE before the convergence rung).
        from dos.completion import Completion, Convergence
        state = ld.LoopState(iteration=2, max_iterations=10,
                             completion=_completion(Completion.COMPLETE),
                             convergence=_convergence(Convergence.THRASHING))
        d = ld.decide(state, ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=1))
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.COMPLETE
        assert d.surface is False

    def test_complete_beats_spinning(self):
        # The operator-decided precedence: COMPLETE wins over SPINNING. A run
        # resumed with nothing left to do is BOTH zero-delta (SPINNING) and
        # all-verified (COMPLETE); "done" is the honest reason (non-forgeable rung).
        from dos.completion import Completion
        from dos.liveness import Liveness
        state = ld.LoopState(iteration=2, max_iterations=10,
                             completion=_completion(Completion.COMPLETE),
                             liveness=Liveness.SPINNING)
        d = ld.decide(state, ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=1))
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.COMPLETE

    def test_complete_does_not_preempt_rate_limited(self):
        # The not-a-fault stops are checked BEFORE the completion gate — a run that
        # hit a rate-limit on its last turn reports RATE_LIMITED, not COMPLETE.
        from dos.completion import Completion
        state = ld.LoopState(iteration=2, max_iterations=10,
                             completion=_completion(Completion.COMPLETE))
        d = ld.decide(state, ld.IterationOutcome(kind=ld.OutcomeKind.RATE_LIMITED))
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.RATE_LIMITED

    def test_complete_does_not_preempt_launch_failed(self):
        from dos.completion import Completion
        state = ld.LoopState(iteration=2, max_iterations=10,
                             completion=_completion(Completion.COMPLETE))
        d = ld.decide(state, ld.IterationOutcome(kind=ld.OutcomeKind.LAUNCH_FAILED))
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.LAUNCH_FAILED

    def test_complete_does_not_preempt_overloaded_retry(self):
        # An OVERLOADED (529) outcome retries with backoff even under a COMPLETE
        # verdict — the transient-overload ladder is checked first.
        from dos.completion import Completion
        state = ld.LoopState(iteration=2, max_iterations=10,
                             completion=_completion(Completion.COMPLETE))
        d = ld.decide(state, ld.IterationOutcome(kind=ld.OutcomeKind.OVERLOADED))
        assert d.action == "retry-same-iter"
        assert d.backoff_seconds > 0

    def test_complete_beats_iteration_cap(self):
        # The specific COMPLETE reason wins over the bare iteration cap — the
        # operator wants "done", not "reached max_iterations". This is the docs/117
        # §5.4 inversion: the cap demotes to a backstop.
        from dos.completion import Completion
        state = ld.LoopState(iteration=10, max_iterations=10,
                             completion=_completion(Completion.COMPLETE))
        outcome = ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=1)
        d = ld.decide(state, outcome)
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.COMPLETE

    def test_verdicts_cleared_from_next_state(self):
        # The evidence-not-carried-state discipline: a COMPLETE/THRASHING that does
        # NOT itself stop the loop (here: pre-empted by RATE_LIMITED) must not linger
        # on next_state and fire spuriously next iteration. The caller re-supplies a
        # fresh verdict each turn.
        from dos.completion import Completion, Convergence
        state = ld.LoopState(iteration=2, max_iterations=10,
                             completion=_completion(Completion.COMPLETE),
                             convergence=_convergence(Convergence.THRASHING))
        d = ld.decide(state, ld.IterationOutcome(kind=ld.OutcomeKind.RATE_LIMITED))
        assert d.next_state.completion is None
        assert d.next_state.convergence is None


class TestPickHeldInvariantSelfStop:
    """docs/168 §5 — the honest-STOP rung. A next-lane Pickability that is HELD
    by a re-dispatch-invariant reason STOPs the loop; an absent verdict or a
    re-dispatch-CURABLE hold leaves behavior unchanged."""

    def _shipped(self):
        # A healthy SHIPPED outcome — the iteration that just ran; the rung is
        # about the NEXT lane's pickability, not this outcome.
        return ld.IterationOutcome(
            kind=ld.OutcomeKind.SHIPPED, packet_judge="SHIPPED-CLEAN", ship_count=1)

    def test_draft_class_hold_stops_and_surfaces(self):
        from dos.pickable import Pickability, HoldReason
        state = ld.LoopState(
            iteration=2, max_iterations=10,
            pickability=Pickability.HELD(HoldReason.DRAFT_CLASS, "FMP DRAFT P32"))
        d = ld.decide(state, self._shipped())
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.PICK_HELD_INVARIANT
        assert d.surface is True

    def test_operator_gated_hold_stops(self):
        from dos.pickable import Pickability, HoldReason
        state = ld.LoopState(
            iteration=2, max_iterations=10,
            pickability=Pickability.HELD(HoldReason.OPERATOR_GATED, "decision #475"))
        d = ld.decide(state, self._shipped())
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.PICK_HELD_INVARIANT

    def test_soak_open_hold_stops(self):
        from dos.pickable import Pickability, HoldReason
        state = ld.LoopState(
            iteration=2, max_iterations=10,
            pickability=Pickability.HELD(HoldReason.SOAK_OPEN, "RTN5 soak open"))
        d = ld.decide(state, self._shipped())
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.PICK_HELD_INVARIANT

    def test_dependency_unmet_hold_stops(self):
        from dos.pickable import Pickability, HoldReason
        state = ld.LoopState(
            iteration=2, max_iterations=10,
            pickability=Pickability.HELD(HoldReason.DEPENDENCY_UNMET, "needs FQ-1"))
        d = ld.decide(state, self._shipped())
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.PICK_HELD_INVARIANT

    def test_no_pickability_verdict_is_byte_identical(self):
        # Opt-in: a None pickability (the default) skips the rung — the SHIPPED
        # outcome continues exactly as it did pre-docs/168.
        state_with = ld.LoopState(iteration=2, max_iterations=10)
        d = ld.decide(state_with, self._shipped())
        assert d.action == "continue"
        assert d.next_mode == "dispatch"

    def test_offerable_verdict_does_not_stop(self):
        # An OFFERABLE next lane never stops here — the loop proceeds.
        from dos.pickable import Pickability
        state = ld.LoopState(
            iteration=2, max_iterations=10, pickability=Pickability.OFFERABLE())
        d = ld.decide(state, self._shipped())
        assert d.action == "continue"

    def test_curable_in_flight_hold_does_not_stop(self):
        # IN_FLIGHT is re-dispatch-CURABLE (the holder finishes) → NOT an honest
        # STOP. The loop keeps its existing behavior.
        from dos.pickable import Pickability, HoldReason
        state = ld.LoopState(
            iteration=2, max_iterations=10,
            pickability=Pickability.HELD(HoldReason.IN_FLIGHT, "sibling on it"))
        d = ld.decide(state, self._shipped())
        assert d.action == "continue"

    def test_curable_cooldown_hold_does_not_stop(self):
        # COOLDOWN clears with time → CURABLE → not a stop here.
        from dos.pickable import Pickability, HoldReason
        state = ld.LoopState(
            iteration=2, max_iterations=10,
            pickability=Pickability.HELD(HoldReason.COOLDOWN, "6h cooldown"))
        d = ld.decide(state, self._shipped())
        assert d.action == "continue"

    def test_invariant_hold_does_not_preempt_rate_limited(self):
        # Placement: a not-a-fault stop (RATE_LIMITED) wins — an upstream wall is
        # the more specific reason than an un-pickable next lane.
        from dos.pickable import Pickability, HoldReason
        state = ld.LoopState(
            iteration=2, max_iterations=10,
            pickability=Pickability.HELD(HoldReason.DRAFT_CLASS, "FMP"))
        d = ld.decide(state, ld.IterationOutcome(kind=ld.OutcomeKind.RATE_LIMITED))
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.RATE_LIMITED

    def test_invariant_hold_does_not_preempt_launch_failed(self):
        from dos.pickable import Pickability, HoldReason
        state = ld.LoopState(
            iteration=2, max_iterations=10,
            pickability=Pickability.HELD(HoldReason.OPERATOR_GATED, "#475"))
        d = ld.decide(state, ld.IterationOutcome(kind=ld.OutcomeKind.LAUNCH_FAILED))
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.LAUNCH_FAILED

    def test_invariant_hold_beats_iteration_cap(self):
        # The specific PICK_HELD_INVARIANT reason wins over the bare cap — the
        # operator wants the typed hold to route, not "reached max_iterations".
        from dos.pickable import Pickability, HoldReason
        state = ld.LoopState(
            iteration=10, max_iterations=10,
            pickability=Pickability.HELD(HoldReason.SOAK_OPEN, "soak"))
        d = ld.decide(state, self._shipped())
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.PICK_HELD_INVARIANT

    def test_pickability_cleared_from_next_state(self):
        # Evidence-not-carried-state: a CURABLE pickability that does NOT stop the
        # loop must not linger on next_state and fire spuriously next iteration.
        from dos.pickable import Pickability, HoldReason
        state = ld.LoopState(
            iteration=2, max_iterations=10,
            pickability=Pickability.HELD(HoldReason.IN_FLIGHT, "sibling"))
        d = ld.decide(state, self._shipped())
        assert d.next_state.pickability is None


class TestPickCooldownRung:
    """The docs/207 §3c PICK_COOLDOWN rung — the anti-churn breaker. When the
    next unit (after the host already skipped fresher candidates) is
    RECENTLY_ATTEMPTED, the loop honest-STOPs rather than re-storm a known drain.
    The backtest-invariant shape (`tests/test_dispatch_scout.py`)."""

    def _shipped(self):
        return ld.IterationOutcome(kind=ld.OutcomeKind.SHIPPED)

    def test_loop_decide_pick_cooldown_rung(self):
        # A re-pick-storm replay: the next unit was attempted-and-DRAINed inside the
        # window AND nothing fresher is offerable → STOP, not re-dispatch.
        from dos.cooldown import Cooldown, CooldownState
        cool = Cooldown(state=CooldownState.RECENTLY_ATTEMPTED, unit_id="AUTH3",
                        until_ms=999, count=3, reason="drained 1h ago")
        state = ld.LoopState(iteration=2, max_iterations=10, cooldown=cool)
        d = ld.decide(state, self._shipped())
        assert d.action == "stop"
        assert d.stop_reason == ld.StopReason.PICK_COOLDOWN
        assert d.surface is True

    def test_clear_cooldown_does_not_stop(self):
        from dos.cooldown import Cooldown, CooldownState
        cool = Cooldown(state=CooldownState.CLEAR, unit_id="AUTH3")
        state = ld.LoopState(iteration=2, max_iterations=10, cooldown=cool)
        d = ld.decide(state, self._shipped())
        assert d.action == "continue"

    def test_no_cooldown_verdict_is_byte_identical(self):
        # Opt-in: None cooldown (the default) skips the rung.
        state = ld.LoopState(iteration=2, max_iterations=10)
        d = ld.decide(state, self._shipped())
        assert d.action == "continue"

    def test_invariant_hold_beats_cooldown(self):
        # Placement: a PICK_HELD_INVARIANT (held forever) is more terminal than a
        # time-bounded cooldown — the invariant reason wins when both are present.
        from dos.pickable import Pickability, HoldReason
        from dos.cooldown import Cooldown, CooldownState
        state = ld.LoopState(
            iteration=2, max_iterations=10,
            pickability=Pickability.HELD(HoldReason.DRAFT_CLASS, "DRAFT"),
            cooldown=Cooldown(state=CooldownState.RECENTLY_ATTEMPTED, unit_id="U", until_ms=1))
        d = ld.decide(state, self._shipped())
        assert d.stop_reason == ld.StopReason.PICK_HELD_INVARIANT

    def test_cooldown_does_not_preempt_rate_limited(self):
        from dos.cooldown import Cooldown, CooldownState
        state = ld.LoopState(
            iteration=2, max_iterations=10,
            cooldown=Cooldown(state=CooldownState.RECENTLY_ATTEMPTED, unit_id="U", until_ms=1))
        d = ld.decide(state, ld.IterationOutcome(kind=ld.OutcomeKind.RATE_LIMITED))
        assert d.stop_reason == ld.StopReason.RATE_LIMITED

    def test_cooldown_cleared_from_next_state(self):
        from dos.cooldown import Cooldown, CooldownState
        state = ld.LoopState(
            iteration=2, max_iterations=10,
            cooldown=Cooldown(state=CooldownState.CLEAR, unit_id="U"))
        d = ld.decide(state, self._shipped())
        assert d.next_state.cooldown is None
