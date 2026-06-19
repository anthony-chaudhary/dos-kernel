"""Shared/exclusive lock modes — the sound way to recover read-concurrency.

Why this exists (the deterministic answer to the ⅓-ratio hazard)
================================================================

`docs/114` §A1 found the `lane_overlap.OVERLAP_RATIO_MAX = 1/3` soft-overlap rule
**unsound**: lane conflict is treated as a *measure* ("how much of the requested
tree shares prefixes"), but fifty years of concurrency control (Gray, Lorie,
Putzolu, Traiger 1975/76, *Granularity of Locks*) make lock-compatibility a
**boolean predicate on the conflict** — two writers may share a contended region
only under operation *commutativity* (O'Neil 1986, escrow), which arbitrary file
overwrites lack. So any ratio > 0 admits genuine write–write conflicts on the
shared remainder — a silent lost-update `verify()` cannot catch.

§A1's *A-note* identified the actually-missing primitive, and §F deferred its
sound half here:

  > The prefix-collision test (`_tree.prefixes_collide`) is **sound** as a
  > conservative predicate-intersection check. What is genuinely missing is **lock
  > MODES**: DOS has exactly one (taken / not-taken ≈ always-exclusive). So two
  > read-only agents on `docs/**` conflict needlessly. A shared mode gives back
  > read concurrency *soundly* — which is the concurrency the ⅓ hack reaches for
  > *unsoundly*. **The absence of S-mode is what makes the ⅓ hack tempting.**

This module is that S-mode, as a **deterministic, pure, testable primitive** — not
prose in a plan. It is the classic two-mode lock-compatibility matrix combined with
the kernel's existing sound region-intersection predicate. No ratio, no model, no
I/O: `region_conflict` is a total boolean function of (tree, mode) × (tree, mode),
so it is replay-tested in isolation exactly like `lane_overlap.overlap_verdict`.

  >>> from dos.lock_modes import LockMode, region_conflict
  >>> # two readers on the same region — COMPATIBLE, no conflict:
  >>> region_conflict(["docs/**"], LockMode.SHARED, ["docs/**"], LockMode.SHARED)
  False
  >>> # a writer vs a reader on the same region — INCOMPATIBLE:
  >>> region_conflict(["docs/**"], LockMode.EXCLUSIVE, ["docs/**"], LockMode.SHARED)
  True
  >>> # two writers on DISJOINT regions — no intersection, so no conflict:
  >>> region_conflict(["src/a/**"], LockMode.EXCLUSIVE, ["src/b/**"], LockMode.EXCLUSIVE)
  False
  >>> # two writers sharing ANY prefix — conflict at *any* overlap (no ratio):
  >>> region_conflict(["src/api/x.py"], LockMode.EXCLUSIVE, ["src/api/**"], LockMode.EXCLUSIVE)
  True

How it relates to the ⅓ rule
============================

`region_conflict(..., EXCLUSIVE, ..., EXCLUSIVE)` is precisely the **sound
`ratio_max = 0` predicate**: two exclusive lanes conflict iff their regions
intersect *at all* (any shared prefix), with no fractional tolerance. So routing
write↔write through this module is the deterministic floor §A1 asked for; the
NEW capability it adds on top is that S/S no longer conflicts, recovering the
read-concurrency the ⅓ hack only reached for unsoundly. It is strictly a
**refine-and-tighten** of the existing predicate, never a loosening of the
write↔write case (which stays at zero-tolerance intersection).

Layering: pure stdlib + the `_tree` leaf it intersects with. A kernel leaf beside
`lane_overlap` / `overlap_policy`. No host names, no I/O. The arbiter/apply-gate
that *consumes* a per-lane mode (the PEP, `docs/119`) lives above this; this module
only decides the compatibility, the same way `overlap_verdict` decides overlap and
the caller acts on it.
"""
from __future__ import annotations

from enum import Enum

from dos._tree import prefixes_collide as _prefixes_collide
from dos.named_lock import normalize_entry as _norm_tree_prefix


class LockMode(str, Enum):
    """The two lock modes a lane may hold over its region.

    ``str``-valued so a mode round-trips through JSON / a WAL record / a
    ``dos.toml`` field as its lowercase name without a custom codec — the same
    convention every other kernel enum uses (``Verdict``, ``LivenessVerdict``).

    * ``SHARED`` — a *read* lock: the lane reads the region but does not write it
      (an audit, a `verify` fan-out, a render, a read-only analysis). Multiple
      SHARED holders over the same region are mutually compatible.
    * ``EXCLUSIVE`` — a *write* lock: the lane may mutate any path in the region.
      Incompatible with every other holder (SHARED or EXCLUSIVE) over an
      intersecting region. This is DOS's historical *only* mode — a lane with no
      declared mode is EXCLUSIVE, so existing behavior is unchanged by default
      (see ``DEFAULT_MODE``).
    """
    SHARED    = "shared"
    EXCLUSIVE = "exclusive"


