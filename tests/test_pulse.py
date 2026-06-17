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


# ---------------------------------------------------------------------------
# The docs/358 enforcement-over-blocking observe leg.
# ---------------------------------------------------------------------------
class _EnforceMetric:
    """A stand-in for enforce_outcomes.EnforceMetric — the fold reads `.false_denies`."""

    def __init__(self, false_denies):
        self.false_denies = false_denies


def test_pulse_surfaces_enforcement_over_blocking_over_threshold():
    """A standing false-DENY count (>= threshold) surfaces a tuning signal at WARN."""
    d = pulse.fold_pulse(
        liveness=[], decisions=[], breaker_verdict=None,
        enforce_metric=_EnforceMetric(false_denies=5), enforce_threshold=3)
    assert not d.empty
    assert d.severity == pulse.WARN
    body = "\n".join(d.lines)
    assert "ENFORCEMENT" in body
    assert "enforce-tune" in body
    assert d.enforce_false_denies == 5


def test_pulse_silent_on_a_single_overridden_deny():
    """One overridden deny is noise (a one-off exception), below the threshold → silent."""
    d = pulse.fold_pulse(
        liveness=[], decisions=[], breaker_verdict=None,
        enforce_metric=_EnforceMetric(false_denies=1), enforce_threshold=3)
    assert d.empty
    assert d.enforce_false_denies == 1


def test_pulse_enforcement_leg_never_demotes_urgent():
    """A STALLED run stays URGENT even when the enforcement leg also fires (WARN)."""
    d = pulse.fold_pulse(
        liveness=[_Liveness("STALLED", run_id="RID-9")], decisions=[],
        breaker_verdict=None, enforce_metric=_EnforceMetric(false_denies=9),
        enforce_threshold=3)
    assert d.severity == pulse.URGENT
    body = "\n".join(d.lines)
    assert "STALLED" in body and "ENFORCEMENT" in body


def test_pulse_enforcement_absent_is_silent():
    """No enforce_metric ⇒ the fold's silent default (no line, 0 count)."""
    d = pulse.fold_pulse(liveness=[], decisions=[], breaker_verdict=None)
    assert d.empty
    assert d.enforce_false_denies == 0


# ---------------------------------------------------------------------------
# The CRON freshness leg (the dead-man's switch for the watchers themselves).
# Uses real `freshness.classify` verdicts — they are duck-typed by the fold.
# ---------------------------------------------------------------------------
from dos import freshness as _freshness  # noqa: E402

_HOUR = 60 * 60 * 1000


def _fresh(job, age_ms, *, critical=True, cadence_ms=6 * _HOUR):
    return _freshness.classify(
        job_id=job, last_beat_age_ms=age_ms, cadence_ms=cadence_ms, critical=critical)


def test_pulse_cron_missing_critical_is_urgent():
    """A declared CRITICAL job gone silent (MISSING) is URGENT — the silent death."""
    d = pulse.fold_pulse(freshness=[_fresh("pulse", None, critical=True)])
    assert not d.empty
    assert d.severity == pulse.URGENT
    assert d.cron_missing == 1
    assert "CRON SILENT" in "\n".join(d.lines)
    assert "pulse" in "\n".join(d.lines)


def test_pulse_cron_missing_noncritical_is_warn():
    """A non-critical MISSING is WARN, not URGENT (informational silence)."""
    d = pulse.fold_pulse(freshness=[_fresh("best-effort", None, critical=False)])
    assert d.severity == pulse.WARN
    assert d.cron_missing == 1


def test_pulse_cron_late_is_warn():
    """LATE (missed a window, not dead) is a WARN, never demotes a present URGENT."""
    d = pulse.fold_pulse(freshness=[_fresh("supervise", 12 * _HOUR)])  # 6h cadence → LATE
    assert d.severity == pulse.WARN
    assert d.cron_late == 1
    assert "CRON LATE" in "\n".join(d.lines)


def test_pulse_cron_fresh_is_silent():
    """A FRESH cron adds nothing (the silence rule) — empty digest, no lines."""
    d = pulse.fold_pulse(freshness=[_fresh("pulse", 1 * _HOUR)])  # within 9h grace
    assert d.empty
    assert d.cron_missing == 0 and d.cron_late == 0


def test_pulse_cron_missing_coexists_with_stalled_run():
    """A hung run AND a dead cron both surface; severity stays URGENT; counts split."""
    d = pulse.fold_pulse(
        liveness=[_Liveness("STALLED", run_id="RID-7")],
        freshness=[_fresh("pulse", None), _fresh("supervise", 12 * _HOUR)])
    assert d.severity == pulse.URGENT
    assert d.stalled == 1
    assert d.cron_missing == 1 and d.cron_late == 1
    body = "\n".join(d.lines)
    assert "STALLED" in body and "CRON SILENT" in body and "CRON LATE" in body


def test_pulse_cron_absent_is_byte_identical_silent():
    """No freshness arg ⇒ the fold is unchanged (the back-compatible default)."""
    d = pulse.fold_pulse(liveness=[], decisions=[])
    assert d.empty
    assert d.cron_missing == 0 and d.cron_late == 0


def test_pulse_digest_to_dict_carries_cron_counts():
    d = pulse.fold_pulse(freshness=[_fresh("pulse", None)])
    assert d.to_dict()["cron_missing"] == 1
    assert d.to_dict()["cron_late"] == 0
