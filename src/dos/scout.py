"""The pre-dispatch CHOOSER — the missing third kernel half of the loop spine.

The `dos` kernel already answers two of the three loop questions:

    dos.health.check        →  "should I START?"      (overlap + recurrence)
    dos.loop_decide.decide  →  "should I CONTINUE?"   (post-iteration stop)

`dos.scout.choose` answers the third — **"what should I start?"** — *before* a
lane is leased and before any child is launched. Today that question is answered
only by a hardcoded `iter-1 mode = dispatch` in the loop's SKILL; scout makes it
a first-class, typed, pure decision over the full activity menu.

WHY (the incident this generalizes): `dos.health` exists because a `/dispatch-loop`
burned ~$9/40min launching a `/dispatch` child to rediscover a blocker knowable at
second zero. But `health` runs *after* `dispatch-lane acquire` and only picks from
a 4-outcome menu (proceed/unstick/replan/overlap) — two of which are
"do-X-then-dispatch" detours. The operator's own override log (the `job` repo's
MEMORY feedback entries) is ~30 transcripts of a human, *before* leasing a lane,
reading the scoreboard / same-day-unstick-count / open-decision state and choosing
replan-vs-unstick-vs-dispatch. Scout is that human override, made a pure function.
It runs FIRST, with no lane held, over the full menu, and may route to a different
activity (unstick / replan / wait) instead of dispatching.

KEY INVARIANT (operator directive 2026-06-03): the chooser's job is to pick *what
to start* — so it **never self-STOPs on an analytic/portfolio signal**. A noisy 7d
scoreboard, an open (already-escalated) operator decision, and a saturated
`/unstick` are ROUTING signals, not halt signals: scout routes to a productive
activity for that context (dispatch the lane's other work; route away from a
looping `/unstick`) and surfaces the signal as evidence. The ONLY condition under
which the chooser refuses to launch anything is a measured can't-launch wall the
host hit (`ScoutState.resource_blocked` — RAM/slot pool exhausted, spawn cap, a
usage window truly spent). "Give up" is never the chooser's call to make on its own.

Design — this module is the **reference example** of the dos composition idiom
(the way `cat` was the first program written for Unix). It demonstrates how a
host-app composes kernel decisions:

  * `choose(state)` is a **pure function**: a frozen `ScoutState` of facts in
    (a pre-computed sibling `HealthVerdict`, pre-reduced scalar signals), one
    typed `ScoutDecision` out. No I/O, no clock, no oracle/health call inside —
    every time-derived signal is reduced to a scalar/bool/tuple by the caller at
    the I/O edge (exactly as `liveness.classify(now_ms)` takes `now` at the edge,
    never inside the pure core). So `choose` is replay-tested in isolation.
  * The sibling `HealthVerdict` is carried as an **input field**
    (`ScoutState.health`), not recomputed — the precise analogue of
    `loop_decide.LoopState.liveness: Optional[Liveness]`. Per CLAUDE.md the litmus
    is "no host, no I/O", NOT "no sibling import": scout READS a verdict value,
    it never computes one. The host adapter runs `dos.health.check` once and hands
    the verdict in. One git gather, one verdict; scout's pure boundary never
    touches a `RunRecord`.
  * `check(...)` is the thin in-kernel composition (kwargs → `ScoutState` →
    `choose`), and `cmd_check` / `build_parser` are the CLI — the same three-tier
    shape (`lane_health` / `check` / `cmd_check`) a `dos.health` reader already
    knows.

Scout only **routes**; the chosen skill does the work. The moment it renders a
packet or reads a plan deeply it has reinvented `/next-up` — so there is no
`command` field here (rendering a shell command is a host concern), and the wider
menu lives in a bounded `_phase2_menu` annex so the live 9-rule spine stays
legible. This is the structural guard against scout becoming the very
"tail wagging the dog" a chooser-of-everything would be.

Spec / design record: the `job` repo's `docs/_design/dispatch-scout-concept.md`.
"""
from __future__ import annotations

import argparse
import enum
import json
import sys
from dataclasses import dataclass, field
from typing import Optional

# Sibling-kernel TYPE import only. `choose()` reads a `HealthVerdict` value; it
# NEVER calls `lane_health`/`check` (those do git I/O — the caller runs them and
# hands the verdict in). This mirrors `loop_decide`'s `from dos.liveness import
# Liveness`. The litmus (CLAUDE.md) is "no host, no I/O", not "no sibling import".
from dos.health import HealthAction, HealthVerdict
# Sibling-kernel seam (TYPE + the floor-AND helper). `choose()` reads a resolved
# `StopPolicy` value off the state and runs it via `stop_under_resource_floor`
# (which enforces fail-to-DEFER + the resource_blocked floor) — it NEVER resolves
# a policy (that is the adapter's call-boundary job, `active_stop_policy`). One-way
# import: scout imports stop_policy, never the reverse (stop_policy reads the state
# by attribute to stay cycle-free). The litmus is "no host, no I/O" — a STOP policy
# VALUE is neither.
from dos.stop_policy import StopPolicy, stop_under_resource_floor


# ───────────────────────────── the menu (enums) ──────────────────────────────
class ScoutActivity(str, enum.Enum):
    """The activity scout routes to. `str`-valued so it serializes to its value.

    The Phase-1 LIVE set is the only thing `choose()` returns today; the
    RESERVED slots ship in the schema now (so widening the menu in Phase 2 is a
    no-schema-break change) but `choose()` never emits them until their
    `_phase2_menu` rungs are wired.
    """

    # Phase-1 LIVE — the only values choose() returns today.
    DISPATCH = "dispatch"      # lane is healthy to start — dispatch the next pick
    UNSTICK = "unstick"        # fresh recurring structural blocker — /unstick first
    REPLAN = "replan"          # soak/data-gated or replan-due — /replan first
    WAIT = "wait"              # overlap / open soak — re-pick a disjoint lane or wait
    STOP = "stop"              # surface to operator; a loop can't resolve this

    # Phase-2 RESERVED — enum slots ship now; rungs live in _phase2_menu.
    NEXT_UP = "next-up"
    JUDGE = "judge"
    PLAN_AUDIT = "plan-audit"
    TAIL_WAG = "tail-wag"
    TRAJECTORY_AUDIT = "trajectory-audit"

    def __str__(self) -> str:  # so f"{activity}" is the value, not "ScoutActivity.STOP"
        return self.value


class Confidence(str, enum.Enum):
    """Auto-run gate axis. The boundary is REVERSIBILITY, not certainty-of-correctness
    — verbatim the `JO_AUTO_ACCEPT` design: a HIGH decision is reversible/safe to
    auto-run; a LOW decision is a judgement call that must SURFACE to the operator
    (never auto-run). Actuation of the gate lives in the host adapter (a
    `SCOUT_AUTO_RUN` flag parallel to `JO_AUTO_ACCEPT`); the kernel only LABELS."""

    HIGH = "high"
    LOW = "low"

    def __str__(self) -> str:
        return self.value


