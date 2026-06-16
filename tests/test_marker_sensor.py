"""loop_decide §wait-marker — the Stop-hook wait-marker budget sensor.

The pure verdict `loop_decide.wait_marker_budget` already ships and is green
(`tests/test_dispatch_loop_decide.py` exercises the allow/refuse arithmetic). This
exercises the BOUNDARY adapter that turns it from an offline-only decision into a
live Stop-hook lever vs the [[project-dos-poll-loop-antipattern]] cache-replay
waste: the session-scoped marker tally (`dos.marker_sensor`, append/count,
schema-gated + torn-tail-tolerant like `intent_ledger`/`posttool_sensor`), and the
end-to-end `dos hook marker` CLI command.

The docs/165 §5 self-certification trap is the enemy: these tests do NOT merely
assert "the bytes match the shape DOS chose." The CLI tests assert the EXACT
control-flow REAL Claude Code honors at a Stop hook — `{"decision": "block"}` to
hold the turn open, EMPTY stdout to allow the stop — the same anti-no-op assertion
that `test_hook_stop.py` makes, because the sibling Stop hook is a SILENT NO-OP
against real CC if it emits the wrong shape. The polarity assertion is the
load-bearing one: this hook BLOCKS while the budget remains and ALLOWS the stop
once it is spent — the inverse of `cmd_hook_stop`.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from dos import config as _config
from dos import durable_schema as _ds
from dos import marker_sensor as ms


# ==========================================================================
# The accumulator — append/count round-trip, schema gate, torn-tail tolerance.
# (Byte-mirrors test_posttool_sensor's accumulator section.)
# ==========================================================================


def test_count_absent_file_is_zero(tmp_path: Path):
    p = tmp_path / "S1.jsonl"
    assert ms.marker_count("S1", path=p) == 0


def test_record_then_count_round_trips(tmp_path: Path):
    p = tmp_path / "S1.jsonl"
    ms.record_marker("S1", path=p)
    ms.record_marker("S1", path=p)
    ms.record_marker("S1", path=p)
    assert ms.marker_count("S1", path=p) == 3


def test_record_stamps_schema_and_ts(tmp_path: Path):
    p = tmp_path / "S1.jsonl"
    ms.record_marker("S1", path=p, reason="held open", run_id="r-123")
    obj = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert obj["op"] == "MARKER"
    assert obj["schema"] == {"family": ms.SCHEMA_FAMILY, "version": ms.WAIT_MARKER_SCHEMA}
    assert "ts" in obj
    # The additive firing-join fields are present only when passed.
    assert obj["reason"] == "held open"
    assert obj["run_id"] == "r-123"


def test_record_without_optional_fields_omits_them(tmp_path: Path):
    p = tmp_path / "S1.jsonl"
    ms.record_marker("S1", path=p)
    obj = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert "reason" not in obj and "run_id" not in obj


def test_torn_tail_line_is_skipped_undercounts(tmp_path: Path):
    """A half-written trailing record is 'didn't happen' — UNDER-count, the safe
    direction for a cost guard (never refuse a marker the loop was entitled to)."""
    p = tmp_path / "S1.jsonl"
    ms.record_marker("S1", path=p)
    ms.record_marker("S1", path=p)
    with p.open("a", encoding="utf-8") as fh:
        fh.write('{"op": "MARKER", "schema": "wait-marker@1"')  # no closing brace / newline
    assert ms.marker_count("S1", path=p) == 2  # the torn line does not count


def test_schema_gate_skips_unreadable_newer(tmp_path: Path):
    """A record tagged a non-additively-NEWER version is skipped (a too-new record
    can never forge a count) — the §6 schema gate, byte-mirroring read_stream."""
    p = tmp_path / "S1.jsonl"
    ms.record_marker("S1", path=p)  # a readable v1 record
    future = {**_ds.tag(ms.SCHEMA_FAMILY, ms.WAIT_MARKER_SCHEMA + 1), "op": "MARKER"}
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(future) + "\n")
    assert ms.marker_count("S1", path=p) == 1  # only the readable record counts


def test_untagged_legacy_record_counts(tmp_path: Path):
    """An UNTAGGED (no-schema) record is read permissively as v1 (the durable_schema
    legacy floor) — back-compat with any pre-tag tally."""
    p = tmp_path / "S1.jsonl"
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"op": "MARKER"}) + "\n")
    assert ms.marker_count("S1", path=p) == 1


def test_wrong_family_record_skipped(tmp_path: Path):
    p = tmp_path / "S1.jsonl"
    ms.record_marker("S1", path=p)
    foreign = {**_ds.tag("some-other-family", 1), "op": "MARKER"}
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(foreign) + "\n")
    assert ms.marker_count("S1", path=p) == 1  # the foreign line is not this reader's


def test_unknown_op_not_counted_and_not_a_reset(tmp_path: Path):
    """A record with an op that is NEITHER MARKER NOR RESET is ignored — not counted as
    a marker, and (unlike RESET) it does not zero the running count."""
    p = tmp_path / "S1.jsonl"
    ms.record_marker("S1", path=p)
    other = {**_ds.tag(ms.SCHEMA_FAMILY, ms.WAIT_MARKER_SCHEMA), "op": "SOMETHING_ELSE"}
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(other) + "\n")
    assert ms.marker_count("S1", path=p) == 1  # the marker still counts; the unknown op is inert


# ==========================================================================
# The forward-delta RESET (docs/259 §Follow-up 2) — the count is now
# markers-AFTER-the-last-reset, the `tool_stream` ADVANCING analogue.
# ==========================================================================


def test_reset_zeroes_the_count(tmp_path: Path):
    """A RESET record zeroes the running no-op count — progress earns a fresh budget."""
    p = tmp_path / "S1.jsonl"
    ms.record_marker("S1", path=p)
    ms.record_marker("S1", path=p)
    ms.record_marker("S1", path=p)
    assert ms.marker_count("S1", path=p) == 3
    ms.record_reset("S1", path=p)
    assert ms.marker_count("S1", path=p) == 0


def test_markers_after_reset_count_from_zero(tmp_path: Path):
    """The count is markers-AFTER-the-last-reset: 3, reset, 2 → 2."""
    p = tmp_path / "S1.jsonl"
    for _ in range(3):
        ms.record_marker("S1", path=p)
    ms.record_reset("S1", path=p)
    ms.record_marker("S1", path=p)
    ms.record_marker("S1", path=p)
    assert ms.marker_count("S1", path=p) == 2


def test_multiple_resets_last_one_wins(tmp_path: Path):
    """marker, RESET, marker, marker, RESET, marker → only the 1 after the LAST reset."""
    p = tmp_path / "S1.jsonl"
    ms.record_marker("S1", path=p)
    ms.record_reset("S1", path=p)
    ms.record_marker("S1", path=p)
    ms.record_marker("S1", path=p)
    ms.record_reset("S1", path=p)
    ms.record_marker("S1", path=p)
    assert ms.marker_count("S1", path=p) == 1


def test_no_reset_in_file_is_byte_identical_to_old_count(tmp_path: Path):
    """The load-bearing back-compat: with NO RESET, the count is the old 'all MARKERs'.
    This is what keeps every shipped host's `dos hook marker` behavior unchanged."""
    p = tmp_path / "S1.jsonl"
    for _ in range(5):
        ms.record_marker("S1", path=p)
    assert ms.marker_count("S1", path=p) == 5  # unchanged from the pre-reset reader


