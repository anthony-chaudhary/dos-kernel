"""tool_trace — the per-call observability spine (docs/361).

The falsifiable proof docs/361 §6 specified: a synthetic session with records
keyed to one call across the four logs folds into exactly ONE joined row carrying
decision + stall + spend together; a call present in only one log yields PARTIAL;
a stray record yields ORPHAN (never silently dropped). Plus the byte-clean
property (only env/runtime fields drive the join), the ordering contract, and the
honest-empty floor.

This is a PURE fold over plain dicts — no disk, no clock — so every test here
runs with zero benchmark/hook access (the `test_model_call` posture).
"""

from __future__ import annotations

import json

from dos import tool_trace as tt
from dos.tool_trace import JoinState, TracePolicy, correlate_calls


# ---------------------------------------------------------------------------
# Record builders — the exact shapes the four writers produce.
# ---------------------------------------------------------------------------
def _obs(run_id="R", ts="2026-06-16T10:00:00Z", verb="posttool",
         outcome="warn", rung="stall", **extra):
    """A hook-observation record (hook_observation._observation_entry shape)."""
    e = {
        "schema": {"family": "hook-observation", "version": 1},
        "op": "OBSERVE", "verb": verb, "outcome": outcome,
        "exit": 0, "latency_ms": 12.5, "ts": ts, "run_id": run_id, "rung": rung,
    }
    e.update(extra)
    return e


def _step(run_id="R", step_index=2, ts="2026-06-16T10:00:00Z",
          tool_name="Read", result_digest="deedb29c", verdict_state="REPEATING",
          **extra):
    """A posttool stream RECORD (posttool_sensor._step_entry shape — carries the
    run_id + step_index join spine, NOT the lossy read_stream reconstruction)."""
    e = {
        "schema": {"family": "tool-stream", "version": 1},
        "op": "STEP", "tool_name": tool_name, "args_digest": "a1b2c3d4",
        "result_digest": result_digest, "run_id": run_id,
        "step_index": step_index, "verdict_state": verdict_state, "ts": ts,
    }
    e.update(extra)
    return e


def _mc(run_id="R", ts="2026-06-16T10:00:00Z", model="claude-opus",
        duration_ms=1200.0, output=300, input=900, **extra):
    """A model-call record (model_call.model_call_entry shape, spend flattened)."""
    e = {
        "schema": {"family": "model-call", "version": 1},
        "op": "MODELCALL", "model": model, "duration_ms": duration_ms,
        "ts": ts, "run_id": run_id, "input": input, "output": output,
    }
    e.update(extra)
    return e


# ===========================================================================
# THE DOCS/361 §6 FALSIFIABLE PROOF — the headline test.
# ===========================================================================
def test_docs361_proof_one_call_joins_across_three_logs():
    """Three records keyed to one call → exactly one JOINED row with decision +
    stall + spend on the SAME row; a one-log-only call → PARTIAL; a stray
    model-call → ORPHAN (never dropped). The exact docs/361 §6 proof."""
    observations = [_obs(run_id="R", outcome="warn", rung="stall")]
    stream = [_step(run_id="R", step_index=2, verdict_state="REPEATING")]
    model_calls = [
        _mc(run_id="R", duration_ms=1200.0, output=300, input=900),  # joins
        _mc(run_id="STRAY", model="claude-haiku"),                   # orphan
    ]
    # a second call present ONLY in the observation log → PARTIAL is exercised by
    # an extra spine step with no siblings:
    stream.append(_step(run_id="R", step_index=5, tool_name="Bash",
                        ts="2026-06-16T10:05:00Z"))

    trace = correlate_calls(observations, stream, model_calls, run_id="R")

    joined = [r for r in trace.rows if r.state is JoinState.JOINED]
    partial = [r for r in trace.rows if r.state is JoinState.PARTIAL]
    orphan = [r for r in trace.rows if r.state is JoinState.ORPHAN]

    # under run=R the STRAY model-call is filtered out before folding, so no
    # orphans survive the run-scoped trace:
    assert orphan == []

    # exactly one JOINED row, carrying decision + stall + spend together:
    assert len(joined) == 1
    row = joined[0]
    assert row.outcome == "warn"
    assert row.stall_state == "REPEATING"
    assert row.model_spend_tokens == 1200  # 900 input + 300 output, on the SAME row
    assert row.model_duration_ms == 1200.0
    assert row.tool_name == "Read"
    assert set(row.sources) == {"stream", "observation", "model_call"}

    # the spine step with no siblings is PARTIAL:
    assert len(partial) == 1
    assert partial[0].tool_name == "Bash"
    assert partial[0].step_index == 5

    # the STRAY model-call (run_id filtered out, then surfaced) — under run=R it
    # is dropped by the run filter, so prove ORPHAN without the run filter:
    trace_all = correlate_calls(observations, stream, model_calls)
    orphans_all = [r for r in trace_all.rows if r.state is JoinState.ORPHAN]
    assert any(r.model == "claude-haiku" and r.run_id == "STRAY"
               for r in orphans_all), "a stray model-call must surface as ORPHAN"


