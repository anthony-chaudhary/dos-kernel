"""Tests for `drivers.enforce_tune` — the self-tuning enforcement-policy loop (docs/365).

Every test runs on a FAKE proposer (no git, no subprocess, no model) so the engine is
deterministic. The witnesses (suite/truth/metric) are injected; the keep-decision is
the real `improve.classify`. The load-bearing tests pin the autonomy rails:

  * a metric-improving candidate is KEPT and (with apply=True) merged;
  * a no-op candidate is REVERTED, the tree untouched;
  * a candidate that edited enforcement LOGIC is REVERTED even if the metric rose
    (the runtime-logic rail) — and a test that FAILS if the rail is removed;
  * apply=False suppresses the merge but not the verdict (dry-run).
"""

from __future__ import annotations

import dos.drivers.enforce_tune as et
from dos import improve, intervention
from dos.arg_provenance import ProvenanceVerdict
from dos.drivers import self_improve
from dos.intervention_eval import InterventionCase


# ---------------------------------------------------------------------------
# A tiny labelled corpus: one true-relevant mint that recovers if blocked. A policy
# that BLOCKs it scores positively; an all-OBSERVE/WARN policy scores ~0. So the
# `net_task_delta` genuinely separates two policies — the metric has teeth.
# ---------------------------------------------------------------------------
def _high_conf_verdict() -> ProvenanceVerdict:
    # Reuse the maintained compact constructor (the SAME one `dos intervention-eval`
    # uses to load a corpus from `confidence: HIGH`) so the verdict shape can never
    # drift from the real eval path. A HIGH-confidence mint → BLOCK under the
    # default policy.
    from dos.cli import _verdict_from_compact

    return _verdict_from_compact("HIGH", ["incident_id"], "test:1")


def _corpus() -> tuple:
    return (
        InterventionCase(
            verdict=_high_conf_verdict(),
            truly_minted=True,
            mattered_to_score=True,
            recovered_if_blocked=True,
            recovered_if_deferred=True,
            label="relevant-mint",
        ),
    )


def _inputs() -> et.EnforceMetricInputs:
    return et.EnforceMetricInputs(
        corpus=_corpus(),
        ladder=intervention.BASE_INTERVENTIONS,
        live_outcomes=(),
    )


# Two policies whose net_task_delta differs: BLOCK-on-high beats OBSERVE-on-high.
_BLOCK_POLICY = intervention.InterventionPolicy(
    on_high_confidence="BLOCK", on_low_confidence="WARN", ceiling="BLOCK")
_TIMID_POLICY = intervention.InterventionPolicy(
    on_high_confidence="WARN", on_low_confidence="WARN", floor="WARN", ceiling="WARN")


def test_metric_separates_two_policies():
    block_work = et.score_policy(_BLOCK_POLICY, _inputs())
    timid_work = et.score_policy(_TIMID_POLICY, _inputs())
    # BLOCKing the relevant mint prevents the corruption → higher net delta.
    assert block_work > timid_work


def test_scale_delta_is_order_preserving_and_non_negative():
    assert et.scale_delta(0.5) > et.scale_delta(0.1) > et.scale_delta(-0.1)
    assert et.scale_delta(-2.0) == 0  # clamped


# ---------------------------------------------------------------------------
# A fake proposer harness: returns a scripted candidate and records actuations.
# ---------------------------------------------------------------------------
class _Harness:
    def __init__(self, candidate: et.EnforceCandidate, *, policy, suite=True, truth=True):
        self._candidate = candidate
        self._policy = policy
        self._suite = suite
        self._truth = truth
        self.merged: list = []
        self.discarded: list = []
        self.escalated: list = []

    def propose(self):
        return self._candidate

    def suite_passes(self, _c):
        return self._suite

    def truth_clean(self, _c):
        return self._truth

    def load_policy(self, _c):
        return self._policy

    def merge(self, c):
        self.merged.append(c)

    def discard(self, c):
        self.discarded.append(c)

    def escalate(self, v):
        self.escalated.append(v)


def _ctx(h: _Harness, *, baseline_work: int, apply: bool) -> et.EnforceCycleContext:
    return et.EnforceCycleContext(
        propose=h.propose,
        suite_passes=h.suite_passes,
        truth_clean=h.truth_clean,
        load_policy=h.load_policy,
        merge=h.merge,
        discard=h.discard,
        escalate=h.escalate,
        metric_inputs=_inputs(),
        baseline_work=baseline_work,
        apply=apply,
    )


# ---------------------------------------------------------------------------
# KEEP: a metric-improving candidate is kept and merged (apply=True).
# ---------------------------------------------------------------------------
def test_improving_candidate_is_kept_and_merged():
    baseline = et.score_policy(_TIMID_POLICY, _inputs())
    cand = et.EnforceCandidate(present=True, commit="abc123", narrated="block high-conf",
                               changed_files=("dos.toml",))
    h = _Harness(cand, policy=_BLOCK_POLICY)
    result = et.run_cycle(_ctx(h, baseline_work=baseline, apply=True))
    assert result.verdict is not None
    assert result.verdict.verdict is improve.Candidate.KEEP
    assert result.action is self_improve.CycleAction.MERGED
    assert h.merged == [cand]
    assert h.discarded == []


