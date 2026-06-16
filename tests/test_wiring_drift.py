"""`dos doctor --wiring` — the hook wiring-drift check (issue #190).

DOS writes its enforcement (`dos init --hooks <runtime>`) but never re-checked
that the wiring is STILL installed — the classic *substrate-built-not-enforced*
gap (the sibling job repo's `provision --verify`). An IDE upgrade or a
teammate's settings edit can silently unwire the DOS PEP, and a trust kernel
whose whole premise is "don't trust that the thing happened" was trusting that
its own enforcement was still bound.

What these tests pin:

  * the PURE classifier `classify_wiring_drift` truth table (config absent →
    NOT_WIRED; all events → WIRED; partial → DRIFTED; zero → NOT_WIRED);
  * only DRIFTED is a regression (`wiring_drift_is_regression`) — a never-wired
    host is not a failure;
  * the verb end-to-end: wire a host, MUTATE its config to drop a DOS event,
    and assert the drift verb reports DRIFTED + exits non-zero (the
    done-condition's witness);
  * the kernel stays vendor-blind — the classifier names no host (it reads the
    `HostHookSpec` the caller resolved).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import dos
from dos import hook_install as hi


def _cli(*argv: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(Path(dos.__file__).parents[1])}
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *argv],
        capture_output=True, text=True, env=env,
    )


# ==========================================================================
# classify_wiring_drift — the pure truth table.
# ==========================================================================
def test_classify_no_config_is_not_wired():
    assert hi.classify_wiring_drift(
        ["PreToolUse", "PostToolUse", "Stop"], [], config_exists=False
    ) == hi.WIRING_NOT_WIRED


def test_classify_all_events_is_wired():
    expected = ["PreToolUse", "PostToolUse", "Stop"]
    assert hi.classify_wiring_drift(
        expected, expected, config_exists=True) == hi.WIRING_WIRED


def test_classify_partial_events_is_drifted():
    expected = ["PreToolUse", "PostToolUse", "Stop"]
    # Only PreToolUse survives a config rewrite → DRIFTED (the silent unwire).
    assert hi.classify_wiring_drift(
        expected, ["PreToolUse"], config_exists=True) == hi.WIRING_DRIFTED


def test_classify_zero_events_present_config_is_not_wired():
    # A config that exists but carries NO DOS events is NOT_WIRED, not DRIFTED:
    # without a persisted install record we cannot distinguish "removed" from
    # "never wired", so we report the conservative absence (never a regression).
    expected = ["PreToolUse", "PostToolUse", "Stop"]
    assert hi.classify_wiring_drift(
        expected, [], config_exists=True) == hi.WIRING_NOT_WIRED


def test_classify_empty_expected_existing_config_is_wired():
    # A degenerate spec that wires nothing cannot drift; an existing config is WIRED.
    assert hi.classify_wiring_drift([], [], config_exists=True) == hi.WIRING_WIRED


def test_only_drifted_is_a_regression():
    assert hi.wiring_drift_is_regression(hi.WIRING_DRIFTED) is True
    assert hi.wiring_drift_is_regression(hi.WIRING_WIRED) is False
    # A never-wired host is expected to be absent — gating on it would fail the
    # verb on every unconfigured runtime.
    assert hi.wiring_drift_is_regression(hi.WIRING_NOT_WIRED) is False


# ==========================================================================
# the verb end-to-end — wire, mutate, detect DRIFT (the done-condition witness).
# ==========================================================================
def test_clean_install_reports_wired_and_exits_zero(tmp_path: Path):
    dest = tmp_path / "svc"
    assert _cli("init", "--hooks", "claude-code", str(dest)).returncode == 0
    proc = _cli("doctor", "--workspace", str(dest), "--wiring", "--json")
    assert proc.returncode == 0, proc.stderr
    rows = {r["host"]: r for r in json.loads(proc.stdout)["wiring"]}
    assert rows["claude-code"]["verdict"] == hi.WIRING_WIRED
    assert rows["claude-code"]["regression"] is False


def test_mutated_config_reports_drift_and_exits_nonzero(tmp_path: Path):
    """The load-bearing case: wire claude-code, then DROP a DOS hook event from
    its config (the silent unwire), and assert the verb catches it."""
    dest = tmp_path / "svc"
    assert _cli("init", "--hooks", "claude-code", str(dest)).returncode == 0
    spec = hi.host_spec("claude-code")
    cfg_path = dest.joinpath(*spec.config_path)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    # Remove the Stop event wholesale — a teammate / IDE upgrade rewriting the
    # config and dropping one DOS hook block. PreToolUse/PostToolUse survive.
    cfg.get("hooks", {}).pop("Stop", None)
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    proc = _cli("doctor", "--workspace", str(dest), "--wiring", "--json")
    assert proc.returncode == 1, "a DRIFTED runtime must exit non-zero"
    rows = {r["host"]: r for r in json.loads(proc.stdout)["wiring"]}
    assert rows["claude-code"]["verdict"] == hi.WIRING_DRIFTED
    assert rows["claude-code"]["regression"] is True
    # The surviving events are still seen (not a false NOT_WIRED).
    assert "PreToolUse" in rows["claude-code"]["wired"]
    assert "Stop" not in rows["claude-code"]["wired"]


def test_fully_removed_config_reports_not_wired_and_exits_zero(tmp_path: Path):
    """A wholesale config removal is NOT_WIRED (absence), not DRIFTED — so the
    verb does not fail on a runtime this repo simply never wired."""
    dest = tmp_path / "svc"
    assert _cli("init", "--hooks", "claude-code", str(dest)).returncode == 0
    spec = hi.host_spec("claude-code")
    dest.joinpath(*spec.config_path).unlink()
    proc = _cli("doctor", "--workspace", str(dest), "--wiring", "--json")
    assert proc.returncode == 0, "a never-wired/removed runtime must not gate"
    rows = {r["host"]: r for r in json.loads(proc.stdout)["wiring"]}
    assert rows["claude-code"]["verdict"] == hi.WIRING_NOT_WIRED


def test_text_output_names_drift_and_exits_nonzero(tmp_path: Path):
    dest = tmp_path / "svc"
    assert _cli("init", "--hooks", "claude-code", str(dest)).returncode == 0
    spec = hi.host_spec("claude-code")
    cfg_path = dest.joinpath(*spec.config_path)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.get("hooks", {}).pop("PostToolUse", None)
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    proc = _cli("doctor", "--workspace", str(dest), "--wiring")
    assert proc.returncode == 1
    assert "DRIFTED" in proc.stdout
    assert "dos init --hooks" in proc.stderr  # the repair hint
