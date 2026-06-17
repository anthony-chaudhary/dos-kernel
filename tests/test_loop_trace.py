"""`dos observe --loops` — the RSI-loop trajectory projection (docs/383).

Pins the read-only-projection contract restated for the loop axis: the fold
(`trajectories_from_events`) is PURE over events + an injected `now` (no disk, no
clock read inside — the `dispatch_top`/`observe` test posture), groups the
improve-syscall stream by `run_id`, and computes the metric curve, the breaker
distance-to-escalate, and the liveness band. The renderers are pure over the data.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from dos import loop_trace as lt
from dos import verdict_journal as vj
from dos.verdict_journal import VerdictEvent, record


NOW = dt.datetime(2026, 6, 16, 12, 30, 0, tzinfo=dt.timezone.utc)


def _ev(
    verdict: str,
    *,
    run_id: str = "RID-aaa",
    work: int | None = None,
    baseline: int | None = None,
    cout: int = 0,
    ts: str = "2026-06-16T12:00:00Z",
    seq: int = 1,
    cause: str = "",
    escalation: str = "",
    suite: bool = True,
    truth: bool = True,
    tokens: int = 100,
    lane: str = "",
    subject: str = "",
    narrated: str = "tried X",
) -> VerdictEvent:
    """Build one `improve` VerdictEvent with the flattened detail the CLI records."""
    detail: dict = {
        "next_consecutive_reverts": cout,
        "escalation": escalation,
        "evidence.suite_passed": suite,
        "evidence.truth_clean": truth,
        "evidence.tokens": tokens,
        "evidence.narrated": narrated,
    }
    if work is not None and baseline is not None:
        detail["evidence.work"] = work
        detail["evidence.baseline_work"] = baseline
        detail["evidence.delta"] = work - baseline
    if cause:
        detail["revert_cause"] = cause
    return VerdictEvent(
        syscall="improve", verdict=verdict, run_id=run_id, lane=lane,
        ts=ts, seq=seq, subject=subject, detail=detail,
    )


def _fold(events, *, now=NOW, max_reverts=3, stall_after_ms=lt.STALL_AFTER_MS):
    return lt.trajectories_from_events(
        events, now=now, max_reverts=max_reverts, stall_after_ms=stall_after_ms
    )


# ---------------------------------------------------------------------------
# The empty / floor case.
# ---------------------------------------------------------------------------


def test_empty_stream_folds_to_nothing():
    assert _fold([]) == []


def test_render_empty_is_the_stable_floor():
    out = lt.render_loops_text([])
    assert "self-improving loops" in out
    assert "(no self-improving loops recorded" in out


def test_non_improve_events_are_ignored():
    """Only syscall == improve folds; a liveness/verify event never makes a loop."""
    evs = [
        VerdictEvent(syscall="liveness", verdict="ADVANCING", run_id="RID-x"),
        VerdictEvent(syscall="verify", verdict="SHIPPED", run_id="RID-x"),
    ]
    assert _fold(evs) == []


# ---------------------------------------------------------------------------
# A climbing KEEP loop — the metric curve + an ACTIVE band.
# ---------------------------------------------------------------------------


def test_climbing_keep_loop_curve_and_active():
    evs = [
        _ev("KEEP", work=43, baseline=40, ts="2026-06-16T12:00:00Z", seq=1, lane="src"),
        _ev("REVERT", work=43, baseline=43, cout=1, cause="no-improvement",
            ts="2026-06-16T12:10:00Z", seq=2, lane="src"),
        _ev("KEEP", work=46, baseline=43, ts="2026-06-16T12:25:00Z", seq=3, lane="src"),
    ]
    [t] = _fold(evs)
    assert t.run_id == "RID-aaa"
    assert t.n == 3
    assert (t.keeps, t.reverts, t.escalates) == (2, 1, 0)
    assert t.no_improvements == 1
    # the metric curve: started at 40, latest 46, high-water 46, net +6
    assert t.start_baseline == 40
    assert t.latest_work == 46
    assert t.high_water == 46
    assert t.net_gain == 6
    # breaker: the last verdict was a KEEP, so the carried count is 0 — full distance
    assert t.current_reverts == 0
    assert t.distance_to_escalate == 3
    assert not t.escalated
    # liveness: the last iteration is 5 min before NOW → ACTIVE
    assert t.state == lt.STATE_ACTIVE
    assert t.lane == "src"


def test_render_summary_has_headline_fields():
    evs = [
        _ev("KEEP", work=43, baseline=40, ts="2026-06-16T12:25:00Z", seq=1, lane="src"),
    ]
    out = lt.render_loops_text(_fold(evs))
    assert "RID-aaa" in out
    assert "ACTIVE" in out
    assert "iter 1" in out
    assert "40→43" in out
    assert "breaker 0/3" in out


# ---------------------------------------------------------------------------
# A regressing loop that trips the breaker — ESCALATED + distance 0.
# ---------------------------------------------------------------------------


def test_escalated_loop_is_terminal_and_distance_zero():
    evs = [
        _ev("REVERT", run_id="RID-bbb", work=42, baseline=42, cout=1, cause="regressed",
            suite=False, ts="2026-06-16T10:00:00Z", seq=1, lane="global"),
        _ev("REVERT", run_id="RID-bbb", work=42, baseline=42, cout=2, cause="regressed",
            suite=False, ts="2026-06-16T10:05:00Z", seq=2, lane="global"),
        _ev("ESCALATE", run_id="RID-bbb", work=42, baseline=42, cout=3, cause="regressed",
            escalation="human", suite=False, ts="2026-06-16T10:10:00Z", seq=3, lane="global"),
    ]
    [t] = _fold(evs)
    assert t.escalated
    assert t.state == lt.STATE_ESCALATED
    assert t.distance_to_escalate == 0
    assert t.escalates == 1
    assert t.regressions == 3  # the cause rides on all three non-keeps
    out = lt.render_trajectory_text(t)
    assert "ESCALATED" in out
    assert "→ human" in out
    assert "suite✗" in out


# ---------------------------------------------------------------------------
# Liveness band — a loop with no recent iteration reads STALLED.
# ---------------------------------------------------------------------------


def test_old_last_iteration_reads_stalled():
    evs = [_ev("KEEP", work=43, baseline=40, ts="2026-06-16T08:00:00Z", seq=1)]
    [t] = _fold(evs)  # last iter 4.5h before NOW, default 30-min stall window
    assert t.state == lt.STATE_STALLED


def test_recent_iteration_within_window_reads_active():
    evs = [_ev("KEEP", work=43, baseline=40, ts="2026-06-16T12:20:00Z", seq=1)]
    [t] = _fold(evs)  # 10 min before NOW < 30-min window
    assert t.state == lt.STATE_ACTIVE


def test_distance_to_escalate_counts_down_from_carried_reverts():
    evs = [
        _ev("REVERT", work=5, baseline=5, cout=2, cause="no-improvement",
            ts="2026-06-16T12:25:00Z", seq=1),
    ]
    [t] = _fold(evs, max_reverts=3)
    assert t.current_reverts == 2
    assert t.distance_to_escalate == 1  # one more non-keep trips the breaker
    assert not t.escalated


# ---------------------------------------------------------------------------
# Grouping + ordering across loops.
# ---------------------------------------------------------------------------


def test_multiple_loops_grouped_and_newest_activity_first():
    evs = [
        _ev("KEEP", run_id="RID-old", work=1, baseline=0, ts="2026-06-16T09:00:00Z", seq=1),
        _ev("KEEP", run_id="RID-new", work=1, baseline=0, ts="2026-06-16T12:00:00Z", seq=2),
    ]
    trajs = _fold(evs)
    assert [t.run_id for t in trajs] == ["RID-new", "RID-old"]


def test_unattributed_events_bucket_under_one_label():
    evs = [_ev("KEEP", run_id="", work=1, baseline=0, ts="2026-06-16T12:00:00Z", seq=1)]
    [t] = _fold(evs)
    assert t.run_id == ""
    assert t.display_run == lt.UNATTRIBUTED


# ---------------------------------------------------------------------------
# from_event detail parsing — the byte-clean inputs only; narrated is context.
# ---------------------------------------------------------------------------


def test_from_event_reads_evidence_counts():
    ev = _ev("KEEP", work=46, baseline=43, cout=0, tokens=1234, narrated="i did the thing")
    it = lt.LoopIteration.from_event(ev)
    assert it.work == 46
    assert it.baseline_work == 43
    assert it.delta == 3
    assert it.tokens == 1234
    assert it.suite_passed is True
    assert it.truth_clean is True
    assert it.narrated == "i did the thing"  # carried…


def test_narrated_never_enters_a_band_or_count():
    """The forgeable channel is surfaced as context, never folded into a verdict."""
    evs = [
        _ev("KEEP", work=1, baseline=0,
            narrated="THIS IS THE BEST IMPROVEMENT EVER, definitely keep", seq=1),
    ]
    [t] = _fold(evs)
    # The KEEP tally / metric come from the env facts, not the boast.
    assert t.keeps == 1
    # The summary note is built from the verdict + counts, never the narration.
    note = lt._iteration_note(t.iterations[-1])
    assert "BEST IMPROVEMENT" not in note


def test_missing_metric_detail_folds_to_none_curve_not_crash():
    """A pre-#381 fossil with no evidence.* detail still folds (None curve)."""
    ev = VerdictEvent(syscall="improve", verdict="KEEP", run_id="RID-z",
                      ts="2026-06-16T12:00:00Z", seq=1, detail={})
    [t] = _fold([ev])
    assert t.start_baseline is None
    assert t.high_water is None
    assert t.net_gain is None
    # renders without raising
    assert "RID-z" in lt.render_loops_text([t])