def test_torn_reset_line_does_not_zero_so_count_stays_higher(tmp_path: Path):
    """The conservative direction: a half-written RESET 'didn't happen', so the count
    stays HIGHER → EXHAUSTED sooner → refuse one MORE no-op turn (never one fewer). A
    torn RESET can never erase a real marker count it failed to fully write."""
    p = tmp_path / "S1.jsonl"
    ms.record_marker("S1", path=p)
    ms.record_marker("S1", path=p)
    with p.open("a", encoding="utf-8") as fh:
        fh.write('{"op": "RESET", "schema": {"family": "wait-marker"')  # torn, no close
    assert ms.marker_count("S1", path=p) == 2  # the torn reset is skipped, count NOT zeroed


def test_schema_too_new_reset_does_not_zero(tmp_path: Path):
    """A RESET tagged a non-additively-NEWER version is skipped (a too-new record can
    never ERASE a count, the mirror of never forging one) — count stays higher."""
    p = tmp_path / "S1.jsonl"
    ms.record_marker("S1", path=p)
    ms.record_marker("S1", path=p)
    future = {**_ds.tag(ms.SCHEMA_FAMILY, ms.WAIT_MARKER_SCHEMA + 1), "op": "RESET"}
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(future) + "\n")
    assert ms.marker_count("S1", path=p) == 2  # the too-new reset does not zero


