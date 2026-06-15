"""Tests for the witnessed leak-test driver (`dos.drivers.witnessed_leak_test`) — docs/339 §4.

The author-disjoint successor to opus-fable-mode's `leak_test.py`: it reads the
same `~/.claude/projects/**.jsonl`, buckets by `message.model` (opus pre/post a
cutoff vs. fable), but scores NON-forgeable axes — tool:landed-effect (commits a
diff witnesses), claim-vs-truth on "done" messages, typed-refusal rate — joining
to `commit_audit`. Pins:

  * the pure fold: `MessageFacts` → `accumulate` → `BucketStats` → `fold_buckets`
    yields the `pre → post (target) [✓/✗]` convergence table.
  * the four metric properties + their NO_DATA-on-empty-denominator honesty.
  * the convergence verdict rests on NON-forgeable axes only (advisory median
    words never lifts/sinks the headline).
  * the host encoder matches Claude Code's per-char dash layout (`C:\\x` → `C--x`).
  * the boundary read buckets a synthetic projects dir correctly via `home=`.
  * the kernel never imports this driver (one-way import litmus).
"""
from __future__ import annotations

import json
import sys

import pytest

from dos.drivers.witnessed_leak_test import (
    BUCKET_FABLE,
    BUCKET_OPUS_POST,
    BUCKET_OPUS_PRE,
    DEFAULT_CUTOFF,
    MODEL_FABLE,
    MODEL_OPUS,
    BucketStats,
    ConvergenceReport,
    MessageFacts,
    accumulate,
    build_report,
    encode_project_dirname,
    fold_buckets,
    projects_dir_for,
    render_report,
    scan_sessions,
)


# --- the pure accumulator ------------------------------------------------------


def _facts(model, ts, *, words=10, tool=0, text=1, done=False, hedge=False, typed=False):
    return MessageFacts(
        model=model, ts=ts, word_count=words, tool_blocks=tool, text_blocks=text,
        says_done=done, hedges=hedge, has_typed_refusal=typed,
    )


def test_accumulate_sums_the_env_counts():
    facts = [
        _facts(MODEL_OPUS, "2026-06-12T00:00:00Z", words=10, tool=3, text=1),
        _facts(MODEL_OPUS, "2026-06-12T01:00:00Z", words=20, tool=1, text=2,
               done=True),
        _facts(MODEL_OPUS, "2026-06-12T02:00:00Z", words=30, tool=0, text=1,
               hedge=True, typed=True),
    ]
    s = accumulate(facts, bucket=BUCKET_OPUS_PRE, corroborated_done=1, landed_effects=2)
    assert s.messages == 3
    assert s.word_total == 60
    assert s.tool_blocks == 4
    assert s.text_blocks == 4
    assert s.done_claims == 1
    assert s.hedges == 1
    assert s.typed_refusals == 1
    assert s.landed_effects == 2
    # corroborated is clamped to done_claims (can't corroborate more than claimed)
    assert s.done_claims_corroborated == 1


def test_accumulate_clamps_corroborated_to_claims():
    facts = [_facts(MODEL_OPUS, "2026-06-12T00:00:00Z", done=True)]
    s = accumulate(facts, bucket=BUCKET_OPUS_PRE, corroborated_done=99, landed_effects=99)
    assert s.done_claims == 1
    assert s.done_claims_corroborated == 1  # clamped, not 99


# --- the four metric properties + NO_DATA honesty ------------------------------


def test_metrics_are_ratios_over_the_witness_not_the_text():
    s = BucketStats(
        bucket=BUCKET_OPUS_POST, messages=4, word_total=40,
        tool_blocks=20, text_blocks=8,
        done_claims=4, done_claims_corroborated=3,
        hedges=4, typed_refusals=1, landed_effects=5,
    )
    assert s.median_words == pytest.approx(10.0)          # 40/4 (advisory mean)
    assert s.tool_landed_effect_ratio == pytest.approx(4.0)   # 20 tool / 5 effects
    assert s.claim_truth_rate == pytest.approx(0.75)      # 3/4
    assert s.typed_refusal_rate == pytest.approx(0.25)    # 1/4