# ---------------------------------------------------------------------------
# to_dict round-trip (the --json surface).
# ---------------------------------------------------------------------------


def test_to_dict_carries_the_curve_and_breaker():
    evs = [
        _ev("KEEP", work=43, baseline=40, ts="2026-06-16T12:00:00Z", seq=1),
        _ev("REVERT", work=43, baseline=43, cout=1, cause="no-improvement",
            ts="2026-06-16T12:20:00Z", seq=2),
    ]
    [t] = _fold(evs)
    d = t.to_dict()
    assert d["n"] == 2
    assert d["start_baseline"] == 40
    assert d["high_water"] == 43
    assert d["current_reverts"] == 1
    assert d["distance_to_escalate"] == 2
    assert d["state"] in (lt.STATE_ACTIVE, lt.STATE_STALLED, lt.STATE_ESCALATED)
    assert len(d["iterations"]) == 2


# ---------------------------------------------------------------------------
# CLI integration — `dos observe --loops` over a real journal (the boundary read).
# ---------------------------------------------------------------------------


@pytest.fixture()
def journal(tmp_path, monkeypatch) -> Path:
    p = tmp_path / "verdict-journal.jsonl"
    monkeypatch.setenv("DISPATCH_VERDICT_JOURNAL_PATH", str(p))
    return p


