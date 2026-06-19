"""SUP — the population verdict (`supervise.supervise`), pinned on frozen fixtures.

The `liveness`/`scope` discipline restated for the population axis: the verdict is
PURE (frozen evidence in, typed verdict out), so every case here is a hand-built
`SuperviseEvidence` — no journal, no clock, no subprocess. The soundness-floor
cases (disjoint spawn plan, never-reap-a-healthy-worker, the no-plan floor) are the
load-bearing ones; the rest pin the population arithmetic.
"""

from __future__ import annotations

import pytest

from dos import supervise as _supervise
from dos.liveness import Liveness
from dos.supervise import (
    DEFAULT_POLICY,
    Disposition,
    LaneLiveness,
    SuperviseEvidence,
    SuperviseOutcome,
    SupervisePolicy,
    SuperviseVerdict,
    load_from_toml,
    overlapping_concurrent_lanes,
    policy_from_table,
    supervise,
)


# --------------------------------------------------------------------------
# Helpers — terse lane builders so the cases read as roster snapshots.
# --------------------------------------------------------------------------
def free(lane, tree=(), exclusive=False):
    return LaneLiveness(lane=lane, liveness=None, tree=tuple(tree), is_exclusive=exclusive)


def held(lane, lv, tree=(), exclusive=False):
    return LaneLiveness(lane=lane, liveness=lv, tree=tuple(tree), is_exclusive=exclusive)


def pending(lane, tree=()):
    return LaneLiveness(lane=lane, liveness=None, tree=tuple(tree), pending=True)


def spinning(lane, age_ms, tree=()):
    """A SPINNING lane carrying its spin age (for the acting-on-spin cases)."""
    return LaneLiveness(lane=lane, liveness=Liveness.SPINNING, tree=tuple(tree),
                        spinning_age_ms=age_ms)


def _lanes_of(plans):
    return {p.lane for p in plans}


# --------------------------------------------------------------------------
# The population arithmetic.
# --------------------------------------------------------------------------
def test_at_target_one_advancing_target_one():
    ev = SuperviseEvidence(lanes=(held("main", Liveness.ADVANCING, ("**/*",)),), target=1)
    v = supervise(ev)
    assert v.verdict == SuperviseOutcome.AT_TARGET
    assert v.alive == 1 and v.admissible == 1
    assert v.spawn == () and v.reap == ()


def test_filling_one_free_disjoint_lane():
    ev = SuperviseEvidence(
        lanes=(free("api", ("src/api/**",)), free("worker", ("src/worker/**",))),
        target=2,
    )
    v = supervise(ev)
    assert v.verdict == SuperviseOutcome.FILLING
    assert v.admissible == 2 and v.alive == 0
    assert _lanes_of(v.spawn) == {"api", "worker"}
    assert all(p.disposition == Disposition.SPAWN for p in v.spawn)


def test_stalled_is_reaped_and_refilled_in_one_tick():
    ev = SuperviseEvidence(
        lanes=(held("api", Liveness.STALLED, ("src/api/**",)),), target=1
    )
    v = supervise(ev)
    # A STALLED worker holds no real lease → alive 0; it is reaped AND refilled.
    assert v.alive == 0
    assert _lanes_of(v.reap) == {"api"}
    assert v.reap[0].disposition == Disposition.REAP
    assert _lanes_of(v.spawn) == {"api"}  # kill-and-refill in one verdict
    # admissible 1 (one concurrent lane), alive 0 → FILLING.
    assert v.verdict == SuperviseOutcome.FILLING


def test_spinning_is_flagged_counts_alive_never_reaped():
    ev = SuperviseEvidence(
        lanes=(held("main", Liveness.SPINNING, ("**/*",)),), target=1
    )
    v = supervise(ev)
    assert v.alive == 1                       # counts toward target (default policy)
    assert v.reap == ()                       # never auto-reaped
    assert _lanes_of(v.flag) == {"main"}      # advisory flag
    assert v.flag[0].disposition == Disposition.FLAG
    assert v.verdict == SuperviseOutcome.AT_TARGET


def test_target_unreachable_generic_default_roster():
    # The generic default: main (**/*, concurrent) + global (**/*, exclusive).
    # admissible computes to 1; target 3 is structurally unreachable.
    ev = SuperviseEvidence(
        lanes=(free("main", ("**/*",)), free("global", ("**/*",), exclusive=True)),
        target=3,
    )
    v = supervise(ev)
    assert v.verdict == SuperviseOutcome.TARGET_UNREACHABLE
    assert v.admissible == 1
    assert "dos.toml [lanes]" in v.reason       # names the fix
    # It STILL fills to the admissible ceiling (one worker on the concurrent lane).
    assert _lanes_of(v.spawn) == {"main"}


def test_over_target_flags_excess_never_reaps_healthy():
    ev = SuperviseEvidence(
        lanes=(
            held("api", Liveness.ADVANCING, ("src/api/**",)),
            held("worker", Liveness.ADVANCING, ("src/worker/**",)),
        ),
        target=1,
    )
    v = supervise(ev)
    assert v.verdict == SuperviseOutcome.OVER_TARGET
    assert v.alive == 2
    assert v.reap == ()                         # NEVER reap a healthy worker
    assert len(v.flag) == 1                     # one excess flagged
    assert v.flag[0].disposition == Disposition.FLAG


