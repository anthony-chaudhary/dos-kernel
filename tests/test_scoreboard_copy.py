"""Pin the scoreboard copy module's AI-built SHARE formatting (`scripts/scoreboard_copy.py`).

Dev tooling, not a kernel module — loaded by path like the rest of the
scoreboard suite.

The load-bearing rule these tests guard: a sub-1% AI-built share must never
render as the literal "0%" on a board OF AI-built repos. `f"{share:.0%}"` rounds
anything under 0.5% to "0%", so a repo with real AI-authored commits (28/5727 =
0.49%, 39/10000 = 0.39%) used to show "0% AI-built" — which reads as a bug to any
reader. The shared `format_ai_share` helper floors any nonzero such share to
"<1%" and is used at all three render sites (index headline, leaderboard column,
per-repo page). The BACKED rate (witnessed/checkable) is a different number and
is deliberately NOT routed through this helper.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_HELPER = Path(__file__).resolve().parents[1] / "scripts" / "scoreboard_copy.py"
_spec = importlib.util.spec_from_file_location("scoreboard_copy", _HELPER)
copy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(copy)


# ---------------------------------------------------------------------------
# format_ai_share — the floored percent formatter.
# ---------------------------------------------------------------------------

def test_sub_one_percent_renders_lt_one_not_zero():
    # The two real repos that used to render "0% AI-built".
    assert copy.format_ai_share(28, 5727) == "<1%"   # 0.49%
    assert copy.format_ai_share(39, 10000) == "<1%"  # 0.39%
    # Neither may ever be the literal "0%".
    assert copy.format_ai_share(28, 5727) != "0%"
    assert copy.format_ai_share(39, 10000) != "0%"


def test_ge_one_percent_renders_whole_percent_unchanged():
    assert copy.format_ai_share(3, 100) == "3%"
    assert copy.format_ai_share(15, 100) == "15%"
    assert copy.format_ai_share(65, 100) == "65%"


def test_rounds_to_one_percent_when_at_least_half_a_percent():
    # 0.5% rounds UP to "1%" (round-half-to-even on 0.5 → 0 is the trap the
    # explicit nonzero floor sidesteps; banker's rounding of 0.5% * 100 = 0.5
    # would give "0%", so the helper must not regress this edge).
    assert copy.format_ai_share(6, 1000) == "1%"   # 0.6% → "1%"
    assert copy.format_ai_share(1, 200) == "<1%"   # 0.5% → rounds to 0 → floored


def test_true_zero_and_no_denominator():
    # A genuine zero share (no AI-authored commits) is honestly "0%".
    assert copy.format_ai_share(0, 5727) == "0%"
    # No denominator → em dash, as the leaderboard expects.
    assert copy.format_ai_share(5, 0) == "—"
    assert copy.format_ai_share(0, 0) == "—"


# ---------------------------------------------------------------------------
# The three render sites all route through the helper.
# ---------------------------------------------------------------------------

def _row(full, attributed, scanned, checkable, witnessed, mix):
    return {"full": full, "attributed": attributed, "scanned": scanned,
            "checkable": checkable, "witnessed": witnessed, "mix": mix}


def test_leaderboard_row_for_sub_one_percent_repo_shows_lt_one():
    rows = [
        _row("unslothai/unsloth", 28, 5727, 22, 22, [("claude", 26), ("cursor", 2)]),
        _row("langchain-ai/langchain", 39, 10000, 29, 29, [("copilot", 24), ("claude", 15)]),
    ]
    table = copy.index_leaderboard(rows)
    # The fixed cells say "<1%", and the buggy "| 0% |" cell is gone.
    assert "| <1% |" in table
    assert "| 0% |" not in table
    # The BACKED column (witnessed/checkable = 100%) is untouched and present.
    assert "| 100% |" in table


def test_aggregate_headline_floors_sub_one_percent():
    # A whole corpus that nets under 1% AI-built must not read "about 0%".
    headline = copy.index_aggregate_headline(
        repos=2, scanned=15727, attributed=67, checkable=51, witnessed=51)
    assert "**<1%**" in headline
    assert "0%" not in headline


def test_agent_share_sentence_floors_sub_one_percent():
    s = copy.agent_share_sentence(attributed=28, scanned=5727)
    assert "**<1%**" in s
    assert "**0%**" not in s
    # A >=1% share still reads as a whole percent.
    assert "**3%**" in copy.agent_share_sentence(attributed=3, scanned=100)