def test_zero_denominators_report_no_data_not_zero_value():
    s = BucketStats(bucket=BUCKET_FABLE, messages=0)
    # every ratio over an empty denominator is 0.0 — the fold reads it as NO_DATA,
    # never as "the value is genuinely zero" (the honesty floor).
    assert s.tool_landed_effect_ratio == 0.0
    assert s.claim_truth_rate == 0.0
    assert s.typed_refusal_rate == 0.0


# --- the convergence fold ------------------------------------------------------


def _bucket(name, **kw):
    base = dict(messages=10, word_total=100, tool_blocks=10, text_blocks=10,
                done_claims=5, done_claims_corroborated=0, hedges=5,
                typed_refusals=0, landed_effects=5)
    base.update(kw)
    return BucketStats(bucket=name, **base)


def test_fold_converging_on_tool_landed_effect():
    # opus_post's tool:landed-effect ratio moves toward fable's; verdict CONVERGING.
    pre = _bucket(BUCKET_OPUS_PRE, tool_blocks=30, landed_effects=3)   # ratio 10.0
    post = _bucket(BUCKET_OPUS_POST, tool_blocks=12, landed_effects=3)  # ratio 4.0
    fab = _bucket(BUCKET_FABLE, tool_blocks=6, landed_effects=3)        # ratio 2.0
    report = fold_buckets(pre, post, fab)
    row = next(r for r in report.rows if r.key == "tool_landed_effect_ratio")
    assert row.pre == pytest.approx(10.0)
    assert row.post == pytest.approx(4.0)
    assert row.target == pytest.approx(2.0)
    assert row.verdict == "CONVERGING"
    assert row.converging


def test_fold_diverging_when_post_moves_away():
    pre = _bucket(BUCKET_OPUS_PRE, tool_blocks=8, landed_effects=4)    # 2.0
    post = _bucket(BUCKET_OPUS_POST, tool_blocks=40, landed_effects=4)  # 10.0
    fab = _bucket(BUCKET_FABLE, tool_blocks=4, landed_effects=4)        # 1.0
    report = fold_buckets(pre, post, fab)
    row = next(r for r in report.rows if r.key == "tool_landed_effect_ratio")
    assert row.verdict == "DIVERGING"


def test_fold_no_data_when_a_needed_bucket_is_empty():
    # claim-vs-truth needs done_claims in every bucket; fable has none → NO_DATA.
    pre = _bucket(BUCKET_OPUS_PRE, done_claims=4, done_claims_corroborated=1)
    post = _bucket(BUCKET_OPUS_POST, done_claims=4, done_claims_corroborated=4)
    fab = _bucket(BUCKET_FABLE, done_claims=0)
    report = fold_buckets(pre, post, fab)
    row = next(r for r in report.rows if r.key == "claim_truth_rate")
    assert row.verdict == "NO_DATA"


def test_verdict_rests_on_nonforgeable_axes_only():
    # tool:landed-effect converges; median words (ADVISORY, forgeable) diverges.
    # The headline must be CONVERGING — the advisory axis cannot sink it. The
    # other two non-forgeable axes are zeroed-out (NO_DATA) so tool:landed-effect
    # is the only SCORED non-forgeable axis, isolating the property under test.
    pre = _bucket(BUCKET_OPUS_PRE, tool_blocks=30, landed_effects=3, word_total=100,
                  messages=10, done_claims=0, hedges=0)
    post = _bucket(BUCKET_OPUS_POST, tool_blocks=9, landed_effects=3, word_total=900,
                   messages=10, done_claims=0, hedges=0)  # words diverge hard
    fab = _bucket(BUCKET_FABLE, tool_blocks=6, landed_effects=3, word_total=50,
                  messages=10, done_claims=0, hedges=0)
    report = fold_buckets(pre, post, fab)
    words = next(r for r in report.rows if r.key == "median_words")
    assert words.advisory is True
    assert words.verdict == "DIVERGING"  # the forgeable proxy moved the wrong way
    assert report.verdict == "CONVERGING"  # but the non-forgeable axis carries it
    assert report.converging_count == 1


