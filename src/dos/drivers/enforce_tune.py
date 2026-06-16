"""dos.drivers.enforce_tune — the self-tuning ENFORCEMENT-POLICY loop (docs/365).

The driver that closes DOS's PDP→PEP→policy feedback loop. DOS decides an
intervention verdict (`intervention.choose_intervention`), a host consumer acts on
it, and the act is journaled (`lane_journal.enforce_entry`). docs/189 named the gap:
nothing fed *what the act turned out to be* back into the policy that drove it. This
driver is that loop — it tunes the enforcement POLICY KNOBS (the `[intervention]` /
`[improve]` table values + ladder ranks) from a metric the KERNEL measures, never the
agent's claim.

It is `drivers.self_improve` POINTED AT THE ENFORCEMENT POLICY. The whole loop
skeleton — propose → gather → classify → actuate, the worktree isolation, the
ratchet, the breaker-to-human escalation — is reused VERBATIM from
`self_improve.run_loop`; this module only supplies the four injected callbacks and
the enforcement-specific metric. The keep-decision stays `improve.classify`'s: a
candidate is KEPT only on a kernel-measured strict gain with the suite green and the
truth syscall clean.

THE METRIC — kernel-measured, never agent-reported
==================================================

The improvement metric is `intervention_eval.score(policy, cases, ladder)
.net_task_delta` — the docs/143 §13.2 instrument, the SAME number `dos
intervention-eval` reports. The policy is RE-LOADED from the *candidate worktree's*
`dos.toml`, so what is scored is the candidate's actual knob edit, never what the
proposing agent narrated. The `cases` are env-authored from two sources:

  * a FROZEN labelled corpus (`InterventionCase` JSONL) — the docs/143 honesty
    baseline, so the loop has signal before a workspace has journal history.
  * the LIVE `OP_ENFORCE` journal, folded by `enforce_outcomes` into false-DENY /
    held-catch labels (the live augmenter — the operator's override window is the
    ground-truth correction the loop did not author).

Because the metric is computed by the kernel from cases the loop did not author, the
loop **cannot keep a policy edit by claiming it is better** (the `improve.classify`
docs/234 invariant, here aimed at the enforcement policy). The only path to KEEP is
to actually move `net_task_delta`.

THE AUTONOMY RAIL — autonomous within the policy-knob blast radius ONLY
======================================================================

With `apply=True` a KEEP verdict is auto-merged — no human in the apply path for a
witnessed improvement. The safety is structural:

  * `improve.classify` refuses an unwitnessed keep (the non-forgeable keep-bit).
  * the breaker ESCALATEs to a human when the loop runs dry (the RSI bottleneck seed).
  * `candidate_touches_runtime` REVERTs — regardless of metric — any candidate whose
    diff touched enforcement *logic* (`self_modify._DISPATCH_RUNTIME_FILES`). The loop
    owns the KNOBS; the SELF_MODIFY guard still owns the LOGIC blast radius. A policy
    tuner that rewrote `arbiter.py` to make a number go up is exactly the
    self-modification hazard DOS refuses — so it is refused here too, before merge.

Layer-4 driver: the only home for the I/O the kernel leaves out (subprocess, git,
disk, the metric gather). Imports the kernel leaves + the self-improve engine; never
imported by them (the `drivers/__init__` one-way arrow).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from dos import improve, intervention, intervention_eval
from dos.drivers import self_improve

# The runtime-logic blast radius the autonomy rail refuses to auto-merge into. Read
# from the SELF_MODIFY guard's own set so the two can never drift: a file the
# admission guard hard-denies a live loop from editing is a file this loop must never
# auto-apply a candidate that touched. (A candidate may edit `dos.toml` / a non-T1
# policy module freely; only the kernel's adjudication-path logic is off-limits.)
from dos.self_modify import _DISPATCH_RUNTIME_FILES as _RUNTIME_FILES
from dos._tree import norm_tree_prefix as _norm_tree_prefix
from dos._tree import prefixes_collide as _prefixes_collide


# A non-negative integer scaling of the [-1, +1]-ish `net_task_delta` so it slots
# into `improve.CandidateEvidence.work` (a non-negative count). 1e6 resolution
# keeps tiny but real deltas distinguishable; the +1 offset makes a net-harmful
# policy a small positive work, never negative (the keep-gate contract — a strict
# `work > baseline_work` still decides KEEP, the offset is order-preserving).
_DELTA_SCALE = 1_000_000


def scale_delta(net_task_delta: float) -> int:
    """Map a `net_task_delta` ([-1,+1]) to a non-negative `improve.work` int. Pure.

    Order-preserving: `a > b  ⇔  scale_delta(a) > scale_delta(b)`, so the kernel's
    strict `work > baseline_work` gain test is exactly "the candidate's net delta
    strictly beat the baseline's". Clamped at 0 so a pathological sub-(-1) delta
    cannot produce a negative work the keep-gate rejects.
    """
    return max(0, round((net_task_delta + 1.0) * _DELTA_SCALE))


def candidate_touches_runtime(changed_files: list[str]) -> list[str]:
    """The enforcement-LOGIC runtime files a candidate's diff touched (empty = none).

    The autonomy rail. `changed_files` is the repo-relative path list of the
    candidate commit's diff (the driver gathers it via `git diff --name-only` at the
    I/O boundary; this function is pure). Returns the `_DISPATCH_RUNTIME_FILES` the
    diff collided with, using the SAME prefix algebra the SELF_MODIFY guard uses
    (`_tree.prefixes_collide`), so "did this candidate edit the kernel's own
    adjudication logic?" is decided identically to how the arbiter decides "may this
    lane edit it." A non-empty result forces a REVERT regardless of the metric.
    """
    hits: list[str] = []
    changed_prefixes = [_norm_tree_prefix(p) for p in (changed_files or []) if p]
    if not changed_prefixes:
        return hits
    for original in _RUNTIME_FILES:
        rp = _norm_tree_prefix(original)
        if any(_prefixes_collide(cp, rp) for cp in changed_prefixes):
            hits.append(original)
    return hits


@dataclass(frozen=True)
class EnforceMetricInputs:
    """Everything the metric gather needs to score ONE policy state. Injected by the host.

    The driver does not know how to load a policy from a worktree or read the live
    journal — those are I/O. The host supplies these as already-gathered values /
    callables so the engine stays unit-testable on fakes:

      corpus        — the frozen labelled `InterventionCase` tuple (the baseline cases).
      ladder        — the active `InterventionLadder` (the ranks the candidate may
                      also have tuned; re-loaded from the worktree by the host).
      live_outcomes — the `enforce_outcomes.EnforceOutcome` tuple folded from the live
                      journal (the augmenter). Carried for the operator surface; the
                      `net_task_delta` rides the corpus today (the live outcomes feed
                      `enforce_outcomes.outcome_metric`, reported alongside).
    """

    corpus: tuple
    ladder: intervention.InterventionLadder
    live_outcomes: tuple = ()


def score_policy(
    policy: intervention.InterventionPolicy, inputs: EnforceMetricInputs
) -> int:
    """The metric: `net_task_delta` of `policy` over the corpus, scaled to `improve.work`.

    PURE — runs the policy through `intervention_eval.score` (the SAME
    `choose_intervention` path the consumer's PEP takes, so the grid reflects what
    would be enacted) and scales the headline `net_task_delta`. This is the function
    the gather callback calls on both the baseline policy and the candidate policy;
    the kernel's strict `work > baseline_work` then decides KEEP.
    """
    report = intervention_eval.score(policy, list(inputs.corpus), ladder=inputs.ladder)
    return scale_delta(report.net_task_delta)


@dataclass(frozen=True)
class EnforceCandidate:
    """One proposed policy-knob edit, as the injected proposer returns it.

    The enforcement-tuning specialization of `self_improve.Candidate`: it adds the
    candidate's `changed_files` (the diff path list) so the runtime-logic rail can
    refuse a candidate that edited adjudication logic, and the worktree root so the
    gather can re-load the candidate's policy. Every trusted field is RE-MEASURED by
    the driver; `narrated` is the proposer's word and is parsed for nothing.
    """

    present: bool
    commit: str = ""
    narrated: str = ""
    tokens: int = 0
    worktree_root: str = ""
    changed_files: tuple = ()


@dataclass(frozen=True)
class EnforceCycleContext:
    """The host's injected I/O for one enforcement-tuning cycle.

    Mirrors `self_improve.CycleContext` but its callbacks are enforcement-shaped. The
    driver wires them into `self_improve.run_cycle` by adapting them: it gathers the
    candidate's policy + diff, computes `work` via `score_policy`, and applies the
    runtime rail BEFORE handing the env-authored facts to the kernel.

      propose          — () -> EnforceCandidate. Apply ONE policy-knob edit in the
                         isolated worktree; the untrusted, intelligent step.
      suite_passes     — (EnforceCandidate) -> bool. Run the host suite on the worktree.
      truth_clean      — (EnforceCandidate) -> bool. `dos commit-audit` the candidate.
      load_policy      — (EnforceCandidate) -> InterventionPolicy. Re-load the policy
                         from the candidate worktree's dos.toml (the scored policy).
      merge            — (EnforceCandidate) -> None. KEEP actuation (auto-merge).
      discard          — (EnforceCandidate) -> None. REVERT actuation.
      escalate         — (improve.CandidateVerdict) -> None. ESCALATE actuation.
      metric_inputs    — the corpus/ladder/live-outcomes for `score_policy`.
      baseline_work    — the scaled metric on the GREEN baseline tree (the work the
                         candidate must strictly beat).
      apply            — autonomous-apply switch. False ⇒ a KEEP verdict is REPORTED
                         but NOT merged (dry-run, the safe default); True ⇒ a KEEP is
                         auto-merged. Either way the verdict is `improve.classify`'s.
      policy           — the `improve.ImprovePolicy` (the keep-gate thresholds).
    """

    propose: Callable[[], EnforceCandidate]
    suite_passes: Callable[[EnforceCandidate], bool]
    truth_clean: Callable[[EnforceCandidate], bool]
    load_policy: Callable[[EnforceCandidate], intervention.InterventionPolicy]
    merge: Callable[[EnforceCandidate], None]
    discard: Callable[[EnforceCandidate], None]
    escalate: Callable[["improve.CandidateVerdict"], None]
    metric_inputs: EnforceMetricInputs
    baseline_work: int
    apply: bool = False
    policy: improve.ImprovePolicy = field(default_factory=improve.ImprovePolicy)


def run_cycle(
    ctx: EnforceCycleContext, consecutive_reverts: int = 0
) -> self_improve.CycleResult:
    """Run ONE enforcement-tuning cycle by ADAPTING `self_improve.run_cycle`.

    Builds the four `self_improve.CycleContext` callbacks from the enforcement ctx:

      * propose  — wraps `ctx.propose`, carrying the candidate through.
      * gather   — the enforcement-specific witness gather:
                     1. the runtime-logic RAIL — if the candidate's diff touched an
                        enforcement-logic runtime file, force the witness to a FAILED
                        truth (so `improve.classify` REVERTs it as a regression),
                        regardless of the metric. The autonomy's hard rail.
                     2. else run the host suite + truth syscall, re-load the
                        candidate's policy from its worktree, and score it via
                        `score_policy` → `work`.
      * merge/discard/escalate — `ctx.merge`/`discard`/`escalate`, but `merge` is
                     SUPPRESSED to `discard` when `apply=False` (dry-run): the verdict
                     is still KEEP, but nothing is merged. The verdict the caller reads
                     is unchanged — only the actuation is gated.

    Returns the `self_improve.CycleResult` verbatim, so the loop, ratchet, and
    breaker are exactly the reference loop's.
    """

    # Thread the proposed candidate through (the adapted callbacks share it).
    box: dict[str, EnforceCandidate] = {}

    def _propose() -> self_improve.Candidate:
        cand = ctx.propose()
        box["cand"] = cand
        return self_improve.Candidate(
            present=cand.present,
            commit=cand.commit,
            narrated=cand.narrated,
            tokens=cand.tokens,
        )

    def _gather(_c: self_improve.Candidate) -> self_improve.WitnessReadback:
        cand = box["cand"]
        # 1. THE RUNTIME-LOGIC RAIL — refuse a candidate that edited adjudication
        #    logic, before any metric is even computed. A logic edit fails the truth
        #    witness (so the kernel REVERTs it as a REGRESSION), regardless of the
        #    suite or the metric. This is the autonomy's non-negotiable hard rail.
        hits = candidate_touches_runtime(list(cand.changed_files))
        if hits:
            return self_improve.WitnessReadback(
                suite_passed=False,
                truth_clean=False,  # a logic edit is NEVER truth-clean for this loop
                work=ctx.baseline_work,  # irrelevant — the floor already reverts it
            )
        # 2. The ordinary env-authored gather: suite, truth, the candidate's metric.
        suite = bool(ctx.suite_passes(cand))
        truth = bool(ctx.truth_clean(cand))
        if not suite or not truth:
            # A failing floor short-circuits the metric — the kernel reverts on the
            # floor; we still report the baseline work so the evidence is coherent.
            return self_improve.WitnessReadback(
                suite_passed=suite, truth_clean=truth, work=ctx.baseline_work
            )
        candidate_policy = ctx.load_policy(cand)
        work = score_policy(candidate_policy, ctx.metric_inputs)
        return self_improve.WitnessReadback(
            suite_passed=suite, truth_clean=truth, work=work
        )

    def _merge(_c: self_improve.Candidate) -> None:
        cand = box["cand"]
        if ctx.apply:
            ctx.merge(cand)
        else:
            # Dry-run: the verdict is KEEP but nothing is merged. Discard the
            # worktree candidate so the tree is left clean for inspection.
            ctx.discard(cand)

    def _discard(_c: self_improve.Candidate) -> None:
        ctx.discard(box["cand"])

    si_ctx = self_improve.CycleContext(
        propose=_propose,
        gather=_gather,
        merge=_merge,
        discard=_discard,
        escalate=ctx.escalate,
        baseline_work=ctx.baseline_work,
        policy=ctx.policy,
    )
    return self_improve.run_cycle(si_ctx, consecutive_reverts=consecutive_reverts)


def run_loop(
    ctx: EnforceCycleContext,
    *,
    max_cycles: int,
    consecutive_reverts: int = 0,
    on_cycle: "Optional[Callable[[self_improve.CycleResult], None]]" = None,
) -> self_improve.LoopOutcome:
    """Run up to `max_cycles` enforcement-tuning cycles, ratcheting the metric.

    The bounded outer loop. Identical in shape to `self_improve.run_loop` — it stops
    on the FIRST of an ESCALATE (the breaker → a human) or `max_cycles` (the
    backstop) — but each cycle is the enforcement-adapted `run_cycle` (the
    runtime-logic rail + the `net_task_delta` metric). The baseline ratchets after a
    KEEP so the next candidate must beat the IMPROVED policy. `on_cycle` is the host's
    per-cycle record sink (the loop record / `dos top` surface); the engine writes
    nothing itself.
    """
    from dataclasses import replace

    cycles: list[self_improve.CycleResult] = []
    kept = reverted = skipped = 0
    baseline = ctx.baseline_work
    reverts = consecutive_reverts
    escalated = False
    stop_reason = f"reached the {max_cycles}-cycle cap"

    for i in range(max_cycles):
        cycle_ctx = replace(ctx, baseline_work=baseline)
        result = run_cycle(cycle_ctx, consecutive_reverts=reverts)
        cycles.append(result)
        if on_cycle is not None:
            on_cycle(result)

        if result.action is self_improve.CycleAction.MERGED:
            kept += 1
        elif result.action is self_improve.CycleAction.DISCARDED:
            reverted += 1
        elif result.action is self_improve.CycleAction.SKIPPED:
            skipped += 1

        baseline = result.next_baseline
        reverts = result.next_consecutive_reverts

        if result.should_stop:
            escalated = True
            stop_reason = (
                f"ESCALATED to a human after {reverts} candidates in a row that "
                f"nothing accepted (cycle {i + 1})"
            )
            break

    return self_improve.LoopOutcome(
        cycles=tuple(cycles),
        kept=kept,
        reverted=reverted,
        skipped=skipped,
        escalated=escalated,
        final_baseline=baseline,
        stop_reason=stop_reason,
    )