# ───────────────────────────── the output type ───────────────────────────────
@dataclass(frozen=True)
class ScoutDecision:
    """The single typed output. `activity` is a *destination*, not a detour.

    There is deliberately NO `command` field: rendering a shell command is a host
    concern (the kernel must not know `/dispatch --scope X` syntax — that is the
    god-skill guard, made structural). The adapter builds the command from
    `activity` + `scope` + `focus` + `gate`.
    """

    activity: ScoutActivity
    rule_id: int                  # 1..9 (concept §3 numbering) — the test anchor:
    #                               which rung fired, not just which activity.
    confidence: Confidence
    reason: str                   # one line, operator-facing
    evidence: tuple[str, ...] = field(default_factory=tuple)  # dated/queryable artefacts
    scope: Optional[str] = None   # the lane the activity runs on
    focus: Optional[str] = None   # not-started|priority-first[:N]|nearly-done|stale-stamp|none
    gate: Optional[str] = None    # only meaningful when activity == DISPATCH
    cause_key: str = ""           # only when activity in {STOP, WAIT} — clusters the
    #                               surfaced cause (mirrors HealthVerdict.cause_key)

    @property
    def needs_lane(self) -> bool:
        """Activities that require a leased lane to run (the host acquires only
        for these — the whole point of choosing BEFORE acquire)."""
        return self.activity in (
            ScoutActivity.DISPATCH, ScoutActivity.UNSTICK, ScoutActivity.NEXT_UP,
        )

    @property
    def auto_runnable(self) -> bool:
        """HIGH-confidence ⇒ a host with SCOUT_AUTO_RUN set MAY auto-run; LOW ⇒
        always surface. The kernel only reports the bit; the host actuates."""
        return self.confidence is Confidence.HIGH

    def __post_init__(self) -> None:
        if self.gate is not None and self.activity is not ScoutActivity.DISPATCH:
            raise ValueError(
                f"a {self.activity} decision must not carry a gate "
                f"(only DISPATCH has a gate policy)"
            )
        if self.cause_key and self.activity not in (ScoutActivity.STOP, ScoutActivity.WAIT):
            raise ValueError(
                f"a {self.activity} decision must not carry a cause_key "
                f"(only STOP/WAIT name a blocking cause)"
            )


# ───────────────────────── reserved Phase-2 input slices ──────────────────────
@dataclass(frozen=True)
class ScoreboardShape:
    """The typed slice of `SCOREBOARD.json` rule 8 reads — never a raw dict
    (data-trust-floor: a decision stands on a named, typed artefact)."""

    runs_7d: int = 0
    zero_ship_7d: int = 0
    false_drain_runs: int = 0
    runs_shipped: int = 0

    @property
    def zero_ship_frac(self) -> float:
        return (self.zero_ship_7d / self.runs_7d) if self.runs_7d else 0.0


@dataclass(frozen=True)
class WedgeSignal:
    """Phase-2 — narrows `dos.recurring_wedge`'s verdict to the booleans scout
    consumes (the same don't-recompute seam as `health`: read the verdict, don't
    re-derive it)."""

    recurring: bool = False
    operator_decision: bool = False
    cause_key: str = ""


@dataclass(frozen=True)
class ClosedLoopSignal:
    """The lane's available CLOSED-LOOP work, as a typed slice (data-trust-floor:
    a decision stands on a named, typed artefact, never a raw dict).

    WHY this is a first-class scout input (operator directive 2026-06-04): the
    highest-leverage work in a self-improving system is the kind that converts a
    *recurring observation* into a *durable mechanism* — an oracle, a preflight,
    a gate, a learned-answer promoter — so the same failure stops recurring
    instead of being re-paid every run. The worked example: the apply learning
    engine extracted the visa/degree screening-answer fix at conf=1.0 ~50× but
    only ever fed it to the LLM prompt (an OPEN loop), so the field failed ~50×;
    closing the loop (FQ-467: promote the lesson to a config write the
    deterministic path reads) removes the failure class outright. Open-loop work
    bleeds at a constant rate; closed-loop work pays down the rate.

    Scout cannot itself know what "a closed-loop primitive" is (that is host
    knowledge — which plans/findings are loop-closers, measured from plan-meta /
    findings tags by the adapter at the I/O edge). The adapter reduces that to
    this slice and hands it in; `choose()` only WEIGHTS it — biasing the
    in-lane `focus` toward the loop-closing work when the lane is otherwise
    clean to dispatch. It never manufactures a route on its own (a lane with
    closed-loop work still has to pass every gate above), and it never STOPs.

    Default `available=False` ⇒ the rung is inert ⇒ `choose()` is byte-identical
    to before this field existed (the reserved-input convention).
    """

    available: bool = False          # the lane has ≥1 pickable loop-closing item
    count: int = 0                   # how many (lets the magnitude ride in evidence)
    top_item: str = ""               # the highest-value loop-closer (operator-facing)

    @property
    def is_actionable(self) -> bool:
        return self.available and self.count > 0


@dataclass(frozen=True)
class LaneOutcomeShape:
    """THIS lane's recent decision→outcome history, as a typed slice (data-trust-
    floor: a decision stands on a named, typed artefact, never a raw dict).

    WHY this is a first-class scout input (operator directive 2026-06-09, the
    "10x self-improving loop" goal): the fleet was richly INSTRUMENTED but not
    feedback-RESPONSIVE — it recorded every iteration's outcome but never read it
    back to ROUTE differently. This slice closes that gap for the per-lane case:
    the adapter reduces the decision→outcome ledger (`decision_outcomes.jsonl`) to
    "over the last K runs on THIS lane, how often did it actually ship", and the
    chooser uses it to PRE-EMPT — a lane that keeps shipping nothing *but has
    pickable work* gets a /replan (re-prioritize its backlog) BEFORE another heavy
    non-shipping child, instead of discovering the unproductivity inside repeated
    $10+ dispatches.

    Distinct from `LaneYield` (instantaneous confirmed-empty pick-yield → refill)
    and from `ScoreboardShape` (fleet-wide 7d zero-ship → noise, never a route):
    this is HISTORICAL per-lane ship-rate ("there IS work but it never ships →
    re-prioritize"). Like every analytic input it ROUTES, never STOPs (the
    2026-06-03 STOP-discipline: only a measured resource wall halts).

    Default `window=0` ⇒ `is_actionable` False ⇒ the rung is inert ⇒ `choose()` is
    byte-identical to before this field existed (the reserved-input convention).
    """

    window: int = 0                  # K runs considered (0 ⇒ no data ⇒ inert)
    runs: int = 0                    # rows in window for this lane
    shipped_runs: int = 0            # rows with verdict SHIPPED
    drained_runs: int = 0            # rows with verdict DRAIN
    blocked_runs: int = 0            # rows with verdict BLOCK
    top_blocked_cause: str = ""      # modal blocked_cause_key (operator-facing)

    @property
    def ship_rate(self) -> float:
        return (self.shipped_runs / self.runs) if self.runs else 0.0

    # Minimum real runs needed to trust the per-lane rate (a floor, NOT the
    # requested window): a 0-ship signal over 3 runs is already actionable; we do
    # not demand the full lookback was available (a lane that has only run K<window
    # times still has a real, weighable history).
    _MIN_RUNS = 3

    @property
    def is_actionable(self) -> bool:
        # enough signal to weigh: at least _MIN_RUNS real runs in the slice. (The
        # `window` is the lookback the adapter REQUESTED; what matters here is how
        # many runs it actually FOUND — `runs` — so a lane with fewer runs than the
        # requested window is still actionable once it clears the floor.)
        return self.runs >= self._MIN_RUNS


