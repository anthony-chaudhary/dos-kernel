"""Tests for the dispatch-family noise filter (`dos.event_severity`).

`classify_event` is the pure keystone: every operator-facing sink (push, commit,
terminal, report, artifact) filters a bookkeeping event by comparing its severity
against a per-sink threshold, exactly the way a logging framework filters by level.
These tests replay the full event -> severity mapping table from the module docstring
(so the table can never silently drift), pin the `(verdict, first_occurrence)`
collapse that kills the repeated-blocker flood, and lock the default-quiet thresholds
+ their env-override behaviour.
"""

from __future__ import annotations

import pytest

from dos import event_severity as es
from dos.event_severity import EventState, Severity
from dos.work_account import WorkAccount


class TestClassifyDispatch:
    """dispatch + dispatch-loop share one branch (same archive shape)."""

    @pytest.mark.parametrize("family", ["dispatch", "dispatch-loop"])
    def test_shipped_when_picks_landed(self, family):
        assert es.classify_event(EventState(family, verdict="LIVE", picks_shipped=2)) is Severity.SHIPPED

    @pytest.mark.parametrize("family", ["dispatch", "dispatch-loop"])
    def test_live_verdict_is_shipped_even_with_zero_pick_count(self, family):
        # verdict=LIVE is the ship signal; the pick count may be folded elsewhere.
        assert es.classify_event(EventState(family, verdict="LIVE")) is Severity.SHIPPED

    @pytest.mark.parametrize("family", ["dispatch", "dispatch-loop"])
    def test_drain_is_noop(self, family):
        assert es.classify_event(EventState(family, verdict="DRAIN", picks_shipped=0)) is Severity.NOOP

    @pytest.mark.parametrize("verdict", ["BLOCKED", "RACE", "ERROR", "RATE_LIMITED"])
    def test_first_blocker_is_blocked_new(self, verdict):
        ev = EventState("dispatch-loop", verdict=verdict, first_occurrence=True)
        assert es.classify_event(ev) is Severity.BLOCKED_NEW

    @pytest.mark.parametrize("verdict", ["BLOCKED", "RACE", "ERROR", "RATE_LIMITED"])
    def test_repeated_blocker_is_noop(self, verdict):
        # The whole point: the SAME blocker recurring every iter is noise, not signal.
        ev = EventState("dispatch-loop", verdict=verdict, first_occurrence=False)
        assert es.classify_event(ev) is Severity.NOOP

    def test_stale_stamp_is_notice(self):
        assert es.classify_event(EventState("dispatch", verdict="STALE-STAMP")) is Severity.NOTICE

    def test_legacy_wedge_folds_to_blocked(self):
        # normalize_token maps WEDGE -> BLOCKED, so a historical verdict=WEDGE
        # classifies identically to BLOCKED (first -> BLOCKED-NEW, repeat -> NOOP).
        assert es.classify_event(EventState("dispatch", verdict="WEDGE", first_occurrence=True)) is Severity.BLOCKED_NEW
        assert es.classify_event(EventState("dispatch", verdict="wedge", first_occurrence=False)) is Severity.NOOP

    def test_unknown_token_falls_through_to_noop(self):
        assert es.classify_event(EventState("dispatch", verdict="GARBLE")) is Severity.NOOP


