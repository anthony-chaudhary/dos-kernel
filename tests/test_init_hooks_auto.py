"""`dos init --hooks auto` — detect the runtime, wire the hooks, ask nothing (docs/303).

The don't-make-me-think install: instead of asking the operator to know their
host's name, `auto` reads the two facts the host already left lying around —
its config dir under the workspace root (`.cursor/`, `.claude/`, …) and the
env marker it exports into shells it spawns — and wires every runtime it finds
through the SAME per-host path an explicit name takes (docs/221).

The disciplines pinned here:
  * detection is spec DATA (`config_path`, `env_markers`) — the machinery never
    compares against a vendor literal (the vendor-agnostic-kernel litmus);
  * hosts sharing one config file are wired ONCE, with the covered sibling
    NAMED in the output (claude-code / claude-cowork, docs/298);
  * nothing detected fails LOUD with the probe list and the host menu — never
    a guessed default (a wrong host is a no-op deny against the real runtime);
  * `auto` is an installer MODE, not a host: `host_spec("auto")` keeps raising;
  * `--dry-run` previews every detected host and writes nothing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import dos
from dos import hook_install as hi


def _all_env_markers() -> set[str]:
    markers: set[str] = set()
    for name in hi.host_names():
        markers.update(hi.host_spec(name).env_markers)
    return markers


def _cli(*argv: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Run the CLI with every host env marker SCRUBBED (this suite itself often
    runs inside a host that exports one), then `env_extra` applied on top."""
    env = {**os.environ, "PYTHONPATH": str(Path(dos.__file__).parents[1])}
    for marker in _all_env_markers():
        env.pop(marker, None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *argv],
        capture_output=True, text=True, env=env,
    )


def _config(dest: Path, host: str) -> dict:
    spec = hi.host_spec(host)
    return json.loads(dest.joinpath(*spec.config_path).read_text(encoding="utf-8"))


def _commands(config: dict, event: str) -> list[str]:
    cmds: list[str] = []
    for item in config.get("hooks", {}).get(event, []):
        if isinstance(item, dict) and "command" in item:
            cmds.append(item["command"])
        elif isinstance(item, dict):
            for h in item.get("hooks", []):
                if isinstance(h, dict) and "command" in h:
                    cmds.append(h["command"])
    return cmds


# ---------------------------------------------------------------------------
# The config-dir rung: a host is in use if its config dir already exists.
# ---------------------------------------------------------------------------
def test_auto_detects_a_single_config_dir(tmp_path: Path):
    dest = tmp_path / "svc"
    (dest / ".cursor").mkdir(parents=True)
    proc = _cli("init", "--hooks", "auto", str(dest))
    assert proc.returncode == 0, proc.stderr
    assert "detected" in proc.stdout and "cursor" in proc.stdout
    cfg = _config(dest, "cursor")
    assert _commands(cfg, "stop") == ["dos hook stop --workspace . --dialect cursor"]
    # Only the detected host was wired — no other host's config file appeared.
    for other in ("claude-code", "codex", "gemini", "antigravity"):
        assert not dest.joinpath(*hi.host_spec(other).config_path).exists(), other


def test_auto_detects_and_wires_multiple_hosts(tmp_path: Path):
    dest = tmp_path / "svc"
    (dest / ".cursor").mkdir(parents=True)
    (dest / ".gemini").mkdir()
    proc = _cli("init", "--hooks", "auto", str(dest))
    assert proc.returncode == 0, proc.stderr
    assert "cursor" in proc.stdout and "gemini" in proc.stdout
    assert _commands(_config(dest, "cursor"), "stop") == [
        "dos hook stop --workspace . --dialect cursor"]
    assert _commands(_config(dest, "gemini"), "AfterAgent") == [
        "dos hook stop --workspace . --dialect gemini"]


def test_auto_dedupes_the_shared_claude_file(tmp_path: Path):
    """`.claude/` detects BOTH claude-code and claude-cowork (one config file,
    docs/298) — auto wires it ONCE, owned by the default host, and NAMES the
    covered sibling instead of staying silent."""
    dest = tmp_path / "svc"
    (dest / ".claude").mkdir(parents=True)
    proc = _cli("init", "--hooks", "auto", str(dest))
    assert proc.returncode == 0, proc.stderr
    assert "also covers claude-cowork" in proc.stdout
    cfg = _config(dest, "claude-code")
    for ev in ("PreToolUse", "PostToolUse", "Stop"):
        dos_cmds = [c for c in _commands(cfg, ev) if c.startswith("dos hook ")]
        assert len(dos_cmds) == 1, (ev, dos_cmds)


def test_auto_rerun_is_idempotent(tmp_path: Path):
    dest = tmp_path / "svc"
    (dest / ".gemini").mkdir(parents=True)
    assert _cli("init", "--hooks", "auto", str(dest)).returncode == 0
    proc = _cli("init", "--hooks", "auto", str(dest))
    assert proc.returncode == 0, proc.stderr
    assert "untouched" in proc.stdout
    cfg = _config(dest, "gemini")
    dos_cmds = [c for c in _commands(cfg, "BeforeTool") if c.startswith("dos hook ")]
    assert len(dos_cmds) == 1