@dataclass(frozen=True)
class LaneYield:
    """Typed slice of the pick-oracle verdict for the lane scout is about to lease
    (yield-aware scout, the `job` repo's docs/_design/dispatch-yield-aware-scout-plan.md
    Phase 1). It names the missing FOURTH kernel question — "is there anything to
    act on?" — so a chooser that routes activities no longer pays a full
    `claude -p` child ($5-15, 15-45 min) just to discover the lane it leased will
    DRAIN at the empty-packet gate.

    `count`: the SAME `int | None` the lane arbiter's pick-oracle
    (`fanout_state._build_pick_oracle`) already produces — a confident count of
    pickable member phases. `>= 1` = has work, `0` = drained (confirmed empty),
    `None` = uncertain (the oracle withholds rather than inventing). The adapter
    reduces the oracle verdict to this slice at the I/O edge; the kernel only READS
    it (the same don't-recompute seam as `health`/`scoreboard`/`lane_outcome`).

    `basis`: which oracle produced it (data-trust-floor: name the artefact).

    SAFETY ASYMMETRY (load-bearing): ONLY a CONFIRMED `0` ever changes anything —
    `None` and `>= 1` leave the decision byte-identical to before this field
    existed (the dormant `None` default => the surfacing rung is inert). So the
    worst case of a wrong oracle is "we surfaced/launched anyway, same as today";
    the change can never CAUSE a launch that wasn't already going to happen.
    """

    count: Optional[int] = None
    basis: str = ""                  # e.g. "fanout_state._build_pick_oracle"


