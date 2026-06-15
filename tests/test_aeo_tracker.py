"""Pin the AEO/SEO surface tracker (`scripts/aeo_tracker.py`).

The tracker turns the inventory's ephemeral "before/after" delta into an
append-only ledger — the AEO/SEO progress becomes a tracked series instead of a
manual eyeball. These tests pin the contract that makes that series trustworthy:

  * a snapshot appends exactly one row, schema-tagged, with the witnessed +
    advisory counts split (witnessed = the gated, in-tree surfaces);
  * the delta is computed against the PRIOR row (the first snapshot is a full
    gain from a zero baseline);
  * --check fails on a measured regression (a witnessed surface dropped) and
    NEVER on a GATED/advisory change — the honesty wall the inventory draws
    between LIVE and GATED, drawn across time;
  * the ledger logs CHANGE, not heartbeat: an unchanged tree + same stamp does
    not append a second identical row unless --force;
  * it is deterministic (stamp injectable) and offline (it composes the
    inventory, whose lone network read already degrades to empty).

Tests drive the pure functions + a tmp ledger so they never depend on the live
tree's exact counts (which other in-repo tooling changes), and run offline.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_HELPER = Path(__file__).resolve().parents[1] / "scripts" / "aeo_tracker.py"
_spec = importlib.util.spec_from_file_location("aeo_tracker", _HELPER)
at = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(at)


# ---------------------------------------------------------------------------
# Synthetic headlines — every WITNESSED + ADVISORY key the tracker reads, so a
# row is well-formed without touching the live tree.
# ---------------------------------------------------------------------------


def _headline(**over) -> dict:
    h = {k: 1 for k in at.WITNESSED_KEYS}
    h.update({k: 9 for k in at.ADVISORY_KEYS})
    h.update(over)
    return h


# ---------------------------------------------------------------------------
# make_row — schema, split, determinism.
# ---------------------------------------------------------------------------


def test_make_row_is_schema_tagged_and_splits_witnessed_from_advisory():
    row = at.make_row(_headline(), stamp="2026-06-15")
    assert row["schema"] == at.LEDGER_SCHEMA
    assert row["stamp"] == "2026-06-15"
    assert set(row["witnessed"].keys()) == set(at.WITNESSED_KEYS)
    # advisory keys present in the headline are carried, gated keys are NOT
    # duplicated into witnessed.
    assert set(row["advisory"].keys()) <= set(at.ADVISORY_KEYS)
    assert set(row["witnessed"]).isdisjoint(row["advisory"])


def test_make_row_is_deterministic_for_a_fixed_stamp():
    a = at.make_row(_headline(answer_pages=7), stamp="2026-06-15")
    b = at.make_row(_headline(answer_pages=7), stamp="2026-06-15")
    assert a == b


def test_gated_submitted_is_advisory_never_witnessed():
    # the honesty wall: a filed-but-unmerged submission is a promise, tracked
    # as advisory, never folded into the gated witnessed set.
    assert "registries_gated_submitted" in at.ADVISORY_KEYS
    assert "registries_gated_submitted" not in at.WITNESSED_KEYS


# ---------------------------------------------------------------------------
# delta + regressions.
# ---------------------------------------------------------------------------


def test_first_snapshot_delta_is_full_gain_from_zero():
    cur = at.make_row(_headline(answer_pages=6), stamp="2026-06-15")
    d = at.delta(None, cur)
    assert d["answer_pages"] == 6  # 6 - 0
    assert at.regressions(None, cur) == {}  # nothing to regress against


def test_delta_is_against_the_prior_row():
    prev = at.make_row(_headline(answer_pages=6, hosts_wireable=4), stamp="2026-06-14")
    cur = at.make_row(_headline(answer_pages=8, hosts_wireable=4), stamp="2026-06-15")
    d = at.delta(prev, cur)
    assert d["answer_pages"] == 2
    assert d["hosts_wireable"] == 0


def test_regression_is_only_a_dropped_witnessed_surface():
    prev = at.make_row(_headline(answer_pages=6), stamp="2026-06-14")
    cur = at.make_row(_headline(answer_pages=5), stamp="2026-06-15")
    regs = at.regressions(prev, cur)
    assert regs == {"answer_pages": -1}


def test_advisory_drop_is_not_a_regression():
    # a GATED submission expiring (advisory) must NOT trip the gate.
    prev = at.make_row(_headline(registries_gated_submitted=4), stamp="2026-06-14")
    cur = at.make_row(_headline(registries_gated_submitted=1), stamp="2026-06-15")
    assert at.regressions(prev, cur) == {}


# ---------------------------------------------------------------------------
# ledger I/O + the snapshot CLI (tmp ledger, injected stamp — offline).
# ---------------------------------------------------------------------------


def test_snapshot_appends_one_row(tmp_path):
    led = tmp_path / "aeo_ledger.jsonl"
    rc = at.main(["--snapshot", "--stamp", "2026-06-15", "--ledger", str(led)])
    assert rc == 0
    rows = at.read_ledger(led)
    assert len(rows) == 1
    assert rows[0]["stamp"] == "2026-06-15"
    assert rows[0]["schema"] == at.LEDGER_SCHEMA
    assert set(rows[0]["witnessed"]) == set(at.WITNESSED_KEYS)


def test_snapshot_is_idempotent_on_an_unchanged_tree(tmp_path):
    # same tree + same stamp twice → the second is a heartbeat, not appended.
    led = tmp_path / "aeo_ledger.jsonl"
    at.main(["--snapshot", "--stamp", "2026-06-15", "--ledger", str(led)])
    at.main(["--snapshot", "--stamp", "2026-06-15", "--ledger", str(led)])
    assert len(at.read_ledger(led)) == 1


def test_force_appends_even_when_unchanged(tmp_path):
    led = tmp_path / "aeo_ledger.jsonl"
    at.main(["--snapshot", "--stamp", "2026-06-15", "--ledger", str(led)])
    at.main(["--snapshot", "--stamp", "2026-06-16", "--ledger", str(led), "--force"])
    assert len(at.read_ledger(led)) == 2


def test_ledger_is_lf_terminated_jsonl(tmp_path):
    led = tmp_path / "aeo_ledger.jsonl"
    at.main(["--snapshot", "--stamp", "2026-06-15", "--ledger", str(led)])
    raw = led.read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r\n" not in raw  # LF even on Windows — byte-comparable across hosts


# ---------------------------------------------------------------------------
# --check — the regression gate, driven through a synthetic two-row ledger.
# ---------------------------------------------------------------------------


def _write_ledger(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                    encoding="utf-8", newline="\n")


def test_check_passes_with_fewer_than_two_snapshots(tmp_path):
    led = tmp_path / "aeo_ledger.jsonl"
    assert at.main(["--check", "--ledger", str(led)]) == 0           # empty
    _write_ledger(led, [at.make_row(_headline(), stamp="2026-06-15")])
    assert at.main(["--check", "--ledger", str(led)]) == 0           # one row


def test_check_passes_when_surfaces_only_grow(tmp_path):
    led = tmp_path / "aeo_ledger.jsonl"
    _write_ledger(led, [
        at.make_row(_headline(answer_pages=6), stamp="2026-06-14"),
        at.make_row(_headline(answer_pages=8), stamp="2026-06-15"),
    ])
    assert at.main(["--check", "--ledger", str(led)]) == 0


def test_check_fails_on_a_witnessed_regression(tmp_path):
    led = tmp_path / "aeo_ledger.jsonl"
    _write_ledger(led, [
        at.make_row(_headline(answer_pages=8), stamp="2026-06-14"),
        at.make_row(_headline(answer_pages=5), stamp="2026-06-15"),
    ])
    assert at.main(["--check", "--ledger", str(led)]) == 1


def test_check_does_not_fail_on_an_advisory_only_change(tmp_path):
    led = tmp_path / "aeo_ledger.jsonl"
    _write_ledger(led, [
        at.make_row(_headline(registries_gated_submitted=4), stamp="2026-06-14"),
        at.make_row(_headline(registries_gated_submitted=0), stamp="2026-06-15"),
    ])
    assert at.main(["--check", "--ledger", str(led)]) == 0


# ---------------------------------------------------------------------------
# status + JSON shape.
# ---------------------------------------------------------------------------


def test_status_reports_trend_since_first(tmp_path, capsys):
    led = tmp_path / "aeo_ledger.jsonl"
    _write_ledger(led, [
        at.make_row(_headline(answer_pages=4), stamp="2026-06-13"),
        at.make_row(_headline(answer_pages=9), stamp="2026-06-15"),
    ])
    at.main(["--status", "--ledger", str(led)])
    out = capsys.readouterr().out
    assert "snapshots recorded: 2" in out
    assert "+5 since first" in out  # 9 - 4 on answer_pages


def test_snapshot_json_shape(tmp_path):
    led = tmp_path / "aeo_ledger.jsonl"
    r = subprocess.run(
        [sys.executable, str(_HELPER), "--snapshot", "--stamp", "2026-06-15",
         "--ledger", str(led), "--json"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["appended"] is True
    assert set(payload["row"]["witnessed"]) == set(at.WITNESSED_KEYS)
    assert set(payload["delta"]) == set(at.WITNESSED_KEYS)
    assert payload["regressions"] == {}  # first row, nothing to regress


def test_status_runs_against_the_real_tree_offline():
    # smoke: the default subcommand composes the real inventory without crashing
    # and without network (host registry degrades to empty). It reads the tracked
    # ledger if present; an empty one is fine.
    r = subprocess.run(
        [sys.executable, str(_HELPER), "--json"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert "snapshots" in payload
