"""INTERVENTION — the typed actuation ladder: *how hard should we act on a verdict?*

docs/143 §13 (the EnterpriseOps-Gym double-down). The whole history of DOS hardened the
*verdict* — the ORACLE→JUDGE→HUMAN trust ladder, the forgeability axiom, the evidence
floor. The live benchmark run (RESULTS.md "⚑ KEY DATA POINT") proved that is **necessary
but not sufficient**: a verdict that was *sound* (0 % false-nudge, 83 % recall) was still
**net-harmful** (−9 pp) because the *intervention* attached to it (skip-the-dispatch +
re-prompt) derailed the model mid-plan — even on a true-positive catch. **Detector
soundness and intervention safety are orthogonal properties.** This module is the next
frontier: hardening the *actuation*, the docs/99 / docs/126 PDP-vs-PEP seam.

The actuation dual of the closed refusal vocabulary
===================================================

`dos.reasons` gives the kernel a **closed reason set** — a no-pick verdict may carry only
a declared `reason_class`. This module gives the kernel the symmetric thing it lacked: a
**closed intervention set** — a verdict may be acted on only at a declared *strength*, on
a documented disruption-cost order:

    OBSERVE  <  WARN  <  BLOCK  <  DEFER
     rank 0     rank 10  rank 20   rank 30

with the **default the least-disruptive that still informs** (WARN). The shape is lifted
verbatim from `dos.reasons` — a `str`-enum vocabulary (`Intervention`), a frozen
`InterventionSpec` carrying the rung's data, a frozen `InterventionLadder` registry that
is closed + ordered + `extend`-not-mutate, a `BASE_INTERVENTIONS` built-in, and a
`dos.toml [intervention]` on-ramp. The *mechanism* (the typed vocabulary + the pure
verdict→intervention map) is kernel; the *actuation* (returning a synthetic result,
skipping a dispatch) lives in a consumer (`benchmark.enterpriseops.dos_react`), never here
— the kernel **reports a recommendation, it never acts** (the advisory-only doctrine that
makes DOS a PDP with no PEP).

The measured cost order supersedes §13.1's prose order
======================================================

§13.1 sketched the order `… DEFER ‹ BLOCK`. **The live run inverts it.** A DEFER (skip the
dispatch, re-prompt) *spends the agent's turn* — that is the −9 pp posture (RESULTS.md
lines 105-120: even a true-positive catch broke a *different* downstream step). A BLOCK
done right (return a synthetic "that id is unresolved — here is the read tool" result in
place of the mutation) *preserves the turn*: the agent gets the corrective observation
WITHOUT a wasted iteration. So **BLOCK is strictly less disruptive than DEFER** and ranks
below it (`rank 20 < rank 30`). This is deliberate and load-bearing — a later editor must
NOT "fix" the order back to §13.1's prose. `tests/test_intervention.py::test_block_cheaper_
than_defer` pins it.

Two-level closure (the hackability decision)
============================================

The *vocabulary* `Intervention` is a fixed 4-member kernel enum: it is the ABI a consumer
actuates against — a consumer cannot perform a rung the kernel does not define, so the set
of *actions* is closed at the kernel. The *ladder data* (ranks, summaries, a host-added
rung that still maps onto the closed `dispatches`/`returns_synthetic` actuation contract)
is extensible via `extend()` / `dos.toml`, exactly as `ReasonRegistry`. A host adds a
rung-with-rank as data; a consumer reads its actuation behavior off the `dispatches` /
`returns_synthetic` fields, never off the token name.

Confidence-gated escalation (docs/143 §13.3)
============================================

The −9 pp came from disruption spent on catches that did not matter to the verifier. The
fix is to couple intervention *strength* to verdict *confidence*: a whole-value-absent id
(a high-confidence mint) earns the strong-but-cheap BLOCK; a one-component-missing
composite (lower confidence) earns only a WARN. `assess_confidence` reads that signal off
a `ProvenanceVerdict`'s real fields, and `choose_intervention` maps it through a
`InterventionPolicy` that enforces the **refuse-LESS-only** direction: a lower-confidence
verdict can only ever map to a *no-more*-disruptive rung (the admission-floor / fail-to-
abstain discipline re-aimed at actuation). That guarantee is checked TWICE — once at policy
construction (vs `BASE_INTERVENTIONS`, the loud early catch) and again inside
`choose_intervention` against the ladder ACTUALLY clamped with (`policy.validate_against`).
The second check is load-bearing and not redundant: the construction check only constrains
the BASE rung order, so a rank-reordered `ladder` passed as `choose_intervention`'s third
argument would otherwise void it (the docs/144 adversarial-review finding). On such a
mismatch the verdict path fails SAFE to the ladder default rather than escalate — so the
guarantee is a property of the ladder-in-use, not merely of BASE.

⚓ Pure kernel, no I/O, advisory only (the dos idiom — mirrors `dos.reasons`,
`liveness.classify`, `arg_provenance.classify_call`): every function here is data-in /
verdict-out. The module returns *decisions* and *payloads*; it dispatches nothing, mutates
nothing, reads no clock and no disk (except the `dos.toml` on-ramp, the boundary loader
that mirrors `reasons.load_from_toml`).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from dos.arg_provenance import ProvenanceStance, ProvenanceVerdict


# ---------------------------------------------------------------------------
# The closed, ordered intervention vocabulary — the actuation ABI.
# ---------------------------------------------------------------------------
class Intervention(str, enum.Enum):
    """The strength a consumer may act on a verdict — closed, the `WedgeReason` analogue.

    `str`-valued so it round-trips a CLI token / JSON / env var without a lookup table (the
    `Accountability` / `ProvenanceStance` idiom). The vocabulary is closed AT THE KERNEL: a
    consumer cannot actuate a rung the kernel does not define. The disruption-cost ORDER
    lives on the `InterventionSpec.rank` (see the ladder), not on the enum's declaration
    order — `OBSERVE < WARN < BLOCK < DEFER` (BLOCK below DEFER; see the module docstring).

      OBSERVE — record the verdict only; the agent never sees it; the real call dispatches.
                The zero-disruption rung (a pure sensor).
      WARN    — annotate the call with the verdict (the model sees it next turn) AND still
                dispatch. The default: the least-disruptive rung that still INFORMS.
      DEFER   — RECOMMEND the consumer skip this dispatch and re-prompt; the agent retries.
                Costs the turn (the live −9 pp posture). The most disruptive rung; opt-in.
      BLOCK   — RECOMMEND the consumer refuse the call but return a SYNTHETIC corrective
                result in its place ("that id is unresolved — here is the read tool"), so
                the agent gets a corrective observation WITHOUT losing the turn. Strong but
                turn-preserving — strictly less disruptive than DEFER.

    Note the advisory wording: DEFER/BLOCK *RECOMMEND* a consumer action; the enum is a
    recommendation, never an action the kernel performs.
    """

    OBSERVE = "OBSERVE"
    WARN = "WARN"
    DEFER = "DEFER"
    BLOCK = "BLOCK"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Confidence(str, enum.Enum):
    """The mint-confidence rung read off a `ProvenanceVerdict` — gates intervention strength.

    `str`-valued (the `Intervention` idiom).

      HIGH — a whole-value-absent mint: exactly one data-bearing component, and it is
             unmatched (`len(components_checked) == 1 and len(components_unmatched) == 1`).
             The maximally-confident "the model invented this id" shape. NB this is keyed on
             the verdict's *component* shape, which a scalar mint AND a degenerate one-element
             container (`['INC9999999']`) both collapse to identically — the verdict does not
             record the scalar-vs-container distinction, and a one-element container IS a
             whole-value mint, so both correctly read HIGH (and HIGH maps to the turn-
             *preserving* BLOCK, not a turn-spending escalation).
      LOW  — a partial / composite / MULTI-component container mint: ≥1 component traced and
             ≥1 did not, OR the value decomposed into several components (a cross-leaf
             superset, `len(components_checked) > 1`) from which whole-value absence cannot be
             proven. The under-confident shape — biases to the LESS disruptive rung.
      NONE — `believe=True` / no UNSUPPORTED arg — nothing was minted. No intervention beyond
             OBSERVE.
    """

    HIGH = "HIGH"
    LOW = "LOW"
    NONE = "NONE"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# ---------------------------------------------------------------------------
# One rung as data — the InterventionSpec (mirror dos.reasons.ReasonSpec).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class InterventionSpec:
    """One intervention rung, as data — the unit a workspace declares to add a rung.

    The actuation dual of `ReasonSpec`: where a `ReasonSpec` carries a refusal's `fix`
    (what the OPERATOR does), an `InterventionSpec` carries `actuation` (what a CONSUMER
    does). The disruption cost lives HERE as `rank` (not in a separate map) so it cannot
    drift from the rung — the `reasons.category_map` "no second map to keep in sync"
    discipline.

    Fields:
      token             — the `Intervention` string (canonical UPPER on `.key`).
      rank              — DISRUPTION COST as a strict total order (lower = less disruptive).
                          The ladder validates ranks are unique so the order is total.
      summary           — man NAME line: what the rung MEANS.
      actuation         — man line: what a CONSUMER does on this rung (the `ReasonSpec.fix`
                          dual). Curated prose co-located with the token (DOM Design-rule 1).
      dispatches        — does the REAL tool call still fire on this rung? OBSERVE/WARN True;
                          DEFER/BLOCK False. The data a consumer reads to decide whether to
                          run the call — never inferred from the token name.
      returns_synthetic — does the consumer return a SYNTHETIC corrective result in place of
                          the withheld call? Only BLOCK. Implies `not dispatches` (validated).
      see_also          — man SEE ALSO pointers.
    """

    token: str
    rank: int
    summary: str
    actuation: str
    dispatches: bool
    returns_synthetic: bool = False
    see_also: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.token or not self.token.strip():
            raise ValueError("InterventionSpec.token must be a non-empty string")
        if self.rank < 0:
            raise ValueError(f"InterventionSpec {self.token!r} rank must be >= 0")
        if self.returns_synthetic and self.dispatches:
            raise ValueError(
                f"InterventionSpec {self.token!r}: returns_synthetic implies the real "
                f"call is withheld — dispatches must be False"
            )

    @property
    def key(self) -> str:
        """The normalized lookup key (UPPER, stripped) — what the ladder matches."""
        return self.token.strip().upper()


# ---------------------------------------------------------------------------
# The closed, ordered registry — the InterventionLadder (mirror ReasonRegistry).
# ---------------------------------------------------------------------------
def _coerce_token(token: "str | Intervention | None") -> str | None:
    if token is None:
        return None
    if isinstance(token, Intervention):
        return token.value
    return str(token)


@dataclass(frozen=True)
class InterventionLadder:
    """A closed, ordered set of `InterventionSpec`s — the active actuation vocabulary.

    Immutable: `extend()` returns a NEW ladder (a process's active ladder is a value on the
    `SubstrateConfig`, never a mutable global a plugin scribbles on — the `ReasonRegistry`
    discipline that keeps "closed set" a real property). The ranks form a STRICT total
    order (`__post_init__` rejects a duplicate rank), so `escalate`/`de_escalate`/`clamp`
    are well-defined.
    """

    specs: tuple[InterventionSpec, ...] = ()
    default_token: str = "WARN"

    def __post_init__(self) -> None:
        seen_tok: set[str] = set()
        seen_rank: set[int] = set()
        for s in self.specs:
            if s.key in seen_tok:
                raise ValueError(
                    f"duplicate intervention token {s.token!r} in ladder — a rung is "
                    f"declared exactly once (later declarations would shadow silently)"
                )
            if s.rank in seen_rank:
                raise ValueError(
                    f"duplicate rank {s.rank} ({s.token!r}) — the disruption-cost order "
                    f"must be a STRICT total order so escalate/clamp are well-defined"
                )
            seen_tok.add(s.key)
            seen_rank.add(s.rank)
        if self.specs and self.get(self.default_token) is None:
            raise ValueError(
                f"default_token {self.default_token!r} is not a declared rung "
                f"(known: {sorted(seen_tok)})"
            )

    # -- lookup ------------------------------------------------------------
    def get(self, token: "str | Intervention | None") -> InterventionSpec | None:
        """The `InterventionSpec` for `token`, case-insensitive; coerces an `Intervention`."""
        t = _coerce_token(token)
        if not t:
            return None
        k = t.strip().upper()
        for s in self.specs:
            if s.key == k:
                return s
        return None

    def is_known(self, token: "str | Intervention | None") -> bool:
        return self.get(token) is not None

    def tokens(self) -> tuple[str, ...]:
        """Every declared token, in declaration order."""
        return tuple(s.key for s in self.specs)

    def by_rank(self) -> tuple[InterventionSpec, ...]:
        """Specs sorted by `rank` ascending — the canonical least→most-disruptive order."""
        return tuple(sorted(self.specs, key=lambda s: s.rank))

    # -- the ordering algebra (well-defined on the strict total order) ------
    def rank_of(self, token: "str | Intervention") -> int:
        """The disruption rank of a token; raises `KeyError` on an unknown one.

        Deliberately NOT a forgiving default — a gate that treats an unknown rung as rank 0
        (least disruptive) could be tricked into never intervening, so an unknown token is a
        loud error, not a silent floor.
        """
        spec = self.get(token)
        if spec is None:
            raise KeyError(f"unknown intervention rung {token!r}")
        return spec.rank

    def _at_offset(self, token: "str | Intervention", offset: int) -> InterventionSpec:
        ordered = self.by_rank()
        idx = next((i for i, s in enumerate(ordered) if s.rank == self.rank_of(token)), None)
        if idx is None:  # pragma: no cover - rank_of would have raised
            raise KeyError(f"unknown intervention rung {token!r}")
        j = max(0, min(len(ordered) - 1, idx + offset))
        return ordered[j]

    def escalate(self, token: "str | Intervention", *, by: int = 1) -> InterventionSpec:
        """The next-MORE-disruptive rung, clamped at the top of the ladder."""
        return self._at_offset(token, abs(by))

    def de_escalate(self, token: "str | Intervention", *, by: int = 1) -> InterventionSpec:
        """The next-LESS-disruptive rung, clamped at the bottom of the ladder."""
        return self._at_offset(token, -abs(by))

    def clamp(self, token: "str | Intervention", *, floor: "str | Intervention",
              ceiling: "str | Intervention") -> InterventionSpec:
        """Move `token` into the rank window `[floor, ceiling]`.

        If `rank(floor) > rank(ceiling)` (an inverted window), the CEILING wins — a
        deterministic tie-break that fails toward the LESS-disruptive rung (the fail-safe
        direction; a buggy window can never over-disrupt). Returns the spec.
        """
        r = self.rank_of(token)
        rf = self.rank_of(floor)
        rc = self.rank_of(ceiling)
        if rf > rc:
            return self.get(ceiling)  # inverted window → fail toward less disruptive
        target = max(rf, min(rc, r))
        return next(s for s in self.specs if s.rank == target)

    def default(self) -> InterventionSpec:
        """The `default_token` spec (the least-disruptive-that-still-informs, WARN)."""
        spec = self.get(self.default_token)
        if spec is None:  # pragma: no cover - __post_init__ guarantees it
            raise KeyError(f"default_token {self.default_token!r} not in ladder")
        return spec

    # -- the data-driven actuation contract --------------------------------
    def dispatches(self, token: "str | Intervention") -> bool:
        """Does the REAL call fire on this rung? Unknown → conservative `False` (an unknown
        rung withholds the call — the `reasons.is_refusal` fail-closed analogue)."""
        spec = self.get(token)
        return spec.dispatches if spec is not None else False

    def actuates(self, token: "str | Intervention") -> bool:
        """True iff this rung WITHHOLDS the real call (`not dispatches`) — the data-driven
        actuation-set test (the `reasons.refusal_tokens` analogue). The eval/consumer read
        this off the `dispatches` DATA, never a hardcoded `{DEFER, BLOCK}` — a host-added
        rung is bucketed correctly by construction."""
        return not self.dispatches(token)

    def returns_synthetic(self, token: "str | Intervention") -> bool:
        """True iff the consumer returns a synthetic corrective result on this rung."""
        spec = self.get(token)
        return bool(spec.returns_synthetic) if spec is not None else False

    def disruption_cost(self, token: "str | Intervention", *, normalized: bool = True) -> float:
        """The rung's disruption cost. `normalized` (default) scales the rank onto `[0, 1]`
        over the ladder's span (min-rank → 0.0, max-rank → 1.0); else the raw rank as a
        float. Pure arithmetic — the eval reads this, never a hardcoded per-rung constant."""
        r = float(self.rank_of(token))
        if not normalized:
            return r
        ordered = self.by_rank()
        if not ordered:  # pragma: no cover - empty ladder
            return 0.0
        lo, hi = ordered[0].rank, ordered[-1].rank
        if hi == lo:
            return 0.0
        return (r - lo) / (hi - lo)

    # -- composition (the hackability verb) --------------------------------
    def extend(self, more: Iterable[InterventionSpec]) -> "InterventionLadder":
        """Return a NEW ladder with `more` appended. The one way to add a rung. Raises on a
        token OR a rank collision (the strict-total-order + declared-exactly-once guards)."""
        return InterventionLadder(
            specs=tuple(self.specs) + tuple(more), default_token=self.default_token
        )


# ---------------------------------------------------------------------------
# The built-in ladder. Ranks gapped by 10 so a host can insert a custom rung
# between any two. Order: OBSERVE(0) < WARN(10) < BLOCK(20) < DEFER(30) — BLOCK
# below DEFER because BLOCK preserves the turn (the measured order, see docstring).
# ---------------------------------------------------------------------------
BASE_INTERVENTIONS = InterventionLadder(default_token="WARN", specs=(
    InterventionSpec(
        token="OBSERVE",
        rank=0,
        summary="Record the verdict only; the agent never sees it, the call dispatches.",
        actuation="Append the verdict to the run ledger; dispatch the call unchanged.",
        dispatches=True,
        returns_synthetic=False,
        see_also=("intervention WARN", "intervention-eval"),
    ),
    InterventionSpec(
        token="WARN",
        rank=10,
        summary="Annotate the call with the verdict (model sees it next turn) and still dispatch.",
        actuation="Attach the verdict as an advisory note to the result; dispatch the call. "
                  "The model is informed and may self-correct next turn, without losing this one.",
        dispatches=True,
        returns_synthetic=False,
        see_also=("intervention OBSERVE", "intervention BLOCK", "wedge"),
    ),
    InterventionSpec(
        token="BLOCK",
        rank=20,
        summary="Refuse the minted call but return a synthetic corrective result — the turn is NOT lost.",
        actuation="Do NOT dispatch; the consumer returns a synthetic 'id unresolved; here is the "
                  "read tool' result in place of the mutation — a corrective observation, no wasted turn.",
        dispatches=False,
        returns_synthetic=True,
        see_also=("intervention DEFER", "dos apply", "arg_provenance"),
    ),
    InterventionSpec(
        token="DEFER",
        rank=30,
        summary="Skip this dispatch; let the agent retry (costs the turn — the -9pp posture).",
        actuation="Do NOT dispatch; re-prompt the agent so it can resolve the id and retry. "
                  "The most disruptive rung — opt-in only (BLOCK is cheaper and usually preferred).",
        dispatches=False,
        returns_synthetic=False,
        see_also=("intervention WARN", "intervention BLOCK"),
    ),
))


# ---------------------------------------------------------------------------
# Confidence extraction — read the mint-confidence rung off a ProvenanceVerdict.
# ---------------------------------------------------------------------------
def assess_confidence(verdict: ProvenanceVerdict) -> Confidence:
    """The mint-confidence of a `ProvenanceVerdict` — HIGH / LOW / NONE. PURE.

    Reads only real `arg_provenance` fields: `verdict.believe`, `verdict.unsupported`,
    `verdict.args`, and each `ArgProvenance.stance` / `.components_checked` /
    `.components_unmatched`. The signal is the SHAPE of the mint:

      * HIGH — a whole-value-absent SCALAR mint: an UNSUPPORTED arg whose
               `components_checked` is a single component AND that component is unmatched
               (`len(checked) == 1 and len(unmatched) == 1`). This is exactly the shape
               `_data_bearing_components` produces for a scalar minted id (e.g. minted
               `INC9999999` → `components_checked=("9999999",)`,
               `components_unmatched=("9999999",)`). The maximally-confident mint.
      * LOW  — anything else that fired: a composite where some components traced but ≥1 did
               not (one-component-missing), or a MULTI-component container/arg whose
               `components_checked` is a cross-leaf superset (`len > 1`) we cannot read whole-
               value absence from. The under-confident shape. (A *one-element* container
               collapses to the single-component scalar shape above and reads HIGH — it is a
               whole-value mint; the verdict does not preserve the scalar-vs-1-list
               distinction, and treating it as HIGH→BLOCK is the turn-preserving, not the
               turn-spending, escalation.)
      * NONE — `believe=True` (or no UNSUPPORTED arg) — nothing minted.

    FAIL-SAFE (the load-bearing direction): every MULTI-component ambiguity resolves to LOW,
    the LESS disruptive rung. The `matched_in` field is deliberately NOT read — it is polluted by
    grammar/substring hits (an `INC` prefix substringing env bytes), so a `not matched_in`
    conjunct would make HIGH never fire. Multi-arg aggregation: ANY single HIGH arg makes
    the whole call HIGH (escalate to the strongest mint signal); every other fired shape is
    LOW.
    """
    if verdict.believe or not verdict.unsupported:
        return Confidence.NONE
    saw_high = False
    for a in verdict.args:
        if a.stance is not ProvenanceStance.UNSUPPORTED:
            continue
        checked = a.components_checked
        unmatched = a.components_unmatched
        # HIGH iff a single data-bearing component that is itself unmatched — the only shape
        # from which whole-value absence is provable. A container's components_checked is a
        # cross-leaf superset (len > 1) → cannot prove whole-value absence → LOW.
        if len(checked) == 1 and len(unmatched) == 1:
            saw_high = True
        # else: composite / container / one-of-many-missing → contributes LOW
    return Confidence.HIGH if saw_high else Confidence.LOW


# ---------------------------------------------------------------------------
# The confidence-gating policy — couple intervention strength to confidence.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class InterventionPolicy:
    """The knobs mapping a confidence rung to an intervention — mechanism kernel, knobs config.

    The `__post_init__` makes the **fail-safe direction structural**: a lower-confidence
    verdict can only ever map to a *no-more-disruptive* rung (refuse-LESS-only), and every
    inverted / dead-letter combination is rejected at construction, so a buggy or hostile
    policy cannot escalate past what confidence warrants. Defaults validated against
    `BASE_INTERVENTIONS`.

      on_high_confidence — a whole-value-absent scalar mint → the non-disruptive PEP (BLOCK).
      on_low_confidence  — a partial / composite / container mint → inform, still dispatch (WARN).
      on_none            — `believe=True` → record only (OBSERVE).
      floor              — the least-disruptive rung ever applied to a FIRED verdict (WARN).
      ceiling            — the most-disruptive an escalation may reach (BLOCK; DEFER is opt-in
                           — a host raises the ceiling to "DEFER" to enable the turn-spending
                           rung).
    """

    on_high_confidence: str = "BLOCK"
    on_low_confidence: str = "WARN"
    on_none: str = "OBSERVE"
    floor: str = "WARN"
    ceiling: str = "BLOCK"

    def __post_init__(self) -> None:
        # Construction-time validation is against BASE_INTERVENTIONS — the rungs a policy
        # is *expected* to name. This catches the common mistake at the earliest point. But
        # it is NOT the whole story: `choose_intervention` clamps against a `ladder`
        # PARAMETER that may differ from BASE (a host-tuned ladder, a test ladder). The
        # rank-order guarantees below only hold for the ladder they were checked against, so
        # `choose_intervention` RE-validates the policy against the actual clamp-ladder (see
        # `validate_against` + its call site). Construction-validation is the fast loud check;
        # the verdict path is where the guarantee is truly enforced, on the ladder in hand.
        self.validate_against(BASE_INTERVENTIONS)

    def validate_against(self, ladder: "InterventionLadder") -> None:
        """Raise iff this policy's rung order is unsafe *on `ladder`* — the refuse-LESS-only
        guarantee, checked against the ladder a caller will actually clamp with.

        Pulled out of `__post_init__` so the SAME checks run at construction (vs BASE) AND in
        `choose_intervention` (vs the passed ladder). The defect this closes: a rank-reordered
        ladder (BLOCK below WARN) passed as `choose_intervention`'s third arg would silently
        void the construction-time order checks, letting a LOW mint resolve harder than a HIGH
        one. Re-validating here makes the cross-confidence monotonicity a property of the
        ladder-in-use, not just of BASE (the adversarial-review finding, docs/144).
        """
        for f in ("on_high_confidence", "on_low_confidence", "on_none", "floor", "ceiling"):
            v = getattr(self, f)
            if not ladder.is_known(v):
                raise ValueError(
                    f"InterventionPolicy.{f}={v!r} is not a known rung "
                    f"(known: {list(ladder.tokens())})"
                )
        rk = ladder.rank_of
        if rk(self.floor) > rk(self.ceiling):
            raise ValueError(
                f"floor {self.floor!r} is more disruptive than ceiling {self.ceiling!r}"
            )
        # refuse-LESS-only, the floor axis (the adversarial-review BUG-1 finding): the floor
        # is a LOWER bound on a FIRED verdict's rung, so it must never EXCEED the least-
        # disruptive confidence mapping — else `clamp(on_low, floor=…)` would silently
        # escalate a LOW mint past the WARN the policy declared for it (a hole the other
        # checks miss because they only relate the on_* rungs to each other). The least
        # confidence-mapped rung that the floor can clamp is `on_low_confidence` (NONE
        # short-circuits before the floor, so it is floor-immune and excluded here).
        if rk(self.floor) > rk(self.on_low_confidence):
            raise ValueError(
                f"floor {self.floor!r} is more disruptive than on_low_confidence "
                f"{self.on_low_confidence!r} — the floor must not escalate a low-confidence "
                f"mint past its declared rung (refuse-LESS-only, the floor axis)"
            )
        # refuse-LESS-only: a lower-confidence verdict must map no harder than a higher one.
        if rk(self.on_low_confidence) > rk(self.on_high_confidence):
            raise ValueError(
                f"on_low_confidence {self.on_low_confidence!r} is more disruptive than "
                f"on_high_confidence {self.on_high_confidence!r} — a lower-confidence mint "
                f"must never intervene harder (refuse-LESS-only)"
            )
        # a no-mint (NONE) call must not out-disrupt a low-confidence mint.
        if rk(self.on_none) > rk(self.on_low_confidence):
            raise ValueError(
                f"on_none {self.on_none!r} is more disruptive than on_low_confidence "
                f"{self.on_low_confidence!r} — a clean call must never intervene harder "
                f"than a low-confidence mint"
            )
        # every confidence-mapped rung must be reachable under the ceiling (no dead letter).
        for f in ("on_high_confidence", "on_low_confidence", "on_none"):
            v = getattr(self, f)
            if rk(v) > rk(self.ceiling):
                raise ValueError(
                    f"{f} {v!r} exceeds ceiling {self.ceiling!r} — a dead-letter rung "
                    f"(raise the ceiling to reach it)"
                )


DEFAULT_POLICY = InterventionPolicy()


@dataclass(frozen=True)
class InterventionDecision:
    """The advisory recommendation a consumer reads — what to do, at what strength, and why.

    The kernel returns this; the consumer ACTS on it. It is a recommendation, never an act.
    `disruption_cost` is the normalized [0,1] cost of the chosen rung (the eval reads it).
    """

    intervention: Intervention
    confidence: Confidence
    rung: InterventionSpec
    disruption_cost: float
    unsupported: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict:
        return {
            "intervention": self.intervention.value,
            "confidence": self.confidence.value,
            "rung": self.rung.key,
            "dispatches": self.rung.dispatches,
            "returns_synthetic": self.rung.returns_synthetic,
            "disruption_cost": round(self.disruption_cost, 4),
            "unsupported": list(self.unsupported),
            "reason": self.reason,
        }


def choose_intervention(
    verdict: ProvenanceVerdict,
    policy: InterventionPolicy = DEFAULT_POLICY,
    ladder: InterventionLadder = BASE_INTERVENTIONS,
) -> InterventionDecision:
    """Map a `ProvenanceVerdict` → an `InterventionDecision`, confidence-gated. PURE + ADVISORY.

    Returns a recommendation; performs nothing. The mapping:
      * NONE  → `policy.on_none` (OBSERVE) — short-circuited BEFORE the floor, so a clean
                `believe=True` call is never floored up to a spurious WARN annotation.
      * HIGH  → `policy.on_high_confidence`, clamped into `[floor, ceiling]`.
      * LOW   → `policy.on_low_confidence`, clamped into `[floor, ceiling]`.

    The clamp (with the default `ceiling=BLOCK`) makes DEFER unreachable by default — the
    turn-spending rung is opt-in (a host raises the ceiling).

    FAIL-SAFE on a mismatched ladder. The policy was rank-validated at construction against
    `BASE_INTERVENTIONS`; here it is RE-validated against the `ladder` actually in hand
    (`policy.validate_against(ladder)`). A rank-reordered ladder that would let a LOW mint
    resolve HARDER than a HIGH one (the unguarded clamp-ladder ≠ validation-ladder hole the
    docs/144 adversarial review found) fails that check — and rather than raise from a pure
    advisory path (a hostile ladder could weaponize a raise into a DoS), we degrade to the
    ladder's own default rung (the least-disruptive-that-still-informs). So the refuse-LESS-
    only guarantee holds for the ladder in use, structurally, not merely for BASE.
    """
    conf = assess_confidence(verdict)
    # Re-validate the policy against the ACTUAL clamp-ladder (not just the BASE it was built
    # against). On a mismatch that would break refuse-LESS-only, fail safe to the default.
    try:
        policy.validate_against(ladder)
    except ValueError as e:
        spec = ladder.default()
        return InterventionDecision(
            intervention=Intervention(spec.token),
            confidence=conf,
            rung=spec,
            disruption_cost=ladder.disruption_cost(spec.token),
            unsupported=verdict.unsupported,
            reason=f"fail-safe: policy unsafe on this ladder ({e}) → ladder default {spec.key}",
        )
    if conf is Confidence.NONE:
        spec = ladder.get(policy.on_none)  # OBSERVE — NOT floored up to WARN
        reason = "no id/FK argument was minted — observe only"
    else:
        base = policy.on_high_confidence if conf is Confidence.HIGH else policy.on_low_confidence
        spec = ladder.clamp(base, floor=policy.floor, ceiling=policy.ceiling)
        reason = (
            f"{conf.value}-confidence mint on {len(verdict.unsupported)} arg(s) "
            f"({', '.join(verdict.unsupported)}) → {spec.key}"
        )
    if spec is None:  # pragma: no cover - policy validated against the ladder
        spec = ladder.default()
    return InterventionDecision(
        intervention=Intervention(spec.token),
        confidence=conf,
        rung=spec,
        disruption_cost=ladder.disruption_cost(spec.token),
        unsupported=verdict.unsupported,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# The synthetic corrective result — the BLOCK content builder (pure; #4a).
# ---------------------------------------------------------------------------
def synthetic_corrective_result(
    verdict: ProvenanceVerdict, tool_name: str, read_tool_hint: str = ""
) -> dict:
    """Build the synthetic tool-RESULT *content* a BLOCK returns in place of the real call.

    docs/143 §13.4 — the non-disruptive enforcement primitive. PURE (a `build_nudge_text`
    sibling): dict in, dict out, dispatches nothing. Shaped as a tool result the model reads
    (status / error / remediation), so on a BLOCK the agent gets a corrective OBSERVATION on
    the SAME turn — the docs/126 `dos apply` gate done right (prevent the bad effect while
    preserving the agent's flow). The kernel BUILDS this content; the CONSUMER substitutes
    it for the withheld mutation.

    SECURITY — the anti-laundering shape (docs/143 §5a, the highest-severity finding). The
    unresolved id VALUE is intentionally NOT echoed as a top-level corpus-bound field: the
    payload summarizes the unresolved arg by NAME + the unresolved COMPONENT TOKENS only,
    and the whole payload carries `dos_blocked: True`. The consumer EXCLUDES `dos_blocked`
    entries from the provenance corpus on the next call — otherwise a BLOCK'd id would
    re-enter the corpus and make `classify_arg`'s whole-value-direct-match return SUPPORTED
    on the re-mint, i.e. a BLOCK would *teach the detector to trust the very id it blocked*.
    """
    missing = [
        {"arg": a.arg_name, "unresolved_components": list(a.components_unmatched)}
        for a in verdict.args
        if a.stance is ProvenanceStance.UNSUPPORTED
    ]
    hint = f" (e.g. {read_tool_hint})" if read_tool_hint else ""
    return {
        "status": "blocked_unresolved_id",
        "error": (
            f"`{tool_name}` was NOT executed: it references id(s) that never appeared in "
            f"any prior tool result or the task — they look invented, not resolved."
        ),
        "unresolved": missing,
        "remediation": (
            f"Call a READ/QUERY tool{hint} to look up the correct id, then retry "
            f"`{tool_name}`. The mutation did not run; the database is unchanged."
        ),
        "dos_blocked": True,
    }


# ---------------------------------------------------------------------------
# The declarative on-ramp — read a ladder out of dos.toml (mirror reasons.*).
# ---------------------------------------------------------------------------
def specs_from_table(table: dict) -> list[InterventionSpec]:
    """Turn a parsed `[intervention]` TOML table into a list of `InterventionSpec`.

    `table` is `{token: {rank, summary?, actuation?, dispatches?, returns_synthetic?,
    see_also?}}` — the shape `tomllib.load(...)["intervention"]` yields. Pure (no I/O);
    raises `ValueError` (via `InterventionSpec.__post_init__`) on a bad rung, so a malformed
    declaration fails loudly at load.
    """
    specs: list[InterventionSpec] = []
    for token, body in (table or {}).items():
        if not isinstance(body, dict):
            raise ValueError(
                f"[intervention.{token}] must be a table, got {type(body).__name__}"
            )
        if "rank" not in body:
            raise ValueError(f"[intervention.{token}] is missing required `rank`")
        see = body.get("see_also") or ()
        if isinstance(see, str):
            see = (see,)
        specs.append(InterventionSpec(
            token=str(token),
            rank=int(body["rank"]),
            summary=str(body.get("summary", "")),
            actuation=str(body.get("actuation", "")),
            dispatches=bool(body.get("dispatches", True)),
            returns_synthetic=bool(body.get("returns_synthetic", False)),
            see_also=tuple(str(s) for s in see),
        ))
    return specs


def load_from_toml(
    path: "Path | str", *, base: InterventionLadder = BASE_INTERVENTIONS
) -> InterventionLadder:
    """Build an `InterventionLadder` from a `dos.toml`'s `[intervention]` table.

    Returns `base` unchanged when the file is absent, has no `[intervention]` table, or
    `tomllib` is unavailable — the declarative path is purely additive, so a missing/empty
    config degrades to the built-in ladder, never an error. A *present but malformed* table
    raises (`specs_from_table` / `InterventionSpec` / ladder validation). Reads with
    `utf-8-sig` to strip a PowerShell-written BOM (the `reasons.load_from_toml` fix).
    """
    p = Path(path)
    if not p.exists():
        return base
    # ONE shared, mtime-keyed parse (`_tomlcache`) - collapses the per-config-layer
    # re-read/re-parse storm on `dos.toml`. A malformed file still raises here
    # (uncached), so the caller's existing handling is unchanged; the missing-file
    # guard above is untouched. The utf-8-sig BOM strip lives inside the helper.
    from dos._tomlcache import read_toml_cached
    data = read_toml_cached(p)
    table = data.get("intervention")
    if not isinstance(table, dict) or not table:
        return base
    return base.extend(specs_from_table(table))
