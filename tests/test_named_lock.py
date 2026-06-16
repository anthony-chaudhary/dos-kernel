"""The `lock://NAME` named-mutex region axis (docs/363 — deconfliction beyond files).

These pin the smallest sound generalization of a lane region past the file tree: a
`lock://NAME` entry names a PURE named mutex (an abstract critical section with no
file backing — the `gh-pages` publish step, a PyPI upload), and rides the UNCHANGED
`prefixes_collide` algebra so:

  * two loops naming the SAME lock collide (the second refuses — REFUSE_EXACT_GLOB,
    a hard collision, not a diluted ratio);
  * two DIFFERENT locks are disjoint (both admit);
  * a lock never collides with a FILE region (different substrate);
  * a BARE / WILDCARDED lock name is an UNKNOWN critical section → the universal
    region (collides with everything) — the named-mutex twin of the empty-tree rule;
  * `--force` overrides exactly as it overrides any other refusal.

The load-bearing regression (`TestFilePathByteIdentical`): adding lock-awareness must
leave the file:// algebra BYTE-IDENTICAL — `normalize_entry` passes a non-lock entry
straight through to `_tree.norm_tree_prefix`, so every existing file verdict is
unchanged. The full arbiter/overlap suite staying green through this path is the
companion proof.
"""
from __future__ import annotations

from dos._tree import norm_tree_prefix
from dos.named_lock import (
    LOCK_SCHEME,
    is_lock_entry,
    lock_name,
    normalize_entry,
    normalize_tree,
)
from dos.lane_overlap import overlap_verdict, Verdict
from dos.arbiter import arbitrate


def _live(lane, tree, kind="keyword"):
    return {"lane": lane, "lane_kind": kind, "tree": tree, "loop_ts": "t1"}


# ── the scheme parsing ──────────────────────────────────────────────────────
class TestLockEntryParsing:
    def test_recognises_the_scheme(self):
        assert is_lock_entry("lock://gh-pages-publish") is True
        assert is_lock_entry("LOCK://Caps") is True  # scheme is case-folded
        assert is_lock_entry("  lock://x  ") is True  # stripped
        # Backslashes are normalized to `/` everywhere in the algebra, so a
        # Windows-authored `lock:\\name` is the SAME scheme as `lock://name`.
        assert is_lock_entry("lock:\\\\back") is True
        assert is_lock_entry("plain-keyword-lane") is False  # no scheme at all

    def test_a_file_entry_is_not_a_lock(self):
        assert is_lock_entry("src/dos/cli.py") is False
        assert is_lock_entry("docs/**") is False
        assert is_lock_entry("") is False

    def test_lock_name_extraction(self):
        assert lock_name("lock://gh-pages-publish") == "gh-pages-publish"
        assert lock_name("LOCK://Pypi-Upload") == "pypi-upload"  # folded
        assert lock_name("lock://a/b") == "a/b"
        assert lock_name("lock://") == ""  # bare = unknown
        assert lock_name("src/x.py") == ""  # not a lock


# ── the normalization (the one new piece of mechanism) ──────────────────────
class TestNormalizeEntry:
    def test_file_entry_is_byte_identical_to_norm_tree_prefix(self):
        # The byte-green guarantee: a non-lock entry must be EXACTLY what
        # _tree.norm_tree_prefix returns — no drift.
        for p in ["src/dos/cli.py", "docs/**", "agents/apply_*.py", "**/*",
                  "*.py", "go/internal/ui/", "Core/Engine/run.py", ""]:
            assert normalize_entry(p) == norm_tree_prefix(p)

    def test_present_lock_name_maps_to_a_reserved_file_disjoint_prefix(self):
        a = normalize_entry("lock://gh-pages-publish")
        b = normalize_entry("lock://pypi-upload")
        assert a != b  # different locks → different prefixes
        assert a == normalize_entry("LOCK://GH-Pages-Publish")  # same lock, folded
        # The reserved prefix can never equal a real file prefix.
        assert a != norm_tree_prefix("lock/gh-pages-publish")
        assert not a.startswith(norm_tree_prefix("src/dos/cli.py"))
        assert not norm_tree_prefix("src/dos/cli.py").startswith(a)

    def test_bare_lock_is_universal(self):
        # An unknown critical section → the universal (empty) prefix.
        assert normalize_entry("lock://") == ""

    def test_wildcarded_lock_name_is_universal(self):
        # A glob in a mutex name is never intended (a mutex is a point); treat it
        # as UNKNOWN → universal, never a narrow region the truncation would make.
        assert normalize_entry("lock://release-*") == ""
        assert normalize_entry("lock://a?b") == ""
        assert normalize_entry("lock://[abc]") == ""

    def test_normalize_tree_drops_only_blank_entries(self):
        out = normalize_tree(["lock://x", "  ", "src/a.py", ""])
        assert out == [normalize_entry("lock://x"), norm_tree_prefix("src/a.py")]