class TestClassifyDispatchWorkAccount:
    """docs/310 — the optional work-kind account on a dispatch-family event.

    The account subdivides the iteration's work by witnessed KIND, so
    real-but-not-pick-shaped work stops reading as a non-event. account=None
    (every test above) stays byte-identical legacy behavior.
    """

    def test_verified_ship_in_account_is_shipped(self):
        # The oracle confirmed a ship the picks_shipped bit never carried.
        ev = EventState("dispatch-loop", verdict="DRAIN",
                        account=WorkAccount(verified_ships=1))
        assert es.classify_event(ev) is Severity.SHIPPED

    @pytest.mark.parametrize("account", [
        WorkAccount(catches=1),          # a refused false claim
        WorkAccount(advance_commits=4),  # partial progress, no phase closed
        WorkAccount(grooms=2),           # stamps reconciled
        WorkAccount(unblocks=1),         # a HELD unit freed
        WorkAccount(surfaced=2),         # operator decisions raised
    ])
    def test_non_pick_work_lifts_drain_to_notice(self, account):
        # The headline fix: a 0-pick iteration that did witnessed work is
        # NOTICE, no longer a NOOP "drained".
        ev = EventState("dispatch-loop", verdict="DRAIN", account=account)
        assert es.classify_event(ev) is Severity.NOTICE

    def test_idle_account_stays_noop(self):
        # An all-zero account adds nothing — the honest drain stays a NOOP.
        ev = EventState("dispatch", verdict="DRAIN", account=WorkAccount())
        assert es.classify_event(ev) is Severity.NOOP

    def test_claims_alone_do_not_lift(self):
        # The non-forgeability rail: a self-reported ship with no oracle answer
        # cannot climb the severity ladder.
        ev = EventState("dispatch", verdict="DRAIN",
                        account=WorkAccount(claimed_ships=3))
        assert es.classify_event(ev) is Severity.NOOP

    def test_blocker_still_outranks_account_work(self):
        # A first-seen blocker is rank-2 actionable; the account's NOTICE-grade
        # work does not mask it.
        ev = EventState("dispatch", verdict="RATE_LIMITED", first_occurrence=True,
                        account=WorkAccount(grooms=3))
        assert es.classify_event(ev) is Severity.BLOCKED_NEW

    def test_subject_composes_account_headline_on_ship(self):
        ev = EventState("dispatch-loop", verdict="LIVE",
                        account=WorkAccount(verified_ships=1, advance_commits=4))
        assert es.subject_lead_token(ev) == "1 pick shipped · 4 commits advanced"

    def test_subject_composes_account_headline_on_notice(self):
        ev = EventState("dispatch-loop", verdict="DRAIN",
                        account=WorkAccount(catches=1, grooms=2))
        assert es.subject_lead_token(ev) == "1 false claim caught · 2 grooms"

    def test_subject_without_account_unchanged(self):
        # Back-compat: the legacy headline grammar is byte-identical when no
        # account was gathered.
        ev = EventState("dispatch-loop", verdict="LIVE", picks_shipped=3)
        assert es.subject_lead_token(ev) == "3 picks shipped"
        assert es.subject_lead_token(
            EventState("dispatch", verdict="DRAIN")) == "drained"


class TestClassifyReplan:
    def test_inbox_promotion_is_shipped(self):
        assert es.classify_event(EventState("replan", surfaced=1)) is Severity.SHIPPED

    def test_findings_or_ships_is_notice(self):
        assert es.classify_event(EventState("replan", new_findings=3)) is Severity.NOTICE
        assert es.classify_event(EventState("replan", substantive_ships=1)) is Severity.NOTICE

    def test_gardening_only_quiet_sweep_is_noop(self):
        # closed==0, added==0, surfaced==0 -> the no-op quiet-sweep flood.
        assert es.classify_event(EventState("replan")) is Severity.NOOP


class TestClassifyNextUp:
    def test_soft_claim_is_notice(self):
        assert es.classify_event(EventState("next-up", soft_claims=2, staged_changed=True)) is Severity.NOTICE

    def test_no_staged_change_is_noop(self):
        assert es.classify_event(EventState("next-up", soft_claims=2, staged_changed=False)) is Severity.NOOP

    def test_zero_soft_claims_is_noop(self):
        assert es.classify_event(EventState("next-up", soft_claims=0, staged_changed=True)) is Severity.NOOP


class TestFailSafeDefaults:
    def test_unknown_family_surfaces_as_notice(self):
        assert es.classify_event(EventState("mystery", verdict="DRAIN")) is Severity.NOTICE

    def test_first_occurrence_defaults_true(self):
        # An unknown caller that omits first_occurrence must fail toward surfacing.
        assert EventState("dispatch", verdict="BLOCKED").first_occurrence is True


