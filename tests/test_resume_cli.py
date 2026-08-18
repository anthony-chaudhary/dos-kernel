"""docs/107 Phase 5 — the `dos resume` human actuator (advisory; §3.3, §8 non-goal 1, §5 req 4).

`dos resume --run-id RID` replays the run's intent ledger, re-verifies progress
against ancestry, and PROPOSES the continuation (residual + non-forgeable re-entry
SHA + the re-dispatch command). It NEVER executes — the kernel proposes; a driver/
operator enacts (the docs/99 advisory floor on the resume axis). Pins: the verdict
maps to the exit code, a RESUMABLE verdict idempotently records RESUME_PROPOSED, and
--no-record / an already-proposed run does not double-propose.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from dos import cli
from dos import config as _config
from dos import intent_ledger as il


def _resume_args(tmp_path: Path, **kw) -> argparse.Namespace:
    base = dict(workspace=str(tmp_path), run_id="RID-R", diverged=False,
                no_record=False, json=True, output=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_resume_no_ledger_is_unresumable(tmp_path: Path, capsys):
    rc = cli.cmd_resume(_resume_args(tmp_path, run_id="RID-NONE"))
    assert rc == cli._RESUME_EXIT_CODES["UNRESUMABLE"]
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "UNRESUMABLE"


def test_resume_missing_run_id_is_contract_error(tmp_path: Path):
    rc = cli.cmd_resume(_resume_args(tmp_path, run_id=""))
    assert rc == cli._RESUME_EXIT_CONTRACT_ERROR


def test_resume_records_resume_proposed_idempotently(tmp_path: Path):
    cfg = _config.default_config(tmp_path)
    # A run with a declared free-form goal and a start SHA → RESUMABLE (no steps).
    il.append("RID-R", il.intent_entry(goal="finish the thing", start_sha="START"),
              cfg=cfg)
    rc = cli.cmd_resume(_resume_args(tmp_path))
    assert rc == cli._RESUME_EXIT_CODES["RESUMABLE"]
    # A RESUME_PROPOSED was recorded on the ledger (idempotence, §5 req 4).
    state = il.replay(il.read_all("RID-R", cfg=cfg))
    assert state.resume_proposed == ("RID-R",)

    # A SECOND resume sees the existing proposal and does NOT double-propose.
    cli.cmd_resume(_resume_args(tmp_path))
    state2 = il.replay(il.read_all("RID-R", cfg=cfg))
    # Still exactly one predecessor recorded (the set dedupes), and only one
    # RESUME_PROPOSED entry total (the second run respected already_proposed).
    proposed_entries = [e for e in il.read_all("RID-R", cfg=cfg)
                        if e.get("op") == "RESUME_PROPOSED"]
    assert len(proposed_entries) == 1
    assert state2.resume_proposed == ("RID-R",)


def test_resume_no_record_inspects_without_proposing(tmp_path: Path):
    cfg = _config.default_config(tmp_path)
    il.append("RID-R", il.intent_entry(goal="g", start_sha="START"), cfg=cfg)
    cli.cmd_resume(_resume_args(tmp_path, no_record=True))
    entries = il.read_all("RID-R", cfg=cfg)
    assert all(e.get("op") != "RESUME_PROPOSED" for e in entries)


def test_resume_diverged_flag_refuses(tmp_path: Path, capsys):
    cfg = _config.default_config(tmp_path)
    il.append("RID-R", il.intent_entry(goal="g", plan="P", phase="phi",
                                       start_sha="START", declared_steps=["s1"]),
              cfg=cfg)
    rc = cli.cmd_resume(_resume_args(tmp_path, diverged=True))
    assert rc == cli._RESUME_EXIT_CODES["DIVERGED"]
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "DIVERGED"
    assert out["diverged_recorded_now"] is True
    # A DIVERGED verdict does NOT record a proposal; it records a human decision.
    entries = il.read_all("RID-R", cfg=cfg)
    assert all(e.get("op") != "RESUME_PROPOSED" for e in entries)
    assert [e.get("op") for e in entries].count("RESUME_DIVERGED") == 1
    assert il.replay(entries).resume_diverged == ("RID-R",)

    # A SECOND diverged read does not append another decision row.
    rc2 = cli.cmd_resume(_resume_args(tmp_path, diverged=True))
    assert rc2 == cli._RESUME_EXIT_CODES["DIVERGED"]
    out2 = json.loads(capsys.readouterr().out)
    assert out2["diverged_recorded_now"] is False
    assert out2["already_diverged"] is True
    entries2 = il.read_all("RID-R", cfg=cfg)
    assert [e.get("op") for e in entries2].count("RESUME_DIVERGED") == 1


def test_resume_text_output_prints_residual_and_proposal(tmp_path: Path, capsys):
    cfg = _config.default_config(tmp_path)
    il.append("RID-R", il.intent_entry(goal="g", start_sha="START"), cfg=cfg)
    cli.cmd_resume(_resume_args(tmp_path, json=False))
    out = capsys.readouterr().out
    assert "RESUMABLE" in out
    assert "propose" in out
    assert "dos loop dispatch --resume RID-R" in out


# --- end-to-end via the real CLI dispatcher (subprocess) -------------------


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _rev(cwd) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(cwd),
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not available",
)
def test_resume_cli_end_to_end_real_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    _git(["add", "a.txt"], repo)
    _git(["commit", "-q", "-m", "start"], repo)
    start = _rev(repo)
    (repo / "b.txt").write_text("y\n", encoding="utf-8")
    _git(["add", "b.txt"], repo)
    _git(["commit", "-q", "-m", "s1 real"], repo)
    s1_sha = _rev(repo)

    cfg = _config.default_config(repo)
    p = il.ledger_path_for("RID-E", cfg=cfg)
    il.append("RID-E", il.intent_entry(goal="g", start_sha=start,
                                       declared_steps=["s1", "s2"]), path=p)
    il.append("RID-E", il.step_claimed_entry("s1", s1_sha), path=p)
    il.append("RID-E", il.step_verified_entry("s1", s1_sha, via="file-path"), path=p)
    il.append("RID-E", il.step_claimed_entry("s2", "deadbeef"), path=p)  # never landed

    proc = subprocess.run(
        ["python", "-m", "dos.cli", "resume", "--workspace", str(repo),
         "--run-id", "RID-E", "--json"],
        capture_output=True, text=True,
    )
    assert proc.returncode == cli._RESUME_EXIT_CODES["RESUMABLE"], proc.stderr
    out = json.loads(proc.stdout)
    assert out["verdict"] == "RESUMABLE"
    assert out["verified"] == ["s1"]
    assert "s2" in out["residual"]          # claimed but not in ancestry → redo
    assert out["resume_sha"] == s1_sha
