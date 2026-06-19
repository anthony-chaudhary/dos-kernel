"""The overlap-policy seam — Axis 7 of hackability: a pluggable disjointness scorer.

Why this exists
===============

The arbiter's single most load-bearing verdict is *may these two known trees run
concurrently?* — the thing that stops two agents writing the same region. Until
this seam, that verdict was a **hardcoded `1/3` prefix-ratio** (`lane_overlap.
overlap_verdict`) buried inside `admission.DisjointnessPredicate`. Every other
hackability axis (reasons, renderers, predicates, judges, …) is *open* — bring
your own implementation via data / an entry-point / a driver, never fork the
package — but the disjointness *scorer* was sealed. `docs/90 §1`/`§2` named this
exact scalar a deliberate research stand-in and specified the answer-shape; this
module is that answer (the full argument is `docs/113`).

The unit a policy rules on is two **known** trees (the unknown-blast-radius
empty-tree asymmetry stays in `DisjointnessPredicate` — it is a soundness
invariant, not a *scoring* choice, so a policy never sees it). A policy returns
the existing typed `lane_overlap.OverlapDecision`, so it is a true drop-in at the
one seam every collision check already routes through.

The soundness floor — structural, not trusted
==============================================

This is the security-load-bearing core. A policy returns a verdict that *includes
admit*, so — unlike an `AdmissionPredicate`, which can only refuse — the type
alone no longer guarantees the safe direction. The guarantee is restored
**structurally** by a deterministic floor:

  > A resolved `OverlapPolicy` may turn an ADMIT into a REFUSE. It may never turn
  > a REFUSE into an ADMIT relative to the unforgeable prefix floor.

`admissible_under_floor` computes the deterministic prefix-disjointness floor
(`PrefixOverlapPolicy` — pure path algebra, no provider, no I/O) AND the resolved
policy's verdict, and admits only when **both** admit:

    admit  ⟺  floor.admissible  AND  policy.admissible

So a policy that admits a pair the prefix floor refuses is **structurally
unable** to produce an admit (the floor is ANDed in, and the floor is not the
plugin's to compute). The worst a buggy/hostile policy can do is *refuse* pairs
the floor would admit — a visible, safe-direction loss of concurrency, never a
collision. A policy that raises / returns the wrong type degrades to the floor
verdict alone (fail-closed toward the prefix rule — i.e. to *today's* behavior).

This is the admission analogue of the judge seam's fail-to-ABSTAIN and the
predicate seam's conjunctive-only, and the `docs/76` design law applied to
admission: a researcher changes *what counts as overlap* (the signal), never
*which way the verdict fails* (the adjudication).

Purity & layering
=================

Pure stdlib + the kernel leaves it delegates to (`lane_overlap`) — a Protocol,
a built-in prefix policy, a resolver, and the floor-AND helper. No host names, no
I/O inside a verdict. So it sits in the kernel layer beside `admission` (which
likewise holds a pure protocol + resolver while real *implementations* live
outside). A policy MAY do I/O *inside* `overlaps` (call a model, read an import
graph) iff it lives in a driver — the JUDGE-rung allowance — but the kernel's own
`PrefixOverlapPolicy` does not. Entry-point discovery (the one bit of I/O) happens
at the call boundary in `active_overlap_policy`, exactly as `active_judges` /
`active_predicates` / renderer discovery do.
"""

from __future__ import annotations

import sys
from typing import Protocol, runtime_checkable

from dos.lane_overlap import OVERLAP_RATIO_MAX, OverlapDecision, Verdict, overlap_verdict