class TestSinkThresholds:
    def test_default_thresholds_are_quiet(self):
        # push is the highest bar; commit keeps everything; the rest sit at NOTICE.
        assert es.sink_threshold("push") is Severity.BLOCKED_NEW
        assert es.sink_threshold("commit") is Severity.NOOP
        for sink in ("terminal", "report", "artifact"):
            assert es.sink_threshold(sink) is Severity.NOTICE

    def test_every_sink_has_an_env_key(self):
        for sink in es.SINKS:
            neutral_key, job_key, default = es._SINK_ENV[sink]
            # the PRIMARY key is kernel-neutral (DISPATCH_*, names no host); the
            # JOB_-prefixed key is the documented back-compat fallback.
            assert neutral_key.startswith("DISPATCH_") and neutral_key.endswith("_MIN_SEVERITY")
            assert not neutral_key.startswith("JOB_")
            assert job_key == "JOB_" + neutral_key
            assert isinstance(default, Severity)

    def test_neutral_env_override(self, monkeypatch):
        # the kernel-neutral key works on its own (a generic workspace's surface)
        monkeypatch.delenv("JOB_DISPATCH_PUSH_MIN_SEVERITY", raising=False)
        monkeypatch.setenv("DISPATCH_PUSH_MIN_SEVERITY", "NOTICE")
        assert es.sink_threshold("push") is Severity.NOTICE

    def test_neutral_key_wins_over_job_fallback(self, monkeypatch):
        monkeypatch.setenv("DISPATCH_PUSH_MIN_SEVERITY", "NOTICE")
        monkeypatch.setenv("JOB_DISPATCH_PUSH_MIN_SEVERITY", "BLOCKED_NEW")
        assert es.sink_threshold("push") is Severity.NOTICE

    def test_env_override(self, monkeypatch):
        # the JOB_-prefixed key still works as a documented back-compat fallback
        monkeypatch.delenv("DISPATCH_PUSH_MIN_SEVERITY", raising=False)
        monkeypatch.setenv("JOB_DISPATCH_PUSH_MIN_SEVERITY", "NOTICE")
        assert es.sink_threshold("push") is Severity.NOTICE

    def test_garbage_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("DISPATCH_PUSH_MIN_SEVERITY", raising=False)
        monkeypatch.setenv("JOB_DISPATCH_PUSH_MIN_SEVERITY", "LOUD")
        assert es.sink_threshold("push") is Severity.BLOCKED_NEW

    def test_unknown_sink_raises(self):
        with pytest.raises(ValueError):
            es.sink_threshold("syslog")