# ---------------------------------------------------------------------------
# The env-marker rung: a fresh repo, but the installer runs INSIDE the host.
# ---------------------------------------------------------------------------
def test_auto_env_marker_detects_the_host_around_it(tmp_path: Path):
    dest = tmp_path / "svc"
    dest.mkdir()
    marker = hi.claude_code_spec().env_markers[0]
    proc = _cli("init", "--hooks", "auto", str(dest), env_extra={marker: "1"})
    assert proc.returncode == 0, proc.stderr
    assert "claude-code" in proc.stdout
    cfg = _config(dest, "claude-code")
    assert _commands(cfg, "Stop") == ["dos hook stop --workspace ."]


# ---------------------------------------------------------------------------
# Nothing detected fails LOUD — with the probe list and the menu, never a guess.
# ---------------------------------------------------------------------------
def test_auto_nothing_detected_fails_loud_with_the_menu(tmp_path: Path):
    dest = tmp_path / "svc"
    dest.mkdir()
    proc = _cli("init", "--hooks", "auto", str(dest))
    assert proc.returncode == 1
    assert "no agent runtime detected" in proc.stderr
    # The refusal carries the actionable next step: the explicit-host menu …
    assert "dos init --hooks" in proc.stderr
    for host in hi.host_names():
        assert host in proc.stderr
    # … and the dirs it looked for.
    assert ".cursor/" in proc.stderr and ".claude/" in proc.stderr
    # No host config file was invented.
    for host in hi.host_names():
        assert not dest.joinpath(*hi.host_spec(host).config_path).exists(), host


def test_auto_is_a_mode_not_a_host():
    """`host_spec("auto")` keeps raising — auto is resolved at the CLI boundary,
    never inside the spec registry (a spec named 'auto' could be wired literally)."""
    with pytest.raises(ValueError, match="unknown hook host"):
        hi.host_spec(hi.AUTO_HOST)
    assert hi.AUTO_HOST not in hi.host_names()


# ---------------------------------------------------------------------------
# --dry-run previews EVERY detected host and writes nothing.
# ---------------------------------------------------------------------------
def test_auto_dry_run_previews_each_host_and_writes_nothing(tmp_path: Path):
    dest = tmp_path / "svc"
    (dest / ".cursor").mkdir(parents=True)
    (dest / ".gemini").mkdir()
    proc = _cli("init", "--hooks", "auto", "--dry-run", str(dest))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.count("nothing written") == 2
    assert "proposed hooks.json" in proc.stdout       # cursor's file
    assert "proposed settings.json" in proc.stdout    # gemini's file
    assert not (dest / ".cursor" / "hooks.json").exists()
    assert not (dest / ".gemini" / "settings.json").exists()
    assert not (dest / "dos.toml").exists()           # preview means touch-nothing


# ---------------------------------------------------------------------------
# The pure core: detection_probe + choose_auto_hosts.
# ---------------------------------------------------------------------------
def test_detection_probe_is_the_config_parent_dir():
    assert hi.detection_probe(hi.host_spec("cursor")) == (".cursor",)
    assert hi.detection_probe(hi.claude_code_spec()) == (".claude",)
    # A root-level config file probes as itself.
    flat = hi.HostHookSpec(
        host="x", config_path=("x.json",), fmt=hi.ConfigFormat.JSON,
        pre_events=("a",), post_events=("b",), stop_events=("c",))
    assert hi.detection_probe(flat) == ("x.json",)


def test_choose_auto_hosts_dedupes_shared_paths_default_first():
    shared = (".claude", "settings.json")
    picked = hi.choose_auto_hosts([
        ("antigravity", (".agents", "hooks.json")),
        ("claude-cowork", shared),
        ("claude-code", shared),
        ("cursor", (".cursor", "hooks.json")),
    ])
    # The default host owns its shared file even when detected later, and is
    # surfaced FIRST; the covered sibling is named; distinct paths pass through.
    assert picked[0] == ("claude-code", ("claude-cowork",))
    assert ("antigravity", ()) in picked and ("cursor", ()) in picked
    assert len(picked) == 3
    # No shared paths → input order, nothing covered.
    assert hi.choose_auto_hosts([
        ("cursor", (".cursor", "hooks.json")),
        ("gemini", (".gemini", "settings.json")),
    ]) == [("cursor", ()), ("gemini", ())]
    assert hi.choose_auto_hosts([]) == []


def test_env_markers_are_data_with_an_empty_default():
    """The marker rides the spec as DATA (the vendor-agnostic discipline): the
    default host carries one; a spec that declares none defaults to ()."""
    assert hi.claude_code_spec().env_markers == ("CLAUDECODE",)
    bare = hi.HostHookSpec(
        host="x", config_path=(".x", "h.json"), fmt=hi.ConfigFormat.JSON,
        pre_events=("a",), post_events=("b",), stop_events=("c",))
    assert bare.env_markers == ()
