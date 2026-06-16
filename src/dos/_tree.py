"""Pure file-tree prefix algebra — the shared normalization the lane arbiter
and the overlap policy both stand on.

Lifted byte-for-byte (logic-identical) from the origin repo's
`scripts/next_up_render.py` (`_norm_tree_prefix`, `lane_trees_disjoint`). Those
two functions are the *only* part of the 3,326-line `next_up_render` the arbiter
actually needed; the rest is the reference userland app's operator-facing
`/next-up` rendering, which stays host-side. Pulling these two pure helpers into
their own leaf module
(rather than dragging `next_up_render`) is the §4 "port the spine, not the prose"
discipline applied at function granularity.

A *lane* owns a set of repo-relative path globs — its *tree*. Two lanes are safe
to run concurrently only when their trees are pairwise disjoint at the
directory-prefix level: no normalized prefix of one is a prefix of the other.

This is **predicate/range locking**, not swim-lane separation: a lane is a leased
predicate-lock over a region of the workspace, and `prefixes_collide` below is the
(conservative, decidable) predicate-intersection test it admits on — general
predicate-satisfiability being undecidable, the prefix rule over-approximates it.
See `docs/89_the-lane-is-a-region-lock.md`.
"""

from __future__ import annotations

from typing import Callable, Optional


def norm_tree_prefix(p: str) -> str:
    """Normalize one tree entry to a comparable directory prefix.

    ``agents/apply_*.py`` -> ``agents/apply_``; ``go/internal/ui/`` ->
    ``go/internal/ui/``; ``job_search/scoring.py`` -> ``job_search/scoring.py``.
    A glob is truncated at the first **glob metacharacter** (``*``, ``?``, or
    ``[``) because everything after it is a wildcard — two entries that share the
    pre-wildcard prefix can name the same file. Truncating only at ``*`` left
    ``?``/``[`` as *literals* and produced **false-disjoints** (#144): e.g.
    ``norm_tree_prefix("?/a")`` kept ``"?/a"``, so ``prefixes_collide("?/a",
    "b/a")`` was ``False`` even though the concrete path ``b/a`` matches ``?/a``.

    A **leading-glob** entry like ``**/*``, ``*.py``, ``?x`` or ``[ab]/c``
    truncates to the EMPTY prefix ``""`` — there is no pre-wildcard directory to
    anchor on. The empty prefix is the *universal* prefix: every path starts with
    it, so it matches **everything**. Callers must not silently drop it (that
    inverts a whole-repo tree into "touches nothing"); see `prefixes_collide`.

    **Case is folded** (``str.casefold``) so the prefix algebra matches the
    semantics of a case-INsensitive filesystem — DOS's documented primary platform
    is Windows, where ``Core/Engine/run.py`` and ``core/engine/run.py`` are the
    SAME file. Without folding, the case-sensitive ``startswith`` in
    `prefixes_collide` judges those two as disjoint, so two lanes editing one real
    file would both be admitted a lease (a false-ADMIT → concurrent writes to one
    file → corruption) and the SELF_MODIFY guard would be bypassable by mixed-case
    paths (``SRC/dos/arbiter.py`` slips past). Folding is **unconditional** (not
    branched on ``os.name``): a lane tree authored on one platform must collide
    identically when the kernel runs on another (deterministic CI), and on a truly
    case-sensitive FS treating two case-variants as colliding is a HARMLESS
    over-refusal — exactly the safe, conservative over-approximation direction this
    module already embraces (`lane_trees_disjoint`'s empty-tree rule). It does NOT
    weaken the filename-prefix discrimination the workshop driver relies on
    (``docs/ui-`` vs ``docs/svc-`` stay distinct after folding); it only ADDS the
    case-variant collisions a case-insensitive FS demands.
    """
    p = (p or "").replace("\\", "/").strip().casefold()
    # Truncate at the FIRST glob metacharacter — `*` (any run), `?` (any single
    # char), or `[` (a char class). `?` and `[` are wildcards exactly as `*` is, so
    # keeping them as literals in the prefix produced false-disjoints (#144): e.g.
    # the concrete path `b/a` matches the glob `?/a`, yet `prefixes_collide("?/a",
    # "b/a")` was False. Cutting at the earliest metachar yields a SHORTER prefix →
    # MORE collisions → no false-disjoint (the unsafe, corrupting direction); a
    # false-conflict only slows. min() over the present metachars (find → -1 when
    # absent, filtered out) picks the earliest.
    cuts = [i for i in (p.find("*"), p.find("?"), p.find("[")) if i != -1]
    if cuts:
        return p[:min(cuts)]
    return p