# ───────────────────────────── the input type ────────────────────────────────
@dataclass(frozen=True)
class ScoutState:
    """Every pure signal `choose()` needs, as a field. Nothing is gathered inside
    `choose` — the host adapter does all I/O and reduces each time-derived signal
    to a scalar/bool/tuple here (so `choose` is timeless and the override-log
    acceptance tests are hermetic one-liners).

    The Phase-1 LIVE signals are `health` + the two named new signals
    (`same_day_unstick_count`/`fixes_landing_since_unstick`, plus
    `operator_owned_cause`). The rest are present-but-dormant: their defaults
    (empty tuple / None / False) make their rungs inert, so Phase 2 wires them in
    with ZERO schema change.
    """

    # request context (constant for this choice)
    scope: Optional[str] = None
    focus: str = "not-started"
    gate: str = "hard"                                   # hard|soft|drive

    # THE ONE input that lets choose() emit STOP. Scout NEVER self-STOPs on an
    # analytic/portfolio signal (a noisy scoreboard, an open decision, a saturated
    # /unstick) — those are ROUTING signals, handled by re-routing to a productive
    # activity for that context (see choose()). The only legitimate reason for the
    # CHOOSER to refuse to launch anything is that the host genuinely *cannot*
    # launch more right now — a hard resource/capacity wall the adapter measured
    # (RAM/slot pool exhausted, the spawn cap hit, a usage window truly spent).
    # Default False ⇒ choose() never STOPs in normal operation. The adapter sets
    # this ONLY from a measured can't-launch condition, never from history shape.
    resource_blocked: bool = False
    resource_block_reason: str = ""                      # operator-facing, when resource_blocked

    # The pluggable loop-STOP seam (`dos.stop_policy`). OPTIONAL: when None, the
    # scout skips the STOP-policy rung entirely and behaves byte-identically to
    # before the seam (an open decision is evidence-only — the 2026-06-03 default).
    # An adapter that wants a host to be able to turn a decision class into a real
    # halt resolves a policy at the call boundary (`active_stop_policy`) and passes
    # it here; `choose` runs it via `stop_under_resource_floor`, so a host policy
    # can only ADD a STOP on top of the `resource_blocked` floor, never remove it,
    # and a failing policy degrades to DEFER (never a spurious halt). Mechanism is
    # the kernel's; whether to stop on THIS decision is the host's choice.
    stop_policy: Optional[StopPolicy] = None

    # THE dos.health reuse seam (input-field): the adapter ran dos.health.check
    # and put the verdict here. Optional only for the degenerate-adapter
    # fail-safe (a None verdict ⇒ PROCEED, matching health's git-fail→[]→proceed).
    health: Optional[HealthVerdict] = None

    # escalated + unanswered operator decision IDs, e.g. ("362",). A SURFACED note
    # on the dispatch decision — NOT a STOP. The decision is already escalated (the
    # JO path carried it to the operator); halting the whole loop on it is the
    # over-reach the operator called out. Scout dispatches the lane's other work and
    # names the open decision in evidence.
    open_escalated_decisions: tuple[str, ...] = field(default_factory=tuple)

    # the /unstick-saturation signals. These no longer manufacture a STOP — a
    # saturated /unstick is a ROUTING signal: when sweeps are not landing, scout
    # routes AWAY from /unstick (to dispatch), because the doom-loop was *caused by*
    # re-routing to UNSTICK. "Route for that specific context" = break the unstick
    # loop by doing real work, never halt. They gate rule 4 (suppress a fresh
    # /unstick once saturated) and ride along in evidence.
    operator_owned_cause: bool = False                  # set from recurring_wedge.operator_decision
    #                                                     OR a real decision-ownership check — NOT a
    #                                                     brittle 'operator' substring of cause_key.
    same_day_unstick_count: int = 0
    latest_unstick_stamp: str = ""
    fixes_landing_since_unstick: bool = True             # default True = conservative: absent
    #                                                     evidence never suppresses a fresh /unstick.

    # rules 6/7
    open_soaks: tuple[str, ...] = field(default_factory=tuple)
    replan_due: bool = False
    replan_desc: str = ""

    # ── replan-redundancy signals (route-forward, never a STOP) ───────────────
    # A `/replan` runs UNSCOPED (portfolio-wide) and so holds NO lane lease — the
    # lease registry the health gate reads cannot see one in flight. Two of these
    # gardening the same global docs/_plans/ tree at once is pure waste: the second
    # races the first on plans.yaml / findings-queue / execution-state and (per the
    # job repo's replan-state.yaml field notes) ends up DEFERRING its own unsafe
    # writes "due to concurrent fleet". So scout must not route a redundant /replan.
    # Per the 2026-06-03 directive this is a ROUTING signal, not a STOP: when a
    # replan is redundant, rule 7 falls through to DISPATCH (rule 9) carrying the
    # signal as surfaced evidence — do real lane work instead of a duplicate sweep.
    #
    # `replan_in_flight`: a /replan child is running RIGHT NOW (measured by the
    #   adapter — scan live chained-run/loop dirs for an active replan). Suppresses
    #   ALL replan routing, including a health ROUTE_REPLAN: a concurrent sweep will
    #   garden whatever the lane needs; a second one only collides.
    # `recent_replan_unproductive`: a /replan finished very recently and produced
    #   nothing (0 closed / 0 surfaced — the zero-surface-streak shape). Suppresses
    #   ONLY the cooldown arm (`replan_due`), NOT a fresh health ROUTE_REPLAN: if the
    #   lane is soak/data-gated right now, a recent empty sweep doesn't change that
    #   dispatch still can't proceed — but it DOES mean "≥3 fanouts elapsed" alone is
    #   not a reason to re-sweep a portfolio a near-identical sweep just gardened.
    # `recent_replan_ran`: a /replan finished very recently REGARDLESS of yield
    #   (productive OR empty). The cooldown arm (`replan_due`) keys on
    #   `fanouts_since_last_run >= 3`, and that counter does NOT reset when a /replan
    #   runs — it tracks fanout activity, not sweeps. So a replan that JUST gardened
    #   the portfolio still leaves the cooldown armed, and the cooldown arm re-routes
    #   /replan forever (`last_run=0h ago` but `fanouts_since=3`) — the first-iter-
    #   replan re-route loop that BLOCKED a real loop (job-repo loop 20260606T170657Z
    #   "cooldown never reset"). `recent_replan_unproductive` only caught the EMPTY
    #   case; a PRODUCTIVE recent sweep (added >=1) slipped through. This is the
    #   superset signal: a recent sweep of EITHER yield vetoes ONLY the cooldown arm
    #   (same scope as the unproductive veto — a live health ROUTE_REPLAN is never
    #   suppressed: if the lane is gated NOW a recent sweep can't change that). The
    #   fix for the loop is "a sweep already ran this cooldown window → dispatch the
    #   refilled work, don't re-sweep purely because the fanout counter is still high".
    replan_in_flight: bool = False
    recent_replan_unproductive: bool = False
    recent_replan_ran: bool = False
    recent_replan_desc: str = ""        # operator-facing, when either fires

    # scoreboard 7d shape — a fleet-level ANALYTIC artefact. Surfaced as evidence on
    # the dispatch decision; NEVER a STOP (a noisy rolling scoreboard says nothing
    # about whether THIS lane has clean isolated work right now). Typed slice, never
    # a raw dict (data-trust-floor).
    scoreboard: Optional[ScoreboardShape] = None

    # PHASE-2 RESERVED inputs (None in P1 ⇒ their menu rungs never fire ⇒
    # byte-identical to P1).
    recurring_wedge: Optional[WedgeSignal] = None       # → rule-2 operator_owned + future JUDGE
    class_drift: Optional[bool] = None                  # → JUDGE
    trajectory_pathology: Optional[bool] = None         # → TRAJECTORY_AUDIT
    plan_surface_drift: Optional[bool] = None           # → PLAN_AUDIT
    inverted_priority: Optional[bool] = None            # → TAIL_WAG

    # CLOSED-LOOP weighting (operator directive 2026-06-04): when the lane has
    # pickable loop-closing work (convert-observation-into-mechanism — an oracle,
    # a gate, a learned-answer promoter), bias the in-lane `focus` toward it on
    # the rule-9 DISPATCH terminal. Reserved-input convention: default None ⇒ the
    # bias never fires ⇒ byte-identical to before. The adapter measures which
    # pickable items are loop-closers (from plan-meta / findings tags) and hands
    # this slice in; the kernel only weights it (it never decides what counts as
    # closed-loop, and a lane with closed-loop work still passes every gate above).
    closed_loop: Optional[ClosedLoopSignal] = None      # → rule-9 focus bias

    # OUTCOME-DRIVEN ROUTING (operator directive 2026-06-09, the 10x self-improving
    # loop goal): this lane's recent ship history. When the lane has shipped 0 of
    # its last K runs BUT still has pickable work, pre-empt with /replan (re-
    # prioritize the backlog) instead of dispatching another non-shipping child —
    # moving the unproductivity discovery from inside repeated $10+ children to a
    # $0 pre-acquire read. Reserved-input convention: default None ⇒ the rung is
    # inert ⇒ byte-identical to before. ROUTES, never STOPs (a lane that keeps
    # shipping nothing is a routing signal; if /replan also yields nothing the
    # existing drained-twice machinery stops the loop). The adapter reduces the
    # decision→outcome ledger to this per-lane slice; the kernel only routes on it.
    lane_outcome: Optional[LaneOutcomeShape] = None     # → rule-10 replan pre-empt

    # YIELD-AWARE SCOUT (docs/_design/dispatch-yield-aware-scout-plan.md, Phase 1):
    # the pick-oracle verdict for the lane scout is about to lease, reduced to a
    # typed slice at the I/O edge. Phase 1 only SURFACES a confirmed-drain
    # (count == 0) as one extra evidence line on the DISPATCH terminal — the
    # decision STAYS dispatch (Phase 2 promotes it to a REPLAN-refill rung). The
    # reserved-input convention: default None ⇒ the surfacing is inert ⇒
    # byte-identical to before this field existed. The safety asymmetry is the
    # whole point — ONLY a confident 0 surfaces; None (uncertain) and >= 1 (has
    # work) preserve today's exact decision.
    lane_yield: Optional[LaneYield] = None              # → rule-9 confirmed-drain surfacing

    @property
    def health_action(self) -> HealthAction:
        """The sibling verdict's action, or PROCEED if no verdict was supplied
        (the degenerate-adapter fail-safe — mirrors health's own
        git-failure→proceed direction: a missing input never invents a route)."""
        return self.health.action if self.health is not None else HealthAction.PROCEED

    def __post_init__(self) -> None:
        if self.gate not in ("hard", "soft", "drive"):
            raise ValueError(f"unknown gate {self.gate!r} — expected hard|soft|drive")