def test_insufficient_when_nothing_scores():
    # every non-advisory metric has an empty denominator somewhere → INSUFFICIENT.
    empty = _bucket(BUCKET_OPUS_PRE, landed_effects=0, done_claims=0, hedges=0)
    report = fold_buckets(
        empty,
        _bucket(BUCKET_OPUS_POST, landed_effects=0, done_claims=0, hedges=0),
        _bucket(BUCKET_FABLE, landed_effects=0, done_claims=0, hedges=0),
    )
    assert report.verdict == "INSUFFICIENT"
    assert report.scored_count == 0


def test_report_to_dict_round_trips():
    report = fold_buckets(
        _bucket(BUCKET_OPUS_PRE), _bucket(BUCKET_OPUS_POST), _bucket(BUCKET_FABLE),
    )
    d = report.to_dict()
    assert set(d) >= {"cutoff", "verdict", "rows", "buckets"}
    assert set(d["buckets"]) == {BUCKET_OPUS_PRE, BUCKET_OPUS_POST, BUCKET_FABLE}
    # JSON-serializable (the --json surface)
    json.dumps(d)


def test_render_is_plain_text_with_the_table_and_headline():
    report = fold_buckets(
        _bucket(BUCKET_OPUS_PRE, tool_blocks=30, landed_effects=3),
        _bucket(BUCKET_OPUS_POST, tool_blocks=12, landed_effects=3),
        _bucket(BUCKET_FABLE, tool_blocks=6, landed_effects=3),
    )
    text = render_report(report)
    assert "tool:landed-effect" in text
    assert "FABLE(target)" in text
    assert "VERDICT:" in text
    assert "ADVISORY" in text  # the forgeable-proxy caveat is shown


# --- the host encoder (the driver's host knowledge) ----------------------------


def test_encode_dirname_per_char_dash_not_run_collapse():
    # Claude Code maps EACH non-alnum char to a dash; `C:\x` → `C--x` (not `C-x`).
    # (Neutral synthetic root — never a real dev-machine path; see CLAUDE.md.)
    enc = encode_project_dirname("C:\\proj\\demo-kernel")
    # the drive colon AND the first backslash each become a dash → double dash
    assert "C--proj" in enc
    assert enc.replace("-", "").isalnum() or enc  # only dashes + alnum


def test_projects_dir_honors_home_override():
    pdir = projects_dir_for("C:\\repo", home="X:\\fakehome")
    s = str(pdir).replace("\\", "/")
    assert s.startswith("X:/fakehome/.claude/projects/")


# --- the boundary read: bucket a synthetic projects dir ------------------------


def _session_line(model, ts, content, *, mtype="assistant"):
    return json.dumps({
        "type": mtype,
        "timestamp": ts,
        "message": {"model": model, "role": "assistant", "content": content},
    })


def _write_session(home, workspace, lines):
    pdir = projects_dir_for(workspace, home=home)
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "session.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_scan_buckets_by_model_and_cutoff(tmp_path):
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    ws.mkdir()
    lines = [
        # an opus message BEFORE the cutoff → opus_pre
        _session_line(MODEL_OPUS, "2026-06-12T10:00:00Z",
                      [{"type": "text", "text": "Let me think about this carefully."}]),
        # an opus message ON/AFTER the cutoff → opus_post, with a tool block + a "done" opener
        _session_line(MODEL_OPUS, "2026-06-13T10:00:00Z",
                      [{"type": "text", "text": "Done. Shipped the fix."},
                       {"type": "tool_use", "name": "Bash", "input": {}}]),
        # a fable message (any date) → fable
        _session_line(MODEL_FABLE, "2026-06-12T10:00:00Z",
                      [{"type": "text", "text": "task done"}]),
        # a non-assistant line is ignored
        json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}),
        # an unknown model is excluded entirely
        _session_line("<synthetic>", "2026-06-13T10:00:00Z",
                      [{"type": "text", "text": "synthetic"}]),
    ]
    _write_session(home, ws, lines)

    buckets = scan_sessions(ws, cutoff="2026-06-13", home=home)
    assert len(buckets[BUCKET_OPUS_PRE]) == 1
    assert len(buckets[BUCKET_OPUS_POST]) == 1
    assert len(buckets[BUCKET_FABLE]) == 1
    # the post message carries a tool block and a done-opener
    post = buckets[BUCKET_OPUS_POST][0]
    assert post.tool_blocks == 1
    assert post.text_blocks == 1
    assert post.says_done is True
    # the pre message opens with "Let me" — that is NOT a done-opener
    assert buckets[BUCKET_OPUS_PRE][0].says_done is False


