"""Executable spec for the `?`-as-literal false-disjoint in the prefix algebra (#144).

`prefixes_collide` (via `norm_tree_prefix`) treats the glob `?` as a LITERAL
character, not a single-char wildcard: `norm_tree_prefix` only truncates at the
first `*`, so a `?` survives into the compared prefix and is matched by
`str.startswith` as the literal byte `?`. The result is a **false-disjoint** — the
predicate calls two regions non-overlapping when a concrete path matches both,
which is the unsafe side of the soundness asymmetry (a false-disjoint, if admitted,
is two writers on one file).

This file is the executable form of issue #144: the soundness oracle (an
independent `fnmatch` matcher) finds a concrete path matching both globs, while
`prefixes_collide` says disjoint. It is marked `xfail(strict=True)` so:

  * the suite stays GREEN today (the gap is known, not a surprise regression);
  * the moment `prefixes_collide`/`norm_tree_prefix` is fixed to handle `?` (or to
    conservatively widen `?`-containing entries to collide), this test FLIPS to an
    unexpected-pass and `strict=True` turns the suite RED — forcing the fixer to
    delete the `xfail` and convert it to a normal regression guard. An xfail that
    silently keeps passing-as-failing can hide a fix; strict mode makes the fix
    announce itself.

See also: docs/90 §1 / docs/92 (the precision plan; its Phase-1 `globs_can_overlap`
handles `?` correctly in its own segment logic and inherits this gap only where it
conservatively delegates `**` to `prefixes_collide`).
"""
from __future__ import annotations

import fnmatch

import pytest

from dos._tree import norm_tree_prefix, prefixes_collide


def _old_collide(a: str, b: str) -> bool:
    """The shipped predicate, applied the way every kernel caller applies it."""
    return prefixes_collide(norm_tree_prefix(a), norm_tree_prefix(b))


def _concrete_matches_both(path: str, a: str, b: str) -> bool:
    """An INDEPENDENT oracle: does one concrete path match both globs? Segment-aware
    `fnmatch` (── `?`/`*` confined to a segment ──), not the predicate under test."""
    def seg_match(p: str, g: str) -> bool:
        ps, gs = p.split("/"), g.split("/")
        return len(ps) == len(gs) and all(
            fnmatch.fnmatchcase(x, y) for x, y in zip(ps, gs))
    return seg_match(path, a) and seg_match(path, b)


# Pairs where a concrete path provably matches BOTH globs, yet the prefix algebra
# (which reads `?` as a literal) calls them disjoint. Each row: (a, b, witness).
_FALSE_DISJOINT_CASES = [
    ("?/a", "b/a", "b/a"),          # `?` matches `b`; old compares literal '?/a' vs 'b/a'
    ("?/x", "y/x", "y/x"),
    ("src/?.py", "src/a.py", "src/a.py"),   # `?` matches `a`; old keeps literal '?'
]


def test_oracle_confirms_these_pairs_really_overlap():
    """Sanity: the witnesses genuinely match both globs (the oracle is not vacuous).
    This part is NOT xfail — it pins that the cases are real overlaps."""
    for a, b, witness in _FALSE_DISJOINT_CASES:
        assert _concrete_matches_both(witness, a, b), (
            f"oracle bug: {witness!r} should match both {a!r} and {b!r}")


@pytest.mark.parametrize("a,b,witness", _FALSE_DISJOINT_CASES)
@pytest.mark.xfail(strict=True, reason="#144: prefixes_collide treats `?` as a "
                   "literal, so it false-disjoints these genuinely-overlapping "
                   "pairs. Fixing #144 flips this to pass — delete the xfail then.")
def test_prefixes_collide_should_not_false_disjoint_on_question_mark(a, b, witness):
    """The soundness claim the fix must satisfy: a pair a concrete path matches BOTH
    must be reported as colliding. Today `prefixes_collide` reports disjoint (the
    #144 gap), so this xfails; the fix makes it pass."""
    assert _concrete_matches_both(witness, a, b)   # the overlap is real…
    assert _old_collide(a, b), (                    # …so the predicate MUST collide
        f"FALSE DISJOINT (#144): {witness!r} matches both {a!r} and {b!r}, "
        f"but prefixes_collide(norm({a!r}), norm({b!r})) said disjoint")