# ── the overlap verdict over lock regions ───────────────────────────────────
class TestLockOverlapVerdict:
    def test_same_lock_is_a_hard_collision(self):
        v = overlap_verdict(["lock://gh-pages-publish"], ["lock://gh-pages-publish"])
        # Two loops claiming the same critical section is an exact-glob hard
        # collision, not a ratio that could be diluted by padding.
        assert v.verdict == Verdict.REFUSE_EXACT_GLOB
        assert not v.admissible

    def test_different_locks_are_disjoint(self):
        v = overlap_verdict(["lock://gh-pages-publish"], ["lock://pypi-upload"])
        assert v.verdict == Verdict.ADMIT_DISJOINT
        assert v.admissible

    def test_lock_never_collides_with_a_file(self):
        v = overlap_verdict(["lock://gh-pages-publish"], ["src/dos/cli.py", "docs/**"])
        assert v.admissible
        # And the reverse direction is symmetric.
        v2 = overlap_verdict(["src/dos/cli.py"], ["lock://gh-pages-publish"])
        assert v2.admissible

    def test_bare_lock_collides_with_everything(self):
        # Universal prefix → shares with any non-empty lease tree (refuse-more).
        v = overlap_verdict(["lock://"], ["src/dos/cli.py"])
        assert not v.admissible


# ── end-to-end through the real arbiter ─────────────────────────────────────
class TestArbitrateLockRegions:
    def test_same_lock_second_loop_refuses(self):
        d = arbitrate(
            requested_lane="release", requested_kind="keyword",
            requested_tree=["lock://gh-pages-publish"],
            live_leases=[_live("release-a", ["lock://gh-pages-publish"])],
            config=None,
        )
        assert d.outcome == "refuse"

    def test_different_locks_both_admit(self):
        d = arbitrate(
            requested_lane="pypi", requested_kind="keyword",
            requested_tree=["lock://pypi-upload"],
            live_leases=[_live("release-a", ["lock://gh-pages-publish"])],
            config=None,
        )
        assert d.outcome == "acquire"

    def test_lock_and_file_loops_coexist(self):
        # A publish-lock loop and a docs loop touch independent substrates.
        d = arbitrate(
            requested_lane="release", requested_kind="keyword",
            requested_tree=["lock://gh-pages-publish"],
            live_leases=[_live("docs", ["docs/foo.md"])],
            config=None,
        )
        assert d.outcome == "acquire"

    def test_bare_lock_refuses_against_a_live_lease(self):
        d = arbitrate(
            requested_lane="x", requested_kind="keyword",
            requested_tree=["lock://"],
            live_leases=[_live("docs", ["docs/foo.md"])],
            config=None,
        )
        assert d.outcome == "refuse"

    def test_force_overrides_a_same_lock_collision(self):
        # --force is the sole override of any refusal, lock regions included.
        d = arbitrate(
            requested_lane="release", requested_kind="keyword",
            requested_tree=["lock://gh-pages-publish"],
            live_leases=[_live("release-a", ["lock://gh-pages-publish"])],
            config=None, force=True,
        )
        assert d.outcome == "acquire"


# ── the load-bearing regression: file:// stays byte-identical ───────────────
class TestFilePathByteIdentical:
    def test_file_only_verdicts_unchanged(self):
        # A representative spread of file-tree pairs must give the IDENTICAL
        # verdict they did before lock-awareness — proven by routing them through
        # the same overlap_verdict the suite already pins, with no lock entries.
        pairs = [
            (["agents/apply_*.py"], ["agents/apply_*.py"]),   # exact glob
            (["docs/a.md"], ["src/b.py"]),                    # disjoint
            (["**/*"], ["src/dos/cli.py"]),                   # universal vs file
        ]
        verdicts = [overlap_verdict(r, l).verdict for r, l in pairs]
        assert verdicts == [
            Verdict.REFUSE_EXACT_GLOB,
            Verdict.ADMIT_DISJOINT,
            Verdict.REFUSE_OVERLAP,  # **/* collides with everything
        ]
