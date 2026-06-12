"""Contract tests for `dos.drivers._effect_gate` (docs/305 Phase 1).

The shared core behind the OpenAI Agents SDK / CrewAI guardrail adapters: the
claim kinds against REAL tmp git repos (the boundary I/O is the module's whole
job, so the tests exercise it for real), the fold table, extractor injection,
and the fail-to-abstain posture. No agent framework is needed anywhere here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dos.drivers._effect_gate import (
    Claim,
    CommitClaim,
    EffectGate,
    FileClaim,
    GateOutcome,
    ShippedClaim,
)


# ---------------------------------------------------------------------------
# A real tmp git repo — the read-backs run against actual git.
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    )
    return proc.stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "ws"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    (r / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(r, "add", "seed.txt")
    _git(r, "commit", "-q", "-m", "seed")
    return r


def _commit_something(repo: Path, name: str = "work.txt") -> None:
    (repo / name).write_text("work\n", encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"land {name}")


# ---------------------------------------------------------------------------
# CommitClaim — the flagship over-claim catch.
# ---------------------------------------------------------------------------


def test_commit_overclaim_trips(repo: Path) -> None:
    gate = EffectGate(str(repo), expect=[CommitClaim()])
    v = gate.adjudicate("done! committed the fix.")
    assert v.outcome is GateOutcome.TRIPPED
    assert v.tripped
    assert "no commit beyond" in v.reason
    (row,) = v.rows
    assert row.is_refuted and row.witness == "git_ancestry"


def test_commit_actually_landed_clears(repo: Path) -> None:
    gate = EffectGate(str(repo), expect=[CommitClaim()])  # baseline = HEAD now
    _commit_something(repo)
    v = gate.adjudicate("done! committed the fix.")
    assert v.outcome is GateOutcome.CLEAR
    assert not v.tripped
    (row,) = v.rows
    assert row.is_confirmed


def test_commit_baseline_pinned_at_construction(repo: Path) -> None:
    # Work landed BEFORE the gate was built must not satisfy the claim.
    _commit_something(repo, "early.txt")
    gate = EffectGate(str(repo), expect=[CommitClaim()])
    v = gate.adjudicate("done")
    assert v.outcome is GateOutcome.TRIPPED


def test_commit_explicit_baseline(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _commit_something(repo)
    v = EffectGate(str(repo), expect=[CommitClaim(baseline=base)]).adjudicate("done")
    assert v.outcome is GateOutcome.CLEAR


def test_not_a_git_repo_abstains_never_trips(tmp_path: Path) -> None:
    bare = tmp_path / "no-repo"
    bare.mkdir()
    gate = EffectGate(str(bare), expect=[CommitClaim()])
    v = gate.adjudicate("done")
    assert v.outcome is GateOutcome.ABSTAINED
    assert not v.tripped


# ---------------------------------------------------------------------------
# FileClaim.
# ---------------------------------------------------------------------------


def test_file_absent_trips(repo: Path) -> None:
    v = EffectGate(str(repo), expect=[FileClaim("out/report.md")]).adjudicate("done")
    assert v.outcome is GateOutcome.TRIPPED
    assert "absent" in v.rows[0].reason or "absent" in v.reason


def test_file_present_clears(repo: Path) -> None:
    (repo / "report.md").write_text("hi\n", encoding="utf-8")
    v = EffectGate(str(repo), expect=[FileClaim("report.md")]).adjudicate("done")
    assert v.outcome is GateOutcome.CLEAR


def test_file_empty_with_non_empty_required_trips(repo: Path) -> None:
    (repo / "empty.md").write_text("", encoding="utf-8")
    v = EffectGate(
        str(repo), expect=[FileClaim("empty.md", non_empty=True)]
    ).adjudicate("done")
    assert v.outcome is GateOutcome.TRIPPED


def test_file_absolute_path(repo: Path, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.txt"
    target.write_text("x", encoding="utf-8")
    v = EffectGate(str(repo), expect=[FileClaim(str(target))]).adjudicate("done")
    assert v.outcome is GateOutcome.CLEAR


# ---------------------------------------------------------------------------
# ShippedClaim — the oracle rung, no plan registry anywhere (the litmus).
# ---------------------------------------------------------------------------


def test_shipped_claim_unshipped_trips(repo: Path) -> None:
    v = EffectGate(str(repo), expect=[ShippedClaim("AUTH", "AUTH9")]).adjudicate("done")
    assert v.outcome is GateOutcome.TRIPPED
    assert "no git evidence" in v.reason


def test_shipped_claim_stamped_clears(repo: Path) -> None:
    (repo / "auth.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "auth.txt")
    _git(repo, "commit", "-q", "-m", "AUTH1: ship the auth phase")
    v = EffectGate(str(repo), expect=[ShippedClaim("AUTH", "AUTH1")]).adjudicate("done")
    assert v.outcome is GateOutcome.CLEAR


# ---------------------------------------------------------------------------
# The fold table.
# ---------------------------------------------------------------------------


def test_no_claims_is_no_claim(repo: Path) -> None:
    v = EffectGate(str(repo)).adjudicate("all done, trust me")
    assert v.outcome is GateOutcome.NO_CLAIM
    assert not v.tripped
    assert v.rows == ()


def test_any_refuted_wins_over_confirmed(repo: Path) -> None:
    (repo / "present.md").write_text("x", encoding="utf-8")
    gate = EffectGate(
        str(repo), expect=[FileClaim("present.md"), FileClaim("missing.md")]
    )
    v = gate.adjudicate("done")
    assert v.outcome is GateOutcome.TRIPPED
    assert len(v.rows) == 2


def test_confirmed_plus_unwitnessed_abstains(repo: Path, tmp_path: Path) -> None:
    # One confirmable file claim + one commit claim whose baseline could not be
    # captured (gate built against a non-repo, then pointed at it) — the fold
    # must land ABSTAINED, not CLEAR: we could not witness everything.
    (repo / "present.md").write_text("x", encoding="utf-8")
    gate = EffectGate(
        str(repo),
        expect=[FileClaim("present.md"), CommitClaim(baseline=None)],
    )
    # Forge the unreachable read-back by blanking the pinned baseline.
    gate._expect = tuple(
        CommitClaim(baseline=None) if isinstance(c, CommitClaim) else c
        for c in gate._expect
    )
    v = gate.adjudicate("done")
    assert v.outcome is GateOutcome.ABSTAINED
    assert not v.tripped


# ---------------------------------------------------------------------------
# The extractor seam.
# ---------------------------------------------------------------------------


def test_extracted_claims_join_declared(repo: Path) -> None:
    def extract(text: str):
        assert "report.md" in text
        return [FileClaim("report.md")]

    gate = EffectGate(str(repo), extract=extract)
    v = gate.adjudicate("wrote report.md as asked")
    assert v.outcome is GateOutcome.TRIPPED  # extracted claim, absent file


def test_extractor_crash_abstains_never_trips(repo: Path) -> None:
    def extract(text: str):
        raise RuntimeError("the host parser blew up")

    gate = EffectGate(str(repo), extract=extract, expect=[CommitClaim()])
    v = gate.adjudicate("done")
    assert v.outcome is GateOutcome.ABSTAINED
    assert not v.tripped
    assert "abstained" in v.reason


def test_unknown_claim_kind_abstains(repo: Path) -> None:
    class Weird(Claim):
        pass

    v = EffectGate(str(repo), expect=[Weird()]).adjudicate("done")
    assert v.outcome is GateOutcome.ABSTAINED


# ---------------------------------------------------------------------------
# The verdict surface.
# ---------------------------------------------------------------------------


def test_to_dict_carries_rows_and_trip_bit(repo: Path) -> None:
    v = EffectGate(str(repo), expect=[CommitClaim()]).adjudicate("done")
    d = v.to_dict()
    assert d["outcome"] == "TRIPPED" and d["tripped"] is True
    assert d["rows"] and d["rows"][0]["verdict"] == "REFUTED"
    assert d["rows"][0]["narrated"] == "done"
