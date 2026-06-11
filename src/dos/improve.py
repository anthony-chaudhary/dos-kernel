"""improve — the self-improving-loop admission verdict: may this loop KEEP this candidate? (docs/280)

The kernel leaf of the **first self-improving work loop for DOS**. A
self-improvement loop is a `propose -> verify -> measure -> keep-or-revert`
cycle: an agent proposes a change, the change is checked, its effect is measured,
and it is kept only if the measurement says it helped. The shape is universal;
its one fatal failure mode is the one DOS exists to refuse —

    the loop grades its own homework: the agent that WROTE the change is the one
    that decides "yes, this is better," so it learns to NARRATE improvement
    instead of making it.

This module is the witness-gated keep-gate that closes that hole. It is
`reward.admit` (docs/230/234 — "may a fine-tune TRAIN on this trajectory?")
re-aimed at a development loop ("may this loop KEEP this commit?"), conjoined with
the green-suite floor of the apply gate (docs/126) and bounded by the circuit
breaker (docs/223). It is the `loop_decide.decide` (docs/258) of the **outer**
loop, whose iterations are candidate self-improvements rather than dispatch
packets:

    loop_decide.decide     (LoopState, IterationOutcome)  -> LoopDecision   (inner: one dispatch iteration)
    improve.classify       (CandidateEvidence, policy)    -> CandidateVerdict (outer: one self-improvement)
                           ^ THIS module

THE NON-FORGEABLE KEEP-BIT — why this makes recursive self-improvement honest
=============================================================================

The Anthropic recursive-self-improvement research names verification as the
gating constraint on the whole enterprise ("requiring verification regimes
enabling labs to confirm"; "human judgment on which problems matter remains the
acknowledged bottleneck"). The keep-bit here is a pure function of four facts,
**every one authored by the environment, none by the loop**:

  * `suite_passed`  — the test runner's exit status on the candidate-only tree.
  * `truth_clean`   — `dos verify` / `dos commit-audit` over git ancestry.
  * `work`          — the env-measured improvement metric, AFTER the candidate.
  * `baseline_work` — the same metric, BEFORE the candidate.

There is no input the loop authors that can move REVERT -> KEEP. The candidate's
commit message, its code comments, its self-assessment — none are read by
`classify` (a `narrated` string is carried for the operator surface and parsed
for NOTHING, exactly as in `reward.admit`). This is the docs/234 theorem at loop
scale: *the policy cannot write its way into the kept set.* A loop that learns to
write "this is a great improvement" in every commit gains exactly zero
keep-probability, because the claim is not in the decision. The only path to KEEP
is to actually make the suite greener / the truth syscall clean / the metric move
— i.e. **to actually do the work.** That is the docs/138 invariant (the bytes
that decide are bytes the claimant did not author), and it is why DOS — the
substrate the loop runs ON and that refuses to believe it — is the right place
for a self-improving loop.

**Advisory, like every verdict leaf.** `classify` REPORTS KEEP/REVERT/ESCALATE;
it executes no merge, no `git checkout`, no escalation. The driver
(`dos.drivers.self_improve`) gathers the four facts at its I/O boundary and
carries out the verdict — the same mechanism/policy/actuation split as
`liveness`/`efficiency`/`breaker`.

**Mechanism is the kernel; which-metric is policy.** The kernel does not know
whether "improvement" means a newly-passing property test, a removed line of dead
policy `dos lint` reports zero of, a faster `dos verify` wall-clock, or a smaller
cold-start. The host names the metric in `dos.toml [improve]` and gathers it; the
kernel only compares the two magnitudes and reads the two boolean witnesses — the
`productivity`/`efficiency` work-unit split, applied to a candidate diff.

No I/O, no clock, names no host: a pure `classify(evidence, policy)` leaf.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from dos import breaker, efficiency
from dos.spend import SpendBreakdown


class Candidate(str, enum.Enum):
    """The verdict for one candidate self-improvement.

    `str`-valued so it round-trips through a CLI stdout token / exit-code map
    without a lookup table (the `liveness.Liveness` / `breaker.BreakerState`
    idiom). Three outcomes — the whole decision space of "what do I do with this
    candidate diff?":

      KEEP     — a WITNESSED improvement: the suite is green on the candidate
                 tree, the truth syscall is clean, the env-measured metric strictly
                 improved, and the spend was not WASTEFUL. Merge it; reset the
                 breaker. This is the loop's `reward.admit` ACCEPT.
      REVERT   — discard the candidate and keep the tree as it was. Either it
                 REGRESSED (suite red or truth dirty — the non-negotiable floor) or
                 it was a NO-OP (safe but the metric did not move). The loop's
                 default action on an unwitnessed candidate is UNDO, not keep — the
                 abstention-first discipline (docs/87) at loop scale.
      ESCALATE — the breaker is OPEN: too many candidates in a row that nothing
                 accepted. Stop proposing and surface to a human — the RSI
                 research's irreducible "human judgment on which problems matter"
                 seed (docs/223 Escalation.HUMAN).
    """

    KEEP = "KEEP"
    REVERT = "REVERT"
    ESCALATE = "ESCALATE"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class RevertCause(str, enum.Enum):
    """Why a REVERT — the floor failure vs the no-op miss, kept distinct.

    A REVERT verdict is two genuinely different situations and an operator routes
    them differently, so the kernel names which one fired (the `liveness`/`breaker`
    echo-the-evidence / `tripped_on` discipline):

      REGRESSED  — the candidate broke the suite or failed the truth syscall. A
                   FAULT: the change actively made the kernel worse. The
                   non-negotiable floor — undone before any improvement is even
                   considered.
      NO_IMPROVEMENT — the candidate is safe (suite green, truth clean) but the
                   env-measured metric did not strictly beat the baseline. A MISS,
                   not a fault: the change cost effort and moved nothing the metric
                   can see. Undone to keep the tree minimal.
      WASTEFUL   — the candidate improved the metric AND kept the suite green, but
                   `efficiency.classify` ruled the spend WASTEFUL/COSTLY for that
                   gain (a host armed an efficiency floor and the candidate fell
                   under it). A real-but-overpriced improvement the host chose to
                   refuse. Only reachable when the host arms an efficiency floor;
                   with the default disabled floor this cause never fires.
    """

    REGRESSED = "regressed"
    NO_IMPROVEMENT = "no-improvement"
    WASTEFUL = "wasteful"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class ImprovePolicy:
    """The thresholds that gate a KEEP — policy, not mechanism.

    The same "mechanism is kernel, thresholds are config" split as `breaker`'s
    maxima and `efficiency`'s floor. The defaults are generic; a workspace declares
    its own in `dos.toml [improve]` (closed-config-as-data, like `[breaker]`).

      max_consecutive_reverts — trip the ESCALATE breaker after this many candidates
                   IN A ROW that did not KEEP (each REVERT bumps the carried count;
                   a KEEP resets it). The RSI research's bottleneck made
                   operational: when the loop cannot find a witnessed improvement
                   after N tries, hand the "which problem matters next" judgment
                   back to a human rather than burn budget on candidates nothing
                   accepts. The `breaker.BreakerPolicy.max_consecutive` rung.
      efficiency_floor — the work-per-token floor handed to `efficiency.classify`
                   when judging whether a metric-improving candidate was worth its
                   spend. Default 0.0 = DISABLED (the `efficiency` default): every
                   improving candidate with green suite + clean truth is KEEP
                   regardless of token cost, and the WASTEFUL revert-cause never
                   fires. A host arms this only when it has a token budget that
                   means something for its improvement unit. (WASTEFUL-for-zero-work
                   never arises here because a zero-`work` candidate is already a
                   NO_IMPROVEMENT revert — the efficiency rung only ever sees a
                   candidate whose metric already beat the baseline.)
      min_tokens_for_efficiency — the `efficiency.classify` min-spend floor below
                   which the efficiency rung withholds its accusation (a candidate
                   that improved the metric on a handful of tokens is not WASTEFUL,
                   it is cheap). Passed straight through to `EfficiencyPolicy`.

    `max_consecutive_reverts` must be >= 1 (a breaker that escalates on zero
    reverts would escalate before the loop ever ran).
    """

    max_consecutive_reverts: int = 3
    efficiency_floor: float = 0.0
    min_tokens_for_efficiency: int = 1000

    def __post_init__(self) -> None:
        if self.max_consecutive_reverts < 1:
            raise ValueError(
                "max_consecutive_reverts must be >= 1 (a breaker that escalates "
                "before the loop runs is a config error)"
            )
        if self.efficiency_floor < 0:
            raise ValueError("efficiency_floor must be non-negative")
        if self.min_tokens_for_efficiency < 0:
            raise ValueError("min_tokens_for_efficiency must be non-negative")


DEFAULT_POLICY = ImprovePolicy()


@dataclass(frozen=True)
class CandidateEvidence:
    """The facts `classify()` reads — gathered by the CALLER at its I/O boundary.

    No subprocess, no git, no clock inside the verdict (the arbiter rule): the
    driver runs the suite, runs the truth syscall, measures the metric, and counts
    the tokens, then freezes the RESULTS here. Every field is env-authored — the
    docs/138 invariant that makes the keep-bit non-forgeable:

      suite_passed   — the test runner's exit status on a tree carrying the
                       candidate's diff AND NOTHING ELSE (the candidate-only tree —
                       a worktree, so a concurrent edit cannot contaminate the
                       witness). True iff `python -m pytest -q` (or the host's
                       suite) exited 0. The runner authored it.
      truth_clean    — True iff the truth witnesses are clean: `dos verify` for any
                       phase the candidate claims, and/or `dos commit-audit` over
                       the candidate commit (the subject's claim matches its diff).
                       The git machinery + the oracle authored it; the loop did not.
      work           — the env-measured improvement metric AFTER the candidate (the
                       host's unit: passing-property-test count, 1000 minus the
                       `dos lint` finding count, a wall-clock budget, …). The same
                       work unit `productivity`/`efficiency` count. Non-negative.
      baseline_work  — the SAME metric BEFORE the candidate (measured on the green
                       baseline tree at the start of the cycle). Non-negative. KEEP
                       requires `work > baseline_work` — a STRICT, env-measured gain.
      tokens         — the tokens the candidate's proposing agent spent (the provider
                       usage record), the env-authored price for the efficiency rung.
                       Non-negative.
      consecutive_reverts — the CARRIED breaker count: how many prior candidates in
                       a row did not KEEP. Threaded by the driver through the cycle
                       (the `breaker.BreakerCounts.consecutive` / `LoopState`
                       counter). Non-negative.
      breakdown      — OPTIONAL (docs/300): the typed five-way split of the same
                       spend (`dos.spend.SpendBreakdown`), threaded into the
                       efficiency rung so the keep/revert record can state WHAT
                       KIND of spend bought the candidate (cache-hit ratio,
                       decode share, reasoning share). Pure legibility — the
                       keep-bit still rides the scalar ladder. When given with
                       `tokens=0` the scalar derives from `breakdown.total`;
                       when both are given they must agree (a mismatch is a
                       contract error, the `EfficiencyEvidence` rule).

    `narrated` is the candidate's own description of what it did — carried for the
    operator surface (`dos improve --json` echoes it) and **parsed for nothing**.
    It is the agent-authored, forgeable channel; by construction it touches no part
    of the verdict (the `reward.admit` / `effect_witness` `narrated`-is-not-read
    discipline). It exists so the operator can read *why the loop thought* a
    reverted candidate would help — never so the loop can argue its way to a KEEP.
    """

    suite_passed: bool
    truth_clean: bool
    work: int = 0
    baseline_work: int = 0
    tokens: int = 0
    consecutive_reverts: int = 0
    narrated: str = ""
    breakdown: Optional[SpendBreakdown] = None

    def __post_init__(self) -> None:
        if self.work < 0:
            raise ValueError("work must be non-negative (a count of the metric)")
        if self.baseline_work < 0:
            raise ValueError("baseline_work must be non-negative")
        if self.tokens < 0:
            raise ValueError("tokens must be non-negative (a count of tokens spent)")
        if self.consecutive_reverts < 0:
            raise ValueError("consecutive_reverts must be non-negative")
        if self.breakdown is not None:
            if self.tokens == 0:
                # The scalar derives from the split — one source of truth.
                object.__setattr__(self, "tokens", self.breakdown.total)
            elif self.tokens != self.breakdown.total:
                raise ValueError(
                    f"tokens ({self.tokens}) disagrees with breakdown.total "
                    f"({self.breakdown.total}) — an inconsistent evidence pair is "
                    f"a contract error, never silently reconciled"
                )

    @property
    def improved(self) -> bool:
        """True iff the env-measured metric STRICTLY beat the baseline — the gain
        the environment witnessed, never the agent's claim of one."""
        return self.work > self.baseline_work

    @property
    def delta(self) -> int:
        """The signed metric change (after minus before). Positive iff improved."""
        return self.work - self.baseline_work


