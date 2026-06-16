"""Tests for the Slack approval-envelope driver oracle (`dos.drivers.slack_approval`).

This oracle is the docs/85 §2 *move-B* reference's next sibling after `ci_status`:
a non-git artifact oracle that answers the ONE claim git can never leave — "an
accountable human approved this" (docs/93 §3, the envelope-not-content rule). Two
halves, tested independently the way the kernel family is built:

  * `classify()` is PURE — a `classify(Evidence, Policy) -> Verdict` in the
    `dos.verdict` family. These tests pin its full four-state ladder
    (APPROVED/INSUFFICIENT/NO_APPROVAL/NO_SIGNAL) and the two policy knobs on FROZEN
    evidence, no network. The load-bearing property pinned here is the conservative
    one: distinct approvers are counted as a SET (two reactions by one user count
    once), only REQUIRED approvers count when a set is declared, and an
    unwired/unreachable venue NEVER fabricates an APPROVED (NO_SIGNAL) — the
    fail-safe-not-fail-open discipline.

  * `gather()`/`_run_slack()` is the boundary I/O. These tests poison
    `subprocess.run` (and use the host-injected `_reader` seam) so the suite never
    touches the network, and prove every failure mode (no token, no `curl`, timeout,
    non-2xx, `ok:false`, malformed JSON) degrades to an honest NO_SIGNAL evidence
    object rather than raising — the `ci_status`/`git_delta` fail-safe stance.

Plus the structural pins: the verdict conforms to `dos.verdict.TypedVerdict`, the
`EvidenceSource` face maps the verdict to the witness vocabulary correctly, and the
driver obeys the one-way import arrow (the kernel never imports it).
"""
from __future__ import annotations

import json
import subprocess

import pytest

from dos.drivers import slack_approval as sa
from dos.drivers.slack_approval import (
    Approval,
    Slack,
    SlackApprovalSource,
    SlackEvidence,
    SlackPolicy,
    classify,
    gather,
)
from dos.evidence import Accountability, EvidenceStance


def _ev(*approvals: Approval, reachable: bool = True, detail: str = "",
        subject: str = "v1.2.3", channel: str = "C123") -> SlackEvidence:
    return SlackEvidence(subject=subject, channel=channel,
                         approvals=tuple(approvals), reachable=reachable, detail=detail)


# ── the pure classifier: the four-state ladder ──────────────────────────────
class TestClassifyLadder:
    def test_one_approver_is_approved(self):
        assert classify(_ev(Approval("U_alice", "1"))).verdict is Slack.APPROVED

    def test_reachable_empty_is_no_approval(self):
        v = classify(_ev())
        assert v.verdict is Slack.NO_APPROVAL

    def test_unreachable_is_no_signal_never_approved(self):
        v = classify(_ev(Approval("U_alice"), reachable=False, detail="unwired"))
        assert v.verdict is Slack.NO_SIGNAL
        assert "unwired" in v.reason

    def test_min_approvals_not_met_is_insufficient(self):
        v = classify(_ev(Approval("U_alice")), SlackPolicy(min_approvals=2))
        assert v.verdict is Slack.INSUFFICIENT

    def test_two_distinct_approvers_meet_min_two(self):
        v = classify(_ev(Approval("U_alice"), Approval("U_bob")), SlackPolicy(min_approvals=2))
        assert v.verdict is Slack.APPROVED
        assert v.approvers == ("U_alice", "U_bob")

    def test_same_approver_twice_counts_once(self):
        # the envelope is WHO, not how-many-times — two reactions by one user is one approver.
        v = classify(_ev(Approval("U_alice", "1"), Approval("U_alice", "2")),
                     SlackPolicy(min_approvals=2))
        assert v.verdict is Slack.INSUFFICIENT
        assert v.approvers == ("U_alice",)

    def test_required_set_filters_counting_approvers(self):
        # an approval by a non-required user does not count toward sign-off.
        v = classify(_ev(Approval("U_bob")), SlackPolicy(required_approvers=frozenset({"U_alice"})))
        assert v.verdict is Slack.INSUFFICIENT
        assert "none by a required approver" in v.reason

    def test_required_approver_signs_off_is_approved(self):
        v = classify(_ev(Approval("U_alice"), Approval("U_bob")),
                     SlackPolicy(required_approvers=frozenset({"U_alice"})))
        assert v.verdict is Slack.APPROVED
        assert v.approvers == ("U_alice",)

    def test_policy_rejects_zero_min(self):
        with pytest.raises(ValueError):
            SlackPolicy(min_approvals=0)


# ── the EvidenceSource face: verdict → witness vocabulary ───────────────────
class _Cfg:
    raw = {"slack_approval": {"channel": ""}}  # unwired by default → NO_SIGNAL floor