class TestSubjectLeadToken:
    """The mechanical commit-subject headline. The keystone property is NEGATIVE:
    the run ordinal can never leak (it is not a field of EventState), so the
    `185th /replan` flood the Phase-1 prose rule failed to stop is structurally
    impossible. These tests pin the per-family wording + the no-ordinal invariant."""

    # ── replan — the family that leaked the ordinal ──────────────────────────
    def test_replan_quiet_sweep_has_no_ordinal(self):
        tok = es.subject_lead_token(EventState("replan"))
        assert tok == "quiet sweep"

    def test_replan_notice_leads_with_counts(self):
        tok = es.subject_lead_token(EventState("replan", new_findings=2, substantive_ships=1))
        assert tok == "2 closed / 1 shipped"

    def test_replan_shipped_names_the_promotion(self):
        assert es.subject_lead_token(EventState("replan", surfaced=3)) == "inbox promoted: 3"

    @pytest.mark.parametrize("ordinal", ["183rd", "184th", "185th", "200", "(185th /replan)"])
    def test_no_replan_subject_token_ever_contains_an_ordinal(self, ordinal):
        # The whole reason this function exists: there is no input that produces
        # an ordinal, because the ordinal is not an EventState field.
        for ev in (
            EventState("replan"),
            EventState("replan", new_findings=5),
            EventState("replan", surfaced=2),
        ):
            assert ordinal not in es.subject_lead_token(ev)

    # ── dispatch / dispatch-loop ─────────────────────────────────────────────
    def test_dispatch_picks_pluralize(self):
        assert es.subject_lead_token(EventState("dispatch-loop", verdict="LIVE", picks_shipped=1)) == "1 pick shipped"
        assert es.subject_lead_token(EventState("dispatch-loop", verdict="LIVE", picks_shipped=3)) == "3 picks shipped"

    def test_dispatch_drain_is_drained(self):
        assert es.subject_lead_token(EventState("dispatch", verdict="DRAIN")) == "drained"

    def test_first_bare_blocked_has_no_redundant_parenthetical(self):
        assert es.subject_lead_token(EventState("dispatch", verdict="BLOCKED", first_occurrence=True)) == "blocked"

    def test_first_specific_blocker_names_the_wall(self):
        tok = es.subject_lead_token(EventState("dispatch", verdict="RATE_LIMITED", first_occurrence=True))
        assert tok == "blocked (rate_limited)"

    def test_repeated_blocker_collapses_to_drained(self):
        # A recurring wall is NOOP -> the dominant non-event headline.
        tok = es.subject_lead_token(EventState("dispatch", verdict="RATE_LIMITED", first_occurrence=False))
        assert tok == "drained"

    def test_legacy_wedge_headline_matches_blocked(self):
        assert es.subject_lead_token(EventState("dispatch", verdict="WEDGE", first_occurrence=True)) == "blocked"

    def test_stale_stamp_headline_is_ascii(self):
        tok = es.subject_lead_token(EventState("dispatch", verdict="STALE-STAMP"))
        assert "stale-stamp" in tok
        # Commit subjects in this repo are ASCII — no smart arrows/dashes.
        assert tok.isascii(), f"non-ASCII char in commit-subject token: {tok!r}"

    # ── next-up ──────────────────────────────────────────────────────────────
    def test_next_up_soft_claim_headline(self):
        assert es.subject_lead_token(EventState("next-up", soft_claims=2, staged_changed=True)) == "soft-claims (2 picks)"
        assert es.subject_lead_token(EventState("next-up", soft_claims=1, staged_changed=True)) == "soft-claims (1 pick)"

    def test_next_up_noop_headline(self):
        assert es.subject_lead_token(EventState("next-up", soft_claims=0, staged_changed=True)) == "no-op (lane drained)"

    # ── invariants ───────────────────────────────────────────────────────────
    def test_headline_always_agrees_with_classify(self):
        # A SHIPPED event must never headline as a drain, etc. — the renderer and
        # the gate read the same EventState, so they cannot disagree.
        shipped = EventState("dispatch-loop", verdict="LIVE", picks_shipped=1)
        assert es.classify_event(shipped) is Severity.SHIPPED
        assert "shipped" in es.subject_lead_token(shipped)
        noop = EventState("dispatch-loop", verdict="DRAIN")
        assert es.classify_event(noop) is Severity.NOOP
        assert es.subject_lead_token(noop) == "drained"

    def test_unknown_family_surfaces_severity_word(self):
        assert es.subject_lead_token(EventState("mystery", verdict="DRAIN")) == "notice"

    def test_token_is_never_empty(self):
        for ev in (
            EventState("replan"),
            EventState("dispatch", verdict="DRAIN"),
            EventState("next-up"),
            EventState(""),
        ):
            assert es.subject_lead_token(ev).strip()


class TestAdmits:
    def test_default_push_admits_shipped_and_first_blocker_only(self):
        assert es.admits("push", Severity.SHIPPED)
        assert es.admits("push", Severity.BLOCKED_NEW)
        assert not es.admits("push", Severity.NOTICE)
        assert not es.admits("push", Severity.NOOP)

    def test_default_commit_admits_everything(self):
        for sev in Severity:
            assert es.admits("commit", sev)

    def test_end_to_end_zero_pick_loop_is_local_only(self):
        # The dominant case: a 0-pick dispatch-loop drain commits locally, never pushes.
        sev = es.classify_event(EventState("dispatch-loop", verdict="DRAIN", picks_shipped=0))
        assert sev is Severity.NOOP
        assert es.admits("commit", sev) is True
        assert es.admits("push", sev) is False
