"""The `[productivity]` / `[efficiency]` / `[efficiency_trend]` / `[improve]`
config seam — the loop-economics family's `dos.toml` tables (issue #37).

docs/300 shipped the four loop-economics verdicts (`productivity`, `efficiency`,
`efficiency_trend`, `improve`) but left every threshold reachable ONLY through a
CLI flag or a dataclass default. The COSTLY / WASTEFUL floors were therefore
dead-by-default with no way to ARM them as declared data — a workspace could not
say "spend under this work-per-token ratio is COSTLY" without editing the kernel.

This file is the contract for closing that gap: each of the four modules grows a
`policy_from_table(table, *, base)` + `load_from_toml(path, *, base)` pair
mirroring `dos.cooldown` (the sibling seam), and `load_workspace_config` folds
the four tables in the same `_layer(...)` loop it already runs for
`[cooldown]`/`[stamp]`/`[supervise]`/… So a workspace declaring

    [efficiency]
    floor = 0.001

makes `dos efficiency --work 1 --tokens 1000000` (NO `--floor` flag) exit 3
(COSTLY), and `dos doctor --json` reports the loaded table — the issue's
done-condition, verbatim.

The four loaders share the sibling seam's discipline, asserted below:
  * OVERRIDE semantics — a present key overrides ``base``; an omitted key inherits.
  * additive degradation — absent file / absent-or-empty table → ``base`` unchanged
    (a workspace that declared nothing is byte-identical to today).
  * loud-on-malformed — an unknown key or a wrong-typed value RAISES ``ValueError``
    (the `stamp.convention_from_table` / `cooldown.policy_from_table` posture);
    `load_workspace_config` catches it, warns, and keeps the base (no axis wedged).
  * BOM tolerance — `utf-8-sig` read, so a PowerShell-written table is not dropped.

Every threshold the four loaders read is already validated by the policy
dataclass's own ``__post_init__`` (non-negative floors, ``min_samples`` etc.), so
a negative value declared in the table surfaces through the same `ValueError`
path as an unknown key — the loader never re-implements the bound, it just builds
the frozen policy and lets the dataclass refuse a bad number.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from dos import efficiency as _eff
from dos import efficiency_trend as _trend
from dos import improve as _improve
from dos import productivity as _prod
import dos.config as _config


# ---------------------------------------------------------------------------
# Shared helpers (mirroring tests/test_workspace_config.py).
# ---------------------------------------------------------------------------


def _write_toml(repo: Path, body: str) -> None:
    """Write a BOM-free dos.toml under ``repo`` (the sibling seam's helper)."""
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "dos.toml").write_text(body, encoding="utf-8")


def _isolated_env(repo: Path) -> dict:
    """Parent env with the workspace override pinned to ``repo`` (#125)."""
    import os
    env = dict(os.environ)
    env.pop("DISPATCH_WORKSPACE", None)
    env["DISPATCH_WORKSPACE"] = str(repo)
    return env


def _cli(repo: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *argv, "--workspace", str(repo)],
        capture_output=True, text=True, cwd=str(repo), env=_isolated_env(repo),
    )


# ===========================================================================
# [efficiency] — the COSTLY floor as data (the issue's worked example).
# ===========================================================================


class TestEfficiencyTable:
    def test_policy_from_table_overrides_floor(self):
        p = _eff.policy_from_table({"floor": 0.001})
        assert p.floor == 0.001
        # min_tokens unnamed → inherits the base default.
        assert p.min_tokens == _eff.DEFAULT_POLICY.min_tokens

    def test_policy_from_table_overrides_min_tokens(self):
        p = _eff.policy_from_table({"min_tokens": 50})
        assert p.min_tokens == 50
        assert p.floor == _eff.DEFAULT_POLICY.floor

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError):
            _eff.policy_from_table({"bogus": 1})

    def test_negative_floor_raises_via_dataclass(self):
        # The loader does not re-implement the bound; the dataclass refuses it.
        with pytest.raises(ValueError):
            _eff.policy_from_table({"floor": -0.5})

    def test_load_from_toml_present(self, tmp_path):
        p = tmp_path / "dos.toml"
        p.write_text("[efficiency]\nfloor = 0.001\n", encoding="utf-8")
        pol = _eff.load_from_toml(p)
        assert pol.floor == 0.001

    def test_load_from_toml_absent_is_base(self, tmp_path):
        assert _eff.load_from_toml(tmp_path / "nope.toml") is _eff.DEFAULT_POLICY

    def test_load_from_toml_bom_tolerated(self, tmp_path):
        # A PowerShell-written (BOM) table must NOT be silently dropped to base.
        p = tmp_path / "dos.toml"
        p.write_bytes(b"\xef\xbb\xbf[efficiency]\nfloor = 0.002\n")
        assert _eff.load_from_toml(p).floor == 0.002


