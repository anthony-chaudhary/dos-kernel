"""dos.drivers.fs_artifact — the on-disk artifact read-back witness (docs/384).

The last roadmap backend of the witness-spectrum arc. These tests build real files
in tmp_path (no monkeypatching of the read — the boundary is exercised) and pin the
load-bearing contract:

  * EXISTENCE is the forgeable floor: a present file ATTESTS at AGENT_AUTHORED and
    does NOT grant belief (the agent can `touch` it);
  * ABSENCE is an accountable refute: a missing artifact REFUTES at OS_RECORDED
    (refuted=True) — the asymmetry that makes the witness useful with no gold;
  * a `sha256:` gold is preimage-sound: a MATCH climbs to OS_RECORDED and grants
    belief (via `derived_witness`), a MISMATCH REFUTES at OS_RECORDED;
  * a `size:` gold stays on the floor (forgeable — recorded, never believed);
  * every cannot-tell failure (a directory, a malformed gold/subject) degrades to
    NO_SIGNAL — never a fabricated attest or refute.
"""

from __future__ import annotations

import hashlib

from dos.drivers.fs_artifact import FsArtifactEvidenceSource, _parse_subject
from dos.evidence import Accountability, EvidenceStance, believe_under_floor


def _src() -> FsArtifactEvidenceSource:
    return FsArtifactEvidenceSource()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Subject parsing.
# ---------------------------------------------------------------------------


def test_parse_modes():
    assert _parse_subject("dist/app.tar.gz") == ("dist/app.tar.gz", "exists", "")
    assert _parse_subject("a.bin#sha256:abc") == ("a.bin", "sha256", "abc")
    assert _parse_subject("a.bin#size:42") == ("a.bin", "size", "42")


def test_parse_rejects_bad_subjects():
    assert _parse_subject("") is None
    assert _parse_subject("a.bin#frob:1") is None      # unknown gold kind
    assert _parse_subject("a.bin#sha256:") is None      # empty gold
    assert _parse_subject("#sha256:abc") is None        # empty path


# ---------------------------------------------------------------------------
# Existence — presence is the floor, absence is an accountable refute.
# ---------------------------------------------------------------------------


def test_existing_file_attests_on_the_floor_and_is_not_believed(tmp_path):
    p = tmp_path / "app.tar.gz"
    p.write_bytes(b"payload")
    facts = _src().gather(str(p), None)
    assert facts.stance is EvidenceStance.ATTESTED
    assert facts.accountability is Accountability.AGENT_AUTHORED   # forgeable floor
    assert believe_under_floor((facts,)).believe is False          # touch is not proof


def test_absent_file_refutes_at_os_recorded(tmp_path):
    facts = _src().gather(str(tmp_path / "never-built.tar.gz"), None)
    assert facts.stance is EvidenceStance.REFUTED
    assert facts.accountability is Accountability.OS_RECORDED
    assert believe_under_floor((facts,)).refuted is True           # "you claimed X — it is absent"


# ---------------------------------------------------------------------------
# sha256 — preimage-sound: a match climbs to OS_RECORDED and grants belief.
# ---------------------------------------------------------------------------


def test_sha256_match_attests_at_os_recorded_and_grants_belief(tmp_path):
    p = tmp_path / "app.bin"
    data = b"the real artifact bytes"
    p.write_bytes(data)
    facts = _src().gather(f"{p}#sha256:{_sha256(data)}", None)
    assert facts.stance is EvidenceStance.ATTESTED
    assert facts.accountability is Accountability.OS_RECORDED
    assert believe_under_floor((facts,)).believe is True


def test_sha256_mismatch_refutes_at_os_recorded(tmp_path):
    p = tmp_path / "app.bin"
    p.write_bytes(b"WRONG content")
    facts = _src().gather(f"{p}#sha256:{_sha256(b'the intended content')}", None)
    assert facts.stance is EvidenceStance.REFUTED
    assert facts.accountability is Accountability.OS_RECORDED
    assert believe_under_floor((facts,)).refuted is True


def test_sha256_on_absent_file_refutes(tmp_path):
    facts = _src().gather(f"{tmp_path / 'missing.bin'}#sha256:{_sha256(b'x')}", None)
    assert facts.stance is EvidenceStance.REFUTED
    assert believe_under_floor((facts,)).refuted is True


def test_sha256_malformed_gold_is_no_signal(tmp_path):
    p = tmp_path / "app.bin"
    p.write_bytes(b"data")
    assert _src().gather(f"{p}#sha256:not-64-hex", None).stance is EvidenceStance.NO_SIGNAL


# ---------------------------------------------------------------------------
# size — a forgeable-floor convenience: recorded, never believed.
# ---------------------------------------------------------------------------


def test_size_match_stays_on_the_floor(tmp_path):
    p = tmp_path / "app.bin"
    p.write_bytes(b"12345")  # 5 bytes
    facts = _src().gather(f"{p}#size:5", None)
    assert facts.stance is EvidenceStance.ATTESTED
    assert facts.accountability is Accountability.AGENT_AUTHORED
    v = believe_under_floor((facts,))
    assert v.believe is False and v.refuted is False   # size is forgeable — never proof


def test_size_mismatch_does_not_redden_on_its_own(tmp_path):
    p = tmp_path / "app.bin"
    p.write_bytes(b"12345")
    facts = _src().gather(f"{p}#size:999", None)
    assert facts.stance is EvidenceStance.REFUTED
    # a forgeable-floor refute is recorded but, by the floor discipline, does not set
    # refuted=True on its own (cf. believe_under_floor — the floor cuts both ways).
    assert believe_under_floor((facts,)).refuted is False


def test_size_malformed_gold_is_no_signal(tmp_path):
    p = tmp_path / "app.bin"
    p.write_bytes(b"data")
    assert _src().gather(f"{p}#size:notanint", None).stance is EvidenceStance.NO_SIGNAL


# ---------------------------------------------------------------------------
# Fail-safe — a non-found-but-unreadable path is cannot-tell, never a refute.
# ---------------------------------------------------------------------------


def test_directory_is_no_signal_not_a_refute(tmp_path):
    # A path that EXISTS but is not a readable artifact (a directory): reading its
    # bytes raises IsADirectoryError/PermissionError — cannot tell, NOT a fabricated
    # refute (the path is reachable; we just cannot read it as a file).
    d = tmp_path / "adir"
    d.mkdir()
    facts = _src().gather(f"{d}#sha256:{_sha256(b'x')}", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL


def test_unparseable_subject_is_no_signal():
    assert _src().gather("", None).stance is EvidenceStance.NO_SIGNAL
    assert _src().gather("a.bin#frob:1", None).stance is EvidenceStance.NO_SIGNAL


# ---------------------------------------------------------------------------
# Registry — fs_artifact is discoverable by name (the entry-point wiring).
# ---------------------------------------------------------------------------


def test_registered_and_resolvable_at_os_recorded_ceiling():
    from dos.evidence import resolve_evidence_source
    src = resolve_evidence_source("fs_artifact")
    assert src.name == "fs_artifact"
    assert src.accountability is Accountability.OS_RECORDED   # the ceiling it can mint
