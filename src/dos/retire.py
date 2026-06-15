"""retire — the library-retention verdict: does this remembered item still EARN ITS PLACE? (docs/350)

The third leaf of the keep-only-what-a-witness-confirms family, aimed at an
accumulator the other three do not touch — the agent's growing library of
remembered lessons and learned skills:

    effect_witness.witness_effect(claim, readbacks) -> did the effect HAPPEN?      (runtime)
    reward.admit(claim_present, readbacks)          -> may a fine-tune TRAIN on it? (lab)
    improve.classify(candidate_evidence, policy)    -> may this loop KEEP this commit? (loop iteration)
    retire.classify(retire_evidence, policy)        -> does this memory/skill still EARN ITS PLACE? (library, over time)
                                                       ^ THIS module

WHY A SEPARATE VERDICT FROM STALENESS (the docs/103 recall gate)
================================================================

`drivers.memory_recall.classify_recall` already answers a DIFFERENT question:
*is this recalled lesson still TRUE?* (RECALL_FRESH / RECALL_STALE) — it re-probes
the memory's concrete claims (a SHA's ancestry, a code token's presence) against
ground truth at read time. That is necessary but not sufficient. A memory can be
perfectly TRUE and have stopped CONTRIBUTING — the lesson still holds but is never
the deciding factor, or a learned skill still runs but no longer moves the success
rate. An un-gated library that only ever ADDS (never retires) is the "Library
Drift" failure the 2026 literature measured: LLM-authored skills accumulated
WITHOUT outcome-driven retirement dropped agents BELOW their no-skill baseline
(arXiv 2605.19576). The named fix is exactly this leaf: **outcome-driven
retirement** (drop an item whose MEASURED contribution falls below a threshold)
plus a **bounded active-cap** (a library that grows without bound dilutes
retrieval, so cap it and retire the lowest contributor when over). Staleness asks
"is it true?"; retirement asks "does it still earn its place?" — orthogonal, and
both may run over the same item.

THE NON-FORGEABLE RETAIN-BIT — why this keeps the library honest
================================================================

The retain/retire bit is a pure function of facts authored by the ENVIRONMENT,
none by the item being judged:

  * `contribution` — the host's MEASURED net effect of the item (a success-rate
    delta with vs. without it, VERIFIED-uses minus harmful-uses, …). The
    environment measured it; the memory did not author it.
  * `trials`       — how many times the item was used / measured. A count, not a
    claim.
  * `active_count` — the library's current size, for the bounded-cap rung.

The item's OWN description — the memory body, the skill's self-summary of how
useful it is — is carried as `narrated` and **parsed for NOTHING** (the
`reward.admit` / `improve.classify` `narrated`-is-not-read discipline, the docs/138
invariant). A skill that writes "this skill is extremely valuable" into its own
description gains exactly zero retain-probability, because the claim is not in the
decision. The only path to KEEP is to actually move a metric the environment
measures — the same asymmetry that makes the loop keep-gate honest, applied to the
library.

THE WITNESS CEILING, STATED HONESTLY (docs/350 §3)
==================================================

An external verifier prevents a self-consuming loop from collapsing, BUT the
guarantee is only as good as the verifier: "gains plateau and may even reverse
unless the verifier is perfectly reliable" (arXiv 2510.16657). For retirement that
means: never RETIRE on thin evidence. Below `min_trials` measurements the verdict
is PROBATION — abstain, keep the item on trial, gather more — never a confident
RETIRE. The fail-to-abstain default: an unproven item is given the benefit of the
doubt, because a wrongly-retired good skill is an irreversible loss the next
measurement cannot undo, while a wrongly-kept weak skill is bounded by the cap and
caught on the next sweep. Under-coverage (PROBATION) is the safe failure
direction, exactly as ABSTAIN is for `reward.admit`.

RETIREMENT PROPOSES, IT NEVER DELETES (the docs/103 §6 discipline)
==================================================================

`classify` REPORTS KEEP / RETIRE / PROBATION; it removes no file, edits no store,
forgets nothing. The wired sweep (a driver over a read-only `MemoryStore`) turns a
RETIRE into a PROPOSAL surfaced for an operator (`dos decisions`), never an
autonomous delete — the same "STALE routes a proposal, never an edit" rule the
recall gate ships with. A library's retention is a human-reviewable protocol move,
not a silent agent purge.

**Mechanism is the kernel; which-metric is policy.** The kernel does not know what
"contribution" means — a success-rate delta, a token-saving, a defect-avoidance
count. The host names the metric in `dos.toml [retire]` and measures it; the kernel
only compares magnitudes and counts (the `improve` / `productivity` work-unit
split, applied to a library item over its lifetime).

No I/O, no clock, names no host: a pure `classify(evidence, policy)` leaf.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional


class Retire(str, enum.Enum):
    """The verdict for one remembered item (a memory, a learned skill).

    `str`-valued so it round-trips through a CLI stdout token / exit-code map
    without a lookup table (the `improve.Candidate` / `liveness.Liveness` idiom).
    Three outcomes — the whole decision space of "what do I do with this library
    item?":

      KEEP      — the item still EARNS ITS PLACE: it has enough measured trials AND
                  its measured contribution is at or above the threshold AND the
                  library is within its active cap (or the item is not the marginal
                  one over-cap). Leave it in the library. The library's `improve`
                  KEEP / `reward.admit` ACCEPT.
      RETIRE    — drop the item from the active library (a PROPOSAL, never an
                  autonomous delete). Either it UNDERPERFORMED (enough trials, but
                  the measured contribution fell below the threshold — the Library
                  Drift below-baseline item) or the library is OVER its active cap
                  and this is the marginal lowest-contribution member the cap
                  evicts.
      PROBATION — too few measured trials to judge (`trials < min_trials`). Keep
                  the item on trial and gather more evidence; NEVER retire on thin
                  evidence (the witness-ceiling honesty). The fail-to-abstain
                  default — the `reward.admit` ABSTAIN at library scale.
    """

    KEEP = "KEEP"
    RETIRE = "RETIRE"
    PROBATION = "PROBATION"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class RetireCause(str, enum.Enum):
    """Why a RETIRE — the underperformance miss vs the cap eviction, kept distinct.

    A RETIRE verdict is two genuinely different situations and an operator routes
    them differently, so the kernel names which one fired (the
    `improve.RevertCause` / `liveness` echo-the-evidence discipline):

      UNDERPERFORMED — enough trials, but the measured contribution fell below
                       `min_contribution`. The item's own merits no longer justify
                       its place — the Library Drift below-baseline skill. A
                       per-item judgement, independent of library size.
      OVER_CAP       — the item clears the contribution bar on its own, but the
                       library is OVER its `max_active` cap and this item is the
                       marginal lowest-contribution member the bounded cap must
                       evict to keep retrieval focused. A relative judgement: it is
                       retired not because it is bad but because the library is full
                       and others earn their place more.
    """

    UNDERPERFORMED = "underperformed"
    OVER_CAP = "over-cap"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class RetirePolicy:
    """The thresholds that gate a KEEP — policy, not mechanism.

    The same "mechanism is kernel, thresholds are config" split as `improve`'s
    `ImprovePolicy` and `breaker`'s maxima. The defaults are generic; a workspace
    declares its own in `dos.toml [retire]` (closed-config-as-data, like
    `[breaker]`).

      min_contribution — the measured-contribution floor an item must clear to
                   KEEP. An item with enough trials whose contribution is strictly
                   below this is RETIRE (UNDERPERFORMED). The unit is the host's
                   (a success-rate delta in basis points, a VERIFIED-minus-harmful
                   count, …); the kernel only compares magnitudes. Default 1 — an
                   item must have made at least SOME measured positive difference;
                   a zero-or-negative contributor does not earn its place. May be
                   negative if a host's metric is a signed delta and it wants to
                   tolerate slightly-negative items (rare).
      min_trials   — the trial floor below which the verdict is PROBATION, never
                   RETIRE — the witness-ceiling honesty (don't retire on thin
                   evidence). The Library Drift "Ratchet Recipe" measured
                   retirement after 100+ trials; the generic default here is a
                   conservative small floor a host raises for its own noise level.
                   Must be >= 1 (zero trials can never witness a contribution).
      max_active   — the bounded active-cap: the largest active library the host
                   wants. When `active_count` exceeds this AND the item is the
                   marginal lowest-contribution member (the caller's
                   `is_marginal` flag), the item is RETIRE (OVER_CAP) even if it
                   clears `min_contribution` on its own. 0 = DISABLED (no cap; the
                   OVER_CAP cause never fires and library size never forces a
                   retire — the conservative default for a host that has not sized
                   its library).
    """

    min_contribution: int = 1
    min_trials: int = 5
    max_active: int = 0

    def __post_init__(self) -> None:
        if self.min_trials < 1:
            raise ValueError(
                "min_trials must be >= 1 (zero trials can never witness a "
                "contribution, so a zero floor would retire un-measured items)"
            )
        if self.max_active < 0:
            raise ValueError("max_active must be non-negative (0 = the cap is disabled)")


DEFAULT_POLICY = RetirePolicy()


@dataclass(frozen=True)
class RetireEvidence:
    """The facts `classify()` reads — gathered by the CALLER at its I/O boundary.

    No store read, no clock inside the verdict (the arbiter rule): the driver
    measures the contribution, counts the trials, reads the library size, then
    freezes the RESULTS here. Every field is env-authored — the docs/138 invariant
    that makes the retain-bit non-forgeable:

      contribution — the env-MEASURED net effect of THIS item (the host's unit: a
                     success-rate delta, VERIFIED-uses minus harmful-uses, …). The
                     environment measured it across the item's uses; the item did
                     not author it. May be negative (a net-harmful item).
      trials       — how many times the item was used / measured (a count, not a
                     claim). Non-negative. Below `policy.min_trials` ⇒ PROBATION.
      active_count — the library's current active size, for the bounded-cap rung.
                     Non-negative. Read it the same way for every item in a sweep.
      is_marginal  — True iff, in the caller's ranking of the active library by
                     contribution, THIS item is the marginal lowest-contribution
                     member that an over-cap eviction would drop. Only meaningful
                     when `active_count > policy.max_active`; the caller computes
                     the ranking (the kernel judges one item at a time and cannot
                     see the others). Default False — an item the caller has not
                     marked marginal is never OVER_CAP-retired, so a caller that
                     does not rank is byte-identically un-capped.

    `narrated` is the item's OWN description of how useful it is — carried for the
    operator surface (`dos retire --json` echoes it) and **parsed for NOTHING**. It
    is the agent-authored, forgeable channel; by construction it touches no part of
    the verdict (the `reward.admit` / `improve.classify` `narrated`-is-not-read
    discipline). It exists so the operator can read what the item CLAIMS for itself
    next to the contribution the environment actually measured.
    """

    contribution: int
    trials: int = 0
    active_count: int = 0
    is_marginal: bool = False
    narrated: str = ""

    def __post_init__(self) -> None:
        if self.trials < 0:
            raise ValueError("trials must be non-negative (a count of uses/measurements)")
        if self.active_count < 0:
            raise ValueError("active_count must be non-negative (a library size)")


@dataclass(frozen=True)
class RetireVerdict:
    """The single verdict `classify()` returns, with the facts echoed back.

    `verdict` is the typed `Retire`. `retire_cause` is set ONLY on a RETIRE (which
    of the two situations fired — UNDERPERFORMED / OVER_CAP), None otherwise.
    `reason` is the one-line operator-facing summary. `evidence` is echoed so
    `dos retire --json` emits the verdict AND the facts behind it in one object (the
    legible-distrust renderer seam — the operator sees not just RETIRE but *why*:
    contribution 0 over 30 trials, or over-cap at 51/50).
    """

    verdict: Retire
    reason: str
    evidence: RetireEvidence
    retire_cause: Optional[RetireCause] = None

    @property
    def is_keep(self) -> bool:
        return self.verdict is Retire.KEEP

    def to_dict(self) -> dict:
        e = self.evidence
        out = {
            "verdict": self.verdict.value,
            "retire_cause": self.retire_cause.value if self.retire_cause else None,
            "reason": self.reason,
            "evidence": {
                "contribution": e.contribution,
                "trials": e.trials,
                "active_count": e.active_count,
                "is_marginal": e.is_marginal,
                "narrated": e.narrated,
            },
        }
        return out


def classify(
    evidence: RetireEvidence, policy: RetirePolicy = DEFAULT_POLICY
) -> RetireVerdict:
    """Decide KEEP / RETIRE / PROBATION for one library item. PURE — no I/O.

    Reads the ladder top to bottom (this function IS the answer to "when does an
    item stay in the library?"):

      1. PROBATION (thin evidence) — `trials < min_trials`. Too few measured uses to
         judge the contribution; abstain and keep the item on trial. Checked FIRST,
         before any contribution or cap rung — the witness-ceiling floor: we never
         RETIRE on evidence too thin to witness, because a wrongly-retired good
         skill is an irreversible loss. The `reward.admit` ABSTAIN at library scale.

      2. RETIRE (underperformed) — enough trials AND `contribution < min_contribution`.
         The item's own measured merits no longer justify its place: the Library
         Drift below-baseline item. A per-item judgement, checked before the cap
         rung so an underperformer is retired for its OWN failure (the specific
         cause the operator wants), not merely as cap collateral.

      3. RETIRE (over-cap) — enough trials AND it clears `min_contribution` on its
         own, BUT the library is over `max_active` AND this is the caller-ranked
         marginal lowest-contribution member. The bounded active-cap eviction: a
         library that grows without bound dilutes retrieval, so the cap drops the
         least-earning member even though it is individually fine. Only reachable
         when the host arms a cap (`max_active > 0`) and ranks the item marginal.

      4. KEEP (still earns its place) — enough trials AND contribution at or above
         the threshold AND (no cap, or not the marginal over-cap member). The item
         is witnessed to still earn its place by bytes it did not author. The
         library's `improve` KEEP.
    """
    e = evidence

    # 1. PROBATION (thin evidence) — the witness-ceiling floor. Below the trial
    #    floor we cannot witness a contribution, so we never RETIRE; keep on trial.
    if e.trials < policy.min_trials:
        return RetireVerdict(
            verdict=Retire.PROBATION,
            reason=(
                f"only {e.trials} measured trial(s) (< {policy.min_trials}) — too "
                f"little evidence to judge; keep the item on PROBATION and gather "
                f"more rather than retire on a thin witness"
            ),
            evidence=e,
        )

    # 2. RETIRE (underperformed) — the per-item floor. Enough trials, but the
    #    measured contribution fell below the bar: the item no longer earns its
    #    place on its own merits (the Library Drift below-baseline skill).
    if e.contribution < policy.min_contribution:
        return RetireVerdict(
            verdict=Retire.RETIRE,
            retire_cause=RetireCause.UNDERPERFORMED,
            reason=(
                f"measured contribution {e.contribution} over {e.trials} trials is "
                f"below the floor {policy.min_contribution} — the item no longer "
                f"earns its place; propose RETIRE (a human reviews the proposal, "
                f"the kernel never deletes)"
            ),
            evidence=e,
        )

    # 3. RETIRE (over-cap) — the bounded active-cap eviction. The item clears the
    #    contribution bar on its own, but the library is over capacity and this is
    #    the caller-ranked marginal lowest-contribution member. Only when a cap is
    #    armed and the caller ranked this item marginal.
    if policy.max_active > 0 and e.active_count > policy.max_active and e.is_marginal:
        return RetireVerdict(
            verdict=Retire.RETIRE,
            retire_cause=RetireCause.OVER_CAP,
            reason=(
                f"the active library holds {e.active_count} items (> the cap "
                f"{policy.max_active}) and this is the marginal lowest-contribution "
                f"member — propose RETIRE to keep retrieval focused (it clears the "
                f"contribution floor on its own; it is evicted by the cap, not its "
                f"merits)"
            ),
            evidence=e,
        )

    # 4. KEEP — enough trials, clears the floor, and within the cap (or not the
    #    marginal member). The item is witnessed to still earn its place.
    return RetireVerdict(
        verdict=Retire.KEEP,
        reason=(
            f"contribution {e.contribution} over {e.trials} trials clears the floor "
            f"{policy.min_contribution}"
            + (
                f" and the library is within its cap {policy.max_active}"
                if policy.max_active > 0
                else " (no active cap armed)"
            )
            + " — the item still earns its place; KEEP it"
        ),
        evidence=e,
    )
