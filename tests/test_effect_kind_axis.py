"""#202 / docs/371 — the typed NON-FILE blast-radius axis.

Today admission models one blast radius: the file tree a call touches. The
no-footprint orchestration tools (Agent, Task, TaskCreate, TaskUpdate, ToolSearch)
write no file, so on the FILE axis they correctly pass the lane-collision check
clean (issue #46). But "writes no file" is not "touches nothing": Agent forks the
fleet, Task* mutate the shared work queue, ToolSearch mutates the capability set.

This pins the second axis:
  - the pure kernel TYPE (`EffectKind` + `classify_spawn_pressure` + the per-holder
    fold) — domain-free, names no tool;
  - the host-shaped adapter (`pretool_sensor.effect_from_event`, the CC tool map);
  - the DONE CONDITION (#202): an Agent fan-out from ONE holder is VISIBLE on the
    new axis while the file-collision check still passes CLEAN.
"""

from __future__ import annotations

import dataclasses
import tempfile
from pathlib import Path

import pytest

from dos import config as _config
from dos import pretool_sensor as prt
from dos.effect_kind import (
    EffectKind,
    SpawnPressure,
    SpawnPressurePolicy,
    classify_spawn_pressure,
    count_spawns_per_holder,
    load_from_toml,
    policy_from_table,
    spawn_storm_holders,
    GENERIC_SPAWN_PRESSURE_POLICY,
)


# ---------------------------------------------------------------------------
# The pure kernel TYPE — a leaf with no host name.
# ---------------------------------------------------------------------------
def test_effect_kind_is_non_file_predicate():
    assert EffectKind.NONE.is_non_file is False
    assert EffectKind.SPAWN.is_non_file is True
    assert EffectKind.COORDINATION.is_non_file is True
    assert EffectKind.CAPABILITY.is_non_file is True


def test_spawn_pressure_bands():
    pol = SpawnPressurePolicy(elevated_at=8, storm_at=25)
    assert classify_spawn_pressure(0, pol).pressure is SpawnPressure.CLEAR
    assert classify_spawn_pressure(7, pol).pressure is SpawnPressure.CLEAR
    assert classify_spawn_pressure(8, pol).pressure is SpawnPressure.ELEVATED
    assert classify_spawn_pressure(24, pol).pressure is SpawnPressure.ELEVATED
    assert classify_spawn_pressure(25, pol).pressure is SpawnPressure.STORM
    # The observed 140-from-one-holder fan-out is unambiguously a STORM.
    v = classify_spawn_pressure(140, pol)
    assert v.pressure is SpawnPressure.STORM
    assert v.surfaceable is True
    assert "140" in v.reason()


def test_spawn_pressure_clamps_negative():
    assert classify_spawn_pressure(-5).live_count == 0


def test_spawn_pressure_policy_validates():
    with pytest.raises(ValueError):
        SpawnPressurePolicy(elevated_at=0, storm_at=10)
    with pytest.raises(ValueError):
        SpawnPressurePolicy(elevated_at=10, storm_at=5)
    with pytest.raises(ValueError):
        SpawnPressurePolicy(elevated_at=2, storm_at=10, window_seconds=0)


def test_spawn_pressure_policy_from_table_overrides_threshold_and_window():
    p = policy_from_table({"storm_at": 4, "window_hours": 12})
    assert p.elevated_at == 4
    assert p.storm_at == 4
    assert p.window_seconds == 12 * 60 * 60


def test_spawn_pressure_load_from_toml(tmp_path):
    toml = tmp_path / "dos.toml"
    toml.write_text("[spawn_pressure]\nstorm_at = 9\nwindow_seconds = 30\n",
                    encoding="utf-8")
    p = load_from_toml(toml)
    assert p.storm_at == 9
    assert p.window_seconds == 30


def test_workspace_config_reads_spawn_pressure_table(tmp_path):
    toml = tmp_path / "dos.toml"
    toml.write_text("[spawn_pressure]\nstorm_at = 9\nwindow_seconds = 30\n",
                    encoding="utf-8")
    base = _config.default_config(tmp_path)
    cfg = _config.load_workspace_config(tmp_path, base=base)
    assert cfg.spawn_pressure.storm_at == 9
    assert cfg.spawn_pressure.window_seconds == 30


def test_count_spawns_per_holder_counts_only_spawns():
    records = [
        ("holderA", "spawn"),
        ("holderA", "spawn"),
        ("holderA", "coordination"),  # not a spawn → not counted
        ("holderB", "spawn"),
        ("", "spawn"),                 # unattributable → dropped
        ("holderA", "capability"),     # not a spawn
    ]
    counts = count_spawns_per_holder(records)
    assert counts == {"holderA": 2, "holderB": 1}


def test_spawn_storm_holders_surfaces_only_the_wide_fanout():
    # holderA forks 30 (a STORM), holderB forks 2 (CLEAR — omitted).
    records = [("holderA", "spawn")] * 30 + [("holderB", "spawn")] * 2
    storms = spawn_storm_holders(records)
    assert set(storms) == {"holderA"}
    assert storms["holderA"].pressure is SpawnPressure.STORM


