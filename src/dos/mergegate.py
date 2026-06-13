"""mergegate — the worktree-merge admission verdict: may this branch MERGE? (docs/327)

The kernel leaf of the **COMMIT half of a worktree transaction**. docs/327 reframes
a worktree as a transaction: isolation is BEGIN, the merge is COMMIT. The shipped
machinery wired the BEGIN half (the arbiter refuses a same-region collision at
spawn) but never the COMMIT half — the gate that decides *whether a candidate
branch in an isolated worktree may merge*. This module is that gate.

The realization docs/327 §4 names: `drivers/self_improve.py:run_cycle` is already
the general worktree-merge admission protocol — apply-in-worktree → gather
env-authored witnesses from a clean process → merge only on a clean verdict — just
specialized to a *self-improvement metric*. This leaf is that protocol's verdict
with the metric generalized away to the **host-agnostic merge floor.**

WHY THIS IS NOT `improve.classify` RE-AIMED
===========================================

`improve.classify` (docs/280) is the keep-gate for a *self-improvement* candidate:
it keeps a change only when an env-measured metric STRICTLY improved
(`work > baseline_work`). A merge gate is different in one decisive way — it must
**admit a correct no-op.** A docs fix, a comment fix, a metric-flat refactor merges
cleanly; `improve` would REVERT it as NO_IMPROVEMENT (see
`tests/test_improve.py::test_safe_noop_is_reverted_and_bumps_breaker`). No value of
`work`/`baseline_work` makes `improve` admit a genuine no-op, so the merge floor is
genuinely new verdict logic: the *regression floor* of `improve` (suite green AND
truth clean) widened to the full merge floor (+ commit-audit, + optional
test-witness) and stripped of the *improvement* requirement. It is a SIBLING of
`improve`, not a wrapper — it reuses the vocabulary (env-authored presence
witnesses, the `narrated`-parsed-for-nothing discipline) but not the verdict.

THE NON-FORGEABLE MERGE-BIT — every witness authored by the environment
=======================================================================

CLEAN is a pure function of presence facts, **every one authored by something other
than the branch's author**:

  * `suite_passed`   — the test runner's exit status on the candidate worktree.
  * `truth_clean`    — `dos verify` / `dos commit-audit` over git ancestry.
  * `audit_ok`       — `commit_audit.classify`: the commit subject matches its diff.
  * `test_witnesses` — OPTIONAL `testwitness` DISCRIMINATES: the new test fails on
                       the baseline tree and passes on the candidate tree. Only
                       reachable because a worktree materializes BOTH trees (a
                       shared checkout threw the baseline away — docs/327 §4).

There is no input the branch authors that can move REFUSE → CLEAN. The candidate's
commit message, its self-assessment — none are read by `classify` (a `narrated`
string is carried for the operator surface and parsed for NOTHING, exactly as in
`improve`/`reward.admit`). This is the docs/234 theorem at merge scale: *a branch
cannot write its way into the merged set.* The only path to CLEAN is to make the
suite green, the truth syscall clean, the diff match its claim — i.e. to actually
do the work.

**Advisory, like every verdict leaf.** `classify` REPORTS CLEAN/REFUSE; it executes
no `git merge`. The driver (`dos.drivers.merge_gate`) gathers the witnesses at its
I/O boundary and actuates the merge — the same mechanism/policy/actuation split as
`improve`/`liveness`/`breaker`.

**Mechanism is the kernel; which-rungs-are-armed is policy.** The AND-conjunction
is a fixed boolean floor identical for every host (no host name, no threshold, no
metric unit, no I/O). The one policy knob is whether the optional test-witness rung
is armed — config-as-data, the host declares it in `dos.toml [mergegate]`, the same
on/off shape `ImprovePolicy` carries. Everything host-shaped — which suite to run,
which phase to verify, how to actuate the merge — is the *gather* and the
*actuation*, which live in the driver.

No I/O, no clock, names no host: a pure `classify(evidence, policy)` leaf.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from dos import commit_audit, testwitness


class Merge(str, enum.Enum):
    """The verdict for one candidate worktree branch at its merge point.

    `str`-valued so it round-trips through a CLI stdout token / exit-code map
    without a lookup table (the `improve.Candidate` / `breaker.BreakerState`
    idiom). Two outcomes — the whole decision space of "may this branch merge?":

      CLEAN  — every armed presence witness is clean: the suite is green on the
               candidate tree, the truth syscall is clean, the commit subject
               matches its diff, and (if the rung is armed) the new test
               DISCRIMINATES across the two trees. The host may merge. This is the
               merge floor's ACCEPT — and it admits a CORRECT NO-OP, the deliberate
               divergence from `improve` (a no-op never improves a metric, but it
               still merges).
      REFUSE — at least one armed witness is unclean (or, for an armed test-witness
               rung, ABSENT — fail-safe). The host must not merge; the receipt names
               which rung(s) refused. The merge floor's abstention-first default
               (docs/87): an unwitnessed branch is held back, never merged on its
               own say-so.
    """

    CLEAN = "CLEAN"
    REFUSE = "REFUSE"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class RefuseCause(str, enum.Enum):
    """Which floor rung refused — echo-the-evidence so the receipt is legible.

    A REFUSE is one of four genuinely different situations and an operator routes
    them differently, so the kernel names which fired (the `improve.RevertCause` /
    `liveness` `tripped_on` discipline). Floor order is the order `classify` reads
    the rungs, so `MergeVerdict.refuse_cause` (the FIRST failing rung) is the single
    CLI token while `refuse_causes` carries ALL of them:

      SUITE_RED          — the test suite is RED on the candidate worktree. The
                           non-negotiable floor: a branch that breaks the suite is
                           not mergeable, full stop.
      TRUTH_DIRTY        — the truth syscall is dirty (`dos verify` / `dos
                           commit-audit` over git ancestry refused). The git
                           machinery + the oracle authored it; the branch did not.
      AUDIT_UNWITNESSED  — `commit_audit.classify` returned CLAIM_UNWITNESSED: the
                           commit subject claims an effect its own diff does not
                           witness (the forgeable-subject catch). Distinct from
                           TRUTH_DIRTY so the operator sees a subject-vs-diff mismatch
                           AS that, not as a generic truth failure.
      TEST_NOT_WITNESSED — the test-witness rung is ARMED but the new test did NOT
                           DISCRIMINATE across the two trees — OR the rung is armed
                           and the witness is ABSENT (fail-safe: never CLEAN on
                           missing evidence). Only reachable when a host arms the rung.
    """

    SUITE_RED = "suite-red"
    TRUTH_DIRTY = "truth-dirty"
    AUDIT_UNWITNESSED = "audit-unwitnessed"
    TEST_NOT_WITNESSED = "test-not-witnessed"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class MergePolicy:
    """The one knob that gates a CLEAN — policy, not mechanism.

    The same "mechanism is kernel, which-rung-is-armed is config" split as
    `improve`'s efficiency floor. The default is generic (the floor is suite + truth
    + audit, a correct no-op merges); a workspace arms the optional test-witness rung
    in `dos.toml [mergegate]` (closed-config-as-data, like `[improve]`).

      require_test_witness — when True, CLEAN additionally requires the new test to
                   DISCRIMINATE across the baseline and candidate trees
                   (`testwitness` DISCRIMINATES). A host arms this only when its merge
                   discipline demands a behavior-changing branch ship a test that
                   provably fails without the change. Default False: the floor is the
                   three always-on rungs and a no-op (or a branch with no new test)
                   merges on suite + truth + audit alone. When armed, an ABSENT
                   witness is a FAILING witness (fail-safe), never a pass.
    """

    require_test_witness: bool = False


DEFAULT_POLICY = MergePolicy()


@dataclass(frozen=True)
class MergeEvidence:
    """The facts `classify()` reads — gathered by the CALLER at its I/O boundary.

    No subprocess, no git, no clock inside the verdict (the arbiter rule): the
    driver runs the suite on the candidate worktree, runs the truth syscall, runs
    `commit_audit`, optionally runs the two-tree test-witness, then freezes the
    RESULTS here. Every field is env-authored — the docs/138 invariant that makes the
    merge-bit non-forgeable:

      suite_passed   — the test runner's exit status on the candidate worktree (a
                       worktree, so a concurrent edit cannot contaminate the witness).
                       True iff the host's suite exited 0. The runner authored it.
      truth_clean    — True iff the truth witnesses are clean: `dos verify` for any
                       phase the candidate claims, and/or the truth-syscall side of
                       `dos commit-audit`. The git machinery + the oracle authored it.
      audit_ok       — True iff `commit_audit.classify` returned OK over the candidate
                       commit (the subject's claim matches its diff). The commit
                       machinery authored the diff; the verdict reads it. (Carried
                       distinct from `truth_clean` so the receipt can name a
                       subject-vs-diff mismatch as AUDIT_UNWITNESSED specifically.)
      test_witnesses — OPTIONAL: True iff `testwitness.classify` returned
                       DISCRIMINATES (the new test failed on the baseline tree and
                       passed on the candidate tree). None when the host did not run
                       the rung (it is disarmed, or there is no new test). Read ONLY
                       when `policy.require_test_witness`; when armed-but-None the
                       verdict REFUSEs (fail-safe).

    `audit_verdict` / `twv_verdict` are OPTIONAL typed echoes of the sub-verdicts
    that produced `audit_ok` / `test_witnesses`, carried purely so the receipt
    (`to_dict` / `dos merge-gate --json`) can state WHICH rung answered — pure
    legibility, the merge-bit rides the booleans.

    `narrated` is the candidate branch's own description of what it did — carried for
    the operator surface and **parsed for nothing** (the `improve`/`reward.admit`
    `narrated`-is-not-read discipline). It is the agent-authored, forgeable channel;
    by construction it touches no part of the verdict. It exists so the operator can
    read *why the branch thought* it should merge — never so a branch can argue its
    way to CLEAN. There is deliberately NO author/identity field: the kernel is
    vendor-blind (`test_vendor_agnostic_kernel.py`), and merge admission reasons over
    witnesses, never over who proposed the branch.
    """

    suite_passed: bool
    truth_clean: bool
    audit_ok: bool
    test_witnesses: Optional[bool] = None
    audit_verdict: Optional[commit_audit.Verdict] = None
    twv_verdict: Optional[testwitness.TestWitness] = None
    narrated: str = ""


@dataclass(frozen=True)
class MergeVerdict:
    """The single verdict `classify()` returns, with the facts echoed back.

    `verdict` is the typed `Merge`. `refuse_cause` is the FIRST failing rung in floor
    order (the single CLI stdout token), None on a CLEAN. `refuse_causes` is the FULL
    tuple of failing rungs (for the receipt — a branch can fail several at once),
    empty on a CLEAN. `reason` is the one-line operator-facing summary. `evidence` is
    echoed so `dos merge-gate --json` emits the verdict AND the facts behind it in one
    object (the legible-distrust renderer seam — the operator sees not just REFUSE but
    *why*: suite red, or subject-vs-diff mismatch).
    """

    verdict: Merge
    reason: str
    evidence: MergeEvidence
    refuse_cause: Optional[RefuseCause] = None
    refuse_causes: tuple[RefuseCause, ...] = ()

    @property
    def is_clean(self) -> bool:
        return self.verdict is Merge.CLEAN

    def to_dict(self) -> dict:
        e = self.evidence
        return {
            "verdict": self.verdict.value,
            "refuse_cause": self.refuse_cause.value if self.refuse_cause else None,
            "refuse_causes": [c.value for c in self.refuse_causes],
            "reason": self.reason,
            "evidence": {
                "suite_passed": e.suite_passed,
                "truth_clean": e.truth_clean,
                "audit_ok": e.audit_ok,
                "test_witnesses": e.test_witnesses,
                "audit_verdict": e.audit_verdict.value if e.audit_verdict else None,
                "twv_verdict": e.twv_verdict.value if e.twv_verdict else None,
                "narrated": e.narrated,
            },
        }


# The human-readable label for each rung, used in the one-line `reason`.
_CAUSE_LABEL = {
    RefuseCause.SUITE_RED: "the test suite is RED on the candidate worktree",
    RefuseCause.TRUTH_DIRTY: "the truth syscall is DIRTY (dos verify / commit-audit refused)",
    RefuseCause.AUDIT_UNWITNESSED: (
        "the commit subject is UNWITNESSED by its own diff (commit-audit)"
    ),
    RefuseCause.TEST_NOT_WITNESSED: (
        "the new test did NOT witness the change (test-witness disarmed/absent/ABSTAIN)"
    ),
}


def classify(
    evidence: MergeEvidence, policy: MergePolicy = DEFAULT_POLICY
) -> MergeVerdict:
    """Decide CLEAN / REFUSE for one candidate worktree branch. PURE — no I/O.

    Reads the merge floor top to bottom — this function IS the answer to "when may a
    branch merge?" Each armed rung is a presence witness the branch's author did not
    write; CLEAN is their conjunction, REFUSE names every rung that failed:

      1. SUITE_RED — the suite is RED on the candidate worktree. The non-negotiable
         floor: a branch that breaks the suite is not mergeable no matter what its
         commit message claims.
      2. TRUTH_DIRTY — `dos verify` / the truth side of `dos commit-audit` over git
         ancestry refused. A branch whose claimed phase did not ship (or whose
         ancestry is dirty) is held back.
      3. AUDIT_UNWITNESSED — `commit_audit` ruled the subject UNWITNESSED by its diff
         (the forgeable-subject catch). Named distinct from TRUTH_DIRTY so a
         subject-vs-diff mismatch surfaces AS that.
      4. TEST_NOT_WITNESSED — ONLY when `policy.require_test_witness`: the new test
         did not DISCRIMINATE across the two trees, OR the witness is ABSENT. Armed +
         absent is a FAILING rung (fail-safe — never merge on missing evidence).

    A correct NO-OP — suite green, truth clean, audit OK, the test-witness rung
    disarmed — clears every armed rung and is CLEAN. This is the deliberate
    divergence from `improve.classify`, which would REVERT the same branch as
    NO_IMPROVEMENT: a merge gate admits a correct no-op; a self-improvement keep-gate
    does not. There is no metric here, by design.

    Advisory: the verdict REPORTS CLEAN/REFUSE; the driver actuates the merge.
    """
    causes: list[RefuseCause] = []

    # 1. SUITE_RED — the non-negotiable floor.
    if not evidence.suite_passed:
        causes.append(RefuseCause.SUITE_RED)

    # 2. TRUTH_DIRTY — git ancestry + the oracle.
    if not evidence.truth_clean:
        causes.append(RefuseCause.TRUTH_DIRTY)

    # 3. AUDIT_UNWITNESSED — the subject-vs-diff catch.
    if not evidence.audit_ok:
        causes.append(RefuseCause.AUDIT_UNWITNESSED)

    # 4. TEST_NOT_WITNESSED — only when the host armed the rung. Armed + absent
    #    (None) is a FAILING rung: fail-safe, never CLEAN on missing evidence.
    if policy.require_test_witness and evidence.test_witnesses is not True:
        causes.append(RefuseCause.TEST_NOT_WITNESSED)

    if not causes:
        rungs = (
            "suite green, truth clean, subject witnessed by its diff"
            + (", new test DISCRIMINATES" if policy.require_test_witness else "")
        )
        return MergeVerdict(
            verdict=Merge.CLEAN,
            reason=(
                f"branch is MERGEABLE — {rungs}; every armed witness was authored by "
                f"the environment, so the clean verdict is not the branch's say-so"
            ),
            evidence=evidence,
        )

    # REFUSE — name the first failing rung as the CLI token, all of them in the receipt.
    first = causes[0]
    detail = "; ".join(_CAUSE_LABEL[c] for c in causes)
    return MergeVerdict(
        verdict=Merge.REFUSE,
        reason=(
            f"branch REFUSED at the merge floor — {detail}. Hold it back; an "
            f"unwitnessed branch is not merged on its own claim"
        ),
        evidence=evidence,
        refuse_cause=first,
        refuse_causes=tuple(causes),
    )
