"""`dos.pulse` + `dos pulse` — DOS's standing self-watch heartbeat.

The motivation (the always-on gap): every DOS verdict today is request-response —
`liveness.classify`, the HUMAN `decisions` queue, the `breaker` are pulled by an
interactive verb or a loop's Step 0. NOTHING periodically asks "what is the state
of everything right now, and is anything wrong that nobody has looked at?" A cron
job is exactly that missing standing pulse, and its first job is to be the READER
for the queue docs/121 §4.1 says nobody reads when unattended: a STALLED run nobody
noticed, a HUMAN decision sat unread for hours, a breaker open since 03:14.

Two halves, mirroring `test_session_digest`'s plan:

  * the PURE fold (`pulse.fold_pulse`) — folds already-gathered facts (per-run
    liveness verdicts + the ranked HUMAN decision rows + a breaker verdict) into one
    typed `PulseDigest`, and obeys the SILENCE RULE: an all-clear workspace folds to
    an EMPTY digest (nothing to push), exactly as `session_digest.build_digest`
    returns None. This is the unit-test surface — facts in, a digest out, no I/O.
  * the litmus PROPERTIES — the floors this verb must keep: it is advisory (the fold
    is pure; the verb takes no lease and mutates nothing — docs/99), and its delivery
    is fail-soft (a raising notifier becomes a non-delivered result via the existing
    `notify.send_safely`, never a crash that takes the cron run down with it).

The fold reads its inputs DUCK-TYPED (only the fields it needs), so these tests pass
light stand-ins rather than constructing a full `LivenessVerdict` / `Decision` —
the `test_session_digest._Summary`/`_Breaker` idiom. The verb's boundary I/O (gather
live leases → per-run liveness evidence → classify; `collect_decisions`; the
breaker) is covered by the verb tests against a throwaway repo, the
`test_session_digest` verb idiom.
"""

from __future__ import annotations

from dos import pulse


# ---------------------------------------------------------------------------
# Light stand-ins — only the fields fold_pulse reads (the _Summary/_Breaker idiom).
# ---------------------------------------------------------------------------
class _Liveness:
    """A stand-in for liveness.LivenessVerdict: a `.verdict` with a `.value` + run id."""

    def __init__(self, verdict, run_id="RID-x", lane="", reason=""):
        class _V:
            value = verdict

        self.verdict = _V()
        self.run_id = run_id
        self.lane = lane
        self.reason = reason


class _Decision:
    """A stand-in for decisions.Decision with the fields the pulse fold reads."""

    def __init__(self, kind="ENFORCE_BREAKER", lane="src", reason_token="SELF_MODIFY",
                 reason_text="", age_seconds=None, dup_count=1):
        class _K:
            value = kind

        self.kind = _K()
        self.lane = lane
        self.reason_token = reason_token
        self.reason_text = reason_text
        self.age_seconds = age_seconds
        self.dup_count = dup_count


class _Breaker:
    def __init__(self, is_open, escalation=""):
        self.is_open = is_open

        class _E:
            value = escalation

        self.escalation = _E()


# ---------------------------------------------------------------------------
# The PURE fold — the silence rule + the three things the pulse watches.
# ---------------------------------------------------------------------------
def test_pulse_silent_when_clear():
    """Nothing STALLED, no HUMAN decisions, a CLOSED breaker → an EMPTY digest.

    The silence rule (copied from session_digest): an all-clear workspace has
    nothing worth pushing, so the cron pulse emits nothing and the signal keeps its
    weight. `.empty` is True and there are no lines to render.
    """
    d = pulse.fold_pulse(liveness=[], decisions=[], breaker_verdict=None)
    assert d.empty
    assert not d.lines
    # An explicitly-clean set is just as silent as the bare call.
    d2 = pulse.fold_pulse(
        liveness=[_Liveness("ADVANCING")], decisions=[], breaker_verdict=_Breaker(False))
    assert d2.empty


def test_pulse_surfaces_stalled_run():
    """A STALLED run is named in the digest — the run nobody was watching."""
    d = pulse.fold_pulse(
        liveness=[_Liveness("ADVANCING", run_id="RID-ok"),
                  _Liveness("STALLED", run_id="RID-3f2", lane="src")],
        decisions=[], breaker_verdict=None)
    assert not d.empty
    body = "\n".join(d.lines)
    assert "STALLED" in body
    assert "RID-3f2" in body
    # An ADVANCING run is not noise the pulse pushes.
    assert "RID-ok" not in body


