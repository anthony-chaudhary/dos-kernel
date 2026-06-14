"""Regression for #137: the root index/meta files take a CONCURRENT lane, not `global`.

`llms.txt`, `llms-full.txt`, and `CITATION.cff` are root metadata/index docs by
nature. Before #137 they were absent from the curated `[lanes].meta` list in this
repo's `dos.toml`, so editing them matched only the exclusive `global` lane
(`**/*`) — where the SELF_MODIFY guard rightly refuses a live loop, since `global`
also covers `src/dos/`. The effect: a routine docs edit to `llms.txt` could take no
clean concurrent lease (the working ritual had to `--force` the kernel lane or route
around it).

The fix added the three files to `[lanes].meta`. This pins that they resolve to the
concurrent `meta` lane AND that adding them did not break the prefix-disjointness
floor (`meta` must stay disjoint from every other declared lane — they are explicit
file paths, not globs, so no `**/*` collision).
"""
from __future__ import annotations

from pathlib import Path

from dos.config import LaneTaxonomy, load_lanes_from_toml
from dos._tree import lane_trees_disjoint

# This repo's own dos.toml (the workspace under test). The test lives at
# <repo>/tests/, so the dos.toml is one directory up.
_REPO = Path(__file__).resolve().parents[1]
_DOS_TOML = _REPO / "dos.toml"

_ROOT_INDEX_FILES = ("llms.txt", "llms-full.txt", "CITATION.cff")


def _meta_tree() -> tuple[str, ...]:
    base = LaneTaxonomy(concurrent=(), autopick=(), exclusive=(), trees={})
    lanes = load_lanes_from_toml(_DOS_TOML, base=base)
    return tuple(lanes.trees.get("meta", ()))


def test_root_index_files_are_in_the_meta_lane():
    """The three root index files resolve to `meta`'s declared tree (#137)."""
    meta = _meta_tree()
    for f in _ROOT_INDEX_FILES:
        assert f in meta, (
            f"{f!r} is not in [lanes].meta — it falls to the exclusive `global` "
            "lane, where the SELF_MODIFY guard refuses a live loop (#137)")


def test_meta_is_a_concurrent_lane():
    """`meta` must be concurrent (and on the autopick ladder), not exclusive — the
    whole point is that a root-index edit can take a clean concurrent lease."""
    base = LaneTaxonomy(concurrent=(), autopick=(), exclusive=(), trees={})
    lanes = load_lanes_from_toml(_DOS_TOML, base=base)
    assert "meta" in lanes.concurrent
    assert "meta" not in lanes.exclusive


def test_meta_tree_stays_disjoint_from_every_other_lane():
    """The soundness floor: adding the root index files keeps `meta` disjoint from
    every other declared lane (explicit file paths, no `**/*` glob that would
    collide with the whole tree). A `?`/`*`-free literal path can only collide with
    a lane that names the same file — none does."""
    base = LaneTaxonomy(concurrent=(), autopick=(), exclusive=(), trees={})
    lanes = load_lanes_from_toml(_DOS_TOML, base=base)
    meta = list(lanes.trees.get("meta", ()))
    assert meta, "meta tree is empty — the loader did not read this repo's dos.toml"
    for name, tree in lanes.trees.items():
        if name in ("meta", "global"):
            continue  # self, and global is the exclusive superset by design
        other = list(tree)
        if not other:
            continue
        assert lane_trees_disjoint(meta, other), (
            f"meta tree overlaps lane {name!r} ({other}) — adding the root index "
            "files broke the prefix-disjointness floor (#137 must not regress it)")
