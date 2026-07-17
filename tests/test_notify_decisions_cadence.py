"""Pins the scheduled `dos notify decisions` wrapper for issue #22.

The script is tooling under scripts/, so load it by path like the other script
tests. These tests assert command construction only; no transport is contacted.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "notify_decisions_cadence.py"
_SPEC = importlib.util.spec_from_file_location("notify_decisions_cadence", _SCRIPT)
cadence = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(cadence)


def _cmd(argv=None, env=None):
    return cadence.build_command(cadence.parse_args(argv or []), env or {})


def test_default_is_null_dry_run_canary():
    assert _cmd() == [
        "dos",
        "notify",
        "decisions",
        "--workspace",
        ".",
        "--notifier",
        "null",
        "--top",
        "5",
        "--dry-run",
    ]


def test_real_notifier_env_delivers_by_default():
    cmd = _cmd(env={
        "DOS_NOTIFY_DECISIONS_NOTIFIER": "webhook",
        "DOS_NOTIFY_DECISIONS_URL": "https://example.invalid/hook",
        "DOS_NOTIFY_DECISIONS_TOKEN": "secret",
        "DOS_NOTIFY_DECISIONS_TOP": "3",
    })
    assert cmd == [
        "dos",
        "notify",
        "decisions",
        "--workspace",
        ".",
        "--notifier",
        "webhook",
        "--top",
        "3",
        "--url",
        "https://example.invalid/hook",
        "--token",
        "secret",
    ]


def test_dry_run_env_keeps_real_notifier_non_sending():
    cmd = _cmd(env={
        "DOS_NOTIFY_DECISIONS_NOTIFIER": "webhook",
        "DOS_NOTIFY_DECISIONS_URL": "https://example.invalid/hook",
        "DOS_NOTIFY_DECISIONS_DRY_RUN": "true",
    })
    assert "--dry-run" in cmd
    assert "--url" in cmd


def test_send_flag_overrides_dry_run_env():
    cmd = _cmd(["--notifier", "webhook", "--url", "https://example.invalid/hook", "--send"],
               env={"DOS_NOTIFY_DECISIONS_DRY_RUN": "1"})
    assert "--dry-run" not in cmd
    assert cmd[-2:] == ["--url", "https://example.invalid/hook"]


def test_all_rows_and_json_are_opt_in():
    cmd = _cmd(env={
        "DOS_NOTIFY_DECISIONS_ALL": "yes",
        "DOS_NOTIFY_DECISIONS_JSON": "1",
    })
    assert "--all" in cmd
    assert "--json" in cmd


def test_main_returns_subprocess_exit_code(monkeypatch):
    seen = {}

    class Result:
        returncode = 17

    def fake_run(command, check):
        seen["command"] = command
        seen["check"] = check
        return Result()

    monkeypatch.setattr(cadence.subprocess, "run", fake_run)
    rc = cadence.main(["--workspace", "repo"], env={})
    assert rc == 17
    assert seen == {
        "command": [
            "dos", "notify", "decisions", "--workspace", "repo",
            "--notifier", "null", "--top", "5", "--dry-run",
        ],
        "check": False,
    }


def test_bad_boolean_env_is_usage_error():
    rc = cadence.main([], env={"DOS_NOTIFY_DECISIONS_DRY_RUN": "maybe"})
    assert rc == 2