def test_wrong_family_reset_does_not_zero(tmp_path: Path):
    """A foreign-family RESET is not this reader's record — it does not zero the count."""
    p = tmp_path / "S1.jsonl"
    ms.record_marker("S1", path=p)
    ms.record_marker("S1", path=p)
    foreign = {**_ds.tag("some-other-family", 1), "op": "RESET"}
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(foreign) + "\n")
    assert ms.marker_count("S1", path=p) == 2


def test_reset_record_stamps_schema_and_op(tmp_path: Path):
    """record_reset writes op:"RESET", the wait-marker schema tag, a ts, and the additive
    reason/run_id only when passed (the _marker_entry posture, for RESET)."""
    p = tmp_path / "S1.jsonl"
    ms.record_reset("S1", path=p, reason="forward delta", run_id="r-9")
    obj = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert obj["op"] == "RESET"
    assert obj["schema"] == {"family": ms.SCHEMA_FAMILY, "version": ms.WAIT_MARKER_SCHEMA}
    assert "ts" in obj
    assert obj["reason"] == "forward delta"
    assert obj["run_id"] == "r-9"


def test_reset_without_optional_fields_omits_them(tmp_path: Path):
    p = tmp_path / "S1.jsonl"
    ms.record_reset("S1", path=p)
    obj = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert obj["op"] == "RESET"
    assert "reason" not in obj and "run_id" not in obj


def test_reset_unusable_session_id_raises(tmp_path: Path):
    """A pure-separator session_id has no safe filename → record_reset raises (the CLI
    catches it as fail-safe), mirroring record_marker."""
    with pytest.raises(ValueError):
        ms.record_reset("///")


def test_hostile_session_id_cannot_escape_dir(tmp_path: Path, monkeypatch):
    """A path-traversal session_id sanitizes to safe chars (or None) — it can never
    write outside the markers dir (the distrusted-host-token discipline)."""
    monkeypatch.setenv("DISPATCH_WORKSPACE", str(tmp_path))
    # A pure-separator id sanitizes to empty → no path → record_marker raises (the CLI
    # catches it as fail-safe); marker_path_for returns None.
    assert ms.marker_path_for("../../etc/passwd") is not None  # the dots/slashes are stripped, leaving "etcpasswd"
    assert ms.marker_path_for("///") is None  # pure separators → empty → None
    p = ms.marker_path_for("../../etc/passwd")
    assert p is not None and p.name == "etcpasswd.jsonl"
    assert ms.markers_dir_for() in p.parents  # stays under .dos/markers/


# ==========================================================================
# The CLI — `dos hook marker`. The polarity + anti-no-op + fail-safe assertions.
# ==========================================================================


def _run_marker(event: dict, workspace: Path, monkeypatch, *, max_markers=4,
                as_json=False, reset=False, loop=True) -> tuple[str, int]:
    """Drive cmd_hook_marker with `event` on stdin; return (stdout, rc).

    `loop` defaults True — these tests exercise the ARMED budget (the block path),
    and the budget only arms inside a keep-alive loop (docs/274: a bare Stop hook
    fires on every finished turn, so an unscoped budget would force keep-alive turns
    on ordinary turns). Pass loop=False to test the ordinary-turn allow-stop guard.
    """
    from dos import cli

    monkeypatch.setenv("DISPATCH_WORKSPACE", str(workspace))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)

    args = cli.argparse.Namespace(
        workspace=None, driver=None, job=False,
        session_id=None, max_markers=max_markers, json=as_json, debug=False,
        reset=reset, loop=loop,
    )
    rc = cli.cmd_hook_marker(args)
    return buf.getvalue(), rc


def _stop_event(session="loop-sess-1"):
    # NO `cwd` — an explicit event cwd is resolved as the workspace (the documented
    # `--workspace › event.cwd › cwd` precedence, where an explicit arg beats the env
    # var). Omitting it lets the test's DISPATCH_WORKSPACE=tmp_path win, so the tally
    # lands under tmp_path, never the real repo's .dos/. (One test below sets cwd
    # deliberately to prove the event-cwd resolution.)
    return {"session_id": session}