# ───────────────────────────── the chooser ───────────────────────────────────
def choose(state: ScoutState) -> ScoutDecision:
    """Choose the next activity. PURE — no I/O, no clock, no oracle/health call.

    DESIGN INVARIANT (operator directive 2026-06-03): **scout NEVER self-STOPs on an
    analytic/portfolio signal.** A noisy 7d scoreboard, an open operator decision,
    and a saturated `/unstick` are all ROUTING signals, not halt signals — the
    chooser's job is to pick *what to start*, so when a context looks bad it routes
    to a *productive* activity for that context, it does not give up. The ONLY thing
    that makes the chooser refuse to launch anything is a measured can't-launch wall
    the host hit (`state.resource_blocked` — RAM/slot pool exhausted, spawn cap, a
    usage window truly spent). Everything else routes forward.

    What this means for the conditions that USED to STOP:
      * scoreboard zero-ship storm → it's a fleet-stats artefact; surface it as
        evidence on the DISPATCH decision, never block (the storm says nothing about
        whether THIS lane has clean isolated work right now — that's the very thing
        the operator pointed out).
      * open escalated operator decision → already escalated to the operator; scout
        DISPATCHes the lane's other work and names the decision in evidence. Halting
        the whole loop on a decision that's already in the operator's queue is the
        over-reach.
      * `/unstick` saturated / not landing (the doom-loop) → route AWAY from
        `/unstick` (suppress the fresh-unstick rung) and DISPATCH instead. The
        doom-loop was *caused by* re-routing to UNSTICK; the fix is to break the
        loop by doing real work, not to halt.

    Decision order (first-match-wins; `rule_id` pins which rung fired):

      0. resource_blocked (measured can't-launch)       → STOP   (the ONLY STOP)
      4. route_unstick (fresh, NOT saturated)           → UNSTICK
      5. overlap_block                                  → WAIT   (re-pick disjoint)
      6. route_replan AND soaks open                    → WAIT   (replan no-ops vs soak)
      7. (route_replan OR replan-due) AND NOT redundant → REPLAN
         (redundant = a /replan is in flight, or one just ran empty — then fall
          through to DISPATCH; in-flight vetoes both replan arms, recently-empty
          vetoes only the cooldown arm)
      [Phase-2 menu annex]
      9. default                                        → DISPATCH
         (open-decision / scoreboard-storm / unstick-saturation / vetoed-replan all
          funnel here, carried as surfaced evidence — they inform the operator, they
          don't block)

    The rule_id numbers keep their concept-doc §3 identities (0 is the new
    resource-STOP rung; the old STOP rungs 1/2/3/8 are RETIRED as STOP triggers and
    fold into rung 9's evidence) so a test pins exactly which rung fired.
    """
    h = state.health
    ha = state.health_action
    foc = state.focus or "not-started"
    sb = state.scoreboard

    # ── 0. THE ONLY STOP: the host measured a can't-launch wall ───────────────
    #    Resource/capacity exhaustion (RAM/slot pool, spawn cap, spent usage
    #    window). This is the *sole* condition under which the CHOOSER refuses to
    #    launch anything — every analytic/portfolio signal below routes forward
    #    instead. confidence=HIGH (a measured wall is not a judgement call) but
    #    needs_lane is False (STOP never leases).
    if state.resource_blocked:
        why = state.resource_block_reason or "host cannot launch more work right now"
        return ScoutDecision(
            activity=ScoutActivity.STOP, rule_id=0, confidence=Confidence.HIGH,
            scope=state.scope, focus=foc,
            cause_key="resource_blocked",
            reason=(f"can't launch more — {why}. The only condition under which the "
                    f"scout halts; wait for capacity to free, then re-run."),
            evidence=(f"resource_blocked: {why}",),
        )

    # ── 1. the host's pluggable STOP policy (opt-in; under the resource floor) ──
    #    The seam that makes "stop-on-this-decision" a host CHOICE, not a kernel
    #    constant. Skipped entirely when no policy is wired (the default) — so the
    #    reference host's behavior is unchanged (an open decision is evidence-only,
    #    rule 9 below). When a policy IS wired, it is run through
    #    `stop_under_resource_floor`, which (a) cannot dilute the rule-0 floor (we
    #    already returned above if resource_blocked) and (b) fail-DEFERs a raising/
    #    bad-return policy, so it can only ADD a halt, never manufacture or suppress
    #    one. confidence rides from the verdict's own signal: a policy reads live
    #    state, so a HIGH stop auto-halts while a non-HIGH one is surfaced via the
    #    adapter's `confidence: low → AskUserQuestion` path (the STOP-discipline).
    if state.stop_policy is not None:
        sv = stop_under_resource_floor(state.stop_policy, state, config=None)
        if sv.should_stop:
            return ScoutDecision(
                activity=ScoutActivity.STOP, rule_id=1, confidence=Confidence.HIGH,
                scope=state.scope, focus=foc,
                cause_key=sv.cause_key or "stop_policy",
                reason=(sv.reason or "host STOP policy halted the loop on a pending "
                        "decision (configured to halt on this class)."),
                evidence=sv.evidence or ("stop_policy",),
            )

    # Is /unstick saturated? (the former rule-2/rule-3 doom-loop conditions). When
    # true we DO NOT route to a fresh /unstick (rule 4) — re-unsticking is exactly
    # what caused the doom-loop. Instead we fall through to DISPATCH and carry the
    # saturation as surfaced evidence. The operator's "route for that specific
    # context" = break the unstick loop by doing real work.
    unstick_op_owned_saturated = (
        ha is HealthAction.ROUTE_UNSTICK and state.same_day_unstick_count >= 2
        and (state.operator_owned_cause or (h is not None and "operator" in h.cause_key))
    )
    unstick_doomloop_saturated = (
        ha is HealthAction.ROUTE_UNSTICK and state.same_day_unstick_count >= 3
        and not state.fixes_landing_since_unstick
    )
    unstick_saturated = unstick_op_owned_saturated or unstick_doomloop_saturated

    # ── 4. route_unstick on a FRESH recurring cause → /unstick first ──────────
    #    Suppressed once saturated (above) — a saturated cause funnels to DISPATCH.
    if ha is HealthAction.ROUTE_UNSTICK and not unstick_saturated:
        return ScoutDecision(
            activity=ScoutActivity.UNSTICK, rule_id=4, confidence=Confidence.HIGH,
            scope=state.scope, focus=foc,
            reason=(f"recurring structural blocker on {(h.cause_key if h else '')!r} "
                    f"({(h.reason[:120] if h else '')}). First sweep on this cause is "
                    f"worth it before launching a child."),
            evidence=(tuple(h.evidence[:4]) if h and h.evidence
                      else ((h.cause_key,) if h and h.cause_key else ())),
        )

    # ── 5. overlap_block → re-pick a disjoint lane (wait) ─────────────────────
    if ha is HealthAction.OVERLAP_BLOCK:
        ol = h.overlap_lane if h else ""
        return ScoutDecision(
            activity=ScoutActivity.WAIT, rule_id=5, confidence=Confidence.HIGH,
            scope=state.scope, focus=foc,
            cause_key="lane_overlap_collision",
            reason=(f"granted lane's tree overlaps a live foreign lease "
                    f"{ol!r} — {(h.reason[:140] if h else '')}. Re-pick a disjoint "
                    f"lane or wait for the foreign lease to release."),
            evidence=(tuple(h.evidence[:4]) if h and h.evidence
                      else ((f"overlap:{ol}",) if ol else ("overlap",))),
        )

    # ── 6. route_replan but soaks are open → replan no-ops; wait ──────────────
    if ha is HealthAction.ROUTE_REPLAN and state.open_soaks:
        soaks = state.open_soaks[:3]
        return ScoutDecision(
            activity=ScoutActivity.WAIT, rule_id=6, confidence=Confidence.HIGH,
            scope=state.scope, focus=foc,
            cause_key="lane_soak_or_data_gated",
            reason=(f"lane is soak/data-gated and {len(state.open_soaks)} soak "
                    f"window(s) are open ({', '.join(soaks)}) — /replan gardens "
                    f"nothing a soak gate cares about; the window closes on time. Wait."),
            evidence=tuple(f"open-soak:{s}" for s in soaks),
        )

    # ── 7. route_replan, or replan otherwise due by cooldown → /replan ────────
    #    UNLESS the replan would be redundant (a concurrent sweep is in flight, or
    #    one already ran this cooldown window) — then fall through to DISPATCH (rule
    #    9), carrying the redundancy as surfaced evidence. A /replan holds no lane
    #    lease, so the only way scout learns "another sweep is happening / already
    #    happened" is these adapter-measured signals; routing forward (not halting)
    #    is the 2026-06-03 directive. `replan_in_flight` vetoes BOTH replan arms;
    #    `recent_replan_ran` and `recent_replan_unproductive` veto ONLY the cooldown
    #    arm (a live health ROUTE_REPLAN means the lane is gated NOW — a recent sweep
    #    can't clear that, so it still routes to /replan).
    #
    #    The cooldown-arm veto is `recent_replan_ran OR recent_replan_unproductive`:
    #    the cooldown keys on `fanouts_since_last_run >= 3`, a counter that does NOT
    #    reset when a /replan runs, so a sweep that JUST gardened the portfolio (of
    #    EITHER yield) leaves the cooldown armed and the arm re-routes /replan forever
    #    — the first-iter-replan re-route loop. `recent_replan_unproductive` only
    #    caught the empty case; `recent_replan_ran` is the superset that also catches
    #    a PRODUCTIVE recent sweep (the case that BLOCKED a real loop). Either ⇒ the
    #    cooldown arm is spent for this window → DISPATCH the refilled work instead of
    #    re-sweeping purely because the fanout counter is still high. (`recent_replan_
    #    unproductive` is kept in the OR for back-compat: an adapter that sets only it
    #    — never the new superset — still vetoes, so no regression.)
    health_routed_replan = ha is HealthAction.ROUTE_REPLAN
    cooldown_replan = state.replan_due and not health_routed_replan
    recent_replan_this_window = (
        state.recent_replan_ran or state.recent_replan_unproductive)
    replan_vetoed = state.replan_in_flight or (
        cooldown_replan and recent_replan_this_window)
    if (health_routed_replan or state.replan_due) and not replan_vetoed:
        why = ("health gate route_replan" if health_routed_replan
               else f"replan cooldown elapsed ({state.replan_desc})")
        return ScoutDecision(
            activity=ScoutActivity.REPLAN, rule_id=7, confidence=Confidence.HIGH,
            scope=None, focus=None,  # /replan is portfolio-wide
            reason=f"{why} — sweep findings + garden the portfolio before more dispatch.",
            evidence=(state.replan_desc or "replan_due",
                      f"health={ha.value}"),
        )

    # ── 10. OUTCOME-DRIVEN pre-empt (route, never STOP) ───────────────────────
    #    A lane that has shipped 0 of its last K runs BUT still has pickable work
    #    is not empty (rung 8'/yield handles empty) and not a fleet-stats artefact
    #    (the scoreboard funnel handles that, never blocking) — it has work that
    #    keeps NOT shipping. The lever is /replan (re-prioritize THIS lane's
    #    backlog) BEFORE dispatching yet another heavy non-shipping child. This is
    #    a ROUTING change, never a STOP: it picks a different activity, and if
    #    /replan also yields nothing the existing drained-twice machinery stops the
    #    loop. Honors the 2026-06-03 STOP-discipline (an analytic signal routes,
    #    never halts). Inert when the slice is absent/insufficient (window=0 ⇒
    #    is_actionable False ⇒ falls through ⇒ byte-identical to before).
    lo = state.lane_outcome
    if (lo is not None and lo.is_actionable
            and lo.shipped_runs == 0 and lo.drained_runs < lo.runs):
        return ScoutDecision(
            activity=ScoutActivity.REPLAN, rule_id=10, confidence=Confidence.HIGH,
            scope=None, focus=None,  # /replan is portfolio-wide (matches rung 7)
            reason=(f"lane {state.scope!r} shipped 0 of its last {lo.runs} runs "
                    f"(top block: {lo.top_blocked_cause or 'none'}) yet has pickable "
                    "work — re-prioritize the backlog before dispatching another "
                    "non-shipping child."),
            evidence=(f"lane_ship_rate=0/{lo.runs}",
                      f"top_blocked_cause={lo.top_blocked_cause or 'none'}"),
        )

    # ── Phase-2 menu annex (returns None in Phase 1 → falls through to rule 9) ─
    p2 = _phase2_menu(state, foc)
    if p2 is not None:
        return p2

    # ── 9. default: DISPATCH — and the funnel for every retired-STOP condition ─
    #    open-decision / scoreboard-storm / unstick-saturation all land here. They
    #    are SURFACED (named in evidence + reason so the operator sees them) but
    #    they do NOT block — the chooser's answer to "what should I start" is "the
    #    next pick", with these as context.
    notes: list[str] = []
    ev: list[str] = [f"health={ha.value}", f"replan_due={state.replan_due}"]
    # A replan was vetoed above (in-flight, or one already ran this cooldown window)
    # and we funneled here: name it so the operator sees DISPATCH was chosen
    # *instead of* a duplicate/loop sweep.
    if (state.replan_in_flight or state.recent_replan_ran
            or state.recent_replan_unproductive):
        if state.replan_in_flight:
            notes.append(f"a /replan is already in flight"
                         f"{f' ({state.recent_replan_desc})' if state.recent_replan_desc else ''}"
                         f" — a concurrent sweep gardens the portfolio; dispatching "
                         f"the lane's work instead of racing a second /replan")
            ev.append("replan_in_flight=1")
        elif state.recent_replan_unproductive:
            notes.append(f"a /replan ran recently to no effect"
                         f"{f' ({state.recent_replan_desc})' if state.recent_replan_desc else ''}"
                         f" — cooldown (≥3 fanouts) alone is not a reason to re-sweep "
                         f"a portfolio a near-identical sweep just gardened; dispatching")
            ev.append("recent_replan_unproductive=1")
        elif state.recent_replan_ran:
            # The productive-recent-sweep case: a /replan already ran this cooldown
            # window and refilled the backlog, but `fanouts_since_last_run` does not
            # reset on a sweep so the cooldown stayed armed. Routing /replan again
            # would re-enter the first-iter-replan loop; dispatch the refilled work.
            notes.append(f"a /replan already ran this cooldown window"
                         f"{f' ({state.recent_replan_desc})' if state.recent_replan_desc else ''}"
                         f" — the fanout counter (≥3) does not reset on a sweep, so the "
                         f"cooldown stayed armed after a sweep already gardened the "
                         f"portfolio; dispatching the refilled work, not re-sweeping")
            ev.append("recent_replan_ran=1")
    if state.open_escalated_decisions:
        ids = state.open_escalated_decisions[:4]
        notes.append(f"{len(state.open_escalated_decisions)} open operator "
                     f"decision(s) (#{', #'.join(ids)}) — already escalated; "
                     f"dispatching the lane's other work")
        ev.extend(f"open-decision:#{d}" for d in ids)
    if unstick_saturated:
        notes.append(f"/unstick saturated ({state.same_day_unstick_count}× today, "
                     f"last {state.latest_unstick_stamp}"
                     f"{', not landing' if unstick_doomloop_saturated else ''}) — "
                     f"routing away from re-unsticking, dispatching instead")
        ev.append(f"unstick_saturated={state.same_day_unstick_count}")
    if sb is not None and sb.runs_7d >= 20 and sb.zero_ship_frac >= 0.75:
        notes.append(f"7d scoreboard {sb.zero_ship_7d}:{sb.runs_7d} "
                     f"({sb.zero_ship_frac * 100:.0f}% zero-ship, "
                     f"{sb.false_drain_runs} false-drains) — fleet-stats noise, not "
                     f"a per-lane signal; if it persists the lever is "
                     f"concurrency / lane allocation")
        ev.append(f"scoreboard.7d={sb.zero_ship_7d}:{sb.runs_7d}")
    else:
        ev.append(f"scoreboard.7d={sb.zero_ship_7d}:{sb.runs_7d}" if sb else "scoreboard=none")

    # ── closed-loop focus bias (operator directive 2026-06-04) ────────────────
    #    When the lane has pickable loop-closing work, prefer it WITHIN the lane:
    #    promote `focus` to "closed-loop-first" and surface why. Closed-loop work
    #    (convert-observation-into-mechanism) pays down the failure rate, where
    #    open-loop work re-pays it every run — so given a clean lane, it is the
    #    higher-leverage pick. Guard: a `stale-stamp` focus is a correctness fix
    #    that must not be displaced (a stale soft-claim blocks picks), so it wins;
    #    every other default focus yields to closed-loop. The bias only re-orders
    #    work WITHIN an already-chosen DISPATCH — it never manufactures a route.
    cl = state.closed_loop
    foc_out = foc
    if cl is not None and cl.is_actionable and foc != "stale-stamp":
        foc_out = "closed-loop-first"
        notes.append(
            f"{cl.count} closed-loop item(s) pickable"
            f"{f' (top: {cl.top_item})' if cl.top_item else ''} — preferring "
            f"loop-closing work (converts a recurring failure into a durable "
            f"mechanism; pays down the rate instead of re-paying it each run)"
        )
        ev.append(
            f"closed_loop={cl.count}"
            + (f":{cl.top_item}" if cl.top_item else "")
        )

    # Low (but non-zero) per-lane ship-rate: SURFACE it on the DISPATCH decision
    # (rung-10 only pre-empts on a hard 0/K; a lane shipping 1/K still dispatches,
    # but the operator should see the weak rate). Pure evidence — no behavior
    # change, the same "surface, don't block" treatment the scoreboard-storm gets.
    if (lo is not None and lo.is_actionable and lo.shipped_runs > 0
            and lo.ship_rate < 0.34):
        notes.append(f"lane ship-rate {lo.shipped_runs}/{lo.runs} "
                     f"({lo.ship_rate * 100:.0f}%) over recent runs — weak; if it "
                     "persists, a /replan to re-prioritize may beat more dispatch")
        ev.append(f"lane_ship_rate={lo.shipped_runs}/{lo.runs}")

    # ── yield-aware surfacing (Phase 1 — docs/_design/dispatch-yield-aware-scout-plan.md) ──
    #    The lane scout is about to DISPATCH was checked against the SAME pick-oracle
    #    the arbiter consults (reduced to a LaneYield slice at the I/O edge). Phase 1
    #    SURFACES a CONFIRMED drain (count == 0) as exactly ONE evidence line — the
    #    decision STAYS dispatch (Phase 2 promotes this to a REPLAN-refill rung once
    #    the monitor has proven prediction == outcome). The safety asymmetry is
    #    load-bearing: ONLY a confident 0 adds anything; None (uncertain) and >= 1
    #    (has work) leave the decision byte-identical, so a wrong oracle can only ever
    #    have "surfaced anyway", never suppressed work. So the operator/loop now SEES
    #    the predicted drain at second-zero, before paying a child to discover it.
    ly = state.lane_yield
    if ly is not None and ly.count == 0:
        ev.append(f"lane-yield:0 (confirmed drained — "
                  f"{ly.basis or 'fanout_state._build_pick_oracle'})")

    reason = ("dispatch the next pick — "
              + ("; ".join(notes) if notes
                 else "health gate clean, no escalated decision, replan not due, "
                      "scoreboard not in a storm"))
    return ScoutDecision(
        activity=ScoutActivity.DISPATCH, rule_id=9, confidence=Confidence.HIGH,
        scope=state.scope, focus=foc_out, gate=state.gate,
        reason=reason,
        evidence=tuple(ev),
    )


