"""Tests for `dos accounts rehome` — the live-rehome actuation nucleus (docs/391).

The verb both the hook and an operator/supervisor call: pick a serving alternate, build its
launch env, persist the rotation handoff, and emit the `--resume` continuation (PROPOSE by
default; `--exec` spawns the headless relaunch). Pin: the pick avoids the current seat, the
handoff lands, the resume command carries the rotated env, `--exec` spawns under that env,
and a walled roster fails cleanly rather than rehoming nowhere.

Run:  python -m pytest tests/test_accounts_rehome.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dos import cli
from dos import config as _config
from dos import rotation_handoff as _rh

NEAR = 9_999_999_999_000


def _enroll(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "t", "expiresAt": NEAR}}),
        encoding="utf-8",
    )


def _roster(tmp: Path, names, *, enroll=True) -> Path:
    lines = ["accounts:"]
    for n in names:
        cdir = tmp / n
        if enroll:
            _enroll(cdir)
        else:
            cdir.mkdir(parents=True, exist_ok=True)  # exists but no creds → needs_enroll
        lines.append(f"  - name: {n}")
        lines.append(f"    config_dir: {cdir.as_posix()}")
    lines += ["rotation:", "  near_cap_util: 0.9"]
    p = tmp / "accounts.yaml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _ws(tmp: Path) -> Path:
    ws = tmp / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _json(capsys) -> dict:
    out = capsys.readouterr().out
    return json.loads(out)


# --------------------------------------------------------------------------- #
# propose (default) — no spawn, just the plan
# --------------------------------------------------------------------------- #
def test_rehome_proposes_a_serving_alternate(tmp_path, capsys):
    roster = _roster(tmp_path, ["seatA", "seatB"])
    ws = _ws(tmp_path)
    rc = cli.main(["accounts", "rehome", "--session-id", "s1", "--from", "seatA",
                   "--accounts-file", str(roster), "--workspace", str(ws), "--json"])
    assert rc == 0
    d = _json(capsys)
    assert d["from"] == "seatA" and d["to"] == "seatB"
    assert d["env"]["CLAUDE_CONFIG_DIR"].endswith("seatB")
    assert d["argv"] == ["claude", "--resume", "s1"]
    assert "seatB" in d["resume_command"] and "--resume s1" in d["resume_command"]
    assert d["handoff_written"] is True
    assert d["execed_pid"] is None  # propose-only by default
    # the handoff actually landed
    h = _rh.read_handoff(_config.default_config(ws), "s1")
    assert h is not None and h.to_account == "seatB"


def test_rehome_from_defaults_to_cid_account_env(tmp_path, capsys, monkeypatch):
    roster = _roster(tmp_path, ["seatA", "seatB"])
    ws = _ws(tmp_path)
    monkeypatch.setenv("CID_ACCOUNT", "seatA")
    rc = cli.main(["accounts", "rehome", "--session-id", "s1",
                   "--accounts-file", str(roster), "--workspace", str(ws), "--json"])
    assert rc == 0
    d = _json(capsys)
    assert d["from"] == "seatA" and d["to"] == "seatB"  # picked the non-current seat


def test_rehome_custom_resume_template(tmp_path, capsys):
    roster = _roster(tmp_path, ["seatA", "seatB"])
    ws = _ws(tmp_path)
    rc = cli.main(["accounts", "rehome", "--session-id", "s1", "--from", "seatA",
                   "--resume-template", "claude -p --resume {session_id} --verbose",
                   "--accounts-file", str(roster), "--workspace", str(ws), "--json"])
    assert rc == 0
    d = _json(capsys)
    assert d["argv"] == ["claude", "-p", "--resume", "s1", "--verbose"]


# --------------------------------------------------------------------------- #
# --exec — the headless relaunch (spawn injected)
# --------------------------------------------------------------------------- #
def test_rehome_exec_spawns_under_the_rotated_env(tmp_path, capsys, monkeypatch):
    roster = _roster(tmp_path, ["seatA", "seatB"])
    ws = _ws(tmp_path)
    spawned = {}

    def _fake_popen(argv, env, cwd):
        spawned["argv"] = argv
        spawned["env"] = env
        spawned["cwd"] = cwd
        return 4242

    monkeypatch.setattr(cli, "_rehome_popen", _fake_popen)
    rc = cli.main(["accounts", "rehome", "--session-id", "s1", "--from", "seatA", "--exec",
                   "--accounts-file", str(roster), "--workspace", str(ws), "--json"])
    assert rc == 0
    d = _json(capsys)
    assert d["execed_pid"] == 4242
    # the child was spawned with the rotated seat's CLAUDE_CONFIG_DIR merged onto os.environ
    assert spawned["argv"] == ["claude", "--resume", "s1"]
    assert spawned["env"]["CLAUDE_CONFIG_DIR"].endswith("seatB")
    assert "PATH" in spawned["env"] or "Path" in spawned["env"]  # inherited os.environ


def test_rehome_exec_failure_is_reported_not_raised(tmp_path, capsys, monkeypatch):
    roster = _roster(tmp_path, ["seatA", "seatB"])
    ws = _ws(tmp_path)
    monkeypatch.setattr(cli, "_rehome_popen", lambda *a, **k: None)  # spawn failed
    rc = cli.main(["accounts", "rehome", "--session-id", "s1", "--from", "seatA", "--exec",
                   "--accounts-file", str(roster), "--workspace", str(ws)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "FAILED to spawn" in out
    # the handoff still landed — the operator can resume from it even if --exec failed
    assert _rh.read_handoff(_config.default_config(ws), "s1") is not None


# --------------------------------------------------------------------------- #
# failure modes
# --------------------------------------------------------------------------- #
def test_rehome_with_no_serving_alternate_fails_cleanly(tmp_path, capsys):
    # seatA is the only enrolled seat; seatB exists but has no creds (needs_enroll).
    roster = _roster(tmp_path, ["seatA"], enroll=True)
    ws = _ws(tmp_path)
    rc = cli.main(["accounts", "rehome", "--session-id", "s1", "--from", "seatA",
                   "--accounts-file", str(roster), "--workspace", str(ws)])
    assert rc != 0  # nothing serving to rehome to
    err = capsys.readouterr().out + capsys.readouterr().err


def test_rehome_requires_session_id(tmp_path):
    roster = _roster(tmp_path, ["seatA", "seatB"])
    ws = _ws(tmp_path)
    with pytest.raises(SystemExit):  # argparse: --session-id is required
        cli.main(["accounts", "rehome", "--from", "seatA",
                  "--accounts-file", str(roster), "--workspace", str(ws)])