def test_improving_candidate_dry_run_reports_keep_but_does_not_merge():
    baseline = et.score_policy(_TIMID_POLICY, _inputs())
    cand = et.EnforceCandidate(present=True, commit="abc123", changed_files=("dos.toml",))
    h = _Harness(cand, policy=_BLOCK_POLICY)
    result = et.run_cycle(_ctx(h, baseline_work=baseline, apply=False))
    # The VERDICT is still KEEP — only the actuation is gated.
    assert result.verdict.verdict is improve.Candidate.KEEP
    # ...but nothing was merged; the worktree candidate was discarded for inspection.
    assert h.merged == []
    assert h.discarded == [cand]


# ---------------------------------------------------------------------------
# REVERT: a no-op candidate (metric flat) is reverted.
# ---------------------------------------------------------------------------
def test_noop_candidate_is_reverted():
    baseline = et.score_policy(_BLOCK_POLICY, _inputs())
    cand = et.EnforceCandidate(present=True, commit="def456", changed_files=("dos.toml",))
    h = _Harness(cand, policy=_BLOCK_POLICY)
    result = et.run_cycle(_ctx(h, baseline_work=baseline, apply=True))
    assert result.verdict.verdict is improve.Candidate.REVERT
    assert result.verdict.revert_cause is improve.RevertCause.NO_IMPROVEMENT
    assert h.merged == []
    assert h.discarded == [cand]


# ---------------------------------------------------------------------------
# THE RUNTIME-LOGIC RAIL: a candidate that edited enforcement LOGIC is reverted
# even when its metric rose. This is the autonomy's hard rail.
# ---------------------------------------------------------------------------
def test_candidate_editing_runtime_logic_is_reverted_despite_metric_gain():
    baseline = et.score_policy(_TIMID_POLICY, _inputs())
    cand = et.EnforceCandidate(
        present=True, commit="bad789", narrated="tuned by rewriting the arbiter",
        changed_files=("src/dos/arbiter.py", "dos.toml"))
    h = _Harness(cand, policy=_BLOCK_POLICY)
    result = et.run_cycle(_ctx(h, baseline_work=baseline, apply=True))
    assert result.verdict.verdict is improve.Candidate.REVERT
    assert result.verdict.revert_cause is improve.RevertCause.REGRESSED
    assert h.merged == []
    assert h.discarded == [cand]


def test_runtime_rail_detects_each_guarded_file():
    from dos.self_modify import _DISPATCH_RUNTIME_FILES
    for f in _DISPATCH_RUNTIME_FILES:
        assert et.candidate_touches_runtime([f]) == [f]
    # A pure policy/config edit touches none.
    assert et.candidate_touches_runtime(["dos.toml"]) == []
    assert et.candidate_touches_runtime(["src/dos/intervention.py"]) == []  # policy, not T1-logic
    assert et.candidate_touches_runtime([]) == []


def test_whole_repo_lease_trips_the_rail():
    hits = et.candidate_touches_runtime(["**/*"])
    assert hits  # not empty — the kernel logic is in the blast radius


# ---------------------------------------------------------------------------
# SKIP + ESCALATE: the engine's terminal behaviors, inherited from self_improve.
# ---------------------------------------------------------------------------
def test_absent_candidate_skips():
    cand = et.EnforceCandidate(present=False)
    h = _Harness(cand, policy=_BLOCK_POLICY)
    result = et.run_cycle(_ctx(h, baseline_work=0, apply=True))
    assert result.action is self_improve.CycleAction.SKIPPED
    assert result.verdict is None
    assert h.merged == [] and h.discarded == []


def test_loop_escalates_after_max_reverts():
    baseline = et.score_policy(_BLOCK_POLICY, _inputs())
    cand = et.EnforceCandidate(present=True, commit="noop", changed_files=("dos.toml",))
    h = _Harness(cand, policy=_BLOCK_POLICY)  # always flat → always REVERT
    ctx = et.EnforceCycleContext(
        propose=h.propose, suite_passes=h.suite_passes, truth_clean=h.truth_clean,
        load_policy=h.load_policy, merge=h.merge, discard=h.discard, escalate=h.escalate,
        metric_inputs=_inputs(), baseline_work=baseline, apply=True,
        policy=improve.ImprovePolicy(max_consecutive_reverts=2),
    )
    outcome = et.run_loop(ctx, max_cycles=5)
    assert outcome.escalated is True
    assert outcome.kept == 0
    assert len(h.escalated) == 1
    assert "ESCALATED" in outcome.stop_reason


def test_loop_ratchets_the_baseline_after_a_keep():
    baseline = et.score_policy(_TIMID_POLICY, _inputs())
    block_work = et.score_policy(_BLOCK_POLICY, _inputs())

    calls = {"n": 0}

    def propose():
        calls["n"] += 1
        return et.EnforceCandidate(present=True, commit=f"c{calls['n']}",
                                   changed_files=("dos.toml",))

    merged: list = []
    discarded: list = []
    ctx = et.EnforceCycleContext(
        propose=propose,
        suite_passes=lambda _c: True,
        truth_clean=lambda _c: True,
        load_policy=lambda _c: _BLOCK_POLICY,
        merge=merged.append,
        discard=discarded.append,
        escalate=lambda _v: None,
        metric_inputs=_inputs(),
        baseline_work=baseline,
        apply=True,
    )
    outcome = et.run_loop(ctx, max_cycles=2)
    assert outcome.kept == 1       # the first cycle kept
    assert outcome.reverted == 1   # the second was flat against the ratchet
    assert outcome.final_baseline == block_work
