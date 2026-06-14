"""`dos pickable` + `dos enumerate` — the Phase-1 CLI verbs (docs/207 Phase 1).

`pickable.classify` (shipped, `8357ac0`) and `enumerate.enumerate_units` (Phase 2)
are pure kernel modules; Phase 1 exposes them as verbs so a generic skill reads
the verdict through `dos` instead of re-implementing it. Pins:

  * `dos pickable`: the verdict IS the exit code, and a HELD verdict gets a
    PER-HoldReason code so a skill branches on WHICH hold (the litmus the plan
    names: `test_cli_pickable_held_draft`, `test_cli_pickable_exit_code_per_hold`);
  * `dos enumerate`: emits the unit universe + shipped/remaining + typed
    DriftNotes; exit status clean=0 / drift=3 / empty=4 / contract=2;
  * both default-text outputs are stable and `--json` round-trips the typed object;
  * the exit-code maps are published in `dos doctor --json exit_codes` (anti-drift).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import dos


def _cli(repo: Path, *argv: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(Path(dos.__file__).parents[1])}
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *argv, "--workspace", str(repo)],
        capture_output=True, text=True, env=env,
    )


# ---------------------------------------------------------------------------
# dos pickable — the verdict→exit-code map, per HoldReason.
# ---------------------------------------------------------------------------


def test_pickable_offerable_exits_zero(tmp_path: Path):
    proc = _cli(tmp_path, "pickable", "U1", "--state", "{}")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("OFFERABLE")


def test_cli_pickable_held_draft(tmp_path: Path):
    """A draft-class unit → HELD(DRAFT_CLASS), the invariant-hold exit code, through
    the CLI (the litmus docs/207 Phase 1 names)."""
    proc = _cli(tmp_path, "pickable", "U1", "--state", json.dumps({"plan_class": "DRAFT"}))
    assert proc.returncode == 10, proc.stderr
    assert "HELD(DRAFT_CLASS)" in proc.stdout
    assert "re-dispatch-invariant" in proc.stdout


def test_cli_pickable_exit_code_per_hold(tmp_path: Path):
    """Each HoldReason maps to a distinct, documented exit code (the verdict IS the
    code). Invariant holds 10..13, curable holds 20..25, OFFERABLE 0 — all disjoint."""
    cases = [
        ({}, 0, "OFFERABLE"),
        ({"plan_class": "DRAFT"}, 10, "DRAFT_CLASS"),
        ({"operator_gated": True}, 11, "OPERATOR_GATED"),
        ({"soak_open": True}, 12, "SOAK_OPEN"),
        ({"dependency_unmet": True}, 13, "DEPENDENCY_UNMET"),
        ({"in_flight": True}, 20, "IN_FLIGHT"),
        ({"soft_claimed_elsewhere": True}, 21, "SOFT_CLAIMED_ELSEWHERE"),
        ({"stale_claim": True}, 22, "STALE_CLAIM"),
        ({"shipped": True}, 25, "SHIPPED"),
        ({"unparseable": True}, 24, "UNPARSEABLE"),
    ]
    seen_codes = set()
    for state, code, token in cases:
        proc = _cli(tmp_path, "pickable", "U1", "--state", json.dumps(state))
        assert proc.returncode == code, f"{state} → {proc.returncode} (want {code}); {proc.stderr}"
        assert token in proc.stdout
        seen_codes.add(code)
    # Every distinct hold has a distinct code (no collisions).
    assert len(seen_codes) == len(cases)


def test_pickable_cooldown_uses_now_ms(tmp_path: Path):
    # A cooldown wall in the future + now before it → HELD(COOLDOWN), code 23.
    state = {"cooldown_until_ms": 2_000_000}
    proc = _cli(tmp_path, "pickable", "U1", "--state", json.dumps(state),
                "--now-ms", "1000000")
    assert proc.returncode == 23, proc.stderr
    assert "COOLDOWN" in proc.stdout
    # Same wall, now AFTER it → OFFERABLE.
    proc2 = _cli(tmp_path, "pickable", "U1", "--state", json.dumps(state),
                 "--now-ms", "3000000")
    assert proc2.returncode == 0, proc2.stderr


def test_pickable_json_round_trips(tmp_path: Path):
    proc = _cli(tmp_path, "pickable", "U1", "--state", json.dumps({"soak_open": True}), "--json")
    assert proc.returncode == 12, proc.stderr
    obj = json.loads(proc.stdout)
    assert obj["held"] is True
    assert obj["reason"] == "SOAK_OPEN"
    assert obj["redispatch_invariant"] is True
    assert obj["unit"] == "U1"
    # The unblock action travels WITH the held verdict (docs/168 §2 routing as data).
    assert obj["next_action"], "a HELD verdict must carry its remedy in JSON"
    assert "soak" in obj["next_action"].lower()


def test_pickable_offerable_json_has_null_next_action(tmp_path: Path):
    proc = _cli(tmp_path, "pickable", "U1", "--state", "{}", "--json")
    assert proc.returncode == 0, proc.stderr
    obj = json.loads(proc.stdout)
    assert obj["held"] is False
    assert obj["next_action"] is None  # nothing to unblock — nothing held


def test_pickable_next_action_stays_off_stdout(tmp_path: Path):
    """The remedy line is stderr + TTY-gated, so a non-TTY text run (this
    subprocess, and any pipe/CI) keeps stdout byte-clean — the `→` never lands
    on stdout where a parser would trip on it."""
    proc = _cli(tmp_path, "pickable", "U1", "--state", json.dumps({"plan_class": "DRAFT"}))
    assert proc.returncode == 10, proc.stderr
    assert proc.stdout.startswith("HELD(DRAFT_CLASS)")
    assert "→" not in proc.stdout  # the action is not on stdout
    # And nothing chatty on stderr either (non-TTY ⇒ the gate suppresses it).
    assert proc.stderr.strip() == ""


def test_pickable_bad_state_is_contract_error(tmp_path: Path):
    proc = _cli(tmp_path, "pickable", "U1", "--state", "{not json")
    assert proc.returncode == 2, proc.stderr
    assert "error" in proc.stderr.lower()


# ---------------------------------------------------------------------------
# dos enumerate — the producer surface + drift/empty/contract status.
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_enumerate_series_clean(tmp_path: Path):
    doc = _write(tmp_path, "p.md",
                 "### AUTH0 — base — SHIPPED 2026-01-01\n### AUTH1 — refresh\n### AUTH2 — out\n")
    proc = _cli(tmp_path, "enumerate", str(doc), "--series", "AUTH")
    assert proc.returncode == 0, proc.stderr
    assert "units=3" in proc.stdout
    assert "AUTH0 via stamp" in proc.stdout


def test_enumerate_json_round_trips(tmp_path: Path):
    doc = _write(tmp_path, "p.md", "### AUTH0 — base\n### AUTH1 — refresh\n")
    proc = _cli(tmp_path, "enumerate", str(doc), "--series", "AUTH", "--json")
    assert proc.returncode == 0, proc.stderr
    obj = json.loads(proc.stdout)
    assert obj["units"] == ["AUTH0", "AUTH1"]
    assert obj["series"] == "AUTH"


def test_enumerate_drift_exits_three(tmp_path: Path):
    doc = _write(tmp_path, "p.md", "### AUTH1 — only one\n")
    proc = _cli(tmp_path, "enumerate", str(doc), "--series", "AUTH",
                "--shipped", json.dumps(["AUTH9"]))
    assert proc.returncode == 3, proc.stderr
    assert "list_table_mismatch" in proc.stdout


def test_enumerate_empty_exits_four(tmp_path: Path):
    doc = _write(tmp_path, "p.md", "# Just prose, no phases.\n")
    proc = _cli(tmp_path, "enumerate", str(doc), "--series", "AUTH")
    assert proc.returncode == 4, proc.stderr
    assert "empty" in proc.stdout


def test_enumerate_missing_file_is_contract_error(tmp_path: Path):
    proc = _cli(tmp_path, "enumerate", str(tmp_path / "nope.md"))
    assert proc.returncode == 2, proc.stderr
    assert "error" in proc.stderr.lower()


def test_enumerate_generic_markdown_no_series(tmp_path: Path):
    doc = _write(tmp_path, "p.md", "### 1. a\n### 2. b\n### 3. c\n")
    proc = _cli(tmp_path, "enumerate", str(doc), "--json")
    assert proc.returncode == 0, proc.stderr
    obj = json.loads(proc.stdout)
    assert obj["units"] == ["1", "2", "3"]


# ---------------------------------------------------------------------------
# Exit codes are published (anti-drift): dos doctor --json carries the maps.
# ---------------------------------------------------------------------------


def test_exit_codes_published_in_doctor(tmp_path: Path):
    proc = _cli(tmp_path, "doctor", "--json")
    assert proc.returncode == 0, proc.stderr
    obj = json.loads(proc.stdout)
    ec = obj.get("exit_codes", {})
    assert ec.get("pickable", {}).get("OFFERABLE") == 0
    assert ec.get("pickable", {}).get("DRAFT_CLASS") == 10
    assert ec.get("enumerate", {}).get("DRIFT") == 3
    assert ec.get("enumerate", {}).get("EMPTY") == 4
    assert ec.get("cooldown", {}).get("CLEAR") == 0
    assert ec.get("cooldown", {}).get("RECENTLY_ATTEMPTED") == 3


# ---------------------------------------------------------------------------
# dos cooldown — the anti-churn read surface (docs/207 Phase 3).
# ---------------------------------------------------------------------------


def test_cooldown_clear_exits_zero(tmp_path: Path):
    proc = _cli(tmp_path, "cooldown", "U1", "--attempts", "[]")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("CLEAR")


def test_cooldown_recently_attempted_exits_three(tmp_path: Path):
    # A drained attempt 1h ago, under the 6h default window → RECENTLY_ATTEMPTED.
    import time
    recent = int(time.time() * 1000) - 3600 * 1000
    attempts = json.dumps([{
        "op": "ATTEMPT", "unit_id": "U1", "outcome": "drained",
        "attempted_at_ms": recent, "schema": {"family": "lane-journal", "version": 1},
    }])
    proc = _cli(tmp_path, "cooldown", "U1", "--attempts", attempts)
    assert proc.returncode == 3, proc.stderr
    assert proc.stdout.startswith("RECENTLY_ATTEMPTED")


def test_cooldown_shipped_is_moot(tmp_path: Path):
    import time
    recent = int(time.time() * 1000) - 3600 * 1000
    attempts = json.dumps([{
        "op": "ATTEMPT", "unit_id": "U1", "outcome": "shipped", "attempted_at_ms": recent,
    }])
    proc = _cli(tmp_path, "cooldown", "U1", "--attempts", attempts, "--json")
    assert proc.returncode == 0, proc.stderr
    obj = json.loads(proc.stdout)
    assert obj["state"] == "CLEAR"


# ---------------------------------------------------------------------------
# dos reconcile — the quiet-completion gate (docs/207 Phase 4).
# ---------------------------------------------------------------------------


def test_reconcile_verified_exits_zero(tmp_path: Path):
    proc = _cli(tmp_path, "reconcile", "U1", "--claimed-done", "--oracle-shipped")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("VERIFIED")


def test_reconcile_quiet_incomplete_exits_three(tmp_path: Path):
    proc = _cli(tmp_path, "reconcile", "U1", "--claimed-done", "--no-oracle-shipped")
    assert proc.returncode == 3, proc.stderr
    assert proc.stdout.startswith("QUIET_INCOMPLETE")


def test_reconcile_honest_open_exits_four(tmp_path: Path):
    proc = _cli(tmp_path, "reconcile", "U1", "--no-oracle-shipped")
    assert proc.returncode == 4, proc.stderr
    assert proc.stdout.startswith("HONEST_OPEN")


def test_reconcile_no_oracle_source_is_contract_error(tmp_path: Path):
    proc = _cli(tmp_path, "reconcile", "U1", "--claimed-done")
    assert proc.returncode == 2, proc.stderr
    assert "error" in proc.stderr.lower()


def test_reconcile_computes_oracle_from_git(tmp_path: Path):
    # With --plan/--phase and no shipped stamp in this empty repo, the oracle says
    # NOT_SHIPPED → a claimed-done unit is QUIET_INCOMPLETE (the real verify rung).
    proc = _cli(tmp_path, "reconcile", "AUTH3", "--claimed-done",
                "--plan", "AUTH", "--phase", "AUTH3")
    assert proc.returncode == 3, proc.stderr
    assert proc.stdout.startswith("QUIET_INCOMPLETE")


def test_reconcile_exit_codes_published(tmp_path: Path):
    proc = _cli(tmp_path, "doctor", "--json")
    assert proc.returncode == 0, proc.stderr
    ec = json.loads(proc.stdout).get("exit_codes", {})
    assert ec.get("reconcile", {}).get("VERIFIED") == 0
    assert ec.get("reconcile", {}).get("QUIET_INCOMPLETE") == 3
    assert ec.get("reconcile", {}).get("HONEST_OPEN") == 4