@runtime_checkable
class OverlapPolicy(Protocol):
    """The contract a researcher implements to swap the disjointness *scorer*.

    ``name`` is the token `dos overlap-eval --policy <name>` selects and
    `dos doctor` lists. ``overlaps`` is handed two **known** (non-empty) trees +
    the active ``config`` (read-only — it reads policy, e.g. a declared
    ``ratio_max``, but the type gives it nothing to mutate) and returns an
    `OverlapDecision` (the existing `lane_overlap` type).

    A policy MAY do I/O *inside* ``overlaps`` (call a model, shell out, read an
    import graph) — unlike a predicate or a renderer, which are pure — IFF it
    lives in a driver, the same reason a ruling judge does. The discipline that
    keeps it honest is NOT purity; it is the deterministic floor
    (`admissible_under_floor`): whatever a policy returns, admission is the AND of
    its admit and the kernel's own unforgeable prefix admit, so a policy is
    structurally unable to admit a pair the prefix floor refuses.
    """

    name: str

    def overlaps(
        self, requested_tree: list[str], lease_tree: list[str], config: object,
    ) -> OverlapDecision:
        ...


class PrefixOverlapPolicy:
    """The built-in, always-available policy: today's `1/3` prefix-ratio scorer.

    A verbatim wrap of `lane_overlap.overlap_verdict`, threading the workspace's
    declared ``ratio_max`` (``config.overlap_ratio_max`` — `dos.toml [overlap]`,
    defaulting to ⅓). Workspaces that explicitly name ``policy = "prefix"`` or
    set a bare ``ratio_max`` still get THIS policy, preserving the legacy
    soft-overlap scorer for callers that deliberately opt into it.

    It is also the **deterministic prefix floor** every other policy is ANDed
    against (`admissible_under_floor`): it is pure path algebra, forgery-proof,
    and the unshadowable lower bound a plugin can never displace (`resolve_overlap_
    policy` resolves built-ins first) — the overlap analogue of the unshadowable
    `text` renderer / `abstain` judge. The floor it computes uses the kernel's
    own ⅓ default (NOT the workspace's possibly-looser declared ratio): the floor
    is the conservative bound, so loosening `ratio_max` in data can only widen the
    *policy's* admit set, never the floor it is checked against. See
    `admissible_under_floor`.
    """

    name = "prefix"

    def overlaps(
        self, requested_tree: list[str], lease_tree: list[str], config: object,
    ) -> OverlapDecision:
        ratio_max = _ratio_max_from_config(config)
        return overlap_verdict(requested_tree, lease_tree, ratio_max=ratio_max)


class LockModeOverlapPolicy:
    """The built-in sound default: prefix intersection plus S/X lock modes.

    This policy exists so the active overlap policy can be reported and resolved by
    name, but the live arbiter's `DisjointnessPredicate` calls
    `lock_mode_decision` directly so it can pass the request and live-lease modes.
    A bare `overlaps()` call has no mode operands, so it uses the conservative
    historical default: EXCLUSIVE on both sides.
    """

    name = "lock_modes"

    def overlaps(
        self, requested_tree: list[str], lease_tree: list[str], config: object,
    ) -> OverlapDecision:
        from dos.lock_modes import DEFAULT_MODE
        return lock_mode_decision(
            requested_tree,
            lease_tree,
            requested_mode=DEFAULT_MODE,
            lease_mode=DEFAULT_MODE,
        )


def _coerce_lock_mode(raw: object):
    """Return a `LockMode`, defaulting invalid/missing values to EXCLUSIVE.

    Invalid data must fail toward less concurrency, not more. That means a typo in
    a WAL row or caller argument becomes the exclusive default instead of being
    interpreted as shared.
    """
    from dos.lock_modes import DEFAULT_MODE, LockMode

    if isinstance(raw, LockMode):
        return raw
    if raw is None or raw == "":
        return DEFAULT_MODE
    try:
        return LockMode(str(raw).strip().lower())
    except ValueError:
        return DEFAULT_MODE


