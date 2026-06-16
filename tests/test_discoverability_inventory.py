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
    "arrival_queries_captured", "arrival_queries_tracked", "arrival_query_pages",
    "arrival_files_present", "arrival_files_expected", "answer_pages",
    "hosts_wireable", "integration_tiers", "framework_recipes",
    "registries_live", "registries_gated_submitted",
    "scoreboard_pages_published", "scoreboard_fanout_engine",
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


def test_scoreboard_fanout_engine_is_present():
    # the multiplicative discovery surface (#98): the orchestrator + index root
    # are in the tree, and page #1 (the self-page) is published. Pin it so a
    # regression that drops the fan-out engine is caught.
    inv = di.gather()
    sb = inv["scoreboard"]
    assert sb["orchestrator"] is True
    assert sb["index_root"] is True
    assert len(sb["pages_published"]) >= 1   # at least page #1
    h = di.headline(inv)
    assert h["scoreboard_fanout_engine"] is True


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


def test_arrival_queries_capture_matches_page_presence():
    # captured is read from the tree (the page exists), never asserted — the same
    # honesty rule as the registries. Every tracked query must point at a real
    # answer page (a dangling target is a rot bug), and the headline's captured
    # count must equal the number of present targets.
    inv = di.gather()
    for q, page, ok in inv["arrival_queries"]:
        assert ok == (_REPO / page).exists(), f"{q!r} capture flag disagrees with the tree"
        assert (_REPO / page).exists(), f"{q!r} points at a missing page: {page}"
    h = di.headline(inv)
    captured = sum(1 for _, _, ok in inv["arrival_queries"] if ok)
    assert h["arrival_queries_captured"] == captured
    assert h["arrival_queries_tracked"] == len(inv["arrival_queries"])
    # distinct pages the captured queries resolve to — the real surface count
    assert h["arrival_query_pages"] == len({page for _, page, ok in inv["arrival_queries"] if ok})


def test_arrival_queries_are_unique():
    # The alias layer (the query-surface multiplier) only counts honestly if no
    # phrasing is tracked twice — a duplicate string would silently double-count a
    # query against the captured total. The flattening in the inventory dedupes by
    # first occurrence across the curated core and both alias maps; pin that here so
    # a future copy-paste dup fails loudly instead of inflating the headline.
    queries = [q for q, _ in di.ARRIVAL_QUERIES]
    seen: dict[str, int] = {}
    for q in queries:
        seen[q] = seen.get(q, 0) + 1
    dupes = {q: n for q, n in seen.items() if n > 1}
    assert not dupes, f"duplicate arrival-query strings (double-counted): {dupes}"


def test_every_arrival_query_points_at_a_real_page():
    # The alias layer adds hundreds of (query, page) tuples; every one must resolve
    # to a page in the tree or the captured count is a lie (the same honesty rule
    # test_arrival_queries_capture_matches_page_presence enforces, asserted directly
    # over the multiplied list so a typo'd alias target fails here too).
    bad = [(q, p) for q, p in di.ARRIVAL_QUERIES if not (_REPO / p).exists()]
    assert not bad, f"arrival queries pointing at missing pages: {bad}"


def test_transition_query_is_tracked_and_captured():
    # the 2026 token-maxxing→verified-outcomes transition query is the measured
    # delta this work added; pin it so a regression that drops the page is caught.
    inv = di.gather()
    targets = {page for _, page, ok in inv["arrival_queries"] if ok}
    assert "docs/answers/what-replaced-tokens-burned-as-the-metric-for-ai-agents.md" in targets


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
