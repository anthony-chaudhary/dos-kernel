"""docs/197 §7(1) — the fold-site result-state witness (`dos verify-result`).

The keystone catch: an ultracode Workflow folds agent()'s self-authored return as
ground truth at the `${result}` interpolation, and ~32% of real subagents return a
HARNESS-synthesized terminal-error string there that survives `.filter(Boolean)` and
is banked as a finished finding. These tests exercise the pure verdict
(`result_state.classify_terminal`), the boundary reader
(`terminal_evidence_from_transcript`), and the end-to-end CLI (`cmd_verify_result`).

The load-bearing pins:

  * **byte-author grounding**: the gate keys on `message.model == "<synthetic>"` (the
    HARNESS authored the death, not the subagent's model) — the docs/138 invariant.
  * **broader than 429**: the whole synthetic-terminal family fires (429/401/403/500
    + the limit-text deaths that carry NO apiErrorStatus), per the 2,935-record sweep
    — a 429-only gate would miss 43% of real deaths.
  * **fail-safe floor**: an UNREADABLE transcript is NOT a death (a read fault must
    never fabricate a death that drops a real result).
  * **top-level fields**: `isApiErrorMessage`/`apiErrorStatus` are read from the
    RECORD, not from `message` (the docs/197 §2.1 location correction).

The fixtures below are the byte-exact shapes from real `~/.claude/projects`
transcripts (the synthetic 429/401/403/500/limit-text records), not invented.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from dos import result_state as rs


# ==========================================================================
# Fixtures — byte-exact real-record shapes (from the 2,935-record corpus sweep).
# ==========================================================================
def _synthetic_record(text, *, api_status=None, stop_reason="stop_sequence"):
    """A harness-synthesized terminal record. `model:"<synthetic>"`, top-level
    `isApiErrorMessage`/`apiErrorStatus` (siblings of `message`, not children)."""
    rec = {
        "parentUuid": "p-uuid",
        "isSidechain": True,
        "agentId": "a1234567",
        "type": "assistant",
        "uuid": "u-uuid",
        "timestamp": "2026-06-06T16:00:00.000Z",
        "isApiErrorMessage": True,
        "userType": "external",
        "cwd": "C:\\dev\\proj",
        "sessionId": "S1",
        "version": "2.1.167",
        "gitBranch": "master",
        "message": {
            "id": "m-id",
            "container": None,
            "model": "<synthetic>",
            "role": "assistant",
            "stop_reason": stop_reason,
            "stop_sequence": "",
            "type": "message",
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "content": [{"type": "text", "text": text}],
        },
    }
    if api_status is not None:
        rec["apiErrorStatus"] = api_status
        rec["error"] = "rate_limit" if api_status == 429 else "error"
    return rec


def _healthy_record(text="Here is my finding: the answer is 42."):
    """A real-model terminal assistant record with content."""
    return {
        "parentUuid": "p-uuid",
        "isSidechain": True,
        "agentId": "a1234567",
        "type": "assistant",
        "uuid": "u-uuid",
        "timestamp": "2026-06-06T16:00:00.000Z",
        "attributionAgent": "Explore",
        "userType": "external",
        "cwd": "C:\\dev\\proj",
        "sessionId": "S1",
        "version": "2.1.167",
        "gitBranch": "master",
        "message": {
            "model": "claude-opus-4-8",
            "id": "m-id",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 50},
        },
    }


def _user_record():
    """A user/tool_result line — must be SKIPPED by the terminal-record walk."""
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"tool_use_id": "t1", "type": "tool_result",
                         "content": "Structured output provided successfully"}],
        },
        "sessionId": "S1",
    }


def _write_transcript(tmp_path: Path, records, name="t.jsonl") -> Path:
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return p


# ==========================================================================
# classify_terminal — the pure verdict.
# ==========================================================================
def test_synthetic_429_is_dead_rate_limit():
    ev = rs.terminal_evidence_from_record(
        _synthetic_record("API Error: Server is temporarily limiting requests "
                          "(not your usage limit) · Rate limited", api_status=429))
    v = rs.classify_terminal(ev)
    assert v.state is rs.TerminalState.SYNTHETIC
    assert v.dead is True
    assert v.cls is rs.TerminalClass.RATE_LIMIT
    assert v.api_status == 429


def test_synthetic_401_auth_is_dead():
    ev = rs.terminal_evidence_from_record(
        _synthetic_record('API Error: 401 {"type":"error","error":'
                          '{"type":"authentication_error"}}', api_status=401))
    v = rs.classify_terminal(ev)
    assert v.dead is True
    assert v.cls is rs.TerminalClass.AUTH


def test_synthetic_500_server_is_dead():
    ev = rs.terminal_evidence_from_record(
        _synthetic_record("API Error: 500 Internal server error. This is a "
                          "server-side issue", api_status=500))
    v = rs.classify_terminal(ev)
    assert v.dead is True
    assert v.cls is rs.TerminalClass.SERVER


def test_synthetic_usage_limit_text_no_status_is_dead():
    """The 50/2935 limit-text deaths carry NO apiErrorStatus — the gate must still
    fire (the broader-than-429 requirement). model=='<synthetic>' is the gate."""
    ev = rs.terminal_evidence_from_record(
        _synthetic_record("You've hit your weekly limit · resets Jun 5, 10pm",
                          api_status=None))
    v = rs.classify_terminal(ev)
    assert v.state is rs.TerminalState.SYNTHETIC
    assert v.dead is True
    assert v.cls is rs.TerminalClass.USAGE_LIMIT
    assert v.api_status is None


def test_synthetic_403_org_disabled_is_usage_limit():
    ev = rs.terminal_evidence_from_record(
        _synthetic_record("Your organization has disabled Claude subscription access",
                          api_status=403))
    v = rs.classify_terminal(ev)
    assert v.dead is True
    assert v.cls is rs.TerminalClass.USAGE_LIMIT


def test_synthetic_model_unavailable_is_dead_and_named():
    """The goal's case: a child launched on a down/retired model returns a shaped
    non-result — "<model> is currently unavailable". It is DEAD (the worker never
    ran) and classified MODEL_UNAVAILABLE (not OTHER) so the heal — reroute to a
    sibling model — is routable. The real text carries no apiErrorStatus."""
    ev = rs.terminal_evidence_from_record(
        _synthetic_record("Claude Fable 5 is currently unavailable", api_status=None))
    v = rs.classify_terminal(ev)
    assert v.state is rs.TerminalState.SYNTHETIC
    assert v.dead is True
    assert v.cls is rs.TerminalClass.MODEL_UNAVAILABLE
    assert v.api_status is None


def test_model_unavailable_outranks_usage_cues():
    """A model-down phrasing must win over the broad USAGE_LIMIT cues even if a
    limit-ish word co-occurs — 'unavailable' anchored on 'model' is the more
    specific class, and routes to a different heal."""
    ev = rs.terminal_evidence_from_record(
        _synthetic_record("The requested model is unavailable", api_status=None))
    v = rs.classify_terminal(ev)
    assert v.cls is rs.TerminalClass.MODEL_UNAVAILABLE


def test_generic_service_unavailable_is_not_model_unavailable():
    """A correlated infra outage ('service unavailable' / 503) is NOT a down model
    — a SIBLING model cannot heal a whole-provider outage, so it must NOT be
    mis-classed MODEL_UNAVAILABLE. It stays OTHER (no spurious reroute)."""
    ev = rs.terminal_evidence_from_record(
        _synthetic_record("API Error: 503 Service unavailable", api_status=503))
    v = rs.classify_terminal(ev)
    assert v.dead is True
    assert v.cls is rs.TerminalClass.OTHER


def test_model_unavailable_envelope_reason_class():
    """The DEAD envelope carries a routable reason_class so the fold/loop sees a
    MODEL_UNAVAILABLE death distinctly from a rate-limit death."""
    from dos import wedge_reason
    v = rs.classify_terminal(rs.terminal_evidence_from_record(
        _synthetic_record("Claude Fable 5 is currently unavailable", api_status=None)))
    env = rs.refusal_envelope(v)
    refuse, _ = wedge_reason.envelope_is_refusal(env)
    assert refuse is True
    assert env["reason_class"] == "RESULT_DEAD_MODEL_UNAVAILABLE"


def test_healthy_terminal_is_not_dead():
    v = rs.classify_terminal(rs.terminal_evidence_from_record(_healthy_record()))
    assert v.state is rs.TerminalState.HEALTHY
    assert v.dead is False
    assert v.cls is rs.TerminalClass.NONE


def test_is_api_error_without_synthetic_model_still_fires():
    """Belt-and-braces: a record stamping isApiErrorMessage but (hypothetically) a
    real-looking model still fires SYNTHETIC — the corroborating flag is an
    alternative gate, the safe direction."""
    rec = _healthy_record("API Error: something")
    rec["isApiErrorMessage"] = True
    rec["apiErrorStatus"] = 429
    v = rs.classify_terminal(rs.terminal_evidence_from_record(rec))
    assert v.dead is True
    assert v.state is rs.TerminalState.SYNTHETIC


def test_empty_evidence_when_no_assistant_record():
    """Read OK but no assistant record at all → EMPTY (dead, no deliverable)."""
    ev = rs.TerminalEvidence(found=False, readable=True)
    v = rs.classify_terminal(ev)
    assert v.state is rs.TerminalState.EMPTY
    assert v.dead is True


def test_assistant_record_with_no_content_is_empty():
    rec = _healthy_record()
    rec["message"]["content"] = []
    v = rs.classify_terminal(rs.terminal_evidence_from_record(rec))
    assert v.state is rs.TerminalState.EMPTY
    assert v.dead is True


def test_tool_only_terminal_is_not_empty():
    """A terminal assistant record that is tool_use-only (no text) DID produce a
    result — must NOT be mis-flagged EMPTY."""
    rec = _healthy_record()
    rec["message"]["content"] = [{"type": "tool_use", "id": "t1", "name": "Bash",
                                  "input": {"command": "ls"}}]
    v = rs.classify_terminal(rs.terminal_evidence_from_record(rec))
    assert v.state is rs.TerminalState.HEALTHY
    assert v.dead is False


# ==========================================================================
# The fail-safe floor — UNREADABLE is NOT a death.
# ==========================================================================
def test_unreadable_is_not_dead():
    ev = rs.TerminalEvidence(found=False, readable=False)
    v = rs.classify_terminal(ev)
    assert v.state is rs.TerminalState.UNREADABLE
    assert v.dead is False


def test_missing_transcript_is_unreadable_not_dead(tmp_path):
    v = rs.verify_transcript(str(tmp_path / "does-not-exist.jsonl"))
    assert v.state is rs.TerminalState.UNREADABLE
    assert v.dead is False


# ==========================================================================
# terminal_evidence_from_transcript — the boundary reader (walks to the LAST
# assistant record; skips user/tool_result lines).
# ==========================================================================
def test_walks_to_last_assistant_record(tmp_path):
    """A transcript ending in a user(tool_result) line after a SYNTHETIC assistant
    record must still witness the death (the synthetic record is the terminal
    ASSISTANT record, even if a housekeeping user line follows)."""
    syn = _synthetic_record("API Error: ... · Rate limited", api_status=429)
    p = _write_transcript(tmp_path, [_healthy_record("early turn"), syn, _user_record()])
    v = rs.verify_transcript(str(p))
    assert v.state is rs.TerminalState.SYNTHETIC
    assert v.dead is True


def test_healthy_transcript_round_trip(tmp_path):
    p = _write_transcript(tmp_path, [_user_record(), _healthy_record()])
    v = rs.verify_transcript(str(p))
    assert v.state is rs.TerminalState.HEALTHY
    assert v.dead is False


def test_transcript_with_only_user_lines_is_empty(tmp_path):
    p = _write_transcript(tmp_path, [_user_record(), _user_record()])
    v = rs.verify_transcript(str(p))
    assert v.state is rs.TerminalState.EMPTY
    assert v.dead is True


def test_torn_tail_line_is_tolerated(tmp_path):
    """A garbled trailing line is skipped; the prior synthetic record still wins."""
    syn = _synthetic_record("· Rate limited", api_status=429)
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(syn) + "\n{ this is not json", encoding="utf-8")
    v = rs.verify_transcript(str(p))
    assert v.dead is True


def test_later_healthy_record_supersedes_earlier_synthetic(tmp_path):
    """If a worker recovered (a synthetic line followed by a real terminal turn),
    the LAST assistant record wins → HEALTHY. Only the TERMINAL state matters."""
    syn = _synthetic_record("· Rate limited", api_status=429)
    p = _write_transcript(tmp_path, [syn, _healthy_record("recovered, here's the answer")])
    v = rs.verify_transcript(str(p))
    assert v.state is rs.TerminalState.HEALTHY
    assert v.dead is False


# ==========================================================================
# refusal_envelope — the wedge_reason-shaped surface.
# ==========================================================================
def test_dead_envelope_is_a_refusal():
    from dos import wedge_reason
    v = rs.classify_terminal(rs.terminal_evidence_from_record(
        _synthetic_record("· Rate limited", api_status=429)))
    env = rs.refusal_envelope(v)
    refuse, reason = wedge_reason.envelope_is_refusal(env)
    assert refuse is True
    assert env["reason_class"] == "RESULT_DEAD_RATE_LIMIT"


def test_healthy_envelope_is_not_a_refusal():
    from dos import wedge_reason
    v = rs.classify_terminal(rs.terminal_evidence_from_record(_healthy_record()))
    env = rs.refusal_envelope(v)
    refuse, _ = wedge_reason.envelope_is_refusal(env)
    assert refuse is False


def test_empty_envelope_reason_class():
    v = rs.classify_terminal(rs.TerminalEvidence(found=False, readable=True))
    env = rs.refusal_envelope(v)
    assert env["reason_class"] == "RESULT_EMPTY"


# ==========================================================================
# The CLI verb — exit codes are the branch signal.
# ==========================================================================
def _run_cli(monkeypatch, *, transcript=None, stdin="", json_flag=False):
    from dos import cli
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    # Force a non-tty so the stdin path is taken when no --transcript is given.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    args = cli.argparse.Namespace(transcript=transcript, json=json_flag)
    rc = cli.cmd_verify_result(args)
    return buf.getvalue(), rc


def test_cli_dead_exits_3(tmp_path, monkeypatch):
    p = _write_transcript(tmp_path, [_synthetic_record("· Rate limited", api_status=429)])
    out, rc = _run_cli(monkeypatch, transcript=str(p))
    assert rc == 3
    assert "DEAD" in out


def test_cli_healthy_exits_0(tmp_path, monkeypatch):
    p = _write_transcript(tmp_path, [_healthy_record()])
    out, rc = _run_cli(monkeypatch, transcript=str(p))
    assert rc == 0
    assert "HEALTHY" in out


def test_cli_unreadable_exits_0_failsafe(tmp_path, monkeypatch):
    out, rc = _run_cli(monkeypatch, transcript=str(tmp_path / "nope.jsonl"))
    assert rc == 0  # UNREADABLE is NOT dead — the fail-safe floor
    assert "UNREADABLE" in out


def test_cli_no_transcript_is_contract_error(monkeypatch):
    out, rc = _run_cli(monkeypatch, transcript=None, stdin="")
    assert rc == 2


def test_cli_reads_transcript_path_from_stdin_event(tmp_path, monkeypatch):
    p = _write_transcript(tmp_path, [_synthetic_record("· Rate limited", api_status=429)])
    event = json.dumps({"hook_event_name": "SubagentStop", "transcript_path": str(p)})
    out, rc = _run_cli(monkeypatch, transcript=None, stdin=event)
    assert rc == 3


def test_cli_json_carries_envelope(tmp_path, monkeypatch):
    p = _write_transcript(tmp_path, [_synthetic_record("· Rate limited", api_status=429)])
    out, rc = _run_cli(monkeypatch, transcript=str(p), json_flag=True)
    obj = json.loads(out)
    assert obj["dead"] is True
    assert obj["state"] == "SYNTHETIC"
    assert obj["class"] == "RATE_LIMIT"
    assert obj["envelope"]["reason_class"] == "RESULT_DEAD_RATE_LIMIT"
