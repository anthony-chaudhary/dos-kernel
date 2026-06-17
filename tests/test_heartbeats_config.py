"""FRS — the `[heartbeats]` config seam (the freshness dead-man's switch as data).

`freshness.classify` is the kernel mechanism; WHICH always-on jobs a workspace
expects to beat — and how often, with how much slack — is policy declared in
`dos.toml [heartbeats]`. These tests pin the table → `HeartbeatPolicy` readback
(via the shared `config.load_workspace_config` loader) and the `policy_from_table`
parsing/validation, mirroring `test`s for the `[supervise]` seam.
"""

from __future__ import annotations

import pytest

from dos import config, freshness
from dos.freshness import (
    CadencePolicy,
    EMPTY_HEARTBEAT_POLICY,
    HeartbeatPolicy,
    JobCadence,
    policy_from_table,
)

_MIN = 60 * 1000
_HOUR = 60 * _MIN


# ---------------------------------------------------------------------------
# 1. policy_from_table — the pure parser.
# ---------------------------------------------------------------------------


def test_table_parses_jobs_and_factors():
    table = {
        "grace_factor": 2.0,
        "dead_factor": 4.0,
        "jobs": {
            "pulse": "6h",
            "supervise": "30m",
            "enforce-tune": {"cadence": "12h", "critical": False},
        },
    }
    hp = policy_from_table(table)
    assert hp.policy == CadencePolicy(grace_factor=2.0, dead_factor=4.0)
    assert hp.jobs == (
        JobCadence(job_id="pulse", cadence_ms=6 * _HOUR, critical=True),
        JobCadence(job_id="supervise", cadence_ms=30 * _MIN, critical=True),
        JobCadence(job_id="enforce-tune", cadence_ms=12 * _HOUR, critical=False),
    )


def test_table_factors_inherit_base_when_absent():
    hp = policy_from_table({"jobs": {"pulse": "6h"}})
    assert hp.policy == freshness.DEFAULT_POLICY
    assert hp.jobs == (JobCadence(job_id="pulse", cadence_ms=6 * _HOUR),)


def test_table_unknown_top_key_raises():
    with pytest.raises(ValueError, match="unknown key"):
        policy_from_table({"jobz": {"pulse": "6h"}})


def test_table_unknown_job_key_raises():
    with pytest.raises(ValueError, match="unknown key"):
        policy_from_table({"jobs": {"pulse": {"cadence": "6h", "crticial": True}}})


def test_table_job_missing_cadence_raises():
    with pytest.raises(ValueError, match="missing 'cadence'"):
        policy_from_table({"jobs": {"pulse": {"critical": True}}})


def test_table_job_critical_must_be_bool():
    with pytest.raises(ValueError, match="critical must be a boolean"):
        policy_from_table({"jobs": {"pulse": {"cadence": "6h", "critical": "yes"}}})


def test_table_bad_cadence_raises():
    with pytest.raises(ValueError):
        policy_from_table({"jobs": {"pulse": "6 lightyears"}})


def test_table_bad_factor_ordering_raises():
    # dead_factor < grace_factor is rejected by CadencePolicy.__post_init__.
    with pytest.raises(ValueError):
        policy_from_table({"grace_factor": 3.0, "dead_factor": 1.5, "jobs": {}})


# ---------------------------------------------------------------------------
# 2. The full readback through load_workspace_config (the operator path).
# ---------------------------------------------------------------------------


def _write(tmp_path, body: str):
    (tmp_path / "dos.toml").write_text(body, encoding="utf-8")


def test_workspace_with_no_table_is_empty_default(tmp_path):
    _write(tmp_path, 'workspace = "."\n')
    cfg = config.load_workspace_config(str(tmp_path), gather_env=False)
    assert cfg.heartbeats == EMPTY_HEARTBEAT_POLICY


def test_workspace_reads_declared_jobs(tmp_path):
    _write(
        tmp_path,
        'workspace = "."\n'
        "[heartbeats]\n"
        "grace_factor = 1.25\n"
        "[heartbeats.jobs]\n"
        'pulse = "6h"\n'
        'supervise = { cadence = "30m", critical = false }\n',
    )
    cfg = config.load_workspace_config(str(tmp_path), gather_env=False)
    assert cfg.heartbeats.policy.grace_factor == 1.25
    ids = [(j.job_id, j.cadence_ms, j.critical) for j in cfg.heartbeats.jobs]
    assert ("pulse", 6 * _HOUR, True) in ids
    assert ("supervise", 30 * _MIN, False) in ids


def test_workspace_malformed_table_warns_keeps_base(tmp_path):
    # A bad cadence makes the table raise inside the loader → warn + keep base
    # (the `supervise` safe-direction posture: a broken table declares nothing,
    # it can never manufacture a MISSING accusation).
    _write(
        tmp_path,
        'workspace = "."\n'
        "[heartbeats.jobs]\n"
        'pulse = "not-a-duration"\n',
    )
    warnings = []
    cfg = config.load_workspace_config(
        str(tmp_path), gather_env=False, warn=lambda label, msg: warnings.append((label, msg))
    )
    assert cfg.heartbeats == EMPTY_HEARTBEAT_POLICY
    assert any(label == "heartbeats" for label, _ in warnings)


def test_beat_ledger_path_resolves_under_each_layout(tmp_path):
    cfg = config.load_workspace_config(str(tmp_path), gather_env=False)
    # The path is set (a sibling of the lane journal) under whichever layout the
    # workspace resolved to — never None after a real build.
    assert cfg.paths.beat_ledger is not None
    assert cfg.paths.beat_ledger.name == "beat-ledger.jsonl"
