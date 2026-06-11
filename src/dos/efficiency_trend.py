"""TRD — the efficiency trend: *is this loop's work-per-token fading across runs?*

docs/300 — the **trend completion of the efficiency family**. The family now
reads three shapes of the same distrust:

    productivity.classify     (WorkHistory, policy)   -> is work-per-STEP fading?   (a trend, within one run)
    efficiency.classify       (EfficiencyEvidence, …) -> did the tokens buy work?   (a ratio, one run)
    efficiency_trend.classify (TrendHistory, policy)  -> is work-per-token fading?  (a trend, ACROSS runs)
                              ^ THIS module

`efficiency` prices one run; it cannot see direction. A self-improving loop (or
a fleet lane) needs the cross-run question: *is each successive run buying less
work per token than the runs before it?* That is the question behind the 2026
practice of token-cost regression gates — a change-set is suspect when the spend
per unit of work drifts up against the trailing baseline. This leaf is that
read, as a pure kernel verdict: deterministic, advisory, no statistics theater
(a formal significance rung is a named follow-up, not smuggled in).

**Byte-clean (docs/138).** A sample is the same two env-authored counts
`efficiency` reads — work the environment witnessed, tokens the provider
billed — frozen per run by whatever recorded them (the natural source is the
verdict journal, where an `--observe`d `dos efficiency` call fossilizes both).
A loop cannot narrate its trend; it can only actually buy more work per token.

**The ladder mirrors `productivity`'s discipline.** Withhold the accusation
until there is enough history (`min_samples`); accuse only on a SUSTAINED
signal (the last TWO runs both outside the band, so one outlier run cannot
false-trip); compare against the MEDIAN of the prior runs (robust to one
historical outlier, and deterministic — no clock, no I/O, timeless).

**Advisory.** DEGRADING reports; it never kills a loop or refuses a lease. The
natural consumers: an operator reading `dos efficiency-trend --from-journal`, a
self-improving loop checking its own economics between cycles, and (future) a
`loop_decide` stop rung.
"""

from __future__ import annotations

import enum
import statistics
from dataclasses import dataclass
from typing import Sequence, Tuple


class EfficiencyTrend(str, enum.Enum):
    """The typed cross-run trend verdict — three states, mutually exclusive.

    `str`-valued so it round-trips through a CLI stdout token / exit-code map
    without a lookup table (mirrors `efficiency.Efficiency` and
    `productivity.Productivity`).
    """

    IMPROVING = "IMPROVING"  # the last two runs both cleared the prior-median band upward
    STEADY = "STEADY"        # inside the band (or too little history to judge)
    DEGRADING = "DEGRADING"  # the last two runs both fell under the band — fading effectiveness

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class TrendPolicy:
    """The thresholds that separate IMPROVING / STEADY / DEGRADING — policy, not mechanism.

      min_samples — the minimum number of runs before the trend will accuse (or
                    praise). Below it there is not enough history for a
                    cross-run direction (two runs are a comparison, not a
                    trend). Floored at 3 in `classify` regardless (two recent
                    runs + at least one baseline run is the smallest readable
                    shape) — the `productivity.min_steps` analogue.
      tolerance  — the fractional band around the prior-median ratio. A run
                    counts as DEGRADING-side only when its ratio is more than
                    `tolerance` BELOW the median of the runs before the last
                    two, and IMPROVING-side only when more than `tolerance`
                    ABOVE it. Defaults to 0.25 (a quarter off the baseline) — a
                    deliberately wide, generic band: the kernel would rather
                    miss a mild drift than manufacture an accusation out of
                    normal run-to-run noise (the docs/263 disabled-floor
                    instinct, applied to a band). A workspace tightens it to
                    taste; a real statistical gate is the named follow-up.

    Both must be non-negative.
    """

    min_samples: int = 3
    tolerance: float = 0.25

    def __post_init__(self) -> None:
        if self.min_samples < 0:
            raise ValueError("min_samples must be non-negative")
        if self.tolerance < 0:
            raise ValueError("tolerance must be non-negative")


DEFAULT_POLICY = TrendPolicy()


@dataclass(frozen=True)
class TrendSample:
    """One run's (work, tokens) pair — the same two counts `efficiency` reads.

    Non-negative, env-authored. `ratio` mirrors `EfficiencyEvidence.ratio`:
    work per token, 0.0 on the degenerate no-spend run (never a divide-by-zero).
    """

    work: int = 0
    tokens: int = 0

    def __post_init__(self) -> None:
        if self.work < 0:
            raise ValueError("work must be non-negative (a count of work done)")
        if self.tokens < 0:
            raise ValueError("tokens must be non-negative (a count of tokens spent)")

    @property
    def ratio(self) -> float:
        if self.tokens <= 0:
            return 0.0
        return self.work / self.tokens


