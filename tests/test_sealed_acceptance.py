"""Tests for `dos.drivers.sealed_acceptance` — the git-sealed acceptance ScopeSource
(docs/390 Phase 1).

The sibling of `test_scope_source.py`, but the load-bearing new property is the
SEAL: the acceptance manifest must be present at the run's START commit
(`LedgerState.start_sha`), read as the *committed* blob — so "authored before the
work" is a git-checkable fact and a worker's working-tree tampering cannot move the
bar. The seam's structural guarantee is inherited unchanged: this source can only
ever WITHHOLD `COMPLETE` (push toward `UNDERDECLARED`), never grant it.

  * `TestParse`            — the manifest parser: well-formed → claims; malformed /
                             no-claims / bad-id → None (the fail-to-strict input);
                             `required=false` excluded from the required set.
  * `TestPureFold`         — `classify_sealed_acceptance` over synthetic `SealEvidence`:
                             every dishonest rung (no anchor / broken seal / malformed
                             / under-declared) and the two honest ones.
  * `TestSoundnessProof`   — THROUGH the real `completion.classify`: a broken/contesting
                             seal flips an otherwise-COMPLETE run to UNDERDECLARED, and
                             an honest seal can NEVER grant COMPLETE to an INCOMPLETE run.
  * `TestDriverGitSeal`    — the driver against REAL tmp git repos: it reads the
                             COMMITTED blob (not the tampered working tree); a manifest
                             added AFTER the start commit is a broken seal; an intact,
                             covered seal votes honest.
  * `TestDriverInert`      — no manifest configured → honest; no start commit → withhold.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dos.drivers import sealed_acceptance as sa
from dos import completion as cm
from dos.intent_ledger import LedgerState, VerifiedStep
from dos.resume import AncestryFacts
from dos.vcs import GitBackend


# ── manifest helpers ───────────────────────────────────────────────────────────
def _manifest(*ids: str, optional: tuple[str, ...] = ()) -> bytes:
    """A minimal acceptance.toml requiring `ids` (and listing `optional` as
    required=false)."""
    lines: list[str] = []
    for i in ids:
        lines += ['[[claim]]', f'id = "{i}"', 'rung = "oracle"', '']
    for i in optional:
        lines += ['[[claim]]', f'id = "{i}"', 'rung = "oracle"', 'required = false', '']
    return "\n".join(lines).encode("utf-8")


# ── 1. the parser ───────────────────────────────────────────────────────────────
class TestParse:
    def test_well_formed_parses(self):
        claims = sa.parse_acceptance(_manifest("a", "b"))
        assert claims is not None
        assert [c.id for c in claims] == ["a", "b"]
        assert all(c.rung == "oracle" and c.required for c in claims)

    def test_required_false_excluded_from_required_ids(self):
        claims = sa.parse_acceptance(_manifest("a", optional=("b",)))
        assert sa.required_ids(claims) == ("a",)

    def test_empty_claim_list_is_not_none(self):
        # An intentionally-empty bar (claim = []) is honest-no-required, NOT malformed.
        claims = sa.parse_acceptance(b"claim = []\n")
        assert claims == ()

    def test_no_claim_table_is_malformed(self):
        assert sa.parse_acceptance(b"title = 'x'\n") is None

    def test_bad_toml_is_malformed(self):
        assert sa.parse_acceptance(b"this is = = not toml") is None

    def test_claim_without_id_is_malformed(self):
        assert sa.parse_acceptance(b'[[claim]]\nrung = "oracle"\n') is None

    def test_required_ids_dedupes_preserving_order(self):
        claims = sa.parse_acceptance(_manifest("a", "b", "a"))
        assert sa.required_ids(claims) == ("a", "b")


# ── 2. the pure fold ────────────────────────────────────────────────────────────
class TestPureFold:
    def test_not_configured_is_honest(self):
        v = sa.classify_sealed_acceptance(sa.SealEvidence(configured=False), ("s1",))
        assert v.extent_honest is True
        assert v.source == "sealed-acceptance"

    def test_configured_but_unanchored_withholds(self):
        v = sa.classify_sealed_acceptance(
            sa.SealEvidence(configured=True, anchored=False), ("s1",))
        assert v.extent_honest is False
        assert "anchor" in v.reason

    def test_broken_seal_withholds(self):
        # Anchored, but the manifest was absent at the start commit → broken seal.
        v = sa.classify_sealed_acceptance(
            sa.SealEvidence(configured=True, anchored=True, sealed_blob=None), ("s1",))
        assert v.extent_honest is False
        assert "seal is broken" in v.reason

    def test_malformed_sealed_blob_withholds(self):
        v = sa.classify_sealed_acceptance(
            sa.SealEvidence(configured=True, anchored=True, sealed_blob=b"= = bad"), ("s1",))
        assert v.extent_honest is False
        assert "malformed" in v.reason

    def test_under_declared_withholds_with_missing(self):
        v = sa.classify_sealed_acceptance(
            sa.SealEvidence(configured=True, anchored=True, sealed_blob=_manifest("s1", "s2")),
            ("s1",),  # s2 sealed-required but never declared
        )
        assert v.extent_honest is False
        assert v.missing == ("s2",)

    def test_all_sealed_required_declared_is_honest(self):
        v = sa.classify_sealed_acceptance(
            sa.SealEvidence(configured=True, anchored=True, sealed_blob=_manifest("s1", "s2")),
            ("s1", "s2"),
        )
        assert v.extent_honest is True
        assert v.missing == ()


# ── 3. the soundness proof (through the real completion.classify) ───────────────
_C1, _C2 = "c1aaaaa", "c2bbbbb"


def _complete_state(declared=("s1", "s2")):
    """Every declared step verified on a non-forgeable rung → `resume` says COMPLETE,
    so the ONLY thing that can withhold completion is a scope verdict (the test_scope
    idiom)."""
    return LedgerState(
        run_id="RID-K", goal="g", start_sha=_C1, declared_steps=tuple(declared),
        verified={s: VerifiedStep(s, sha, via="file-path")
                  for s, sha in zip(declared, (_C1, _C2, "c3ccccc", "c4ddddd"))},
    )


def _complete_anc(declared=("s1", "s2")):
    shas = (_C1, _C2, "c3ccccc", "c4ddddd")[:len(declared)]
    return AncestryFacts(
        shas_in_ancestry=frozenset(shas),
        steps_verified_at_read=frozenset(declared),
        lane_advanced_past_resume=False,
    )


def _incomplete_state(declared=("s1", "s2")):
    """Only the first declared step verified → a non-empty residual → `resume` says
    RESUMABLE, so completion is INCOMPLETE no matter what a scope source votes."""
    return LedgerState(
        run_id="RID-K", goal="g", start_sha=_C1, declared_steps=tuple(declared),
        verified={declared[0]: VerifiedStep(declared[0], _C1, via="file-path")},
    )


def _incomplete_anc(declared=("s1", "s2")):
    return AncestryFacts(
        shas_in_ancestry=frozenset({_C1}),
        steps_verified_at_read=frozenset({declared[0]}),
        lane_advanced_past_resume=False,
    )


class TestSoundnessProof:
    def test_intact_covered_seal_allows_complete(self):
        # Baseline: a complete run with an honest seal verdict IS COMPLETE.
        honest = sa.classify_sealed_acceptance(
            sa.SealEvidence(configured=True, anchored=True, sealed_blob=_manifest("s1", "s2")),
            ("s1", "s2"))
        v = cm.classify(_complete_state(), _complete_anc(), scope_verdicts=(honest,))
        assert v.state is cm.Completion.COMPLETE

    def test_broken_seal_downgrades_complete_to_underdeclared(self):
        # The headline refusal: a complete run whose seal is broken is NOT done.
        broken = sa.classify_sealed_acceptance(
            sa.SealEvidence(configured=True, anchored=True, sealed_blob=None), ("s1", "s2"))
        v = cm.classify(_complete_state(), _complete_anc(), scope_verdicts=(broken,))
        assert v.state is cm.Completion.UNDERDECLARED

    def test_honest_seal_cannot_grant_completion_to_incomplete_run(self):
        # The structural guarantee: an honest seal NEVER upgrades an INCOMPLETE run.
        honest = sa.classify_sealed_acceptance(
            sa.SealEvidence(configured=True, anchored=True, sealed_blob=_manifest("s1", "s2")),
            ("s1", "s2"))
        v = cm.classify(_incomplete_state(), _incomplete_anc(), scope_verdicts=(honest,))
        assert v.state is cm.Completion.INCOMPLETE

    def test_lying_extent_honest_still_cannot_manufacture_complete(self):
        # Even a hand-forged extent_honest=True verdict cannot make an incomplete run
        # done — the residual floor, not the scope vote, owns the positive done-bit.
        from dos.scope_source import ScopeVerdict
        forged = ScopeVerdict(extent_honest=True, reason="trust me", source="sealed-acceptance")
        v = cm.classify(_incomplete_state(), _incomplete_anc(), scope_verdicts=(forged,))
        assert v.state is cm.Completion.INCOMPLETE


# ── 4. the driver against real git ──────────────────────────────────────────────
def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True,
        check=True, stdin=subprocess.DEVNULL,
    )
    return proc.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "wt"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    (r / "seed.txt").write_text("seed\n")
    _git(r, "add", "seed.txt")
    _git(r, "commit", "-q", "-m", "seed")
    return r


def _state(start_sha: str, declared: tuple[str, ...]) -> LedgerState:
    return LedgerState(run_id="RID", goal="g", start_sha=start_sha,
                       declared_steps=tuple(declared))


class TestDriverGitSeal:
    def test_reads_committed_blob_not_tampered_working_tree(self, repo: Path):
        # Seal: manifest requiring s1 committed at the start commit.
        (repo / "acceptance.toml").write_bytes(_manifest("s1"))
        _git(repo, "add", "acceptance.toml")
        _git(repo, "commit", "-q", "-m", "seal acceptance")
        start_sha = _git(repo, "rev-parse", "HEAD").strip()
        # The worker tampers the WORKING TREE to an empty bar (no required claims)…
        (repo / "acceptance.toml").write_bytes(b"claim = []\n")
        # …and declares nothing. If the source read the working tree it would vote
        # honest; reading the COMMITTED blob (s1 required) it must withhold.
        src = sa.SealedAcceptanceScope("acceptance.toml", vcs=GitBackend(root=repo))
        v = src.scope_verdict(_state(start_sha, ()), config=None)
        assert v.extent_honest is False
        assert v.missing == ("s1",)

    def test_manifest_added_after_start_is_a_broken_seal(self, repo: Path):
        # The run starts BEFORE any manifest exists.
        start_sha = _git(repo, "rev-parse", "HEAD").strip()
        # The manifest is added only later — exactly the post-hoc co-design the seal refuses.
        (repo / "acceptance.toml").write_bytes(_manifest("s1"))
        _git(repo, "add", "acceptance.toml")
        _git(repo, "commit", "-q", "-m", "late add")
        src = sa.SealedAcceptanceScope("acceptance.toml", vcs=GitBackend(root=repo))
        v = src.scope_verdict(_state(start_sha, ("s1",)), config=None)
        assert v.extent_honest is False
        assert "seal is broken" in v.reason

    def test_intact_covered_seal_votes_honest(self, repo: Path):
        (repo / "acceptance.toml").write_bytes(_manifest("s1", "s2"))
        _git(repo, "add", "acceptance.toml")
        _git(repo, "commit", "-q", "-m", "seal")
        start_sha = _git(repo, "rev-parse", "HEAD").strip()
        src = sa.SealedAcceptanceScope("acceptance.toml", vcs=GitBackend(root=repo))
        v = src.scope_verdict(_state(start_sha, ("s1", "s2")), config=None)
        assert v.extent_honest is True


# ── 5. the inert paths ──────────────────────────────────────────────────────────
class TestDriverInert:
    def test_no_manifest_configured_is_honest(self):
        src = sa.SealedAcceptanceScope()  # no path, no config field
        v = src.scope_verdict(_state("abc123", ("s1",)), config=object())
        assert v.extent_honest is True

    def test_no_start_sha_withholds(self):
        src = sa.SealedAcceptanceScope("acceptance.toml", vcs=GitBackend(root=Path(".")))
        v = src.scope_verdict(_state("", ("s1",)), config=None)
        assert v.extent_honest is False
        assert "anchor" in v.reason