def test_torn_config_load_allows_stop_without_crashing(tmp_path: Path, monkeypatch):
    # Torn-config fail-soft (issue #206): a SIBLING loop mid-editing config.py leaves a
    # write that raises AttributeError out of `load_workspace_config`. The marker hook is
    # host-invoked — a traceback lands in the host's Stop lifecycle. It must degrade to
    # its OWN no-op (allow the stop, exit 0, empty stdout), the same as a missing session
    # identity. Fails-unpatched (the error used to propagate up cmd_hook_marker);
    # passes-patched.
    def boom(*a, **k):
        raise AttributeError("'SubstrateConfig' object has no attribute 'vcs_backend'")
    monkeypatch.setattr("dos.config.load_workspace_config", boom)

    out, rc = _run_marker(_stop_event(), tmp_path, monkeypatch)
    assert rc == 0
    assert out.strip() == ""  # allow the stop — no block object on stdout


def test_cli_blocks_while_budget_remains_then_allows_stop(tmp_path: Path, monkeypatch):
    """The end-to-end lever: with max_markers=4, the first 4 keep-alive turns BLOCK
    (hold the turn open), the 5th ALLOWS the stop (empty stdout) — the loop ends its
    turn and waits on the real task-notification.

    This IS the in-flight cost guard: each BLOCK is a marker the loop was entitled to;
    the empty 5th is the refusal that stops the cache-replay bleed."""
    ev = _stop_event()
    # Markers 1-4: budget remains → block, turn held open.
    for i in range(4):
        out, rc = _run_marker(ev, tmp_path, monkeypatch, max_markers=4)
        assert rc == 0
        obj = json.loads(out)
        assert obj["decision"] == "block", f"marker {i+1} should hold the turn open"
        assert "reason" in obj and "wait-marker" in obj["reason"]
    # Marker 5: budget spent → allow the stop (EMPTY stdout, CC's 'allow stop').
    out5, rc5 = _run_marker(ev, tmp_path, monkeypatch, max_markers=4)
    assert rc5 == 0
    assert out5.strip() == "", "the 5th turn must allow the stop (empty output), not block"


def test_cli_ordinary_turn_without_loop_signal_allows_stop(tmp_path: Path, monkeypatch):
    """⚠ docs/274 — the load-bearing fix. A Stop hook fires on EVERY finished turn, not
    only on a keep-alive poll. So without a loop signal (--loop / DOS_LOOP / CID_RUN_ID)
    the budget must NOT arm — an ordinary interactive turn gets an empty stdout (allow
    stop), never a block. This is the guard that stops the inversion where an unscoped
    budget forced keep-alive turns on every interactive turn (44 sessions, 35 @ 4/4)."""
    monkeypatch.delenv("DOS_LOOP", raising=False)
    monkeypatch.delenv("CID_RUN_ID", raising=False)
    # Even with a fresh budget (count 0, would otherwise block), an unarmed turn allows.
    out, rc = _run_marker(_stop_event(session="ordinary-sess"), tmp_path, monkeypatch,
                          max_markers=4, loop=False)
    assert rc == 0
    assert out.strip() == "", "an ordinary turn (no loop signal) must allow the stop"
    # And nothing was recorded — an unarmed turn is not a marker.
    cfg = _config.active()
    assert ms.marker_count("ordinary-sess", cfg) == 0


def test_cli_loop_env_arms_the_budget(tmp_path: Path, monkeypatch):
    """The DOS_LOOP env arms the budget identically to --loop (the loop-local opt-in a
    headless dispatch loop sets, docs/274 Case A) — so a loop's keep-alive polling is
    still capped, only now scoped to the loop instead of every session."""
    monkeypatch.setenv("DOS_LOOP", "1")
    out, rc = _run_marker(_stop_event(session="env-loop-sess"), tmp_path, monkeypatch,
                          max_markers=4, loop=False)  # loop=False, but env arms it
    assert rc == 0
    assert json.loads(out)["decision"] == "block", "DOS_LOOP must arm the budget"