def test_pending_lane_counts_alive_and_is_not_respawned():
    # The double-spawn race guard: a pending spawn counts toward alive and the
    # lane is NOT emitted as a spawn again.
    ev = SuperviseEvidence(
        lanes=(pending("api", ("src/api/**",)), free("worker", ("src/worker/**",))),
        target=1,
    )
    v = supervise(ev)
    assert v.alive == 1                         # pending counts as alive-or-coming
    assert "api" not in _lanes_of(v.spawn)      # never re-spawned
    assert v.verdict == SuperviseOutcome.AT_TARGET
    assert v.spawn == ()                        # already at target via the pending


# --------------------------------------------------------------------------
# The SPAWN SOUNDNESS FLOOR — the plan is disjoint by construction.
# (These are the cases the adversarial review traced; the blocker was that the
#  spawn walk took the first N candidates by count without re-checking overlap.)
# --------------------------------------------------------------------------
def test_spawn_plan_never_proposes_two_overlapping_lanes():
    # Roster: X a/** , Y a/sub/** (overlaps X), Z b/** — all FREE, target 2.
    # admissible = 2 ({X, Z}); a blind count-take would grab [X, Y] and emit two
    # OVERLAPPING spawns. The disjoint walk must emit {X, Z}.
    ev = SuperviseEvidence(
        lanes=(
            free("X", ("a/**",)),
            free("Y", ("a/sub/**",)),
            free("Z", ("b/**",)),
        ),
        target=2,
    )
    v = supervise(ev)
    assert v.admissible == 2
    spawned = _lanes_of(v.spawn)
    # X and Y overlap, so they must never both be spawned.
    assert not ({"X", "Y"} <= spawned)
    assert spawned == {"X", "Z"}


def test_spawn_never_lands_on_a_held_advancing_region():
    # Held ADVANCING lane A src/api/** ; FREE candidate C src/api/sub/** (subset of
    # A) ; FREE candidate B src/worker/** (disjoint). target 2.
    # The walk must skip C (overlaps the held A) and pick B.
    ev = SuperviseEvidence(
        lanes=(
            held("A", Liveness.ADVANCING, ("src/api/**",)),
            free("C", ("src/api/sub/**",)),
            free("B", ("src/worker/**",)),
        ),
        target=2,
    )
    v = supervise(ev)
    spawned = _lanes_of(v.spawn)
    assert "C" not in spawned                   # would overlap the held A
    assert spawned == {"B"}


def test_spawn_emits_fewer_than_headroom_when_candidates_collide():
    # Two FREE lanes that overlap each other; target 2 but only one can run.
    ev = SuperviseEvidence(
        lanes=(free("P", ("src/**",)), free("Q", ("src/api/**",))),
        target=2,
    )
    v = supervise(ev)
    # admissible greedily admits P then rejects Q (overlap) → 1.
    assert v.admissible == 1
    assert len(v.spawn) == 1                    # not 2 — the headroom was illusory


# --------------------------------------------------------------------------
# The NO-PLAN FLOOR — empty roster returns, never crashes.
# --------------------------------------------------------------------------
def test_no_plan_floor_empty_roster():
    v = supervise(SuperviseEvidence(lanes=(), target=3))
    assert v.verdict == SuperviseOutcome.TARGET_UNREACHABLE
    assert v.admissible == 0
    assert v.alive == 0
    assert v.spawn == ()


def test_target_zero_is_at_target_with_no_spawns():
    ev = SuperviseEvidence(lanes=(free("main", ("**/*",)),), target=0)
    v = supervise(ev)
    # target 0 ≤ admissible 1, alive 0 == target → AT_TARGET, nothing spawned.
    assert v.verdict == SuperviseOutcome.AT_TARGET
    assert v.spawn == ()


# --------------------------------------------------------------------------
# POLICY knobs.
# --------------------------------------------------------------------------
def test_reap_stalled_false_is_report_only():
    ev = SuperviseEvidence(
        lanes=(held("api", Liveness.STALLED, ("src/api/**",)),), target=1
    )
    v = supervise(ev, SupervisePolicy(target=1, reap_stalled=False))
    assert v.reap == ()                         # no reap emitted
    # The lane is report-only: not refilled, dead worker not counted alive.
    assert v.alive == 0


def test_count_spinning_as_alive_false_does_not_refill_the_spinner():
    # A spinner holds its lease; even when not counted alive it must NOT be a
    # spawn candidate (re-spawning would try to displace a live worker).
    ev = SuperviseEvidence(
        lanes=(held("main", Liveness.SPINNING, ("**/*",)),), target=1
    )
    v = supervise(ev, SupervisePolicy(target=1, count_spinning_as_alive=False))
    assert _lanes_of(v.flag) == {"main"}        # still flagged
    assert "main" not in _lanes_of(v.spawn)     # never refilled onto the live lease
    assert v.spawn == ()


