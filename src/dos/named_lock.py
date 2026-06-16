"""The `lock://NAME` region scheme — deconfliction beyond the file tree (docs/363).

The arbiter is "a lock manager whose granularity is a glob-set" (docs/89): two
workers may run concurrently iff their declared regions are provably disjoint. A
region has, until now, been a set of **file-path globs**. But a fleet collides on
plenty of resources that have **no file representation at all** — the `gh-pages`
publish step, a PyPI upload, a tag-namespace mint, a shared external critical
section. Two `/release` loops can race that publish today and nothing leases it,
because there is no path to put in a lane tree.

This leaf adds the **smallest sound generalization** of the region: a region entry
may be a `lock://NAME` URI naming a **pure named mutex** — an abstract critical
section identified only by a string. It is the degenerate region-lock: a namespace
of cardinality one, where "disjoint" means "different name." That is exactly the
docs/89 §4.2 thesis made concrete — *the same primitive over a richer predicate
algebra, not a new arbiter* — and it is the one axis from the docs/363 survey that
the file-tree algebra genuinely **cannot** express today.

Why this needs almost no new mechanism
=======================================

A `lock://NAME` entry rides the EXISTING `_tree.prefixes_collide` algebra
unchanged for the common case: `lock://gh-pages-publish` and
`lock://gh-pages-publish` already collide (same prefix), `lock://a` and `lock://b`
are already disjoint, and `lock://anything` never prefixes a real file path. The
arbiter, the `DisjointnessPredicate`, and the lease/journal spine are all
untouched — a lock region is just another string in the lane `tree`. The file://
suite stays byte-green **by construction** because the path algebra is not edited.

The TWO things the raw prefix algebra gets wrong for a *named mutex* — and that
this module is the single home for — are both in the conservative (refuse-more)
direction, so getting them right can only ADD refusals:

  1. **A glob inside a lock name is meaningless and unsafe.** `_tree.norm_tree_prefix`
     truncates at the first glob metachar, so `lock://*` and `lock://anything-*`
     both collapse to the bare `lock://` prefix and would falsely collide with
     *every* other lock. A mutex name is a POINT, not a range; a wildcard in it is
     never what the operator meant. So a wildcarded lock name is treated as an
     **unknown** critical section → the universal region (collides with
     everything), never a narrow one.
  2. **A bare / empty lock name is an UNKNOWN blast radius.** `lock://` with no
     name is the named-mutex analogue of the empty tree: the operator declared "a
     critical section" but not WHICH one. The sound reading is the same as
     `_tree.lane_trees_disjoint`'s empty-tree rule and the `**/*` → universal-prefix
     corner — an unknown region collides with everything, so it is refused
     alongside any live region rather than waved through as "touches nothing."

Everything else (a present, glob-free name) maps to a reserved, file-disjoint
prefix that collides only with the identical lock. No per-scheme conflict function,
no parser, no edit to the path algebra.

The honest scope limit (docs/363 §"what this is NOT")
=====================================================

This ships ONE scheme — `lock://` — deliberately. The full resource-URI algebra
(`branch://`, `db://rows/{range}`, `k8s://ns/deploy`, `queue://topic/partition`)
is a real future direction docs/89 §4.2 sanctioned, but each of those needs its
own range-intersection predicate AND a **cross-scheme aliasing analysis**: a
`branch://master` lease and a `file://src/**` lease name the SAME working-tree
bytes (a checkout/merge rewrites the tree), so a naive "different schemes never
collide" rule would FALSE-ADMIT a real collision — the one direction a region-lock
must never fail in. `lock://` is the scheme that is sound TODAY because a pure
named mutex is over a substrate physically independent of every file: holding
`lock://gh-pages-publish` claims the abstract publish step, not any path. A lock
that guards a file region must ALSO declare that file region in the same lease;
this module does not invent a mapping from a lock name to files.

Pure stdlib + the `_tree` prefix algebra it delegates to — no I/O, no host names.
The `lock://` scheme token is generic; the kernel names no specific critical
section (the operator declares `lock://gh-pages-publish` in their own lease tree).
"""

from __future__ import annotations

from dos._tree import norm_tree_prefix as _norm_tree_prefix

