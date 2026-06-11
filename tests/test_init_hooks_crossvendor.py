"""`dos init --hooks <host>` — the cross-vendor hook installer (docs/221).

docs/217 made DOS RENDER a deny into the bytes Cursor / Codex / Gemini honor; this
installer WIRES those renderers into each host's own config file so a team already
on one of those runtimes binds the DOS PEP with no hand-authored config. `--hooks
<host>` is the cross-vendor generalization of `--with-hooks` (which is now exactly
`--hooks claude-code`).

The disciplines pinned here:
  * each host writes the RIGHT file at the RIGHT path in the RIGHT format, naming
    that host's own event vocabulary, with the matching `--dialect <host>` on the
    wired command (so the verb emits the host's envelope);
  * `--with-hooks` stays byte-identical to today (the parity floor — also covered by
    the unchanged test_init_hooks.py);
  * the merge preserves the user's own hooks/keys and is idempotent on re-run;
  * `--force` repairs an existing DOS block without duplicating it;
  * an unknown host fails LOUD (argparse usage error), never a silent no-op;
  * NO host's tool-input REWRITE key is ever written (the docs/191 §4 byte-author
    floor, preserved at the install layer).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import dos
from dos import hook_install as hi


def _cli(*argv: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(Path(dos.__file__).parents[1])}
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *argv],
        capture_output=True, text=True, env=env,
    )


def _json_config(dest: Path, spec: hi.HostHookSpec) -> dict:
    return json.loads(dest.joinpath(*spec.config_path).read_text(encoding="utf-8"))


def _flat_commands(config: dict, event: str) -> list[str]:
    """Every `command` wired under `event` in a host config (flat or group-wrapped)."""
    cmds: list[str] = []
    for item in config.get("hooks", {}).get(event, []):
        if isinstance(item, dict) and "command" in item:        # flat (Cursor/Gemini)
            cmds.append(item["command"])
        elif isinstance(item, dict):                            # group-wrapped (CC)
            for h in item.get("hooks", []):
                if isinstance(h, dict) and "command" in h:
                    cmds.append(h["command"])
    return cmds


# ---------------------------------------------------------------------------
# Per-host: the right file, format, events, and dialect-tagged command.
# ---------------------------------------------------------------------------
def test_cursor_writes_hooks_json_with_version_and_both_pre_events(tmp_path: Path):
    dest = tmp_path / "svc"
    proc = _cli("init", "--hooks", "cursor", str(dest))
    assert proc.returncode == 0, proc.stderr
    cfg = _json_config(dest, hi.host_spec("cursor"))
    # hooks.json requires {"version": 1}.
    assert cfg["version"] == 1
    # PRE is two events (shell + MCP), both wired to pretool with the cursor dialect.
    for ev in ("beforeShellExecution", "beforeMCPExecution"):
        assert _flat_commands(cfg, ev) == ["dos hook pretool --workspace . --dialect cursor"]
    assert _flat_commands(cfg, "afterFileEdit") == ["dos hook posttool --workspace . --dialect cursor"]
    assert _flat_commands(cfg, "stop") == ["dos hook stop --workspace . --dialect cursor"]
    # Cursor entries are FLAT {"command": …} — no CC group wrapper, no "type" key.
    entry = cfg["hooks"]["stop"][0]
    assert set(entry) == {"command"}
    # The failClosed note is surfaced to the operator.
    assert "failClosed" in proc.stdout


def test_codex_writes_valid_toml_preserving_a_users_config(tmp_path: Path):
    dest = tmp_path / "svc"
    dest.mkdir()
    codex_dir = dest / ".codex"
    codex_dir.mkdir()
    # A pre-existing, comment-rich config.toml with the user's OWN hook + a key.
    (codex_dir / "config.toml").write_text(
        '# my codex config\nmodel = "gpt-5-codex"\n\n'
        '[[hooks.PreToolUse]]\nmatcher = "^Bash$"\n'
        '[[hooks.PreToolUse.hooks]]\ntype = "command"\ncommand = "my-own-linter"\n',
        encoding="utf-8",
    )
    proc = _cli("init", "--hooks", "codex", str(dest))
    assert proc.returncode == 0, proc.stderr
    text = (codex_dir / "config.toml").read_text(encoding="utf-8")
    # The result is valid TOML.
    parsed = tomllib.loads(text)
    # The user's key + comment + own hook all survive (text append, not re-serialize).
    assert parsed["model"] == "gpt-5-codex"
    assert "# my codex config" in text
    pre = [c for g in parsed["hooks"]["PreToolUse"] for c in (h["command"] for h in g["hooks"])]
    assert "my-own-linter" in pre
    assert "dos hook pretool --workspace . --dialect codex" in pre
    # All three DOS events were wired.
    assert "dos hook posttool --workspace . --dialect codex" in [
        h["command"] for g in parsed["hooks"]["PostToolUse"] for h in g["hooks"]]
    assert "dos hook stop --workspace . --dialect codex" in [
        h["command"] for g in parsed["hooks"]["Stop"] for h in g["hooks"]]
    # The coverage-limit note is surfaced.
    assert "coverage" in proc.stdout.lower() or "handlers" in proc.stdout.lower()


def test_gemini_writes_settings_json_with_its_event_names(tmp_path: Path):
    dest = tmp_path / "svc"
    proc = _cli("init", "--hooks", "gemini", str(dest))
    assert proc.returncode == 0, proc.stderr
    cfg = _json_config(dest, hi.host_spec("gemini"))
    # Gemini's own event vocabulary: BeforeTool / AfterTool / AfterAgent.
    assert _flat_commands(cfg, "BeforeTool") == ["dos hook pretool --workspace . --dialect gemini"]
    assert _flat_commands(cfg, "AfterTool") == ["dos hook posttool --workspace . --dialect gemini"]
    assert _flat_commands(cfg, "AfterAgent") == ["dos hook stop --workspace . --dialect gemini"]
    # Gemini 0.45.x adopted Claude-Code's GROUP-WRAPPED hook-config shape (docs/268):
    # each event is a list of {"hooks": [{"type":"command","command":…}]} groups, NOT
    # a flat {"type","command"} entry — the loader discards a definition whose `hooks`
    # is not an array. So the structural shape is CC's, group wrapper and all.
    group = cfg["hooks"]["BeforeTool"][0]
    assert set(group) == {"hooks"}                       # group wrapper, no flat command at top
    assert group["hooks"][0]["type"] == "command"


def test_antigravity_writes_agents_hooks_json_cc_shaped_with_its_dialect(tmp_path: Path):
    """Antigravity is the HYBRID host: a Claude-Code-SHAPED config (group-wrapped
    entries under PreToolUse/PostToolUse/Stop) wired with the GEMINI-SHAPED
    `--dialect antigravity` (its hook OUTPUT is top-level {"decision":"deny"}). This
    pins both halves of that split at the install layer."""
    dest = tmp_path / "svc"
    proc = _cli("init", "--hooks", "antigravity", str(dest))
    assert proc.returncode == 0, proc.stderr
    spec = hi.host_spec("antigravity")
    # The right file at the right path: .agents/hooks.json.
    assert spec.config_path == (".agents", "hooks.json")
    cfg = _json_config(dest, spec)
    # CC event vocabulary, each wired to the right verb with the antigravity dialect.
    assert _flat_commands(cfg, "PreToolUse") == ["dos hook pretool --workspace . --dialect antigravity"]
    assert _flat_commands(cfg, "PostToolUse") == ["dos hook posttool --workspace . --dialect antigravity"]
    assert _flat_commands(cfg, "Stop") == ["dos hook stop --workspace . --dialect antigravity"]
    # CC-SHAPED config: entries nest under a {"hooks": [...]} group (NOT a flat list),
    # and the inner entry is the typed {"type":"command","command":…} — like claude-code.
    group = cfg["hooks"]["PreToolUse"][0]
    assert set(group) == {"hooks"}                       # group wrapper, no flat command at top
    assert group["hooks"][0]["type"] == "command"
    # No {"version": …} key (Antigravity's hooks.json needs none, unlike Cursor's).
    assert "version" not in cfg


def test_claude_cowork_writes_the_shared_claude_settings_file(tmp_path: Path):
    """Claude Cowork is the SHARED-surface host (docs/298): it runs the Claude Code
    harness, so its hook surface IS `.claude/settings.json` — and the wired command
    carries NO --dialect (the shared file is read by both runtimes; the default CC
    envelope is the one both honor). A cowork install is byte-identical to a
    claude-code install; only the operator-facing note differs."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    proc = _cli("init", "--hooks", "claude-cowork", str(a))
    assert proc.returncode == 0, proc.stderr
    assert _cli("init", "--hooks", "claude-code", str(b)).returncode == 0
    spec = hi.host_spec("claude-cowork")
    # The right file: the SAME path the claude-code spec writes.
    assert spec.config_path == (".claude", "settings.json")
    fa = (a / ".claude" / "settings.json").read_text(encoding="utf-8")
    fb = (b / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert fa == fb
    cfg = json.loads(fa)
    # CC events, dialect-less commands (the default IS the envelope — both runtimes
    # run the CC harness), group-wrapped CC shape.
    assert _flat_commands(cfg, "PreToolUse") == ["dos hook pretool --workspace ."]
    assert _flat_commands(cfg, "PostToolUse") == ["dos hook posttool --workspace ."]
    assert _flat_commands(cfg, "Stop") == ["dos hook stop --workspace ."]
    group = cfg["hooks"]["PreToolUse"][0]
    assert set(group) == {"hooks"}
    assert group["hooks"][0]["type"] == "command"
    # The Cowork-specific coverage note is surfaced: the product does not FIRE hooks
    # yet (anthropics/claude-code#63360) — the Codex coverage-limit precedent.
    assert "63360" in proc.stdout
    assert "Cowork" in proc.stdout


def test_claude_cowork_and_claude_code_share_one_set_of_hooks(tmp_path: Path):
    """Wiring either host name wires BOTH runtimes — one file, one set of hooks.
    A second install under the sibling name is the idempotent path ('already'),
    never a duplicate entry, in both orders."""
    for first, second in (("claude-code", "claude-cowork"),
                          ("claude-cowork", "claude-code")):
        dest = tmp_path / f"{first}-then-{second}"
        assert _cli("init", "--hooks", first, str(dest)).returncode == 0
        proc = _cli("init", "--hooks", second, str(dest))
        assert proc.returncode == 0, proc.stderr
        assert "untouched" in proc.stdout
        cfg = _json_config(dest, hi.host_spec(second))
        for ev in ("PreToolUse", "PostToolUse", "Stop"):
            dos_cmds = [c for c in _flat_commands(cfg, ev) if c.startswith("dos hook ")]
            assert len(dos_cmds) == 1, (first, second, ev, dos_cmds)


def test_claude_code_via_hooks_flag_matches_with_hooks(tmp_path: Path):
    """`--hooks claude-code` and `--with-hooks` produce the IDENTICAL CC file."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    assert _cli("init", "--hooks", "claude-code", str(a)).returncode == 0
    assert _cli("init", "--with-hooks", str(b)).returncode == 0
    fa = (a / ".claude" / "settings.json").read_text(encoding="utf-8")
    fb = (b / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert fa == fb
    # And the CC command carries NO --dialect (it is the default) — the parity floor.
    cfg = json.loads(fa)
    assert _flat_commands(cfg, "PreToolUse") == ["dos hook pretool --workspace ."]


# ---------------------------------------------------------------------------
# Merge, idempotency, repair — across formats.
# ---------------------------------------------------------------------------
def test_cursor_merge_preserves_user_hooks_and_keys(tmp_path: Path):
    dest = tmp_path / "svc"
    dest.mkdir()
    cur = dest / ".cursor"
    cur.mkdir()
    (cur / "hooks.json").write_text(json.dumps({
        "version": 1,
        "hooks": {
            "beforeShellExecution": [{"command": "my-own-guard.sh"}],
            "afterShellExecution": [{"command": "my-audit.sh"}],
        },
    }), encoding="utf-8")
    assert _cli("init", "--hooks", "cursor", str(dest)).returncode == 0
    cfg = _json_config(dest, hi.host_spec("cursor"))
    # The user's own guard survives ALONGSIDE the DOS one (merge, not clobber).
    shell = _flat_commands(cfg, "beforeShellExecution")
    assert "my-own-guard.sh" in shell
    assert "dos hook pretool --workspace . --dialect cursor" in shell
    # The user's unrelated event is untouched.
    assert _flat_commands(cfg, "afterShellExecution") == ["my-audit.sh"]


def test_idempotent_rerun_does_not_duplicate(tmp_path: Path):
    for host in ("cursor", "codex", "gemini", "antigravity", "claude-cowork"):
        dest = tmp_path / host
        assert _cli("init", "--hooks", host, str(dest)).returncode == 0
        proc = _cli("init", "--hooks", host, str(dest))
        assert proc.returncode == 0, proc.stderr
        assert "untouched" in proc.stdout
        spec = hi.host_spec(host)
        if spec.fmt is hi.ConfigFormat.TOML:
            text = dest.joinpath(*spec.config_path).read_text(encoding="utf-8")
            # Exactly one DOS block, one DOS pretool command.
            assert text.count(hi.TOML_FENCE_OPEN) == 1
            assert text.count("dos hook pretool") == 1
        else:
            cfg = _json_config(dest, spec)
            for ev in spec.pre_events:
                dos_cmds = [c for c in _flat_commands(cfg, ev) if c.startswith("dos hook ")]
                assert len(dos_cmds) == 1, (host, ev, dos_cmds)


def test_force_repairs_without_duplicating(tmp_path: Path):
    # Codex: a second --force run strips the old fenced block and re-adds ONE.
    dest = tmp_path / "cdx"
    assert _cli("init", "--hooks", "codex", str(dest)).returncode == 0
    assert _cli("init", "--hooks", "codex", "--force", str(dest)).returncode == 0
    text = dest.joinpath(".codex", "config.toml").read_text(encoding="utf-8")
    assert text.count(hi.TOML_FENCE_OPEN) == 1
    parsed = tomllib.loads(text)
    pre = [c for g in parsed["hooks"]["PreToolUse"] for h in g["hooks"] for c in [h["command"]]]
    assert pre.count("dos hook pretool --workspace . --dialect codex") == 1


def test_malformed_json_is_reported_then_force_rescues(tmp_path: Path):
    dest = tmp_path / "svc"
    dest.mkdir()
    cur = dest / ".cursor"
    cur.mkdir()
    (cur / "hooks.json").write_text("{ not valid json", encoding="utf-8")
    # Without --force, a malformed file is a reported error (exit 1), never lost.
    proc = _cli("init", "--hooks", "cursor", str(dest))
    assert proc.returncode == 1
    assert "valid json" in proc.stderr.lower()
    # With --force it is rescued and the DOS hooks are written.
    proc = _cli("init", "--hooks", "cursor", "--force", str(dest))
    assert proc.returncode == 0, proc.stderr
    cfg = _json_config(dest, hi.host_spec("cursor"))
    assert _flat_commands(cfg, "stop") == ["dos hook stop --workspace . --dialect cursor"]


def test_works_on_already_initd_workspace(tmp_path: Path):
    dest = tmp_path / "svc"
    assert _cli("init", str(dest)).returncode == 0          # dos.toml only
    assert (dest / "dos.toml").exists()
    proc = _cli("init", "--hooks", "gemini", str(dest))     # add hooks after the fact
    assert proc.returncode == 0, proc.stderr
    assert "wired 3 DOS hook(s)" in proc.stdout


# ---------------------------------------------------------------------------
# The litmus tests.
# ---------------------------------------------------------------------------
def test_unknown_host_fails_loud(tmp_path: Path):
    proc = _cli("init", "--hooks", "bogus", str(tmp_path / "svc"))
    # argparse rejects the choice (usage error, exit 2) — never a silent no-op.
    assert proc.returncode == 2
    assert "invalid choice" in proc.stderr.lower()
    # No config file was written anywhere.
    assert not (tmp_path / "svc" / ".cursor").exists()


def test_no_host_ever_writes_a_tool_input_rewrite_key(tmp_path: Path):
    """The docs/191 §4 byte-author floor, enforced at the install layer for all hosts.

    The installer wires only the deny/observe verbs; it must never write a host's
    tool-input REWRITE key (minting a corrective argument for the agent is forbidden).
    """
    forbidden = ("updatedInput", "updated_input", "updatedCommand", "modifiedInput")
    for host in hi.host_names():
        dest = tmp_path / host
        assert _cli("init", "--hooks", host, str(dest)).returncode == 0
        text = dest.joinpath(*hi.host_spec(host).config_path).read_text(encoding="utf-8")
        for key in forbidden:
            assert key not in text, (host, key)


def test_host_spec_resolver_is_fail_loud():
    """The pure resolver raises on an unknown host (the programmatic guarantee)."""
    import pytest
    with pytest.raises(ValueError, match="unknown hook host"):
        hi.host_spec("nope")
    # Every advertised choice resolves.
    for name in hi.host_names():
        assert hi.host_spec(name).host == name


def test_trae_install_spec_is_a_deliberate_absence(tmp_path: Path):
    """Trae has NO hook surface, so `--hooks trae` must keep failing LOUD (docs/294).

    Proved out 2026-06-10: ByteDance's Trae (IDE / SOLO / TRAE CLI) ships no
    lifecycle hook system — no events, no stdout JSON contract, no exit-code
    semantics. A `trae_install_spec` would let `dos init --hooks trae` write a
    config file Trae never reads: FAKE enforcement, the silent fail-open docs/217
    fails loud to prevent. Trae's real surfaces (MCP via .trae/mcp.json, rules,
    skills) are advisory and documented in docs/294 §3. If this test bothers you
    because Trae just shipped hooks: re-run the docs/294 §1 probe, implement the
    docs/269 playbook, and delete this pin consciously."""
    import pytest
    with pytest.raises(ValueError, match="unknown hook host"):
        hi.host_spec("trae")
    assert "trae" not in hi.host_names()
    proc = _cli("init", "--hooks", "trae", str(tmp_path / "svc"))
    assert proc.returncode == 2
    assert "invalid choice" in proc.stderr.lower()
    # And nothing Trae-shaped was written — no fake .trae/hooks.json.
    assert not (tmp_path / "svc" / ".trae").exists()


# ---------------------------------------------------------------------------
# Read-only detection (the `dos doctor` "runtime hooks" line, docs/221).
# ---------------------------------------------------------------------------
def test_wired_events_detect_inverts_the_merge(tmp_path: Path):
    """`wired_events_*` reports exactly the events a fresh install wired — the inverse
    of the merge, the read-only signal behind `dos doctor`."""
    # JSON host (cursor): detect finds nothing before, all events after.
    cur = hi.host_spec("cursor")
    assert hi.wired_events_json({}, cur) == []
    merged, _, _ = hi.merge_json({}, cur)
    detected = hi.wired_events_json(merged, cur)
    assert set(detected) == {"beforeShellExecution", "beforeMCPExecution", "afterFileEdit", "stop"}
    # A config with the user's OWN hook but no DOS one detects nothing (no false +).
    user_only = {"version": 1, "hooks": {"beforeShellExecution": [{"command": "mine.sh"}]}}
    assert hi.wired_events_json(user_only, cur) == []
    # TOML host (codex): the fence presence means all events are wired.
    cdx = hi.host_spec("codex")
    assert hi.wired_events_toml("model = 'x'\n", cdx) == []
    text, _, _ = hi.merge_toml("", cdx)
    assert set(hi.wired_events_toml(text, cdx)) == {"PreToolUse", "PostToolUse", "Stop"}


def test_doctor_reports_runtime_hook_binding(tmp_path: Path):
    """`dos doctor` (text + --json) reports which runtimes have the DOS hooks wired —
    so an adopter can confirm `dos init --hooks` took effect (a mis-wire is a silent
    no-op otherwise)."""
    import json as _json
    dest = tmp_path / "svc"
    assert _cli("init", str(dest)).returncode == 0
    # Before wiring: text says none wired (with the actionable hint); JSON is all-empty.
    before = _cli("doctor", "--workspace", str(dest))
    assert "runtime hooks" in before.stdout
    assert "none wired" in before.stdout and "dos init --hooks" in before.stdout
    # Wire two hosts.
    assert _cli("init", "--hooks", "cursor", str(dest)).returncode == 0
    assert _cli("init", "--hooks", "gemini", str(dest)).returncode == 0
    after = _cli("doctor", "--workspace", str(dest))
    assert "cursor (4)" in after.stdout      # 2 PRE + post + stop
    assert "gemini (3)" in after.stdout
    # JSON parity: the wired events are listed per host; un-wired hosts are [].
    js = _cli("doctor", "--workspace", str(dest), "--json")
    rh = _json.loads(js.stdout)["runtime_hooks"]
    assert set(rh["cursor"]) == {"beforeShellExecution", "beforeMCPExecution", "afterFileEdit", "stop"}
    assert set(rh["gemini"]) == {"BeforeTool", "AfterTool", "AfterAgent"}
    assert rh["codex"] == [] and rh["claude-code"] == []


def test_doctor_runtime_hooks_is_read_only(tmp_path: Path):
    """Probing for hook status writes NO config file (doctor's read-only contract)."""
    dest = tmp_path / "svc"
    assert _cli("init", str(dest)).returncode == 0
    _cli("doctor", "--workspace", str(dest))
    _cli("doctor", "--workspace", str(dest), "--json")
    # No host config file was created by the probe.
    for host in hi.host_names():
        assert not dest.joinpath(*hi.host_spec(host).config_path).exists(), host