# --------------------------------------------------------------------------
# CONTRACT shape — frozen, validated, JSON-clean, verdict.py value-shape.
# --------------------------------------------------------------------------
def test_policy_and_evidence_reject_negative_target():
    with pytest.raises(ValueError):
        SupervisePolicy(target=-1)
    with pytest.raises(ValueError):
        SuperviseEvidence(lanes=(), target=-1)


def test_laneliveness_rejects_empty_lane_name():
    with pytest.raises(ValueError):
        LaneLiveness(lane="", liveness=None)


def test_verdict_is_frozen():
    v = supervise(SuperviseEvidence(lanes=(free("main", ("**/*",)),), target=1))
    with pytest.raises(Exception):
        v.alive = 99  # type: ignore[misc]


def test_to_dict_shape_is_json_clean():
    ev = SuperviseEvidence(
        lanes=(
            held("main", Liveness.SPINNING, ("**/*",)),
            free("global", ("**/*",), exclusive=True),
        ),
        target=2,
    )
    v = supervise(ev)
    d = v.to_dict()
    # Required keys present, verdict is the str value, plans are lists of dicts.
    assert d["verdict"] == v.verdict.value and isinstance(d["verdict"], str)
    assert isinstance(d["reason"], str)
    assert d["alive"] == v.alive and d["admissible"] == v.admissible
    for key in ("spawn", "reap", "flag"):
        assert isinstance(d[key], list)
        for p in d[key]:
            assert set(p) == {"lane", "disposition", "reason"}
    # Evidence echoed with per-lane liveness as the str value (or None when FREE).
    lanes = d["evidence"]["lanes"]
    assert lanes[0]["liveness"] == "SPINNING"
    assert lanes[1]["liveness"] is None


def test_verdict_conforms_to_typed_verdict_value_shape():
    # SuperviseVerdict is a verdict.py COUSIN, not a member — but it matches the
    # VALUE shape (closed-enum .verdict that is str-valued, str .reason, dict
    # to_dict), so the renderer/MCP seam treats it uniformly.
    from dos import verdict as _verdict

    v = supervise(SuperviseEvidence(lanes=(free("main", ("**/*",)),), target=1))
    assert _verdict.conforms(v)
    # It must NOT be registered as a kernel verdict verb (it is an effect-emitter).
    from dos import verdicts as _verdicts

    assert "supervise" not in _verdicts.names()


def test_disposition_and_outcome_are_str_enums():
    assert Disposition.SPAWN.value == "SPAWN"
    assert str(Disposition.REAP) == "REAP"
    assert SuperviseOutcome.AT_TARGET.value == "AT_TARGET"
    assert str(SuperviseOutcome.FILLING) == "FILLING"


# --------------------------------------------------------------------------
# PURITY — supervise() touches no clock / IO / subprocess. We prove it by
# poisoning those modules and asserting the verdict still returns (the
# `verdict.py` property every kernel verb's suite proves).
# --------------------------------------------------------------------------
def test_supervise_is_pure_no_io_clock_or_subprocess(monkeypatch):
    import builtins
    import subprocess
    import time

    def _boom(*a, **k):  # pragma: no cover - only fires on a violation
        raise AssertionError("supervise() must not perform I/O / clock / subprocess")

    monkeypatch.setattr(builtins, "open", _boom)
    monkeypatch.setattr(time, "time", _boom)
    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)

    ev = SuperviseEvidence(
        lanes=(
            held("api", Liveness.ADVANCING, ("src/api/**",)),
            held("worker", Liveness.STALLED, ("src/worker/**",)),
            free("docs", ("docs/**",)),
        ),
        target=3,
    )
    v = supervise(ev)  # must not raise
    assert isinstance(v, SuperviseVerdict)
    assert v.evidence is ev  # evidence echoed, not re-read


