"""Tests for `dos hook live-rotate` — the proactive live-boundary rehome hook (docs/391).

Drive the hook exactly as the wired Stop-hook command does: feed a host event on STDIN and
call ``cli.main(["hook", "live-rotate", ...])``. Pin the load-bearing behaviours:

  * a seat the live signal says is WALLED/NEAR_CAP, with a serving alternate → the rotation
    handoff is written EARLY (before any death) + a same-window `--resume` line is surfaced;
  * a HEALTHY session (no live signal) → NOTHING happens (no handoff, no output) — the
    fail-open floor that keeps the fleet from churning on phantom walls;
  * an unknown seat / a single-account roster / a seat not in the roster → no-op, exit 0.

Run:  python -m pytest tests/test_hook_live_rotate.py -q
"""
from __future__ import annotations

import io
import json
from pathlib import Path

from dos import breaker as _brk
from dos import cli
from dos import config as _config
from dos import rotation_handoff as _rh
from dos import stop_failure_sensor as _sfs

NEAR = 9_999_999_999_000  # far-future expiresAt (enrolled, unexpired)


def _enroll(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "tok-" + config_dir.name,
                                       "expiresAt": NEAR}}),
        encoding="utf-8",
    )


def _roster(tmp: Path, names) -> Path:
    lines = ["accounts:"]
    for n in names:
        cdir = tmp / n
        _enroll(cdir)
        lines.append(f"  - name: {n}")
        lines.append(f"    config_dir: {cdir.as_posix()}")
    lines += ["rotation:", "  near_cap_util: 0.9"]
    p = tmp / "accounts.yaml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _seed_breaker(workspace: Path, sid: str, consecutive: int) -> None:
    cfg = _config.default_config(workspace)
    _sfs.save_counts(sid, _brk.BreakerCounts(consecutive=consecutive, total=consecutive), cfg)


def _run(monkeypatch, capsys, event: dict, *argv: str) -> "tuple[int, str]":
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    rc = cli.main(["hook", "live-rotate", *argv])
    out = capsys.readouterr().out
    return rc, out


def _ws(tmp: Path) -> Path:
    ws = tmp / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


# --------------------------------------------------------------------------- #
# The rotate path — proactive handoff + continuation
# --------------------------------------------------------------------------- #
def test_walled_seat_writes_handoff_and_surfaces_continuation(tmp_path, monkeypatch, capsys):
    roster = _roster(tmp_path, ["seatA", "seatB"])
    ws = _ws(tmp_path)
    _seed_breaker(ws, "sess1", consecutive=3)  # breaker pressure → walled
    rc, out = _run(
        monkeypatch, capsys,
        {"session_id": "sess1", "account": "seatA", "cwd": str(ws)},
        "--workspace", str(ws), "--accounts-file", str(roster),
    )
    assert rc == 0
    assert "live-rotate" in out and "seatB" in out
    assert "claude --resume sess1" in out
    # The handoff was written EARLY (proactively), targeting the serving alternate.
    cfg = _config.default_config(ws)
    h = _rh.read_handoff(cfg, "sess1")
    assert h is not None and h.to_account == "seatB" and h.from_account == "seatA"


def test_rotate_records_the_leaving_seat_in_its_ledger(tmp_path, monkeypatch, capsys):
    from dos import account_ledger as _al

    roster = _roster(tmp_path, ["seatA", "seatB"])
    ws = _ws(tmp_path)
    _seed_breaker(ws, "sess1", consecutive=3)
    _run(monkeypatch, capsys,
         {"session_id": "sess1", "account": "seatA", "cwd": str(ws)},
         "--workspace", str(ws), "--accounts-file", str(roster))
    cfg = _config.default_config(ws)
    summ = _al.summary(cfg, "seatA")
    assert summ["failures"] >= 1


def test_near_cap_via_token_budget_rotates(tmp_path, monkeypatch, capsys):
    roster = _roster(tmp_path, ["seatA", "seatB"])
    ws = _ws(tmp_path)
    # No breaker pressure; instead a configured window cap + a transcript over near_cap.
    tx = tmp_path / "transcript.jsonl"
    tx.write_text(
        json.dumps({"type": "assistant",
                    "message": {"usage": {"output_tokens": 9500}}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_WINDOW_TOKEN_CAP", "10000")
    rc, out = _run(
        monkeypatch, capsys,
        {"session_id": "sess1", "account": "seatA", "cwd": str(ws),
         "transcript_path": str(tx)},
        "--workspace", str(ws), "--accounts-file", str(roster),
    )
    assert rc == 0
    assert "seatB" in out and "95%" in out  # 9500/10000 surfaced as near-cap util


# --------------------------------------------------------------------------- #
# The fail-open floor — a healthy session is NEVER rotated
# --------------------------------------------------------------------------- #
def test_healthy_session_does_nothing(tmp_path, monkeypatch, capsys):
    roster = _roster(tmp_path, ["seatA", "seatB"])
    ws = _ws(tmp_path)
    rc, out = _run(
        monkeypatch, capsys,
        {"session_id": "clean", "account": "seatA", "cwd": str(ws)},
        "--workspace", str(ws), "--accounts-file", str(roster),
    )
    assert rc == 0
    assert out.strip() == ""  # no signal → no rotation, no noise
    cfg = _config.default_config(ws)
    assert _rh.read_handoff(cfg, "clean") is None


def test_unknown_seat_is_skipped(tmp_path, monkeypatch, capsys):
    roster = _roster(tmp_path, ["seatA", "seatB"])
    ws = _ws(tmp_path)
    _seed_breaker(ws, "sess1", consecutive=3)
    # No "account" in the event and no CID_ACCOUNT → cannot attribute → skip.
    monkeypatch.delenv("CID_ACCOUNT", raising=False)
    rc, out = _run(
        monkeypatch, capsys,
        {"session_id": "sess1", "cwd": str(ws)},
        "--workspace", str(ws), "--accounts-file", str(roster),
    )
    assert rc == 0 and out.strip() == ""


def test_single_account_roster_does_not_rotate(tmp_path, monkeypatch, capsys):
    roster = _roster(tmp_path, ["seatA"])  # the only seat — nowhere to go
    ws = _ws(tmp_path)
    _seed_breaker(ws, "sess1", consecutive=3)
    rc, out = _run(
        monkeypatch, capsys,
        {"session_id": "sess1", "account": "seatA", "cwd": str(ws)},
        "--workspace", str(ws), "--accounts-file", str(roster),
    )
    assert rc == 0 and out.strip() == ""
    cfg = _config.default_config(ws)
    assert _rh.read_handoff(cfg, "sess1") is None  # NO_ALTERNATE → no handoff


def test_seat_not_in_roster_is_skipped(tmp_path, monkeypatch, capsys):
    roster = _roster(tmp_path, ["seatA", "seatB"])
    ws = _ws(tmp_path)
    _seed_breaker(ws, "sess1", consecutive=3)
    rc, out = _run(
        monkeypatch, capsys,
        {"session_id": "sess1", "account": "ghost-seat", "cwd": str(ws)},
        "--workspace", str(ws), "--accounts-file", str(roster),
    )
    assert rc == 0 and out.strip() == ""


def test_bad_stdin_is_failsoft(tmp_path, monkeypatch, capsys):
    roster = _roster(tmp_path, ["seatA", "seatB"])
    ws = _ws(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("not json {{{"))
    rc = cli.main(["hook", "live-rotate", "--workspace", str(ws),
                   "--accounts-file", str(roster)])
    assert rc == 0  # advisory fail-to-silence