def lock_mode_decision(
    requested_tree: list[str],
    lease_tree: list[str],
    *,
    requested_mode: object,
    lease_mode: object,
) -> OverlapDecision:
    """Known-tree admission under the live S/X lock-mode path.

    Two known trees may run together iff their regions are disjoint, or their
    regions intersect but both holders are SHARED. Any intersecting pair involving
    EXCLUSIVE refuses at zero tolerance; no ratio can dilute the write surface.
    """
    from dos.lock_modes import LockMode, region_conflict

    req_mode = _coerce_lock_mode(requested_mode)
    held_mode = _coerce_lock_mode(lease_mode)
    requested = len(requested_tree)
    if not region_conflict(
        requested_tree, LockMode.EXCLUSIVE,
        lease_tree, LockMode.EXCLUSIVE,
    ):
        return OverlapDecision(
            Verdict.ADMIT_DISJOINT,
            0,
            requested,
            "no shared prefixes — fully disjoint",
        )
    if not region_conflict(requested_tree, req_mode, lease_tree, held_mode):
        return OverlapDecision(
            Verdict.ADMIT_SHARED,
            1,
            requested,
            (f"shared lock-compatible region — {req_mode.value}/"
             f"{held_mode.value} modes may coexist"),
        )
    if req_mode is LockMode.SHARED or held_mode is LockMode.SHARED:
        mode_note = f"{req_mode.value}/{held_mode.value}"
    else:
        mode_note = "exclusive/exclusive"
    return OverlapDecision(
        Verdict.REFUSE_LOCK_CONFLICT,
        1,
        requested,
        (f"lock-mode conflict: intersecting region with {mode_note} modes "
         "requires zero-tolerance refusal"),
    )


def _ratio_max_from_config(config: object) -> float:
    """The soft-overlap tolerance to use, read off the config (data seam).

    Reads ``config.overlap_ratio_max`` when present and a sane float in (0, 1];
    otherwise the kernel default ⅓. Defensive on purpose — a hand-built test
    config, a `None` config, or a malformed value all fall back to the default
    rather than crashing the admission hot path (the warn-and-fall-back posture
    every config axis takes). A non-positive or >1 value is ignored (a ratio
    outside (0, 1] is meaningless for a shared/requested fraction)."""
    raw = getattr(config, "overlap_ratio_max", None)
    if raw is None:
        return OVERLAP_RATIO_MAX
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return OVERLAP_RATIO_MAX
    if not (0.0 < val <= 1.0):
        return OVERLAP_RATIO_MAX
    return val


# The unforgeable floor instance — pure, stateless, reused.
_FLOOR = PrefixOverlapPolicy()


def floor_decision(requested_tree: list[str], lease_tree: list[str]) -> OverlapDecision:
    """The deterministic prefix-disjointness floor verdict for two known trees.

    Computed with the **kernel default** ⅓ tolerance, NOT a workspace-declared
    one — the floor is the conservative bound a policy is checked against, so it
    must not itself be loosened by `dos.toml [overlap]`.

    The load-bearing consequence — net admission is ``policy ∧ floor``, so with the
    floor fixed at ⅓:

      * **Tightening** ``[overlap] ratio_max`` below ⅓ *works*: the policy is the
        stricter voice, the floor never interferes, net admit = the tighter ratio.
      * **Loosening** ``[overlap] ratio_max`` above ⅓ is *capped at ⅓*: the policy
        would admit up to the looser ratio, but the floor re-refuses anything past
        ⅓, so net admit stays ⅓. This is DELIBERATE — ⅓ is a fixed SAFETY CEILING
        an operator cannot raise with a config line (loosening is the dangerous
        direction for false-admits, `docs/90 §2`). To genuinely admit a pair the
        prefix rule refuses you need a *stricter sound floor* (the glob-intersection
        floor, `docs/113 §3.1`), not a looser scalar — soundness is not a knob.

    So the data knob tunes admission *downward* freely and is bounded *upward* by
    the kernel floor — the same asymmetry the rest of the seam has (a swappable
    scorer can only refuse-more)."""
    return overlap_verdict(requested_tree, lease_tree, ratio_max=OVERLAP_RATIO_MAX)


