"""Pins for scoreboard_charts — the inline-SVG front-door visuals.

The charts are dev tooling under `scripts/` (loaded by path, like the other
scoreboard generators). These tests guard the load-bearing properties: the SVG
is self-contained (no JS, no external fetch, no CSS variables — it must render
on GitHub Markdown AND the Pages site), every number comes from the caller, and
the render is byte-deterministic so the committed assets stay reproducible.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "scoreboard_charts", REPO / "scripts" / "scoreboard_charts.py")
charts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(charts)


# A small fixed row set covering: a high-share repo, a sub-1% repo, an unmapped
# agent (folds to OTHER), and a multi-agent mix.
ROWS = [
    {"full": "kenn-io/roborev", "attributed": 432, "scanned": 668,
     "mix": [("claude", 430), ("copilot", 1), ("cursor", 1)],
     "checkable": 273, "witnessed": 273},
    {"full": "openai/codex", "attributed": 344, "scanned": 6800,
     "mix": [("codex", 331), ("claude", 10), ("copilot", 3)],
     "checkable": 155, "witnessed": 155},
    {"full": "tiny/repo", "attributed": 5, "scanned": 5000,
     "mix": [("someNewAgent", 5)], "checkable": 4, "witnessed": 4},
]


# ---------------------------------------------------------------------------
# self-contained SVG — the cross-renderer contract.
# ---------------------------------------------------------------------------

def _all_svgs():
    return [
        charts.ai_share_chart(ROWS),
        charts.agent_mix_chart(ROWS),
        charts.claim_kind_chart(
            {"code_effect": 1240, "test": 41, "doc": 269, "none": 984},
            backed=1550, checkable=1553),
    ]


def test_svgs_are_self_contained():
    for svg in _all_svgs():
        assert svg.startswith("<svg")
        assert svg.rstrip().endswith("</svg>")
        # No JS, no external resources, no CSS custom properties — GitHub
        # Markdown strips <style>/<script> and ignores var(); the Pages site
        # renders the same file. So none of these may appear. (The mandatory
        # SVG xmlns is the one allowed http URL — strip it before scanning, it
        # is a namespace declaration, never a fetch.)
        scan = svg.replace('xmlns="http://www.w3.org/2000/svg"', "")
        for forbidden in ("<script", "var(--", "<style", "http://", "https://",
                           "onload", "xlink:href", "<foreignObject"):
            assert forbidden not in scan, f"{forbidden!r} leaked into the SVG"
        # explicit literal colours only (we sampled the palette constants).
        assert charts.ACCENT in svg or charts.GREEN in svg or "#" in svg


def test_render_is_deterministic():
    # Same input twice → byte-identical (no clock, no random, stable float fmt).
    for fn in (lambda: charts.ai_share_chart(ROWS),
               lambda: charts.agent_mix_chart(ROWS)):
        assert fn() == fn()


def test_numbers_come_from_the_caller():
    # The true percent is printed, and a sub-0.5% share floors to "<1%", never
    # "0%" — the same honesty floor scoreboard_copy.format_ai_share keeps.
    svg = charts.ai_share_chart(ROWS)
    assert "65%" in svg          # 432/668
    assert "&lt;1%" in svg       # 5/5000 → 0.1%, escaped for SVG/XML
    assert "0%" not in svg.replace("&lt;1%", "")  # no bare "0%" cell


def test_ai_share_drops_rows_with_no_denominator():
    rows = [{"full": "x/y", "attributed": 3, "scanned": 0}]
    assert charts.ai_share_chart(rows) == ""


def test_agent_color_is_fixed_and_falls_back():
    assert charts.agent_color("claude") == charts.AGENT_COLORS["claude"]
    assert charts.agent_color("CLAUDE") == charts.AGENT_COLORS["claude"]  # ci
    assert charts.agent_color("never-heard-of-it") == charts.OTHER


def test_agent_mix_normalizes_and_uses_the_color_map():
    svg = charts.agent_mix_chart(ROWS)
    # the dominant agent's colour appears; the unmapped agent uses OTHER.
    assert charts.AGENT_COLORS["claude"] in svg
    assert charts.AGENT_COLORS["codex"] in svg
    assert charts.OTHER in svg  # someNewAgent → OTHER


def test_claim_kind_excludes_the_skipped_bucket():
    svg = charts.claim_kind_chart(
        {"code_effect": 100, "test": 10, "doc": 5, "none": 999})
    # the three checkable kinds are drawn; the 999 'none' is not in the bar's
    # denominator (115 total), so its colour/label is absent.
    assert charts.KIND_LABELS["code_effect"][:4] in svg
    assert "no checkable claim" not in svg


def test_agent_colors_are_distinct():
    # Every mapped agent (plus OTHER) is a distinct colour — two agents sharing
    # a near-identical tone read as one segment in a stacked bar (the crush/devin
    # collision this guards against). Exact-equality is the floor; the palette is
    # hand-tuned for side-by-side separation beyond that.
    colours = list(charts.AGENT_COLORS.values()) + [charts.OTHER]
    assert len(colours) == len(set(colours)), "two agents share a colour"


def test_backed_fact_has_no_raw_apostrophe_entity():
    # The headline trust fact under the bar carries no apostrophe — an escaped
    # &#x27; renders in a browser but is noise in the markup / a non-HTML reader.
    svg = charts.claim_kind_chart(
        {"code_effect": 100, "test": 10, "doc": 5}, backed=110, checkable=115)
    assert "backed by the diff" in svg
    assert "&#x27;" not in svg and "&#39;" not in svg


def test_empty_inputs_render_nothing():
    assert charts.ai_share_chart([]) == ""
    assert charts.agent_mix_chart([]) == ""
    assert charts.claim_kind_chart({}) == ""
    assert charts.claim_kind_chart({"none": 50}) == ""  # only skipped → nothing


def test_svg_has_accessible_title():
    # role=img + a <title>/<desc> so the chart is not an opaque blob to a
    # screen reader or a crawler.
    for svg in _all_svgs():
        assert 'role="img"' in svg
        assert "<title>" in svg and "<desc>" in svg
        assert "aria-label=" in svg