def test_cli_stop_hook_active_never_re_blocks(tmp_path: Path, monkeypatch):
    """docs/274 Case C — honor Claude Code's own infinite-loop backstop. When the Stop
    event carries stop_hook_active:true (this stop is ALREADY being continued by a prior
    hook), the marker hook must never escalate it with another block — even inside a
    loop with budget remaining. It allows the stop (empty stdout)."""
    ev = {"session_id": "active-sess", "stop_hook_active": True}
    out, rc = _run_marker(ev, tmp_path, monkeypatch, max_markers=4, loop=True)
    assert rc == 0
    assert out.strip() == "", "an already-hook-continued stop must not be re-blocked"


def test_cli_block_dialect_is_exactly_what_cc_honors(tmp_path: Path, monkeypatch):
    """The anti-no-op assertion (docs/165 §2): a Stop block MUST be the top-level
    {"decision": "block", "reason": …} CC parses — NEVER an {"ok": …} /
    hookSpecificOutput shape (which CC silently ignores at a Stop hook)."""
    out, rc = _run_marker(_stop_event(), tmp_path, monkeypatch, max_markers=4)
    obj = json.loads(out)
    assert set(obj.keys()) == {"decision", "reason"}
    assert obj["decision"] == "block"
    assert "ok" not in obj and "hookSpecificOutput" not in obj


def test_cli_refused_marker_does_not_advance_count(tmp_path: Path, monkeypatch):
    """A refused marker (budget spent) is NOT recorded — it was not emitted. So a
    second over-budget invocation still reads the cap, not cap+1 (the count is the
    truth of markers EMITTED, and a refusal emits none)."""
    ev = _stop_event(session="refuse-sess")
    for _ in range(4):
        _run_marker(ev, tmp_path, monkeypatch, max_markers=4)
    cfg = _config.active()  # DISPATCH_WORKSPACE was set by the last _run_marker
    # Two over-budget invocations.
    _run_marker(ev, tmp_path, monkeypatch, max_markers=4)
    _run_marker(ev, tmp_path, monkeypatch, max_markers=4)
    assert ms.marker_count("refuse-sess", cfg) == 4  # never advanced past the cap


def test_cli_json_surface_reports_decision_without_cc_dialect(tmp_path: Path, monkeypatch):
    """--json emits the machine object, never the CC Stop dialect. An ALLOWED marker
    in --json mode still advances the durable count (for the next invocation)."""
    ev = _stop_event(session="json-sess")
    out, rc = _run_marker(ev, tmp_path, monkeypatch, max_markers=2, as_json=True)
    obj = json.loads(out)
    assert obj["allow_marker"] is True
    assert obj["markers_emitted"] == 1  # 0 prior + this one
    assert obj["max_markers"] == 2
    assert "decision" not in obj  # not the CC dialect
    # The count advanced durably.
    cfg = _config.active()
    assert ms.marker_count("json-sess", cfg) == 1


def test_cli_fail_safe_empty_stdin_allows_stop(tmp_path: Path, monkeypatch):
    from dos import cli

    monkeypatch.setenv("DISPATCH_WORKSPACE", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    args = cli.argparse.Namespace(
        workspace=None, driver=None, job=False,
        session_id=None, max_markers=4, json=False, debug=False, reset=False,
        loop=True,
    )
    rc = cli.cmd_hook_marker(args)
    # No session_id in an empty event → no accumulator → allow stop (empty output).
    assert rc == 0 and buf.getvalue().strip() == ""


def test_cli_fail_safe_missing_session_id_allows_stop(tmp_path: Path, monkeypatch):
    """No session_id → no per-session tally → let the agent stop (empty output)."""
    out, rc = _run_marker({"cwd": str(tmp_path)}, tmp_path, monkeypatch)
    assert rc == 0 and out.strip() == ""


def test_cli_event_cwd_resolves_the_workspace(tmp_path: Path, monkeypatch):
    """The documented `--workspace › event.cwd › cwd` precedence: with NO --workspace
    and NO DISPATCH_WORKSPACE, the event's `cwd` selects the served workspace, so the
    tally lands under THAT root's .dos/markers/. (This is why a test event must never
    carry a real-repo cwd — it would write into that repo.)"""
    from dos import cli

    monkeypatch.delenv("DISPATCH_WORKSPACE", raising=False)
    ev = {"session_id": "cwd-sess", "cwd": str(tmp_path)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(ev)))
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    args = cli.argparse.Namespace(
        workspace=None, driver=None, job=False,
        session_id=None, max_markers=4, json=False, debug=False, reset=False,
        loop=True,
    )
    rc = cli.cmd_hook_marker(args)
    assert rc == 0
    assert json.loads(buf.getvalue())["decision"] == "block"
    # The tally landed under the event-cwd workspace, not cwd.
    assert (tmp_path / ".dos" / "markers" / "cwd-sess.jsonl").exists()