# ===========================================================================
# [improve] — the WASTEFUL revert floor as data.
# ===========================================================================


class TestImproveTable:
    def test_policy_from_table_overrides(self):
        p = _improve.policy_from_table(
            {"efficiency_floor": 0.01, "max_consecutive_reverts": 5}
        )
        assert p.efficiency_floor == 0.01
        assert p.max_consecutive_reverts == 5
        # min_tokens_for_efficiency unnamed → inherits.
        assert (
            p.min_tokens_for_efficiency
            == _improve.DEFAULT_POLICY.min_tokens_for_efficiency
        )

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError):
            _improve.policy_from_table({"floor": 1})  # efficiency_floor, not floor

    def test_zero_max_reverts_raises_via_dataclass(self):
        with pytest.raises(ValueError):
            _improve.policy_from_table({"max_consecutive_reverts": 0})

    def test_load_from_toml_present(self, tmp_path):
        p = tmp_path / "dos.toml"
        p.write_text(
            "[improve]\nefficiency_floor = 0.05\nmax_consecutive_reverts = 4\n",
            encoding="utf-8",
        )
        pol = _improve.load_from_toml(p)
        assert pol.efficiency_floor == 0.05
        assert pol.max_consecutive_reverts == 4

    def test_load_from_toml_absent_is_base(self, tmp_path):
        assert _improve.load_from_toml(tmp_path / "nope.toml") is _improve.DEFAULT_POLICY


# ===========================================================================
# [productivity] — the per-step DIMINISHING floor as data.
# ===========================================================================


class TestProductivityTable:
    def test_policy_from_table_overrides(self):
        p = _prod.policy_from_table({"min_steps": 5, "floor": 250})
        assert p.min_steps == 5
        assert p.floor == 250

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError):
            _prod.policy_from_table({"bogus": 1})

    def test_negative_floor_raises_via_dataclass(self):
        with pytest.raises(ValueError):
            _prod.policy_from_table({"floor": -1})

    def test_load_from_toml_present(self, tmp_path):
        p = tmp_path / "dos.toml"
        p.write_text("[productivity]\nfloor = 100\n", encoding="utf-8")
        assert _prod.load_from_toml(p).floor == 100

    def test_load_from_toml_absent_is_base(self, tmp_path):
        assert _prod.load_from_toml(tmp_path / "nope.toml") is _prod.DEFAULT_POLICY


# ===========================================================================
# [efficiency_trend] — the cross-run DEGRADING band as data.
# ===========================================================================


class TestEfficiencyTrendTable:
    def test_policy_from_table_overrides(self):
        p = _trend.policy_from_table({"min_samples": 4, "tolerance": 0.1})
        assert p.min_samples == 4
        assert p.tolerance == 0.1

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError):
            _trend.policy_from_table({"bogus": 1})

    def test_negative_tolerance_raises_via_dataclass(self):
        with pytest.raises(ValueError):
            _trend.policy_from_table({"tolerance": -0.1})

    def test_load_from_toml_present(self, tmp_path):
        p = tmp_path / "dos.toml"
        p.write_text("[efficiency_trend]\ntolerance = 0.15\n", encoding="utf-8")
        assert _trend.load_from_toml(p).tolerance == 0.15

    def test_load_from_toml_absent_is_base(self, tmp_path):
        assert _trend.load_from_toml(tmp_path / "nope.toml") is _trend.DEFAULT_POLICY


# ===========================================================================
# load_workspace_config — the four tables reach the config (the layering rail).
# ===========================================================================


