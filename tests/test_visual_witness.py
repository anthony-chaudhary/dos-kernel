"""dos.drivers.visual_witness — the rendered-screen / screenshot witness (docs/384).

The deterministic (oracle) rung of visual evidence: a perceptual-hash distance under a
tolerance. These tests build real PNM images in tmp_path (no image library needed) and
pin: the PNM decode (P5/P6 binary, P2/P3 ascii), the dHash, the ATTEST/REFUTE/NO_SIGNAL
branches, the tolerance knob, and — the soundness guard mirroring state_diff — that the
source REFUSES an AGENT_AUTHORED rung (an agent-pasted screenshot is not a witness).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dos.drivers.visual_witness import (
    _HASH_SIZE,
    VisualWitnessEvidenceSource,
    _dhash,
    _decode_pnm,
    _hash_image_file,
    _parse_subject,
)
from dos.evidence import Accountability, EvidenceStance, believe_under_floor

_HEX = _HASH_SIZE * _HASH_SIZE // 4  # hex chars in a hash (64 bits → 16)


# ---------------------------------------------------------------------------
# Image builders (PNM — no Pillow).
# ---------------------------------------------------------------------------


def _write_pgm_p5(path: Path, w: int, h: int, pixels: list[int]) -> None:
    path.write_bytes(f"P5\n{w} {h}\n255\n".encode("ascii") + bytes(pixels))


def _grad(w: int, h: int, *, invert: bool = False) -> list[int]:
    """A left→right luminance gradient (or its inverse) — clean, distinguishable dHash."""
    out = []
    for _y in range(h):
        for x in range(w):
            v = int(x / (w - 1) * 255) if w > 1 else 0
            out.append(255 - v if invert else v)
    return out


def _src() -> VisualWitnessEvidenceSource:
    return VisualWitnessEvidenceSource()


# ---------------------------------------------------------------------------
# PNM decode — the four formats.
# ---------------------------------------------------------------------------


def test_decode_p5_binary_grayscale():
    data = b"P5\n2 2\n255\n" + bytes([0, 10, 20, 30])
    assert _decode_pnm(data) == (2, 2, [0, 10, 20, 30])


def test_decode_p6_binary_rgb_reduces_to_luma():
    # a single white pixel → luma ~255; a single black pixel → 0
    data = b"P6\n2 1\n255\n" + bytes([255, 255, 255, 0, 0, 0])
    w, h, gray = _decode_pnm(data)
    assert (w, h) == (2, 1)
    assert gray[0] >= 254 and gray[1] == 0


def test_decode_p2_ascii_grayscale():
    data = b"P2\n2 2\n255\n0 10\n20 30\n"
    assert _decode_pnm(data) == (2, 2, [0, 10, 20, 30])


def test_decode_handles_comments_and_rejects_16bit():
    data = b"P5\n# a comment\n2 1\n255\n" + bytes([5, 9])
    assert _decode_pnm(data) == (2, 1, [5, 9])
    assert _decode_pnm(b"P5\n2 1\n65535\n\x00\x01\x00\x02") is None  # 16-bit unsupported
    assert _decode_pnm(b"not an image") is None


# ---------------------------------------------------------------------------
# dHash — identical vs inverted.
# ---------------------------------------------------------------------------


def test_dhash_identical_is_distance_zero():
    g = _grad(16, 16)
    assert _dhash(16, 16, g) == _dhash(16, 16, g)


def test_dhash_gradient_vs_inverse_is_far():
    a = _dhash(16, 16, _grad(16, 16))
    b = _dhash(16, 16, _grad(16, 16, invert=True))
    assert bin(a ^ b).count("1") > _HASH_SIZE  # far apart (monotonic vs anti-monotonic)


# ---------------------------------------------------------------------------
# The gather stance branches.
# ---------------------------------------------------------------------------


def test_identical_images_attest_and_grant_belief(tmp_path):
    a = tmp_path / "shot.ppm"
    ref = tmp_path / "baseline.ppm"
    _write_pgm_p5(a, 16, 16, _grad(16, 16))
    _write_pgm_p5(ref, 16, 16, _grad(16, 16))
    facts = _src().gather(f"{a}#ref:{ref}", None)
    assert facts.stance is EvidenceStance.ATTESTED
    assert facts.accountability is Accountability.OS_RECORDED
    assert believe_under_floor((facts,)).believe is True


def test_different_images_refute(tmp_path):
    a = tmp_path / "shot.ppm"
    ref = tmp_path / "baseline.ppm"
    _write_pgm_p5(a, 16, 16, _grad(16, 16))
    _write_pgm_p5(ref, 16, 16, _grad(16, 16, invert=True))
    facts = _src().gather(f"{a}#ref:{ref}", None)
    assert facts.stance is EvidenceStance.REFUTED
    assert believe_under_floor((facts,)).refuted is True


def test_phash_mode_matches_self(tmp_path):
    a = tmp_path / "shot.ppm"
    _write_pgm_p5(a, 16, 16, _grad(16, 16))
    h = _hash_image_file(str(a))
    hexh = format(h, f"0{_HEX}x")
    facts = _src().gather(f"{a}#phash:{hexh}", None)
    assert facts.stance is EvidenceStance.ATTESTED


def test_tolerance_knob(tmp_path):
    a = tmp_path / "shot.ppm"
    _write_pgm_p5(a, 16, 16, _grad(16, 16))
    h = _hash_image_file(str(a))
    gold = h ^ 0b110  # 2 bits different → Hamming distance 2 from the image
    hexg = format(gold, f"0{_HEX}x")
    assert _src().gather(f"{a}#phash:{hexg}/1", None).stance is EvidenceStance.REFUTED   # 2 > 1
    assert _src().gather(f"{a}#phash:{hexg}/3", None).stance is EvidenceStance.ATTESTED  # 2 <= 3


# ---------------------------------------------------------------------------
# Fail-safe + the soundness guard.
# ---------------------------------------------------------------------------


def test_unreadable_image_is_no_signal(tmp_path):
    ref = tmp_path / "baseline.ppm"
    _write_pgm_p5(ref, 16, 16, _grad(16, 16))
    facts = _src().gather(f"{tmp_path / 'missing.ppm'}#ref:{ref}", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL


def test_unreadable_reference_is_no_signal(tmp_path):
    a = tmp_path / "shot.ppm"
    _write_pgm_p5(a, 16, 16, _grad(16, 16))
    facts = _src().gather(f"{a}#ref:{tmp_path / 'nope.ppm'}", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL


def test_malformed_subject_is_no_signal():
    assert _src().gather("no-hash-marker", None).stance is EvidenceStance.NO_SIGNAL
    assert _src().gather("img.ppm#bogus:x", None).stance is EvidenceStance.NO_SIGNAL
    assert _parse_subject("img.ppm#ref:") is None


def test_refuses_agent_authored_rung():
    """The state_diff soundness guard: a visual witness over an agent-authored image is
    not a witness — actor==witness — so the constructor refuses the forgeable floor."""
    with pytest.raises(ValueError):
        VisualWitnessEvidenceSource(accountability=Accountability.AGENT_AUTHORED)