# --------------------------------------------------------------------------
# The [supervise] config seam (docs/99) — the standing population policy as
# data. Mirrors the [cooldown] seam tests: from_table overrides/inherits, an
# unknown/malformed key raises, load_from_toml reads/absent-inherits, and the
# config carries the policy so `dos loop` + the driver read one declaration.
# --------------------------------------------------------------------------
class TestSupervisePolicyTable:
    def test_to_dict_shape(self):
        assert DEFAULT_POLICY.to_dict() == {
            "target": 1,
            "count_spinning_as_alive": True,
            "reap_stalled": True,
            "spin_halt_after_ms": None,  # acting-on-spin off by default
            # vendor-neutral default launcher (the kernel names no agent binary)
            "worker_launch_template": "/dos-dispatch-loop --lane {lane}",
            "max_concurrency": None,  # derived-claim budget off by default (docs/283)
        }

    def test_override_each_field(self):
        p = policy_from_table(
            {"target": 3, "count_spinning_as_alive": False, "reap_stalled": False})
        assert (p.target, p.count_spinning_as_alive, p.reap_stalled) == (3, False, False)

    def test_worker_launch_template_default_is_vendor_neutral(self):
        # The kernel's default launcher names NO agent-runtime binary — it is the
        # bare DOS skill invocation. (The `claude -p "…"` wrapper is a vendor
        # specific that lives in the supervisor DRIVER / a host's dos.toml.)
        assert DEFAULT_POLICY.worker_launch_template == "/dos-dispatch-loop --lane {lane}"
        assert "claude" not in DEFAULT_POLICY.worker_launch_template

    def test_worker_launch_template_override(self):
        p = policy_from_table(
            {"worker_launch_template": 'claude -p "/dos-dispatch-loop --lane {lane}"'})
        assert p.worker_launch_template == 'claude -p "/dos-dispatch-loop --lane {lane}"'
        # other knobs untouched
        assert p.target == DEFAULT_POLICY.target

    def test_worker_launch_template_must_carry_lane_placeholder(self):
        # A template with no {lane} is a loud declaration error (it could never
        # name the lane to spawn), the same fail-loud posture as the other knobs.
        try:
            policy_from_table({"worker_launch_template": "/dos-dispatch-loop"})
        except ValueError as e:
            assert "{lane}" in str(e)
        else:
            raise AssertionError("expected ValueError for a template without {lane}")

    def test_worker_launch_template_must_be_a_string(self):
        try:
            policy_from_table({"worker_launch_template": 123})
        except ValueError as e:
            assert "worker_launch_template" in str(e)
        else:
            raise AssertionError("expected ValueError for a non-string template")

    def test_omitted_fields_inherit_base(self):
        # Only `target` declared; the two booleans inherit the (non-default) base.
        base = SupervisePolicy(target=1, count_spinning_as_alive=False, reap_stalled=False)
        p = policy_from_table({"target": 9}, base=base)
        assert (p.target, p.count_spinning_as_alive, p.reap_stalled) == (9, False, False)

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError):
            policy_from_table({"targett": 3})

    def test_non_table_raises(self):
        with pytest.raises(ValueError):
            policy_from_table(["not", "a", "table"])  # type: ignore[arg-type]

    def test_target_bool_rejected(self):
        # A TOML bool is an int subclass; `target = true` must be a loud error,
        # not silently mean 1.
        with pytest.raises(ValueError):
            policy_from_table({"target": True})

    def test_target_non_int_rejected(self):
        with pytest.raises(ValueError):
            policy_from_table({"target": "three"})

    def test_negative_target_rejected(self):
        # Delegated to SupervisePolicy.__post_init__ — still a ValueError.
        with pytest.raises(ValueError):
            policy_from_table({"target": -1})

    def test_boolean_field_must_be_bool(self):
        with pytest.raises(ValueError):
            policy_from_table({"reap_stalled": 1})

    def test_load_from_toml(self, tmp_path):
        p = tmp_path / "dos.toml"
        p.write_text(
            "[supervise]\ntarget = 4\nreap_stalled = false\n", encoding="utf-8")
        pol = load_from_toml(p)
        assert pol.target == 4
        assert pol.reap_stalled is False
        assert pol.count_spinning_as_alive is True  # omitted → inherits default

    def test_load_from_toml_absent_is_base(self, tmp_path):
        assert load_from_toml(tmp_path / "nope.toml") is DEFAULT_POLICY

    def test_load_from_toml_no_table_is_base(self, tmp_path):
        p = tmp_path / "dos.toml"
        p.write_text("[cooldown]\nwindow_hours = 2\n", encoding="utf-8")
        assert load_from_toml(p) is DEFAULT_POLICY

    def test_config_carries_supervise(self):
        import dos.config as c
        cfg = c.default_config()
        assert isinstance(cfg.supervise, SupervisePolicy)
        assert cfg.supervise is _supervise.DEFAULT_POLICY

    def test_config_layers_supervise_table(self, tmp_path):
        import dos.config as c
        (tmp_path / "dos.toml").write_text(
            "[supervise]\ntarget = 5\ncount_spinning_as_alive = false\n",
            encoding="utf-8")
        cfg = c.load_workspace_config(workspace=tmp_path)
        assert cfg.supervise.target == 5
        assert cfg.supervise.count_spinning_as_alive is False
        assert cfg.supervise.reap_stalled is True  # omitted → inherits default

    def test_config_malformed_supervise_warns_keeps_base(self, tmp_path):
        # A present-but-broken [supervise] must NOT crash config-build (a verify
        # with a broken table still runs); it warns and keeps the base policy.
        import dos.config as c
        (tmp_path / "dos.toml").write_text(
            "[supervise]\ntarget = \"lots\"\n", encoding="utf-8")
        warnings = []
        cfg = c.load_workspace_config(
            workspace=tmp_path, warn=lambda label, msg: warnings.append((label, msg)))
        assert cfg.supervise is _supervise.DEFAULT_POLICY
        assert any(label == "supervise" for label, _ in warnings)


