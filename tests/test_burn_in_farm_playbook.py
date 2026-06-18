"""Pin playbook 09 (hardware-in-the-loop) against the real kernel.

`examples/playbooks/09_hardware-in-the-loop-equipment.md` pastes verbatim
transcripts for three halves — bench equipment lanes, the farm-wide class
budget, and the rig effect-witness. Pasted output rots silently: a changed
verdict field, a renamed refuse reason, or a different attest verdict would
turn the playbook stale with nothing in the suite going red. These tests
execute the same seams against the fixture under
`examples/workspaces/burn-in-farm/` and assert each half's HEADLINE property —
never the agent's narration — the same discipline `test_fleet_framework_
examples.py` applies to the cookbook.

The bench-lane and witness halves are driven through the real `dos` CLI from
inside the workspace dir (exactly as the playbook's `cd examples/workspaces/
burn-in-farm` transcripts do); the budget half is driven through the runnable
`farm_budget_demo.py` the playbook's Step 2 quotes (the generic CLI cannot show
a class budget — #97's scoping finding).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path

import pytest

_WS = Path(__file__).resolve().parents[1] / "examples" / "workspaces" / "burn-in-farm"
_BENCH_A = ["benches/bench-a/**"]


@pytest.fixture()
def in_workspace():
    """cd into the fixture (the playbook's transcripts run from there: the
    --key-file / --accept-cmd paths are workspace-relative)."""
    prev = os.getcwd()
    os.chdir(_WS)
    try:
        yield
    finally:
        os.chdir(prev)


def _cli(*argv):
    """Run `dos <argv>` and return (exit_code, stdout)."""
    from dos import cli

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        rc = cli.main(list(argv))
    return rc, buf.getvalue()


# ── Step 1: a bench is an equipment lane (the generic #97 demonstration) ─────


def _arbitrate(lane: str, leases: list[dict]):
    rc, out = _cli("arbitrate", "--workspace", ".", "--lane", lane,
                   "--leases", json.dumps(leases))
    return rc, json.loads(out)


def test_first_bench_taker_is_admitted(in_workspace):
    rc, d = _arbitrate("bench-a", [])
    assert rc == 0
    assert d["outcome"] == "acquire"
    assert d["lane"] == "bench-a"
    assert d["tree"] == _BENCH_A


def test_second_same_bench_taker_refused_same_lane(in_workspace):
    # One campaign per chamber: the second taker on a held bench refuses
    # same-lane and the refusal lists the lanes that ARE free.
    live = [{"lane": "bench-a", "lane_kind": "cluster", "tree": _BENCH_A}]
    rc, d = _arbitrate("bench-a", live)
    assert rc == 1
    assert d["outcome"] == "refuse"
    assert "already held" in d["reason"]
    assert "firmware" in d["free_clusters"] and "tests" in d["free_clusters"]


def test_disjoint_second_bench_admitted_concurrently(in_workspace):
    # A held chamber A must not serialize the lab: chamber B is disjoint, so it
    # is admitted alongside — the headline equipment-lane property.
    live = [{"lane": "bench-a", "lane_kind": "cluster", "tree": _BENCH_A}]
    rc, d = _arbitrate("bench-b", live)
    assert rc == 0
    assert d["outcome"] == "acquire"
    assert d["lane"] == "bench-b"


# ── Step 2: the farm-wide class budget refuses the (N+1)th chamber ───────────


def _farm_demo():
    """Import the runnable demo the playbook's Step 2 transcript comes from."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_farm_budget_demo", _WS / "farm_budget_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.run_demo()


def test_class_budget_admits_up_to_n_then_refuses():
    # Budget burn-in=2: chambers 1 and 2 admit; the 3rd is refused with
    # CLASS_BUDGET_EXHAUSTED naming the full class — NOT a tree collision and
    # NOT a /replan. This is the half the generic CLI cannot show.
    d = _farm_demo()
    assert d["grab1"].outcome == "acquire" and d["grab1"].lane == "chamber-1"
    assert d["grab2"].outcome == "acquire" and d["grab2"].lane == "chamber-2"
    assert d["grab3"].outcome == "refuse"
    assert "CLASS_BUDGET_EXHAUSTED" in d["grab3"].reason
    assert "burn-in (2/2)" in d["grab3"].reason
    assert "do NOT /replan" in d["grab3"].reason


# ── Step 3: the witness is the rig (effect-witness over the instrument) ──────


def _attest(claim: str, accept_cmd: str):
    rc, out = _cli(
        "attest", "--workspace", ".", "--claim", claim,
        "--narrated", "(claim under adjudication)",
        "--accept-cmd", accept_cmd,
        "--key-file", "benches/bench-a/attest-demo.key",
        "--timestamp", "2026-06-14T00:00:00Z", "--json")
    return rc, json.loads(out)


def test_honest_soak_claim_confirmed(in_workspace):
    # unit-3 genuinely passed: the acceptance command over the campaign log
    # succeeds → CONFIRMED, exit 0. Use Python here instead of the playbook's
    # `grep` transcript so the OS-exit witness is portable on Windows.
    cmd = ("python -c \"from pathlib import Path; import sys; "
           "sys.exit(0 if 'PASS unit-3' in "
           "Path('benches/bench-a/campaign.log').read_text(encoding='utf-8') else 1)\"")
    rc, receipt = _attest("soak:unit-3", cmd)
    assert rc == 0
    assert receipt["verdict"] == "CONFIRMED"


def test_false_soak_claim_refuted_from_the_instrument(in_workspace):
    # unit-7 carries a PASS in the campaign log, but the witness reads the
    # INSTRUMENT capture: measured 71C < 125C setpoint → the accept command
    # fails → REFUTED, exit 1. The caught-lie moment.
    cmd = ("python -c \"import json,sys; "
           "d=json.load(open('benches/bench-a/thermal_after.json')); "
           "sys.exit(0 if d['unit-7:maxC'] >= d['chamberA:setpointC'] else 1)\"")
    rc, receipt = _attest("soak:unit-7", cmd)
    assert rc == 1
    assert receipt["verdict"] == "REFUTED"


# ── the fixture itself: zero kernel change needed (the #97 thesis) ───────────


def test_workspace_loads_with_the_documented_taxonomy():
    from dos import config as _config

    cfg = _config.load_workspace_config(_WS)
    lanes = set(cfg.lanes.trees)
    assert {"bench-a", "bench-b", "firmware", "tests"} <= lanes
    # The farm-wide budget is declared as data (docs/97), not a lane.
    assert cfg.class_budgets.as_arbiter_budgets().get("burn-in") == 2


def test_no_kernel_module_names_the_burn_in_fixture():
    # The whole #97 point: the equipment case is pure policy data. No kernel
    # source should name this fixture — it lives entirely in examples/.
    src = Path(__file__).resolve().parents[1] / "src" / "dos"
    for py in src.rglob("*.py"):
        assert "burn-in-farm" not in py.read_text(encoding="utf-8"), py
