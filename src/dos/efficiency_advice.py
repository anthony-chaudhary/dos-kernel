"""ADV — the efficiency advisor: the recommender capstone of the loop-economics family.

`efficiency.classify` answers ONE question — *did the tokens buy work?* — from one
measured ratio. ADV is its **second-order** sibling: it folds a BUNDLE of already-
measured, env-authored signals — the provider spend split (`dos.spend.SpendBreakdown`),
the work count, the no-op streak, the over-claim count, the cross-run trend verdict,
the serving-window count — and emits a RANKED list of typed, vendor-free
recommendations plus a single CLEAN / ADVISE / WASTE rollup. It is the only reader
that ADJUDICATES the spend KPIs `dos efficiency --usage-json` computes but never judges.

**Same disciplines as the rest of the family.**

  * **Byte-clean (docs/138).** Every signal is a count the *environment* authored —
    the API's usage record, git's commit count, the kernel's own no-op / over-claim
    tallies — never the judged agent's narration. An agent cannot move an advice
    toward CLEAN by *saying* it was efficient; the numbers behind every rung are
    bytes it did not write.
  * **No I/O at all.** `recommend` is a pure fold over the signals — no clock, no
    file, no git (`test_recommend_is_pure`). The caller's boundary freezes the
    counts; ADV only ranks them.
  * **Withhold until there is enough spend to judge.** The spend-shaped rungs
    (WASTEFUL_SPEND and the tuned ratio rungs) stay silent below `min_tokens` — the
    `efficiency.min_tokens` young-and-alive guard, lateral: a handful of tokens and
    no work is a run that has barely started, not waste.
  * **Two tiers, the structural rungs free and the tuned rungs opt-in.** The
    structural-waste rungs (zero-work spend, no-op spin, a degrading trend, an
    over-claim, idle serving windows) are always-correct and ARMED by default — they
    fire on a fact, not a tuned threshold. The ratio-shape rungs (cold cache,
    overthinking, decode-heavy, costly ratio) are DISABLED until a host arms a floor,
    the same disabled-by-default discipline `efficiency.floor` takes: there is no
    universal "good" cache-hit / reasoning ratio, so shipping a guessed one would
    manufacture verdicts out of a unit mismatch.

**ADV recommends; it never adjudicates quality.** Like `efficiency` reports a price
and never says the work was *wrong*, every rung here names a *waste shape* and the
action that addresses it — never whether the output was good. Quality is an advisory
judge's call, never this deterministic verb.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from dos.spend import SpendBreakdown


# ---------------------------------------------------------------------------
# The typed vocabulary — verdicts, severities, recommendation kinds.
# ---------------------------------------------------------------------------


class Advice(str, enum.Enum):
    """The rolled-up advisory verdict — three states, mutually exclusive.

    `str`-valued so it round-trips through a CLI stdout token / exit-code map
    without a lookup table (the `efficiency.Efficiency` idiom).
    """

    CLEAN = "CLEAN"    # no waste signal crossed a floor — the run is spending well
    ADVISE = "ADVISE"  # advisory signals fired, but none critical — worth a look
    WASTE = "WASTE"    # a CRITICAL waste signal fired — the spend is buying ~nothing

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Severity(str, enum.Enum):
    """How loudly a recommendation speaks — the ranking key (CRITICAL first)."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# CRITICAL sorts first; ties keep catalogue (enum) order via a stable sort.
_SEVERITY_RANK = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
}


class RecommendationKind(str, enum.Enum):
    """The closed catalogue of waste shapes ADV can name.

    Declaration order IS the catalogue order: within one severity the ranking keeps
    this order (`test_ranking_is_critical_first_then_catalogue_order`). The five
    structural rungs come first (armed by default), the four tuned ratio rungs after
    (disabled until a host arms a floor).
    """

    # --- structural waste rungs (armed by default, always-correct) ---
    WASTEFUL_SPEND = "WASTEFUL_SPEND"        # meaningful tokens, 0 work landed
    NOOP_SPIN = "NOOP_SPIN"                  # a streak of no-op turns at/over budget
    DEGRADING_TREND = "DEGRADING_TREND"      # work-per-token falling across runs
    OVERCLAIM = "OVERCLAIM"                  # claimed ships the truth syscall didn't confirm
    SEAT_UNDERUTILIZED = "SEAT_UNDERUTILIZED"  # idle serving windows — throughput left over
    # --- tuned ratio rungs (DISABLED by default, armed via policy) ---
    COLD_CACHE = "COLD_CACHE"                # cache-hit ratio under the floor
    OVERTHINKING = "OVERTHINKING"            # reasoning share over the ceiling
    DECODE_HEAVY = "DECODE_HEAVY"            # output share over the ceiling
    COSTLY_RATIO = "COSTLY_RATIO"            # nonzero work, but work/token under the floor

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# Every kind's severity — the catalogue's integrity invariant
# (`test_every_kind_has_a_severity`): every `RecommendationKind` is a key here.
_KIND_SEVERITY = {
    RecommendationKind.WASTEFUL_SPEND: Severity.CRITICAL,
    RecommendationKind.NOOP_SPIN: Severity.CRITICAL,
    RecommendationKind.DEGRADING_TREND: Severity.HIGH,
    RecommendationKind.OVERCLAIM: Severity.HIGH,
    RecommendationKind.SEAT_UNDERUTILIZED: Severity.MEDIUM,
    RecommendationKind.COLD_CACHE: Severity.MEDIUM,
    RecommendationKind.OVERTHINKING: Severity.MEDIUM,
    RecommendationKind.DECODE_HEAVY: Severity.MEDIUM,
    RecommendationKind.COSTLY_RATIO: Severity.HIGH,
}


