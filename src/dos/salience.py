"""`salience` — the "is this true thing LIVE, or true-but-PARKED?" verdict (docs/391).

The missing dual of `retire` (docs/350). A fleet's verdicts, findings, claims, code
paths, and remembered lessons are not only TRUE-or-FALSE — they are also USEFUL-or-not,
and **those are orthogonal axes.** A thing can be *perfectly true and not currently
useful*: a real bug in a code path that is not on the default execution path; a correct
finding about a feature behind a disabled flag; a lesson that still holds but no longer
decides anything. The danger named by the operator (the docs/391 goal) is the **silent
loss** of such a thing — dropping it *as if it were false*, when it is merely *not in the
hotpath by default.* A dropped-because-not-useful truth costs nothing today and bites the
day the path goes live; and the drop leaves no record, so no one knows it happened.

This leaf converts that silent drop into a **recorded, recoverable park.** It does NOT
delete; it classifies a true thing as `LIVE` (in the default hotpath) or `PARKED` (out of
the hotpath, under a *typed* reason like `NOT_IN_HOTPATH`, retained and surfaced so it is
recoverable) or `INDETERMINATE` (cannot decide on the evidence — abstain → retain). The
load-bearing guarantee is the one the goal asks for:

    PARKED  ≠  dropped.   A parked thing is RETAINED and SURFACED, never silently lost.

Where it sits in the keep-only-what-a-witness-confirms family (the docs/350 table):

    effect_witness.witness_effect  -> did the effect HAPPEN?                     (runtime)
    improve.classify               -> may this loop KEEP this commit?            (loop iteration)
    retire.classify                -> does this item still EARN ITS PLACE?  DROP (library, measured)
    salience.classify              -> is this true thing LIVE or PARKED?   KEEP  (per item, mechanical)
                                      ^ THIS module — the "keep-but-park, never silently lose" dual

`retire` and `salience` are deliberate opposites on the same orthogonality. `retire`
*removes* a library item once the environment has MEASURED that it stopped contributing —
a recorded, evidence-gated DROP. `salience` *keeps* a true thing and moves it out of the
default hotpath under a typed reason — a recorded, recoverable PARK. Use `retire` when
enough trials prove a thing earns no place; use `salience` to make sure a true thing is
never lost just because it is not, today, on the hot path.

THE TWO RELATED ALTITUDES (do not conflate)
===========================================
  * `lifecycle` (docs/207) carries a plan-CLASS taxonomy that already includes a `PARK`
    class (ACTIVE / MAINTENANCE / PARK / TOMB / DRAFT), with transitions a JUDGE approves
    — a *semantic*, advisory, whole-plan move. `salience` is the *mechanical, per-item*
    floor underneath it: a deterministic W2 verdict on one finding/claim/path/lesson that
    can FEED a lifecycle PARK transition, but never needs a judge to fire. `PARKED` here
    is the same word for the same idea, one altitude down.
  * `retention` (docs/106) governs how much .dos scratch to KEEP on disk, with the floor
    "a misconfigured policy may keep too much, but must never cause a False-collect of
    state the kernel still needs." `salience` carries that exact asymmetry up to the
    item level: a False-PARK is tolerable (the thing is still there, surfaced,
    recoverable); a silent loss is not. So the fail-safe always points at RETAIN.

⚠ THE HONESTY BOUNDARY — read this before extending. This verdict judges **mechanical /
measured salience**, never **semantic importance.** `LIVE` means *"no park-reason fired"*
— exactly like `answer_shape`'s `ANSWER_SHAPED` means *"no disqualifier found,"* and
pointedly NOT *"this is important / worth your attention."* The question "is this finding
*actually worth acting on*?" is the Tier-3 gestalt the kernel ABSTAINS on (the docs/212
world-witness arc; the `answer_shape` honesty boundary one axis over): it has no
independent witness, so it belongs to a JUDGE (advisory, fail-to-abstain) or a HUMAN,
never to a deterministic oracle. On anything it cannot decide from the mechanical /
measured evidence, this verdict returns `INDETERMINATE` — the abstain floor, which means
RETAIN + surface — never a confident `LIVE` and, above all, never a silent drop.

So where on the witness ladder (docs/192)? `PARKED` is a **W2-presence-class** call on
ground the environment authored: a reachability bit, a default-on flag, a supersession
event, a MEASURED contribution count — the same altitude as `verify()`'s file-path rung
and `answer_shape`'s shape rung. It is *advisory* (PDP, not PEP): it REPORTS a park; the
consumer (an assembly policy, a picker, a reviewer) decides whether to route the hotpath
around it. It never executes the move and never deletes.

The reasons are **policy, not hardcode** (the `dos.reasons` closed-set-as-data
discipline, docs/HACKING.md): the kernel ships the cross-domain park classes as data and
lets a host declare its own via the evidence's `declared_reason`. The kernel carries the
*fold + the floor*; the host carries the *signals*.

⚓ Pure; the per-item facts (`SalienceEvidence`) and the declared rules (`SaliencePolicy`)
are handed in at the caller boundary. No I/O, no model call, no reachability computation
at import (the kernel cannot and must not compute reachability — that is a host's static
analysis; it hands the bit in). Returns a verdict; NEVER raises (a bad/absent input
degrades toward `LIVE`/`INDETERMINATE` — RETAIN — never toward a drop and never an
exception; the fail-safe direction is the dual of `run_judge`'s fail-to-abstain, pointed
at "do not lose anything").

Go-parity note (docs/385 §3/§8): like `answer_shape`, this is born in Python so it is
reachable through the Python CLI (`dos salience`) and `import dos` while those seams are
still Python (Tier 4 / TP5). It is pure `classify(evidence, policy)` with no I/O — i.e.
PORT-READY for the docs/124 differential gate — and a Go parity port is QUEUED under the
TP2 RE2-clean pure set (enum/bool/float only, no regex). Recording that obligation here
is what keeps this a queued port, not a Python-only regression against the mandate.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass


class Salience(str, enum.Enum):
    """The typed salience verdict (docs/391).

    `str`-valued so it round-trips a `--json` token / exit code with no lookup table (the
    `AnswerShape` / `Reconciliation` idiom). The load-bearing asymmetry mirrors
    `answer_shape`: only `PARKED` is a positive classification; `LIVE` is "no park-reason
    fired" (NOT a claim of importance); `INDETERMINATE` is the abstain floor — and here
    the floor, like every non-PARKED state, means RETAIN, never drop.
    """

    LIVE = "LIVE"                     # no park-reason fired — kept in the default hotpath (NOT "important")
    PARKED = "PARKED"                 # true-but-not-useful — out of the hotpath under a typed, RECOVERABLE reason
    INDETERMINATE = "INDETERMINATE"   # cannot decide on the evidence — abstain → RETAIN + surface (the floor)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value

    @property
    def is_live(self) -> bool:
        """True iff this thing belongs in the default hotpath (LIVE only)."""
        return self is Salience.LIVE

    @property
    def is_parked(self) -> bool:
        """True iff this thing was positively parked out of the hotpath (PARKED only)."""
        return self is Salience.PARKED

    @property
    def is_retained(self) -> bool:
        """True for EVERY state — the prevent-silent-loss invariant in one line.

        No salience verdict ever means "delete": `LIVE` keeps it in the hotpath,
        `PARKED` keeps it out-but-surfaced, `INDETERMINATE` keeps it pending a
        JUDGE/HUMAN. Deletion is a different, stronger verdict (`retire` / a lifecycle
        TOMB), gated on measured evidence — never a side effect of low salience.
        """
        return True


# The cross-domain park classes — the typed "why this true thing is out of the hotpath"
# reasons. Plain string constants (not a Python enum) so a host MAY declare its OWN reason
# via `SalienceEvidence.declared_reason` without editing the kernel (the `dos.reasons`
# closed-set-as-data discipline), and so each round-trips a `--json` token. The kernel
# SHIPS these defaults; a host adds reasons on top. Each is a MECHANICAL/MEASURED tell —
# never a semantic "this is unimportant" judgment (that is the JUDGE/HUMAN's, see boundary).
PARK_NOT_IN_HOTPATH = "NOT_IN_HOTPATH"      # not on the default execution path (default-off flag / not reached by default)
PARK_UNREACHABLE = "UNREACHABLE"            # no path reaches it at all — orphaned / dead code
PARK_SUPERSEDED = "SUPERSEDED"              # a later thing replaces it (the `apply_gate` supersede idiom, per item)
PARK_LOW_CONTRIBUTION = "LOW_CONTRIBUTION"  # MEASURED net utility below the host's floor — the `retire` bridge, but PARKED not DROPPED

#: The canonical park classes the kernel ships (a host's `declared_reason` extends this).
GENERIC_PARK_REASONS: tuple[str, ...] = (
    PARK_NOT_IN_HOTPATH,
    PARK_UNREACHABLE,
    PARK_SUPERSEDED,
    PARK_LOW_CONTRIBUTION,
)

# The RE-ENTRY affordance — the operator-facing "how to pull this back into the hotpath"
# line, per kernel park class. This is the load-bearing distinction from `retire`: `retire`
# proposes evicting an item from the ACTIVE library (its terminal disposition is archive,
# with no re-entry verdict); `salience` PARKS it IN PLACE with a defined, cheap path back.
# A parked thing that cannot be recovered is just a slow drop — so every PARKED verdict
# carries its reactivation line, exactly the `pickable.HoldReason.next_action` idiom (the
# typed-reason-plus-unblock-line pattern), NOT a heavier registry. A host-declared reason
# the kernel does not know falls to the generic line (forward-compatible, never KeyErrors).
_REACTIVATION: dict[str, str] = {
    PARK_NOT_IN_HOTPATH: (
        "re-activates when the path goes default-on; force it live now with "
        "`dos salience --label <id> --default-on`"
    ),
    PARK_UNREACHABLE: (
        "re-activates when a caller/entrypoint reaches it again — re-run the "
        "reachability witness; nothing is deleted while it waits"
    ),
    PARK_SUPERSEDED: (
        "re-activates if the superseding item is itself reverted/retired, or is found "
        "not to cover this one after all"
    ),
    PARK_LOW_CONTRIBUTION: (
        "re-activates on the next measured use that lifts contribution back above the "
        "floor — the park clears itself, no manual step"
    ),
}

_GENERIC_REACTIVATION = (
    "retained out of the hotpath under a host reason — re-activate per the host's policy "
    "for this reason class (it is surfaced, not dropped)"
)


def reactivation_for(reason_class: str) -> str:
    """The re-entry line for a park class — the kernel default, or the generic fallback.

    Total over every string (a host's own `declared_reason` resolves to the generic line),
    so a surfaced parked item ALWAYS carries a recovery affordance. Never raises.
    """
    return _REACTIVATION.get(reason_class, _GENERIC_REACTIVATION)


@dataclass(frozen=True)
class SalienceEvidence:
    """The env-authored facts about ONE item — none authored by the item being judged.

    Every field is a fact the ENVIRONMENT supplies (a static-analysis bit, a flag state,
    a supersession event, a measured count), echoing `retire`'s non-forgeable discipline:
    the item's own self-description is read for NOTHING here (it is not even a field). The
    kernel does not compute these — a host hands them in at the boundary.

    ``label`` identifies the item (a finding id, a symbol, a memory key); echoed back so a
    surfaced verdict / partition is self-joining.

    ``reachable`` — is the item reached from a default entrypoint? ``None`` = unknown (the
    host did not analyze it); only an explicit ``False`` can park it (and only when the
    policy arms that rung). ``default_on`` — is it on the DEFAULT path (not behind a
    disabled flag)? Same tri-state. The ``None``-is-unknown rule is what keeps the
    fail-safe pointed at RETAIN: absence of a signal never parks.

    ``superseded`` — a later grant / finding / version replaces this one (a definite
    event; ``False`` means "not superseded", the safe default).

    ``declared_reason`` — a host-declared park class (the open extension point over
    `GENERIC_PARK_REASONS`); non-empty ⇒ the host is asserting a typed park reason of its
    own. Read verbatim, parsed for nothing.

    ``contribution`` / ``trials`` — the MEASURED net utility of the item and how many
    measurements stand behind it, exactly as in `retire`. The measured park rung consults
    these only when armed AND ``trials >= min_trials`` — never park on thin evidence
    (`retire`'s witness-ceiling rule, docs/350 §3).
    """

    label: str = ""
    reachable: "bool | None" = None
    default_on: "bool | None" = None
    superseded: bool = False
    declared_reason: str = ""
    contribution: "float | None" = None
    trials: int = 0


@dataclass(frozen=True)
class SaliencePolicy:
    """The declared, swappable park rules — which rungs are armed, and the thresholds.

    Every rung defaults to a *safe* posture. The boolean rungs (`park_unreachable`,
    `park_default_off`, `park_superseded`, `park_declared`) are armed in the shipped
    `GENERIC_SALIENCE_POLICY` because their signals are unambiguous and the host only
    supplies them deliberately (an unknown signal is `None`/`False`, which never parks).
    The MEASURED rung is OFF by default — like `answer_shape`'s ceiling, the host declares
    its budget; the kernel never guesses one.

    ``park_unreachable`` — arm: ``reachable is False`` ⇒ PARK(`UNREACHABLE`).
    ``park_default_off`` — arm: ``default_on is False`` ⇒ PARK(`NOT_IN_HOTPATH`).
    ``park_superseded``  — arm: ``superseded`` ⇒ PARK(`SUPERSEDED`).
    ``park_declared``    — arm: a non-empty ``declared_reason`` ⇒ PARK(that reason).

    ``min_contribution`` / ``min_trials`` — the MEASURED rung (the `retire` bridge). It is
    armed only when ``min_trials > 0`` (so it is OFF by default); when armed, an item with
    ``trials >= min_trials`` AND ``contribution < min_contribution`` is PARK(`LOW_CONTRIBUTION`).
    Below ``min_trials`` measurements the rung ABSTAINS (the thin-evidence floor) — it
    never parks on too little data, so a sparsely-measured truth stays LIVE, not lost.
    """

    park_unreachable: bool = False
    park_default_off: bool = False
    park_superseded: bool = False
    park_declared: bool = False
    min_contribution: float = 0.0
    min_trials: int = 0


#: The generic, domain-free default. Arms the unambiguous mechanical rungs (the host only
#: ever feeds them deliberate signals; an unknown signal never parks) and leaves the
#: MEASURED rung OFF (the host declares its contribution budget — the kernel never guesses).
GENERIC_SALIENCE_POLICY = SaliencePolicy(
    park_unreachable=True,
    park_default_off=True,
    park_superseded=True,
    park_declared=True,
)


@dataclass(frozen=True)
class SalienceVerdict:
    """The single verdict `classify` returns, inputs echoed back for legibility.

    ``state`` is the typed `Salience`. ``reason_class`` is the typed park class that fired
    (one of `GENERIC_PARK_REASONS` or a host's `declared_reason`; ``""`` when not parked)
    — the machine-actionable "why", the dual of `answer_shape`'s ``matched``. ``reason``
    is the operator one-liner. ``reactivation`` is the "how to pull it back into the
    hotpath" line when PARKED (``""`` otherwise) — the re-entry affordance that makes
    "recoverable" concrete and is the load-bearing distinction from `retire`'s evict-to-
    archive. ``label`` echoes the item id (so a surfaced verdict / partition row self-joins).
    """

    state: Salience
    reason_class: str
    reason: str
    reactivation: str = ""
    label: str = ""

    @property
    def is_live(self) -> bool:
        return self.state.is_live

    @property
    def is_parked(self) -> bool:
        return self.state.is_parked

    @property
    def is_retained(self) -> bool:
        """Always True — no salience verdict ever means delete (the prevent-silent-loss invariant)."""
        return self.state.is_retained

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "reason_class": self.reason_class,
            "is_live": self.is_live,
            "is_parked": self.is_parked,
            "is_retained": self.is_retained,
            "reactivation": self.reactivation,
            "label": self.label,
            "reason": self.reason,
        }


def _has_signal(ev: SalienceEvidence) -> bool:
    """Did the host supply ANY usefulness evidence to judge on?

    Distinguishes "we looked and nothing parked it" (LIVE) from "we had nothing to go on"
    (INDETERMINATE → abstain → retain). A bare `superseded=False` is the safe default, not
    a signal; an explicit reachability/default bit, a declared reason, or a measured
    contribution IS a signal.
    """
    return (
        ev.reachable is not None
        or ev.default_on is not None
        or bool(ev.declared_reason)
        or ev.superseded
        or ev.contribution is not None
    )


def classify(
    evidence: "SalienceEvidence | None",
    *,
    policy: "SaliencePolicy | None" = GENERIC_SALIENCE_POLICY,
) -> SalienceVerdict:
    """Classify one item's salience: LIVE / PARKED / INDETERMINATE. PURE.

    ``evidence`` is the env-authored facts about the item, gathered at the boundary.
    ``policy`` is the declared park rules; the generic default if omitted, or ``None`` to
    force INDETERMINATE (no rules → cannot judge → abstain → retain).

    The decision order (first match wins; the more-deliberate / harder signals first):

      1. ``policy is None`` or ``evidence is None`` → INDETERMINATE (no rules / nothing to
         judge → abstain → RETAIN + surface — the floor).
      2. ``park_declared`` armed AND ``declared_reason`` set → PARKED(that reason). The
         host's own explicit, typed park reason — honored first.
      3. ``park_superseded`` armed AND ``superseded`` → PARKED(`SUPERSEDED`). A later
         thing replaces it — the hardest mechanical fact.
      4. ``park_unreachable`` armed AND ``reachable is False`` → PARKED(`UNREACHABLE`).
      5. ``park_default_off`` armed AND ``default_on is False`` → PARKED(`NOT_IN_HOTPATH`).
      6. MEASURED rung armed (``min_trials > 0``) AND ``contribution`` present AND
         ``trials >= min_trials`` AND ``contribution < min_contribution`` →
         PARKED(`LOW_CONTRIBUTION`). Below the trials floor it ABSTAINS (never park on
         thin evidence — `retire`'s witness ceiling).
      7. no park fired AND some usefulness evidence was present → LIVE (no park-reason —
         kept in the hotpath; NOT a claim of importance).
      8. otherwise → INDETERMINATE (no evidence at all → abstain → RETAIN + surface).

    Returns a `SalienceVerdict`; NEVER raises. Remember the boundary: a `PARKED` is a
    sound mechanical/measured parking (and is RECOVERABLE — the thing is retained and
    surfaced, never dropped); a `LIVE` is only "no park-reason fired"; and the semantic
    "is this actually worth acting on?" question is for a JUDGE/HUMAN (INDETERMINATE is
    where the mechanical evidence honestly cannot decide). No state ever means delete.
    """
    if policy is None or evidence is None:
        return SalienceVerdict(
            state=Salience.INDETERMINATE,
            reason_class="",
            reason=(
                "no policy / no evidence — cannot judge salience; abstain → RETAIN + "
                "surface (the semantic 'is it useful?' question goes to a JUDGE/HUMAN)"
            ),
            label=evidence.label if evidence is not None else "",
        )

    # 2. The host's own explicit, typed park reason — most deliberate, honored first.
    if policy.park_declared and evidence.declared_reason:
        return SalienceVerdict(
            state=Salience.PARKED,
            reason_class=evidence.declared_reason,
            reason=(
                f"host declared park reason {evidence.declared_reason!r} — out of the "
                f"hotpath, RETAINED + surfaced (PARKED ≠ dropped; recoverable)"
            ),
            reactivation=reactivation_for(evidence.declared_reason),
            label=evidence.label,
        )

    # 3. Superseded — a later thing replaces it (the hardest mechanical fact).
    if policy.park_superseded and evidence.superseded:
        return SalienceVerdict(
            state=Salience.PARKED,
            reason_class=PARK_SUPERSEDED,
            reason=(
                "a later thing supersedes this one — parked out of the hotpath, RETAINED "
                "+ surfaced (recoverable; not dropped — that is retire/TOMB's call)"
            ),
            reactivation=reactivation_for(PARK_SUPERSEDED),
            label=evidence.label,
        )

    # 4. Unreachable — no path reaches it (only an explicit False parks; None = unknown).
    if policy.park_unreachable and evidence.reachable is False:
        return SalienceVerdict(
            state=Salience.PARKED,
            reason_class=PARK_UNREACHABLE,
            reason=(
                "not reachable from any entrypoint — parked out of the hotpath, RETAINED "
                "+ surfaced (true-but-unreachable is recoverable, never silently lost)"
            ),
            reactivation=reactivation_for(PARK_UNREACHABLE),
            label=evidence.label,
        )

    # 5. Not in the default hotpath — on a non-default / disabled-by-default path.
    if policy.park_default_off and evidence.default_on is False:
        return SalienceVerdict(
            state=Salience.PARKED,
            reason_class=PARK_NOT_IN_HOTPATH,
            reason=(
                "not on the default execution path (off by default) — parked, RETAINED + "
                "surfaced; recoverable the moment the path goes live (the docs/391 case)"
            ),
            reactivation=reactivation_for(PARK_NOT_IN_HOTPATH),
            label=evidence.label,
        )

    # 6. The MEASURED rung — low contribution on enough trials. Off unless min_trials > 0;
    #    abstains below the trials floor (never park on thin evidence — retire's ceiling).
    if policy.min_trials > 0 and evidence.contribution is not None:
        if evidence.trials >= policy.min_trials and evidence.contribution < policy.min_contribution:
            return SalienceVerdict(
                state=Salience.PARKED,
                reason_class=PARK_LOW_CONTRIBUTION,
                reason=(
                    f"measured contribution {evidence.contribution:g} over "
                    f"{evidence.trials} trials is below the floor "
                    f"{policy.min_contribution:g} — parked, RETAINED + surfaced "
                    f"(retire would propose EVICT-to-archive; salience PARKS in place — recoverable)"
                ),
                reactivation=reactivation_for(PARK_LOW_CONTRIBUTION),
                label=evidence.label,
            )

    # 7/8. No park fired: LIVE if we had evidence to look at, else abstain (retain).
    if _has_signal(evidence):
        return SalienceVerdict(
            state=Salience.LIVE,
            reason_class="",
            reason=(
                "no park-reason fired — kept in the default hotpath (NOT a claim of "
                "importance; that is a JUDGE/HUMAN question)"
            ),
            label=evidence.label,
        )
    return SalienceVerdict(
        state=Salience.INDETERMINATE,
        reason_class="",
        reason=(
            "no usefulness evidence supplied — cannot judge salience; abstain → RETAIN + "
            "surface (never dropped on absence of evidence)"
        ),
        label=evidence.label,
    )


@dataclass(frozen=True)
class SaliencePartition:
    """The result of `partition` — three buckets, with the no-loss invariant in the type.

    ``live`` / ``parked`` / ``indeterminate`` are tuples of `SalienceVerdict` (each
    carries its ``label`` so a caller rejoins to the original item). EVERY input lands in
    EXACTLY ONE bucket and NONE is dropped — that is the prevent-silent-loss contract,
    enforced by `partition` and checkable as ``len(live)+len(parked)+len(indeterminate)``
    == the input count. A caller routes the hotpath to ``live``, SURFACES ``parked`` (with
    each row's ``reason_class``) for recovery, and escalates ``indeterminate`` to a
    JUDGE/HUMAN.
    """

    live: tuple[SalienceVerdict, ...]
    parked: tuple[SalienceVerdict, ...]
    indeterminate: tuple[SalienceVerdict, ...]

    @property
    def total(self) -> int:
        """The input count — by the no-loss invariant, the sum of the three buckets."""
        return len(self.live) + len(self.parked) + len(self.indeterminate)

    def to_dict(self) -> dict:
        return {
            "live": [v.to_dict() for v in self.live],
            "parked": [v.to_dict() for v in self.parked],
            "indeterminate": [v.to_dict() for v in self.indeterminate],
            "total": self.total,
        }


def partition(
    items: Iterable[SalienceEvidence],
    *,
    policy: "SaliencePolicy | None" = GENERIC_SALIENCE_POLICY,
) -> SaliencePartition:
    """Classify many items into live / parked / indeterminate — NOTHING dropped. PURE.

    The prevent-silent-loss fold: every item in ``items`` is `classify`-d and routed into
    exactly one bucket; the parked items are RETURNED (surfaced), never filtered away. The
    caller is what decides the hotpath — but it can no longer *silently* lose a true thing,
    because a parked truth is sitting in `parked` with its typed `reason_class`.

    Returns a `SaliencePartition`; NEVER raises.
    """
    live: list[SalienceVerdict] = []
    parked: list[SalienceVerdict] = []
    indeterminate: list[SalienceVerdict] = []
    for ev in items:
        v = classify(ev, policy=policy)
        if v.state is Salience.LIVE:
            live.append(v)
        elif v.state is Salience.PARKED:
            parked.append(v)
        else:
            indeterminate.append(v)
    return SaliencePartition(
        live=tuple(live),
        parked=tuple(parked),
        indeterminate=tuple(indeterminate),
    )
