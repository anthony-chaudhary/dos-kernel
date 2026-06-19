"""`dos init .` turns on every surface by default (docs/303).

A bare `dos init <dir>` now leaves a workspace with the core SKILLS copied, the
DOS MCP server wired into `.mcp.json`, and the DOS HOOKS wired into any runtime
already CONFIGURED here (its hooks config file exists) — so a single command
makes `dos doctor` report every surface active. Each surface has an opt-out
(`--no-skills` / `--no-hooks` / `--no-mcp`); explicit flags stay additive.

The load-bearing disciplines pinned here:
  * bare init writes dos.toml + the core skills + .mcp.json (all on by default);
  * bare init SOFT-skips hooks when no host is configured here (exit 0, never a
    guessed host), and wires them when a host's CONFIG FILE already exists;
  * the skill copy (which lands in `.claude/skills/`) does NOT pollute hook
    detection — a second bare init still skips hooks until a real config file
    appears (config-FILE probe, not the dir);
  * each `--no-X` opt-out suppresses exactly its surface;
  * re-running is idempotent (a refresh, not an error) and the merges preserve
    the user's own keys/servers.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import dos


def _cli(*argv: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(Path(dos.__file__).parents[1])}
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *argv],
        capture_output=True, text=True, env=env,
    )


_CORE = {"dos-next-up", "dos-dispatch", "dos-dispatch-loop", "dos-replan"}


# ---------------------------------------------------------------------------
# The default-on install: dos.toml + core skills + MCP, hooks soft-skipped.
# ---------------------------------------------------------------------------
def test_bare_init_installs_core_skills_and_mcp(tmp_path: Path):
    dest = tmp_path / "svc"
    proc = _cli("init", str(dest))
    assert proc.returncode == 0, proc.stderr
    # dos.toml scaffolded.
    assert (dest / "dos.toml").is_file()
    # The core skill pack copied as editable local files.
    found = {p.parent.name for p in (dest / ".claude" / "skills").rglob("SKILL.md")}
    assert found == _CORE
    # The MCP server wired into the portable .mcp.json.
    mcp = json.loads((dest / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["dos"]["command"] == "dos-mcp"


def test_bare_init_soft_skips_hooks_when_no_host_configured(tmp_path: Path):
    dest = tmp_path / "svc"
    proc = _cli("init", str(dest))
    assert proc.returncode == 0, proc.stderr            # NOT a fail-loud exit 1
    assert "skipped" in proc.stdout.lower()
    # No host hooks config file was invented by the default flow.
    assert not (dest / ".claude" / "settings.json").exists()
    assert not (dest / ".cursor" / "hooks.json").exists()


def test_bare_init_wires_hooks_when_a_host_config_file_exists(tmp_path: Path):
    """When a runtime is already CONFIGURED here (its hooks config FILE exists),
    the default flow wires that runtime — merged, never clobbering the user's own
    keys."""
    dest = tmp_path / "svc"
    dest.mkdir()
    (dest / ".claude").mkdir()
    (dest / ".claude" / "settings.json").write_text(
        json.dumps({"model": "opus"}), encoding="utf-8")
    proc = _cli("init", str(dest))
    assert proc.returncode == 0, proc.stderr
    assert "detected claude-code" in proc.stdout
    settings = json.loads((dest / ".claude" / "settings.json").read_text("utf-8"))
    assert settings["model"] == "opus"                  # user key preserved
    stops = [h["command"] for g in settings["hooks"]["Stop"] for h in g["hooks"]]
    assert "dos hook stop --workspace ." in stops


# ---------------------------------------------------------------------------
# The pollution guard: the skill copy lands in `.claude/`, but that MUST NOT be
# mistaken for a Claude-Code install. Detection probes the config FILE, so a
# re-run still skips hooks until a real config appears.
# ---------------------------------------------------------------------------
def test_skill_copy_does_not_pollute_hook_detection(tmp_path: Path):
    dest = tmp_path / "svc"
    assert _cli("init", str(dest)).returncode == 0
    # The skill copy created `.claude/skills/`, but NOT `.claude/settings.json`.
    assert (dest / ".claude" / "skills").is_dir()
    assert not (dest / ".claude" / "settings.json").exists()
    # A second bare init STILL skips hooks (config-FILE probe, not the dir) …
    again = _cli("init", str(dest))
    assert again.returncode == 0, again.stderr
    assert "skipped" in again.stdout.lower()
    assert not (dest / ".claude" / "settings.json").exists()
    # … until a real host config file appears, at which point it wires.
    (dest / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    third = _cli("init", str(dest))
    assert third.returncode == 0, third.stderr
    assert "detected claude-code" in third.stdout


# ---------------------------------------------------------------------------
# The opt-outs: each --no-X suppresses exactly its surface.
# ---------------------------------------------------------------------------
def test_no_skills_opt_out(tmp_path: Path):
    dest = tmp_path / "svc"
    proc = _cli("init", "--no-skills", str(dest))
    assert proc.returncode == 0, proc.stderr
    assert not (dest / ".claude" / "skills").exists()   # skills suppressed
    assert (dest / "dos.toml").is_file()                # …but dos.toml + MCP still on
    assert (dest / ".mcp.json").is_file()


def test_no_hooks_opt_out(tmp_path: Path):
    dest = tmp_path / "svc"
    dest.mkdir()
    (dest / ".claude").mkdir()
    (dest / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    proc = _cli("init", "--no-hooks", str(dest))
    assert proc.returncode == 0, proc.stderr
    settings = json.loads((dest / ".claude" / "settings.json").read_text("utf-8"))
    assert "hooks" not in settings                      # hooks NOT wired
    assert (dest / ".claude" / "skills").is_dir()       # …but skills still on


def test_no_mcp_opt_out(tmp_path: Path):
    dest = tmp_path / "svc"
    proc = _cli("init", "--no-mcp", str(dest))
    assert proc.returncode == 0, proc.stderr
    assert not (dest / ".mcp.json").exists()            # MCP suppressed
    assert (dest / ".claude" / "skills").is_dir()       # …but skills still on


def test_all_opt_outs_yield_just_dos_toml(tmp_path: Path):
    dest = tmp_path / "svc"
    proc = _cli("init", "--no-skills", "--no-hooks", "--no-mcp", str(dest))
    assert proc.returncode == 0, proc.stderr
    assert (dest / "dos.toml").is_file()
    assert not (dest / ".claude").exists()
    assert not (dest / ".mcp.json").exists()


# ---------------------------------------------------------------------------
# Idempotency + merge preservation.
# ---------------------------------------------------------------------------
def test_bare_init_rerun_is_idempotent(tmp_path: Path):
    dest = tmp_path / "svc"
    assert _cli("init", str(dest)).returncode == 0
    toml = (dest / "dos.toml").read_text(encoding="utf-8")
    # A second bare init REFRESHES the surfaces (not the old "already exists" error).
    again = _cli("init", str(dest))
    assert again.returncode == 0, again.stderr
    assert (dest / "dos.toml").read_text(encoding="utf-8") == toml  # untouched
    assert "skipped" in again.stdout.lower()            # skills already present


def test_mcp_merge_preserves_other_servers(tmp_path: Path):
    dest = tmp_path / "svc"
    dest.mkdir()
    (dest / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "foo"}}}),
        encoding="utf-8")
    proc = _cli("init", str(dest))
    assert proc.returncode == 0, proc.stderr
    servers = json.loads((dest / ".mcp.json").read_text("utf-8"))["mcpServers"]
    assert servers["other"] == {"command": "foo"}       # user server preserved
    assert servers["dos"]["command"] == "dos-mcp"       # DOS server added


def test_force_refreshes_mcp_entry(tmp_path: Path):
    dest = tmp_path / "svc"
    dest.mkdir()
    (dest / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"dos": {"command": "stale"}}}), encoding="utf-8")
    proc = _cli("init", "--force", str(dest))
    assert proc.returncode == 0, proc.stderr
    servers = json.loads((dest / ".mcp.json").read_text("utf-8"))["mcpServers"]
    assert servers["dos"]["command"] == "dos-mcp"       # refreshed to canonical
