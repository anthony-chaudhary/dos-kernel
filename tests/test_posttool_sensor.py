"""docs/173 §4/§5 — the PostToolUse tool_stream sensor: the in-flight half of dos.tool_stream.

The pure verdict `tool_stream.classify_stream` already ships and is green; this
exercises the BOUNDARY adapter that turns it from an offline-only detector into a
live advisory WARN: the pure event→`StreamStep` adapter, the pure
`StreamVerdict`→Claude-Code-dialect renderer, the session-scoped accumulator
(append/replay, schema-gated + torn-tail-tolerant like `intent_ledger`), and the
end-to-end CLI command.

The docs/165 §5 self-certification trap is the enemy: these tests do NOT merely
assert "the bytes match the shape DOS chose." The `warn_payload` tests assert the
EXACT shape REAL Claude Code honors (`hookSpecificOutput`/`hookEventName ==
"PostToolUse"`/`additionalContext`) — the anti-no-op assertion, because the sibling
`dos hook stop` is a SILENT NO-OP against real CC for emitting the wrong dialect
(`{"ok": false}`). The end-to-end test IS the in-flight twin of
`benchmark/toolathlon/dos_solves_output_poll.py`: five identical-result events then
one different, fed one at a time through the live CLI.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from dos import config as _config
from dos import durable_schema as _ds
from dos import posttool_sensor as pts
from dos.tool_stream import StreamState, StreamStep, StreamVerdict, ToolStream, classify_stream


# ==========================================================================
# step_from_event — the PURE event→StreamStep adapter.
# ==========================================================================


def _event(result, *, key="tool_response", tool="Read", tool_input=None, session="S1"):
    """A minimal PostToolUse event. `key` chooses which result key carries the bytes."""
    e = {
        "session_id": session,
        "tool_name": tool,
        "tool_input": tool_input if tool_input is not None else {"file_path": "x.output"},
    }
    if key is not None:
        e[key] = result
    return e


def test_step_from_event_extracts_digests():
    step = pts.step_from_event(_event("hello bytes"))
    assert isinstance(step, StreamStep)
    assert step.tool_name == "Read"
    assert isinstance(step.args_digest, str) and step.args_digest
    assert isinstance(step.result_digest, str) and step.result_digest


def test_step_from_event_reads_tool_response_key():
    """The current-docs result key (`tool_response`) is read."""
    step = pts.step_from_event(_event("payload", key="tool_response"))
    assert step is not None
    assert step.result_digest is not None


def test_step_from_event_reads_tool_output_key():
    """The alternate/older build result key (`tool_output`) is read too — the
    MANDATORY dual-read (docs/173 §4). A build that emits `tool_output` must not
    read back as 'no result'."""
    step = pts.step_from_event(_event("payload", key="tool_output"))
    assert step is not None
    assert step.result_digest is not None


def test_tool_response_and_tool_output_with_same_bytes_digest_equally():
    """The dual-read is value-faithful: the SAME bytes under either key produce the
    SAME result_digest (so a stream that mixes the two keys still detects a repeat)."""
    a = pts.step_from_event(_event("same bytes", key="tool_response"))
    b = pts.step_from_event(_event("same bytes", key="tool_output"))
    assert a.result_digest == b.result_digest


def test_step_from_event_none_when_no_tool_name():
    """No tool_name → nothing to record (not a tool call / malformed)."""
    assert pts.step_from_event({"session_id": "S1", "tool_response": "x"}) is None
    assert pts.step_from_event({"tool_name": "", "tool_response": "x"}) is None
    assert pts.step_from_event("not a dict") is None  # type: ignore[arg-type]


def test_step_from_event_result_digest_none_when_no_result():
    """A call that returned nothing (NEITHER result key) → result_digest None — the
    fail-safe break (None never matches another step)."""
    step = pts.step_from_event(_event(None, key=None))  # no result key at all
    assert step is not None
    assert step.result_digest is None
    # An explicit null result is also 'no result' (the safe direction).
    step2 = pts.step_from_event({"tool_name": "Read", "tool_input": {}, "tool_response": None})
    assert step2.result_digest is None


def test_identical_results_produce_identical_keys():
    """Two events with the SAME tool, args, and env result bytes produce the SAME
    (args_digest, result_digest) — the repeat-identity that `tool_stream` keys on."""
    a = pts.step_from_event(_event("unchanged\n"))
    b = pts.step_from_event(_event("unchanged\n"))
    assert a.args_digest == b.args_digest
    assert a.result_digest == b.result_digest


def test_different_result_bytes_break_the_key():
    """Different ENV result bytes → different result_digest (the byte-clean break).
    The agent's CALL is identical; only the env-authored bytes decide repeat vs
    advance (the docs/138 invariant: the agent cannot forge this)."""
    a = pts.step_from_event(_event("old output\n"))
    b = pts.step_from_event(_event("new output\n"))
    assert a.args_digest == b.args_digest  # same call
    assert a.result_digest != b.result_digest  # different env bytes


def test_args_digest_separates_different_calls():
    """Different tool_input → different args_digest (two reads of DIFFERENT rows are
    not a repeat even if the env returned the same bytes)."""
    a = pts.step_from_event(_event("same\n", tool_input={"file_path": "a"}))
    b = pts.step_from_event(_event("same\n", tool_input={"file_path": "b"}))
    assert a.args_digest != b.args_digest


def test_args_digest_is_key_order_invariant():
    """The args digest is over the NORMALIZED (sorted-key) input, so key order in
    the event's tool_input does not change the digest."""
    a = pts.step_from_event(_event("r", tool_input={"a": 1, "b": 2}))
    b = pts.step_from_event(_event("r", tool_input={"b": 2, "a": 1}))
    assert a.args_digest == b.args_digest