@dataclass(frozen=True)
class CandidateVerdict:
    """The single verdict `classify()` returns, with the facts echoed back.

    `verdict` is the typed `Candidate`. `revert_cause` is set ONLY on a REVERT
    (which of the two/three revert situations fired — REGRESSED / NO_IMPROVEMENT /
    WASTEFUL), None otherwise. `next_consecutive_reverts` is the breaker count the
    driver carries into the NEXT cycle — reset to 0 on KEEP, bumped on REVERT, and
    on ESCALATE held at the tripping value (the loop is stopping, the count is
    historical). `escalation` is the trust-ladder rung an ESCALATE names (HUMAN by
    construction — a stuck improver is a human-seed, never a deterministic
    re-check); NONE otherwise. `reason` is the one-line operator-facing summary.
    `evidence` is echoed so `dos improve --json` emits the verdict AND the facts
    behind it in one object (the legible-distrust renderer seam — the operator sees
    not just REVERT but *why*: suite red, or metric flat at 42).
    """

    verdict: Candidate
    next_consecutive_reverts: int
    reason: str
    evidence: CandidateEvidence
    revert_cause: Optional[RevertCause] = None
    escalation: breaker.Escalation = breaker.Escalation.NONE
    # docs/300 — the efficiency rung's full verdict, set whenever the rung ran
    # (the improving paths). Carried so a breakdown-bearing keep/revert record
    # can state the candidate's price facts; None on the paths that never
    # priced the candidate (a regression / a no-improvement miss).
    efficiency_verdict: Optional[efficiency.EfficiencyVerdict] = None

    @property
    def is_keep(self) -> bool:
        return self.verdict is Candidate.KEEP

    def to_dict(self) -> dict:
        e = self.evidence
        out = {
            "verdict": self.verdict.value,
            "revert_cause": self.revert_cause.value if self.revert_cause else None,
            "escalation": self.escalation.value,
            "next_consecutive_reverts": self.next_consecutive_reverts,
            "reason": self.reason,
            "evidence": {
                "suite_passed": e.suite_passed,
                "truth_clean": e.truth_clean,
                "work": e.work,
                "baseline_work": e.baseline_work,
                "delta": e.delta,
                "improved": e.improved,
                "tokens": e.tokens,
                "consecutive_reverts": e.consecutive_reverts,
                "narrated": e.narrated,
            },
        }
        # docs/300 — the price facts, surfaced ONLY for breakdown-carrying
        # evidence so the scalar path keeps its exact old JSON shape.
        if e.breakdown is not None:
            out["evidence"]["breakdown"] = e.breakdown.to_dict()
            if self.efficiency_verdict is not None:
                out["efficiency"] = self.efficiency_verdict.to_dict()
        return out