def _phase2_menu(state: ScoutState, foc: str) -> Optional[ScoutDecision]:
    """The wider-menu rungs — bounded ANNEX so the live 9-rule spine above stays
    legible (the god-skill guard, made structural). Returns None in Phase 1: every
    reserved input defaults None, so every rung here is skipped and `choose` is
    byte-identical to the Phase-1 spine. Phase 2 adds opt-in rungs, each guarded by
    `if state.<reserved_field> is not None:` — a None field is a skipped rung, never
    an accidental route.

    Phase-2 intent (not yet wired):
      - state.class_drift           → JUDGE  (class-cycle: ACTIVE count low / class drift)
      - state.trajectory_pathology  → TRAJECTORY_AUDIT (cross-run token-waste / read-loop)
      - state.plan_surface_drift    → PLAN_AUDIT (plan-meta vs ship-oracle drift)
      - state.inverted_priority     → TAIL_WAG (inverted-priority driver suspected)
    """
    return None


# ───────────────────────────── CLI (I/O composition) ─────────────────────────
# Exit code per activity so a shell caller can branch on the exit alone. The
# unstick/replan/overlap codes (3/4/5) are kept legible alongside dos.health's
# (3 unstick, 4 replan); STOP gets 6 (health's overlap exit), and a LOW-confidence
# pick of ANY activity is FLIPPED to 6 (surface) at the boundary — the
# JO_AUTO_ACCEPT actuation line lives in the host, but the CLI exposes the bit.
_ACTIVITY_EXIT = {
    ScoutActivity.DISPATCH: 0,
    ScoutActivity.UNSTICK: 3,
    ScoutActivity.REPLAN: 4,
    ScoutActivity.WAIT: 5,
    ScoutActivity.STOP: 6,
    ScoutActivity.NEXT_UP: 7,
    ScoutActivity.JUDGE: 8,
    ScoutActivity.PLAN_AUDIT: 9,
    ScoutActivity.TAIL_WAG: 10,
    ScoutActivity.TRAJECTORY_AUDIT: 11,
}