# ==========================================================================
# warn_payload — the PURE StreamVerdict→Claude-Code-dialect renderer.
# This is the ANTI-NO-OP surface: the sibling `dos hook stop` is a SILENT NO-OP
# against real CC because it emits `{"ok": false}`, a dialect CC ignores. These
# tests pin the EXACT dialect real CC HONORS (docs/165 §5 self-cert trap).
# ==========================================================================


def _verdict(state: StreamState, run: int = 4) -> StreamVerdict:
    step = StreamStep(tool_name="Read", args_digest="aaaa", result_digest="bbbb")
    return StreamVerdict(
        state=state, repeat_run=run,
        repeated_step=(step if state is not StreamState.ADVANCING else None),
        reason="the same triple repeated",
    )


def test_warn_payload_repeating_emits_the_exact_cc_dialect():
    # ANTI-NO-OP: assert the shape REAL Claude Code honors, not a shape DOS invented.
    # `dos hook stop`'s `{"ok": false}` is invisible to CC; this MUST be the honored
    # PostToolUse `additionalContext` envelope, field names case-exact (docs/173 §4).
    out = pts.warn_payload(_verdict(StreamState.REPEATING))
    assert isinstance(out, dict)
    assert set(out.keys()) == {"hookSpecificOutput"}  # EXACT top-level key, nothing else
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse"  # the exact value CC matches on
    assert isinstance(hso["additionalContext"], str) and hso["additionalContext"]
    assert "REPEATING" in hso["additionalContext"]
    assert "Read" in hso["additionalContext"]  # names the repeated tool
    assert "4" in hso["additionalContext"]  # the repeat_run count is surfaced


def test_warn_payload_stalled_emits_the_exact_cc_dialect():
    out = pts.warn_payload(_verdict(StreamState.STALLED, run=5))
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "STALLED" in out["hookSpecificOutput"]["additionalContext"]


def test_warn_payload_advancing_is_none():
    """ADVANCING → emit nothing (None). The no-action verdict."""
    assert pts.warn_payload(_verdict(StreamState.ADVANCING)) is None


def test_warn_payload_never_tells_the_agent_to_stop():
    """The re-surface is advisory: it points at waiting / using the held value, never
    a command to stop (a legitimate poll must not be cut — docs/99 / the honest
    eventual-consistency hole)."""
    text = pts.warn_payload(_verdict(StreamState.STALLED, run=9))["hookSpecificOutput"][
        "additionalContext"
    ].lower()
    assert "stop" not in text.replace("do not re-issue", "")  # no "stop" command
    assert "wait" in text  # it advises waiting for a completion signal


# ==========================================================================
# the accumulator — append/read_stream round-trip + schema gate + torn tail.
# ==========================================================================


def _cfg(tmp_path: Path):
    return _config.default_config(tmp_path)


def _path(tmp_path: Path) -> Path:
    return tmp_path / "S1.jsonl"


