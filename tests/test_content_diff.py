"""The content-diff witness rung — the W2→W3 climb (docs/192).

`verify`'s flagship git rung is W2 PRESENCE ("a commit touched file X"); the
`content_diff` driver climbs to W3 CONTENT ("X's content is the RIGHT value"). These
tests pin the three load-bearing properties:

  1. **The floor falsifier (security-load-bearing).** A content match against an
     agent/plan-supplied (`inline:`/`plan:`) gold is ATTESTED but at the AGENT_AUTHORED
     floor, and `believe_under_floor` REFUSES to believe it — the agent cannot grade its
     own homework (docs/192 §4 row C). This is the whole reason the rung is sound.
  2. **The sound climb.** A `sha256:` digest invariant (row A) the agent cannot
     satisfy-by-construction → ATTESTED @ OS_RECORDED, believed; a mismatch → REFUTED
     (the "changed but WRONG" disconfirmation, distinct from "no signal").
  3. **Fail-safe, never fail-open.** A missing blob / unreadable gold / unparseable
     subject → NO_SIGNAL, never a fabricated REFUTE that would fail an honest commit.

The git-backed tests build a real temp repo (so `git cat-file` reads genuine blobs);
the fail-safe tests poison `subprocess.run` so they never spawn. The
CONFORMANCE-not-correctness boundary is explicit (test at the bottom): a byte match
proves byte-identity to a fixed value, never that the content is semantically right.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from dos.drivers.content_diff import ContentDiffEvidenceSource, _parse_subject
from dos.evidence import Accountability, EvidenceStance, believe_under_floor


# ---------------------------------------------------------------------------
# A real temp git repo (the test_commit_audit._init_repo idiom).
# ---------------------------------------------------------------------------


def _git_ok() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


gitmark = pytest.mark.skipif(not _git_ok(), reason="git not available")


def _init_repo(d: Path, *, path: str = "data.txt", content: bytes = b"the right value\n") -> str:
    """Init a repo with one committed file; return its HEAD sha."""
    def g(*a):
        subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True)
    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    g("config", "commit.gpgsign", "false")
    fp = d / path
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_bytes(content)  # write_bytes, never write_text — no CRLF surprise
    g("add", path)
    g("commit", "-qm", "initial")
    sha = subprocess.run(
        ["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    return sha


def _src(tmp: Path) -> ContentDiffEvidenceSource:
    return ContentDiffEvidenceSource(cwd=str(tmp))


# ---------------------------------------------------------------------------
# 1. The floor falsifier — the agent cannot grade its own homework.
# ---------------------------------------------------------------------------


@gitmark
def test_inline_gold_that_MATCHES_is_attested_but_at_the_floor(tmp_path: Path):
    """A plan/inline gold equal to the blob bytes ATTESTs — but at AGENT_AUTHORED, so
    `believe_under_floor` refuses it. The demotion that makes the rung sound.

    (Newline-free content: `_parse_subject` strips surrounding whitespace from the
    gold-ref, so an `inline:` gold can't carry a trailing newline — fine, since inline
    is the forgeable floor and exists only to prove the demotion.)"""
    content = b"the right value"
    sha = _init_repo(tmp_path, content=content)
    # the inline gold is byte-equal to the committed blob
    facts = _src(tmp_path).gather(f"data.txt@{sha}#inline:the right value", None)
    assert facts.stance is EvidenceStance.ATTESTED           # the bytes DO match
    assert facts.accountability is Accountability.AGENT_AUTHORED  # …but the gold is forgeable
    # the floor refuses to believe it — the agent graded its own homework
    assert believe_under_floor((facts,)).believe is False


@gitmark
def test_plan_gold_is_also_the_floor(tmp_path: Path):
    content = b"v1"
    sha = _init_repo(tmp_path, content=content)
    facts = _src(tmp_path).gather(f"data.txt@{sha}#plan:v1", None)
    assert facts.stance is EvidenceStance.ATTESTED
    assert facts.accountability is Accountability.AGENT_AUTHORED
    assert believe_under_floor((facts,)).believe is False


# ---------------------------------------------------------------------------
# 2. The sound climb — a sha256 invariant the agent can't satisfy-by-construction.
# ---------------------------------------------------------------------------


@gitmark
def test_correct_sha256_gold_is_believed_at_os_recorded(tmp_path: Path):
    content = b"the right value\n"
    sha = _init_repo(tmp_path, content=content)
    digest = hashlib.sha256(content).hexdigest()
    facts = _src(tmp_path).gather(f"data.txt@{sha}#sha256:{digest}", None)
    assert facts.stance is EvidenceStance.ATTESTED
    assert facts.accountability is Accountability.OS_RECORDED
    b = believe_under_floor((facts,))
    assert b.believe is True and b.refuted is False


@gitmark
def test_wrong_sha256_gold_is_refuted(tmp_path: Path):
    """The W2→W3 value: 'the file changed, but to the WRONG content' — a positive
    disconfirmation an accountable witness made, not 'no signal'."""
    sha = _init_repo(tmp_path, content=b"the right value\n")
    facts = _src(tmp_path).gather(f"data.txt@{sha}#sha256:{'0' * 64}", None)
    assert facts.stance is EvidenceStance.REFUTED
    b = believe_under_floor((facts,))
    assert b.believe is False and b.refuted is True


@gitmark
def test_malformed_sha256_gold_is_no_signal(tmp_path: Path):
    sha = _init_repo(tmp_path)
    facts = _src(tmp_path).gather(f"data.txt@{sha}#sha256:nothex", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL
    assert facts.reachable is False


# ---------------------------------------------------------------------------
# 2b. The abstract third-party validation boundary (`source:`).
# ---------------------------------------------------------------------------


@gitmark
def test_source_gold_trusts_the_boundary_rung(tmp_path: Path, monkeypatch):
    """`source:<name>:<subj>` resolves a host-wired validator and TRUSTS the rung it
    declares (the operator steer). A THIRD_PARTY validator exposing `gold_bytes` whose
    bytes match → ATTESTED and BELIEVED. The derived rung is min(blob, gold): the blob
    is OS_RECORDED (git-authored) and the gold THIRD_PARTY, so the derivation lands at
    OS_RECORDED — both operands are non-forgeable, so it is believed (the value of the
    THIRD_PARTY gold is that it is a non-forgeable operand, not that it raises the rung
    above the blob's)."""
    content = b"the right value\n"
    sha = _init_repo(tmp_path, content=content)

    class _GoldValidator:
        name = "fake_gold"
        accountability = Accountability.THIRD_PARTY

        def gold_bytes(self, subject):
            return content  # the gold the third party vouches for

        def gather(self, subject, config):  # Protocol conformance, unused here
            raise AssertionError("gold_bytes path should be taken")

    import dos.drivers.content_diff as cd
    monkeypatch.setattr(cd, "resolve_evidence_source", lambda n: _GoldValidator())

    facts = _src(tmp_path).gather(f"data.txt@{sha}#source:fake_gold:any", None)
    assert facts.stance is EvidenceStance.ATTESTED
    # min(OS_RECORDED blob, THIRD_PARTY gold) == OS_RECORDED — both non-forgeable → believed
    assert facts.accountability is Accountability.OS_RECORDED
    assert believe_under_floor((facts,)).believe is True


@gitmark
def test_source_gold_forgeable_validator_is_capped_at_floor(tmp_path: Path, monkeypatch):
    """If the host-wired gold validator declares the FORGEABLE floor, the derived rung is
    capped there and the floor refuses belief — a host cannot launder an agent-authored
    gold up by routing it through `source:`."""
    content = b"the right value\n"
    sha = _init_repo(tmp_path, content=content)

    class _ForgeableGold:
        name = "weak_gold"
        accountability = Accountability.AGENT_AUTHORED  # the host honestly declares the floor

        def gold_bytes(self, subject):
            return content

    import dos.drivers.content_diff as cd
    monkeypatch.setattr(cd, "resolve_evidence_source", lambda n: _ForgeableGold())

    facts = _src(tmp_path).gather(f"data.txt@{sha}#source:weak_gold:any", None)
    assert facts.stance is EvidenceStance.ATTESTED
    assert facts.accountability is Accountability.AGENT_AUTHORED
    assert believe_under_floor((facts,)).believe is False


@gitmark
def test_source_gold_passthrough_uses_validator_stance(tmp_path: Path, monkeypatch):
    """A validator with NO `gold_bytes` answers 'is it right?' itself; its REFUTED stance
    rides through as the content-diff verdict (the abstract pass-through)."""
    sha = _init_repo(tmp_path)
    from dos.evidence import EvidenceFacts

    class _StanceValidator:
        name = "stance_gold"
        accountability = Accountability.THIRD_PARTY

        def gather(self, subject, config):
            return EvidenceFacts.refute(self.name, self.accountability, subject,
                                        detail="the third party says no")

    import dos.drivers.content_diff as cd
    monkeypatch.setattr(cd, "resolve_evidence_source", lambda n: _StanceValidator())

    facts = _src(tmp_path).gather(f"data.txt@{sha}#source:stance_gold:x", None)
    assert facts.stance is EvidenceStance.REFUTED
    assert facts.accountability is Accountability.THIRD_PARTY


@gitmark
def test_unknown_source_validator_is_no_signal(tmp_path: Path):
    sha = _init_repo(tmp_path)
    # no such validator registered → resolve raises → no_signal (never a fabricated gold)
    facts = _src(tmp_path).gather(f"data.txt@{sha}#source:does_not_exist:x", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL
    assert facts.reachable is False


# ---------------------------------------------------------------------------
# 3. Fail-safe, never fail-open.
# ---------------------------------------------------------------------------


@gitmark
def test_missing_path_is_no_signal_not_refute(tmp_path: Path):
    """A path that isn't a blob at <sha> → NO_SIGNAL, never a fabricated REFUTE that
    would falsely fail an honest commit."""
    sha = _init_repo(tmp_path)
    facts = _src(tmp_path).gather(f"no_such_file.txt@{sha}#inline:whatever", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL
    assert facts.reachable is False


def test_unparseable_subject_is_no_signal():
    src = ContentDiffEvidenceSource()
    for bad in ("", "no-hash-here", "  ", "@sha#", "path-only@sha"):
        facts = src.gather(bad, None)
        assert facts.stance is EvidenceStance.NO_SIGNAL, bad
        assert facts.reachable is False, bad


def test_git_failure_degrades_to_no_signal(monkeypatch):
    """Poison `subprocess.run` to raise — a missing git / OS error → NO_SIGNAL, never a
    crash and never an attestation."""
    def _boom(*a, **k):
        raise FileNotFoundError("git missing")
    monkeypatch.setattr(subprocess, "run", _boom)
    facts = ContentDiffEvidenceSource().gather("data.txt@HEAD#inline:x", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL
    assert facts.reachable is False


# ---------------------------------------------------------------------------
# The subject grammar (pure).
# ---------------------------------------------------------------------------


def test_parse_subject_defaults_sha_to_head():
    assert _parse_subject("a.txt#inline:v") == ("a.txt", "HEAD", "inline:v")


def test_parse_subject_full():
    assert _parse_subject("d/a.txt@abc123#sha256:ff") == ("d/a.txt", "abc123", "sha256:ff")


def test_parse_subject_keeps_hash_in_goldref():
    # only the FIRST '#' splits; a gold value may contain '#'
    assert _parse_subject("a@HEAD#inline:a#b") == ("a", "HEAD", "inline:a#b")


def test_parse_subject_rejects_empty_parts():
    assert _parse_subject("") is None
    assert _parse_subject("a.txt") is None          # no gold-ref
    assert _parse_subject("a.txt@sha#") is None      # empty gold-ref
    assert _parse_subject("@sha#inline:v") is None   # empty path


# ---------------------------------------------------------------------------
# Conformance, not correctness (mirror test_os_acceptance's boundary test).
# ---------------------------------------------------------------------------


@gitmark
def test_match_relabels_the_source_never_claims_semantic_correctness(tmp_path: Path):
    """A byte/digest match proves byte-identity to a FIXED value — not that the content
    is semantically right. The verdict is about provenance + presence, never meaning."""
    content = b"def add(a, b): return a - b\n"  # semantically WRONG, byte-fixed
    sha = _init_repo(tmp_path, path="app.py", content=content)
    digest = hashlib.sha256(content).hexdigest()
    facts = _src(tmp_path).gather(f"app.py@{sha}#sha256:{digest}", None)
    # It ATTESTs (the bytes ARE the fixed value) — it makes NO claim the function is correct.
    assert facts.stance is EvidenceStance.ATTESTED
    assert "==" in facts.detail  # the verdict speaks of byte-equality, not correctness
