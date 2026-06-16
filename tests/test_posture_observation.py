"""docs/370 Phase C — posture-aware observation: escalated-to-human vs hard-blocked.

The kernel computes ONE pretool verdict (a deny); the operator's POSTURE chooses
how it is surfaced — a hard block, an `ask` at the human's prompt, or an advisory
note. Phase A recorded only the kernel verdict (`outcome=deny`), so the surface
action was rendering-only and invisible to the operator. This suite pins the
Phase-C observation seam:

  * `observation_entry` carries the `posture` + `surface` fields ONLY when set, so
    a default-`block` record stays byte-identical to a pre-posture one (the
    additive contract + the docs/370 parity floor).
  * `posture_split` folds the refused pretool records into escalated / blocked /
    observed, with `refused == blocked + escalated + observed` (a total partition)
    and a back-compat rule: a refused record with no `surface` counts as blocked.
  * The end-to-end PRE hook records the surface the posture produced (a deny is
    `surface=ask` under gate, `surface=warn` under observe, and writes NEITHER
    field under the block default).
  * `dos doctor` (text + `--json`) surfaces the effective posture, where it came
    from, and the escalated-vs-blocked split.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from dos import config as _config
from dos import hook_observation as hobs


# ---------------------------------------------------------------------------
# observation_entry — the posture/surface fields are additive (parity floor).
# ---------------------------------------------------------------------------
def test_entry_omits_posture_and_surface_when_unset():
    """A default record names neither field — byte-identical to a pre-Phase-C one."""
    e = hobs.observation_entry("pretool", "deny")
    assert "posture" not in e
    assert "surface" not in e


def test_entry_carries_posture_and_surface_when_set():
    e = hobs.observation_entry("pretool", "deny", posture="gate", surface="ask")
    assert e["posture"] == "gate"
    assert e["surface"] == "ask"


# ---------------------------------------------------------------------------
# posture_split — the pure fold (escalated / blocked / observed).
# ---------------------------------------------------------------------------
def _rec(outcome="deny", surface=None, verb="pretool", ts="2026-06-16T00:00:00Z"):
    r = {"verb": verb, "outcome": outcome, "ts": ts}
    if surface is not None:
        r["surface"] = surface
    return r


def test_split_buckets_by_surface():
    recs = [
        _rec(surface="ask"), _rec(surface="ask"),     # escalated
        _rec(surface="deny"),                          # blocked
        _rec(surface="warn"),                          # observed
        _rec(surface=None),                            # blocked (no surface → back-compat)
        _rec(outcome="passthrough"),                   # not refused → ignored
        _rec(outcome="block", surface="ask", verb="stop"),  # not pretool → ignored
    ]
    s = hobs.posture_split(recs)
    assert (s.refused, s.escalated, s.blocked, s.observed) == (5, 2, 2, 1)
    assert s.escalated_pct == 40.0


def test_split_partition_invariant_is_total():
    """Every refused record lands in exactly one bucket — no double-count, no drop."""
    recs = [_rec(surface=x) for x in ("ask", "deny", "warn", None, "ask", "deny")]
    s = hobs.posture_split(recs)
    assert s.refused == s.blocked + s.escalated + s.observed


def test_split_missing_surface_counts_as_blocked():
    """A posture-blind writer (the Go binary) / a pre-Phase-C record has no surface
    field; it counts as a hard block — the BLOCK-default, behavior-preserving read."""
    s = hobs.posture_split([_rec(surface=None), _rec(outcome="block")])
    assert (s.refused, s.blocked, s.escalated, s.observed) == (2, 2, 0, 0)


def test_split_honors_the_since_window():
    recs = [
        _rec(surface="ask", ts="2026-06-15T00:00:00Z"),   # before window
        _rec(surface="ask", ts="2026-06-16T12:00:00Z"),   # in window
    ]
    s = hobs.posture_split(recs, since="2026-06-16T00:00:00Z")
    assert (s.refused, s.escalated) == (1, 1)


def test_split_empty_is_all_zero():
    s = hobs.posture_split([])
    assert s.to_dict() == {"refused": 0, "blocked": 0, "escalated": 0,
                           "observed": 0, "escalated_pct": 0.0}


# ---------------------------------------------------------------------------
# End-to-end: the PRE hook records the SURFACE the posture produced.
# ---------------------------------------------------------------------------
def _patch_deny(monkeypatch):
    """Make decide() return a real-shaped SELF_MODIFY deny, so the hook re-shapes it
    under the posture and records the resulting surface (no kernel-repo needed)."""
    cc = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "blocked by DOS (SELF_MODIFY)"}}
    outcome = {"decision": "deny", "rung": "B",
               "reason_class": "SELF_MODIFY", "tree_known": True}
    monkeypatch.setattr("dos.pretool_sensor.decide",
                        lambda event, cfg, handler_name="observe": (cc, outcome))


def _run_pretool(monkeypatch, tmp_path, *, posture=None):
    from dos import cli
    monkeypatch.setenv("DISPATCH_WORKSPACE", str(tmp_path))
    if posture is None:
        monkeypatch.delenv("DOS_HOOK_POSTURE", raising=False)
    else:
        monkeypatch.setenv("DOS_HOOK_POSTURE", posture)
    event = {"tool_name": "Write", "session_id": "S1",
             "tool_input": {"file_path": "src/dos/arbiter.py", "content": "x"},
             "cwd": str(tmp_path)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    args = cli.argparse.Namespace(
        workspace=None, driver=None, job=False, handler="observe", debug=False)
    rc = cli.cmd_hook_pretool(args)
    return buf.getvalue(), rc


def _last_pretool_record(tmp_path):
    recs = hobs.read_observations(cfg=_config.default_config(tmp_path))
    pre = [r for r in recs if r.get("verb") == "pretool"]
    assert pre, "no pretool observation recorded"
    return pre[-1]


def test_block_default_records_no_posture_or_surface(monkeypatch, tmp_path):
    """The parity floor at the OBSERVATION layer: a deny under the block default
    surfaces as a deny and records NEITHER new field — byte-identical to today."""
    _patch_deny(monkeypatch)
    out, rc = _run_pretool(monkeypatch, tmp_path, posture=None)
    assert rc == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    rec = _last_pretool_record(tmp_path)
    assert rec["outcome"] == "deny"
    assert "posture" not in rec and "surface" not in rec


def test_gate_records_surface_ask(monkeypatch, tmp_path):
    _patch_deny(monkeypatch)
    out, rc = _run_pretool(monkeypatch, tmp_path, posture="gate")
    assert rc == 0
    # The operator SAW an ask (escalated), but the kernel verdict on the journal
    # is still the deny — the denominator the refused-rate reads stays honest.
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "ask"
    rec = _last_pretool_record(tmp_path)
    assert rec["outcome"] == "deny"
    assert rec["posture"] == "gate"
    assert rec["surface"] == "ask"


def test_observe_records_surface_warn(monkeypatch, tmp_path):
    _patch_deny(monkeypatch)
    out, rc = _run_pretool(monkeypatch, tmp_path, posture="observe")
    assert rc == 0
    hso = json.loads(out)["hookSpecificOutput"]
    assert "permissionDecision" not in hso  # observe never blocks, never prompts
    rec = _last_pretool_record(tmp_path)
    assert rec["outcome"] == "deny"
    assert rec["posture"] == "observe"
    assert rec["surface"] == "warn"


def test_split_reads_the_recorded_surfaces_end_to_end(monkeypatch, tmp_path):
    """A gate deny then an observe deny → the fold over the SAME log reports one
    escalated + one observed (the operator's escalated-vs-blocked view)."""
    _patch_deny(monkeypatch)
    _run_pretool(monkeypatch, tmp_path, posture="gate")
    _run_pretool(monkeypatch, tmp_path, posture="observe")
    s = hobs.posture_split(hobs.read_observations(cfg=_config.default_config(tmp_path)))
    assert (s.refused, s.escalated, s.observed, s.blocked) == (2, 1, 1, 0)


# ---------------------------------------------------------------------------
# dos doctor — surfaces the effective posture + the split (text + JSON).
# ---------------------------------------------------------------------------
def _doctor_args(tmp_path, *, as_json=False):
    from dos import cli
    return cli.argparse.Namespace(
        workspace=str(tmp_path), json=as_json, check=False, wiring=False,
        skill_strict=False, driver=None)


def _seed_observations(tmp_path, rows):
    cfg = _config.default_config(tmp_path)
    for r in rows:
        hobs.append(hobs.observation_entry("pretool", r["outcome"],
                                           posture=r.get("posture", ""),
                                           surface=r.get("surface", "")),
                    cfg=cfg, debug=True)


def test_doctor_json_reports_posture_and_split(monkeypatch, tmp_path, capsys):
    from dos import cli
    (tmp_path / "dos.toml").write_text('[enforcement]\nposture = "gate"\n', encoding="utf-8")
    monkeypatch.delenv("DOS_HOOK_POSTURE", raising=False)
    _seed_observations(tmp_path, [
        {"outcome": "deny", "posture": "gate", "surface": "ask"},
        {"outcome": "deny", "posture": "gate", "surface": "ask"},
        {"outcome": "deny"},  # a block-default record → hard-blocked
    ])
    cli.cmd_doctor(_doctor_args(tmp_path, as_json=True))
    report = json.loads(capsys.readouterr().out)
    enf = report["enforcement"]
    assert enf["posture"] == "gate"
    assert enf["source"] == "config"
    assert enf["surfaced_refusals"]["refused"] == 3
    assert enf["surfaced_refusals"]["escalated"] == 2
    assert enf["surfaced_refusals"]["blocked"] == 1


def test_doctor_text_names_the_posture_and_escalations(monkeypatch, tmp_path, capsys):
    from dos import cli
    monkeypatch.setenv("DOS_HOOK_POSTURE", "gate")
    _seed_observations(tmp_path, [{"outcome": "deny", "posture": "gate", "surface": "ask"}])
    cli.cmd_doctor(_doctor_args(tmp_path, as_json=False))
    out = capsys.readouterr().out
    assert "enforcement posture gate" in out
    assert "from env" in out                 # the precedence source is named
    assert "escalated-to-human" in out       # the split line renders


def test_doctor_default_posture_is_block_from_default(monkeypatch, tmp_path, capsys):
    from dos import cli
    monkeypatch.delenv("DOS_HOOK_POSTURE", raising=False)
    cli.cmd_doctor(_doctor_args(tmp_path, as_json=True))
    enf = json.loads(capsys.readouterr().out)["enforcement"]
    assert enf["posture"] == "block"
    assert enf["source"] == "default"