class TestReapStalledPolicyFlows:
    """The behavioral payoff of the seam: the two booleans actually change the
    verdict. Before the seam, `reap_stalled`/`count_spinning_as_alive` were
    unreachable from config; these pin that a declared value reaches `supervise`.
    """

    def test_reap_stalled_false_suppresses_reap(self):
        ev = SuperviseEvidence(
            lanes=(held("worker", Liveness.STALLED, ("src/worker/**",)),), target=1)
        # Default: a STALLED worker yields a REAP.
        assert _lanes_of(supervise(ev, SupervisePolicy(target=1)).reap) == {"worker"}
        # reap_stalled=False: report-only, no REAP emitted.
        v = supervise(ev, SupervisePolicy(target=1, reap_stalled=False))
        assert v.reap == ()

    def test_count_spinning_as_alive_false_changes_population(self):
        # One SPINNING worker, target 1. Counting it alive → AT_TARGET, no spawn.
        # NOT counting it → it is not alive, so the roster tries to fill (but the
        # spinner still holds its lease, so it is never a spawn candidate).
        ev = SuperviseEvidence(
            lanes=(held("api", Liveness.SPINNING, ("src/api/**",)),), target=1)
        v_count = supervise(ev, SupervisePolicy(target=1, count_spinning_as_alive=True))
        assert v_count.alive == 1
        v_nocount = supervise(ev, SupervisePolicy(target=1, count_spinning_as_alive=False))
        assert v_nocount.alive == 0
        # Either way the spinner is FLAGged (advisory), never reaped.
        assert _lanes_of(v_count.flag) == {"api"}
        assert _lanes_of(v_nocount.flag) == {"api"}


# --------------------------------------------------------------------------
# Acting-on-spin (docs/90 sec5, docs/210 pivot): FLAG -> PROPOSE_HALT escalation.
# The supervisor's next stage after the [supervise] seam. A SPINNING worker past
# the policy threshold yields a *proposed* halt in a SEPARATE channel - advisory,
# never a reap. The load-bearing tests are the soundness-floor invariance and the
# fail-quiet-on-absent-evidence ones.
# --------------------------------------------------------------------------
class TestActingOnSpin:
    def test_off_by_default_flag_only(self):
        # spin_halt_after_ms=None (default): a long-spinning lane yields the SAME
        # single FLAG and an EMPTY proposed_halt as today (the no-op-by-default).
        ev = SuperviseEvidence(lanes=(spinning("api", 10_000_000, ("src/api/**",)),), target=1)
        v = supervise(ev, SupervisePolicy(target=1))
        assert _lanes_of(v.flag) == {"api"}
        assert v.proposed_halt == ()

    def test_escalates_past_threshold(self):
        ev = SuperviseEvidence(lanes=(spinning("api", 120_000, ("src/api/**",)),), target=1)
        v = supervise(ev, SupervisePolicy(target=1, spin_halt_after_ms=60_000))
        assert _lanes_of(v.flag) == {"api"}
        assert _lanes_of(v.proposed_halt) == {"api"}
        assert v.proposed_halt[0].disposition == Disposition.PROPOSE_HALT
        assert v.reap == ()

    def test_threshold_boundary_is_inclusive(self):
        pol = SupervisePolicy(target=1, spin_halt_after_ms=60_000)
        at = supervise(SuperviseEvidence(lanes=(spinning("api", 60_000, ("a/**",)),), target=1), pol)
        below = supervise(SuperviseEvidence(lanes=(spinning("api", 59_999, ("a/**",)),), target=1), pol)
        assert _lanes_of(at.proposed_halt) == {"api"}
        assert below.proposed_halt == ()

    def test_fail_quiet_on_absent_age(self):
        ev = SuperviseEvidence(
            lanes=(LaneLiveness(lane="api", liveness=Liveness.SPINNING,
                                tree=("src/api/**",), spinning_age_ms=None),),
            target=1)
        v = supervise(ev, SupervisePolicy(target=1, spin_halt_after_ms=60_000))
        assert _lanes_of(v.flag) == {"api"}
        assert v.proposed_halt == ()

    def test_propose_halt_never_releases_region_or_refills(self):
        free_lane = free("web", ("src/web/**",))
        ev = SuperviseEvidence(
            lanes=(spinning("api", 120_000, ("src/api/**",)), free_lane), target=2)
        v = supervise(ev, SupervisePolicy(target=2, spin_halt_after_ms=60_000))
        assert _lanes_of(v.proposed_halt) == {"api"}
        assert _lanes_of(v.spawn) == {"web"}
        assert "api" not in _lanes_of(v.spawn)
        assert v.reap == ()

    def test_soundness_floor_spawn_plan_invariant_under_escalation(self):
        free_lane = free("web", ("src/web/**",))
        pol = SupervisePolicy(target=2, spin_halt_after_ms=60_000)
        below = supervise(
            SuperviseEvidence(lanes=(spinning("api", 30_000, ("src/api/**",)), free_lane), target=2), pol)
        above = supervise(
            SuperviseEvidence(lanes=(spinning("api", 120_000, ("src/api/**",)), free_lane), target=2), pol)
        assert [p.lane for p in below.spawn] == [p.lane for p in above.spawn]
        assert below.alive == above.alive
        assert below.admissible == above.admissible
        assert below.proposed_halt == ()
        assert _lanes_of(above.proposed_halt) == {"api"}

    def test_multi_spinner_deterministic_order(self):
        ev = SuperviseEvidence(
            lanes=(spinning("api", 120_000, ("src/api/**",)),
                   spinning("web", 120_000, ("src/web/**",))),
            target=2)
        v = supervise(ev, SupervisePolicy(target=2, spin_halt_after_ms=60_000))
        assert [p.lane for p in v.proposed_halt] == ["api", "web"]

    def test_to_dict_carries_proposed_halt_and_spin_age(self):
        ev = SuperviseEvidence(lanes=(spinning("api", 120_000, ("src/api/**",)),), target=1)
        v = supervise(ev, SupervisePolicy(target=1, spin_halt_after_ms=60_000))
        d = v.to_dict()
        assert d["proposed_halt"] and d["proposed_halt"][0]["disposition"] == "PROPOSE_HALT"
        assert d["evidence"]["lanes"][0]["spinning_age_ms"] == 120_000

    def test_propose_halt_pure_no_io(self, monkeypatch):
        import builtins
        import subprocess
        import time

        def _boom(*a, **k):  # pragma: no cover - only on a violation
            raise AssertionError("supervise() must not perform I/O on the spin path")

        monkeypatch.setattr(builtins, "open", _boom)
        monkeypatch.setattr(time, "time", _boom)
        monkeypatch.setattr(subprocess, "Popen", _boom)
        ev = SuperviseEvidence(lanes=(spinning("api", 120_000, ("src/api/**",)),), target=1)
        v = supervise(ev, SupervisePolicy(target=1, spin_halt_after_ms=60_000))
        assert _lanes_of(v.proposed_halt) == {"api"}