def prefixes_collide(a: str, b: str) -> bool:
    """True iff two normalized prefixes can name the same file.

    The single definition of "these two tree prefixes overlap," shared by every
    collision check in the kernel (`lane_trees_disjoint`, `lane_overlap`, the
    self-modify guard) so they cannot drift apart — the drift that let
    `lane_overlap` call two ``**/*`` lanes "fully disjoint" while
    `lane_trees_disjoint` (correctly) called them overlapping.

    Two prefixes collide when one is a prefix of the other (the original rule).
    The **empty prefix** (`""`, from a leading-glob like ``**/*``) is the
    universal prefix — it collides with *everything*, including another empty
    prefix — because ``"".startswith(x)`` is only true for ``x == ""`` but
    ``x.startswith("")`` is true for all ``x``. The asymmetry is handled here so
    every caller treats a whole-repo glob as the maximal blast radius it is.
    """
    return a.startswith(b) or b.startswith(a)


def lane_trees_disjoint(tree_a: list[str], tree_b: list[str]) -> bool:
    """True when two lane file trees cannot edit the same file.

    **Conservative-by-design — an empty tree is treated as NOT disjoint.** An
    empty tree is an *unknown* blast radius, not a *zero* one, so this returns
    ``False`` (unsafe / overlapping) when either tree is empty: the caller must
    refuse a concurrent admission rather than assume the lane touches nothing.
    """
    if not tree_a or not tree_b:
        # Unknown blast radius — refuse. See the docstring.
        return False
    norm_a = [norm_tree_prefix(p) for p in tree_a if p]
    norm_b = [norm_tree_prefix(p) for p in tree_b if p]
    if not norm_a or not norm_b:
        return False
    for na in norm_a:
        for nb in norm_b:
            if na.startswith(nb) or nb.startswith(na):
                return False
    return True


def tree_disjoint_from_all_live(
    *,
    requested_tree: list[str],
    live: list[dict],
    sibling_tree_lookup: Callable[[str], Optional[list[str]]],
) -> bool:
    """True iff ``requested_tree`` is provably disjoint from EVERY live sibling.

    The shared "can this region run alongside everything currently live" predicate
    — the same posture as `lane_trees_disjoint`, lifted from `sibling_scan`'s own
    `_disjoint_from_all_live` so the lane ARBITER (selection-time) and the SIBLING
    SCAN (post-acquire escape) prove disjointness through one definition and cannot
    drift apart. Conservative on three counts, each mapping an *unknown* to "cannot
    prove disjoint → not safe":

      * a sibling whose ``lane`` is empty/unknown → no resolvable tree → unknown
        blast radius → NOT provably disjoint (returns ``False``). A read-only
        activity-class sibling (an un-leased ``/replan``) must be filtered out
        UPSTREAM by the caller, not waved through on an empty tree.
      * a sibling whose tree resolves empty (lookup miss) → unknown → ``False``.
      * any sibling whose tree OVERLAPS the requested tree → ``False``.

    Only when every live sibling has a known, non-empty, disjoint tree is it safe to
    run concurrently. An empty ``live`` (no siblings) is vacuously disjoint → ``True``.
    """
    for s in live:
        lane = str(s.get("lane") or "")
        if not lane:
            return False  # unknown blast radius — cannot prove disjoint
        try:
            tree = list(sibling_tree_lookup(lane) or [])
        except Exception:
            tree = []
        if not tree:
            return False  # tree did not resolve — unknown — not safe
        if not lane_trees_disjoint(list(requested_tree), tree):
            return False  # provable overlap
    return True