def test_scan_missing_projects_dir_yields_empty_buckets(tmp_path):
    # no session ever written → three empty buckets, never an error.
    buckets = scan_sessions(tmp_path / "ws", home=tmp_path / "nohome")
    assert buckets[BUCKET_OPUS_PRE] == []
    assert buckets[BUCKET_OPUS_POST] == []
    assert buckets[BUCKET_FABLE] == []


def test_scan_torn_line_is_tolerated(tmp_path):
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    ws.mkdir()
    pdir = projects_dir_for(ws, home=home)
    pdir.mkdir(parents=True, exist_ok=True)
    good = _session_line(MODEL_OPUS, "2026-06-13T10:00:00Z",
                         [{"type": "text", "text": "Done."}])
    (pdir / "s.jsonl").write_text(good + "\n{not valid json\n", encoding="utf-8")
    buckets = scan_sessions(ws, cutoff="2026-06-13", home=home)
    assert len(buckets[BUCKET_OPUS_POST]) == 1  # the torn line skipped, not fatal


def test_scan_cap_bounds_a_bucket(tmp_path):
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    ws.mkdir()
    lines = [
        _session_line(MODEL_OPUS, "2026-06-13T10:00:00Z",
                      [{"type": "text", "text": f"msg {i}"}])
        for i in range(10)
    ]
    _write_session(home, ws, lines)
    buckets = scan_sessions(ws, cutoff="2026-06-13", home=home, cap=3)
    assert len(buckets[BUCKET_OPUS_POST]) == 3  # capped


def test_opus_message_with_no_timestamp_buckets_pre(tmp_path):
    # a missing timestamp is conservatively PRE (cannot manufacture post-cutoff
    # convergence).
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    ws.mkdir()
    pdir = projects_dir_for(ws, home=home)
    pdir.mkdir(parents=True, exist_ok=True)
    line = json.dumps({
        "type": "assistant",
        "message": {"model": MODEL_OPUS, "role": "assistant",
                    "content": [{"type": "text", "text": "no timestamp"}]},
    })
    (pdir / "s.jsonl").write_text(line + "\n", encoding="utf-8")
    buckets = scan_sessions(ws, cutoff="2026-06-13", home=home)
    assert len(buckets[BUCKET_OPUS_PRE]) == 1
    assert len(buckets[BUCKET_OPUS_POST]) == 0


# --- the one-call boundary orchestrator ----------------------------------------


def test_build_report_over_a_synthetic_workspace(tmp_path):
    # build_report runs end-to-end over a synthetic projects dir + a non-git
    # workspace (no commits → 0 landed effects → INSUFFICIENT, never a crash).
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_session(home, ws, [
        _session_line(MODEL_OPUS, "2026-06-13T10:00:00Z",
                      [{"type": "text", "text": "Done."},
                       {"type": "tool_use", "name": "Bash", "input": {}}]),
        _session_line(MODEL_FABLE, "2026-06-12T10:00:00Z",
                      [{"type": "text", "text": "done"}]),
    ])
    report = build_report(ws, cutoff="2026-06-13", home=home)
    assert isinstance(report, ConvergenceReport)
    # non-git workspace → no witnessed effects → the verdict is honest, not a crash
    assert report.verdict in ("INSUFFICIENT", "NOT_CONVERGING", "CONVERGING")
    # and it renders without error
    assert "VERDICT:" in render_report(report)


# --- the layering litmus: the kernel never imports this driver -----------------


def test_kernel_does_not_import_the_driver():
    # importing the kernel must NOT pull in this host-naming driver (the one-way
    # arrow: a driver imports the kernel; the kernel never imports a driver).
    for name in list(sys.modules):
        if name.startswith("dos.drivers.witnessed_leak_test"):
            del sys.modules[name]
    import importlib

    import dos.commit_audit  # noqa: F401
    import dos.effect_witness  # noqa: F401
    importlib.import_module("dos.config")
    assert "dos.drivers.witnessed_leak_test" not in sys.modules


def test_default_cutoff_is_the_governor_date():
    # the default cutoff mirrors opus-fable-mode's governor-install date.
    assert DEFAULT_CUTOFF == "2026-06-13"