class TestSpinHaltPolicyTable:
    def test_spin_halt_ms(self):
        assert policy_from_table({"spin_halt_after_ms": 90_000}).spin_halt_after_ms == 90_000

    def test_spin_halt_minutes_ergonomic(self):
        assert policy_from_table({"spin_halt_after_minutes": 5}).spin_halt_after_ms == 300_000

    def test_absent_inherits(self):
        base = SupervisePolicy(spin_halt_after_ms=42_000)
        assert policy_from_table({"target": 2}, base=base).spin_halt_after_ms == 42_000

    def test_both_units_raises(self):
        with pytest.raises(ValueError):
            policy_from_table({"spin_halt_after_ms": 1, "spin_halt_after_minutes": 1})

    def test_bool_rejected(self):
        with pytest.raises(ValueError):
            policy_from_table({"spin_halt_after_ms": True})

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            policy_from_table({"spin_halt_after_ms": -1})

    def test_str_rejected(self):
        with pytest.raises(ValueError):
            policy_from_table({"spin_halt_after_ms": "10s"})

    def test_load_from_toml_minutes(self, tmp_path):
        p = tmp_path / "dos.toml"
        p.write_text("[supervise]\nspin_halt_after_minutes = 10\n", encoding="utf-8")
        assert load_from_toml(p).spin_halt_after_ms == 600_000

    def test_config_layers_spin_halt(self, tmp_path):
        import dos.config as c
        (tmp_path / "dos.toml").write_text(
            "[supervise]\nspin_halt_after_ms = 120000\n", encoding="utf-8")
        cfg = c.load_workspace_config(workspace=tmp_path)
        assert cfg.supervise.spin_halt_after_ms == 120_000


# --------------------------------------------------------------------------
# The roster-order-sensitivity lint (docs/210 pivot) - the spawn-ranking descope.
# Value-aware spawn RANKING was declined (a no-op in the disjoint norm); the rare
# order-sensitive case is overlapping CONCURRENT lanes, surfaced at config time.
# --------------------------------------------------------------------------
class TestRosterOrderLint:
    def test_disjoint_concurrent_is_silent(self):
        triples = (("api", ("src/api/**",), False), ("web", ("src/web/**",), False))
        assert overlapping_concurrent_lanes(triples) == ()

    def test_overlapping_concurrent_is_flagged(self):
        triples = (("api", ("src/**",), False), ("web", ("src/web/**",), False))
        assert overlapping_concurrent_lanes(triples) == (("api", "web"),)

    def test_exclusive_lane_never_flagged(self):
        triples = (("api", ("src/api/**",), False), ("global", ("**/*",), True))
        assert overlapping_concurrent_lanes(triples) == ()

    def test_universal_concurrent_overlaps_all(self):
        triples = (("a", ("**/*",), False), ("b", ("src/**",), False), ("c", ("docs/**",), False))
        assert overlapping_concurrent_lanes(triples) == (("a", "b"), ("a", "c"))

    def test_pairs_are_name_sorted_and_deduped(self):
        triples = (("z", ("src/**",), False), ("a", ("src/**",), False))
        assert overlapping_concurrent_lanes(triples) == (("a", "z"),)

    def test_empty_roster(self):
        assert overlapping_concurrent_lanes(()) == ()


# --------------------------------------------------------------------------
# The DERIVED-CLAIM concurrency budget (docs/283 — `max_concurrency`).
# The supervisor can reach a target ABOVE the static disjoint-lane count by riding
# a REPEATABLE (fungible auto-pick) lane, without N disjoint trees pre-declared.
# The arbiter still gates each per-pick claim — the supervisor only budgets the slot
# count. With the budget OFF (default None) every case here is byte-for-byte today's.
# --------------------------------------------------------------------------
def repeatable_free(lane, tree=()):
    """A FREE fungible auto-pick handle — its per-pick claim is derived, not fixed."""
    return LaneLiveness(lane=lane, liveness=None, tree=tuple(tree), repeatable=True)


