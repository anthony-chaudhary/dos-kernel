"""FRS — the `dos beat` verb (record + --check), the cron dead-man's switch boundary.

`freshness.classify` is the pure verdict; these tests pin the CLI BOUNDARY that
records a beat to the JSONL ledger and folds it against the declared `[heartbeats]`
cadences. Direct `cmd_beat(namespace)` calls (fast, no subprocess) over an isolated
tmp workspace; the exit code is the meta-cron contract (0 clean, 1 on LATE/MISSING).
"""

from __future__ import annotations

import argparse
import json

import pytest

from dos import cli, config

_MIN = 60 * 1000
_HOUR = 60 * _MIN

_TOML = (
    'workspace = "."\n'
    "[heartbeats]\n"
    "grace_factor = 1.5\n"
    "[heartbeats.jobs]\n"
    'pulse = "6h"\n'
    'supervise = { cadence = "30m", critical = false }\n'
)


def _ws(tmp_path):
    (tmp_path / "dos.toml").write_text(_TOML, encoding="utf-8")
    return str(tmp_path)


def _args(workspace, **over):
    base = dict(
        workspace=workspace,
        job_id=None,
        check=False,
        fail=False,
        detail="",
        now_ms=None,
        json=False,
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_record_then_check_round_trip(tmp_path, capsys):
    ws = _ws(tmp_path)

    # 1. Check with no beats → every declared job MISSING, exit 1.
    rc = cli.cmd_beat(_args(ws, check=True, now_ms=1_000_000_000))
    assert rc == 1
    out = capsys.readouterr().out
    assert out.count("MISSING") == 2

    # 2. Record a pulse beat at T.
    rc = cli.cmd_beat(_args(ws, job_id="pulse", now_ms=1_000_000_000))
    assert rc == 0
    assert "beat recorded: pulse" in capsys.readouterr().out

    # 3. Check 1h later → pulse FRESH, supervise still MISSING, exit 1 (a problem remains).
    rc = cli.cmd_beat(_args(ws, check=True, now_ms=1_000_000_000 + _HOUR))
    assert rc == 1
    out = capsys.readouterr().out
    assert "FRESH" in out and "pulse" in out
    assert "MISSING" in out and "supervise" in out


def test_check_all_fresh_exits_zero(tmp_path, capsys):
    ws = _ws(tmp_path)
    now = 2_000_000_000
    cli.cmd_beat(_args(ws, job_id="pulse", now_ms=now))
    cli.cmd_beat(_args(ws, job_id="supervise", now_ms=now))
    capsys.readouterr()
    # A few minutes later, both are within their cadence → all FRESH → exit 0.
    rc = cli.cmd_beat(_args(ws, check=True, now_ms=now + 5 * _MIN))
    assert rc == 0
    out = capsys.readouterr().out
    assert "MISSING" not in out and "LATE" not in out
    assert out.count("FRESH") == 2


def test_check_json_shape(tmp_path, capsys):
    ws = _ws(tmp_path)
    now = 3_000_000_000
    cli.cmd_beat(_args(ws, job_id="pulse", now_ms=now))
    capsys.readouterr()
    rc = cli.cmd_beat(_args(ws, check=True, json=True, now_ms=now + _HOUR))
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["now_ms"] == now + _HOUR
    by_job = {v["job_id"]: v["verdict"] for v in payload["verdicts"]}
    assert by_job == {"pulse": "FRESH", "supervise": "MISSING"}


def test_record_missing_job_id_is_usage_error(tmp_path, capsys):
    ws = _ws(tmp_path)
    rc = cli.cmd_beat(_args(ws))  # no job_id, no --check
    assert rc == 2
    assert "provide a JOB-ID" in capsys.readouterr().err


def test_no_declared_heartbeats_is_clean(tmp_path, capsys):
    (tmp_path / "dos.toml").write_text('workspace = "."\n', encoding="utf-8")
    rc = cli.cmd_beat(_args(str(tmp_path), check=True, now_ms=1))
    assert rc == 0  # nothing declared → nothing to watch → clean
    assert "no declared heartbeats" in capsys.readouterr().out


def test_ledger_is_append_only_and_torn_tail_tolerant(tmp_path, capsys):
    ws = _ws(tmp_path)
    cli.cmd_beat(_args(ws, job_id="pulse", now_ms=100))
    cli.cmd_beat(_args(ws, job_id="pulse", now_ms=200, detail="second"))
    capsys.readouterr()

    cfg = config.load_workspace_config(ws, gather_env=False)
    ledger = cfg.paths.beat_ledger
    # Two beats appended (append-only — the first record is not overwritten).
    lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2
    # A half-written trailing line must not break the read (the WAL safe-reading).
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write('{"job": "pulse", "ts_ms": 30')  # torn — no newline, no close brace
    records = cli._read_beats(cfg)
    assert len(records) == 2  # the torn tail is skipped
    assert cli.cmd_beat(_args(ws, check=True, now_ms=300)) in (0, 1)  # no crash


# ---------------------------------------------------------------------------
# The cmd_pulse BOUNDARY freshness gather — a declared-but-silent cron surfaces
# in the standing self-watch digest (the dead-man's switch reaches the operator).
# ---------------------------------------------------------------------------


def _pulse_args(workspace, **over):
    base = dict(
        workspace=workspace, json=True, notifier="null", channel="", url="",
        token="", dry_run=False, start_sha="", no_proc=True, now_ms=None,
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_pulse_surfaces_silent_cron(tmp_path, capsys):
    (tmp_path / "dos.toml").write_text(
        'workspace = "."\n[heartbeats.jobs]\npulse = "6h"\n', encoding="utf-8")
    ws = str(tmp_path)
    # Declared but never beaten → pulse digest carries an URGENT cron_missing.
    rc = cli.cmd_pulse(_pulse_args(ws))
    assert rc == 0  # null notifier → success exit even when the digest is non-empty
    digest = json.loads(capsys.readouterr().out)["digest"]
    assert digest["cron_missing"] == 1
    assert digest["severity"] == "URGENT"
    assert any("CRON SILENT" in ln for ln in digest["lines"])


def test_pulse_quiet_after_beat(tmp_path, capsys):
    (tmp_path / "dos.toml").write_text(
        'workspace = "."\n[heartbeats.jobs]\npulse = "6h"\n', encoding="utf-8")
    ws = str(tmp_path)
    cli.cmd_beat(_args(ws, job_id="pulse"))
    capsys.readouterr()
    rc = cli.cmd_pulse(_pulse_args(ws))
    assert rc == 0
    digest = json.loads(capsys.readouterr().out)["digest"]
    assert digest["cron_missing"] == 0
    assert digest["empty"] is True