def test_orphan_observation_is_surfaced_never_dropped():
    """An observation whose run_id matches no spine step is an ORPHAN row, not a
    silent drop — the seam is visible (docs/361 §4)."""
    stream = [_step(run_id="R", step_index=0)]
    observations = [_obs(run_id="R", step_index=0),
                    _obs(run_id="GHOST", outcome="deny", rung="self_modify")]
    trace = correlate_calls(observations, stream, [])
    orphans = [r for r in trace.rows if r.state is JoinState.ORPHAN]
    assert any(r.run_id == "GHOST" and r.outcome == "deny" for r in orphans)
    # and the join report counts it honestly:
    assert trace.report.by_log_unmatched["observations"] == 1


# ===========================================================================
# BYTE-CLEAN — only env/runtime fields drive the join; agent fields ride along.
# ===========================================================================
def test_join_never_crosses_run_id():
    """Two records with different run_ids are NEVER the same call, whatever their
    ts (the docs/297 like-for-like hard floor)."""
    stream = [_step(run_id="A", step_index=0, ts="2026-06-16T10:00:00Z")]
    # same ts, different run — must NOT join:
    observations = [_obs(run_id="B", ts="2026-06-16T10:00:00Z")]
    trace = correlate_calls(observations, stream, [])
    joined = [r for r in trace.rows if r.state is JoinState.JOINED]
    assert not joined, "a cross-run ts coincidence must not fabricate a join"
    # the spine step is PARTIAL, the observation is ORPHAN:
    assert any(r.state is JoinState.PARTIAL and r.run_id == "A" for r in trace.rows)
    assert any(r.state is JoinState.ORPHAN and r.run_id == "B" for r in trace.rows)


def test_agent_authored_fields_ride_along_never_drive_join():
    """tool_name/args_digest are AGENT-authored: surfaced on the row, but the
    join is on run_id (env/runtime), so two calls with identical args on
    different steps stay distinct rows (docs/145 provenance split)."""
    stream = [
        _step(run_id="R", step_index=0, tool_name="Read", args_digest="SAME"),
        _step(run_id="R", step_index=1, tool_name="Read", args_digest="SAME"),
    ]
    trace = correlate_calls([], stream, [])
    rows = [r for r in trace.rows if r.state is JoinState.PARTIAL]
    assert len(rows) == 2, "identical agent args must not collapse two calls"
    assert [r.step_index for r in rows] == [0, 1]


def test_reasoning_not_double_counted_in_tokens():
    """`reasoning` is a SUB-count of output, so token total must not add it
    (the SpendBreakdown canonical form — no double count)."""
    stream = [_step(run_id="R", step_index=0)]
    model_calls = [_mc(run_id="R", input=100, output=200, reasoning=50)]
    trace = correlate_calls([], stream, model_calls)
    joined = [r for r in trace.rows if r.state is JoinState.JOINED][0]
    assert joined.model_spend_tokens == 300  # 100 + 200, NOT + 50


# ===========================================================================
# ORDERING + EMPTY FLOOR + ts WINDOW.
# ===========================================================================
def test_rows_ordered_by_run_then_step_index():
    """The replay is ordered by (run_id, step_index, ts) — the 'what happened in
    order' question."""
    stream = [
        _step(run_id="R", step_index=5, tool_name="fifth"),
        _step(run_id="R", step_index=1, tool_name="first"),
        _step(run_id="R", step_index=3, tool_name="third"),
    ]
    trace = correlate_calls([], stream, [])
    names = [r.tool_name for r in trace.rows]
    assert names == ["first", "third", "fifth"]


def test_empty_logs_render_honest_floor():
    """No records → an empty, honest trace (a read-only surface shows what it
    has, never an error)."""
    trace = correlate_calls([], [], [])
    assert trace.rows == ()
    assert trace.report.joined == 0
    text = tt.render_text(trace)
    assert "no tool calls recorded" in text


def test_effect_verdict_displayed_never_derived():
    """An effect_witness verdict, when persisted, is DISPLAYED on the matching
    row keyed by (run_id, step_index) — never derived here (consume, don't
    re-derive)."""
    stream = [_step(run_id="R", step_index=2)]
    evs = [{"run_id": "R", "step_index": 2, "verdict": "CONFIRMED"}]
    trace = correlate_calls([], stream, [], effect_verdicts=evs)
    row = [r for r in trace.rows if r.step_index == 2][0]
    assert row.effect_verdict == "CONFIRMED"