def _is_number(value) -> bool:
    """True for a real int/float, never a bool (bools are not counts/ratios)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ---------------------------------------------------------------------------
# Policy — the thresholds that separate the rungs (mechanism is kernel, thresholds
# are config; the `efficiency.EfficiencyPolicy` split).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdvicePolicy:
    """The thresholds the rungs read — policy, not mechanism.

    The structural rungs need only `min_tokens` (the spend floor) and `noop_budget`
    (the no-op streak that counts as a spin); they are armed by default. The four
    tuned ratio rungs are each gated by an OPTIONAL floor/ceiling that is `None`
    (disabled) by default — a host arms one only when it means something for its
    workload (the `efficiency.floor` disabled-by-default discipline, generalized).

      min_tokens        — minimum tokens spent before any spend-shaped rung will
                          accuse the run (default 1000; the `efficiency.min_tokens`
                          analogue — below it there is too little spend to judge).
      noop_budget       — the no-op-turn streak at/above which NOOP_SPIN fires
                          (default 4 — the "3 strikes plus one" spin threshold).
      efficiency_floor  — the work/token floor under which COSTLY_RATIO fires
                          (None = disabled). A positive ratio.
      cache_hit_floor   — the cache-hit fraction under which COLD_CACHE fires
                          (None = disabled). In [0, 1].
      reasoning_ceiling — the reasoning-share fraction over which OVERTHINKING fires
                          (None = disabled). In [0, 1].
      output_ceiling    — the decode-share fraction over which DECODE_HEAVY fires
                          (None = disabled). In [0, 1].
    """

    min_tokens: int = 1000
    noop_budget: int = 4
    efficiency_floor: Optional[float] = None
    cache_hit_floor: Optional[float] = None
    reasoning_ceiling: Optional[float] = None
    output_ceiling: Optional[float] = None

    def __post_init__(self) -> None:
        if isinstance(self.min_tokens, bool) or not isinstance(self.min_tokens, int) or self.min_tokens < 0:
            raise ValueError("min_tokens must be a non-negative integer")
        if isinstance(self.noop_budget, bool) or not isinstance(self.noop_budget, int) or self.noop_budget < 1:
            raise ValueError("noop_budget must be a positive integer")
        if self.efficiency_floor is not None and (not _is_number(self.efficiency_floor) or self.efficiency_floor <= 0):
            raise ValueError("efficiency_floor must be a positive number or None")
        for name in ("cache_hit_floor", "reasoning_ceiling", "output_ceiling"):
            v = getattr(self, name)
            if v is not None and (not _is_number(v) or v < 0.0 or v > 1.0):
                raise ValueError(f"{name} must be a fraction in [0, 1] or None")


DEFAULT_POLICY = AdvicePolicy()


# ---------------------------------------------------------------------------
# Signals — the bundle ADV folds, gathered by the CALLER at its boundary.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EfficiencySignals:
    """The already-measured signals `recommend` folds — none is the agent's narration.

    Every field is OPTIONAL: `None` means *the caller did not measure it* and the
    rungs that read it stay silent (an unmeasured signal is never guessed to 0). The
    count fields are non-negative ints; `degrading_trend` is a tri-state bool; `spend`
    is the typed five-way split. When `spend` is given, `tokens` derives from
    `spend.total` (or, if both are given, must AGREE — a mismatch is a contract error,
    never silently reconciled).

      work             — ground-truth work units the environment witnessed (commits,
                         changed bytes, passed tests — the host's unit, the same one
                         `efficiency`/`productivity` count).
      tokens           — tokens the run spent (the provider usage record).
      noop_turns       — consecutive turns that landed no work (the spin counter).
      overclaim        — claimed ships the truth syscall did NOT confirm (docs/138).
      degrading_trend  — the cross-run `efficiency-trend` verdict: True = work/token
                         is falling across runs; False = measured, not falling; None
                         = not measured.
      serving_accounts — how many provider serving windows were available this wave.
      seats_used       — how many of those windows the fleet actually used.
      spend            — the typed `SpendBreakdown` behind `tokens` (arms the tuned
                         ratio rungs: cache-hit / reasoning / decode shares).
    """

    work: Optional[int] = None
    tokens: Optional[int] = None
    noop_turns: Optional[int] = None
    overclaim: Optional[int] = None
    degrading_trend: Optional[bool] = None
    serving_accounts: Optional[int] = None
    seats_used: Optional[int] = None
    spend: Optional[SpendBreakdown] = None

    def __post_init__(self) -> None:
        for name in ("work", "tokens", "noop_turns", "overclaim", "serving_accounts", "seats_used"):
            v = getattr(self, name)
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, int):
                raise ValueError(f"{name} must be a non-negative integer count or None, got {v!r}")
            if v < 0:
                raise ValueError(f"{name} must be non-negative, got {v!r}")
        if self.degrading_trend is not None and not isinstance(self.degrading_trend, bool):
            raise ValueError("degrading_trend must be a bool or None")
        if self.spend is not None and not isinstance(self.spend, SpendBreakdown):
            raise ValueError("spend must be a SpendBreakdown or None")
        # The scalar derives from the split — one source of truth (the
        # `EfficiencyEvidence` discipline). Both given ⇒ they must agree.
        if self.spend is not None:
            if self.tokens is None:
                object.__setattr__(self, "tokens", self.spend.total)
            elif self.tokens != self.spend.total:
                raise ValueError(
                    f"tokens ({self.tokens}) disagrees with spend.total "
                    f"({self.spend.total}) — an inconsistent pair is a contract "
                    f"error, never silently reconciled"
                )

    @property
    def ratio(self) -> Optional[float]:
        """Work per token — the efficiency. None when work or tokens is unmeasured
        (or no tokens were spent); never a divide-by-zero."""
        if self.work is None or self.tokens is None or self.tokens <= 0:
            return None
        return self.work / self.tokens

    def measured(self) -> dict:
        """The signals the caller actually measured, as JSON — an unmeasured signal
        is ABSENT, never serialized as a null or a guessed 0. The spend split rides
        along under `spend` so the operator sees the bytes behind the advice."""
        out: dict = {}
        for name in ("work", "tokens", "noop_turns", "overclaim", "degrading_trend",
                     "serving_accounts", "seats_used"):
            v = getattr(self, name)
            if v is not None:
                out[name] = v
        if self.spend is not None:
            out["spend"] = self.spend.to_dict()
        return out


# ---------------------------------------------------------------------------
# A single recommendation, and the report a fold returns.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Recommendation:
    """One waste shape ADV named, with the action that addresses it and the bytes
    behind it (the legible-distrust shape: the operator sees not just the kind but
    the env-authored evidence that fired it)."""

    kind: RecommendationKind
    severity: Severity
    signal: str       # what the evidence shows (operator-facing)
    advice: str       # the action that addresses it
    evidence: dict    # the env-authored numbers behind the rung

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "severity": self.severity.value,
            "signal": self.signal,
            "advice": self.advice,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class AdviceReport:
    """The fold `recommend` returns: the ranked recommendations, the rolled-up
    verdict, the one-line reason, and the signals that drove it (carried so
    `--json` emits the advice AND the facts behind it in one object)."""

    verdict: Advice
    reason: str
    recommendations: tuple
    signals: EfficiencySignals

    @property
    def top(self) -> Optional[Recommendation]:
        """The worst (highest-ranked) recommendation, or None on a CLEAN report."""
        return self.recommendations[0] if self.recommendations else None

    def of_severity(self, severity: Severity) -> tuple:
        """The recommendations at exactly this severity, in ranked order."""
        return tuple(r for r in self.recommendations if r.severity is severity)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "signals": self.signals.measured(),
            "recommendations": [r.to_dict() for r in self.recommendations],
        }


def _rec(kind: RecommendationKind, *, signal: str, advice: str, evidence: dict) -> Recommendation:
    """Build a recommendation, pinning its severity from the catalogue."""
    return Recommendation(kind=kind, severity=_KIND_SEVERITY[kind], signal=signal,
                          advice=advice, evidence=evidence)


# ---------------------------------------------------------------------------
# The fold — pure, no I/O.
# ---------------------------------------------------------------------------


def recommend(signals: EfficiencySignals, policy: AdvicePolicy = DEFAULT_POLICY) -> AdviceReport:
    """Fold the measured signals into a ranked advice report. PURE — no I/O.

    Walks the catalogue top to bottom, appending each rung that fires (in catalogue
    order), then stable-sorts by severity so CRITICAL leads and ties keep catalogue
    order. The structural rungs are always armed; the tuned ratio rungs fire only
    when their floor/ceiling is set AND there is enough spend to judge (`min_tokens`).
    The verdict rolls up: any CRITICAL ⇒ WASTE; any recommendation at all ⇒ ADVISE;
    none ⇒ CLEAN.
    """
    recs: list = []
    spend = signals.spend
    tokens = signals.tokens
    # A spend-shaped rung withholds below the spend floor — too little spent to judge.
    spend_floor_ok = tokens is not None and tokens >= policy.min_tokens

    # 1. WASTEFUL_SPEND (CRITICAL) — meaningful tokens, 0 work landed. Needs work
    #    MEASURED as 0 (None is unmeasured, never guessed to 0). Unit-independent.
    if signals.work is not None and signals.work == 0 and spend_floor_ok:
        recs.append(_rec(
            RecommendationKind.WASTEFUL_SPEND,
            signal=f"{tokens} tokens spent, 0 work units landed",
            advice="the spend bought nothing — stop the run and check for a marker-spin "
                   "or a stuck loop before re-dispatching",
            evidence={"work": 0, "tokens": tokens}))

    # 2. NOOP_SPIN (CRITICAL) — a streak of no-op turns at/over budget: the loop is
    #    alive but landing nothing.
    if signals.noop_turns is not None and signals.noop_turns >= policy.noop_budget:
        recs.append(_rec(
            RecommendationKind.NOOP_SPIN,
            signal=f"{signals.noop_turns} consecutive no-op turn(s) (budget {policy.noop_budget})",
            advice="the loop is spinning without landing work — trip the breaker / "
                   "escalate; re-dispatching as-is will only burn more budget",
            evidence={"noop_turns": signals.noop_turns, "noop_budget": policy.noop_budget}))

    # 3. DEGRADING_TREND (HIGH) — the cross-run efficiency-trend verdict says
    #    work/token is FALLING. Only True fires; False (measured-flat) and None
    #    (unmeasured) stay silent.
    if signals.degrading_trend is True:
        recs.append(_rec(
            RecommendationKind.DEGRADING_TREND,
            signal="work-per-token is falling across runs",
            advice="cross-run efficiency is degrading — investigate the cause before "
                   "scaling the fleet wider",
            evidence={"degrading_trend": True}))

    # 4. OVERCLAIM (HIGH) — claimed ships the truth syscall did not confirm. Tokens
    #    spent narrating a 'done' that did not land.
    if signals.overclaim is not None and signals.overclaim > 0:
        recs.append(_rec(
            RecommendationKind.OVERCLAIM,
            signal=f"{signals.overclaim} claimed ship(s) the truth syscall did not confirm",
            advice="tighten the done-condition — gate 'done' on `dos verify` / "
                   "`dos commit-audit`, not the agent's say-so",
            evidence={"overclaim": signals.overclaim}))

    # 5. SEAT_UNDERUTILIZED (MEDIUM) — idle serving windows: throughput left on the
    #    table. A single serving window cannot be 'under-spread', so > 1 is required.
    if (signals.serving_accounts is not None and signals.serving_accounts > 1
            and signals.seats_used is not None and signals.seats_used < signals.serving_accounts):
        idle = signals.serving_accounts - signals.seats_used
        recs.append(_rec(
            RecommendationKind.SEAT_UNDERUTILIZED,
            signal=f"{idle} of {signals.serving_accounts} serving window(s) idle",
            advice="account-balance the fleet wider — idle serving windows are "
                   "throughput left on the table",
            evidence={"serving_accounts": signals.serving_accounts, "seats_used": signals.seats_used}))

    # --- the tuned ratio rungs — armed only when their floor/ceiling is set AND
    #     there is enough spend to judge (the spend floor). ---

    # 6. COLD_CACHE (MEDIUM) — the prompt cache is under the hit floor.
    if (policy.cache_hit_floor is not None and spend is not None and spend_floor_ok
            and spend.cache_hit_ratio < policy.cache_hit_floor):
        recs.append(_rec(
            RecommendationKind.COLD_CACHE,
            signal=f"cache-hit {spend.cache_hit_ratio:.3g} under the {policy.cache_hit_floor:.3g} floor",
            advice="the prompt cache is cold — stabilize the prompt prefix so the "
                   "provider serves more of the context from cache",
            evidence={"cache_read": spend.cache_read, "prefill": spend.prefill,
                      "cache_hit_ratio": spend.cache_hit_ratio}))

    # 7. OVERTHINKING (MEDIUM) — most of the decode was deliberation.
    if (policy.reasoning_ceiling is not None and spend is not None and spend_floor_ok
            and spend.reasoning_share > policy.reasoning_ceiling):
        recs.append(_rec(
            RecommendationKind.OVERTHINKING,
            signal=f"reasoning {spend.reasoning_share:.3g} of output, over the "
                   f"{policy.reasoning_ceiling:.3g} ceiling",
            advice="most of the decode is deliberation — cap the thinking budget or "
                   "simplify the task framing",
            evidence={"reasoning": spend.reasoning, "output": spend.output,
                      "reasoning_share": spend.reasoning_share}))

    # 8. DECODE_HEAVY (MEDIUM) — the spend is decode-dominated (the expensive side).
    if (policy.output_ceiling is not None and spend is not None and spend_floor_ok
            and spend.output_share > policy.output_ceiling):
        recs.append(_rec(
            RecommendationKind.DECODE_HEAVY,
            signal=f"decode {spend.output_share:.3g} of total, over the "
                   f"{policy.output_ceiling:.3g} ceiling",
            advice="the spend is decode-dominated (the slow, per-token-priced side) — "
                   "shorten outputs or move bulk work to a cheaper rung",
            evidence={"output": spend.output, "total": spend.total,
                      "output_share": spend.output_share}))

    # 9. COSTLY_RATIO (HIGH) — nonzero work, but work/token under the floor. Distinct
    #    from WASTEFUL_SPEND (which is the work==0 floor) — here the run IS doing work,
    #    just paying a lot per unit.
    if (policy.efficiency_floor is not None and signals.work is not None
            and signals.work > 0 and spend_floor_ok):
        ratio = signals.ratio
        if ratio is not None and ratio < policy.efficiency_floor:
            recs.append(_rec(
                RecommendationKind.COSTLY_RATIO,
                signal=f"{ratio:.3g} work/token under the {policy.efficiency_floor:.3g} floor",
                advice="doing work but paying a lot per unit — profile the loop for "
                       "redundant reads / re-planning",
                evidence={"work": signals.work, "tokens": tokens, "ratio": ratio}))

    # Rank: CRITICAL first; ties keep catalogue order (the append order above) via a
    # STABLE sort on the severity rank.
    recs.sort(key=lambda r: _SEVERITY_RANK[r.severity])

    if not recs:
        verdict = Advice.CLEAN
        reason = "the run is spending well — no waste signal crossed a floor"
    else:
        if any(r.severity is Severity.CRITICAL for r in recs):
            verdict = Advice.WASTE
        else:
            verdict = Advice.ADVISE
        n = len(recs)
        reason = (f"{n} efficiency signal(s) — worst: {recs[0].kind.value} "
                  f"({recs[0].severity.value})")

    return AdviceReport(
        verdict=verdict,
        reason=reason,
        recommendations=tuple(recs),
        signals=signals,
    )


# ---------------------------------------------------------------------------
# The `[efficiency_advice]` config seam — modelled on `efficiency.policy_from_table`.
# ---------------------------------------------------------------------------


def policy_from_table(table: dict, *, base: "AdvicePolicy" = DEFAULT_POLICY) -> "AdvicePolicy":
    """Build an `AdvicePolicy` from a parsed `[efficiency_advice]` TOML table. PURE.

    Each field the table names overrides ``base``; omitted fields inherit. An
    unknown key raises ``ValueError`` (the `efficiency.policy_from_table` posture);
    a wrong-typed or out-of-bound value raises via the dataclass ``__post_init__``.
    """
    import dataclasses as _dc
    if not isinstance(table, dict):
        raise ValueError(f"[efficiency_advice] must be a table, got {type(table).__name__}")
    known = {"min_tokens", "noop_budget", "efficiency_floor", "cache_hit_floor",
             "reasoning_ceiling", "output_ceiling"}
    unknown = set(table) - known
    if unknown:
        raise ValueError(
            f"[efficiency_advice] has unknown key(s) {sorted(unknown)}; "
            f"known keys are {sorted(known)}"
        )
    overrides = {k: table[k] for k in known if k in table}
    return _dc.replace(base, **overrides)