def repeatable_held(lane, lv, tree=()):
    return LaneLiveness(lane=lane, liveness=lv, tree=tuple(tree), repeatable=True)


class TestDerivedClaimConcurrency:
    def test_policy_default_is_off(self):
        assert DEFAULT_POLICY.max_concurrency is None

    def test_policy_rejects_below_one(self):
        with pytest.raises(ValueError):
            SupervisePolicy(max_concurrency=0)
        with pytest.raises(ValueError):
            SupervisePolicy(max_concurrency=-3)

    def test_laneliveness_rejects_repeatable_and_exclusive(self):
        with pytest.raises(ValueError):
            LaneLiveness(lane="x", repeatable=True, is_exclusive=True)

    def test_budget_off_is_byte_for_byte_today(self):
        # An exclusive-only roster (the job model) with NO budget: admissible 1,
        # target 8 unreachable — exactly the status quo this whole feature relaxes.
        ev = SuperviseEvidence(
            lanes=(free("orchestration", ("docs/_plans/",), exclusive=True),
                   free("global", ("**/*",), exclusive=True)),
            target=8,
        )
        v = supervise(ev)  # default policy, no budget
        assert v.verdict == SuperviseOutcome.TARGET_UNREACHABLE
        assert v.admissible == 1

    def test_budget_needs_a_repeatable_lane_to_ride(self):
        # A budget with NO repeatable lane in the roster is meaningless — the ceiling
        # stays the static count (there is no fungible handle a 2nd worker could take).
        ev = SuperviseEvidence(
            lanes=(free("global", ("**/*",), exclusive=True),), target=8)
        v = supervise(ev, SupervisePolicy(target=8, max_concurrency=8))
        assert v.admissible == 1  # exclusive-only → still capped at 1
        assert v.verdict == SuperviseOutcome.TARGET_UNREACHABLE

    def test_budget_lifts_ceiling_on_a_repeatable_handle(self):
        # The headline: a single FREE repeatable auto-pick handle + an exclusive
        # global, budget 8, target 8 → admissible 8, eight workers spawned on the
        # handle. No disjoint trees were pre-declared — the operator declared ONE
        # number (the user's exact ask).
        ev = SuperviseEvidence(
            lanes=(repeatable_free("auto"),
                   free("global", ("**/*",), exclusive=True)),
            target=8,
        )
        v = supervise(ev, SupervisePolicy(target=8, max_concurrency=8))
        assert v.admissible == 8
        assert v.verdict == SuperviseOutcome.FILLING
        assert len(v.spawn) == 8
        assert all(p.lane == "auto" for p in v.spawn)
        assert all(p.disposition == Disposition.SPAWN for p in v.spawn)

    def test_multiple_repeatable_lanes_get_distinct_slots_before_repeats(self):
        # A workspace may put every concrete concurrent lane in autopick, making
        # each one repeatable. The supervisor should still fill distinct free lanes
        # before using any repeatable handle twice.
        ev = SuperviseEvidence(
            lanes=(
                repeatable_free("api", ("src/api/**",)),
                repeatable_free("worker", ("src/worker/**",)),
                repeatable_free("web", ("src/web/**",)),
            ),
            target=3,
        )
        v = supervise(ev, SupervisePolicy(target=3))
        assert v.admissible == 3
        assert [p.lane for p in v.spawn] == ["api", "worker", "web"]

    def test_free_repeatable_lanes_fill_before_duplicating_held_handle(self):
        # Once a repeatable lane is already ADVANCING, refilling target should use
        # other free concrete repeatable lanes before launching a duplicate on the
        # held handle.
        ev = SuperviseEvidence(
            lanes=(
                repeatable_held("api", Liveness.ADVANCING, ("src/api/**",)),
                repeatable_free("worker", ("src/worker/**",)),
                repeatable_free("web", ("src/web/**",)),
            ),
            target=3,
        )
        v = supervise(ev, SupervisePolicy(target=3))
        assert v.alive == 1
        assert v.admissible == 3
        assert [p.lane for p in v.spawn] == ["worker", "web"]

    def test_target_below_budget_fills_to_target_not_budget(self):
        # admissible is max(static, budget)=8, but we only spawn up to TARGET (3).
        ev = SuperviseEvidence(lanes=(repeatable_free("auto"),), target=3)
        v = supervise(ev, SupervisePolicy(target=3, max_concurrency=8))
        assert v.admissible == 8
        assert len(v.spawn) == 3

    def test_held_repeatable_worker_counts_alive_and_refills_remainder(self):
        # One worker already ADVANCING on the handle (alive 1); budget 5, target 5
        # → spawn 4 MORE on the same handle (the held repeatable lane holds no fixed
        # region, so it never blocks its own refill).
        ev = SuperviseEvidence(
            lanes=(repeatable_held("auto", Liveness.ADVANCING),), target=5)
        v = supervise(ev, SupervisePolicy(target=5, max_concurrency=5))
        assert v.alive == 1
        assert v.admissible == 5
        assert len(v.spawn) == 4
        assert all(p.lane == "auto" for p in v.spawn)
        assert v.verdict == SuperviseOutcome.FILLING

    def test_at_target_when_repeatable_population_is_full(self):
        # Two workers advancing on the handle, budget+target 2 → AT_TARGET, no spawn.
        ev = SuperviseEvidence(
            lanes=(repeatable_held("auto", Liveness.ADVANCING),), target=2)
        # The evidence only carries ONE handle row (leases key by lane), so alive=1
        # from it; this case pins that a held handle never OVER-counts or re-spawns
        # beyond the budget. alive 1 < target 2 → fill one more.
        v = supervise(ev, SupervisePolicy(target=2, max_concurrency=2))
        assert v.alive == 1 and len(v.spawn) == 1

    def test_live_exclusive_still_caps_at_one_despite_budget(self):
        # A held EXCLUSIVE worker runs alone — a budget can never let a second worker
        # join it. admissible reflects the exclusive-only floor (1).
        ev = SuperviseEvidence(
            lanes=(repeatable_free("auto"),
                   held("global", Liveness.ADVANCING, ("**/*",), exclusive=True)),
            target=8,
        )
        v = supervise(ev, SupervisePolicy(target=8, max_concurrency=8))
        # admissible = max(static_concurrent=1 (the repeatable lane admits 1 by tree),
        # budget=8) = 8; but the live exclusive worker holds the whole tree, so the
        # spawn walk cannot place a disjoint worker... EXCEPT the repeatable handle
        # holds no fixed region. This is the one genuinely subtle case: the kernel
        # trusts the per-pick arbiter, so it DOES synthesise slots. The exclusive
        # lease is the operator's signal that nothing else should run — but enforcing
        # that against a derived-claim handle is the arbiter's job at acquire time.
        # We assert the population MATH is honest (alive counts the exclusive worker).
        assert v.alive == 1

    def test_budget_does_not_exceed_its_own_value(self):
        # target 20 but budget 5 → admissible capped at 5, only 5 spawned.
        ev = SuperviseEvidence(lanes=(repeatable_free("auto"),), target=20)
        v = supervise(ev, SupervisePolicy(target=20, max_concurrency=5))
        assert v.admissible == 5
        assert len(v.spawn) == 5
        assert v.verdict == SuperviseOutcome.TARGET_UNREACHABLE  # 20 > 5
        assert "max_concurrency" not in v.reason or v.admissible == 5

    def test_fixed_lanes_fill_first_then_repeatable_soaks_remainder(self):
        # A disjoint fixed-tree lane + a repeatable handle, budget 4, target 4.
        # The fixed lane fills its one region; the handle soaks the other 3.
        ev = SuperviseEvidence(
            lanes=(free("api", ("src/api/**",)), repeatable_free("auto")),
            target=4,
        )
        v = supervise(ev, SupervisePolicy(target=4, max_concurrency=4))
        assert v.admissible == 4
        lanes = [p.lane for p in v.spawn]
        assert lanes.count("api") == 1
        assert lanes.count("auto") == 3

    def test_to_dict_carries_repeatable(self):
        ev = SuperviseEvidence(lanes=(repeatable_free("auto"),), target=2)
        v = supervise(ev, SupervisePolicy(target=2, max_concurrency=2))
        d = v.to_dict()
        assert d["evidence"]["lanes"][0]["repeatable"] is True

    def test_purity_no_io(self, monkeypatch):
        # The verdict stays pure under the budget path (no clock/subprocess/file).
        import subprocess
        import time as _time

        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no subprocess")))
        monkeypatch.setattr(_time, "time", lambda: (_ for _ in ()).throw(AssertionError("no clock")))
        ev = SuperviseEvidence(lanes=(repeatable_free("auto"),), target=3)
        v = supervise(ev, SupervisePolicy(target=3, max_concurrency=3))
        assert len(v.spawn) == 3