# ---------------------------------------------------------------------------
# The host-shaped adapter — the CC tool → EffectKind mapping.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tool,expected", [
    ("Agent", EffectKind.SPAWN),
    ("Task", EffectKind.COORDINATION),
    ("TaskCreate", EffectKind.COORDINATION),
    ("TaskUpdate", EffectKind.COORDINATION),
    ("ToolSearch", EffectKind.CAPABILITY),
    ("Read", EffectKind.NONE),
    ("Write", EffectKind.NONE),
    ("Bash", EffectKind.NONE),
])
def test_effect_from_event_maps_cc_tools(tool, expected):
    assert prt.effect_from_event({"tool_name": tool}) is expected


def test_effect_from_event_is_fail_soft():
    assert prt.effect_from_event({}) is EffectKind.NONE
    assert prt.effect_from_event({"tool_name": 123}) is EffectKind.NONE


# ---------------------------------------------------------------------------
# The DONE CONDITION — an Agent SPAWN is visible on the new axis while the
# file-collision check still passes CLEAN (no false WARN, no verdict change).
# ---------------------------------------------------------------------------
def _kernel_cfg(tmp_path: Path):
    cfg = _config.default_config(tmp_path)
    facts = _config.WorkspaceFacts(
        root=tmp_path,
        kernel_runtime_files=("src/dos/arbiter.py",),
        is_kernel_repo=True,
    )
    return dataclasses.replace(cfg, workspace=facts)


def _event(tool_name, tool_input=None, *, cwd=None):
    e = {"tool_name": tool_name, "session_id": "S1",
         "tool_input": tool_input if tool_input is not None else {}}
    if cwd is not None:
        e["cwd"] = cwd
    return e


def _src_lease():
    return {"lane": "src", "tree": ["src/"], "kind": "cluster",
            "loop_ts": "2026-06-06T00:00:00Z"}


def test_agent_spawn_visible_on_axis_while_file_check_passes_clean(monkeypatch):
    """The #202 done-condition. An Agent call against a live `src` lease:
      - passes the FILE-collision check CLEAN (no dialect, passthrough, tree_known)
        — the issue #46 fix is preserved (no regression to the 200+-warn noise);
      - carries `effect_kind == "spawn"` in the journaled outcome — VISIBLE on the
        new axis even though it touched no file."""
    cfg = _kernel_cfg(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(prt, "live_leases_for", lambda c: [_src_lease()])
    dialect, outcome = prt.decide(
        _event("Agent", {"description": "x", "prompt": "spawn a worker"}, cwd="/repo"),
        cfg,
    )
    # FILE axis unchanged — clean pass.
    assert dialect is None, "an Agent spawn still emits no file-collision advisory"
    assert outcome["decision"] == "passthrough"
    assert outcome["tree_known"] is True
    # NEW axis — the spawn is visible.
    assert outcome["effect_kind"] == "spawn"


def test_agent_spawn_visible_with_no_live_lease(monkeypatch):
    """With NO live lease the Agent call admits and reaches Rung B's
    passthrough — the effect tag must ride that path too (a spawn is a spawn
    whether or not a lease is held)."""
    cfg = _kernel_cfg(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(prt, "live_leases_for", lambda c: [])
    dialect, outcome = prt.decide(
        _event("Agent", {"description": "x", "prompt": "spawn"}, cwd="/repo"), cfg)
    assert dialect is None
    assert outcome["decision"] == "passthrough"
    assert outcome["effect_kind"] == "spawn"


def test_ordinary_read_carries_no_effect_tag(monkeypatch):
    """A plain Read is `EffectKind.NONE` → no `effect_kind` key on its outcome
    (the tag is additive only for non-file effects; a read pollutes nothing)."""
    cfg = _kernel_cfg(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(prt, "live_leases_for", lambda c: [_src_lease()])
    _dialect, outcome = prt.decide(
        _event("Read", {"file_path": "/repo/x.txt"}, cwd="/repo"), cfg)
    assert "effect_kind" not in outcome


def test_fanout_from_one_holder_is_a_storm_via_the_fold():
    """End-to-end of the surface: 140 Agent-spawn outcomes from ONE holder, folded
    into the per-holder count, classify as a STORM — the runaway fan-out #202 exists
    to make surfaceable, now visible from the journaled `effect_kind` tag."""
    # Each spawn outcome contributes a (holder, effect_kind) record the way a
    # journal fold would extract it. One holder, 140 spawns (the observed case).
    records = [("7bb9e16d", EffectKind.SPAWN.value)] * 140
    storms = spawn_storm_holders(records, GENERIC_SPAWN_PRESSURE_POLICY)
    assert "7bb9e16d" in storms
    assert storms["7bb9e16d"].pressure is SpawnPressure.STORM