def test_pulse_surfaces_unread_human_queue():
    """A queued HUMAN decision is surfaced — the absent operator's proxy (§4.1)."""
    d = pulse.fold_pulse(
        liveness=[],
        decisions=[_Decision(reason_token="SELF_MODIFY", dup_count=2)],
        breaker_verdict=None)
    assert not d.empty
    body = "\n".join(d.lines)
    assert "SELF_MODIFY" in body
    # The count of pending HUMAN decisions rides the digest so a glance suffices.
    assert "1" in body or "decision" in body.lower()


def test_pulse_open_breaker_speaks_closed_is_silent():
    """An OPEN breaker is worth a pulse line; a CLOSED one stays silent."""
    d = pulse.fold_pulse(liveness=[], decisions=[], breaker_verdict=_Breaker(True, "HUMAN"))
    assert not d.empty
    assert "breaker" in "\n".join(d.lines).lower()
    # CLOSED on its own never breaks the silence rule.
    assert pulse.fold_pulse(
        liveness=[], decisions=[], breaker_verdict=_Breaker(False)).empty


def test_pulse_severity_rises_with_the_worst_signal():
    """STALLED → URGENT; a pending HUMAN queue / OPEN breaker → WARN; clear → INFO.

    Mirrors notification_for_decisions / notification_for_top: the digest's severity
    is the worst row, so a transport routes a hung run louder than a pending queue.
    """
    stalled = pulse.fold_pulse(
        liveness=[_Liveness("STALLED", run_id="RID-x", lane="src")],
        decisions=[], breaker_verdict=None)
    pending = pulse.fold_pulse(
        liveness=[], decisions=[_Decision()], breaker_verdict=None)
    clear = pulse.fold_pulse(liveness=[], decisions=[], breaker_verdict=None)
    assert stalled.severity == "URGENT"
    assert pending.severity == "WARN"
    # A clear digest is INFO (or empty) — never louder than the state warrants.
    assert clear.severity in ("INFO", "")


def test_pulse_digest_to_dict_roundtrips():
    """`PulseDigest.to_dict()` is the `--json` surface — stable keys, no objects."""
    d = pulse.fold_pulse(
        liveness=[_Liveness("STALLED", run_id="RID-3f2", lane="src")],
        decisions=[_Decision()], breaker_verdict=_Breaker(True, "HUMAN"))
    out = d.to_dict()
    assert isinstance(out, dict)
    assert out["empty"] is False
    assert out["severity"] in ("INFO", "WARN", "URGENT")
    assert isinstance(out["lines"], list)
    # Every value is JSON-native (the gate_classify/Notification to_dict posture).
    import json
    json.dumps(out)


# ---------------------------------------------------------------------------
# The litmus PROPERTIES — the floors the verb keeps.
# ---------------------------------------------------------------------------
def test_pulse_fold_is_pure_and_advisory():
    """fold_pulse mutates none of its inputs — it REPORTS, it never acts (docs/99).

    The advisory floor made concrete: the pulse reads projections and folds them; it
    holds no lease and changes no state. Folding the same facts twice yields equal
    digests (no hidden accumulation).
    """
    liveness = [_Liveness("STALLED", run_id="RID-3f2", lane="src")]
    decisions = [_Decision()]
    a = pulse.fold_pulse(liveness=liveness, decisions=decisions, breaker_verdict=None)
    b = pulse.fold_pulse(liveness=liveness, decisions=decisions, breaker_verdict=None)
    assert a.to_dict() == b.to_dict()
    # the inputs are untouched
    assert len(liveness) == 1 and len(decisions) == 1


def test_pulse_send_failure_does_not_crash():
    """A raising notifier degrades to a non-delivered result, never a crash.

    Reuses the existing notify.send_safely fail-soft contract: a cron pulse must
    survive a mis-wired transport (the fleet loop never dies on its own telemetry).
    """
    from dos import notify

    class _Boom:
        name = "boom"

        def send(self, note):
            raise RuntimeError("transport down")

    note = notify.Notification(
        severity=notify.Severity.URGENT, title="t", summary="s")
    result = notify.send_safely(_Boom(), note)
    assert result.delivered is False
    assert "error" in result.detail.lower() or result.detail