def admissible_under_floor(
    policy: OverlapPolicy,
    requested_tree: list[str],
    lease_tree: list[str],
    config: object,
) -> OverlapDecision:
    """Run ``policy`` for two known trees, AND-ed under the deterministic floor.

    The structural soundness guarantee in one function. Returns an
    `OverlapDecision` whose ``admissible`` is::

        floor.admissible  AND  policy.admissible

    so a policy can only ever move the verdict toward REFUSE relative to the
    prefix floor:

      * floor REFUSE → the floor verdict is returned regardless of the policy
        (the dangerous cell — admitting a prefix-colliding pair — is unreachable;
        the policy is not even able to express an admit that survives the AND).
      * floor ADMIT, policy ADMIT → the policy verdict (it may carry a richer
        reason; both agree it is safe).
      * floor ADMIT, policy REFUSE → the policy's REFUSE (a stricter scorer caught
        an overlap the prefix rule missed — the safe, more-refusing direction).
      * policy raises / returns a non-`OverlapDecision` → the floor verdict alone
        (fail-closed toward the prefix rule — today's behavior, never looser).

    This is the ONE place `DisjointnessPredicate` consults a policy; it is what
    makes "a swappable scorer can never admit a collision" a property of the
    *shape of the computation*, not a property of the plugin behaving."""
    floor = floor_decision(requested_tree, lease_tree)
    # Floor refuses → no policy can admit. Return the floor verdict verbatim so the
    # operator sees the unforgeable reason (and a hostile policy cannot even dilute
    # the *message*, let alone the verdict).
    if not floor.admissible:
        return floor
    # Floor admits → consult the policy, fail-closed to the floor on any misbehavior.
    name = getattr(policy, "name", type(policy).__name__)
    try:
        decision = policy.overlaps(list(requested_tree), list(lease_tree), config)
    except Exception as e:  # fail-closed: a policy that raises falls back to the floor
        return OverlapDecision(
            floor.verdict, floor.shared, floor.requested,
            (f"overlap policy {name!r} raised ({e!r}) — using the deterministic "
             f"prefix floor verdict ({floor.reason})."),
        )
    if not isinstance(decision, OverlapDecision):
        # A policy that does not return our type cannot be trusted to admit; we
        # never read a foreign object's `.admissible`, so no admit leaks through a
        # wrong return type. Fall back to the floor.
        return OverlapDecision(
            floor.verdict, floor.shared, floor.requested,
            (f"overlap policy {name!r} returned a {type(decision).__name__}, not "
             f"an OverlapDecision — using the deterministic prefix floor verdict "
             f"({floor.reason})."),
        )
    if decision.admissible:
        # Both floor and policy admit. Return the policy's (possibly richer) verdict.
        return decision
    # Floor admits but policy refuses — the stricter scorer wins (refuse-more is
    # the safe direction). Surface the policy's reason.
    return decision


# ---------------------------------------------------------------------------
# Resolution — built-in first, then the `dos.overlap_policies` entry-point group.
# ---------------------------------------------------------------------------

# The entry-point group a workspace/researcher registers an overlap policy under.
OVERLAP_POLICY_ENTRY_POINT_GROUP = "dos.overlap_policies"

# The built-in policies, resolvable by name and UNSHADOWABLE by a plugin (a plugin
# registering `prefix` cannot displace this one — built-ins resolve first). Only
# the deterministic prefix scorer ships in the kernel; a model-backed or
# import-graph policy lives in a driver/plugin.
_BUILT_IN_POLICIES: dict[str, type] = {
    PrefixOverlapPolicy.name: PrefixOverlapPolicy,
    LockModeOverlapPolicy.name: LockModeOverlapPolicy,
}