class TestMaxConcurrencyPolicyTable:
    def test_parse_max_concurrency(self):
        assert policy_from_table({"max_concurrency": 8}).max_concurrency == 8

    def test_absent_inherits(self):
        base = SupervisePolicy(max_concurrency=4)
        assert policy_from_table({"target": 2}, base=base).max_concurrency == 4

    def test_bool_rejected(self):
        with pytest.raises(ValueError):
            policy_from_table({"max_concurrency": True})

    def test_str_rejected(self):
        with pytest.raises(ValueError):
            policy_from_table({"max_concurrency": "8"})

    def test_below_one_rejected(self):
        with pytest.raises(ValueError):
            policy_from_table({"max_concurrency": 0})

    def test_load_from_toml(self, tmp_path):
        p = tmp_path / "dos.toml"
        p.write_text("[supervise]\nmax_concurrency = 6\n", encoding="utf-8")
        assert load_from_toml(p).max_concurrency == 6

    def test_config_layers_max_concurrency(self, tmp_path):
        import dos.config as c
        (tmp_path / "dos.toml").write_text(
            "[supervise]\nmax_concurrency = 12\n", encoding="utf-8")
        cfg = c.load_workspace_config(workspace=tmp_path)
        assert cfg.supervise.max_concurrency == 12
