"""dos.drivers.merge_gate — the worktree-merge admission ENGINE (docs/327 build #1).

The driver half of the worktree-merge gate. The kernel leaf (`dos.mergegate`) is a
PURE floor verdict — `classify(MergeEvidence, MergePolicy) -> CLEAN/REFUSE`. This
module is the layer-4 driver that does the I/O the kernel refuses: it runs the test
suite on the candidate worktree, runs the truth syscall, runs `commit_audit` over
the candidate commit, optionally runs the two-tree test-witness, calls the kernel,
and carries out the verdict (merge on CLEAN / hold back on REFUSE).

THE GENERALIZATION OF `run_cycle` — the COMMIT half, host-agnostic
==================================================================

docs/327 §4 names the realization this engine ships: `drivers/self_improve.py`'s
`run_cycle` is the general worktree-merge admission protocol — apply-in-worktree →
gather env-authored witnesses from a clean process → merge only on a clean verdict —
specialized to a *self-improvement metric*. This engine is that protocol with the
metric generalized away to the host-agnostic merge floor (suite + truth + audit +
optional test-witness, no improvement requirement — see `dos.mergegate` for why a
merge gate must admit a correct no-op that `improve` would revert).

THE DELIBERATE BOUNDARY — the engine adjudicates NOTHING
========================================================

The verdict is the PURE kernel's (`mergegate.classify`); this engine only gathers
the witnesses and actuates. Like every driver, it gathers + actuates and does not
adjudicate. The witnesses are gathered through injected callbacks, so:

  * the engine is fully DETERMINISTIC and unit-testable on fakes (no suite, no git,
    no subprocess in a unit test), and
  * the merge-bit is provably a function of env-authored witnesses, never of
    whatever the branch's commit message narrated.

This module names no host beyond the callbacks and reads the witnesses through them,
so it is domain-free: the host names *how to gather* each witness (which suite, which
phase to verify, which commit to audit) and *how to actuate* the merge; the engine
owns the gather→classify→actuate skeleton.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from dos import commit_audit, mergegate, testwitness


# ---------------------------------------------------------------------------
# The injected boundary — what the host supplies to gate one branch.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateContext:
    """Everything the engine needs to gate ONE worktree branch — the host's injected I/O.

    The callbacks are the seam: the engine owns the gather→classify→actuate skeleton;
    the host owns every side-effecting step. All are plain callables so the engine is
    testable on fakes. Each gather callback authors ONE env witness on the candidate
    worktree; none is read from the branch's own claim.

      gather_suite — () -> bool. Run the host's test suite on the candidate worktree
                     and return True iff it exited 0. The runner authors it.
      gather_truth — () -> bool. Run the truth syscall (`dos verify` for any phase the
                     candidate claims, and/or the truth side of `dos commit-audit`)
                     and return True iff clean. The oracle + git ancestry author it.
      gather_audit — () -> mergegate / commit_audit verdict. Run `commit_audit` over
                     the candidate commit (e.g. `commit_audit.audit_commit(sha,
                     root=...)`) and return its `ClaimVerdict` (or None if the commit
                     is unreadable — treated fail-safe as NOT OK). The commit
                     machinery authored the diff; the verdict reads it.
      gather_twv   — OPTIONAL () -> TestWitnessVerdict | None. Run the two-tree
                     test-witness (the new test on the BASELINE worktree, then on the
                     CANDIDATE worktree) and return its verdict. Called ONLY when
                     `policy.require_test_witness` (the disarmed path pays no
                     baseline-checkout cost). None / absent ⇒ the rung is absent,
                     which the armed floor treats fail-safe as a failing witness.
      merge        — (MergeVerdict) -> None. CLEAN actuation: merge the candidate's
                     worktree branch onto the target (the engine calls this only on a
                     CLEAN verdict).
      refuse       — (MergeVerdict) -> None. REFUSE actuation: hold the branch back,
                     leaving the target untouched (surface the typed refuse cause).
      narrated     — the branch's own description of why it should merge. Carried to
                     the operator surface and the kernel's `narrated` field — parsed
                     for NOTHING (docs/234). The ONE field that is the branch's word,
                     and by construction it touches no part of the verdict.
      policy       — the `mergegate.MergePolicy` (whether the test-witness rung is
                     armed; the host's `dos.toml [mergegate]`).
    """

    gather_suite: Callable[[], bool]
    gather_truth: Callable[[], bool]
    gather_audit: Callable[[], "Optional[commit_audit.ClaimVerdict]"]
    merge: Callable[["mergegate.MergeVerdict"], None]
    refuse: Callable[["mergegate.MergeVerdict"], None]
    gather_twv: "Optional[Callable[[], Optional[testwitness.TestWitnessVerdict]]]" = None
    narrated: str = ""
    policy: mergegate.MergePolicy = field(default_factory=mergegate.MergePolicy)


@dataclass(frozen=True)
class GateResult:
    """The outcome of gating ONE branch — the verdict and the act taken.

    `verdict` is the kernel's `MergeVerdict` (CLEAN / REFUSE with the typed cause).
    `merged` is True iff the engine carried out the merge (CLEAN). It is the legible
    record a run-archive / `dos top` surface reads: not just "REFUSE" but the
    `verdict.refuse_cause` behind it.
    """

    verdict: "mergegate.MergeVerdict"
    merged: bool


def run_gate(ctx: GateContext) -> GateResult:
    """Gate ONE worktree branch: gather → classify → actuate. The deterministic engine.

    Steps:

      1. GATHER — author each env witness on the candidate worktree: run the suite,
         run the truth syscall, run `commit_audit`, and (only when the policy arms it)
         run the two-tree test-witness. Every fact is env-authored; the branch's
         commit message is carried in `narrated` and moves nothing.
      2. CLASSIFY — hand the env-authored witnesses to the PURE kernel
         (`mergegate.classify`). The merge-decision is the kernel's.
      3. ACTUATE — carry out the verdict: CLEAN → merge; REFUSE → hold the branch back.

    Returns a `GateResult` carrying the verdict and whether the merge happened. PURE
    of policy: the one knob is in `ctx.policy`, every side effect is in `ctx`'s
    callbacks — the engine just wires them.
    """
    # 1. GATHER — the env-authored witnesses, measured on the candidate worktree.
    suite_passed = ctx.gather_suite()
    truth_clean = ctx.gather_truth()

    audit_verdict = ctx.gather_audit()
    # A None (unreadable commit) is fail-safe: treat as NOT OK, never as clean.
    audit_ok = (
        audit_verdict is not None
        and audit_verdict.verdict is commit_audit.Verdict.OK
    )

    # The optional test-witness rung — gathered ONLY when the host armed it.
    test_witnesses: Optional[bool] = None
    twv_verdict: Optional[testwitness.TestWitness] = None
    if ctx.policy.require_test_witness and ctx.gather_twv is not None:
        twv = ctx.gather_twv()
        if twv is not None:
            twv_verdict = twv.verdict
            test_witnesses = twv.verdict is testwitness.DISCRIMINATES

    # 2. CLASSIFY — the PURE kernel merge floor. The branch's `narrated` rides along
    #    but, by construction (docs/234), cannot move the verdict.
    evidence = mergegate.MergeEvidence(
        suite_passed=suite_passed,
        truth_clean=truth_clean,
        audit_ok=audit_ok,
        test_witnesses=test_witnesses,
        audit_verdict=(audit_verdict.verdict if audit_verdict is not None else None),
        twv_verdict=twv_verdict,
        narrated=ctx.narrated,
    )
    verdict = mergegate.classify(evidence, ctx.policy)

    # 3. ACTUATE — carry out the kernel's verdict.
    if verdict.is_clean:
        ctx.merge(verdict)
        return GateResult(verdict=verdict, merged=True)

    ctx.refuse(verdict)
    return GateResult(verdict=verdict, merged=False)