def test_append_then_read_round_trips_in_order(tmp_path: Path):
    p = _path(tmp_path)
    steps = [
        StreamStep("Read", "a1", "r1"),
        StreamStep("Read", "a1", "r1"),
        StreamStep("Grep", "a2", None),  # a step with no result
    ]
    for s in steps:
        pts.append_step("S1", s, path=p)
    stream = pts.read_stream("S1", path=p)
    assert isinstance(stream, ToolStream)
    assert len(stream.steps) == 3
    assert [s.tool_name for s in stream.steps] == ["Read", "Read", "Grep"]
    assert [s.args_digest for s in stream.steps] == ["a1", "a1", "a2"]
    assert [s.result_digest for s in stream.steps] == ["r1", "r1", None]


def test_append_stamps_schema_and_ts(tmp_path: Path):
    p = _path(tmp_path)
    pts.append_step("S1", StreamStep("Read", "a1", "r1"), path=p)
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert rec["schema"] == {"family": "tool-stream", "version": 1}
    assert rec["ts"]
    assert rec["tool_name"] == "Read"


def test_append_without_firing_fields_is_byte_identical_v1(tmp_path: Path):
    """The docs/179 additive fields are ABSENT when not supplied — the record is the
    byte-for-byte v1 record (additive evolution: a new optional field that is only
    written when known does NOT change the common record or bump the schema)."""
    p = _path(tmp_path)
    pts.append_step("S1", StreamStep("Read", "a1", "r1"), path=p)
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert rec["schema"] == {"family": "tool-stream", "version": 1}  # NOT bumped
    assert "run_id" not in rec
    assert "step_index" not in rec
    assert "verdict_state" not in rec


def test_append_firing_fields_round_trip(tmp_path: Path):
    """When supplied, run_id / step_index / verdict_state are persisted on the record
    (the docs/179 Phase-0 firing fact) — and the schema version still does NOT bump
    (additive)."""
    p = _path(tmp_path)
    pts.append_step(
        "S1", StreamStep("Read", "a1", "r1"), path=p,
        run_id="RID-abc", step_index=4, verdict_state="STALLED",
    )
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert rec["schema"] == {"family": "tool-stream", "version": 1}
    assert rec["run_id"] == "RID-abc"
    assert rec["step_index"] == 4
    assert rec["verdict_state"] == "STALLED"


def test_firing_fields_do_not_break_stream_replay(tmp_path: Path):
    """A record carrying the firing fields still folds into a normal StreamStep — the
    pure `read_stream`/`classify_stream` path ignores the extra fields (forward
    compatible: an additive field is ignorable by the verdict that does not read it)."""
    p = _path(tmp_path)
    pts.append_step("S1", StreamStep("Read", "a1", "r1"), path=p)
    pts.append_step("S1", StreamStep("Read", "a1", "r1"), path=p,
                    run_id="RID-x", step_index=1, verdict_state="REPEATING")
    stream = pts.read_stream("S1", path=p)
    assert [s.result_digest for s in stream.steps] == ["r1", "r1"]


def test_read_stream_absent_file_is_empty(tmp_path: Path):
    assert pts.read_stream("S1", path=tmp_path / "nope.jsonl").steps == ()


def test_read_stream_skips_torn_trailing_line(tmp_path: Path):
    p = _path(tmp_path)
    pts.append_step("S1", StreamStep("Read", "a1", "r1"), path=p)
    with p.open("a", encoding="utf-8") as f:
        f.write('{"op": "STEP", "tool_name": "Read"')  # torn — no closing brace
    stream = pts.read_stream("S1", path=p)
    assert len(stream.steps) == 1  # the torn final line is dropped ("didn't happen")


def test_read_stream_schema_gate_refuses_a_too_new_record(tmp_path: Path):
    """A record from a FUTURE kernel (schema v99) is NOT parsed into a StreamStep —
    it is skipped, so a too-new record can never fabricate a repeat (the §6 floor,
    the intent_ledger schema-gate posture)."""
    p = _path(tmp_path)
    pts.append_step("S1", StreamStep("Read", "a1", "r1"), path=p)
    future = {**_ds.tag("tool-stream", 99), "op": "STEP", "tool_name": "Read",
              "args_digest": "a1", "result_digest": "r1"}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(future, sort_keys=True) + "\n")
    # An OLD reader (understands only v1) skips the v99 record.
    stream = pts.read_stream("S1", path=p, understands=1)
    assert len(stream.steps) == 1  # only the v1 record survived


