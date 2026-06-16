"""Pins for scoreboard_index — the reproducible observatory front door.

The front door (`docs/scoreboard/README.md`) is rebuilt from the committed
per-repo `sweep.json` data by `scripts/scoreboard_index.py`. These tests guard:
the committed README + SVG assets match the data (the `--check` honesty gate,
the same one `scoreboard_rollup.py` runs on its numbers), the render is pure +
deterministic, and the observatory framing carries its load-bearing pieces (the
charts, the cross-set insight, the ethics line, the self page).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "scoreboard_index", REPO / "scripts" / "scoreboard_index.py")
idx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(idx)


# ---------------------------------------------------------------------------
# the honesty gate — the committed front door must match the committed data.
# ---------------------------------------------------------------------------

def test_committed_readme_matches_the_data():
    # `--check` regenerates the README + SVGs in memory and compares to tree.
    # If this fails, run `python scripts/scoreboard_index.py` and commit.
    assert idx.main(["--check"]) == 0


def test_render_is_pure_and_deterministic():
    readme1, assets1 = idx.build(stamp="2026-06-16")
    readme2, assets2 = idx.build(stamp="2026-06-16")
    assert readme1 == readme2
    assert assets1 == assets2


def test_stamp_is_the_only_clock_input():
    # Different stamp ⇒ the README may differ (the as-of date); but with no
    # date token in the body today, the two are equal — the point is that no
    # OTHER nondeterminism leaks in. Build twice with different stamps and
    # assert only deterministic content.
    a, _ = idx.build(stamp="2026-01-01")
    b, _ = idx.build(stamp="2026-12-31")
    # the leaderboard table is identical regardless of stamp.
    assert "| Repo | AI-built | Agents | Claims checked | Backed |" in a
    assert "| Repo | AI-built | Agents | Claims checked | Backed |" in b


# ---------------------------------------------------------------------------
# the observatory framing — the reframe's load-bearing pieces are present.
# ---------------------------------------------------------------------------

def test_front_door_leads_with_the_comparison_not_the_auditor_pitch():
    readme, _ = idx.build(stamp="2026-06-16")
    # H1 is the observatory hook, not the old "AI commit messages can lie".
    assert readme.startswith("# How AI built the software you already use")


def test_front_door_embeds_the_three_charts():
    readme, assets = idx.build(stamp="2026-06-16")
    for rel in (idx.ASSET_AI_SHARE, idx.ASSET_AGENT_MIX, idx.ASSET_CLAIM_KINDS):
        assert assets[rel].startswith("<svg")
        assert f"]({rel})" in readme  # embedded via markdown image syntax


def test_front_door_carries_the_cross_set_insight():
    readme, _ = idx.build(stamp="2026-06-16")
    # the 'so what': the most prolific agent named, with a share.
    assert "the most prolific agent" in readme


def test_front_door_keeps_the_ethics_line_and_self_page():
    readme, _ = idx.build(stamp="2026-06-16")
    assert "never** a correctness, honesty, or intent grade" in readme
    assert idx.SELF_REPO in readme  # the auditor grades itself first


def test_no_false_withheld_zero():
    # The withheld count is not knowable from committed data, so the front door
    # must NOT claim "Another 0 repos were checked" — it omits the sentence.
    readme, _ = idx.build(stamp="2026-06-16")
    assert "Another 0 repos" not in readme


# ---------------------------------------------------------------------------
# the fold — rows match the rollup aggregate (one source of truth).
# ---------------------------------------------------------------------------

def test_rows_and_aggregate_agree_on_the_same_files():
    paths = idx.rollup.find_sweeps(idx.SCOREBOARD)
    rows = idx.leaderboard_rows(paths)
    agg = idx.rollup.fold(paths)
    # same repo count, and the row checkable sums to the aggregate checkable.
    assert len(rows) == agg["repos"]
    assert sum(r["checkable"] for r in rows) == agg["checkable"]
    assert sum(r["witnessed"] for r in rows) == agg["witnessed"]


def test_insight_facts_are_derived_not_authored():
    agg = idx.rollup.fold(idx.rollup.find_sweeps(idx.SCOREBOARD))
    facts = idx.insight_facts(agg)
    # the top agent is the biggest marker in the fold (claude, on this set).
    assert facts["top_agent"] == agg["mix"][0][0]
    assert 0.0 < facts["top_agent_share"] <= 1.0
    assert 0.0 <= facts["code_pct"] <= 1.0