@dataclass(frozen=True)
class TrendHistory:
    """The ordered cross-run samples `classify()` reads — OLDEST first.

    Gathered by the CALLER at its boundary (the `dos efficiency-trend`
    evidence-gather: an explicit `--samples` list, or the verdict journal's
    fossilized efficiency evidence). No clock, no I/O inside the verdict.
    """

    samples: Tuple[TrendSample, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.samples, tuple):
            object.__setattr__(self, "samples", tuple(self.samples))

    @property
    def run_count(self) -> int:
        return len(self.samples)

    @property
    def ratios(self) -> Tuple[float, ...]:
        return tuple(s.ratio for s in self.samples)

    @classmethod
    def of(cls, pairs: Sequence[Tuple[int, int]]) -> "TrendHistory":
        """Build a history from ordered (work, tokens) pairs, oldest first."""
        return cls(tuple(TrendSample(work=w, tokens=t) for (w, t) in pairs))


@dataclass(frozen=True)
class TrendVerdict:
    """The single verdict `classify()` returns, with the trend echoed back.

    `history` is carried so `--json` can emit the verdict *and the facts behind
    it* (the legible-distrust renderer seam): the operator sees not just
    DEGRADING but the falling ratio sequence, the prior-median baseline, and
    the band it fell out of.
    """

    verdict: EfficiencyTrend
    reason: str
    history: TrendHistory
    baseline: float = 0.0  # the prior-median ratio the last two runs were judged against (0.0 when unjudged)

    def to_dict(self) -> dict:
        h = self.history
        ratios = h.ratios
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "history": {
                "samples": [
                    {"work": s.work, "tokens": s.tokens, "ratio": s.ratio}
                    for s in h.samples
                ],
                "run_count": h.run_count,
                "last_ratio": ratios[-1] if ratios else None,
                "prior_ratio": ratios[-2] if len(ratios) >= 2 else None,
                "baseline": self.baseline,
            },
        }


def classify(
    history: TrendHistory, policy: TrendPolicy = DEFAULT_POLICY
) -> TrendVerdict:
    """Classify a loop's cross-run token effectiveness. PURE — no I/O.

    Reads the ladder top to bottom (this function IS the answer to "is the
    work-per-token fading across runs?"):

      1. STEADY (too little history) — fewer than `min_samples` runs (and never
         fewer than 3: two recent runs + one baseline run is the smallest
         readable shape). Withhold the judgment; report the benign verdict.
      2. DEGRADING — the last TWO ratios are BOTH more than `tolerance` below
         the median of every run before them (a sustained fall against a robust
         baseline — one bad run cannot trip it, one historical outlier cannot
         poison the baseline).
      3. IMPROVING — the last TWO ratios are BOTH more than `tolerance` above
         that same prior-median (sustained, symmetric).
      4. STEADY — inside the band: normal run-to-run noise.

    The baseline is `median(ratios[:-2])` — strictly PRIOR runs, so the runs
    being judged never sit inside their own yardstick. With a zero baseline
    (all prior runs landed nothing) any sustained nonzero ratio reads as
    IMPROVING — from nothing to something is improvement, honestly.
    """
    n = history.run_count
    floor_n = max(policy.min_samples, 3)

    # 1. STEADY (too little history) — not enough runs to judge a direction.
    if n < floor_n:
        return TrendVerdict(
            verdict=EfficiencyTrend.STEADY,
            reason=(
                f"{n} run(s) so far (< min {floor_n}) — not enough history to "
                f"judge a cross-run trend; no direction yet"
            ),
            history=history,
        )

    ratios = history.ratios
    last, prior = ratios[-1], ratios[-2]
    baseline = statistics.median(ratios[:-2])
    low = baseline * (1.0 - policy.tolerance)
    high = baseline * (1.0 + policy.tolerance)

    # 2. DEGRADING — a SUSTAINED fall: the last two runs both under the band.
    if last < low and prior < low:
        return TrendVerdict(
            verdict=EfficiencyTrend.DEGRADING,
            reason=(
                f"the last two of {n} runs landed {prior:.6g} then {last:.6g} "
                f"work/token, both more than {policy.tolerance:.0%} under the "
                f"{baseline:.6g} prior-median — token effectiveness is fading "
                f"across runs"
            ),
            history=history,
            baseline=baseline,
        )

    # 3. IMPROVING — a sustained rise, judged symmetrically.
    if last > high and prior > high:
        return TrendVerdict(
            verdict=EfficiencyTrend.IMPROVING,
            reason=(
                f"the last two of {n} runs landed {prior:.6g} then {last:.6g} "
                f"work/token, both more than {policy.tolerance:.0%} above the "
                f"{baseline:.6g} prior-median — token effectiveness is improving "
                f"across runs"
            ),
            history=history,
            baseline=baseline,
        )

    # 4. STEADY — inside the band; normal run-to-run noise.
    return TrendVerdict(
        verdict=EfficiencyTrend.STEADY,
        reason=(
            f"last two of {n} runs landed {prior:.6g} then {last:.6g} work/token "
            f"against a {baseline:.6g} prior-median (±{policy.tolerance:.0%} band) "
            f"— steady"
        ),
        history=history,
        baseline=baseline,
    )