class TestEvidenceSourceFace:
    def test_class_level_accountability_is_third_party(self):
        assert SlackApprovalSource.accountability is Accountability.THIRD_PARTY
        assert SlackApprovalSource.name == "slack_approval"

    def test_empty_subject_is_no_signal(self):
        f = SlackApprovalSource().gather("", _Cfg())
        assert f.stance is EvidenceStance.NO_SIGNAL
        assert f.reachable is False

    def test_unwired_channel_is_no_signal_never_attest(self):
        # no channel resolvable ⇒ the venue is unwired ⇒ NO_SIGNAL, never a fabricated attest.
        f = SlackApprovalSource().gather("v1", _Cfg())
        assert f.stance is EvidenceStance.NO_SIGNAL

    def test_approved_via_reader_attests(self):
        raw = json.dumps({"ok": True, "messages": [{"user": "U_alice", "ts": "9", "text": "lgtm approve"}]})
        v = sa.approval_of("v1", channel="C1", approve_marker="approve", _reader=lambda s, c: raw)
        assert v.verdict is Slack.APPROVED
        assert v.approvers == ("U_alice",)

    def test_no_approval_via_reader_refutes_shape(self):
        # a reachable "nobody approved" is a positive REFUTED at the face, not NO_SIGNAL.
        raw = json.dumps({"ok": True, "messages": []})
        v = sa.approval_of("v1", channel="C1", _reader=lambda s, c: raw)
        assert v.verdict is Slack.NO_APPROVAL


# ── the boundary reader: every failure degrades to unreachable, never raises ─
class TestGatherFailSafe:
    def test_no_subject_is_unreachable(self):
        ev = gather("", channel="C1")
        assert ev.reachable is False

    def test_no_channel_is_unreachable(self):
        ev = gather("v1", channel="")
        assert ev.reachable is False

    def test_no_token_is_unreachable(self, monkeypatch):
        monkeypatch.delenv("DOS_SLACK_TOKEN", raising=False)
        ev = gather("v1", channel="C1", token="")
        assert ev.reachable is False
        assert "unwired" in ev.detail or "token" in ev.detail

    def test_missing_curl_is_unreachable(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError("curl")
        monkeypatch.setattr(subprocess, "run", boom)
        ev = gather("v1", channel="C1", token="xoxb-fake")
        assert ev.reachable is False
        assert "curl" in ev.detail

    def test_timeout_is_unreachable(self, monkeypatch):
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="curl", timeout=20)
        monkeypatch.setattr(subprocess, "run", boom)
        ev = gather("v1", channel="C1", token="xoxb-fake")
        assert ev.reachable is False
        assert "timed out" in ev.detail

    def test_ok_false_is_unreachable(self):
        ev = gather("v1", channel="C1", _reader=lambda s, c: json.dumps({"ok": False, "error": "channel_not_found"}))
        assert ev.reachable is False
        assert "channel_not_found" in ev.detail

    def test_malformed_json_is_unreachable(self):
        ev = gather("v1", channel="C1", _reader=lambda s, c: "{not json")
        assert ev.reachable is False
        assert "malformed" in ev.detail

    def test_host_reader_exception_is_unreachable(self):
        def boom(s, c):
            raise RuntimeError("client died")
        ev = gather("v1", channel="C1", _reader=boom)
        assert ev.reachable is False
        assert "host reader failed" in ev.detail

    def test_good_read_parses_approvals(self):
        raw = json.dumps({"ok": True, "messages": [
            {"user": "U_alice", "ts": "1"},
            {"user": "U_bob", "ts": "2"},
            {"ts": "3"},  # no user — dropped
        ]})
        ev = gather("v1", channel="C1", _reader=lambda s, c: raw)
        assert ev.reachable is True
        assert {a.approver for a in ev.approvals} == {"U_alice", "U_bob"}


# ── structural pins ─────────────────────────────────────────────────────────
class TestStructural:
    def test_verdict_conforms_to_typed_verdict(self):
        v = classify(_ev(Approval("U_alice")))
        assert isinstance(v.verdict.value, str)
        assert isinstance(v.reason, str)
        d = v.to_dict()
        assert d["verdict"] == "APPROVED"
        assert "evidence" in d and "approvers" in d

    def test_to_dict_is_json_shaped(self):
        v = classify(_ev(Approval("U_alice", "1")))
        assert json.loads(json.dumps(v.to_dict()))["verdict"] == "APPROVED"

    def test_kernel_does_not_import_this_driver(self):
        # The one-way arrow: a driver imports the kernel, never the reverse. Walk the
        # IMPORT STATEMENTS (AST) of the kernel source (everything under src/dos except
        # drivers/) for an import of this module — there must be none. AST, not a
        # substring grep, so a docstring/prose mention does not false-trip (mirrors
        # test_ci_status.py::test_kernel_does_not_import_this_driver).
        import ast
        import pathlib

        import dos
        root = pathlib.Path(dos.__file__).parent
        offenders = []
        for p in root.rglob("*.py"):
            if "drivers" in p.parts:
                continue
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.endswith("slack_approval") or "slack_approval" in alias.name.split("."):
                            offenders.append(f"{p.name}:{node.lineno}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if mod.endswith("slack_approval") or "slack_approval" in mod.split("."):
                        offenders.append(f"{p.name}:{node.lineno}: from {mod} import …")
                    elif mod.endswith("drivers") or mod == "dos.drivers":
                        for alias in node.names:
                            if alias.name == "slack_approval":
                                offenders.append(f"{p.name}:{node.lineno}: from {mod} import slack_approval")
        assert not offenders, "kernel imports the slack_approval driver:\n" + "\n".join(offenders)

    def test_registered_as_an_evidence_source(self):
        from dos.evidence import active_evidence_source_names, resolve_evidence_source
        assert "slack_approval" in active_evidence_source_names()
        assert resolve_evidence_source("slack_approval").accountability is Accountability.THIRD_PARTY
