"""PRD — the productivity verdict: *is this run still doing work, or just spending?*

docs/218 — the **loop-economics completion of `liveness()`**. `liveness` asks a
binary, lifetime question off ground truth: did git/journal state advance *at all*
since the run started (ADVANCING), is the run alive-but-not-moving (SPINNING), or
dead (STALLED)? PRD asks a different, *continuous* question off a **trend**: is the
amount of work landed *per step* collapsing toward nothing? A run can be ADVANCING
(it committed) and still be DIMINISHING (each successive step does less and less
until it is burning budget to refine the same thing). That gap — productive vs.
*productive-but-fading* — has no home in `liveness` (a single since-start count
cannot see a trend) and no home in `loop_decide` (whose every stop is a hard count
cap or a discrete verdict, never a velocity).

This is `liveness`'s **lateral sibling** — the same pure-verdict shape, re-aimed
from "did state move?" to "is the work-per-step rate fading?":

    arbiter.arbitrate     (request, live_leases, config)  -> decision
    loop_decide.decide    (LoopState, IterationOutcome)    -> LoopDecision
    liveness.classify     (ProgressEvidence, policy)       -> LivenessVerdict
    productivity.classify  (WorkHistory, policy)            -> ProductivityVerdict
                           ^ THIS module

It is lifted faithfully from the diminishing-returns gate Claude Code ships in its
own session loop (`query/tokenBudget.ts` `checkTokenBudget` — the
`isDiminishing = continuationCount>=3 AND lastDelta<T AND priorDelta<T` rule, the
docs/189 audit's "cleanest loop-economics lift"). DOS owns the *mechanism* — a pure
trend verdict — and pushes the *policy* (which unit the deltas count, how many
steps before judging, what floor counts as "fading") out to data, the
mechanism/policy split that lets a small thing be a universal cog: the kernel does
not know whether a "work unit" is a token, a commit, a changed byte, or a passed
test. The host names the unit in `dos.toml [productivity]`; the kernel only knows
*the rate is falling*.

**Byte-clean by construction.** A per-step work delta is a count the *runtime/env*
authors (tokens spent this turn, commits this step, bytes diffed) — never the
judged agent's narration. PRD reads the same kind of agent-external counter
`liveness` reads off git, and `tool_stream` reads off env-authored result digests
(the docs/138 invariant). So DIMINISHING is "the work rate the environment
recorded is fading," never "the agent says it's almost done" — a quantity, not a
self-report. PRD says the *rate* fell; it never says the work was *wrong* (quality
is an advisory judge's call — `llm_judge` — never this deterministic verb, the
distrust-state / distrust-judgment line `liveness` draws).

**Multi-signal, so one slow step can't false-trip.** The whole reason CC ANDs
three signals (enough steps AND this delta small AND the prior delta small) is that
a single quiet turn is not a fading run — a run legitimately pauses to read, to
plan, to wait on eventual consistency. DIMINISHING requires a *sustained* low rate
(the two most recent deltas both under the floor) past a minimum step count, so the
verdict fires on a trend, not a blip. This is the productivity analogue of
`liveness`'s `grace_ms` young-and-alive guard: withhold the accusation until there
is enough evidence to make it.

**Advisory.** Like `liveness.SPINNING`, DIMINISHING REPORTS; it never kills a
process or refuses a lease. A loop may consult PRD and choose to stop (the natural
first consumer — a `loop_decide` DIMINISHING_RETURNS rung that converts
stop-after-N into stop-when-unproductive), the enforce ladder may attach a
WARN-before-BLOCK nudge, and `dos top` may surface a fading run — but the
productivity verdict and the admission decision stay different syscalls.

**No-telemetry / no-plan discipline** (the `test_verify_no_plan` sibling): PRD needs
*nothing* but a list of per-step work deltas the caller already has. No plan, no
registry, no journal, no clock — `classify()` makes no I/O at all (there is no clock
rung here; unlike `liveness`, productivity is timeless — it reads a sequence, not
ages). A caller with two deltas gets a verdict; a caller with none gets the honest
"not enough history to judge" (PRODUCTIVE-benign, the withhold-the-accusation
floor).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Sequence


class Productivity(str, enum.Enum):
    """The typed productivity verdict — three states, mutually exclusive.

    `str`-valued so it round-trips through a CLI stdout token / exit-code map
    without a lookup table (mirrors `liveness.Liveness` and `gate_classify.Verdict`).
    """

    PRODUCTIVE = "PRODUCTIVE"    # still landing work per step (or too little history to judge)
    DIMINISHING = "DIMINISHING"  # a sustained low work-rate past the min-step count — fading
    STALLED = "STALLED"          # the most recent step landed ZERO work — flat-lined

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class ProductivityPolicy:
    """The thresholds that separate PRODUCTIVE / DIMINISHING / STALLED — policy, not mechanism.

    The same "mechanism is kernel, thresholds are config" split as `liveness`'s
    windows and `loop_decide`'s `max_iterations`. The defaults are GENERIC and lifted
    from Claude Code's own loop (`tokenBudget.ts`: 3 continuations, a 500-unit
    diminishing threshold); a workspace declares its own in `dos.toml
    [productivity]`, the closed-config-as-data pattern (`[lanes]` / `[stamp]` /
    `[liveness]`).

      min_steps — the **minimum number of work steps** before PRD will call a run
                  DIMINISHING. Below it there is not enough of a trend to judge a
                  *fading* rate (one or two small deltas are a blip, not a decline),
                  and the verdict withholds the accusation. CC's `continuationCount
                  >= 3`. This is the productivity analogue of `liveness.grace_ms`.
      floor     — the **per-step work-unit floor** below which a step counts as "did
                  little." A run is DIMINISHING only when the two most recent deltas
                  are BOTH under this floor (a sustained low rate). CC's
                  `DIMINISHING_THRESHOLD` (500 tokens). The UNIT is the host's —
                  tokens, commits, changed bytes — declared alongside the floor; the
                  kernel only compares magnitudes.

    Defaults: 3 steps, a 500-unit floor. So a run that has taken ≥3 steps and whose
    last two steps each landed < 500 units of work is fading; fewer steps, or either
    of the last two steps clearing the floor, is still PRODUCTIVE.
    """

    min_steps: int = 3      # CC continuationCount>=3 — min trend length before judging
    floor: int = 500        # CC DIMINISHING_THRESHOLD — per-step "did little" work-unit floor

    def __post_init__(self) -> None:
        if self.min_steps < 0:
            raise ValueError("min_steps must be non-negative")
        if self.floor < 0:
            raise ValueError("the work-unit floor must be non-negative")


DEFAULT_POLICY = ProductivityPolicy()


@dataclass(frozen=True)
class WorkHistory:
    """The per-step work-delta trend `classify()` reads — gathered by the CALLER.

    No clock, no I/O inside the verdict — the arbiter rule, sharpened: there is not
    even a clock rung here (productivity is *timeless*; it reads a sequence of
    deltas, never an age). The caller's boundary (the `dos productivity`
    evidence-gather) measures each step's work — tokens spent that step, commits that
    step, bytes diffed — and freezes the ordered list here.

      deltas — the ordered per-step work deltas, OLDEST first, one number per step.
               Each is a count of *work units* (the host's chosen unit) the
               runtime/env measured for that step. Empty or one-element is "not
               enough history to judge a trend." Negative values are rejected — a
               work delta is a non-negative quantity of work done (a step that
               *removed* work is still a step that did the work of removing; the host
               passes the magnitude, never a signed regression).

    The two load-bearing reads are `deltas[-1]` (this step) and `deltas[-2]` (the
    prior step) — the same `lastDeltaTokens` / `deltaSinceLastCheck` pair CC's
    `isDiminishing` ANDs. The full list is carried so `--output json` can echo the
    whole trend (the legible-distrust renderer seam: the operator sees not just
    DIMINISHING but the falling sequence behind it), and so `step_count` is honest.
    """

    deltas: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        # Accept any Sequence at the boundary, freeze to a tuple so the dataclass
        # stays hashable/immutable (the frozen-evidence discipline). A caller that
        # passes a list does not get a shared-mutable field.
        if not isinstance(self.deltas, tuple):
            object.__setattr__(self, "deltas", tuple(self.deltas))
        if any(d < 0 for d in self.deltas):
            raise ValueError("work deltas must be non-negative (a count of work done)")

    @property
    def step_count(self) -> int:
        """How many work steps the trend covers."""
        return len(self.deltas)

    @classmethod
    def of(cls, deltas: Sequence[int]) -> "WorkHistory":
        """Build a history from any ordered (oldest-first) sequence of deltas."""
        return cls(tuple(deltas))


@dataclass(frozen=True)
class ProductivityVerdict:
    """The single verdict `classify()` returns, with the trend echoed back.

    `verdict` is the typed `Productivity`. `reason` is a one-line operator-facing
    summary (the tally-row string). `history` is the `WorkHistory` that drove the
    call, carried so `dos productivity --output json` can emit the verdict *and the
    facts behind it* in one object (the RND/Axis-4 renderer seam) — legible
    distrust: the operator sees not just DIMINISHING but *why* (last two steps 40,
    12 units, both under the 500 floor, 6 steps in). `to_dict` is the json shape.
    """

    verdict: Productivity
    reason: str
    history: WorkHistory

    def to_dict(self) -> dict:
        h = self.history
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "history": {
                "deltas": list(h.deltas),
                "step_count": h.step_count,
                "last_delta": h.deltas[-1] if h.deltas else None,
                "prior_delta": h.deltas[-2] if len(h.deltas) >= 2 else None,
            },
        }


def classify(
    history: WorkHistory, policy: ProductivityPolicy = DEFAULT_POLICY
) -> ProductivityVerdict:
    """Classify a run's productivity from its per-step work trend. PURE — no I/O.

    Reads the ladder top to bottom (this function IS the answer to "is it still
    doing work?"):

      1. PRODUCTIVE (too little history) — fewer than `min_steps` steps: there is
         not enough of a trend to accuse a run of fading. Withhold the accusation
         (the `liveness` young-and-alive guard, lateral). This is checked FIRST so a
         brand-new run with one big step is never mislabelled on a length
         technicality.
      2. STALLED — the most recent step landed ZERO work (`deltas[-1] == 0`): the run
         flat-lined, the degenerate floor of diminishing. Distinguished from
         DIMINISHING (which is a fading-but-nonzero rate) because a zero is the
         operator's clearest "it stopped doing anything" signal — the give-up rung.
         Checked before DIMINISHING so an exact flat-line is named precisely.
      3. DIMINISHING — a sustained low rate: `step_count >= min_steps` AND the last
         two deltas are BOTH under `floor`. The CC `isDiminishing` rule exactly —
         fading, but still moving a little. The multi-signal AND is what keeps one
         quiet step from false-tripping.
      4. PRODUCTIVE — none of the above: either a recent step cleared the floor, or
         the run simply hasn't sustained a low rate. Still doing real work.

    The DIMINISHING test needs the prior delta (`deltas[-2]`); with exactly
    `min_steps` steps that always exists when `min_steps >= 2`. A pathological
    `min_steps < 2` policy is handled: the prior-delta read falls back so the verdict
    never indexes off the end (a one-step history can only be PRODUCTIVE or STALLED).
    """
    n = history.step_count

    # 1. PRODUCTIVE (too little history) — not enough steps to judge a trend.
    #    Withhold the DIMINISHING accusation; report the benign verdict. A run with
    #    no steps at all also lands here (nothing to judge, no problem yet).
    if n < policy.min_steps or n == 0:
        return ProductivityVerdict(
            verdict=Productivity.PRODUCTIVE,
            reason=(
                f"{n} work step(s) so far (< min {policy.min_steps}) — not enough "
                f"history to judge a fading rate; no productivity problem yet"
            ),
            history=history,
        )

    last = history.deltas[-1]

    # 2. STALLED — the most recent step did zero work. The flat-line / give-up rung,
    #    named distinctly from a merely-fading rate so the operator's clearest signal
    #    ("it stopped") is not blurred into DIMINISHING.
    if last == 0:
        return ProductivityVerdict(
            verdict=Productivity.STALLED,
            reason=(
                f"the most recent of {n} steps landed 0 work units — flat-lined "
                f"(zero forward work this step)"
            ),
            history=history,
        )

    # The prior delta — the second of CC's two ANDed signals. Guarded so a
    # degenerate min_steps<2 policy cannot index off the front (a 1-element history
    # has no prior; treat it as "above floor" so it can never satisfy DIMINISHING).
    prior = history.deltas[-2] if n >= 2 else policy.floor

    # 3. DIMINISHING — a SUSTAINED low rate: enough steps AND both recent deltas
    #    under the floor. The CC `isDiminishing` rule, the whole point of the module.
    if last < policy.floor and prior < policy.floor:
        return ProductivityVerdict(
            verdict=Productivity.DIMINISHING,
            reason=(
                f"the last two of {n} steps landed {prior} then {last} work units, "
                f"both under the {policy.floor}-unit floor — a sustained fading rate "
                f"(diminishing returns)"
            ),
            history=history,
        )

    # 4. PRODUCTIVE — a recent step cleared the floor, or the low rate is not
    #    sustained across the last two steps. Still doing real work.
    return ProductivityVerdict(
        verdict=Productivity.PRODUCTIVE,
        reason=(
            f"last step landed {last} work units over {n} steps "
            f"(prior {prior}; floor {policy.floor}) — still productive"
        ),
        history=history,
    )


# ---------------------------------------------------------------------------
# The `[productivity]` config seam (issue #37) — modelled on `dos.cooldown`.
#
# The loop-economics thresholds were reachable only via a CLI flag or the
# dataclass default; this pair arms them as declared `dos.toml` data. Every
# bound is already validated by the frozen policy's `__post_init__`, so the
# loader never re-implements a bound — it builds the policy and lets the
# dataclass refuse a bad value (the same `ValueError` path as an unknown key).
# ---------------------------------------------------------------------------


def policy_from_table(table: dict, *, base: "ProductivityPolicy" = DEFAULT_POLICY) -> "ProductivityPolicy":
    """Build a `ProductivityPolicy` from a parsed `[productivity]` TOML table. PURE.

    Each field the table names overrides ``base``; omitted fields inherit. An
    unknown key raises ``ValueError`` (the `stamp.convention_from_table` /
    `cooldown.policy_from_table` posture); a wrong-typed or out-of-bound value
    raises via the dataclass ``__post_init__``.
    """
    import dataclasses as _dc
    if not isinstance(table, dict):
        raise ValueError(f"[productivity] must be a table, got {type(table).__name__}")
    known = {"min_steps", "floor"}
    unknown = set(table) - known
    if unknown:
        raise ValueError(
            f"[productivity] has unknown key(s) {sorted(unknown)}; "
            f"known keys are {sorted(known)}"
        )
    overrides = {k: table[k] for k in known if k in table}
    return _dc.replace(base, **overrides)


def load_from_toml(path, *, base: "ProductivityPolicy" = DEFAULT_POLICY) -> "ProductivityPolicy":
    """Build a `ProductivityPolicy` from a `dos.toml`'s `[productivity]` table.

    Returns ``base`` unchanged (the SAME object) when the file is absent, has no
    `[productivity]` table, or it is empty. A present-but-malformed table raises.
    Mirrors `cooldown.load_from_toml` (incl. the `utf-8-sig` BOM strip)."""
    from pathlib import Path as _Path
    p = _Path(path)
    if not p.exists():
        return base
    try:
        import tomllib as _toml
    except ModuleNotFoundError:  # pragma: no cover - py<3.11 fallback
        try:
            import tomli as _toml  # type: ignore
        except ModuleNotFoundError:
            return base
    data = _toml.loads(p.read_text(encoding="utf-8-sig"))
    table = data.get("productivity")
    if not isinstance(table, dict) or not table:
        return base
    return policy_from_table(table, base=base)
