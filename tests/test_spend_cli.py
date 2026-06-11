"""The `--usage-json` CLI boundary on efficiency and improve (docs/300 P4).

The ONE place a provider usage record is read for the efficiency family: a file
(or stdin via `-`), JSON-decoded and normalized through `spend.parse_usage` at
the boundary — the classifiers stay pure. A malformed record, an unrecognized
shape, or a `--tokens` that disagrees with the record's total is a contract
error (exit 2), loud, never silently reconciled.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def test_efficiency_cli_usage_json_additive(tmp_path: Path):
    """A real additive-shape usage record drives the verdict AND the JSON split."""
    usage = tmp_path / "usage.json"
    usage.write_text(
        json.dumps(
            {
                "input_tokens": 3_571,
                "output_tokens": 727,
                "cache_read_input_tokens": 6_656,
                "cache_creation_input_tokens": 0,
            }
        ),
        encoding="utf-8",
    )
    r = _run_cli(
        "efficiency", "--work", "0", "--usage-json", str(usage), "--json",
        cwd=tmp_path,
    )
    assert r.returncode == 4, r.stderr  # 10,954 tokens, zero work → WASTEFUL
    obj = json.loads(r.stdout)
    assert obj["evidence"]["tokens"] == 10_954
    assert obj["evidence"]["breakdown"]["cache_hit_ratio"] == pytest.approx(
        6_656 / 10_227
    )


def test_efficiency_cli_usage_json_mismatch_is_contract_error(tmp_path: Path):
    """--tokens disagreeing with the record's total → exit 2, loudly."""
    usage = tmp_path / "usage.json"
    usage.write_text(
        json.dumps({"input_tokens": 100, "output_tokens": 50}), encoding="utf-8"
    )
    r = _run_cli(
        "efficiency", "--work", "1", "--tokens", "9999",
        "--usage-json", str(usage), cwd=tmp_path,
    )
    assert r.returncode == 2
    assert "disagrees" in r.stderr


def test_efficiency_cli_usage_json_malformed_is_contract_error(tmp_path: Path):
    usage = tmp_path / "usage.json"
    usage.write_text("not json", encoding="utf-8")
    r = _run_cli(
        "efficiency", "--work", "1", "--usage-json", str(usage), cwd=tmp_path
    )
    assert r.returncode == 2
    assert "usage-json" in r.stderr


def test_improve_cli_usage_json_surfaces_price_facts(tmp_path: Path):
    usage = tmp_path / "usage.json"
    usage.write_text(
        json.dumps(
            {
                "prompt_tokens": 40_000,
                "completion_tokens": 10_000,
                "prompt_tokens_details": {"cached_tokens": 35_000},
            }
        ),
        encoding="utf-8",
    )
    r = _run_cli(
        "improve", "--suite-passed", "--truth-clean",
        "--work", "10", "--baseline-work", "5",
        "--usage-json", str(usage), "--json",
        cwd=tmp_path,
    )
    assert r.returncode == 0, r.stderr
    obj = json.loads(r.stdout)
    assert obj["verdict"] == "KEEP"
    assert obj["evidence"]["tokens"] == 50_000
    assert obj["evidence"]["breakdown"]["cache_hit_ratio"] == pytest.approx(0.875)
    assert obj["efficiency"]["verdict"] == "EFFICIENT"