def _efficiency_policy(policy: ImprovePolicy) -> efficiency.EfficiencyPolicy:
    """Build the `EfficiencyPolicy` for the WASTEFUL rung from the improve policy."""
    return efficiency.EfficiencyPolicy(
        min_tokens=policy.min_tokens_for_efficiency, floor=policy.efficiency_floor
    )


def classify(
    evidence: CandidateEvidence, policy: ImprovePolicy = DEFAULT_POLICY
) -> CandidateVerdict:
    """Decide KEEP / REVERT / ESCALATE for one candidate self-improvement. PURE — no I/O.

    Reads the ladder top to bottom (this function IS the answer to "when does the
    loop keep a candidate?"):

      1. REVERT (regressed) — the suite is red OR the truth syscall is dirty. The
         non-negotiable floor: a self-improvement that breaks the kernel is not an
         improvement, full stop. Checked FIRST, before any gain is considered, and
         before the breaker — a regression is undone whether or not the breaker
         would also escalate (the operator wants "it broke the suite," and the
         bumped count rides along in `next_consecutive_reverts` for the next cycle).
         The conjunctive floor (docs/113 `admissible_under_floor`): KEEP requires
         clearing this floor AND the improvement policy admitting — the floor alone
         can never be overridden by a "but it's better" claim.

      2. ESCALATE (breaker open) — the candidate did NOT clear the floor as an
         improvement (it is heading for a REVERT) AND bumping the carried
         `consecutive_reverts` reaches `max_consecutive_reverts`. Stop proposing and
         surface to a human (docs/223 Escalation.HUMAN) — the RSI bottleneck: N
         candidates in a row that nothing accepted means the loop should hand the
         "what matters next" judgment back, not propose an (N+1)th. Checked AFTER
         the regression floor so the operator still learns a regression *as* a
         regression even on the escalating candidate — the ESCALATE carries the
         revert_cause that tipped it.

      3. KEEP (witnessed improvement) — suite green AND truth clean AND the metric
         STRICTLY improved (`work > baseline_work`, an env-measured gain) AND the
         spend was not WASTEFUL/COSTLY by `efficiency.classify`. The improvement is
         witnessed by bytes the loop did not author, so keep it and RESET the
         breaker (`next_consecutive_reverts = 0`). This is the loop's `reward.admit`
         ACCEPT — the gain is a count the environment authored, never the claim.

      4. REVERT (no improvement / wasteful) — suite green and truth clean, but
         either the metric did not beat the baseline (NO_IMPROVEMENT — a safe miss)
         or it did but the spend was WASTEFUL/COSTLY under an armed efficiency floor
         (WASTEFUL — a real-but-overpriced gain the host refuses). Undo it to keep
         the tree minimal; bump the breaker (a no-op still counts toward escalation,
         so a loop that only ever proposes no-ops eventually stops).

    The breaker arithmetic is `breaker.record_failure` / `record_success`
    specialized to the carried `consecutive_reverts` field — the same primitive
    `loop_decide` routes its streaks through (docs/258). A REVERT is a `failure`
    (bump); a KEEP is a `success` (reset).
    """
    brk_policy = breaker.BreakerPolicy(
        max_consecutive=policy.max_consecutive_reverts,
        max_total=0,  # consecutive-only — a no-op streak escalates; cumulative is not the signal
        on_trip=breaker.Escalation.HUMAN,
    )

    # 1. REVERT (regressed) — the non-negotiable floor. A red suite or a dirty truth
    #    syscall is undone before any improvement is weighed. We still compute the
    #    breaker transition so the carried count rides forward (and an escalating
    #    regression surfaces as a regression, below).
    if not evidence.suite_passed or not evidence.truth_clean:
        trip = breaker.record_failure(
            breaker.BreakerCounts(consecutive=evidence.consecutive_reverts),
            brk_policy,
        )
        why = (
            "the test suite is RED"
            if not evidence.suite_passed
            else "the truth syscall is DIRTY (dos verify / commit-audit refused)"
        )
        if trip.verdict.is_open:
            return CandidateVerdict(
                verdict=Candidate.ESCALATE,
                next_consecutive_reverts=trip.counts.consecutive,
                reason=(
                    f"candidate REGRESSED ({why}) AND that is "
                    f"{trip.counts.consecutive} reverted candidates in a row "
                    f"(>= {policy.max_consecutive_reverts}) — revert and ESCALATE "
                    f"to a human (the loop can't find a witnessed improvement)"
                ),
                evidence=evidence,
                revert_cause=RevertCause.REGRESSED,
                escalation=trip.verdict.escalation,
            )
        return CandidateVerdict(
            verdict=Candidate.REVERT,
            next_consecutive_reverts=trip.counts.consecutive,
            reason=(
                f"candidate REGRESSED ({why}) — revert; it broke the kernel, so it "
                f"is not an improvement no matter what it claims "
                f"({trip.counts.consecutive}/{policy.max_consecutive_reverts} "
                f"consecutive reverts)"
            ),
            evidence=evidence,
            revert_cause=RevertCause.REGRESSED,
        )

    # The candidate cleared the non-negotiable floor (suite green, truth clean).
    # Now: did it actually IMPROVE the env-measured metric?
    if evidence.improved:
        # It improved. Was the spend worth it? Ask `efficiency` — but only the
        # WASTEFUL/COSTLY rung can refuse here (a zero-work candidate never reaches
        # this branch — it is a NO_IMPROVEMENT revert below). With the default
        # disabled floor, `efficiency` always returns EFFICIENT for nonzero work, so
        # this rung is a no-op until a host arms a floor.
        eff = efficiency.classify(
            efficiency.EfficiencyEvidence.of(
                work=evidence.delta,
                tokens=evidence.tokens,
                breakdown=evidence.breakdown,  # docs/300 — the price facts ride along
            ),
            _efficiency_policy(policy),
        )
        if eff.verdict is efficiency.Efficiency.EFFICIENT:
            # KEEP — a witnessed, well-priced improvement. Reset the breaker.
            healed = breaker.record_success(
                breaker.BreakerCounts(consecutive=evidence.consecutive_reverts),
                brk_policy,
            )
            return CandidateVerdict(
                verdict=Candidate.KEEP,
                next_consecutive_reverts=healed.counts.consecutive,  # 0
                reason=(
                    f"candidate KEPT — suite green, truth clean, metric improved "
                    f"{evidence.baseline_work} -> {evidence.work} (+{evidence.delta}); "
                    f"witnessed by bytes the loop did not author, so the gain is real"
                ),
                evidence=evidence,
                efficiency_verdict=eff,
            )
        # Improved, but the host's efficiency floor refused the price. A REVERT, not
        # a KEEP — bump the breaker like any other non-keep.
        trip = breaker.record_failure(
            breaker.BreakerCounts(consecutive=evidence.consecutive_reverts),
            brk_policy,
        )
        verdict = Candidate.ESCALATE if trip.verdict.is_open else Candidate.REVERT
        return CandidateVerdict(
            verdict=verdict,
            next_consecutive_reverts=trip.counts.consecutive,
            reason=(
                f"candidate improved the metric (+{evidence.delta}) but the spend "
                f"was {eff.verdict} for that gain ({eff.reason}) — revert the "
                f"overpriced improvement"
                + (
                    f" AND ESCALATE ({trip.counts.consecutive} reverts in a row)"
                    if trip.verdict.is_open
                    else ""
                )
            ),
            evidence=evidence,
            revert_cause=RevertCause.WASTEFUL,
            escalation=trip.verdict.escalation,
            efficiency_verdict=eff,
        )

    # 4. REVERT (no improvement) — safe but the metric did not move. A miss, not a
    #    fault. Undo to keep the tree minimal; bump the breaker so an all-no-op loop
    #    eventually escalates.
    trip = breaker.record_failure(
        breaker.BreakerCounts(consecutive=evidence.consecutive_reverts),
        brk_policy,
    )
    if trip.verdict.is_open:
        return CandidateVerdict(
            verdict=Candidate.ESCALATE,
            next_consecutive_reverts=trip.counts.consecutive,
            reason=(
                f"candidate did not improve the metric (flat at {evidence.work}) AND "
                f"that is {trip.counts.consecutive} non-improving candidates in a row "
                f"(>= {policy.max_consecutive_reverts}) — revert and ESCALATE to a "
                f"human (the loop has run dry of witnessed improvements)"
            ),
            evidence=evidence,
            revert_cause=RevertCause.NO_IMPROVEMENT,
            escalation=trip.verdict.escalation,
        )
    return CandidateVerdict(
        verdict=Candidate.REVERT,
        next_consecutive_reverts=trip.counts.consecutive,
        reason=(
            f"candidate is safe (suite green, truth clean) but did not improve the "
            f"metric (flat at {evidence.work}) — revert; a safe no-op is not kept "
            f"({trip.counts.consecutive}/{policy.max_consecutive_reverts} consecutive "
            f"reverts)"
        ),
        evidence=evidence,
        revert_cause=RevertCause.NO_IMPROVEMENT,
    )
