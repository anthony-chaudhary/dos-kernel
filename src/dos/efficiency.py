"""EFF — the token-effectiveness verdict: *did the tokens this run spent buy work?*

The **token-economics completion of `productivity()`**. The kernel already has two
loop-economics verdicts and a clean gap between them:

    liveness.classify      (ProgressEvidence, policy)   -> did state move AT ALL?      (a binary, lifetime count)
    productivity.classify  (WorkHistory, policy)        -> is the work-per-step RATE fading?  (a trend over steps)
    efficiency.classify    (EfficiencyEvidence, policy) -> did the tokens buy WORK?     (a ratio: work per token)
                           ^ THIS module

`liveness` reads a single since-start count; `productivity` reads a *trend* of
per-step work deltas; neither relates the work to its **price**. A run can be
ADVANCING (it committed) AND PRODUCTIVE (each step lands work) and still be
spending ten times the tokens that work was worth — the gap between *did it do
work?* and *was the work worth what it cost?* That gap is **efficiency**, and it
is the question an operator means by "token effectiveness": not "is the run
moving" but "is the run **spending well**." EFF is `productivity`'s lateral
sibling, re-aimed from a trend over time onto a **ratio**: `work / tokens`.

**Byte-clean by construction (the docs/138 invariant).** Both inputs are counts
the *runtime / environment* authors, never the judged agent's narration:

  * `tokens` — what the model API billed this run (the usage record the provider
    returns), the same env-authored counter `liveness.tokens_spent_since` reads.
  * `work` — a count of ground-truth work the environment measured: commits
    landed, bytes diffed, tests passed — the same `productivity` work-unit, the
    same kind of thing `verify` confirms off git. Whatever unit the host chooses.

So WASTEFUL is "the environment recorded N tokens spent and ~0 work landed,"
never "the agent says it was inefficient." A quantity, not a self-report — and
crucially **non-forgeable in the direction that matters**: an agent cannot move
the verdict toward EFFICIENT by *narrating* productivity, because the numerator
is work the environment witnessed (a commit the git machinery authored, a test
the runner authored), not a claim the agent emitted. This is the same reason
`reward.admit` trusts the read-back and not the answer text: the bytes that move
the verdict are bytes the claimant did not write.

**EFF reports a price, never a quality.** Like `productivity` says the *rate*
fell (never that the work was *wrong*), EFF says the *cost per unit of work* is
high — it never says the work was bad. A run can be perfectly correct and
WASTEFUL (it burned tokens deliberating, re-reading, marker-spinning); it can be
EFFICIENT and wrong (cheap garbage). Quality is an advisory judge's call
(`llm_judge`), never this deterministic verb — the distrust-state / distrust-
judgment line the whole temporal-verdict family draws.

**Withhold the accusation until there is enough spend to judge.** The whole
reason EFF has a `min_tokens` floor is the `productivity.min_steps` reason: a run
that has barely started has spent too little to have an honest ratio (3 tokens
and 0 work is not a wasteful run, it is a run that has not done anything yet).
Below the floor EFF returns EFFICIENT-benign ("not enough spend to judge") — the
young-and-alive guard, lateral. The accusation (COSTLY / WASTEFUL) fires only
once the run has spent enough that a low ratio is real signal.

**No-telemetry / no-plan discipline** (the `test_verify_no_plan` sibling, the
strongest of the verdict family alongside `productivity`): EFF needs *nothing*
but the two counts the caller already has. No git, no registry, no journal, no
clock — `classify()` makes no I/O at all (EFF is timeless, like `productivity`;
it reads two numbers, not ages). A caller with a work count and a token count
gets a verdict; a caller with too few tokens gets the honest "not enough spend to
judge" (EFFICIENT-benign).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from dos.spend import SpendBreakdown


class Efficiency(str, enum.Enum):
    """The typed token-effectiveness verdict — three states, mutually exclusive.

    `str`-valued so it round-trips through a CLI stdout token / exit-code map
    without a lookup table (mirrors `productivity.Productivity` and
    `liveness.Liveness`).
    """

    EFFICIENT = "EFFICIENT"  # work-per-token at/above the floor (or too little spend to judge)
    COSTLY = "COSTLY"        # nonzero work, but the ratio is under the floor — spending a lot per unit
    WASTEFUL = "WASTEFUL"    # meaningful tokens spent, ~0 work landed — the tokens bought nothing

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class EfficiencyPolicy:
    """The thresholds that separate EFFICIENT / COSTLY / WASTEFUL — policy, not mechanism.

    The same "mechanism is kernel, thresholds are config" split as
    `productivity`'s `min_steps`/`floor` and `liveness`'s windows. The defaults
    are GENERIC; a workspace declares its own in `dos.toml [efficiency]` (the
    closed-config-as-data pattern, the forward-looking seam `productivity` also
    documents).

      min_tokens — the **minimum tokens spent** before EFF will accuse a run of
                   being COSTLY / WASTEFUL. Below it the run has spent too little
                   to have an honest ratio (a handful of tokens and no work is a
                   run that has barely started, not a wasteful one), and the
                   verdict withholds the accusation. The token analogue of
                   `productivity.min_steps` — the `liveness.grace_ms` guard,
                   measured in spend instead of steps or time.
      floor      — the **work-per-token efficiency floor**: the minimum ratio
                   `work / tokens` a run must clear to be EFFICIENT. Below it (but
                   with nonzero work) the run is COSTLY — it is doing work, but
                   paying a lot per unit. The UNIT of `work` is the host's
                   (commits, changed bytes, passed tests); the kernel only
                   compares the ratio to the floor. A float, because work-per-
                   token is normally « 1 (one commit might be tens of thousands of
                   tokens → a floor like 0.00002 commits/token, or — far more
                   legibly — the host counts work in a coarser unit so the floor
                   is a readable number).

    Defaults: `min_tokens=1000` (a run that has spent under ~1k tokens has barely
    started — too little to judge), `floor=0.0` (DISABLED by default — see below).

    **Why the default floor is 0.0 (disabled), not a guessed number.** Unlike
    `productivity`, which could lift a real constant from Claude Code's own loop
    (`tokenBudget.ts`'s 500-token diminishing threshold), there is no universal
    "good" work-per-token ratio — it depends entirely on what the host counts as a
    work unit (a ratio sensible for "changed bytes" is meaningless for "commits").
    Shipping a guessed floor would manufacture COSTLY verdicts out of a unit
    mismatch (the docs/235 slice-must-have-power lesson: a threshold that fires for
    the wrong reason is worse than none). So the default floor is 0.0 — every
    nonzero-work run is EFFICIENT until the host declares a floor that means
    something for *its* unit. The one verdict EFF always gives for free, no floor
    needed, is **WASTEFUL** (zero work for meaningful spend), because "tokens
    bought literally nothing" is unit-independent: 0 work is 0 work whatever the
    unit. That is the cost-free, always-correct half of the verdict; COSTLY is the
    opt-in half a host arms by setting a floor.
    """

    min_tokens: int = 1000   # below this spend, withhold the accusation (the productivity.min_steps analogue)
    floor: float = 0.0       # work-per-token floor; 0.0 = disabled (only WASTEFUL fires) — see docstring

    def __post_init__(self) -> None:
        if self.min_tokens < 0:
            raise ValueError("min_tokens must be non-negative")
        if self.floor < 0:
            raise ValueError("the work-per-token floor must be non-negative")


DEFAULT_POLICY = EfficiencyPolicy()


@dataclass(frozen=True)
class EfficiencyEvidence:
    """The two counts `classify()` reads — gathered by the CALLER at its boundary.

    No clock, no I/O inside the verdict — the arbiter rule, sharpened the way
    `productivity` sharpens it: there is not even a clock rung (EFF is *timeless*;
    it reads two numbers, never an age). The caller's boundary (the `dos
    efficiency` evidence-gather, or a loop reading the provider usage record + its
    own git delta) measures the work and the spend and freezes them here.

      work   — the count of ground-truth **work units** the environment measured
               for this run (commits landed, bytes diffed, tests passed — the
               host's unit, the same one `productivity` counts). Non-negative: a
               run that *removed* work still did the work of removing it (the host
               passes the magnitude, never a signed regression), and a run that
               landed nothing passes 0.
      tokens — the count of **tokens** the run spent (the provider usage record),
               the env-authored price. Non-negative. Zero tokens is the degenerate
               "no spend yet" case (a ratio is undefined) — handled as
               EFFICIENT-benign, never a divide-by-zero.
      breakdown — OPTIONAL (docs/300): the typed five-way split of that same
               spend (`dos.spend.SpendBreakdown` — input / output / cache_read /
               cache_creation / reasoning). Pure legibility: the verdict ladder
               still rides the scalar `tokens`, so a richer breakdown can never
               flip a verdict — it only lets the JSON say *what kind* of spend
               was behind it (cache-hit ratio, decode share, reasoning share).
               When given with `tokens=0` the scalar derives from
               `breakdown.total`; when both are given they must AGREE — a
               mismatched pair is a contract error, refused loudly, never
               silently reconciled (the double-count discipline).

    Both are env-authored (the docs/138 invariant): `work` is what git/the test
    runner witnessed, `tokens` is what the API billed — neither is the agent's
    "I was efficient" narration. The ratio `work / tokens` is the run's
    efficiency; the verdict compares it to the policy floor.
    """

    work: int = 0
    tokens: int = 0
    breakdown: Optional[SpendBreakdown] = None

    def __post_init__(self) -> None:
        if self.work < 0:
            raise ValueError("work must be non-negative (a count of work done)")
        if self.tokens < 0:
            raise ValueError("tokens must be non-negative (a count of tokens spent)")
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
    def ratio(self) -> float:
        """Work per token spent — the efficiency. 0.0 when no tokens were spent
        (the degenerate no-spend case; the verdict treats it as benign, never a
        divide-by-zero)."""
        if self.tokens <= 0:
            return 0.0
        return self.work / self.tokens

    @classmethod
    def of(
        cls, work: int, tokens: int, breakdown: Optional[SpendBreakdown] = None
    ) -> "EfficiencyEvidence":
        """Build evidence from a work count and a token count (and, optionally,
        the typed spend breakdown behind that count — docs/300)."""
        return cls(work=work, tokens=tokens, breakdown=breakdown)


@dataclass(frozen=True)
class EfficiencyVerdict:
    """The single verdict `classify()` returns, with the facts echoed back.

    `verdict` is the typed `Efficiency`. `reason` is a one-line operator-facing
    summary (the tally-row string). `evidence` is the `EfficiencyEvidence` that
    drove the call, carried so `dos efficiency --json` can emit the verdict *and
    the facts behind it* in one object (the legible-distrust renderer seam): the
    operator sees not just WASTEFUL but *why* (80,000 tokens spent, 0 work landed),
    and not just COSTLY but the ratio and the floor it fell under. `to_dict` is the
    json shape.
    """

    verdict: Efficiency
    reason: str
    evidence: EfficiencyEvidence

    def to_dict(self) -> dict:
        e = self.evidence
        evidence: dict = {
            "work": e.work,
            "tokens": e.tokens,
            "ratio": e.ratio,
        }
        # docs/300 — the optional spend diagnostics. Omitted entirely when the
        # caller supplied only the scalar, so the pre-breakdown JSON shape stays
        # byte-identical for every existing consumer.
        if e.breakdown is not None:
            evidence["breakdown"] = e.breakdown.to_dict()
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "evidence": evidence,
        }


def classify(
    evidence: EfficiencyEvidence, policy: EfficiencyPolicy = DEFAULT_POLICY
) -> EfficiencyVerdict:
    """Classify a run's token effectiveness from its work and its spend. PURE — no I/O.

    Reads the ladder top to bottom (this function IS the answer to "did the tokens
    buy work?"):

      1. EFFICIENT (too little spend) — fewer than `min_tokens` tokens spent (or
         zero): the run has barely started; there is not enough spend to have an
         honest ratio, so withhold the COSTLY/WASTEFUL accusation (the
         `productivity` young-and-alive guard, lateral). Checked FIRST so a
         just-launched run with one token and no commit is never mislabelled
         WASTEFUL on a spend technicality.
      2. WASTEFUL — meaningful tokens spent (`tokens >= min_tokens`) AND zero work
         landed (`work == 0`): the tokens bought nothing — the degenerate floor of
         inefficiency, the unit-independent half of the verdict (0 work is 0 work
         whatever the unit, so this fires with NO floor needed). Named distinctly
         from COSTLY (a fading-but-nonzero ratio) because zero is the operator's
         clearest "the spend was pure overhead" signal — the marker-storm /
         spin-without-shipping rung. Checked before COSTLY so an exact zero is
         named precisely.
      3. COSTLY — meaningful spend AND nonzero work AND the ratio under `floor`:
         the run is doing work but paying a lot per unit (fading efficiency, but
         not pure waste). The opt-in half of the verdict — fires only when the host
         has armed a `floor` that means something for its work unit (with the
         default `floor=0.0` this rung never fires; every nonzero-work run is
         EFFICIENT). The efficiency analogue of `productivity.DIMINISHING`.
      4. EFFICIENT — none of the above: the ratio is at/above the floor (or the
         floor is disabled and work is nonzero). The tokens bought their work.

    The COSTLY test uses `>` on the floor (ratio strictly under floor is costly),
    so a ratio exactly AT the floor is EFFICIENT — the floor is the minimum
    acceptable efficiency, inclusive. With the default `floor=0.0`, no nonzero-work
    ratio is under it, so only WASTEFUL ever fires without an explicit floor.
    """
    tokens = evidence.tokens
    work = evidence.work

    # 1. EFFICIENT (too little spend) — not enough tokens spent to judge a ratio.
    #    Withhold the COSTLY/WASTEFUL accusation; report the benign verdict. A run
    #    that has spent nothing at all also lands here (no spend, no problem yet).
    if tokens < policy.min_tokens or tokens == 0:
        return EfficiencyVerdict(
            verdict=Efficiency.EFFICIENT,
            reason=(
                f"{tokens} token(s) spent (< min {policy.min_tokens}) — not enough "
                f"spend to judge token effectiveness; no efficiency problem yet"
            ),
            evidence=evidence,
        )

    # 2. WASTEFUL — meaningful spend bought ZERO work. The pure-overhead rung, named
    #    distinctly from a merely-low ratio so the operator's clearest signal
    #    ("the tokens bought nothing") is not blurred into COSTLY. Unit-independent:
    #    fires with no floor, because 0 work is 0 work whatever the host's unit.
    if work == 0:
        return EfficiencyVerdict(
            verdict=Efficiency.WASTEFUL,
            reason=(
                f"{tokens} tokens spent and 0 work units landed — the spend bought "
                f"nothing (pure overhead)"
            ),
            evidence=evidence,
        )

    ratio = evidence.ratio

    # 3. COSTLY — a low-but-nonzero efficiency: the run is doing work but paying a
    #    lot per unit. The opt-in half — fires only when the host armed a floor that
    #    means something for its work unit. With the default floor=0.0 this never
    #    fires (no nonzero ratio is < 0.0). The productivity.DIMINISHING analogue.
    if ratio < policy.floor:
        return EfficiencyVerdict(
            verdict=Efficiency.COSTLY,
            reason=(
                f"{work} work units for {tokens} tokens — {ratio:.6g} work/token, "
                f"under the {policy.floor:.6g} floor (doing work, but spending a lot "
                f"per unit)"
            ),
            evidence=evidence,
        )

    # 4. EFFICIENT — the ratio cleared the floor (or the floor is disabled and work
    #    is nonzero). The tokens bought their work.
    return EfficiencyVerdict(
        verdict=Efficiency.EFFICIENT,
        reason=(
            f"{work} work units for {tokens} tokens — {ratio:.6g} work/token "
            f"(at/above the {policy.floor:.6g} floor) — the spend bought its work"
        ),
        evidence=evidence,
    )