def test_stream_path_for_rejects_traversal(tmp_path: Path):
    """A hostile session_id with separators is sanitized so it cannot escape the
    streams dir; an empty/whitespace token yields no path (no identity, no file)."""
    cfg = _cfg(tmp_path)
    p = pts.stream_path_for("../../etc/passwd", cfg)
    assert p is not None
    assert p.parent == pts.streams_dir_for(cfg)  # stays inside .dos/streams
    assert ".." not in p.name and "/" not in p.name and "\\" not in p.name
    assert pts.stream_path_for("   ", cfg) is None
    assert pts.stream_path_for("", cfg) is None


def test_stream_path_under_dot_dos(tmp_path: Path):
    cfg = _cfg(tmp_path)
    p = pts.stream_path_for("2cd77e93", cfg)
    assert p.parent.name == "streams"
    assert p.parent.parent.name == ".dos"
    assert p.name == "2cd77e93.jsonl"
    assert not p.exists()  # resolving the path never creates it


# ==========================================================================
# END-TO-END through the CLI — the in-flight twin of dos_solves_output_poll.py.
# Five identical-result PostToolUse events then one different, fed one at a time,
# byte-faithful to the captured 2cd77e93 .output poll window.
# ==========================================================================


def _run_posttool(event: dict, workspace: Path, monkeypatch) -> str:
    """Drive cmd_hook_posttool with `event` on stdin; return captured stdout."""
    from dos import cli

    monkeypatch.setenv("DISPATCH_WORKSPACE", str(workspace))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    args = cli.argparse.Namespace(
        workspace=None, driver=None, job=False,
        session_id=None, debug=False,
    )
    rc = cli.cmd_hook_posttool(args)
    return buf.getvalue(), rc


def test_torn_config_load_records_nothing_without_crashing(tmp_path: Path, monkeypatch):
    # Torn-config fail-soft (issue #206): a SIBLING loop mid-editing config.py leaves a
    # write that raises AttributeError out of `load_workspace_config`. POST is host-
    # invoked — a traceback lands in the host's PostToolUse lifecycle. It must degrade
    # to its OWN no-op (record nothing, exit 0), the same as an event with no tool_name.
    # Fails-unpatched (the error used to propagate up cmd_hook_posttool); passes-patched.
    def boom(*a, **k):
        raise AttributeError("'SubstrateConfig' object has no attribute 'vcs_backend'")
    monkeypatch.setattr("dos.config.load_workspace_config", boom)

    out, rc = _run_posttool(_poll_event("x", cwd=str(tmp_path)), tmp_path, monkeypatch)
    assert rc == 0
    assert out.strip() == ""  # nothing recorded, nothing emitted — the agent proceeds


def _poll_event(result: str, cwd: str = "/work/dos"):
    # Byte-faithful to the captured 2cd77e93 window: a Read of a background-task
    # .output file, the env-returned bytes are the result.
    #
    # `cwd` is overridable because `cmd_hook_posttool` resolves the served workspace
    # as event-cwd › DISPATCH_WORKSPACE: a test that drives the real I/O boundary
    # (the accumulator must land under its tmp workspace) MUST pass cwd=tmp_path, or
    # the resolution diverges by platform — on Linux the captured "/work/dos" is a
    # valid absolute path and WINS over the env, so the per-test stream never
    # persists and REPEATING never fires (it only "passes" on Windows because
    # "/work/dos" is not a valid root there, so the env wins by accident). The
    # default keeps the captured value for the PURE `step_from_event` caller below,
    # which never touches cwd.
    return {
        "session_id": "2cd77e93-7d7f-4af9-b3d8-fc5097293fba",
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/tasks/bdbokqf2c.output"},
        "tool_response": result,
        "cwd": cwd,
    }


