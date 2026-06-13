"""Pin the discoverability inventory (`scripts/discoverability_inventory.py`).

The inventory turns the unbounded goal "make DOS discoverable by agents" into a
counted, re-runnable number read from the repo's own ground truth. These tests
pin the contract that makes that number trustworthy:

  * the headline carries exactly the documented keys, all non-negative ints;
  * a GATED registry (a filed-but-unmerged submission) is NEVER counted in the
    LIVE total — the honesty rule that keeps the headline from inflating on a
    promise;
  * `gather()` reads the real tree (all 10 arrival files are present today, so
    the rot pin is green), and the answer-page count matches the glob;
  * `--check` exits non-zero when an expected arrival file is absent (the rot
    pin: a renamed manifest fails loudly, same discipline as the llms.txt test).

No network: the host registry read degrades to an empty list rather than
crashing, so these run offline.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "discoverability_inventory.py"
_spec = importlib.util.spec_from_file_location("discoverability_inventory", _HELPER_PATH)
di = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(di)

_REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# headline contract — documented keys, non-negative ints.
# ---------------------------------------------------------------------------

_HEADLINE_KEYS = {
    "arrival_files_present", "arrival_files_expected", "answer_pages",
    "hosts_wireable", "integration_tiers", "framework_recipes",
    "registries_live", "registries_gated_submitted",
}


def test_headline_has_exactly_the_documented_keys():
    h = di.headline(di.gather())
    assert set(h.keys()) == _HEADLINE_KEYS


def test_headline_values_are_non_negative_ints():
    h = di.headline(di.gather())
    for k, v in h.items():
        assert isinstance(v, int), f"{k} is {type(v)}"
        assert v >= 0, f"{k} = {v}"


# ---------------------------------------------------------------------------
# the honesty rule — GATED never folds into the LIVE count.
# ---------------------------------------------------------------------------


def test_gated_registries_are_excluded_from_the_live_count():
    inv = di.gather()
    live = [r for r in inv["registries"] if r["status"] == "LIVE"]
    gated = [r for r in inv["registries"] if r["status"] == "GATED"]
    h = di.headline(inv)
    assert h["registries_live"] == len(live)
    assert h["registries_gated_submitted"] == len(gated)
    # and they are disjoint sets of names — a venue is one or the other.
    assert {r["name"] for r in live}.isdisjoint({r["name"] for r in gated})


def test_every_registry_status_is_from_the_closed_set():
    for r in di.gather()["registries"]:
        assert r["status"] in {"LIVE", "GATED"}


def test_three_integration_tiers_including_exit_code():
    # the exit-code tier (#92) is the third — its presence is the measured delta
    # this work added; pin it so a regression that drops it is caught.
    inv = di.gather()
    assert len(inv["tiers"]) == 3
    joined = " ".join(inv["tiers"]).lower()
    assert "mcp" in joined and "hook" in joined and "exit-code" in joined


# ---------------------------------------------------------------------------
# tree reads — the inventory reflects real repo state.
# ---------------------------------------------------------------------------


def test_all_arrival_files_present_today_rot_pin():
    # If this fails, an arrival file the inventory expects was renamed/removed —
    # fix the path or the file, do not delete the assertion (the llms.txt rule).
    missing = [p for p, _, ok in di.gather()["arrival_files"] if not ok]
    assert not missing, f"arrival files missing: {missing}"


def test_answer_page_count_matches_the_glob():
    inv = di.gather()
    globbed = sorted(
        str(p.relative_to(_REPO)).replace("\\", "/")
        for p in _REPO.glob("docs/answers/*.md")
        if "README" not in p.name
    )
    assert inv["answers_pages"] == globbed
    assert len(globbed) >= 5  # the shipped corpus floor (docs/325)


# ---------------------------------------------------------------------------
# --check rot pin — a missing arrival file exits non-zero.
# ---------------------------------------------------------------------------


def test_check_passes_on_the_real_tree():
    r = subprocess.run(
        [sys.executable, str(_HELPER_PATH), "--check"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr


def test_check_fails_when_an_arrival_file_is_missing(monkeypatch):
    # Inject a bogus expected arrival file; --check must surface it as exit 1.
    monkeypatch.setattr(
        di, "ARRIVAL_FILES",
        di.ARRIVAL_FILES + [("does/not/exist.txt", "synthetic missing surface")],
    )
    rc = di.main(["--check"])
    assert rc == 1


def test_json_output_is_valid_and_carries_headline():
    r = subprocess.run(
        [sys.executable, str(_HELPER_PATH), "--json"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert set(payload["headline"].keys()) == _HEADLINE_KEYS
    assert "inventory" in payload
