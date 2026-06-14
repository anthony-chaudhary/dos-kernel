"""`pickable` — the pre-dispatch gate (docs/168 Concept 2).

The kernel already owns four ground-truth syscalls (`verify`/`oracle`,
`arbitrate`, `liveness`, `scout`/`loop_decide`). But a fleet's throughput is
lost *before a worker launches* to a question the kernel did not yet own:

  > "Is there anything here a worker could actually pick up — and if not, *why
  > not*, precisely enough to route?"

The `job` host answered this in its own code
(`fanout_state._phase_universe_has_pickable_phase`,
`next_up_context._attach_pick_gates`, `plan_pickability._phase_gate_reason`),
and every bug in that re-implementation was a fleet-wide wedge: the drain-trap
(FQ-493 / ASI #475 / RTN / FMP — the pick-count oracle counted a DEFERRED /
DRAFT / operator-gated phase as *pickable*), the FQ-420 un-typed gate-set, the
picker-invisibility gap. The `picker_oracle` module is a **post-hoc audit** that
reconstructs ground truth *after* a dispatch emitted a verdict, to *measure*
picker precision/recall — it is NOT a pre-dispatch gate the picker can call to
decide what to offer. This module is that gate.

The relationship to `picker_oracle`:

  * `pickable.classify`  → the **pre-flight gate** — decide what to offer.
  * `picker_oracle`      → the **post-flight audit** — was the gate right?

They share ONE vocabulary. `HoldReason` is the FINER closed set; it collapses to
the coarse `picker_oracle.NoPickCause` via `.to_no_pick_cause`, so the gate and
the audit can never drift (the same `gate_classify` → `dispatch-loop` shape that
already worked). Concretely: a `HELD(OPERATOR_GATED)` pre-flight is exactly the
case `picker_oracle` audits as `OPERATOR_GATE` — one enum, two consumers.

The keystone is `HoldReason.is_redispatch_invariant`. The single most expensive
recurring mistake was a loop that kept re-dispatching a lane whose *only* hold
reason was one a re-dispatch cannot change (`DRAFT_CLASS`, `OPERATOR_GATED`,
`SOAK_OPEN`, `DEPENDENCY_UNMET`). `loop_decide` reads that flag and gains a clean
rung: a lane held only by re-dispatch-invariant reasons is STOP-now, not
continue — the honest-STOP that was a per-run human override becomes a kernel
rule (docs/168 §5; the same move docs/145 made for the stall reader).

⚓ Pure; host gathers state. Identical seam to `dos.scout.choose` reading a
sibling `HealthVerdict`: `classify(unit_state, …)` is `pure(state)`, all the I/O
(read the plan class, the soak index, the live claims) on the host adapter side.
The host's `_phase_universe_has_pickable_phase` becomes a thin
`all(classify(u, …).held for u in units)` instead of bespoke gate logic.

⚓ Degrade, never crash. A missing key in `unit_state` is treated as its falsy
default; `classify` never raises. The picker-invisibility gap was a *silent*
drop; the cure here is a typed verdict the picker can always produce.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Mapping, Optional

from dos import picker_oracle


# ---------------------------------------------------------------------------
# HoldReason — the finer closed set of reasons a unit is not offerable.
# ---------------------------------------------------------------------------


class HoldReason(str, enum.Enum):
    """Why a declared work unit is NOT offerable to a worker right now.

    The single closed enum of hold reasons — the keystone of docs/168 §2. The
    drain-trap existed because "gated" and "shipped" were collapsed into a single
    boolean ("has a pickable phase: y/n") with no reason. As a kernel enum the
    reasons become the contract every picker shares, and the consequence-routing
    (a `DRAFT_CLASS` hold → `/promote`; an `OPERATOR_GATED` hold → escalate a
    decision; a `SOAK_OPEN` hold → wait, never `/replan`) is derivable from the
    reason instead of re-discovered per incident.

    `str`-valued so it round-trips through a host's JSON/stdout token without a
    lookup table (mirrors `gate_classify.Verdict`, `picker_oracle.NoPickCause`).
    """

    SHIPPED = "SHIPPED"                              # already verified shipped — drop from residual
    IN_FLIGHT = "IN_FLIGHT"                          # a live worker is already on this unit
    SOFT_CLAIMED_ELSEWHERE = "SOFT_CLAIMED_ELSEWHERE"  # a sibling fanout holds a live soft-claim
    DRAFT_CLASS = "DRAFT_CLASS"                      # plan is DRAFT — phases not greenlit for build (FMP / #493)
    OPERATOR_GATED = "OPERATOR_GATED"                # blocked on an open operator decision (ASI / #475)
    SOAK_OPEN = "SOAK_OPEN"                          # a soak deadline has not yet elapsed (RTN)
    DEPENDENCY_UNMET = "DEPENDENCY_UNMET"            # a prerequisite unit has not shipped
    COOLDOWN = "COOLDOWN"                            # tried recently; per-pick cooldown window not elapsed
    UNPARSEABLE = "UNPARSEABLE"                      # the unit's declaration could not be parsed (typed, not silent)
    STALE_CLAIM = "STALE_CLAIM"                      # blocked by a claim that is itself orphaned/stale

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value

    @property
    def is_redispatch_invariant(self) -> bool:
        """True iff a re-dispatch CANNOT change this hold.

        The keystone for the `loop_decide` honest-STOP rung (docs/168 §5). A lane
        held only by these reasons will re-block identically on the next
        iteration — re-dispatching it is pure waste. The four members:

          * DRAFT_CLASS      — only an operator promotion (DRAFT→ACTIVE) un-gates it.
          * OPERATOR_GATED   — only an operator decision un-gates it.
          * SOAK_OPEN        — only the passage of wall-clock time un-gates it
                               (a re-dispatch *now* cannot fast-forward the soak).
          * DEPENDENCY_UNMET — only shipping the prerequisite un-gates it; a
                               re-dispatch of THIS unit cannot.

        The re-dispatch-CURABLE reasons are deliberately NOT here:

          * SHIPPED          — terminal (drop from residual; the loop is done with it,
                               not stuck on it — it never re-enters a dispatch attempt).
          * IN_FLIGHT / SOFT_CLAIMED_ELSEWHERE / STALE_CLAIM — clear when the holder
                               finishes / a claim ages out / a scavenge runs.
          * COOLDOWN         — clears when the cooldown window elapses.
          * UNPARSEABLE      — clears when the host fixes/re-parses the declaration.
        """
        return self in _REDISPATCH_INVARIANT

    @property
    def to_no_pick_cause(self) -> "picker_oracle.NoPickCause":
        """Map onto the coarse post-hoc `picker_oracle.NoPickCause` vocabulary.

        docs/168 §2: the pre-flight gate and the post-hoc audit MUST share one
        vocabulary so a `HELD(reason)` the picker emits is the same thing the
        oracle later audits. The finer `HoldReason` collapses onto the coarse
        `NoPickCause` exactly as `picker_oracle._LEGACY_REASON_ALIASES` already
        collapses `OPERATOR_GATED`/`SOAK_OPEN` → `OPERATOR_GATE`:

          * DRAFT_CLASS / OPERATOR_GATED / SOAK_OPEN          → OPERATOR_GATE
          * IN_FLIGHT / SOFT_CLAIMED_ELSEWHERE / STALE_CLAIM  → STALE_CLAIM
          * SHIPPED / DEPENDENCY_UNMET                        → TRUE_DRAIN
          * COOLDOWN                                          → TRUE_DRAIN
          * UNPARSEABLE                                       → UNCLASSIFIED
        """
        return _TO_NO_PICK_CAUSE[self]

    @property
    def next_action(self) -> str:
        """The canonical unblock action for this hold — one operator-facing line.

        The consequence-routing the class docstring describes ("a `DRAFT_CLASS`
        hold → `/promote`; an `OPERATOR_GATED` hold → escalate a decision; a
        `SOAK_OPEN` hold → wait, never `/replan`") returned AS DATA instead of
        re-discovered per incident or buried in a skill's prose. Co-located with
        the token by design — the same discipline as `reasons.ReasonSpec.fix` —
        so a `HELD(reason)` verdict can carry its own remedy to the surface
        (`dos pickable` prints it, `/dos-promote` routes on it). Total over the
        enum, pinned by `tests/test_pickable.py`.
        """
        return _NEXT_ACTION[self]


_REDISPATCH_INVARIANT: frozenset[HoldReason] = frozenset(
    {
        HoldReason.DRAFT_CLASS,
        HoldReason.OPERATOR_GATED,
        HoldReason.SOAK_OPEN,
        HoldReason.DEPENDENCY_UNMET,
    }
)


# The coarse mapping (docs/168 §2). Total over `HoldReason` — pinned by
# `tests/test_pickable.py` (every member maps to a real `NoPickCause`).
_TO_NO_PICK_CAUSE: dict[HoldReason, "picker_oracle.NoPickCause"] = {
    HoldReason.SHIPPED: picker_oracle.NoPickCause.TRUE_DRAIN,
    HoldReason.IN_FLIGHT: picker_oracle.NoPickCause.STALE_CLAIM,
    HoldReason.SOFT_CLAIMED_ELSEWHERE: picker_oracle.NoPickCause.STALE_CLAIM,
    HoldReason.DRAFT_CLASS: picker_oracle.NoPickCause.OPERATOR_GATE,
    HoldReason.OPERATOR_GATED: picker_oracle.NoPickCause.OPERATOR_GATE,
    HoldReason.SOAK_OPEN: picker_oracle.NoPickCause.OPERATOR_GATE,
    HoldReason.DEPENDENCY_UNMET: picker_oracle.NoPickCause.TRUE_DRAIN,
    HoldReason.COOLDOWN: picker_oracle.NoPickCause.TRUE_DRAIN,
    HoldReason.UNPARSEABLE: picker_oracle.NoPickCause.UNCLASSIFIED,
    HoldReason.STALE_CLAIM: picker_oracle.NoPickCause.STALE_CLAIM,
}


# The unblock action per hold reason (docs/168 §2 consequence-routing as data).
# Total over `HoldReason` — pinned by `tests/test_pickable.py`. Each line names a
# generic next step (a `dos`/`/replan`/`/promote` verb or a wait), never a host
# path: the same vendor-blind discipline as `reasons.ReasonSpec.fix`. The
# re-dispatch-INVARIANT holds need an operator/time/dependency event; the
# CURABLE holds clear on their own, so their action is "wait" (re-dispatching
# now is the waste `is_redispatch_invariant` exists to name).
_NEXT_ACTION: dict[HoldReason, str] = {
    HoldReason.SHIPPED:
        "Nothing to do — the unit is already shipped; drop it from the residual.",
    HoldReason.IN_FLIGHT:
        "Wait for the live worker to finish (racing it is the collision the gate "
        "prevents). It clears on its own when the holder ships or releases.",
    HoldReason.SOFT_CLAIMED_ELSEWHERE:
        "Wait for the sibling fanout's soft-claim to ship or age out; do not "
        "re-dispatch this unit in parallel.",
    HoldReason.DRAFT_CLASS:
        "Promote the plan from DRAFT to ACTIVE (an operator greenlight, e.g. "
        "/dos-promote) — its phases are not yet cleared for build.",
    HoldReason.OPERATOR_GATED:
        "Answer the open operator decision (`dos decisions`); it surfaces once, "
        "then /replan to re-rank.",
    HoldReason.SOAK_OPEN:
        "Wait for the soak window to elapse — a re-dispatch now cannot "
        "fast-forward it. Never /replan to dodge the soak.",
    HoldReason.DEPENDENCY_UNMET:
        "Ship the prerequisite unit first; this one un-gates only when its "
        "dependency lands.",
    HoldReason.COOLDOWN:
        "Wait for the per-pick cooldown window to elapse, then re-attempt.",
    HoldReason.UNPARSEABLE:
        "Inspect the unit's declaration (the deriver could not parse it) and fix "
        "the malformed plan/phase syntax, then re-enumerate.",
    HoldReason.STALE_CLAIM:
        "Let the orphaned claim age out or scavenge it (`dos reconcile`), then "
        "re-attempt — the unit itself is fine.",
}


# ---------------------------------------------------------------------------
# Pickability — the typed verdict.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Pickability:
    """Whether a unit is offerable to a worker right now, and the typed reason
    it is not.

    Frozen + classmethod-constructors, the kernel verdict idiom (mirrors
    `admission.AdmissionVerdict.admit()/refuse()`):

      * `Pickability.OFFERABLE()`        — nothing holds the unit; offer it.
      * `Pickability.HELD(reason, ev)`   — held by exactly one typed `HoldReason`,
                                           with operator-facing `evidence`.

    `held` is the load-bearing field a picker branches on (the inverse of
    `OFFERABLE`); `reason` is `None` iff `held is False`.
    """

    held: bool
    reason: Optional[HoldReason] = None
    evidence: str = ""

    @classmethod
    def OFFERABLE(cls) -> "Pickability":
        """An offerable verdict — no hold applies; a worker may pick this up."""
        return cls(held=False, reason=None, evidence="")

    @classmethod
    def HELD(cls, reason: HoldReason, evidence: str = "") -> "Pickability":
        """A held verdict carrying the single typed `reason` it is not offerable
        and an operator-facing `evidence` line."""
        return cls(held=True, reason=reason, evidence=evidence)

    @property
    def is_redispatch_invariant(self) -> bool:
        """True iff this verdict is HELD by a re-dispatch-invariant reason.

        The convenience the `loop_decide` rung reads: `held` AND the reason is
        one a re-dispatch cannot change. `OFFERABLE` is never invariant (it is
        not held at all)."""
        return self.held and self.reason is not None and self.reason.is_redispatch_invariant


# ---------------------------------------------------------------------------
# classify — the pure pre-dispatch gate.
# ---------------------------------------------------------------------------


def classify(
    unit_state: Mapping,
    *,
    now_ms: int,
    policy: Optional[Mapping] = None,
) -> Pickability:
    """Decide whether a declared work unit is offerable. PURE — no I/O.

    `unit_state` is a dict the HOST pre-gathers (all the file/git/registry reads
    happen on the adapter side — the same seam as `dos.scout.choose` reading a
    sibling `HealthVerdict`). Recognised keys, each defaulting to its falsy value
    when absent (degrade-never-crash):

      * shipped: bool                 — the unit is already verified shipped.
      * in_flight: bool               — a live worker is on this unit now.
      * soft_claimed_elsewhere: bool  — a sibling fanout holds a live soft-claim.
      * plan_class: str               — the plan's class ("DRAFT" → DRAFT_CLASS).
      * operator_gated: bool          — blocked on an open operator decision.
      * soak_open: bool               — a soak deadline has not yet elapsed.
      * dependency_unmet: bool        — a prerequisite unit has not shipped.
      * cooldown_until_ms: int | None — per-pick cooldown wall; held iff `now_ms`
                                        is strictly before it.
      * unparseable: bool             — the declaration could not be parsed.

    `now_ms` is the caller's clock (an input, never read from the wall here — the
    same discipline as `liveness.classify`), used only for the COOLDOWN check.
    `policy` is reserved for host-declared knobs (docs/168 §"mechanism-not-
    policy"); unused today, accepted so the signature is stable.

    Precedence (most-terminal / most-specific first, documented in docs/168 §2):

      1. SHIPPED                — terminal; nothing else matters once it shipped.
      2. UNPARSEABLE            — a typed "I could not parse this" beats every
                                  content gate (a gate read off an unparseable
                                  declaration is meaningless; surface the parse
                                  failure instead of a derived hold).
      3. the in-flight family   — IN_FLIGHT, then SOFT_CLAIMED_ELSEWHERE, then
                                  STALE_CLAIM (a live worker / claim wins over a
                                  class/gate/soak/dep reason — the unit IS being
                                  worked, the gate would only matter once it frees).
      4. DRAFT_CLASS            — the plan class gate.
      5. OPERATOR_GATED         — an open operator decision.
      6. SOAK_OPEN              — an unelapsed soak.
      7. DEPENDENCY_UNMET       — an unshipped prerequisite.
      8. COOLDOWN               — the per-pick cooldown wall (the most transient,
                                  curable by time alone — checked last).

    Returns `Pickability.OFFERABLE()` when no hold applies.
    """
    s = unit_state or {}

    def _b(key: str) -> bool:
        # Defensive truthiness — a missing key is its falsy default; never raise.
        try:
            return bool(s.get(key))
        except AttributeError:  # pragma: no cover - non-Mapping degrade path
            return False

    # 1. SHIPPED — terminal. Once verified shipped the unit leaves the residual;
    #    no later gate can resurrect it.
    if _b("shipped"):
        return Pickability.HELD(
            HoldReason.SHIPPED,
            "unit is verified shipped — drop from the residual",
        )

    # 2. UNPARSEABLE — a typed parse failure beats every content gate. The
    #    picker-invisibility gap was a SILENT drop; this is the cure — surface a
    #    refusal reason instead of an empty universe.
    if _b("unparseable"):
        return Pickability.HELD(
            HoldReason.UNPARSEABLE,
            "the unit's declaration could not be parsed — surfaced as a typed "
            "refusal rather than silently dropped",
        )

    # 3. The in-flight family — a live worker / claim on the unit. It IS being
    #    worked (or held by a sibling), so a class/gate/soak reason is moot until
    #    it frees; report the live holder, most-specific first.
    if _b("in_flight"):
        return Pickability.HELD(
            HoldReason.IN_FLIGHT,
            "a live worker is already on this unit",
        )
    if _b("soft_claimed_elsewhere"):
        return Pickability.HELD(
            HoldReason.SOFT_CLAIMED_ELSEWHERE,
            "a sibling fanout holds a live soft-claim on this unit",
        )
    if _b("stale_claim"):
        return Pickability.HELD(
            HoldReason.STALE_CLAIM,
            "blocked by a claim that is itself orphaned/stale",
        )

    # 4. DRAFT_CLASS — the plan is DRAFT; its phases are not greenlit for build.
    #    Only an operator promotion (DRAFT→ACTIVE) un-gates it — a /replan cannot
    #    (FMP / decision #493). Re-dispatch-invariant.
    plan_class = ""
    try:
        plan_class = str(s.get("plan_class") or "").strip().upper()
    except AttributeError:  # pragma: no cover - non-Mapping degrade path
        plan_class = ""
    if plan_class == "DRAFT":
        return Pickability.HELD(
            HoldReason.DRAFT_CLASS,
            "plan is DRAFT-class — phases are not greenlit for build; only an "
            "operator promotion (DRAFT→ACTIVE) un-gates it, not a /replan",
        )

    # 5. OPERATOR_GATED — an open operator decision blocks it (ASI / #475).
    #    Re-dispatch-invariant: only an operator answer un-gates it.
    if _b("operator_gated"):
        return Pickability.HELD(
            HoldReason.OPERATOR_GATED,
            "blocked on an open operator decision — escalate the decision; a "
            "re-dispatch cannot answer it",
        )

    # 6. SOAK_OPEN — a soak deadline has not yet elapsed (RTN). Re-dispatch-
    #    invariant: only the passage of time un-gates it; never /replan, wait.
    if _b("soak_open"):
        return Pickability.HELD(
            HoldReason.SOAK_OPEN,
            "a soak deadline has not yet elapsed — wait for the soak to close; a "
            "re-dispatch now cannot fast-forward it",
        )

    # 7. DEPENDENCY_UNMET — a prerequisite unit has not shipped. Re-dispatch-
    #    invariant: only shipping the prerequisite un-gates THIS unit.
    if _b("dependency_unmet"):
        return Pickability.HELD(
            HoldReason.DEPENDENCY_UNMET,
            "a prerequisite unit has not shipped — ship the dependency first; a "
            "re-dispatch of this unit cannot",
        )

    # 8. COOLDOWN — the per-pick cooldown wall. The most transient hold (curable
    #    by wall-clock time alone), so it is checked last. Held iff `now_ms` is
    #    strictly before the wall; a missing/None/zero wall never holds.
    cooldown_until = s.get("cooldown_until_ms")
    if cooldown_until is not None:
        try:
            wall = int(cooldown_until)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            wall = 0
        if wall > 0 and now_ms < wall:
            return Pickability.HELD(
                HoldReason.COOLDOWN,
                f"per-pick cooldown active until {wall}ms (now {now_ms}ms) — "
                f"the window has not elapsed",
            )

    return Pickability.OFFERABLE()
