"""Native Codex hook regression fixtures and Windows launcher behavior.

Issue anthony-chaudhary/fak#7212 failed before DOS ran: on Windows, Codex
selected the POSIX ``command`` for PreToolUse/PostToolUse because the manifest
had no ``commandWindows`` override. These tests pin the host envelope and the
shell-launch seam independently of the Python component probes.
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "claude-plugin" / "hooks" / "hooks.json"
ADAPTER = ROOT / "claude-plugin" / "bin" / "dos-hook-codex.ps1"
FIXTURES = ROOT / "tests" / "fixtures" / "codex_hooks"


def _manifest_hook(event: str) -> dict[str, object]:
    manifest = json.loads(HOOKS.read_text(encoding="utf-8"))
    return manifest["hooks"][event][0]["hooks"][0]


def _fixture(name: str, workspace: Path) -> dict[str, object]:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    payload["cwd"] = str(workspace)
    payload["transcript_path"] = str(workspace / ".codex" / "rollout.jsonl")
    return payload


def _native_hook_binary() -> Path:
    system = {
        "linux": "linux",
        "darwin": "darwin",
        "win32": "windows",
    }.get(os.sys.platform)
    if system is None:
        pytest.skip(f"no bundled hook binary for {os.sys.platform}")

    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
    suffix = ".exe" if system == "windows" else ""
    binary = ROOT / "claude-plugin" / "bin" / f"dos-hook-{system}-{arch}{suffix}"
    if not binary.is_file():
        pytest.skip(f"bundled hook binary absent: {binary.name}")
    return binary


def _run_native(
    verb: str,
    payload: dict[str, object] | str,
    workspace: Path,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [str(_native_hook_binary()), verb, "--workspace", str(workspace)],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def _run_windows_adapter(
    verb: str,
    payload: dict[str, object],
    workspace: Path,
    *,
    adapter: Path = ADAPTER,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(adapter),
            verb,
            "--workspace",
            str(workspace),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def _run_manifest_windows_command(
    event: str,
    payload: dict[str, object],
    workspace: Path,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = _manifest_hook(event)["commandWindows"]
    assert isinstance(command, str)
    command_env = os.environ.copy() if env is None else env.copy()
    plugin_root = str(ROOT / "claude-plugin")
    command_env["PLUGIN_ROOT"] = plugin_root
    command_env["CODEX_PLUGIN_ROOT"] = plugin_root
    expanded = command.replace("${CODEX_PLUGIN_ROOT}", plugin_root)
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", expanded],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        cwd=workspace,
        env=command_env,
    )


def _load_build_plugin():
    path = ROOT / "scripts" / "build_plugin.py"
    spec = importlib.util.spec_from_file_location("build_plugin_codex_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _structural_deny_fixture(workspace: Path) -> tuple[dict[str, object], dict[str, str]]:
    runtime_file = workspace / "src" / "dos" / "_tree.py"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_text("# structural deny fixture\n", encoding="utf-8")
    payload = _fixture("pretool-shell.json", workspace)
    payload["tool_input"] = {"command": "rm src/dos/_tree.py"}
    env = os.environ.copy()
    env["DOS_LOOP"] = "1"
    return payload, env


@pytest.mark.parametrize(
    ("event", "verb"),
    [("PreToolUse", "pretool"), ("PostToolUse", "posttool")],
)
def test_windows_tool_hooks_use_versioned_codex_adapter(event: str, verb: str):
    hook = _manifest_hook(event)
    command = hook["commandWindows"]
    assert isinstance(command, str)
    assert "dos-hook-codex.ps1" in command
    assert f") {verb} --workspace ." in command
    assert "${" not in command
    assert "command -p sh" not in command


def test_windows_stop_family_uses_versioned_codex_adapter():
    manifest = json.loads(HOOKS.read_text(encoding="utf-8"))
    stop_hooks = manifest["hooks"]["Stop"][0]["hooks"]
    assert len(stop_hooks) == 1
    assert "stop-transaction --workspace . --dialect codex" in stop_hooks[0]["commandWindows"]
    assert "stop-failure --success" in stop_hooks[0]["command"]
    assert "live-rotate" in stop_hooks[0]["command"]

    subagent_hooks = manifest["hooks"]["SubagentStop"][0]["hooks"]
    assert len(subagent_hooks) == 1
    assert "stop --workspace . --dialect codex" in subagent_hooks[0]["commandWindows"]

def test_launcher_reachability_counts_command_windows():
    build_plugin = _load_build_plugin()
    commands = build_plugin._hooks_command_text(ROOT)
    assert "dos-hook-codex.ps1" in commands


@pytest.mark.parametrize(
    ("fixture_name", "verb"),
    [
        ("pretool-shell.json", "pretool"),
        ("posttool-shell.json", "posttool"),
        ("stop.json", "stop"),
        ("subagent-stop.json", "stop"),
    ],
)
def test_native_codex_envelopes_replay_healthy(
    fixture_name: str,
    verb: str,
    tmp_path: Path,
):
    payload = _fixture(fixture_name, tmp_path)
    result = _run_native(verb, payload, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_installed_profile_live_smoke_witness_is_complete():
    witness = json.loads((FIXTURES / "live-smoke.json").read_text(encoding="utf-8"))

    assert witness["schema"] == "dos.codex-hook-live-smoke.v1"
    assert witness["codex_cli"] == "0.147.0"
    assert witness["plugin"] == {
        "id": "dos-kernel@dos-7212-smoke",
        "version": "0.30.0",
        "marketplace": "dos-7212-smoke",
        "source_revision": "ea5a9dde58051cdeb1075c86740e143710947056",
        "installed_manifest_sha256": (
            "b900587b713c8927ddcf38d29ad8a118c25887035846f67f30a1b62dd160c728"
        ),
        "installed_adapter_sha256": (
            "dc34a6fc2149017206f9db7ed031e6a759f711a98384d1dd79f991bc7dca0cfe"
        ),
        "trust_status": "trusted",
        "hook_trust_bypass": False,
    }
    assert witness["command_exit_code"] == 0
    assert witness["sequence"] == [
        {
            "kind": "hook",
            "event": "preToolUse",
            "status": "completed",
            "source": "plugin",
        },
        {
            "kind": "commandExecution",
            "status": "completed",
            "exit_code": 0,
        },
        {
            "kind": "hook",
            "event": "postToolUse",
            "status": "completed",
            "source": "plugin",
        },
    ]


def test_native_codex_pretool_structural_deny_is_host_json_before_effect(tmp_path: Path):
    payload, env = _structural_deny_fixture(tmp_path)

    result = _run_native("pretool", payload, tmp_path, env=env)

    # DOS intentionally uses Codex's structured deny channel at exit 0. A
    # process exit 2 would also deny, but would discard the typed reason and
    # conflate a policy verdict with a launcher failure.
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    hook_output = output["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "PreToolUse"
    assert hook_output["permissionDecision"] == "deny"
    assert "SELF_MODIFY" in hook_output["permissionDecisionReason"]


@pytest.mark.skipif(os.name != "nt", reason="PowerShell adapter is Windows-only")
def test_windows_adapter_structural_deny_uses_codex_blocking_exit(tmp_path: Path):
    payload, env = _structural_deny_fixture(tmp_path)

    result = _run_windows_adapter("pretool", payload, tmp_path, env=env)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "SELF_MODIFY" in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="commandWindows is Windows-only")
@pytest.mark.parametrize(
    ("event", "fixture_name"),
    [
        ("PreToolUse", "pretool-shell.json"),
        ("PostToolUse", "posttool-shell.json"),
        ("Stop", "stop.json"),
        ("SubagentStop", "subagent-stop.json"),
    ],
)
def test_manifest_command_windows_replays_healthy_native_envelopes(
    event: str,
    fixture_name: str,
    tmp_path: Path,
):
    result = _run_manifest_windows_command(
        event,
        _fixture(fixture_name, tmp_path),
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.skipif(os.name != "nt", reason="commandWindows is Windows-only")
def test_manifest_command_windows_denies_before_effect(tmp_path: Path):
    payload, env = _structural_deny_fixture(tmp_path)

    result = _run_manifest_windows_command(
        "PreToolUse",
        payload,
        tmp_path,
        env=env,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "SELF_MODIFY" in result.stderr


@pytest.mark.parametrize(
    ("verb", "payload"),
    [
        ("pretool", "{not-json"),
        ("posttool", "{not-json"),
        (
            "pretool",
            json.dumps(
                {
                    "hook_event_name": "UnknownToolEvent",
                    "tool_name": "Bash",
                    "tool_input": {"command": "git push origin master"},
                }
            ),
        ),
    ],
)
def test_native_codex_malformed_or_unknown_input_is_explicit_fail_open(
    verb: str,
    payload: str,
    tmp_path: Path,
):
    result = _run_native(verb, payload, tmp_path)
    assert result.returncode == 0
    assert result.stdout == ""


@pytest.mark.skipif(os.name != "nt", reason="PowerShell adapter is Windows-only")
@pytest.mark.parametrize(
    ("fixture_name", "verb"),
    [
        ("pretool-shell.json", "pretool"),
        ("posttool-shell.json", "posttool"),
        ("stop.json", "stop"),
        ("subagent-stop.json", "stop"),
    ],
)
def test_windows_adapter_replays_native_codex_envelopes(
    fixture_name: str,
    verb: str,
    tmp_path: Path,
):
    payload = _fixture(fixture_name, tmp_path)
    result = _run_windows_adapter(verb, payload, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.skipif(os.name != "nt", reason="PowerShell adapter is Windows-only")
@pytest.mark.parametrize("verb", ["pretool", "posttool", "stop", "stop-failure", "live-rotate"])
def test_windows_adapter_errors_are_typed_and_nonblocking(
    verb: str,
    tmp_path: Path,
):
    isolated = tmp_path / "bin"
    isolated.mkdir()
    adapter = isolated / ADAPTER.name
    shutil.copyfile(ADAPTER, adapter)
    powershell = (
        Path(os.environ["SystemRoot"])
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    fixture_name = {
        "pretool": "pretool-shell.json",
        "posttool": "posttool-shell.json",
        "stop": "stop.json",
        "stop-failure": "stop-failure.json",
        "live-rotate": "stop.json",
    }[verb]
    payload = _fixture(fixture_name, tmp_path)
    env = os.environ.copy()
    env["PATH"] = ""

    result = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(adapter),
            verb,
            "--workspace",
            str(tmp_path),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    diagnostic = json.loads(result.stderr)
    assert diagnostic == {
        "schema": "dos.codex-hook-diagnostic.v1",
        "adapter_version": 1,
        "hook": verb,
        "stage": "executable_selection",
        "backend": None,
        "exit_code": 127,
        "posture": "fail_open",
    }


@pytest.mark.skipif(os.name != "nt", reason="PowerShell adapter is Windows-only")
def test_windows_stop_adapter_delegates_native_exit_three_to_python(
    tmp_path: Path,
):
    isolated = tmp_path / "bin"
    isolated.mkdir()
    adapter = isolated / ADAPTER.name
    shutil.copyfile(ADAPTER, adapter)

    fake_native = tmp_path / "fake-native.ps1"
    fake_native.write_text("exit 3\n", encoding="utf-8")
    fake_site = tmp_path / "fake-site" / "dos"
    fake_site.mkdir(parents=True)
    (fake_site / "__init__.py").write_text("", encoding="utf-8")
    (fake_site / "cli.py").write_text(
        'import json; print(json.dumps({"decision":"block","reason":"keep working"}))\n',
        encoding="utf-8",
    )
    adapter.write_text(
        adapter.read_text(encoding="utf-8").replace(
            '$native = Join-Path $selfDir "dos-hook-windows-$goarch.exe"',
            f"$native = '{fake_native}'",
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PATH"] = str(Path(sys.executable).parent)
    env["PYTHONPATH"] = str(tmp_path / "fake-site")
    result = _run_windows_adapter(
        "stop", _fixture("stop.json", tmp_path), tmp_path, adapter=adapter, env=env
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"decision": "block", "reason": "keep working"}
    assert result.stderr == ""

@pytest.mark.skipif(os.name != "nt", reason="PowerShell adapter is Windows-only")
@pytest.mark.parametrize(
    ("backend_source", "expected_stage", "expected_exit", "expected_stdout"),
    [
        ('print("not-json")', "backend_output", 65, ""),
        ('raise SystemExit(23)', "backend_policy", 23, ""),
        (
            'import json; print(json.dumps({"decision":"block","reason":"keep working"}))',
            None,
            0,
            '{"decision":"block","reason":"keep working"}',
        ),
    ],
)
def test_windows_stop_adapter_preserves_only_valid_backend_protocol(
    backend_source: str,
    expected_stage: str | None,
    expected_exit: int,
    expected_stdout: str,
    tmp_path: Path,
):
    isolated = tmp_path / "bin"
    isolated.mkdir()
    adapter = isolated / ADAPTER.name
    shutil.copyfile(ADAPTER, adapter)

    fake_site = tmp_path / "fake-site" / "dos"
    fake_site.mkdir(parents=True)
    (fake_site / "__init__.py").write_text("", encoding="utf-8")
    (fake_site / "cli.py").write_text(backend_source + "\n", encoding="utf-8")
    python_dir = Path(sys.executable).parent

    powershell = (
        Path(os.environ["SystemRoot"])
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    env = os.environ.copy()
    env["PATH"] = str(python_dir)
    env["PYTHONPATH"] = str(tmp_path / "fake-site")
    result = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(adapter),
            "stop",
            "--workspace",
            str(tmp_path),
            "--dialect",
            "codex",
        ],
        input=json.dumps(_fixture("stop.json", tmp_path)),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    assert result.stdout == expected_stdout
    if expected_stage is None:
        assert result.stderr == ""
    else:
        diagnostic = json.loads(result.stderr)
        assert diagnostic["schema"] == "dos.codex-hook-diagnostic.v1"
        assert diagnostic["hook"] == "stop"
        assert diagnostic["stage"] == expected_stage
        assert diagnostic["exit_code"] == expected_exit
        assert diagnostic["posture"] == "fail_open"
