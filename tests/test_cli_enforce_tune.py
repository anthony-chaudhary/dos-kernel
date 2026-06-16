"""CLI tests for `dos enforce-tune` + `dos enforce-outcomes` (docs/365).

`enforce-tune` rides `improve.classify` (exit 0=KEEP / 3=REVERT / 4=ESCALATE) with
the net_task_delta metric folded in. `enforce-outcomes` is a read-only projection of
the live OP_ENFORCE journal. The tests drive the verbs through `cli.main` with a
real on-disk corpus + a synthetic journal, asserting exit codes and the runtime rail.
"""

from __future__ import annotations

import json

from dos import cli, lane_journal


def _write_corpus(tmp_path):
    """A two-case corpus: one true-relevant mint (recovers if blocked) + one false-flag.

    A BLOCK-on-high policy scores positively (prevents the relevant corruption) → the
    default-policy work is > 0, so against baseline 0 the keep-gate KEEPs.
    """
    p = tmp_path / "cases.jsonl"
    lines = [
        {"confidence": "HIGH", "unsupported": ["incident_id"],
         "truly_minted": True, "mattered_to_score": True,
         "recovered_if_blocked": True, "recovered_if_deferred": True,
         "label": "relevant-mint"},
        {"confidence": "HIGH", "unsupported": ["legit_id"],
         "truly_minted": False, "mattered_to_score": False,
         "recovered_if_blocked": True, "recovered_if_deferred": True,
         "label": "false-flag"},
    ]
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return p


def test_enforce_tune_keeps_an_improving_candidate(tmp_path, capsys):
    corpus = _write_corpus(tmp_path)
    rc = cli.main([
        "enforce-tune", "--cases", str(corpus),
        "--suite-passed", "--truth-clean", "--baseline-work", "0",
        "--json", "--workspace", str(tmp_path),
    ])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["verdict"] == "KEEP"
    assert out["measured_work"] > 0


def test_enforce_tune_reverts_a_noop_against_its_own_baseline(tmp_path, capsys):
    corpus = _write_corpus(tmp_path)
    cli.main([
        "enforce-tune", "--cases", str(corpus), "--suite-passed", "--truth-clean",
        "--baseline-work", "0", "--json", "--workspace", str(tmp_path),
    ])
    work = json.loads(capsys.readouterr().out)["measured_work"]
    rc = cli.main([
        "enforce-tune", "--cases", str(corpus), "--suite-passed", "--truth-clean",
        "--baseline-work", str(work), "--json", "--workspace", str(tmp_path),
    ])
    out = json.loads(capsys.readouterr().out)
    assert rc == 3
    assert out["verdict"] == "REVERT"
    assert out["revert_cause"] == "no-improvement"


def test_enforce_tune_runtime_rail_reverts_despite_metric(tmp_path, capsys):
    corpus = _write_corpus(tmp_path)
    rc = cli.main([
        "enforce-tune", "--cases", str(corpus), "--suite-passed", "--truth-clean",
        "--baseline-work", "0",
        "--changed-files", "src/dos/arbiter.py", "dos.toml",
        "--json", "--workspace", str(tmp_path),
    ])
    out = json.loads(capsys.readouterr().out)
    assert rc == 3  # REVERT
    assert out["verdict"] == "REVERT"
    assert out["revert_cause"] == "regressed"
    assert "src/dos/arbiter.py" in out["runtime_logic_hits"]


def test_enforce_tune_red_suite_reverts(tmp_path, capsys):
    corpus = _write_corpus(tmp_path)
    # No --suite-passed → red → REGRESSED revert regardless of the metric.
    rc = cli.main([
        "enforce-tune", "--cases", str(corpus), "--truth-clean", "--baseline-work", "0",
        "--json", "--workspace", str(tmp_path),
    ])
    out = json.loads(capsys.readouterr().out)
    assert rc == 3
    assert out["revert_cause"] == "regressed"


def test_enforce_tune_missing_corpus_is_a_contract_error(tmp_path, capsys):
    rc = cli.main([
        "enforce-tune", "--cases", str(tmp_path / "nope.jsonl"),
        "--suite-passed", "--truth-clean", "--workspace", str(tmp_path),
    ])
    capsys.readouterr()
    assert rc not in (0, 3, 4)  # a contract error, distinct from a verdict


# ---------------------------------------------------------------------------
# enforce-outcomes — the read-only projection over a synthetic journal.
# ---------------------------------------------------------------------------
def _journal_with_a_false_deny(tmp_path):
    """Write a lane journal with a deny→override (false-DENY, by a DIFFERENT holder)
    + a standalone deny (held catch)."""
    jpath = tmp_path / "lane_journal.jsonl"
    rows = [
        {"op": lane_journal.OP_ENFORCE, "seq": 1, "holder": "agentA",
         "intervention": "BLOCK", "reason_class": "SELF_MODIFY",
         "reason": "would edit own running code (src/dos/arbiter.py) — refusing.",
         "proposal": {"decision": "deny", "reason_class": "SELF_MODIFY"}},
        # The override echoes the refused verdict's reason (the real producer shape),
        # so it resolves to the SAME target as the deny — and it is by a DIFFERENT
        # holder (the operator), the realistic false-DENY shape.
        {"op": lane_journal.OP_ENFORCE, "seq": 2, "holder": "operator-S1",
         "intervention": "OBSERVE", "reason_class": "SELF_MODIFY",
         "reason": "would edit own running code (src/dos/arbiter.py) — refusing. [override armed]",
         "proposal": {"decision": "override-admit", "reason_class": "SELF_MODIFY"}},
        {"op": lane_journal.OP_ENFORCE, "seq": 3, "holder": "agentB",
         "intervention": "BLOCK", "reason_class": "SELF_MODIFY",
         "reason": "would edit own running code (src/dos/config.py) — refusing.",
         "proposal": {"decision": "deny", "reason_class": "SELF_MODIFY"}},
    ]
    jpath.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return jpath


def test_enforce_outcomes_projects_the_live_journal(tmp_path, capsys, monkeypatch):
    jpath = _journal_with_a_false_deny(tmp_path)
    monkeypatch.setenv("DISPATCH_LANE_JOURNAL_PATH", str(jpath))
    rc = cli.main(["enforce-outcomes", "--json", "--workspace", str(tmp_path)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["metric"]["false_denies"] == 1
    assert out["metric"]["held_catches"] == 1
    assert out["metric"]["n_pairs"] == 2


def test_enforce_outcomes_empty_journal(tmp_path, capsys, monkeypatch):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("DISPATCH_LANE_JOURNAL_PATH", str(empty))
    rc = cli.main(["enforce-outcomes", "--workspace", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no enforcement outcomes" in out