def check(
    *,
    scope: Optional[str] = None,
    focus: str = "not-started",
    gate: str = "hard",
    resource_blocked: bool = False,
    resource_block_reason: str = "",
    health: Optional[HealthVerdict] = None,
    open_escalated_decisions: tuple[str, ...] = (),
    operator_owned_cause: bool = False,
    same_day_unstick_count: int = 0,
    latest_unstick_stamp: str = "",
    fixes_landing_since_unstick: bool = True,
    open_soaks: tuple[str, ...] = (),
    replan_due: bool = False,
    replan_desc: str = "",
    replan_in_flight: bool = False,
    recent_replan_unproductive: bool = False,
    recent_replan_ran: bool = False,
    recent_replan_desc: str = "",
    scoreboard: Optional[ScoreboardShape] = None,
    closed_loop: Optional[ClosedLoopSignal] = None,
    lane_outcome: Optional[LaneOutcomeShape] = None,
    lane_yield: Optional[LaneYield] = None,
) -> ScoutDecision:
    """Thin in-kernel composition: ALREADY-gathered field values in (the host
    adapter did the I/O), one `ScoutDecision` out. Does no I/O itself — it just
    builds a `ScoutState` and calls `choose`, giving a `dos.health` reader the
    exact three-tier shape (`lane_health` / `check` / `cmd_check`). Phase-2 kwargs
    are added here when their rungs land."""
    return choose(ScoutState(
        scope=scope, focus=focus, gate=gate,
        resource_blocked=resource_blocked,
        resource_block_reason=resource_block_reason,
        health=health,
        open_escalated_decisions=tuple(open_escalated_decisions),
        operator_owned_cause=operator_owned_cause,
        same_day_unstick_count=same_day_unstick_count,
        latest_unstick_stamp=latest_unstick_stamp,
        fixes_landing_since_unstick=fixes_landing_since_unstick,
        open_soaks=tuple(open_soaks),
        replan_due=replan_due, replan_desc=replan_desc,
        replan_in_flight=replan_in_flight,
        recent_replan_unproductive=recent_replan_unproductive,
        recent_replan_ran=recent_replan_ran,
        recent_replan_desc=recent_replan_desc,
        scoreboard=scoreboard,
        closed_loop=closed_loop,
        lane_outcome=lane_outcome,
        lane_yield=lane_yield,
    ))