def test_ts_window_can_only_tighten_never_loosen():
    """A positive ts_window rejects a far-apart same-run match (tightening); it
    never reaches across run_id (loosening is impossible)."""
    stream = [_step(run_id="R", step_index=0, ts="2026-06-16T10:00:00Z")]
    # observation 10 minutes later, same run:
    observations = [_obs(run_id="R", ts="2026-06-16T10:10:00Z")]
    # window 0 (default) = run_id-only join → they reconcile:
    wide = correlate_calls(observations, stream, [])
    assert any(r.state is JoinState.JOINED for r in wide.rows)
    # a 60s window rejects the 10-min-apart match → PARTIAL + ORPHAN:
    tight = correlate_calls(observations, stream, [],
                            policy=TracePolicy(ts_window_s=60.0))
    assert not any(r.state is JoinState.JOINED for r in tight.rows)
    assert any(r.state is JoinState.PARTIAL for r in tight.rows)
    assert any(r.state is JoinState.ORPHAN for r in tight.rows)


def test_render_shows_decision_stall_and_spend_on_one_line():
    """The render is the concept-is-named proof: one line per call showing
    decision + stall + spend together (the operator question with no verb)."""
    stream = [_step(run_id="R", step_index=2, tool_name="Read",
                    verdict_state="REPEATING")]
    observations = [_obs(run_id="R", outcome="warn", rung="stall")]
    model_calls = [_mc(run_id="R", output=300, input=900)]
    trace = correlate_calls(observations, stream, model_calls, run_id="R")
    text = tt.render_text(trace)
    assert "Read" in text
    assert "warn" in text and "stall" in text
    assert "REPEATING" in text
    assert "1200" in text  # tokens on the same row
    assert "JOINED" in text


# ===========================================================================
# THE RAW-RECORD READER — the docs/361 persistence-nuance helper.
# ===========================================================================
def test_read_stream_records_preserves_join_fields(tmp_path):
    """`read_stream_records` returns RAW records keeping run_id/step_index — the
    fields `read_stream`'s bare StreamStep reconstruction drops (docs/361)."""
    from dos import posttool_sensor as pts

    f = tmp_path / "sess.jsonl"
    rec = pts._step_entry(
        pts.StreamStep(tool_name="Read", args_digest="a1", result_digest="d1"),
        run_id="R", step_index=2, verdict_state="REPEATING",
    )
    rec["ts"] = "2026-06-16T10:00:00Z"
    f.write_text(json.dumps(rec) + "\n", encoding="utf-8")

    # the raw reader keeps the join fields:
    raw = pts.read_stream_records("", path=f)
    assert len(raw) == 1
    assert raw[0]["run_id"] == "R"
    assert raw[0]["step_index"] == 2
    assert raw[0]["verdict_state"] == "REPEATING"

    # whereas read_stream drops them (the lossy reconstruction the doc warns of):
    steps = pts.read_stream("", path=f).steps
    assert len(steps) == 1
    assert not hasattr(steps[0], "run_id")  # StreamStep has no run_id field


def test_read_all_stream_records_missing_dir_is_empty(tmp_path):
    """A read fault / missing streams dir degrades to [] — never a crash."""
    from dos import posttool_sensor as pts

    out = pts.read_stream_records("", path=tmp_path / "nope.jsonl")
    assert out == []


# ===========================================================================
# CLI SMOKE — the concept-is-named entry point exists and folds end-to-end.
# ===========================================================================
def test_cli_tool_trace_smoke(tmp_path, capsys):
    """`dos tool-trace --run R` folds the real on-disk logs into the ordered
    replay — the operator entry point, end to end (the `dos model-calls` smoke
    posture). Writes one joined call's records, then reads them back via the
    verb's JSON shape by calling `cli.main` in-process (tests this tree's code)."""
    from dos import cli as _cli
    from dos import config as _config
    from dos import hook_observation as hobs
    from dos import model_call as mc
    from dos import posttool_sensor as pts
    from dos.spend import SpendBreakdown

    cfg = _config.load_workspace_config(tmp_path)
    # one call across three logs, same run_id:
    hobs.append(hobs.observation_entry("posttool", "warn", rung="stall",
                                       run_id="RUN1", ts="2026-06-16T10:00:00Z"),
                cfg=cfg, debug=True)
    pts.append_step("sessA",
                    pts.StreamStep(tool_name="Read", args_digest="a1",
                                   result_digest="d1"),
                    cfg, run_id="RUN1", step_index=0, verdict_state="REPEATING")
    mc.append(mc.model_call_entry("claude-opus", 1200.0,
                                  spend=SpendBreakdown(input=900, output=300),
                                  run_id="RUN1", ts="2026-06-16T10:00:00Z"),
              cfg=cfg, debug=True)

    rc = _cli.main(["--workspace", str(tmp_path),
                    "tool-trace", "--run", "RUN1", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run"] == "RUN1"
    joined = [r for r in payload["rows"] if r["state"] == "JOINED"]
    assert len(joined) == 1
    assert joined[0]["outcome"] == "warn"
    assert joined[0]["stall_state"] == "REPEATING"
    assert joined[0]["model_spend_tokens"] == 1200
