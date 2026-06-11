"""The `dos efficiency-trend` CLI boundary + the journal fossil loop (docs/300 P4).

Two evidence sources, exactly one required: `--samples` (caller-assembled
work:tokens pairs, oldest first — the `dos productivity --deltas` idiom) and
`--from-journal` (fold the verdict journal's recorded efficiency evidence).
The end-to-end test is the point of the phase: `--observe`d `dos efficiency`
verdicts fossilize their (work, tokens) counts to the journal, and the trend
verb reads them back — the first place a recorded verdict feeds a later one,
as evidence read at a boundary, never an adjudicator inside the journal.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run_cli(
    *args: str, cwd: Path, env: dict | None = None
) -> subprocess.CompletedProcess:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=merged,
    )


def test_trend_cli_degrading_exit_code(tmp_path: Path):
    r = _run_cli(
        "efficiency-trend", "--samples", "9:1000,8:1000,3:2000,2:2400", cwd=tmp_path
    )
    assert r.returncode == 3, r.stderr
    assert "DEGRADING" in r.stdout


def test_trend_cli_steady_and_improving_exit_zero(tmp_path: Path):
    r = _run_cli(
        "efficiency-trend", "--samples", "10:1000,9:1000,11:1000,10:1000",
        cwd=tmp_path,
    )
    assert r.returncode == 0, r.stderr
    assert "STEADY" in r.stdout
    r = _run_cli(
        "efficiency-trend", "--samples", "2:1000,3:1000,8:1000,9:1000", cwd=tmp_path
    )
    assert r.returncode == 0, r.stderr
    assert "IMPROVING" in r.stdout


def test_trend_cli_requires_exactly_one_source(tmp_path: Path):
    """Neither (or both) of --samples / --from-journal → contract error."""
    r = _run_cli("efficiency-trend", cwd=tmp_path)
    assert r.returncode == 2
    r = _run_cli(
        "efficiency-trend", "--samples", "1:1", "--from-journal", cwd=tmp_path
    )
    assert r.returncode == 2


def test_trend_cli_malformed_samples_is_contract_error(tmp_path: Path):
    r = _run_cli("efficiency-trend", "--samples", "9:1000,banana", cwd=tmp_path)
    assert r.returncode == 2
    assert "--samples" in r.stderr


def test_trend_cli_json_carries_the_history(tmp_path: Path):
    r = _run_cli(
        "efficiency-trend", "--samples", "9:1000,8:1000,3:2000,2:2400", "--json",
        cwd=tmp_path,
    )
    assert r.returncode == 3, r.stderr
    obj = json.loads(r.stdout)
    assert obj["verdict"] == "DEGRADING"
    assert obj["history"]["run_count"] == 4


def test_trend_cli_appears_in_exit_codes_contract(tmp_path: Path):
    """The verb publishes its verdict→code map (the `dos exit-codes` contract)."""
    r = _run_cli("exit-codes", "--json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    obj = json.loads(r.stdout)
    row = obj["efficiency-trend"]
    assert row["IMPROVING"] == 0
    assert row["STEADY"] == 0
    assert row["DEGRADING"] == 3


# ---------------------------------------------------------------------------
# End-to-end: the fossil loop. `--observe`d efficiency verdicts recorded to the
# verdict journal, then folded back by `--from-journal` (docs/300 P4's point).
# ---------------------------------------------------------------------------


def test_trend_from_journal_folds_observed_efficiency_verdicts(tmp_path: Path):
    journal = tmp_path / "verdict-journal.jsonl"
    env = {
        "DISPATCH_OBSERVE": "1",
        "DISPATCH_VERDICT_JOURNAL_PATH": str(journal),
    }
    # Four runs, recorded in order: two healthy ratios, then a sustained fall.
    for work, tokens in [(9, 1000), (8, 1000), (3, 2000), (2, 2400)]:
        r = _run_cli(
            "efficiency", "--work", str(work), "--tokens", str(tokens),
            cwd=tmp_path, env=env,
        )
        assert r.returncode in (0, 3, 4), r.stderr
    assert journal.exists(), "the --observe'd verdicts must fossilize"

    r = _run_cli(
        "efficiency-trend", "--from-journal", "--json", cwd=tmp_path, env=env
    )
    assert r.returncode == 3, r.stderr
    obj = json.loads(r.stdout)
    assert obj["verdict"] == "DEGRADING"
    assert obj["history"]["run_count"] == 4
    assert obj["history"]["last_ratio"] == pytest.approx(2 / 2400)


def test_trend_from_journal_last_n_limits_the_window(tmp_path: Path):
    journal = tmp_path / "verdict-journal.jsonl"
    env = {
        "DISPATCH_OBSERVE": "1",
        "DISPATCH_VERDICT_JOURNAL_PATH": str(journal),
    }
    for work, tokens in [(9, 1000), (8, 1000), (3, 2000), (2, 2400)]:
        _run_cli(
            "efficiency", "--work", str(work), "--tokens", str(tokens),
            cwd=tmp_path, env=env,
        )
    # --last 2 leaves too little history → STEADY-benign, exit 0.
    r = _run_cli(
        "efficiency-trend", "--from-journal", "--last", "2", cwd=tmp_path, env=env
    )
    assert r.returncode == 0, r.stderr
    assert "not enough history" in r.stdout