class TestConfigCarriesEconomicsPolicies:
    def test_default_config_carries_base_policies(self):
        """A no-`dos.toml` workspace carries the four base policies (the fields
        exist and default to the module DEFAULT_POLICY — byte-identical to today
        for a workspace that declared nothing)."""
        cfg = _config.default_config()
        assert cfg.efficiency == _eff.DEFAULT_POLICY
        assert cfg.improve == _improve.DEFAULT_POLICY
        assert cfg.productivity == _prod.DEFAULT_POLICY
        assert cfg.efficiency_trend == _trend.DEFAULT_POLICY

    def test_efficiency_table_reaches_config(self, tmp_path):
        _write_toml(tmp_path, "[efficiency]\nfloor = 0.001\nmin_tokens = 500\n")
        cfg = _config.load_workspace_config(tmp_path)
        assert cfg.efficiency.floor == 0.001
        assert cfg.efficiency.min_tokens == 500

    def test_improve_table_reaches_config(self, tmp_path):
        _write_toml(tmp_path, "[improve]\nefficiency_floor = 0.02\n")
        cfg = _config.load_workspace_config(tmp_path)
        assert cfg.improve.efficiency_floor == 0.02

    def test_productivity_and_trend_tables_reach_config(self, tmp_path):
        _write_toml(
            tmp_path,
            "[productivity]\nfloor = 300\n\n[efficiency_trend]\ntolerance = 0.2\n",
        )
        cfg = _config.load_workspace_config(tmp_path)
        assert cfg.productivity.floor == 300
        assert cfg.efficiency_trend.tolerance == 0.2

    def test_malformed_table_warns_and_keeps_base(self, tmp_path):
        """A present-but-malformed table must NOT crash a command that never
        touches that axis — it is warned and the base is kept (the shared
        warn-and-fall-back posture)."""
        _write_toml(tmp_path, "[efficiency]\nbogus_key = 1\n")
        warnings: list[tuple[str, str]] = []
        cfg = _config.load_workspace_config(
            tmp_path, warn=lambda label, msg: warnings.append((label, msg))
        )
        # base kept; a one-line notice was emitted for the efficiency axis.
        assert cfg.efficiency == _eff.DEFAULT_POLICY
        assert any(label == "efficiency" for label, _ in warnings)


# ===========================================================================
# The done-condition — armed floor flips the CLI verdict with no flag.
# ===========================================================================


class TestDoneCondition:
    def test_armed_floor_makes_efficiency_exit_costly(self, tmp_path):
        """The issue's verbatim done-condition: a `[efficiency] floor = 0.001`
        table makes `dos efficiency --work 1 --tokens 1000000` (NO --floor flag)
        exit 3 (COSTLY) — the dead-by-default floor, ARMED as data."""
        _write_toml(tmp_path, "[efficiency]\nfloor = 0.001\n")
        # work/tokens = 1/1_000_000 = 1e-6, far under the 0.001 floor → COSTLY.
        r = _cli(tmp_path, "efficiency", "--work", "1", "--tokens", "1000000")
        assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
        assert "COSTLY" in r.stdout

    def test_no_table_efficiency_is_efficient(self, tmp_path):
        """Without the table the same call is EFFICIENT (floor disabled at 0.0) —
        the additive-degradation half: arming is opt-in, absence is today."""
        _write_toml(tmp_path, "[reasons.FOO]\ncategory = 'OPERATOR_GATE'\n")
        r = _cli(tmp_path, "efficiency", "--work", "1", "--tokens", "1000000")
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert "EFFICIENT" in r.stdout

    def test_doctor_json_reports_loaded_tables(self, tmp_path):
        """`dos doctor --json` surfaces the loaded economics tables so an operator
        can see, at a glance, which floors a workspace has armed."""
        import json
        _write_toml(tmp_path, "[efficiency]\nfloor = 0.001\n")
        r = _cli(tmp_path, "doctor", "--json")
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        doc = json.loads(r.stdout)
        # The doctor report names an `economics` (or per-table) section carrying
        # the armed floor; the exact key is pinned by the doctor renderer edit.
        assert "0.001" in r.stdout or doc.get("economics", {}).get(
            "efficiency", {}
        ).get("floor") == 0.001