#: The mode a lane holds when it declares none. EXCLUSIVE — the conservative
#: default that reproduces DOS's pre-S-mode behavior byte-for-byte (every lane
#: was effectively a write lock). Opting INTO ``SHARED`` is the only way to widen
#: concurrency, and it is the caller's explicit, auditable choice — never inferred.
DEFAULT_MODE: LockMode = LockMode.EXCLUSIVE


#: The lock-compatibility relation (Gray et al. 1975). The whole soundness of this
#: module is in this one table, so it is written as explicit data, not derived:
#: only SHARED↔SHARED is compatible; anything involving an EXCLUSIVE conflicts.
#: Symmetric by construction (every unordered pair is listed once each way), which
#: is the property the ⅓ ratio rule provably *lacked* (the 2026-06-01 TM↔tailor
#: asymmetric wedge — `docs/114` §A1).
_MODES_COMPATIBLE: dict[tuple[LockMode, LockMode], bool] = {
    (LockMode.SHARED,    LockMode.SHARED):    True,
    (LockMode.SHARED,    LockMode.EXCLUSIVE): False,
    (LockMode.EXCLUSIVE, LockMode.SHARED):    False,
    (LockMode.EXCLUSIVE, LockMode.EXCLUSIVE): False,
}


def modes_compatible(a: LockMode, b: LockMode) -> bool:
    """True iff two lock modes may be held over an INTERSECTING region at once.

    Pure lookup into the Gray-1975 compatibility matrix — the boolean
    lock-compat relation, total over the two-mode lattice and symmetric. This is
    *only* the mode half of the decision; whether the two regions intersect is
    `_tree.prefixes_collide`, combined in `region_conflict`.
    """
    return _MODES_COMPATIBLE[(a, b)]


def _trees_intersect(req_tree: list[str], lease_tree: list[str]) -> bool:
    """True iff any normalized prefix of one tree collides with one of the other.

    The sound, zero-tolerance region-intersection test — `_tree.prefixes_collide`
    (one prefix is a prefix of the other) applied pairwise. This is the
    ``ratio_max = 0`` predicate: ANY shared prefix is an intersection, with no
    fractional dilution. Literally-blank entries (falsy before normalization)
    carry no path and are dropped; a leading-glob entry (``**/*`` → the universal
    empty prefix) is KEPT and collides with everything, exactly as
    `lane_overlap._shared_count` and `_tree.lane_trees_disjoint` treat it.
    """
    if not req_tree or not lease_tree:
        # Unknown blast radius is the CALLER's asymmetry to enforce (cf.
        # `_tree.lane_trees_disjoint`, `DisjointnessPredicate`); an empty tree is
        # not "no region," so this low-level helper reports "no provable
        # intersection" (False) and lets the caller apply the empty-tree refuse.
        return False
    req_prefixes = [_norm_tree_prefix(p) for p in req_tree if p]
    lease_prefixes = [_norm_tree_prefix(p) for p in lease_tree if p]
    if not req_prefixes or not lease_prefixes:
        return False
    for nr in req_prefixes:
        for nl in lease_prefixes:
            if _prefixes_collide(nr, nl):
                return True
    return False


def region_conflict(
    requested_tree: list[str],
    requested_mode: LockMode,
    lease_tree: list[str],
    lease_mode: LockMode,
) -> bool:
    """True iff a lane may NOT run alongside a live lease, under lock modes.

    The sound floor §A1 asked for, as one deterministic function:

        conflict  ⟺  regions intersect  AND  modes are incompatible

    * Disjoint regions never conflict (whatever the modes) — they cannot touch the
      same file. This is `_trees_intersect`, the zero-tolerance (no-ratio)
      intersection predicate.
    * Intersecting regions conflict **iff** the modes are incompatible
      (`modes_compatible`): two SHARED (read) holders coexist; anything with an
      EXCLUSIVE (write) holder conflicts.

    The write↔write case (``EXCLUSIVE`` vs ``EXCLUSIVE``) reduces to *intersect at
    all* — the sound ``ratio_max = 0`` predicate, with none of the ⅓ rule's
    fractional admit-window. The only concurrency this adds over zero-tolerance
    exclusive-locking is the *sound* one: SHARED↔SHARED over a shared region. So
    `region_conflict` can only ever refuse-MORE than the ⅓ rule on writes, and
    admit-more only on provably-safe read/read — never a write–write collision.

    Empty-tree handling: `_trees_intersect` returns False on an empty tree (no
    provable intersection), so this returns False (no conflict) — the caller MUST
    apply the unknown-blast-radius refuse upstream (as `DisjointnessPredicate`
    already does), exactly as it must for `lane_overlap.overlap_verdict`. This
    function decides the *known-vs-known under modes* case only.
    """
    if not _trees_intersect(list(requested_tree), list(lease_tree)):
        return False
    return not modes_compatible(requested_mode, lease_mode)