def test_cli_explicit_session_id_overrides_event(tmp_path: Path, monkeypatch):
    """--session-id wins over the event's session_id (the override flag, mirroring
    cmd_hook_posttool)."""
    from dos import cli

    monkeypatch.setenv("DISPATCH_WORKSPACE", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({})))  # no session_id in the event
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    args = cli.argparse.Namespace(
        workspace=None, driver=None, job=False,
        session_id="explicit-sess", max_markers=4, json=False, debug=False, reset=False,
        loop=True,
    )
    rc = cli.cmd_hook_marker(args)
    out = buf.getvalue()
    assert rc == 0
    assert json.loads(out)["decision"] == "block"  # budget remained → blocked
    cfg = _config.active()
    assert ms.marker_count("explicit-sess", cfg) == 1


def test_cli_max_markers_zero_allows_stop_immediately(tmp_path: Path, monkeypatch):
    """A budget of 0 refuses the FIRST marker (0 >= 0) → allow stop immediately. The
    degenerate the pure wait_marker_budget preserves, surfaced through the CLI."""
    out, rc = _run_marker(_stop_event(session="zero-sess"), tmp_path, monkeypatch,
                          max_markers=0)
    assert rc == 0 and out.strip() == ""
    cfg = _config.active()
    assert ms.marker_count("zero-sess", cfg) == 0  # nothing recorded


# ==========================================================================
# The --reset CLI path (docs/259 §Follow-up 2) — a forward delta zeroes the tally,
# emits nothing, and refreshes the budget. Fail-safe like every other path.
# ==========================================================================


def test_cli_reset_writes_reset_and_emits_nothing(tmp_path: Path, monkeypatch):
    """--reset appends a RESET (zeroing the count) and emits NOTHING (a reset is not a
    Stop-block — it is progress, not the loop choosing to keep waiting)."""
    ev = _stop_event(session="reset-cli")
    # Burn 2 markers first.
    _run_marker(ev, tmp_path, monkeypatch, max_markers=4)
    _run_marker(ev, tmp_path, monkeypatch, max_markers=4)
    cfg = _config.active()
    assert ms.marker_count("reset-cli", cfg) == 2
    # Now reset.
    out, rc = _run_marker(ev, tmp_path, monkeypatch, reset=True)
    assert rc == 0 and out.strip() == "", "a reset emits nothing"
    assert ms.marker_count("reset-cli", cfg) == 0, "the reset zeroed the tally"


def test_cli_reset_refreshes_the_budget(tmp_path: Path, monkeypatch):
    """The end-to-end §Follow-up 2 proof: spend the budget, reset, and the budget is
    fresh again — a re-entered wait phase starts with its full allowance."""
    ev = _stop_event(session="refresh-cli")
    # Spend the whole budget: 4 blocks, then the 5th allows the stop.
    for _ in range(4):
        out, rc = _run_marker(ev, tmp_path, monkeypatch, max_markers=4)
        assert json.loads(out)["decision"] == "block"
    out5, _ = _run_marker(ev, tmp_path, monkeypatch, max_markers=4)
    assert out5.strip() == ""  # budget spent → allow stop
    # A forward delta resets the streak.
    _run_marker(ev, tmp_path, monkeypatch, reset=True)
    # The very next keep-alive turn blocks again — the budget is fresh.
    out6, rc6 = _run_marker(ev, tmp_path, monkeypatch, max_markers=4)
    assert rc6 == 0 and json.loads(out6)["decision"] == "block", "budget refreshed after reset"


def test_cli_reset_fail_safe_no_session_id(tmp_path: Path, monkeypatch):
    """--reset with no session_id → no accumulator → emit nothing, exit 0 (never a
    crash on the sensor's own inability to key a tally)."""
    out, rc = _run_marker({"cwd": str(tmp_path)}, tmp_path, monkeypatch, reset=True)
    assert rc == 0 and out.strip() == ""