def test_cli_observe_loops_renders_recorded_loop(journal, capsys):
    from dos import cli
    record(_ev("KEEP", run_id="RID-cli", work=50, baseline=40,
               ts="2026-06-16T12:00:00Z", lane="src"))
    rc = cli.main(["observe", "--loops"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "RID-cli" in out
    assert "40→50" in out


def test_cli_observe_loops_json_is_machine_readable(journal, capsys):
    import json
    from dos import cli
    record(_ev("KEEP", run_id="RID-cli", work=50, baseline=40,
               ts="2026-06-16T12:00:00Z"))
    rc = cli.main(["observe", "--loops", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert "loops" in payload
    assert payload["loops"][0]["run_id"] == "RID-cli"


def test_cli_observe_loops_single_run_full_curve(journal, capsys):
    from dos import cli
    record(_ev("KEEP", run_id="RID-cli", work=43, baseline=40,
               ts="2026-06-16T12:00:00Z", seq=1))
    record(_ev("KEEP", run_id="RID-cli", work=46, baseline=43,
               ts="2026-06-16T12:05:00Z", seq=2))
    rc = cli.main(["observe", "--loops", "--run", "RID-cli"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "loop RID-cli" in out
    assert "iteration(s)" in out
    # both iterations rendered
    assert "40->43" in out
    assert "43->46" in out


# ---------------------------------------------------------------------------
# The correlation wiring (docs/383 Phase 2) — `dos improve --observe --run-id
# --subject` records a journal event the trajectory projection can fold.
# ---------------------------------------------------------------------------


def test_improve_observe_records_correlated_event(journal, capsys):
    """`dos improve --observe --run-id --subject` journals a foldable iteration."""
    from dos import cli
    rc = cli.main([
        "improve", "--suite-passed", "--truth-clean", "--work", "5",
        "--baseline-work", "3", "--run-id", "RID-wired", "--lane", "src",
        "--subject", "cycle-1", "--observe",
    ])
    capsys.readouterr()  # drain the verdict line
    assert rc == 0  # KEEP
    events = [e for e in vj.read_events() if e.syscall == "improve"]
    assert len(events) == 1
    ev = events[0]
    assert ev.run_id == "RID-wired"
    assert ev.subject == "cycle-1"
    assert ev.lane == "src"
    # and it folds into a trajectory
    [t] = _fold(events, now=dt.datetime.now(dt.timezone.utc))
    assert t.run_id == "RID-wired"
    assert t.keeps == 1


def test_improve_without_observe_records_nothing(journal):
    """No --observe / DISPATCH_OBSERVE ⇒ the verdict evaporates (the opt-in floor)."""
    from dos import cli
    cli.main([
        "improve", "--suite-passed", "--truth-clean", "--work", "5",
        "--baseline-work", "3", "--run-id", "RID-quiet",
    ])
    assert [e for e in vj.read_events() if e.syscall == "improve"] == []
