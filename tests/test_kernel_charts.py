"""Pins for kernel_charts — the inline-SVG views of the kernel's OWN behaviour.

These three charts (disbelief funnel, self-modify wall, memory recall) are the
mirror of the scoreboard charts: they draw what the kernel did to itself, folded
live from this repo's `.dos/` journals. Same load-bearing contract as
`test_scoreboard_charts.py`: the SVG is self-contained (no JS, no <style>, no
CSS variables, no external fetch — it must render on GitHub Markdown AND the
Pages site AND rasterize to a PNG), every number comes from the caller's fold,
and the render is byte-deterministic so a regenerated asset is reproducible.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


charts = _load("kernel_charts")
metrics = _load("kernel_metrics")


# Fixed fold-shaped inputs covering the real shapes: a huge pass slice + a tiny
# stopped tail, a self-modify tally dominated by one module, and a recall split
# with a single stale sliver.
OUTCOMES = {
    "passthrough": 56000, "delegate": 16000, "no-claims": 800,
    "warn": 720, "deny": 308, "let-active": 58, "override-admit": 17,
    "block": 2,
}
SELF_MODIFY = {
    "total": 296, "block": 198, "warn": 98, "got_through": 0,
    "by_module": [("arbiter.py", 215), ("config.py", 48),
                  ("loop_decide.py", 14), ("self_modify.py", 8)],
}
RECALL = {"RECALL_FRESH": 20, "RECALL_UNVERIFIABLE": 54, "RECALL_STALE": 1}


def _all_svgs():
    return [
        charts.disbelief_funnel_chart(OUTCOMES, pretool_p50_ms=7.3),
        charts.self_modify_wall_chart(SELF_MODIFY, enforce_block=332,
                                      enforce_warn=727, n_entries=1101),
        charts.memory_recall_chart(RECALL),
    ]


# ---------------------------------------------------------------------------
# the cross-renderer contract — self-contained SVG.
# ---------------------------------------------------------------------------

def test_svgs_are_self_contained():
    for svg in _all_svgs():
        assert svg.startswith("<svg")
        assert svg.rstrip().endswith("</svg>")
        # strip the mandatory namespace decl (the one allowed http URL), then
        # assert nothing GitHub Markdown would strip or that needs a fetch.
        scan = svg.replace('xmlns="http://www.w3.org/2000/svg"', "")
        for forbidden in ("<script", "var(--", "<style", "http://", "https://",
                          "onload", "xlink:href", "<foreignObject"):
            assert forbidden not in scan, f"{forbidden!r} leaked into the SVG"


def test_render_is_deterministic():
    # Same input twice → byte-identical (no clock, no random, stable float fmt).
    for fn in (lambda: charts.disbelief_funnel_chart(OUTCOMES, pretool_p50_ms=7.3),
               lambda: charts.self_modify_wall_chart(SELF_MODIFY),
               lambda: charts.memory_recall_chart(RECALL)):
        assert fn() == fn()


def test_svg_has_accessible_title():
    for svg in _all_svgs():
        assert 'role="img"' in svg
        assert "<title>" in svg and "<desc>" in svg
        assert "aria-label=" in svg


# ---------------------------------------------------------------------------
# the numbers come from the caller — never authored in the builder.
# ---------------------------------------------------------------------------

def test_funnel_numbers_come_from_the_caller():
    svg = charts.disbelief_funnel_chart(OUTCOMES, pretool_p50_ms=7.3)
    total = sum(OUTCOMES.values())          # 73,908
    stopped = OUTCOMES["warn"] + OUTCOMES["deny"] + OUTCOMES["block"]  # 1,030
    assert f"{total:,}" in svg              # the headline total
    assert f"{stopped:,}" in svg           # the magnified stopped count
    assert "7.3ms" in svg                  # the median latency the caller passed
    # override-admit lets a call THROUGH, so it is NOT in the stopped count.
    assert stopped == 1030


def test_funnel_other_bucket_is_the_unnamed_remainder():
    # everything the sensor emitted that is not a named stream segment is the
    # honest "other" bucket — the bar's segments sum to the true total.
    svg = charts.disbelief_funnel_chart(OUTCOMES)
    total = sum(OUTCOMES.values())
    assert f"{total:,}" in svg


def test_self_modify_wall_shows_the_zero_and_the_module():
    svg = charts.self_modify_wall_chart(SELF_MODIFY, enforce_block=332,
                                        enforce_warn=727, n_entries=1101)
    assert "0 got through" in svg          # the wall held
    assert "arbiter.py" in svg             # the dominant module is named
    assert "the module doing the refusing" in svg
    assert "296" in svg                    # the self-modify total
    assert "1,101" in svg                  # the journal entry count (caller)


def test_memory_recall_headline_is_the_true_fresh_rate():
    svg = charts.memory_recall_chart(RECALL)
    assert "20 of 75" in svg               # fresh / total, both from the fold
    assert "STALE" in svg                  # the single stale sliver is surfaced
    assert "75 recalled" in svg


# ---------------------------------------------------------------------------
# empty / degenerate inputs render nothing — never a misleading empty chart.
# ---------------------------------------------------------------------------

def test_empty_inputs_render_nothing():
    assert charts.disbelief_funnel_chart({}) == ""
    assert charts.self_modify_wall_chart({"total": 0}) == ""
    assert charts.memory_recall_chart({}) == ""
    assert charts.memory_recall_chart({"RECALL_FRESH": 0}) == ""


def test_label_fit_helper_is_conservative():
    # a label that obviously overflows a tiny box does not "fit"; one that
    # obviously fits a wide box does — the guard that keeps text on-panel.
    assert not charts._fits("a very long label indeed", 20)
    assert charts._fits("ok", 200)


# ---------------------------------------------------------------------------
# the folds are real + fail-soft — a missing journal is a zero fact, not a crash.
# ---------------------------------------------------------------------------

def test_folds_are_failsoft_on_missing_files(tmp_path):
    missing = tmp_path / "nope.jsonl"
    assert metrics.fold_observations(missing)["total"] == 0
    assert metrics.fold_lane_journal(missing)["self_modify"]["total"] == 0
    assert metrics.fold_verdict_journal(missing)["recall"] == {}


def test_fold_observations_counts_outcomes(tmp_path):
    p = tmp_path / "obs.jsonl"
    p.write_text(
        '{"outcome": "passthrough", "verb": "pretool", "latency_ms": 5}\n'
        '{"outcome": "deny", "verb": "pretool", "latency_ms": 9}\n'
        '{"outcome": "warn", "verb": "posttool", "latency_ms": 2}\n'
        "garbage not json\n",
        encoding="utf-8")
    fold = metrics.fold_observations(p)
    assert fold["total"] == 3                # the junk line is skipped
    assert fold["outcomes"]["deny"] == 1
    assert fold["stopped"] == 2              # warn + deny (no block here)


def test_fold_lane_journal_extracts_self_modify_module(tmp_path):
    p = tmp_path / "lane.jsonl"
    p.write_text(
        '{"op": "ENFORCE", "intervention": "BLOCK", "reason_class": '
        '"SELF_MODIFY", "reason": "lane would edit src/dos/arbiter.py — no"}\n'
        '{"op": "ENFORCE", "intervention": "WARN", "reason_class": '
        '"SELF_MODIFY", "reason": "edits src/dos/config.py advisory"}\n',
        encoding="utf-8")
    sm = metrics.fold_lane_journal(p)["self_modify"]
    assert sm["total"] == 2
    assert sm["block"] == 1 and sm["warn"] == 1
    assert ("arbiter.py", 1) in sm["by_module"]