def _discover_entry_point_policies(*, _stderr=None) -> list[tuple[str, OverlapPolicy]]:
    """Find overlap policies registered under the `dos.overlap_policies` group.

    A policy plugin registers ``name = "pkg.module:PolicyClass"`` in its
    ``[project.entry-points."dos.overlap_policies"]``. We load each, instantiate
    it if it is a class, and return ``(entry_point_name, policy)`` pairs sorted by
    name (stable, so `dos doctor` order is deterministic). A plugin that fails to
    load is skipped with a one-line stderr note rather than crashing arbitration —
    the same posture `_discover_entry_point_judges` / predicate discovery take."""
    stderr = _stderr if _stderr is not None else sys.stderr
    out: list[tuple[str, OverlapPolicy]] = []
    try:
        from importlib.metadata import entry_points
    except Exception:  # pragma: no cover - importlib.metadata always present py3.11+
        return out
    try:
        eps = entry_points(group=OVERLAP_POLICY_ENTRY_POINT_GROUP)
    except TypeError:  # pragma: no cover - py<3.10 selectable-API fallback
        eps = entry_points().get(OVERLAP_POLICY_ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - defensive: never let discovery crash a call
        return out
    for ep in sorted(eps, key=lambda e: e.name):
        try:
            obj = ep.load()
            policy = obj() if isinstance(obj, type) else obj
        except Exception as e:  # pragma: no cover - depends on third-party plugin
            print(
                f"warning: overlap policy plugin {ep.name!r} failed to load ({e}); "
                f"skipping",
                file=stderr,
            )
            continue
        out.append((ep.name, policy))
    return out


def resolve_overlap_policy(name: str, *, _stderr=None) -> OverlapPolicy:
    """Resolve an overlap policy by name: built-ins first, then plugins.

    Built-ins (`prefix`) resolve FIRST and cannot be shadowed by a plugin of the
    same name — the trusted-floor guarantee, identical to `resolve_judge` /
    `resolve_renderer`. An unknown name fails LOUD with the known list (it never
    silently degrades to `prefix`, which would hide a typo'd `--policy`): the
    caller asked for a specific scorer and getting a different one silently is
    exactly the unannounced substitution the kernel refuses."""
    if name in _BUILT_IN_POLICIES:
        return _BUILT_IN_POLICIES[name]()
    discovered = dict(_discover_entry_point_policies(_stderr=_stderr))
    if name in discovered:
        return discovered[name]
    known = sorted(set(_BUILT_IN_POLICIES) | set(discovered))
    raise ValueError(
        f"unknown overlap policy {name!r}; known: {', '.join(known)}"
    )


def active_overlap_policy(*, config: object = None, _stderr=None) -> OverlapPolicy:
    """The overlap policy a CALLER threads into the disjointness check.

    Resolution: a workspace may name its policy in ``config.overlap_policy_name``
    (the `dos.toml [overlap] policy` data field); absent that, the built-in
    `prefix` floor scorer. Does ENTRY-POINT DISCOVERY (I/O) when a non-`prefix`
    name is configured, so it is a CALL-BOUNDARY helper (the CLI's `cmd_arbitrate`,
    `dos doctor`), never called inside the pure `arbitrate`. The pure default —
    no config, or `prefix` — returns the built-in with no discovery, so the hot
    path stays I/O-free, exactly as `built_in_predicates` does.

    Like the predicate seam, the kernel's pure `arbitrate` never calls this; the
    boundary resolves the policy and passes it in. The default is `lock_modes`;
    `prefix` is the legacy ratio scorer kept for explicit compatibility."""
    name = getattr(config, "overlap_policy_name", None) if config is not None else None
    if not name:
        return LockModeOverlapPolicy()
    if name == PrefixOverlapPolicy.name:
        return PrefixOverlapPolicy()
    if name == LockModeOverlapPolicy.name:
        return LockModeOverlapPolicy()
    return resolve_overlap_policy(str(name), _stderr=_stderr)


def active_overlap_policy_names(*, _stderr=None) -> list[str]:
    """The names of every resolvable overlap policy (built-in + discovered) — what
    `dos doctor` lists so an operator can see which scorers the arbiter could use
    (the overlap analogue of "see the active predicates / judges")."""
    built = list(_BUILT_IN_POLICIES)
    discovered = [n for n, _p in _discover_entry_point_policies(_stderr=_stderr)]
    return built + discovered
