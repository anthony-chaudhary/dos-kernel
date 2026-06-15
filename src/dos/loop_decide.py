"""OC2 — the /dispatch-loop control-flow decision surface (the "one small thing").

`/dispatch-loop`'s SKILL.md is ~1400 lines and ~80 steps; the load-bearing
question — *under what exact conditions does this loop stop?* — was answerable
only by reading Step 3, Step 3.5, and Step 4 together (~210 lines of prose state
transitions). OC2 pulls that loop-level decision into one pure, testable
function so a reader can hold the control flow in their head and verify the stop
conditions without the whole file.

This module is the loop-level layer **above** `gate_classify.gate_policy`:

    gate_classify.classify_packet  →  one packet → one typed Verdict
    gate_classify.gate_policy      →  (Verdict, --gate mode) → one GateAction
    dispatch_loop_decide.decide    →  (LoopState, IterationOutcome) → one LoopDecision
                                      ^ THIS module — composes gate_policy, adds
                                        the counters/streaks/cap the loop carries

`decide()` consumes one iteration's typed outcome plus the carried working-
context counters and returns exactly one decision: continue (with the next mode)
or stop (with a named reason). It is **pure** — no subprocess, no file or git
I/O — for the same reason `gate_policy` is: the loop's stop conditions can be
replay-tested in isolation, away from everything that makes a live /dispatch
iteration cost $10-40.

The five stop conditions, in one place (the whole point of this module):

  1. ITERATION_CAP     — iteration count reached `max_iterations` (default 5).
  2. DRAINED_TWICE     — a DRAIN verdict on the /dispatch immediately after a
                         **productive** /replan that itself followed a DRAIN.
                         /replan tried to refill and could not; the
                         lane/portfolio is genuinely exhausted. (hard gate only —
                         soft/drive stop on the first DRAIN.) FQ-240: an
                         *unproductive* /replan (0 gardening / 0 refill, e.g. the
                         §1.5 no-op skip) does NOT arm this trigger — it never
                         actually attempted a refill, so a DRAIN after it is not
                         "drained twice".
  3. CONSECUTIVE_UNCLEAR — `consecutive_unclear` reached `max_unclear` (default
                         3). The iteration subprocess is failing systematically,
                         not draining a backlog.
  4. RATE_LIMITED      — a usage/rate-limit rejection. Every retry would fail the
                         same way until the window resets; do not burn launches.
  5. LAUNCH_FAILED     — the iteration subprocess never produced a valid init
                         envelope. A repeating launch failure would burn all
                         remaining slots.

Plus the soft/drive gate-policy stops (a true DRAIN or a BLOCKED under
soft/drive), which `decide()` reads straight off `gate_policy`'s GateAction
rather than re-encoding.

⚓ Mechanical contract over prose ([[feedback_mechanical_contract_over_prose]]):
the loop's stop/continue/replan decision is now a mechanism (this function),
not ~80 steps of prose a downstream model is trusted to apply consistently.

⚓ Typed verdict over binary gate ([[feedback_typed_verdict_over_binary_gate]]):
`decide()` composes the existing typed `gate_policy` rather than re-classifying;
the loop-level counters (drained-twice, unclear streak) are the part this layer
adds on top.

The wait-marker budget (`wait_marker_budget`) is the OC2 billing addendum: every
`claude -p` keep-alive marker is its own assistant turn that replays the full
context out of cache (~$0.03-0.10 each; session 4b4ff97c burned 252 markers /
~$7.80 in one run). The post-hoc `keepalive_poll` flag in
`scripts/headless_telemetry.py` *names* the spend at >=5 markers; this function
is the *runtime* lever — the loop can refuse a marker that won't earn its
cache-read cost before it is emitted.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from typing import Optional

from dos import breaker, improve
from dos.gate_classify import (
    GATE_HARD,
    GATE_MODES,
    GateAction,
    ReplanProductivity,
    Verdict,
    gate_policy,
)
from dos.liveness import Liveness
from dos.completion import (
    Completion,
    CompletionVerdict,
    ConvergenceVerdict,
)
from dos.pickable import Pickability
from dos.cooldown import Cooldown
from dos.tokens import blocked_reason_for_key


# ---------------------------------------------------------------------------
# Iteration outcome — the typed result of ONE /dispatch-loop iteration.
#
# This is exactly what Step 3's gate-detection grep already prints:
# `SHIPPED verdict=LIVE`, `GATE verdict=<DRAIN|STALE-STAMP|BLOCKED>`, `INTERIM`,
# `UNCLEAR`, `RATE_LIMITED`. `OutcomeKind` names those, and `IterationOutcome`
# carries the GATE verdict alongside the kind so `decide()` can route a GATE
# through `gate_policy` without re-parsing prose.
# ---------------------------------------------------------------------------


class OutcomeKind(str, enum.Enum):
    """The kind of one iteration's exit, as Step 3's grep classifies it.

    `str`-valued so it round-trips through the grep's stdout token without a
    lookup table (mirrors `gate_classify.Verdict`).
    """

    SHIPPED = "SHIPPED"            # /dispatch shipped picks (child2 ran)
    GATE = "GATE"                  # /dispatch reached Step 9 with child2 skipped
    REPLAN_DONE = "REPLAN_DONE"    # a /replan iteration completed (any outcome)
    UNCLEAR = "UNCLEAR"            # crashed/killed before Step 9, or INTERIM
    RATE_LIMITED = "RATE_LIMITED"  # usage/rate-limit rejection — not a fault
    OVERLOADED = "OVERLOADED"      # transient 529 server overload — retryable with backoff
    LAUNCH_FAILED = "LAUNCH_FAILED"  # no valid init envelope — never started

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class DescendantProgress(str, enum.Enum):
    """FQ-509 — *is a parked parent's own descendant making FORWARD PROGRESS?*

    The loop-level companion to `liveness`, but about a DIFFERENT subject: not
    "is THIS run advancing" (that is `liveness`/`Liveness`) but "did the headless
    `-p` orchestrator this iteration launched PARK while a descendant it spawned
    is still committing the registered picks". When a parent `/dispatch` ends its
    turn early (the self-park invariant), its descendants keep working in their
    own (detached) trees and land their commits seconds-to-minutes LATER — but the
    driver's ancestry check fires the instant the parent `-p` exits, sees 0
    committed picks, and the iteration collapses to UNCLEAR. Today that UNCLEAR
    charges the `consecutive_unclear` breaker, so a parent that merely parked over
    a HEALTHY committing descendant is counted as a systematic FAULT and the loop
    self-stops with CONSECUTIVE_UNCLEAR after `max_unclear` such iters — AND it
    re-dispatches a fresh child each time instead of waiting for the live one.

    The word is **PROGRESS, not "liveness", on purpose.** A child-stall probe
    reports a child ALIVE whenever its log was touched inside the quiet window
    (~10 min) — so a grandchild REAPED seconds ago at parent-exit still reads
    "alive" for ten minutes (a corpse). "Liveness" invites that conflation; this
    enum's contract is FORWARD DELTA only. The host maps the child-stall facts to
    this enum and MUST collapse a log-touched-but-no-commit "alive" to
    `NONE_OBSERVED`, mapping `ADVANCING` ONLY on a real forward delta — HEAD
    advanced since the iteration's start SHA (`new_commit`) OR the ancestry-backed
    CHURNING verdict (all registered picks already ancestors of HEAD). That
    corpse-guard is what keeps the adopt-wait from waiting on a dead child.

    Values:
      ADVANCING     — the descendant landed a forward delta (new commit since
                      start, or all picks already shipped/churning): a parked-but-
                      PRODUCTIVE child; the UNCLEAR is not a fault, so adopt-wait.
      DEAD          — the descendant is genuinely dead (no log growth AND no new
                      commit): today's behavior exactly — the honest UNCLEAR stop.
      NONE_OBSERVED — no forward-progress signal (no own descendant, no ancestry
                      window, or a log-touched-but-not-committing "alive" corpse).
                      Treated identically to `None` (the un-migrated default).
    """

    ADVANCING = "advancing"
    DEAD = "dead"
    NONE_OBSERVED = "none-observed"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class IterationOutcome:
    """One iteration's typed result, as Step 3 produces it.

    `kind` is the Step-3 grep token. `verdict` is set ONLY for `kind=GATE` — it
    is the typed `Verdict` from the structural `verdict=<X>` token in
    /dispatch's Step 9 archive subject (QWB8). For every other kind it is None
    (a SHIPPED iteration has no gate verdict; an UNCLEAR one never reached the
    gate).

    `replan_productivity` is the FQ-240 signal, set ONLY for `kind=REPLAN_DONE`
    — the typed `ReplanProductivity` verdict from
    `gate_classify.classify_replan_productivity` over the /replan iteration's
    terminal result text. It is what makes the drained-twice rule honest: a DRAIN
    is only "drained twice" if the /replan between the two DRAINs was
    PRODUCTIVE (a genuine refill attempt). When None on a REPLAN_DONE outcome
    (a caller that did not classify), `decide()` defaults it to PRODUCTIVE — the
    conservative pre-FQ-240 behavior.

    `packet_judge` is the PJ2 stage-3 verdict from
    `scripts/packet_verdict.py classify` (`SHIPPED-CLEAN` / `SHIPPED-DIRTY` /
    `STALLED` / `BLOCKED-OUTCOME`), set ONLY for `kind=SHIPPED`. `ship_count` is the
    measured ship-count from the same classify evidence — required when
    `packet_judge` is set. The pair drives the SHIPPED-DIRTY-0 breaker: a
    SHIPPED iter with packet_judge=`SHIPPED-DIRTY` AND ship_count==0 is the
    degraded-shipping signal the breaker counts; any other SHIPPED outcome
    resets the streak.

    `measurement_expected` is the FQ-420 distrust flag (set ONLY for
    `kind=SHIPPED`). A SHIPPED token is a *self-report* — "/dispatch says it
    shipped picks." The PJ2 packet-judge is the kernel's independent measurement
    of that claim against the post-fanout commit set. When the driver INTENDED to
    measure but could not resolve the fanout run-ts (`packet_judge` came back
    None on a head==SHIPPED iteration), the measurement is MISSING, not absent-
    by-design — and a missing measurement on a claimed ship is exactly the lie
    the kernel exists to refuse. Setting `measurement_expected=True` asserts "a
    measurement was owed here"; `decide()` then STALLs the loop with
    `UNMEASURED_SHIPPED` rather than taking the conservative healthy path, so a
    null-on-SHIPPED can never silently pass `continue`. The default `False`
    preserves the un-migrated-caller behavior: a caller that never measures
    (no PJ2 stage at all) still gets the pre-FQ-420 conservative healthy path
    when it omits `packet_judge` — the kernel only distrusts a SHIPPED whose
    owner SAID it would measure it. Requiring `packet_judge` to be present
    whenever `measurement_expected=True` AND the iter is healthy is the
    caller's contract; the kernel reads the *absence* of the judge under an
    expectation as the STALL signal. Must be False unless `kind=SHIPPED`.

    `blocked_cause` is the classified `dos.tokens.BlockedReason` key for a GATE
    BLOCKED — the canonical cause the driver mined from the Outcome cell (via
    `unstick_audit.classify_cause`), set ONLY for `kind=GATE` with
    `verdict=BLOCKED`. It is what lets `decide()` distinguish a *re-dispatch-
    curable* BLOCKED (a stale-stamp / refill drift a `/replan` clears — counts
    toward the FQ-452 spin-breaker, routes to /replan as before) from a
    *re-dispatch-INVARIANT* BLOCKED (an operator-decision, a false-ship oracle
    conflation — a reason whose `BLOCKED_REASONS[cause].self_heals_via` is NOT
    `/replan`). An invariant BLOCKED re-blocks identically on every re-dispatch,
    so spinning it through /replan up to the FQ-452 cap (3 iters) is pure churn;
    `decide()` honest-STOPs on the FIRST such BLOCKED instead (the post-run
    analogue of the pre-launch `PICK_HELD_INVARIANT` rung). None (an un-migrated
    caller, or a BLOCKED whose cause the driver could not classify) preserves
    today's behavior exactly — the FQ-452 spin-breaker still bounds the churn at
    3. Must be None unless `kind=GATE` with `verdict=BLOCKED`.
    """

    kind: OutcomeKind
    verdict: Optional[Verdict] = None
    replan_productivity: Optional[ReplanProductivity] = None
    packet_judge: Optional[str] = None
    ship_count: Optional[int] = None
    measurement_expected: bool = False
    blocked_cause: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind is OutcomeKind.GATE and self.verdict is None:
            raise ValueError(
                "a GATE outcome must carry a typed verdict "
                "(the verdict=<X> token from /dispatch's Step 9 archive subject)"
            )
        if self.kind is not OutcomeKind.GATE and self.verdict is not None:
            raise ValueError(
                f"a {self.kind} outcome must not carry a verdict "
                f"(only a GATE iteration has a gate verdict)"
            )
        if (
            self.kind is not OutcomeKind.REPLAN_DONE
            and self.replan_productivity is not None
        ):
            raise ValueError(
                f"a {self.kind} outcome must not carry a replan_productivity "
                f"verdict (only a REPLAN_DONE iteration is a /replan)"
            )
        if self.kind is not OutcomeKind.SHIPPED and (
            self.packet_judge is not None or self.ship_count is not None
        ):
            raise ValueError(
                f"a {self.kind} outcome must not carry packet_judge/ship_count "
                f"(only a SHIPPED iteration has a packet-outcome verdict)"
            )
        if (self.packet_judge is None) != (self.ship_count is None):
            raise ValueError(
                "packet_judge and ship_count must be set together "
                "(both required when present on a SHIPPED outcome)"
            )
        if self.measurement_expected and self.kind is not OutcomeKind.SHIPPED:
            raise ValueError(
                f"a {self.kind} outcome must not set measurement_expected "
                f"(only a SHIPPED iteration owes a packet-judge measurement)"
            )
        if self.blocked_cause is not None and not (
            self.kind is OutcomeKind.GATE and self.verdict is Verdict.BLOCKED
        ):
            raise ValueError(
                f"a {self.kind} outcome (verdict={self.verdict}) must not carry "
                f"blocked_cause (only a GATE BLOCKED iteration has a blocked cause)"
            )


# ---------------------------------------------------------------------------
# Loop state — the carried working context.
#
# These are the per-loop counters Step 0/Step 3/Step 4 thread through working
# context. Holding them in one frozen dataclass — and transitioning them in one
# function — is what makes the loop's control flow inspectable: a reader checks
# the five stop conditions against these fields, not against scattered prose.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoopState:
    """The /dispatch-loop working-context counters `decide()` transitions.

    Fields (the loop-level carry-over — `SCOPE`/`LANE` are not here because they
    are constant for the whole loop and never drive a stop decision):

      iteration            — 1-based count of the iteration that just ran.
      last_replan_drained  — True iff the immediately-prior iteration was a
                             **productive** /replan that followed a DRAIN. The
                             drained-twice trigger: a DRAIN on the /dispatch
                             *after* such a /replan means /replan tried to refill
                             and could not. FQ-240: an unproductive /replan (0
                             gardening / 0 refill) leaves this False — it was not
                             a refill attempt.
      consecutive_unclear  — back-to-back UNCLEAR streak; the circuit breaker.
      consecutive_dirty_zero — back-to-back SHIPPED-DIRTY iters where the
                             measured ship-count was 0. The breaker that pairs
                             with the cap-10 raise: catches a /dispatch
                             regression that ships apparently-successful but
                             actually-empty iters indefinitely (input gate
                             says LIVE, packet-judge says SHIPPED-DIRTY, 0
                             commits land). Reset on any SHIPPED-CLEAN /
                             GATE / REPLAN_DONE outcome.
      gate_mode            — the --gate policy (hard|soft|drive), constant for
                             the loop; passed straight to `gate_policy`.
      max_iterations       — the hard cap (10; no override flag in the SKILL).
                             Raised from 5 in the 2026-05-22 cap raise — see
                             the SKILL's Contract section for the named
                             damage-bound rationale (the degraded-shipping
                             scenario the SHIPPED-DIRTY-0 breaker now kills).
      max_unclear          — the circuit-breaker threshold (3).
      max_dirty_zero       — the SHIPPED-DIRTY-0 breaker threshold (3).
                             Sized to detect a sustained degraded-shipping
                             regression while tolerating one-off DIRTY-0 iters
                             that may recover on the next /dispatch.
      consecutive_stale_stamp — FQ-452: back-to-back GATE iterations whose
                             verdict was STALE-STAMP or BLOCKED and routed to
                             /replan WITHOUT the lane recovering. The
                             non-converging-spin breaker: a plan-meta `remaining:`
                             list naming already-shipped phases makes the picker
                             re-derive 0-live → GATE BLOCKED → /replan → (the
                             §1.5 skip-gate keys on new_findings/substantive_ships,
                             not stale-stamp drift, so /replan exits UNPRODUCTIVE
                             without reconciling the list) → BLOCKED again, forever.
                             The streak SURVIVES the intervening REPLAN_DONE on
                             purpose (the /replan is the *response* to the
                             stale-stamp; if it didn't fix it, the next /dispatch
                             stale-stamps again and the streak must continue) — it
                             resets only on a SHIPPED iteration or a DIFFERENT gate
                             verdict (LIVE/DRAIN/RACE). On the Kth consecutive
                             instance `decide()` STOPs with
                             STALE_STAMP_UNRECONCILED + surface so the loop refuses
                             to spin a (K+1)th /replan into the same unreconciled
                             list; the caller (driver) names the
                             `plan-meta-gardening:<series>` actuation the operator
                             /replan must run (the kernel is pure + series-blind).
      max_stale_stamp      — the FQ-452 spin-breaker threshold (3). One
                             stale-stamp gate routes to /replan normally (the
                             gardening sweep usually clears it); three in a row
                             without recovery means /replan is structurally NOT
                             reconciling the list and another iteration would
                             just re-spin.
      liveness             — the OPTIONAL in-flight `Liveness` verdict
                             (ADVANCING/SPINNING/STALLED) the caller gathered via
                             `dos liveness` for THIS run over the interval since it
                             started (docs/99 / docs/82 Phase-3a). It lives here,
                             not on `IterationOutcome`, because liveness is a
                             property of the run *across the interval* — carried
                             context, like `gate_mode` — not of one iteration's
                             exit token. `decide()` STOPs the loop with
                             `StopReason.SPINNING` when this is `SPINNING`: a
                             ground-truth anti-spin breaker that complements the
                             self-report breakers (`consecutive_dirty_zero` et al.)
                             by reading git/journal, not the caller's outcome
                             token. **Opt-in**: `None` (the default) means the
                             caller did not gather a verdict, and `decide()` is
                             then BYTE-IDENTICAL to the pre-3a behavior — the same
                             conservative-default discipline as
                             `IterationOutcome.measurement_expected=False`. ADVANCING
                             and STALLED never stop the loop here: ADVANCING is the
                             benign verdict, and STALLED ("dead/hung") is the
                             SUPERVISOR's reap input (`supervise.py`), not a live
                             loop's self-stop — a loop making decisions is by
                             construction alive, so STALLED reaching `decide()` is
                             degenerate and mapping it would duplicate the
                             supervisor's job and blur the alive-vs-dead line.
                             (`Liveness` is a SIBLING kernel import — `liveness` is
                             `loop_decide`'s sibling per CLAUDE.md; the litmus is
                             "no host, no I/O", not "no sibling import", and
                             `loop_decide` stays pure: it READS a verdict value,
                             never computes one.)
      completion           — the OPTIONAL in-flight `CompletionVerdict` (docs/117
                             §5.4 / Phase 3) the caller gathered for THIS run after
                             the iteration: it ran `completion.classify` over the
                             run's `LedgerState` + freshly-read `AncestryFacts` (the
                             same git read `resume`'s evidence-gather does) and
                             handed the result in. Like `liveness` it is in-flight
                             EVIDENCE, not carried counter state — it lives here
                             because `decide()` is pure and may not read git itself.
                             `decide()` STOPs with `StopReason.COMPLETE` when this is
                             `COMPLETE` (every declared unit verified on the
                             non-forgeable rung → the work is *finished*, the first
                             non-give-up terminal) and with `StopReason.THRASHING`
                             when it is `UNDERDECLARED` (done-but-under-declared; a
                             human must reconcile → surface). INCOMPLETE and
                             INDETERMINATE never stop here: INCOMPLETE means the loop
                             should *continue* re-dispatching the residual (the
                             caller owns that), and INDETERMINATE means "can't tell"
                             — we never *assert* done on an unsound fold, so it falls
                             through to the existing logic. **Opt-in**: `None` (the
                             default) means the caller gathered no verdict and
                             `decide()` is BYTE-IDENTICAL to the pre-Phase-3 loop —
                             the same conservative default as `liveness`.
      pickability          — the OPTIONAL pre-dispatch `Pickability` verdict
                             (docs/168 §5) the caller gathered for the lane it would
                             dispatch NEXT: it ran `pickable.classify` over the
                             host-gathered unit state and handed the result in. Like
                             `liveness`/`completion` it is in-flight EVIDENCE, not
                             carried counter state — `decide()` is pure and may not
                             read the plan class / soak index / live claims itself.
                             `decide()` STOPs with `StopReason.PICK_HELD_INVARIANT`
                             when this verdict `is_redispatch_invariant` (the lane is
                             held ONLY by a reason a re-dispatch cannot change —
                             DRAFT_CLASS / OPERATOR_GATED / SOAK_OPEN /
                             DEPENDENCY_UNMET): re-dispatching it would re-block
                             identically, so the loop honest-STOPs and surfaces the
                             typed hold for routing (DRAFT→/promote, OPERATOR→
                             escalate a decision, SOAK→wait) instead of spinning. This
                             converts the per-run human "honest STOP" override
                             (documented across a dozen drain-trap run READMEs — ASI
                             #475, RTN soak, FMP #493) into a kernel rule. An
                             OFFERABLE verdict, or a HELD verdict whose reason is
                             re-dispatch-CURABLE (IN_FLIGHT / SOFT_CLAIMED_ELSEWHERE /
                             STALE_CLAIM / COOLDOWN / SHIPPED / UNPARSEABLE), never
                             stops here — those CAN clear, so the loop keeps its
                             existing behavior. **Opt-in**: `None` (the default)
                             skips the rung entirely → BYTE-IDENTICAL to the
                             pre-docs/168 loop, the same conservative default as
                             `liveness` / `completion`.
      cooldown             — the OPTIONAL anti-churn `Cooldown` verdict (docs/207 §3)
                             the caller gathered for the unit it would dispatch NEXT,
                             AFTER it already skipped every fresher candidate: it ran
                             `cooldown.cooldown_verdict` over the unit's `OP_ATTEMPT`
                             history and handed the result in. `decide()` STOPs with
                             `StopReason.PICK_COOLDOWN` when this verdict is
                             `RECENTLY_ATTEMPTED` — the unit was attempted-and-didn't-
                             move inside the window AND (by the host's pick-selection
                             contract) nothing fresher is offerable, so re-dispatching
                             it would re-storm (the ~5%-shipping re-pick storm the bare
                             loop hit). This is the cross-run memory `liveness` (a
                             single-run verdict) cannot provide. A `CLEAR` verdict
                             never stops — the window elapsed or nothing held it. Like
                             `pickability` it is in-flight EVIDENCE, not carried state
                             — `decide()` is pure and may not read the journal. The
                             host's contract: only hand a `RECENTLY_ATTEMPTED` here
                             once it has ALREADY skipped the offerable-and-not-cooled
                             units (the skip-to-next is pick-selection's job; the STOP
                             is the all-cooled terminal). **Opt-in**: `None` skips it
                             → byte-identical to the pre-docs/207 loop.
      convergence          — the OPTIONAL in-flight `ConvergenceVerdict` (docs/117
                             §5.2 / Phase 3) over the residual-size history: the
                             DYNAMIC companion to `completion`. `COMPLETE` is a
                             static fixpoint (residual empty *now*); this catches the
                             *won't-ever-get-there* loop (the residual churns but
                             never empties — the reviewer-finds-new-findings case).
                             `decide()` STOPs with `StopReason.THRASHING` (surface)
                             when this verdict `should_surface` (THRASHING or
                             STARVED). CONVERGING / INSUFFICIENT never stop — the
                             loop keeps going (no fixpoint reached *yet* is not a
                             stop). Checked only when `completion` did not already
                             stop the loop COMPLETE — a converged run is done, not
                             thrashing. **Opt-in**: `None` skips the rung entirely.
      descendant_progress  — FQ-509: the OPTIONAL `DescendantProgress` verdict for
                             THIS iteration's own parked descendant (the headless
                             `-p` child the iteration launched that PARKED while a
                             grandchild it spawned is still committing). In-flight
                             EVIDENCE the caller re-gathers each iteration from the
                             child-stall probe (NOT carried state — cleared up-front
                             like `liveness`). `decide()` reads it ONLY inside the
                             UNCLEAR rung: when it is `ADVANCING` (the descendant
                             landed a forward delta — new commit since start, or all
                             picks already ancestors), the UNCLEAR is a parked-but-
                             PRODUCTIVE child, NOT a /dispatch fault — so `decide()`
                             CONTINUEs (re-dispatch / adopt-wait for the live child
                             to land its picks) WITHOUT charging the
                             `consecutive_unclear` breaker, bounded by
                             `consecutive_adopt_wait`. `DEAD` / `NONE_OBSERVED` /
                             `None` all take today's exact UNCLEAR path — the host's
                             corpse-guard (a log-touched-but-not-committing "alive"
                             must map to `NONE_OBSERVED`, never `ADVANCING`) is what
                             keeps a reaped descendant from ever adopt-waiting.
                             **Opt-in**: `None` (the default) skips the pre-check →
                             BYTE-IDENTICAL to the pre-FQ-509 loop, the same
                             conservative default as `liveness`/`pickability`.
      consecutive_adopt_wait — the carried bound for the `descendant_progress`
                             adopt-wait. Back-to-back UNCLEAR iters where the
                             descendant read `ADVANCING` but STILL had not landed
                             the registered picks. Bumped on each ADVANCING adopt-
                             wait continue; reset to 0 on any non-ADVANCING UNCLEAR
                             iter (so a flapping child cannot accrue it) AND
                             implicitly on any non-UNCLEAR outcome. On the Kth
                             (`max_adopt_wait`) the adopt-wait rung FALLS THROUGH to
                             today's UNCLEAR breaker path (which itself caps at
                             `max_unclear`) — a clock-free bound that degrades to
                             current behavior rather than a new terminal. UNLIKE
                             `consecutive_unclear`, this IS reset on the non-
                             advancing branch, but `consecutive_unclear` is NOT
                             reset there — so a flapping ALIVE/quiet child still
                             reaches `max_unclear` and stops. CARRIED state (it must
                             round-trip through the driver's next_state).
      max_adopt_wait       — the adopt-wait bound (default 2). Two consecutive
                             ADVANCING-but-uncommitted iters is enough evidence the
                             descendant is not actually about to land its picks
                             (or its "advance" is unrelated drift); fall through to
                             the UNCLEAR breaker rather than wait a 3rd.
      consecutive_unproductive_replan_drains — FQ-509-sibling (QWD benign-drain).
                             Back-to-back UNPRODUCTIVE /replans, each the response
                             to a DRAIN, on the same lane. The drained-twice rung
                             (`last_replan_drained`) only arms off a PRODUCTIVE
                             /replan (FQ-240) — but a BENIGN genuinely-drained lane
                             (every phase already shipped/in-flight, nothing left to
                             refill) returns UNPRODUCTIVE from every /replan, so
                             drained-twice never arms and the loop spins
                             DRAIN→/replan→DRAIN→/replan to the iteration cap. This
                             counter catches that: incremented in 5b when a
                             REPLAN_DONE is UNPRODUCTIVE *and* the immediately-prior
                             gate was a DRAIN (`last_gate_was_drain`); reset to 0 on
                             any SHIPPED, any PRODUCTIVE /replan, or any non-DRAIN
                             gate verdict (the lane moved off the benign-drain
                             pattern). On the Kth, the DRAIN that would route the
                             (K+1)th /replan instead STOPs with
                             `StopReason.BENIGN_DRAIN` — the kernel reaches the
                             honest-STOP from typed verdicts the operator otherwise
                             has to eyeball (the QWD run-README override). Default 0
                             keeps the loop BYTE-IDENTICAL for any lane that ever
                             ships or has a productive /replan.
      max_unproductive_replan_drains — the benign-drain breaker threshold (2). Two
                             UNPRODUCTIVE /replans around DRAINs without recovery
                             means /replan is structurally unable to refill the lane
                             (it is benignly drained) and a third would just re-spin.
                             Sized to the QWD memory's measured "2 consecutive
                             UNPRODUCTIVE replans around DRAINs → honest-STOP".
      consecutive_unproductive_replan — #506 / docs/258: back-to-back UNPRODUCTIVE
                             /replans REGARDLESS of the prior gate. The BROADER
                             sibling of `consecutive_unproductive_replan_drains`:
                             that one counts only unproductive replans BRACKETED by a
                             DRAIN (a benignly-drained lane); this one counts EVERY
                             unproductive replan, because the measured pathology (#506:
                             /replan = 45% of loop wall-clock, 43% of replan iters
                             refill nothing) includes a 53-turn replan that produced 0
                             refill even though commits had landed — so the gate was
                             NOT a DRAIN and the benign-drain bracket deliberately
                             skips it (pinned by
                             `test_benign_drain_unproductive_replan_without_prior_drain_no_count`).
                             Bumped in 5b on an UNPRODUCTIVE REPLAN_DONE (via the
                             `dos.breaker` primitive — the FIRST loop_decide counter
                             so expressed); reset to 0 on any PRODUCTIVE replan, any
                             SHIPPED, or a non-stale gate (the lane moved off the
                             stall). On the Kth, `decide()` STOPs with
                             `StopReason.REPLAN_STALLED` + surface. **Opt-in**: only an
                             UNPRODUCTIVE `REPLAN_DONE` (`outcome.replan_productivity is
                             UNPRODUCTIVE`) ever bumps it, and the FQ-240 default treats
                             an unclassified replan as PRODUCTIVE — so a caller that
                             never classifies replan productivity never feeds this and
                             is BYTE-IDENTICAL to the pre-#506 loop, the same
                             conservative default as the benign-drain rung.
      max_unproductive_replan — the REPLAN_STALLED threshold (2). #506: "trip on the
                             2nd unproductive `REPLAN_DONE` — a sweep that refilled
                             nothing twice won't on a 3rd identical pass." Two
                             expensive (16-22min / ~$5) 0-refill replans in a row is
                             enough evidence /replan is structurally unproductive on
                             this lane right now.
      last_gate_was_drain  — internal one-iteration carry: True iff the gate of the
                             immediately-prior iteration was a DRAIN that routed to
                             /replan. Read+reset in 5b to know a following
                             REPLAN_DONE is the response to a DRAIN (the bracket that
                             makes an UNPRODUCTIVE /replan count toward the
                             benign-drain breaker). Set in 5c on a DRAIN that routes
                             to /replan; cleared on any non-DRAIN outcome. Not a
                             stop signal on its own.
      ratchet              — docs/351: the OPTIONAL in-flight `improve.CandidateVerdict`
                             (the OUTER RATCHET) the caller gathered for THIS iteration
                             via `dos improve` over its witnessed net gain — env-authored
                             facts (the reconcile-VERIFIED ship-count rising, suite green,
                             truth clean), NEVER the loop's "I shipped" self-report. Like
                             `liveness`/`completion` it is in-flight EVIDENCE the caller
                             re-gathers each iteration, not carried counter state —
                             `decide()` is pure and may not run `dos improve` itself. It
                             lives here so RSI is first-class across the loops without a
                             new leaf: `improve.classify` already IS the outer-ratchet
                             verdict, and this is the FOURTH instance of the
                             optional-in-flight-evidence pattern (the sibling of
                             `liveness`/`completion`/`pickability`/`cooldown`). `decide()`
                             STOPs with `StopReason.NOT_RATCHETING` when the verdict is
                             `improve.Candidate.ESCALATE` — N iterations in a row with no
                             witnessed net gain (the breaker fired INSIDE `improve`), i.e.
                             the loop is running but not improving (the spinning-while-
                             narrating-progress failure that WORSENS with optimization
                             steps). A KEEP never stops; a lone REVERT is one breaker tick
                             carried by `improve`, not a stop. The verdict is read
                             VERBATIM (the loop adds NO second judgement — the docs/138
                             non-forgeability carried end-to-end). **Opt-in**: `None` (the
                             default) skips the rung entirely → BYTE-IDENTICAL to the
                             pre-docs/351 loop, the same conservative default as
                             `liveness`. (`improve` is a SIBLING kernel import — the
                             litmus is "no host, no I/O", not "no sibling import", and
                             `decide()` stays pure: it READS a verdict value, never
                             computes one.)
    """

    iteration: int = 1
    last_replan_drained: bool = False
    consecutive_unclear: int = 0
    consecutive_dirty_zero: int = 0
    consecutive_overloaded: int = 0
    consecutive_stale_stamp: int = 0
    gate_mode: str = GATE_HARD
    max_iterations: int = 10
    max_unclear: int = 3
    max_dirty_zero: int = 3
    max_overloaded: int = 3
    max_stale_stamp: int = 3
    consecutive_unproductive_replan_drains: int = 0
    max_unproductive_replan_drains: int = 2
    consecutive_unproductive_replan: int = 0
    max_unproductive_replan: int = 2
    last_gate_was_drain: bool = False
    liveness: Optional[Liveness] = None
    completion: Optional[CompletionVerdict] = None
    convergence: Optional[ConvergenceVerdict] = None
    pickability: Optional[Pickability] = None
    cooldown: Optional[Cooldown] = None
    descendant_progress: Optional[DescendantProgress] = None
    consecutive_adopt_wait: int = 0
    max_adopt_wait: int = 2
    ratchet: Optional[improve.CandidateVerdict] = None

    def __post_init__(self) -> None:
        if self.gate_mode not in GATE_MODES:
            raise ValueError(
                f"unknown gate_mode {self.gate_mode!r} — expected one of {GATE_MODES}"
            )


class StopReason(str, enum.Enum):
    """Why the loop stopped — the named stop conditions, in one enum.

    These ARE the answer to "under what exact conditions does this loop stop?"
    — every terminal path produces one of these.
    """

    ITERATION_CAP = "iteration-cap"            # reached max_iterations
    DRAINED_TWICE = "drained-twice"            # DRAIN after a PRODUCTIVE /replan that still couldn't refill
    DRAIN = "drain"                            # soft/drive: a single true DRAIN
    BLOCKED = "blocked"                        # soft/drive: picks blocked (was WEDGE)
    CONSECUTIVE_UNCLEAR = "consecutive-unclear"  # circuit breaker
    CONSECUTIVE_DIRTY_ZERO = "consecutive-dirty-zero"  # K back-to-back SHIPPED-DIRTY+0 iters
    CONSECUTIVE_OVERLOADED = "consecutive-overloaded"  # K back-to-back 529s — outage, not transient
    RATE_LIMITED = "rate-limited"              # usage/rate-limit window exhausted
    LAUNCH_FAILED = "launch-failed"            # subprocess never started
    UNMEASURED_SHIPPED = "unmeasured-shipped"  # FQ-420: SHIPPED claimed, PJ2 measurement owed but missing
    SPINNING = "spinning"                      # docs/99: liveness() says SPINNING — alive, 0 forward delta (ground-truth anti-spin)
    STALE_STAMP_UNRECONCILED = "stale-stamp-unreconciled"  # FQ-452: K consecutive STALE-STAMP/BLOCKED gates /replan never reconciled — refuse to spin another
    BLOCKED_REDISPATCH_INVARIANT = "blocked-redispatch-invariant"  # FQ-510: a GATE BLOCKED whose classified cause is re-dispatch-INVARIANT (operator_decision / a false-ship oracle conflation — any reason whose BLOCKED_REASONS[cause].self_heals_via is NOT /replan). A /replan provably cannot clear it, so it re-blocks identically every iteration; honest-STOP on the FIRST such BLOCKED (the post-run analogue of PICK_HELD_INVARIANT) rather than spinning /replan to the FQ-452 cap (~$15-25/1.5h of churn). The operator-decision sub-case is also auto-filed once by the driver's emit-decision-needed actuation.
    COMPLETE = "complete"                      # docs/117: completion.classify() says COMPLETE — every declared unit verified; the FIRST stop reason that means "finished," not "gave up" (the anti-ITERATION_CAP)
    THRASHING = "thrashing"                    # docs/117: completion.convergence() says THRASHING/STARVED — the residual won't reach a fixpoint; surface, don't burn the cap silently
    PICK_HELD_INVARIANT = "pick-held-invariant"  # docs/168 §5: the next lane is HELD only by a re-dispatch-invariant reason (DRAFT_CLASS/OPERATOR_GATED/SOAK_OPEN/DEPENDENCY_UNMET) — re-dispatch re-blocks identically; honest-STOP + surface the typed hold for routing
    PICK_COOLDOWN = "pick-cooldown"            # docs/207 §3: the next unit was attempted-and-didn't-move inside the cooldown window AND nothing fresher is offerable — re-dispatching it would re-storm; honest-STOP + surface the cooled unit (the anti-churn breaker; the ~5%-shipping re-pick storm)
    BENIGN_DRAIN = "benign-drain"              # FQ-509-sibling (QWD): K consecutive UNPRODUCTIVE /replans, each bracketed by a DRAIN, on the same lane — the lane is genuinely drained but BENIGN (every phase already shipped/in-flight, nothing to refill). The drained-twice rung never arms (an UNPRODUCTIVE /replan is not a refill attempt, FQ-240), so without this rung the loop spins DRAIN→/replan→DRAIN→/replan to the iteration cap (~$11+/55min for 0 refill). Stop instead + surface (re-scope or wait for the in-flight phases to settle). The benign-drain analogue of DRAINED_TWICE: that one is "a PRODUCTIVE /replan still couldn't refill"; this is "the /replans are all UNPRODUCTIVE because there is nothing left to refill."
    REPLAN_STALLED = "replan-stalled"          # #506 / docs/258: K consecutive UNPRODUCTIVE /replans regardless of WHY (the broader sibling of BENIGN_DRAIN). MEASURED: /replan is 45% of all loop wall-clock and 43% of replan iters STALL (0 refill) — a 53-turn replan that refilled nothing even though commits landed (so the gate was NOT a DRAIN, which is exactly the case BENIGN_DRAIN's `last_gate_was_drain` bracket deliberately ignores). BENIGN_DRAIN = "lane empty"; REPLAN_STALLED = "/replan keeps doing costly nothing." Trips on the Kth unproductive REPLAN_DONE ITSELF (default K=2). The FIRST loop_decide rung expressed through the `dos.breaker` primitive rather than a hand-written inline counter.
    NOT_RATCHETING = "not-ratcheting"          # docs/351: the OUTER ratchet. The caller gathered an `improve.CandidateVerdict` (via `dos improve`) over THIS iteration's witnessed net gain — env-authored facts (reconcile-VERIFIED ships rising, suite green, truth clean), never the loop's "I shipped" self-report. An `improve` ESCALATE maps here: N iterations in a row with NO witnessed net gain (the breaker fired INSIDE `improve`). The loop is running but producing no witnessed progress — the "spinning, narrating progress while net-shipping nothing real" failure the +31.4pp-over-steps reward-hacking result names. A single REVERT is one breaker tick, not a stop. The library-sibling of this rung is `retire`/docs/350; the verdict is taken VERBATIM from `improve` (the loop adds no second judgement — the docs/138 non-forgeability carried end-to-end). Checked in the SPINNING ground-truth tier (after COMPLETE, which beats it; SPINNING wins the reason when both fire).

    # PERMANENT legacy alias — same object as BLOCKED, so any un-migrated
    # `is StopReason.WEDGE` keeps working (mirrors GateVerdict.WEDGE).
    WEDGE = "blocked"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class LoopDecision:
    """The single decision `decide()` returns for one iteration.

    `action` — `"continue"` or `"stop"`. The loop branches on this and nothing
    else; everything below is detail for the continue/stop path it picks.

    Continue fields (action == "continue"):
      next_mode    — `"dispatch"` | `"replan"`: the next iteration's mode.
      reconcile    — True iff the loop must run an inline stamp-reconcile pass
                     before the next iteration (a soft/drive STALE-STAMP). Read
                     straight off `gate_policy`'s GateAction.

    Stop fields (action == "stop"):
      stop_reason  — the named StopReason.
      surface      — True iff the stop needs operator attention (a BLOCKED, a
                     soft/drive DRAIN). Read off `gate_policy` for gate stops.

    Always set:
      next_state   — the transitioned `LoopState` to carry into the next
                     iteration (only meaningful when action == "continue", but
                     always returned so the caller never re-derives counters).
      reason       — a one-line operator-facing summary for the tally row.
    """

    action: str  # "continue" | "retry-same-iter" | "stop"
    next_state: LoopState
    reason: str
    next_mode: str = ""
    reconcile: bool = False
    stop_reason: Optional[StopReason] = None
    surface: bool = False
    # Set on action == "retry-same-iter" (transient 529 OVERLOADED): seconds the
    # caller should sleep before relaunching the SAME iteration number. The 60s
    # → 270s → 1200s ladder is well inside the prompt-cache TTL on attempt 1 and
    # past it on attempts 2-3; 3 OVERLOADED in a row escalates to STOP via
    # `consecutive_overloaded` (an outage, not transient).
    backoff_seconds: int = 0


_CONTINUE = "continue"
_STOP = "stop"
_RETRY_SAME_ITER = "retry-same-iter"

# Backoff schedule for OVERLOADED retries — 60s, 270s, 1200s. First step stays
# inside the prompt-cache TTL (cheap); the second and third pay the cache miss
# but are still cheaper than burning a real /dispatch iter under server overload.
# After the third retry hits OVERLOADED again, `consecutive_overloaded` reaches
# `max_overloaded` and the loop STOPs with CONSECUTIVE_OVERLOADED — that's not
# a transient capacity blip, it is a sustained outage and an operator should look.
_OVERLOADED_BACKOFF = (60, 270, 1200)


# ---------------------------------------------------------------------------
# The breaker bridge (docs/258 — the loop_decide → breaker migration).
#
# Every consecutive-streak rung below — UNCLEAR / OVERLOADED / DIRTY-ZERO /
# STALE-STAMP / benign-drain / the new REPLAN_STALLED — is the SAME mechanism:
# bump a count, compare it to a max, trip if reached, reset on a clean outcome.
# `breaker.py` IS that mechanism, lifted into one pure leaf (docs/223). These two
# helpers are the only bridge `decide()` needs: they turn one of `LoopState`'s
# int counter fields + its max field into a `breaker.BreakerCounts` /
# `BreakerPolicy`, run the primitive's fold, and hand back the new count + the
# trip bit. The int fields STAY the public surface (callers construct/read them);
# the bump/compare ARITHMETIC is what moves into `breaker`. Mechanism lifted,
# policy (which field, which threshold, which outcome resets it) stays at the call
# site — exactly the split `breaker.py`'s docstring argues for.
#
# Each loop_decide rung is consecutive-only (no cumulative/flapping rung), so the
# policy is always `max_consecutive=<max>, max_total=0`.


def _breaker_fail(consecutive: int, max_consecutive: int) -> tuple[int, bool]:
    """Record one failure of a consecutive-only streak. Returns (new_count, is_open).

    The `breaker.record_failure` fold, specialized to a loop_decide counter:
    `BreakerCounts(consecutive=…)` + `BreakerPolicy(max_consecutive=…, max_total=0)`.
    Byte-identical to the inline `streak = consecutive + 1; is_open = streak >= max`
    it replaces, BECAUSE `record_failure` bumps then `_classify` trips on `>=`.

    The one boundary the primitive can't take: `max_consecutive == 0`. Inline,
    `max=0` means "trip on the first" (`0+1 >= 0`); but `BreakerPolicy` REFUSES a
    both-zero policy (a breaker that can never trip is a config error). To preserve
    the degenerate exactly, `max == 0` is reproduced here (`new >= 0` is always
    True → trips immediately) rather than routed through the primitive. Every real
    threshold is ≥ 2, so the breaker path is the live one; this guard changes no
    behavior, it only keeps the boundary byte-identical.
    """
    if max_consecutive <= 0:
        new = consecutive + 1
        return new, new >= max_consecutive
    t = breaker.record_failure(
        breaker.BreakerCounts(consecutive=consecutive),
        breaker.BreakerPolicy(max_consecutive=max_consecutive, max_total=0),
    )
    return t.counts.consecutive, t.verdict.is_open


def _replan_stall_policy(state: LoopState) -> breaker.BreakerPolicy:
    """The `BreakerPolicy` for the #506 REPLAN_STALLED rung (docs/258).

    A consecutive-only policy keyed on `max_unproductive_replan`. Clamped to a
    minimum of 1 so `breaker.record_success` (which only reads the healed count,
    always 0, and never the verdict on the success path) can be called even when a
    caller passes the degenerate `max_unproductive_replan == 0` — the FAILURE path
    routes through `_breaker_fail`, which preserves the trip-on-first degenerate
    itself, so this clamp affects only the success-side classify (whose count is 0
    regardless of threshold).
    """
    return breaker.BreakerPolicy(
        max_consecutive=max(state.max_unproductive_replan, 1), max_total=0
    )


def decide(state: LoopState, outcome: IterationOutcome) -> LoopDecision:
    """Decide continue/stop for one /dispatch-loop iteration. PURE — no I/O.

    `state` is the working-context carry-over (the iteration that just produced
    `outcome`). `outcome` is that iteration's typed result (Step 3's grep token,
    plus the GATE verdict when applicable).

    Returns one `LoopDecision`. The decision order is the loop's actual control
    flow, top to bottom — read this function to know exactly when the loop
    stops:

      1. LAUNCH_FAILED → stop (a repeating launch failure burns all slots).
      2. RATE_LIMITED / OVERLOADED → stop / retry-with-backoff; NOT a fault, so
                         neither counts toward the UNCLEAR breaker.
      3. COMPLETE / THRASHING → stop (docs/117 Phase 3): if `state.completion`
                         is COMPLETE the work is verifiably DONE — stop, no
                         surface (the anti-`ITERATION_CAP`). UNDERDECLARED, or a
                         `state.convergence` that `should_surface`
                         (THRASHING/STARVED), stops AND surfaces (no fixpoint /
                         scope in doubt). Checked AFTER the not-a-fault stops and
                         BEFORE SPINNING (a provably-finished run beats a
                         zero-delta SPINNING read — the resumed-already-done
                         case). Opt-in: `None` skips these rungs → byte-identical.
      4. SPINNING      → stop (docs/99): if `state.liveness` is `SPINNING`, the
                         run is alive but landing zero forward delta — a
                         ground-truth anti-spin breaker. Checked AFTER the
                         upstream/transient breakers (an outage-induced idle is
                         not a spin) and BEFORE the outcome block (ground truth
                         overrides the SHIPPED self-report). Opt-in: `None`
                         liveness skips this rung entirely → byte-identical.
      4a. NOT_RATCHETING → stop (docs/351): if `state.ratchet` (an
                         `improve.CandidateVerdict` the caller gathered via
                         `dos improve` over this iteration's witnessed net gain)
                         ESCALATEd, the loop has run N iterations in a row with no
                         witnessed net gain — running but not improving (the OUTER
                         ratchet, RSI first-class across the loops). Same
                         ground-truth tier as SPINNING (checked just after it, so
                         SPINNING wins the reason when both fire); the verdict is
                         read VERBATIM. A KEEP / lone REVERT never stops. Opt-in:
                         `None` skips it → byte-identical.
      4b. PICK_HELD_INVARIANT → stop (docs/168 §5): if `state.pickability` is HELD
                         by a re-dispatch-invariant reason (DRAFT_CLASS /
                         OPERATOR_GATED / SOAK_OPEN / DEPENDENCY_UNMET), the next
                         lane would re-block identically — honest-STOP + surface the
                         typed hold for routing rather than spin. Checked AFTER the
                         not-a-fault/COMPLETE/SPINNING stops and BEFORE the outcome
                         block (the gate beats the self-report). Opt-in: `None`
                         skips it → byte-identical.
      4c. PICK_COOLDOWN → stop (docs/207 §3): if `state.cooldown` is
                         RECENTLY_ATTEMPTED (the next unit was attempted-and-didn't-
                         move inside the window AND the host already skipped every
                         fresher candidate), re-dispatching it would re-storm —
                         honest-STOP + surface the cooled unit. The anti-churn
                         breaker; checked AFTER PICK_HELD_INVARIANT (an invariant
                         hold is more terminal than a time-bounded cooldown). Opt-in:
                         `None` skips it → byte-identical.
      5. UNCLEAR       → increment the streak; stop if it hit max_unclear,
                         else retry `dispatch`.
      6. SHIPPED / REPLAN_DONE / GATE → route via the next-mode + drained-twice
                         + gate-policy logic, then apply the iteration cap. Within
                         the GATE sub-block, a BLOCKED whose `outcome.blocked_cause`
                         is re-dispatch-INVARIANT (FQ-510: a cause whose
                         `BLOCKED_REASONS[cause].self_heals_via` is NOT `/replan` —
                         operator_decision, a false-ship oracle conflation, …) STOPs
                         on the FIRST occurrence (`BLOCKED_REDISPATCH_INVARIANT`),
                         checked BEFORE the FQ-452 stale-stamp spin-counter so an
                         invariant cause never spins /replan to the cap. The
                         post-run analogue of rung 4b. A `/replan`-curable BLOCKED,
                         or one with no classified cause, falls through unchanged.

    The iteration cap is applied LAST, after a continue decision is otherwise
    reached, so a stop *reason* (drained-twice, breaker, rate-limit, spinning)
    always wins over the bare cap — the operator wants the specific reason, not
    "reached 5".
    """
    # The in-flight liveness verdict (docs/99) is per-iteration EVIDENCE the
    # caller re-gathers each turn (via `dos liveness`), never carried state like
    # `consecutive_unclear`. Read it into a local for the SPINNING rung below and
    # CLEAR it from `state` up front, so it never survives into ANY returned
    # `next_state` (terminal or continuing) — a stale verdict can't linger and
    # fire spuriously next iteration; the caller must supply a fresh one. This is
    # the evidence-in-not-state-carried discipline (the same reason `now_ms` is an
    # input to `liveness.classify`, never stored), and it is also what makes the
    # ADVANCING / STALLED / no-verdict paths byte-identical to the pre-3a loop:
    # with the field cleared everywhere, their decisions differ in no field at all.
    # The SPINNING `reason` string + `surface=True` carry the *why* for the
    # operator, so dropping the verdict from `next_state` costs no legibility.
    #
    # The completion + convergence verdicts (docs/117 Phase 3) are gathered and
    # cleared the SAME way and for the SAME reason: they are in-flight evidence the
    # caller re-derives each turn (it owns the intent ledger and re-reads git
    # ancestry), never carried state, so a stale verdict must not survive into the
    # next iteration's `state`. With all three cleared up front, every path that
    # does NOT stop on them is byte-identical to the pre-Phase-3 loop.
    live = state.liveness
    comp = state.completion
    conv = state.convergence
    pick = state.pickability
    cool = state.cooldown
    dprog = state.descendant_progress
    # The outer-ratchet verdict (docs/351) is gathered and cleared the SAME way and
    # for the SAME reason as `liveness`/`completion`: it is in-flight EVIDENCE the
    # caller re-gathers each iteration (it ran `dos improve` over this iteration's
    # witnessed net gain — reconcile-VERIFIED ships rising, suite green, truth clean)
    # and handed the verdict in. It is NOT carried counter state, so a stale verdict
    # must not survive into the next iteration's `state`; with it cleared up front,
    # every path that does NOT stop on it is byte-identical to the pre-docs/351 loop.
    rat = state.ratchet
    state = replace(
        state, liveness=None, completion=None, convergence=None, pickability=None,
        cooldown=None, descendant_progress=None, ratchet=None,
    )

    # 1. LAUNCH_FAILED — the subprocess never produced a valid init envelope.
    #    A repeating launch failure would burn every remaining slot, so stop on
    #    the first one (the SKILL's Step 2 init-line guard).
    if outcome.kind is OutcomeKind.LAUNCH_FAILED:
        return LoopDecision(
            action=_STOP,
            next_state=state,
            stop_reason=StopReason.LAUNCH_FAILED,
            surface=True,
            reason="iteration subprocess failed to start (no valid init envelope)",
        )

    # 2. RATE_LIMITED — a hard usage/rate-limit rejection. Every retry fails the
    #    same way until the window resets; it is NOT a /dispatch fault, so it
    #    must not increment the consecutive-UNCLEAR breaker. Stop and let the
    #    operator re-invoke once the window resets.
    if outcome.kind is OutcomeKind.RATE_LIMITED:
        return LoopDecision(
            action=_STOP,
            next_state=state,
            stop_reason=StopReason.RATE_LIMITED,
            surface=True,
            reason="usage/rate-limit window exhausted — not a fault; re-invoke after reset",
        )

    # 2b. OVERLOADED — a transient 529 / overloaded_error. Unlike a quota window,
    #     this clears in seconds to a couple minutes. Retry the SAME iteration
    #     with exponential backoff (60s → 270s → 1200s). After
    #     `max_overloaded` (3) consecutive OVERLOADED hits, escalate to STOP —
    #     that's an outage, not a capacity blip, and the operator should look.
    #     The breaker does NOT increment the consecutive-UNCLEAR streak (an
    #     OVERLOADED is upstream, not a /dispatch fault), same precedent as
    #     RATE_LIMITED.
    if outcome.kind is OutcomeKind.OVERLOADED:
        streak, tripped = _breaker_fail(
            state.consecutive_overloaded, state.max_overloaded
        )
        bumped = replace(state, consecutive_overloaded=streak)
        if tripped:
            return LoopDecision(
                action=_STOP,
                next_state=bumped,
                stop_reason=StopReason.CONSECUTIVE_OVERLOADED,
                surface=True,
                reason=(
                    f"{streak} consecutive OVERLOADED (529) hits — sustained "
                    f"server-side overload, not a transient blip; stop and "
                    f"re-invoke after the upstream incident clears"
                ),
            )
        backoff = _OVERLOADED_BACKOFF[min(streak - 1, len(_OVERLOADED_BACKOFF) - 1)]
        return LoopDecision(
            action=_RETRY_SAME_ITER,
            next_state=bumped,
            backoff_seconds=backoff,
            reason=(
                f"OVERLOADED (streak {streak}/{state.max_overloaded}) — "
                f"transient 529, sleep {backoff}s then retry same iter"
            ),
        )

    # A non-OVERLOADED outcome resets the OVERLOADED streak — a clean run means
    # the upstream incident cleared.
    state = replace(state, consecutive_overloaded=0)

    # 3. COMPLETE (docs/117 Phase 3) — the stop-on-DONE gate, the first terminal
    #    that means "finished," not "gave up." If the caller gathered a
    #    `CompletionVerdict` and it is COMPLETE, every declared unit is verified on
    #    the non-forgeable ancestry rung (the residual is empty): the work is done,
    #    so stop — cleanly, NO surface (a clean finish is not an operator decision).
    #    This is the anti-`ITERATION_CAP`: a healthy loop now terminates HERE, and
    #    the cap demotes to a backstop for genuinely pathological runs (docs/117
    #    §5.4 — "the critical inversion").
    #
    #    Placement is load-bearing and was an explicit operator decision: COMPLETE is
    #    checked BEFORE the SPINNING rung. The two can BOTH fire for one legitimate
    #    case — a run resumed with nothing left to do has zero git delta since start
    #    (SPINNING) AND every declared unit already verified (COMPLETE). When the
    #    work is provably finished on the non-forgeable rung, "done" is the honest
    #    reason even with zero recent delta, so COMPLETE wins. (It stays AFTER the
    #    not-a-fault stops — LAUNCH_FAILED / RATE_LIMITED / OVERLOADED — for the same
    #    reason SPINNING does: a run that failed to launch or 529'd on its last turn
    #    has not "finished," and the specific outage is the reason the operator
    #    wants.)
    #
    #    UNDERDECLARED (Phase 4, not emitted yet) → stop AND surface: the run thinks
    #    it is done but an external `ScopeSource` says it under-declared its extent;
    #    a human must reconcile. We route it through `StopReason.THRASHING` (the
    #    "no clean finish, look at this" terminal) with surface=True — the residual
    #    is empty but the *scope* is in doubt, which is exactly a surface-for-review.
    #    INCOMPLETE / INDETERMINATE never stop here: INCOMPLETE means "continue,
    #    re-dispatch the residual" (the caller owns that actuation), and INDETERMINATE
    #    means "can't tell from an unsound fold" — we never ASSERT done on it, so it
    #    falls through to the existing logic untouched.
    #
    #    Opt-in / byte-identical: `comp is None` (the default) skips this rung
    #    entirely, so an un-migrated caller is unaffected.
    if comp is not None:
        if comp.state is Completion.COMPLETE:
            return LoopDecision(
                action=_STOP,
                next_state=state,
                stop_reason=StopReason.COMPLETE,
                surface=False,
                reason=(
                    "completion() reports COMPLETE — every declared unit is verified "
                    "against git ancestry; the residual is empty, so the loop stops "
                    "because the work is DONE (stop-on-done, not out-of-budget). "
                    + comp.reason
                ),
            )
        if comp.state is Completion.UNDERDECLARED:
            return LoopDecision(
                action=_STOP,
                next_state=state,
                stop_reason=StopReason.THRASHING,
                surface=True,
                reason=(
                    "completion() reports UNDERDECLARED — the declared residual is "
                    "empty but an external scope check says the extent was "
                    "under-declared; stopping and surfacing for a human to reconcile. "
                    + comp.reason
                ),
            )

    # 3b. THRASHING / STARVED (docs/117 Phase 3, §5.2) — the dynamic no-fixpoint
    #     gate. COMPLETE above is the STATIC fixpoint (residual empty now); this is
    #     its dynamic companion: the residual keeps churning but never empties (each
    #     pass closes some work and opens as much — the reviewer-finds-new-findings
    #     loop). If the caller gathered a `ConvergenceVerdict` over the residual-size
    #     history and it `should_surface` (THRASHING or STARVED), the loop will not
    #     reach a fixpoint — stop and surface rather than burn the iteration cap
    #     silently. Checked AFTER the COMPLETE gate (a run whose residual just reached
    #     0 is CONVERGING/done, never thrashing) and, like it, before the
    #     UNCLEAR/SHIPPED/GATE block. CONVERGING / INSUFFICIENT never stop — "no
    #     fixpoint *yet*" is not a stop signal. Opt-in: `conv is None` skips it.
    if conv is not None and conv.state.should_surface:
        return LoopDecision(
            action=_STOP,
            next_state=state,
            stop_reason=StopReason.THRASHING,
            surface=True,
            reason=(
                "convergence() reports "
                f"{conv.state.value} — the residual is not trending to empty over "
                "the recent window; the loop is productive but has no fixpoint, so "
                "stopping and surfacing rather than spending the cap. " + conv.reason
            ),
        )

    # 4. SPINNING (docs/99 / docs/82 Phase-3a) — the ground-truth anti-spin
    #    breaker. If the caller gathered an in-flight `Liveness` verdict for this
    #    run and it is SPINNING (alive — fresh heartbeat — but zero commits and
    #    zero state-mutating lane events since start), the loop is burning tokens
    #    narrating motion it is not making. Stop on the hard evidence rather than
    #    waiting for the iteration cap or a self-report streak.
    #
    #    Placement is load-bearing: AFTER LAUNCH_FAILED / RATE_LIMITED / OVERLOADED
    #    (a run idle only because it is backing off a 529 / quota window is NOT
    #    spinning — those not-a-fault stops must win, the same precedence they get
    #    over the UNCLEAR breaker), and BEFORE the UNCLEAR / SHIPPED / GATE block
    #    (liveness reads ground truth, and the whole docs/82 thesis is that ground
    #    truth overrides the self-report — a loop reporting SHIPPED every iteration
    #    while landing 0 commits is the canonical spin, and SHIPPED's healthy path
    #    must not pre-empt the verdict). This mirrors UNMEASURED_SHIPPED being
    #    checked FIRST inside the SHIPPED branch: a ground-truth distrust signal
    #    pre-empts the conservative continue.
    #
    #    Opt-in / byte-identical: `live is None` (the default) skips this rung
    #    entirely, so an un-migrated caller gets the pre-3a behavior exactly.
    #    Only SPINNING stops here — ADVANCING is benign; STALLED ("dead/hung") is
    #    the supervisor's reap input (`supervise.py`), not a live loop's self-stop.
    if live is Liveness.SPINNING:
        return LoopDecision(
            action=_STOP,
            next_state=state,
            stop_reason=StopReason.SPINNING,
            surface=True,
            reason=(
                "liveness() reports SPINNING — the run is alive but has landed 0 "
                "commits and 0 lane events since it started; stopping on "
                "ground-truth evidence rather than burning the iteration budget "
                "narrating motion it is not making"
            ),
        )

    # 4a. NOT_RATCHETING (docs/351) — the OUTER RATCHET, RSI made first-class across
    #     the loops. If the caller gathered an `improve.CandidateVerdict` for THIS
    #     iteration's witnessed net gain (it ran `dos improve` over env-authored facts
    #     — reconcile-VERIFIED ships rising, suite green, truth clean — NEVER the
    #     loop's "I shipped" self-report) and that verdict ESCALATEd, the loop has run
    #     N iterations in a row producing NO witnessed net gain: it is running but not
    #     improving — the "spinning, narrating progress while net-shipping nothing
    #     real" failure the reward-hacking literature names (it WORSENS with steps:
    #     +31.4pp over 10→100 optimization steps), and the reason the witness must
    #     gate EVERY kept iteration, not run once. Stop and surface — the breaker
    #     already fired INSIDE `improve` (its ESCALATE IS the streak), so this is the
    #     RSI bottleneck handed back to a human, by construction.
    #
    #     The verdict is read VERBATIM — the loop adds NO second judgement. That is
    #     what carries the docs/138 non-forgeability end-to-end: the stop bit is a
    #     pure function of facts the loop did not author (the `improve` keep-gate's
    #     four env-authored facts). A loop that learns to narrate "great progress"
    #     gains zero stop-avoidance, because the claim is not in the decision.
    #
    #     A single REVERT does NOT stop here — it is one breaker tick (mirrors how a
    #     single UNCLEAR does not stop but the streak does; `improve` carries the
    #     count, ESCALATE is the streak reaching the cap). KEEP never stops.
    #
    #     Placement is load-bearing and mirrors SPINNING (the sibling ground-truth
    #     rung): AFTER the not-a-fault stops + COMPLETE (a provably-finished run is
    #     not "not ratcheting" — COMPLETE beats it, the docs/280 precedent), and in
    #     the same tier as SPINNING. SPINNING is checked FIRST, so when BOTH fire
    #     (a run landing 0 delta AND escalating the ratchet — the same disease) the
    #     ground-truth git-delta reason wins; NOT_RATCHETING is the composed-verdict
    #     reason for the case SPINNING did not already name. BEFORE the pick rungs +
    #     the outcome block (the ratchet pre-empts the iteration's self-report — the
    #     same "ground truth beats the SHIPPED token" precedence SPINNING has).
    #
    #     Opt-in / byte-identical: `rat is None` (the default) skips this rung
    #     entirely, so an un-migrated caller gets the pre-docs/351 behavior exactly.
    #     Only an ESCALATE stops; KEEP and a lone REVERT fall through unchanged.
    if rat is not None and rat.verdict is improve.Candidate.ESCALATE:
        return LoopDecision(
            action=_STOP,
            next_state=state,
            stop_reason=StopReason.NOT_RATCHETING,
            surface=True,
            reason=(
                "the outer ratchet (dos improve) ESCALATEd — the loop has run too "
                "many iterations in a row with no witnessed net gain (suite/truth/"
                "measured-progress, not the loop's self-report), so it is running "
                "but not improving; stopping and handing the judgment back to a "
                "human rather than spinning. " + rat.reason
            ),
        )

    # 4b. PICK_HELD_INVARIANT (docs/168 §5) — the honest-STOP rung. If the caller
    #     gathered a pre-dispatch `Pickability` verdict for the lane it would
    #     dispatch next and that verdict is HELD by a reason a re-dispatch CANNOT
    #     change (DRAFT_CLASS / OPERATOR_GATED / SOAK_OPEN / DEPENDENCY_UNMET), the
    #     next iteration would re-block on the identical deterministic gate. This is
    #     the drain-trap the host hit on three distinct lanes in 36h (ASI #475
    #     operator-gated, RTN soak, FMP #493 DRAFT): the loop's `decide()` modeled
    #     continue→dispatch on a DRAIN, so the operator had to OVERRIDE with an
    #     "honest STOP" every time. With the hold reason typed, that override
    #     becomes a kernel rule — STOP and surface the typed hold so the host can
    #     route it (DRAFT→/promote, OPERATOR_GATED→escalate a decision, SOAK_OPEN→
    #     wait, never /replan; DEPENDENCY_UNMET→ship the prerequisite).
    #
    #     EVIDENCE-GATED: it fires ONLY when the verdict is present AND
    #     `is_redispatch_invariant`. An OFFERABLE verdict, or a HELD verdict whose
    #     reason is re-dispatch-CURABLE (IN_FLIGHT / SOFT_CLAIMED_ELSEWHERE /
    #     STALE_CLAIM / COOLDOWN / SHIPPED / UNPARSEABLE — all CAN clear), never
    #     stops here.
    #
    #     Placement is load-bearing: AFTER the not-a-fault stops (LAUNCH_FAILED /
    #     RATE_LIMITED / OVERLOADED — an outage is not a reason to declare the lane
    #     un-pickable) and AFTER COMPLETE / SPINNING (a provably-finished or
    #     ground-truth-spinning run names a more specific terminal), and BEFORE the
    #     UNCLEAR / SHIPPED / GATE outcome block (an invariant hold on the next lane
    #     pre-empts whatever this iteration's outcome token says — the same "the
    #     gate beats the self-report" precedence the SPINNING rung has).
    #
    #     Opt-in / byte-identical: `pick is None` (the default) skips this rung
    #     entirely, so an un-migrated caller is unaffected.
    if pick is not None and pick.is_redispatch_invariant:
        reason = pick.reason  # guaranteed non-None by is_redispatch_invariant
        return LoopDecision(
            action=_STOP,
            next_state=state,
            stop_reason=StopReason.PICK_HELD_INVARIANT,
            surface=True,
            reason=(
                f"next lane is HELD by {reason.value} — a re-dispatch-invariant "
                f"hold a re-dispatch cannot change; honest-STOP rather than "
                f"re-block on the identical gate next iteration. "
                + (pick.evidence or "")
            ).strip(),
        )

    # 4c. PICK_COOLDOWN (docs/207 §3) — the anti-churn breaker. If the caller
    #     gathered a `Cooldown` verdict for the unit it would dispatch NEXT (after
    #     it ALREADY skipped every fresher offerable-and-not-cooled candidate — the
    #     host's pick-selection contract) and that verdict is RECENTLY_ATTEMPTED,
    #     the unit was attempted-and-didn't-move inside the window and nothing
    #     fresher is left. Re-dispatching it would re-storm (the ~5%-shipping
    #     re-pick loop the bare loop hit), so honest-STOP + surface the cooled unit
    #     rather than burn the iteration re-confirming a known drain. This is the
    #     CROSS-RUN memory `liveness` (a single-run verdict) cannot provide.
    #
    #     EVIDENCE-GATED: fires ONLY when the verdict is present AND `held`
    #     (RECENTLY_ATTEMPTED). A CLEAR verdict — the window elapsed, or nothing
    #     held the unit — never stops; the loop keeps its existing behavior.
    #
    #     Placement: AFTER the not-a-fault stops + COMPLETE/SPINNING/PICK_HELD
    #     (an invariant hold names a more specific terminal than a cooldown — a
    #     DRAFT lane is held forever, a cooled one only until the wall), and BEFORE
    #     the outcome block (the cooldown pre-empts the iteration's self-report, the
    #     same "the gate beats the self-report" precedence the sibling rungs have).
    #
    #     Opt-in / byte-identical: `cool is None` (the default) skips this rung.
    if cool is not None and cool.held:
        return LoopDecision(
            action=_STOP,
            next_state=state,
            stop_reason=StopReason.PICK_COOLDOWN,
            surface=True,
            reason=(
                f"next unit {cool.unit_id!r} is in a cooldown window — "
                + (cool.reason or "attempted recently and did not move")
                + "; nothing fresher is offerable, so honest-STOP rather than "
                "re-storm a known drain (the anti-churn breaker)"
            ),
        )

    # 4. UNCLEAR — crashed/killed before Step 9, or an INTERIM envelope. Retry
    #    as `dispatch`, but increment the streak; three in a row means the
    #    subprocess is failing systematically (the circuit breaker).
    if outcome.kind is OutcomeKind.UNCLEAR:
        # 4d. DESCENDANT-PROGRESS adopt-wait (FQ-509) — the pre-check that
        #     distinguishes a *parked-but-PRODUCTIVE* parent from a systematic
        #     failure. A headless `-p` child that PARKED its own turn while a
        #     grandchild it spawned is still committing the registered picks lands
        #     here as UNCLEAR (the parent's ancestry check ran the instant it
        #     exited, saw 0 committed picks, and the token collapsed to UNCLEAR).
        #     Charging that to the UNCLEAR breaker is WRONG: the descendant is
        #     healthy and about to land its commits — counting it as a fault makes
        #     the loop self-stop with CONSECUTIVE_UNCLEAR over live work AND
        #     re-dispatch a fresh child each time instead of waiting for the live
        #     one. When the host supplied `descendant_progress == ADVANCING` (the
        #     descendant landed a forward delta — a real new commit since start, or
        #     the ancestry-backed CHURNING verdict; the host's corpse-guard ensures
        #     a log-touched-but-not-committing "alive" maps to NONE_OBSERVED, never
        #     here), CONTINUE the loop (adopt-wait: re-dispatch so the live child
        #     gets the chance to land its picks → the NEXT iteration's ancestry
        #     check lifts it to SHIPPED) WITHOUT charging the UNCLEAR breaker, and
        #     RESET consecutive_unclear to 0 (a live committing child means the
        #     prior UNCLEARs were not a systematic fault).
        #
        #     BOUNDED, clock-free: the adopt-wait is itself counted by
        #     `consecutive_adopt_wait`; after `max_adopt_wait` consecutive
        #     ADVANCING-but-the-picks-still-uncommitted iters it FALLS THROUGH to
        #     today's UNCLEAR breaker path (which caps at max_unclear) rather than
        #     a new terminal — so a descendant that keeps "advancing" but never
        #     lands its registered picks can never adopt-wait forever. The continue
        #     also cannot persist past death: descendant_progress is re-gathered
        #     every iteration (cleared up-front), so a child that DIES flips to
        #     DEAD next iter and takes the normal UNCLEAR path.
        #
        #     Opt-in / byte-identical: `dprog` defaults None (cleared up-front),
        #     and the guard is `dprog is DescendantProgress.ADVANCING` — DEAD,
        #     NONE_OBSERVED, and None all skip it → the rung below is byte-identical
        #     to the pre-FQ-509 loop.
        if dprog is DescendantProgress.ADVANCING:
            aw_streak, aw_tripped = _breaker_fail(
                state.consecutive_adopt_wait, state.max_adopt_wait
            )
            if not aw_tripped:
                # Live committing descendant — adopt-wait. Do NOT charge the
                # UNCLEAR breaker; reset it (this iter is not a fault).
                bumped = replace(
                    state, consecutive_adopt_wait=aw_streak, consecutive_unclear=0
                )
                return _continue_or_cap(
                    bumped,
                    next_mode="dispatch",
                    reason=(
                        f"descendant FORWARD-PROGRESSING (adopt-wait "
                        f"{aw_streak}/{state.max_adopt_wait}) — the parent parked "
                        f"but a descendant it spawned is committing the registered "
                        f"picks; wait for it to land them, not a /dispatch fault"
                    ),
                )
            # aw_tripped: the descendant kept "advancing" but never landed its
            # picks within the bound → fall through to the normal UNCLEAR breaker
            # path below (degrade to today's behavior; not a new terminal). The
            # bumped adopt-wait count rides into next_state via the streak below.
        # Non-advancing UNCLEAR (DEAD / NONE_OBSERVED / None, or a tripped
        # adopt-wait): today's exact path. Reset consecutive_adopt_wait (a
        # non-advancing iter breaks the adopt streak) but NOT consecutive_unclear
        # (it accrues — so a flapping ALIVE/quiet child still reaches max_unclear).
        streak, tripped = _breaker_fail(state.consecutive_unclear, state.max_unclear)
        bumped = replace(state, consecutive_unclear=streak, consecutive_adopt_wait=0)
        if tripped:
            return LoopDecision(
                action=_STOP,
                next_state=bumped,
                stop_reason=StopReason.CONSECUTIVE_UNCLEAR,
                surface=True,
                reason=(
                    f"{streak} consecutive UNCLEAR iterations — the /dispatch "
                    f"subprocess is failing systematically, not draining a backlog"
                ),
            )
        return _continue_or_cap(
            bumped,
            next_mode="dispatch",
            reason=f"UNCLEAR (streak {streak}/{state.max_unclear}) — retrying dispatch",
        )

    # A non-UNCLEAR, non-fault iteration completed → reset the UNCLEAR breaker.
    # The SHIPPED-DIRTY-0 breaker is reset only inside the SHIPPED branch on a
    # *healthy* SHIPPED outcome (or on a REPLAN_DONE / GATE outcome that
    # naturally interrupts a back-to-back-SHIPPED streak — handled below).
    base = replace(state, consecutive_unclear=0)
    if outcome.kind in (OutcomeKind.REPLAN_DONE, OutcomeKind.GATE):
        # A non-SHIPPED outcome breaks the back-to-back-SHIPPED-DIRTY-0 streak.
        base = replace(base, consecutive_dirty_zero=0)

    # 5a. SHIPPED — picks landed. Backlog still has work; clear the drained flag.
    #
    # FQ-420 unmeasured-ship STALL (checked FIRST): a SHIPPED token is the
    # /dispatch child's *self-report*. The PJ2 packet-judge is the kernel's
    # independent measurement of that claim against the post-fanout commit set.
    # If the driver asserted a measurement was owed (`measurement_expected`) but
    # the judge came back None — the FQ-420 shape: head==SHIPPED yet the fanout
    # run-ts could not be resolved, so PJ2 classify never ran — the kernel has a
    # claimed ship it could NOT verify. It must not fall through to the healthy
    # path on the strength of an unverified self-report (that is the exact lie
    # the substrate exists to refuse — a manual git-log check should never be
    # what catches it). STALL and surface so the operator re-measures: resolve
    # the fanout ts from the archive, or treat the ship as unproven. This guard
    # precedes the dirty-zero / healthy classification because a missing
    # measurement makes ALL of that sub-classification untrustworthy.
    #
    # SHIPPED-DIRTY-0 breaker: a SHIPPED iter that the packet-judge classified
    # as SHIPPED-DIRTY AND measured 0 commits is the degraded-shipping signal
    # the breaker counts (input gate says LIVE, packet-judge says DIRTY, no
    # commits actually landed). K back-to-back instances → stop; this is the
    # structural defense that justifies the iteration cap raise from 5 to 10
    # — it kills the degraded-shipping damage path at iter K regardless of cap.
    # Every other SHIPPED outcome (SHIPPED-CLEAN, SHIPPED-DIRTY with ship_count>0,
    # or no packet-judge supplied AND none expected) resets the streak. Callers
    # that do not pass packet_judge/ship_count AND do not set
    # measurement_expected get pre-breaker behavior — the streak is held
    # constant rather than incremented; this matches the "treat as PRODUCTIVE
    # when unclassified" conservative-default precedent (an un-migrated caller
    # that never measures is trusted; one that SAID it would measure is not).
    if outcome.kind is OutcomeKind.SHIPPED:
        # FQ-452: a SHIPPED iteration is genuine forward progress — the lane is
        # no longer stuck on a stale-stamp gate. Reset the spin-breaker streak.
        # (Reset here, NOT in the shared `base` block above, because a
        # REPLAN_DONE must NOT reset it — the /replan is the *response* to the
        # stale-stamp and the streak has to survive it to ever reach the cap.)
        base = replace(base, consecutive_stale_stamp=0)
        # QWD benign-drain: a ship means the lane was NOT benignly drained — clear
        # the unproductive-replan-drain streak + the prior-DRAIN carry. (Same
        # reasoning as the stale-stamp reset: reset on a real ship, not in the
        # shared block, because rung 5b consumes `last_gate_was_drain`.)
        # #506: a ship also clears the REPLAN_STALLED streak — the lane produced
        # work, so /replan is not in the 0-refill stall. Like the two resets above,
        # done here (not in the shared `base` block) so a REPLAN_DONE does NOT reset
        # it: the stall streak must SURVIVE the dispatch→GATE→/replan cycle between
        # two unproductive replans to ever reach the threshold (a GATE always sits
        # between two REPLAN_DONE outcomes, exactly as `consecutive_stale_stamp`
        # survives the intervening REPLAN_DONE for the mirror reason).
        base = replace(
            base,
            consecutive_unproductive_replan_drains=0,
            consecutive_unproductive_replan=0,
            last_gate_was_drain=False,
        )
        if outcome.measurement_expected and outcome.packet_judge is None:
            return LoopDecision(
                action=_STOP,
                next_state=replace(base, last_replan_drained=False),
                stop_reason=StopReason.UNMEASURED_SHIPPED,
                surface=True,
                reason=(
                    "SHIPPED claimed but the PJ2 packet-judge measurement is "
                    "missing (fanout run-ts unresolved) — the ship is "
                    "self-reported and unverified; STALL and re-measure rather "
                    "than trust an unmeasured ship"
                ),
            )
        is_dirty_zero = (
            outcome.packet_judge == "SHIPPED-DIRTY"
            and outcome.ship_count == 0
        )
        if is_dirty_zero:
            streak, tripped = _breaker_fail(
                base.consecutive_dirty_zero, base.max_dirty_zero
            )
            bumped = replace(
                base, last_replan_drained=False, consecutive_dirty_zero=streak
            )
            if tripped:
                return LoopDecision(
                    action=_STOP,
                    next_state=bumped,
                    stop_reason=StopReason.CONSECUTIVE_DIRTY_ZERO,
                    surface=True,
                    reason=(
                        f"{streak} consecutive SHIPPED-DIRTY iters with 0 commits "
                        f"— /dispatch is shipping apparently-successful but "
                        f"actually-empty iters (degraded-shipping regression)"
                    ),
                )
            return _continue_or_cap(
                bumped,
                next_mode="dispatch",
                reason=(
                    f"SHIPPED-DIRTY-0 (streak {streak}/{base.max_dirty_zero}) "
                    f"— continue dispatch, but watch the streak"
                ),
            )
        # Healthy SHIPPED outcome (SHIPPED-CLEAN, or SHIPPED-DIRTY with ≥1 commit,
        # or no packet-judge supplied) — reset the dirty-zero streak.
        nxt = replace(
            base, last_replan_drained=False, consecutive_dirty_zero=0
        )
        return _continue_or_cap(
            nxt, next_mode="dispatch", reason="SHIPPED — picks shipped, continue dispatch"
        )

    # 5b. REPLAN_DONE — a /replan iteration completed. Next is `dispatch`. The
    #     FQ-240 fix: arm `last_replan_drained` (the drained-twice trigger) ONLY
    #     when the /replan was PRODUCTIVE — i.e. it actually refilled / gardened.
    #     An UNPRODUCTIVE /replan (the §1.5 no-op skip, or a 0/0/0 sweep) is NOT
    #     a refill attempt; arming the trigger off it would let a DRAIN that
    #     follows a /replan-that-did-nothing false-stop the loop as DRAINED_TWICE
    #     (finding #240's second shape, distinct from the QWB7 STALE-STAMP half).
    #     Default to PRODUCTIVE when unclassified — the conservative pre-FQ-240
    #     behavior, so this change can never make the loop run *longer*.
    if outcome.kind is OutcomeKind.REPLAN_DONE:
        productivity = outcome.replan_productivity or ReplanProductivity.PRODUCTIVE
        productive = productivity is ReplanProductivity.PRODUCTIVE
        # QWD benign-drain breaker (FQ-509-sibling). An UNPRODUCTIVE /replan whose
        # immediately-prior gate was a DRAIN (`last_gate_was_drain`) is the
        # benign-drain signal: /replan was asked to refill a drained lane and
        # produced nothing because there is nothing left. Count it. A PRODUCTIVE
        # /replan, or one not preceded by a DRAIN, resets the streak (the lane is
        # not in the benign-drain spin). The prior-DRAIN carry is consumed either
        # way (it describes only the one transition into this /replan). The count
        # is bumped via the `dos.breaker` fold (docs/258); the trip is NOT checked
        # here — the benign-drain stop fires on the NEXT DRAIN (rung 5c), so we keep
        # only the new count and discard the trip bit at this point.
        if productive:
            benign_streak = 0
        elif base.last_gate_was_drain:
            benign_streak, _ = _breaker_fail(
                base.consecutive_unproductive_replan_drains,
                base.max_unproductive_replan_drains,
            )
        else:
            benign_streak = base.consecutive_unproductive_replan_drains

        # #506 / docs/258 — the REPLAN_STALLED breaker, the BROADER sibling, and the
        # FIRST loop_decide rung whose trip is taken straight off `dos.breaker`. An
        # UNPRODUCTIVE /replan is a failure of this class REGARDLESS of the prior
        # gate (a costly 0-refill sweep is the pathology whether or not a DRAIN
        # preceded it — the gap the benign-drain bracket leaves). A PRODUCTIVE
        # /replan is a success (the sweep refilled → the stall cleared) and heals the
        # streak. On the Kth (default 2) consecutive unproductive /replan, STOP +
        # surface rather than spend another 16-22min/~$5 sweep that the measurement
        # says will refill nothing. Opt-in/byte-identical: an unclassified /replan
        # defaults to PRODUCTIVE (FQ-240), so a caller that never classifies
        # productivity records only successes here and never trips this.
        if productive:
            stall_t = breaker.record_success(
                breaker.BreakerCounts(consecutive=base.consecutive_unproductive_replan),
                _replan_stall_policy(base),
            )
            stall_streak = stall_t.counts.consecutive  # 0 — healed
        else:
            stall_streak, stall_open = _breaker_fail(
                base.consecutive_unproductive_replan, base.max_unproductive_replan
            )
            if stall_open:
                return LoopDecision(
                    action=_STOP,
                    next_state=replace(
                        base,
                        last_replan_drained=False,
                        consecutive_unproductive_replan_drains=benign_streak,
                        consecutive_unproductive_replan=stall_streak,
                        last_gate_was_drain=False,
                    ),
                    stop_reason=StopReason.REPLAN_STALLED,
                    surface=True,
                    reason=(
                        f"{stall_streak} consecutive UNPRODUCTIVE /replans — /replan "
                        f"keeps refilling nothing (the measured 0-refill stall, ~45% "
                        f"of loop wall-clock); stop and surface rather than spend "
                        f"another ~16-22min/~$5 sweep that won't refill on a "
                        f"{stall_streak + 1}th identical pass"
                    ),
                )

        nxt = replace(
            base,
            last_replan_drained=productive,
            consecutive_unproductive_replan_drains=benign_streak,
            consecutive_unproductive_replan=stall_streak,
            last_gate_was_drain=False,
        )
        if productive:
            reason = "REPLAN_DONE (productive) — backlog refilled, dispatch next (drained-twice armed)"
        else:
            reason = (
                "REPLAN_DONE (unproductive) — /replan did 0 gardening / 0 refill; "
                "drained-twice NOT armed (a DRAIN next is not drained-twice)"
            )
            if benign_streak:
                reason += (
                    f"; benign-drain streak {benign_streak}/"
                    f"{base.max_unproductive_replan_drains}"
                )
            if stall_streak:
                reason += (
                    f"; replan-stall streak {stall_streak}/"
                    f"{base.max_unproductive_replan}"
                )
        return _continue_or_cap(nxt, next_mode="dispatch", reason=reason)

    # 5c. GATE — /dispatch reached Step 9 with child2 skipped. The typed verdict
    #     + the --gate policy decide what to do (the pure `gate_policy`). The
    #     loop-level part this layer adds is the drained-twice counter.
    assert outcome.kind is OutcomeKind.GATE and outcome.verdict is not None
    action: GateAction = gate_policy(outcome.verdict, base.gate_mode)

    # FQ-510 — re-dispatch-INVARIANT BLOCKED stop (the post-run analogue of the
    # pre-launch PICK_HELD_INVARIANT rung 4b). A BLOCKED gate carries a classified
    # cause (`outcome.blocked_cause`, the dos.tokens.BlockedReason key the driver
    # mined from the Outcome cell). When that cause's catalog entry self-heals via
    # something OTHER than /replan — an `operator_decision` (`self_heals_via=""`),
    # a false-ship oracle conflation / stale-claim / lying-verdict (`/unstick`),
    # any non-`/replan` remedy — routing it to /replan is structurally wrong: the
    # next /dispatch re-derives the identical BLOCKED and the loop spins
    # BLOCKED→/replan→BLOCKED to the FQ-452 cap (3 iters, ~$15-25/1.5h) before the
    # spin-breaker catches it. So STOP on the FIRST such BLOCKED + surface, instead
    # of spinning. Checked BEFORE the FQ-452 counter so the invariant cause never
    # even increments the stale-stamp streak (it is a different, terminal class).
    # A BLOCKED whose cause IS /replan-curable (lane_soak_gated /
    # lane_all_inflight_or_deferred / data_gated_closeout — a genuine refill/stamp
    # drift) falls through to the FQ-452 path unchanged, as does a BLOCKED with no
    # classified cause (the driver could not name one) — both preserve today's
    # behavior exactly. The operator-decision sub-case is ALSO auto-filed once by
    # the driver's `emit-decision-needed` actuation, so the operator sees it in
    # the findings queue regardless of this stop.
    if outcome.verdict is Verdict.BLOCKED and outcome.blocked_cause:
        _info = blocked_reason_for_key(outcome.blocked_cause)
        if _info is not None and _info.self_heals_via != "/replan":
            _route = (
                "file the operator decision (auto-filed) and resolve it"
                if _info.operator_action_required
                else f"run {_info.self_heals_via or '/unstick'} for the structural fix"
            )
            return LoopDecision(
                action=_STOP,
                next_state=base,
                stop_reason=StopReason.BLOCKED_REDISPATCH_INVARIANT,
                surface=True,
                reason=(
                    f"BLOCKED on {outcome.blocked_cause} ({_info.label}) — a "
                    f"re-dispatch-invariant cause a /replan cannot clear; stop on "
                    f"the first occurrence rather than spinning /replan to the "
                    f"FQ-452 cap. Route: {_route}."
                ),
            )

    # FQ-452 — the non-converging-spin breaker. A STALE-STAMP or BLOCKED gate
    # routes to /replan (under hard) or an inline reconcile (under soft/drive),
    # but when the root cause is plan-meta `remaining:`-list drift the §1.5
    # skip-gate never reconciles, /replan exits UNPRODUCTIVE and the very next
    # /dispatch re-derives the same 0-live gate — forever. Count consecutive
    # STALE-STAMP/BLOCKED gates that DON'T recover; on the Kth, refuse to spin
    # another /replan into the same unreconciled list and STOP so the operator's
    # /replan (now carrying the FQ-452 unconditional remaining-reconcile) runs
    # once and clears it. A LIVE/DRAIN/RACE verdict means the lane moved off the
    # stale-stamp cause → reset the streak. The streak deliberately SURVIVES the
    # intervening REPLAN_DONE (handled in 5b, which never touches it) — that is
    # what lets three dispatch→/replan→dispatch cycles accumulate to the cap.
    is_stale_stamp_class = outcome.verdict in (Verdict.STALE_STAMP, Verdict.BLOCKED)
    if is_stale_stamp_class:
        stale_streak, tripped = _breaker_fail(
            base.consecutive_stale_stamp, base.max_stale_stamp
        )
        base = replace(base, consecutive_stale_stamp=stale_streak)
        if tripped:
            return LoopDecision(
                action=_STOP,
                next_state=base,
                stop_reason=StopReason.STALE_STAMP_UNRECONCILED,
                surface=True,
                reason=(
                    f"{stale_streak} consecutive {outcome.verdict.value} gates "
                    f"that /replan did not reconcile — the picker keeps deriving "
                    f"0-live from a stale plan-meta `remaining:` list; stop and "
                    f"run a /replan that reconciles the list (plan-meta-gardening) "
                    f"rather than spinning another /replan into the same drift"
                ),
            )
    else:
        # LIVE / DRAIN / RACE — the lane moved off the stale-stamp cause.
        base = replace(base, consecutive_stale_stamp=0)

    # QWD benign-drain — a non-DRAIN gate verdict (LIVE / STALE_STAMP / BLOCKED /
    # RACE) means the lane is NOT in the benign genuinely-drained spin: clear the
    # unproductive-replan-drain streak + the prior-DRAIN carry. A DRAIN verdict is
    # the spin's own signal, so it must NOT reset here — its handling (count-check
    # + arm the carry) lives in the `counts_toward_drain` branch below.
    if outcome.verdict is not Verdict.DRAIN:
        base = replace(
            base,
            consecutive_unproductive_replan_drains=0,
            last_gate_was_drain=False,
        )

    # soft/drive can return next_mode="stop" (a true DRAIN or a BLOCKED) — the
    # gate policy already decided the loop stops; name the StopReason from the
    # verdict and pass `surface` through.
    if action.next_mode == "stop":
        stop_reason = (
            StopReason.DRAIN if outcome.verdict is Verdict.DRAIN else StopReason.BLOCKED
        )
        return LoopDecision(
            action=_STOP,
            next_state=base,
            stop_reason=stop_reason,
            surface=action.surface,
            reason=action.reason,
        )

    # reconcile=True (a soft/drive STALE-STAMP) → re-dispatch after an inline
    # stamp-reconcile pass; never counts toward drained-twice.
    if action.reconcile:
        return _continue_or_cap(
            base,
            next_mode=action.next_mode,  # "dispatch"
            reconcile=True,
            reason=action.reason,
        )

    # next_mode == "replan" (hard on any non-LIVE verdict). Now apply the
    # drained-twice rule, keyed on `action.counts_toward_drain` — QWB7's rule
    # is DRAIN-only, so STALE-STAMP/BLOCKED route to /replan but never arm a stop.
    if action.counts_toward_drain:
        # verdict was DRAIN. QWD benign-drain breaker FIRST (FQ-509-sibling): if
        # `max_unproductive_replan_drains` UNPRODUCTIVE /replans have already
        # bracketed DRAINs on this lane, /replan is structurally unable to refill
        # it — the lane is benignly drained (every phase shipped/in-flight). This
        # DRAIN is the one that would route the (K+1)th /replan; STOP instead so
        # the loop does not spin DRAIN→/replan to the iteration cap. This precedes
        # the drained-twice check because a benign-drain streak only accumulates
        # when every intervening /replan was UNPRODUCTIVE — which means
        # `last_replan_drained` is False (it arms only on a PRODUCTIVE /replan), so
        # the two stops are mutually exclusive and the benign one is the correct
        # name for the all-unproductive spin.
        if (
            base.consecutive_unproductive_replan_drains
            >= base.max_unproductive_replan_drains
        ):
            return LoopDecision(
                action=_STOP,
                next_state=base,
                stop_reason=StopReason.BENIGN_DRAIN,
                surface=True,
                reason=(
                    f"DRAIN after {base.consecutive_unproductive_replan_drains} "
                    f"consecutive UNPRODUCTIVE /replans — the lane is genuinely "
                    f"drained but BENIGN (every phase already shipped/in-flight, "
                    f"nothing to refill); /replan cannot refill it, so stop and "
                    f"re-scope (or wait for the in-flight phases to settle) rather "
                    f"than spinning another /replan to the iteration cap"
                ),
            )
        # If the prior iteration was a PRODUCTIVE /replan that followed a DRAIN
        # (last_replan_drained — armed by 4b only when the /replan actually
        # refilled/gardened), /replan tried and could not refill → stop early. An
        # UNPRODUCTIVE /replan never armed the flag (FQ-240), so a DRAIN after a
        # /replan-that-did-nothing falls through to a fresh /replan route below
        # rather than a false drained-twice stop.
        if base.last_replan_drained:
            return LoopDecision(
                action=_STOP,
                next_state=base,
                stop_reason=StopReason.DRAINED_TWICE,
                surface=False,
                reason=(
                    "DRAIN again after a productive /replan — /replan tried but "
                    "could not refill, lane/portfolio genuinely drained"
                ),
            )
        # The normal first drain: route to /replan, disarm the drained-twice flag
        # (it only becomes meaningful *after* the /replan completes — REPLAN_DONE
        # re-arms it) and ARM the benign-drain prior-DRAIN carry so an UNPRODUCTIVE
        # /replan that follows counts toward the benign-drain breaker.
        nxt = replace(base, last_replan_drained=False, last_gate_was_drain=True)
        return _continue_or_cap(
            nxt, next_mode="replan", reason=action.reason
        )

    # STALE-STAMP / BLOCKED under `hard` — route to /replan, do NOT touch the
    # drained-twice flag. A stale-stamp/blocked gate can never arm a false stop.
    return _continue_or_cap(
        base, next_mode="replan", reason=action.reason
    )


def _continue_or_cap(
    next_state: LoopState,
    *,
    next_mode: str,
    reason: str,
    reconcile: bool = False,
) -> LoopDecision:
    """Apply the iteration cap as the LAST gate on an otherwise-continue path.

    A continue decision has been reached. But if the iteration that just ran was
    the `max_iterations`th, the loop is done — there is no slot for `next_mode`.
    Applying the cap here (and only here) means a specific stop reason
    (drained-twice, breaker, rate-limit, launch-fail) always wins over the bare
    cap, because those return a `stop` directly before reaching this helper.
    """
    if next_state.iteration >= next_state.max_iterations:
        return LoopDecision(
            action=_STOP,
            next_state=next_state,
            stop_reason=StopReason.ITERATION_CAP,
            surface=False,
            reason=f"reached max_iterations ({next_state.max_iterations})",
        )
    advanced = replace(next_state, iteration=next_state.iteration + 1)
    return LoopDecision(
        action=_CONTINUE,
        next_state=advanced,
        next_mode=next_mode,
        reconcile=reconcile,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Wait-marker budget (OC2 billing addendum, 2026-05-19).
#
# Every `claude -p` keep-alive marker is its own assistant turn that replays the
# full system+skill+context out of cache. Session 4b4ff97c burned 252 markers /
# ~26M cache-read tokens / ~$7.80 in one run (91% of the run's cache_read). The
# SKILL-level prose caps (/dispatch 2-per-child, /dispatch-loop 4-per-run) are
# prose the model must remember; this is the runtime lever — a pure decision the
# loop can consult before emitting a marker, so a marker that won't earn its
# cache-read cost is refused, not emitted.
#
# `headless_telemetry.py`'s `keepalive_poll` flag (fires at >=5 markers) is the
# POST-HOC surface; this is its PRE-HOC decision-surface sibling. The default
# `max_markers` here (4) matches the /dispatch-loop SKILL's 4-per-run prose cap,
# so the runtime refusal lands one marker before the telemetry flag would fire.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WaitMarkerDecision:
    """Whether to emit one keep-alive wait-marker, and why.

    `allow` — True to emit the marker, False to refuse it. `markers_emitted` is
    the count carried into the *next* decision (incremented iff allowed).
    `reason` is operator-facing.
    """

    allow: bool
    markers_emitted: int
    reason: str


def wait_marker_budget(
    markers_emitted: int,
    max_markers: int = 4,
) -> WaitMarkerDecision:
    """Decide whether the loop should emit one more keep-alive wait-marker.

    PURE — the caller passes the running marker count; this returns the
    allow/refuse decision and the count to carry forward. A refused marker means
    the loop stops holding the turn open with no-op Bash calls and instead waits
    on the existing Bash `<task-notification>` (which fires on real exit
    regardless) — OC1's deterministic orphan sweep is the safety net, so a
    refused marker cannot silently lose a child.

    `max_markers` defaults to 4 — the /dispatch-loop SKILL's per-run prose cap —
    so the runtime refusal fires one marker before `headless_telemetry.py`'s
    `keepalive_poll` flag (>=5) would. Each marker past the budget is pure
    cache-replay cost (~$0.03-0.10) for no work, so the budget is the cost guard
    the prose cap could only suggest.
    """
    if markers_emitted >= max_markers:
        return WaitMarkerDecision(
            allow=False,
            markers_emitted=markers_emitted,
            reason=(
                f"wait-marker budget exhausted ({markers_emitted}/{max_markers}) "
                f"— each further marker replays full context out of cache for no "
                f"work; wait on the Bash task-notification, OC1's orphan sweep "
                f"is the safety net"
            ),
        )
    return WaitMarkerDecision(
        allow=True,
        markers_emitted=markers_emitted + 1,
        reason=f"wait-marker {markers_emitted + 1}/{max_markers} — turn held open",
    )


# The generalized verdict over this same count — `noop_streak.classify` — re-aims the
# arithmetic above off "markers emitted" onto "no-op turns since the last forward
# delta" (docs/259 §Follow-up 1). It is a SIBLING module, not a call from here (no new
# import edge into loop_decide); a test pins that the two agree on the allow/refuse bit.


def propose_tighter_budget(observed_markers: int, current_max: int = 4) -> int:
    """Propose a tighter wait-marker budget from an OBSERVED keep-alive burst. PURE.

    The audit→budget closing of the loop (docs/259 §Follow-up 3): `trajectory-audit`'s
    `keepalive_poll` finding saw `observed_markers` keep-alive markers in one session,
    under a budget of `current_max`; this proposes a TIGHTER cap so the pre-hoc lever
    (`wait_marker_budget`) would have refused sooner. ADVISORY — a proposal a human or
    host consumes, NEVER auto-applied (the kernel computes the number; nothing here
    feeds it back into `wait_marker_budget`, the PDP/PEP line).

    The arithmetic, and why each clamp:

      * `observed_markers - 1` — the doc's proposal: refuse one marker before the
        burst's length, so a repeat of the same wait would land under budget.
      * `min(current_max, …)` — NEVER propose a LOOSER cap than the one already in
        force (monotone-down). This is the load-bearing clamp, and it encodes the
        honest reading of a HUGE burst: 252 markers under a 4-cap proposes
        `min(4, 251) = 4` — i.e. NO tightening — because 252 ≫ 4 does not mean "4 is
        too loose," it means the cap was not ENFORCED (the hook was unwired or
        bypassed). The fix for that is to wire `dos hook marker`, not to lower a number
        that was never consulted; the caller surfaces the `observed > current_max`
        alarm separately. The clamp only bites — produces a genuinely tighter number —
        when the burst sat *within* the current cap yet still tripped the telemetry
        threshold (e.g. observed 5 under a generous current 8 → propose 4).
      * `max(1, …)` — floor at 1: a 0 budget would refuse the FIRST legitimate
        wait-marker outright, trapping a loop that has a real reason to wait one turn.

    So: `max(1, min(current_max, observed_markers - 1))`. Monotone-down, floored at 1,
    and deliberately conservative — a cost-guard proposal never loosens, and a burst
    that proves non-enforcement yields no spurious "lower the cap" noise.
    """
    return max(1, min(current_max, observed_markers - 1))