def test_end_to_end_output_poll_fires_in_flight(tmp_path: Path, monkeypatch):
    """The in-flight version of dos_solves_output_poll.py: five identical 126-class
    byte results then one different.

      * reads #1-#2 → empty stdout (too young to accuse / ADVANCING),
      * reads #3-#5 → the exact CC WARN dialect (REPEATING then STALLED),
      * read  #6   → empty stdout again (the env returned DIFFERENT bytes → the
                     byte-clean break back to ADVANCING).
    """
    unchanged = "task running; no new output\n" * 4  # identical across the poll run
    new = "task complete; results written to live_results/...\n" * 11  # the break read

    outs = []
    for _ in range(5):
        out, rc = _run_posttool(
            _poll_event(unchanged, cwd=str(tmp_path)), tmp_path, monkeypatch)
        assert rc == 0
        outs.append(out.strip())

    # reads #1, #2 — silent (ADVANCING / too short to judge).
    assert outs[0] == ""
    assert outs[1] == ""
    # read #3 — REPEATING fires (the 3rd identical .output read).
    assert outs[2] != ""
    rep = json.loads(outs[2])
    assert rep["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "Read" in rep["hookSpecificOutput"]["additionalContext"]
    # reads #4, #5 — keep warning (REPEATING → STALLED).
    assert outs[3] != "" and outs[4] != ""
    assert "STALLED" in json.loads(outs[4])["hookSpecificOutput"]["additionalContext"]

    # read #6 — the env returned DIFFERENT bytes → the run resets → silent again.
    out6, rc6 = _run_posttool(_poll_event(new, cwd=str(tmp_path)), tmp_path, monkeypatch)
    assert rc6 == 0
    assert out6.strip() == ""  # the byte-clean break (the agent cannot forge this)


def test_end_to_end_matches_offline_proof_state_sequence(tmp_path: Path, monkeypatch):
    """The live CLI's per-read verdict sequence matches the offline replay
    (dos_solves_output_poll.py): ADVANCING, ADVANCING, REPEATING, REPEATING,
    STALLED, then ADVANCING after the break — proving the in-flight sensor IS the
    same signal, not a look-alike."""
    cfg = _config.default_config(tmp_path)
    unchanged = "task running; no new output\n" * 4
    new = "done\n" * 11
    sid = "live-seq"

    states = []
    for result in [unchanged] * 5 + [new]:
        step = pts.step_from_event(_poll_event(result))
        pts.append_step(sid, step, cfg)
        v = classify_stream(pts.read_stream(sid, cfg), cfg.stream_policy)
        states.append(v.state)
    assert states == [
        StreamState.ADVANCING, StreamState.ADVANCING,
        StreamState.REPEATING, StreamState.REPEATING, StreamState.STALLED,
        StreamState.ADVANCING,
    ]


# ==========================================================================
# fail-safe — every failure mode → empty stdout, exit 0, never raises.
# ==========================================================================


def _run_raw(stdin_text: str, workspace: Path, monkeypatch, **ns_over) -> tuple[str, int]:
    from dos import cli

    monkeypatch.setenv("DISPATCH_WORKSPACE", str(workspace))
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    ns = dict(workspace=None, driver=None, job=False, session_id=None, debug=False)
    ns.update(ns_over)
    args = cli.argparse.Namespace(**ns)
    rc = cli.cmd_hook_posttool(args)
    return buf.getvalue(), rc


def test_fail_safe_empty_stdin(tmp_path: Path, monkeypatch):
    out, rc = _run_raw("", tmp_path, monkeypatch)
    assert out.strip() == "" and rc == 0


def test_fail_safe_malformed_json(tmp_path: Path, monkeypatch):
    out, rc = _run_raw("{not valid json", tmp_path, monkeypatch)
    assert out.strip() == "" and rc == 0


def test_fail_safe_missing_tool_name(tmp_path: Path, monkeypatch):
    out, rc = _run_raw(json.dumps({"session_id": "S1", "tool_response": "x"}),
                       tmp_path, monkeypatch)
    assert out.strip() == "" and rc == 0


def test_fail_safe_missing_session_id(tmp_path: Path, monkeypatch):
    """No session_id → no accumulator (an unkeyed stream cannot accumulate a
    per-session repeat). Even five identical results must not fire."""
    ev = {"tool_name": "Read", "tool_input": {"file_path": "x"}, "tool_response": "same"}
    for _ in range(5):
        out, rc = _run_raw(json.dumps(ev), tmp_path, monkeypatch)
        assert out.strip() == "" and rc == 0


def test_fail_safe_missing_result(tmp_path: Path, monkeypatch):
    """A call with no result returned → result_digest None → never a repeat. Five
    such events in one session stay silent (None never matches another step)."""
    ev = {"session_id": "S1", "tool_name": "Read", "tool_input": {"file_path": "x"}}
    for _ in range(5):
        out, rc = _run_raw(json.dumps(ev), tmp_path, monkeypatch)
        assert out.strip() == "" and rc == 0