# The reserved scheme token. A lane-tree entry starting with this (case-folded) is
# a named-mutex region, not a file path. Chosen so it can never collide with a real
# repo-relative path: a path entry never starts with `lock://` (a leading `lock/`
# directory would be `lock/…`, no `://`), and the `://` keeps it visually a URI.
LOCK_SCHEME = "lock://"

# The reserved normalized-prefix namespace a resolved lock name lives in. A present,
# glob-free lock NAME `n` normalizes to `LOCK_PREFIX_NS + n` — a string that (a)
# collides with the identical lock and nothing else, and (b) never prefixes or is
# prefixed by a real file path (a file prefix is repo-relative, never starts with
# this sentinel). Distinct from the raw `lock://` so a bare/wildcarded lock can be
# sent to the UNIVERSAL prefix instead without aliasing a real `lock://literal`.
LOCK_PREFIX_NS = "\x00lock\x00/"


def is_lock_entry(entry: str) -> bool:
    """True iff a lane-tree entry is a `lock://` named-mutex region (not a file).

    Case-insensitive on the scheme (the path algebra case-folds throughout), so
    `LOCK://X` and `lock://x` are the same scheme — matching `norm_tree_prefix`'s
    unconditional `casefold`.
    """
    return (entry or "").replace("\\", "/").strip().casefold().startswith(LOCK_SCHEME)


def lock_name(entry: str) -> str:
    """The critical-section name from a `lock://NAME` entry — `''` if absent.

    `lock://gh-pages-publish` → `gh-pages-publish`; bare `lock://` → `''` (an
    unknown critical section). Case-folded, slash-normalized, stripped — the same
    fold the prefix algebra uses, so two spellings of one lock are one lock.
    """
    s = (entry or "").replace("\\", "/").strip().casefold()
    if not s.startswith(LOCK_SCHEME):
        return ""
    return s[len(LOCK_SCHEME):].strip("/").strip()


def _name_has_glob(name: str) -> bool:
    """A glob metachar (`*`, `?`, `[`) anywhere in a lock NAME — the path algebra's
    own metachar set (`_tree.norm_tree_prefix`). A named mutex is a point, so a glob
    in it is never intended; we treat such a name as UNKNOWN (universal), never as a
    narrow region the truncation would otherwise produce."""
    return any(c in name for c in ("*", "?", "["))


def normalize_entry(entry: str) -> str:
    """Normalize ONE lane-tree entry to a comparable prefix — the lock-aware front
    door to `_tree.norm_tree_prefix`.

    A **file** entry (no `lock://` scheme) is passed through to the unchanged
    `_tree.norm_tree_prefix` verbatim — so the file:// algebra is byte-identical and
    every existing collision verdict is preserved.

    A **lock** entry resolves so two same-named locks collide, two different-named
    locks are disjoint, and a lock never collides with a file:

      * a present, glob-free name `n` → `LOCK_PREFIX_NS + n` (a reserved,
        file-disjoint point that collides only with the identical lock);
      * a bare/empty name, or a name containing a glob → `""`, the UNIVERSAL
        prefix (`_tree.prefixes_collide` treats `""` as colliding with everything)
        — the conservative "unknown critical section, refuse alongside any live
        region" reading, the named-mutex twin of the empty-tree rule.

    The result is a plain prefix string the EXISTING `_tree.prefixes_collide` /
    `lane_trees_disjoint` compare correctly with no further change — which is the
    whole point: a richer region rode the unchanged gate.
    """
    if not is_lock_entry(entry):
        return _norm_tree_prefix(entry)
    name = lock_name(entry)
    if not name or _name_has_glob(name):
        # Unknown / wildcarded critical section → universal region (collide with
        # all). Same safe direction as the `**/*` → empty-prefix and empty-tree
        # rules: an unknown blast radius is never waved through.
        return ""
    return LOCK_PREFIX_NS + name


def normalize_tree(tree: list[str]) -> list[str]:
    """Lock-aware normalization of a whole lane tree — `normalize_entry` per entry,
    blank entries dropped (no path information), order preserved.

    This is what a caller threads in place of a raw `[norm_tree_prefix(p) for p in
    tree]` when a tree may carry `lock://` regions. The resulting prefixes feed the
    UNCHANGED `_tree.prefixes_collide` exactly as file prefixes do.
    """
    out: list[str] = []
    for p in tree or []:
        if not (p or "").strip():
            continue
        out.append(normalize_entry(p))
    return out