def decision_to_dict(d: ScoutDecision) -> dict:
    return {
        "activity": d.activity.value,
        "rule_id": d.rule_id,
        "confidence": d.confidence.value,
        "auto_runnable": d.auto_runnable,
        "needs_lane": d.needs_lane,
        "scope": d.scope,
        "focus": d.focus,
        "gate": d.gate,
        "reason": d.reason,
        "cause_key": d.cause_key,
        "evidence": list(d.evidence),
    }


def _health_from_dict(obj: dict | None) -> Optional[HealthVerdict]:
    """Rebuild a HealthVerdict from the JSON `dos health` emits (verdict_to_dict),
    or None. The host normally passes the verdict in-process; the CLI path lets a
    shell caller pipe `dos health` JSON straight in."""
    if not obj:
        return None
    action_raw = obj.get("action", "proceed")
    try:
        action = HealthAction(action_raw)
    except ValueError:
        action = HealthAction.PROCEED
    return HealthVerdict(
        action=action,
        reason=str(obj.get("reason", "")),
        cause_key=str(obj.get("cause_key", "")),
        runs_considered=int(obj.get("runs_considered", 0) or 0),
        blocker_runs=int(obj.get("blocker_runs", 0) or 0),
        overlap_lane=str(obj.get("overlap_lane", "")),
        evidence=tuple(obj.get("evidence", []) or ()),
    )


def _state_from_json(blob: str) -> ScoutState:
    """Build a ScoutState from a JSON object (the fixture-driven CLI). `health` is
    a nested `dos health` verdict dict; `scoreboard` a nested shape dict; the rest
    are scalars/lists matching the ScoutState fields."""
    obj = json.loads(blob) if blob else {}
    sb = obj.get("scoreboard")
    cl = obj.get("closed_loop")
    return ScoutState(
        scope=obj.get("scope"),
        focus=obj.get("focus", "not-started"),
        gate=obj.get("gate", "hard"),
        resource_blocked=bool(obj.get("resource_blocked", False)),
        resource_block_reason=str(obj.get("resource_block_reason", "")),
        health=_health_from_dict(obj.get("health")),
        open_escalated_decisions=tuple(obj.get("open_escalated_decisions", []) or ()),
        operator_owned_cause=bool(obj.get("operator_owned_cause", False)),
        same_day_unstick_count=int(obj.get("same_day_unstick_count", 0) or 0),
        latest_unstick_stamp=str(obj.get("latest_unstick_stamp", "")),
        fixes_landing_since_unstick=bool(obj.get("fixes_landing_since_unstick", True)),
        open_soaks=tuple(obj.get("open_soaks", []) or ()),
        replan_due=bool(obj.get("replan_due", False)),
        replan_desc=str(obj.get("replan_desc", "")),
        replan_in_flight=bool(obj.get("replan_in_flight", False)),
        recent_replan_unproductive=bool(obj.get("recent_replan_unproductive", False)),
        recent_replan_ran=bool(obj.get("recent_replan_ran", False)),
        recent_replan_desc=str(obj.get("recent_replan_desc", "")),
        scoreboard=(ScoreboardShape(
            runs_7d=int(sb.get("runs_7d", 0) or 0),
            zero_ship_7d=int(sb.get("zero_ship_7d", 0) or 0),
            false_drain_runs=int(sb.get("false_drain_runs", 0) or 0),
            runs_shipped=int(sb.get("runs_shipped", 0) or 0),
        ) if isinstance(sb, dict) else None),
        closed_loop=(ClosedLoopSignal(
            available=bool(cl.get("available", False)),
            count=int(cl.get("count", 0) or 0),
            top_item=str(cl.get("top_item", "")),
        ) if isinstance(cl, dict) else None),
    )


def cmd_check(args: argparse.Namespace) -> int:
    """`dos scout --state-json '{...}'` → decision JSON + exit-per-activity.

    The whole state arrives as JSON (so even the CLI is fixture-driven and the
    host's gathering stays on the host side). A LOW-confidence pick is flipped to
    exit 6 (surface) regardless of activity — the JO_AUTO_ACCEPT boundary made
    visible to a shell caller."""
    state = _state_from_json(args.state_json)
    d = choose(state)
    print(json.dumps(decision_to_dict(d), indent=2, sort_keys=True))
    if not d.auto_runnable:
        return 6  # LOW ⇒ surface, never auto-run (the reversibility gate)
    return _ACTIVITY_EXIT[d.activity]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dos-scout",
        description="Pre-dispatch CHOOSER — pick the next activity BEFORE leasing a lane.",
    )
    p.add_argument(
        "--state-json", default="",
        help="JSON ScoutState: {scope, focus, gate, health:{...dos health...}, "
             "open_escalated_decisions:[], operator_owned_cause, same_day_unstick_count, "
             "latest_unstick_stamp, fixes_landing_since_unstick, open_soaks:[], replan_due, "
             "replan_desc, replan_in_flight, recent_replan_unproductive, recent_replan_ran, recent_replan_desc, "
             "scoreboard:{runs_7d,zero_ship_7d,false_drain_runs,runs_shipped}}",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return cmd_check(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
