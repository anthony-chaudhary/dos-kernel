"""dos.drivers.visual_witness — the rendered-screen / screenshot witness (docs/384).

The headline new witness TYPE: an agent's claim "the page renders correctly / the
chart was produced / the UI is unchanged" witnessed by **pixels**, not the agent's
narration. The kernel reads a captured image, computes a perceptual hash, and compares
it to a reference (a baseline image, or a gold perceptual hash) within a
Hamming-distance tolerance.

Where this sits on the spectrum — the "various levels" made concrete (docs/384)
================================================================================

Visual evidence spans TWO rungs of adjudication, and this driver is the DETERMINISTIC
(oracle) one:

  * **Deterministic (here):** a perceptual-hash distance under a tolerance is a pure,
    repeatable ATTEST/REFUTE — the kernel decides, no model in the loop.
  * **Perceptual / semantic (NOT here):** "does this screenshot SHOW the error dialog?"
    has no canonical hash — it routes to the `dos.judges` seam (advisory,
    fail-to-abstain). An AGENT_AUTHORED screenshot can ONLY take that advisory path.

Why OS_RECORDED — and the honest contract (the `state_diff` precedent)
======================================================================

The rung mirrors `dos.drivers.state_diff` EXACTLY: a witness over a snapshot/image is
sound only when a surface the agent does NOT author produced it — a kernel-run capture
(a headless-browser screenshot, an OS screencapture), not pixels the agent pasted. So:

  * the source is `OS_RECORDED` by default and **refuses `AGENT_AUTHORED`** in its
    constructor (a visual witness over an agent-pasted screenshot is not a witness —
    actor==witness; route that to a JUDGE instead);
  * the kernel READS the image file directly (the agent did not hand us the bytes);
  * choosing a path that holds a kernel-captured image (not an agent-authored one) is
    the HOST's job — the same "the host wires the surface" contract `os_acceptance` /
    `state_diff` already live under.

The gold (the reference image or `phash:` digest) must likewise be a baseline an
independent party authored — a committed golden screenshot, an operator-recorded hash —
not a value the agent supplied (the `content_diff` gold-provenance discipline).

The image format — PNM (PPM/PGM), stdlib-decodable
==================================================

To stay dependency-free (the kernel/driver import set is thin — no Pillow/numpy), the
driver decodes the **PNM family** (`P2`/`P3` ascii, `P5`/`P6` binary — PGM grayscale
and PPM RGB), which every capture pipeline can emit (`chromium --screenshot` →
`magick png:- ppm:-`; `import -window root ppm:-`; `scrot`/`ffmpeg` → ppm). A host with
PNG-only capture converts in its capture step; the witness names the format it reads.

The perceptual hash — dHash (difference hash)
=============================================

A robust, classic perceptual hash: downscale to a 9×8 luminance grid (nearest-neighbor;
resolution- and minor-rescale-tolerant), then for each row emit one bit per adjacent
pair (is the next pixel brighter?) → 8×8 = 64 bits. Two images are "the same" iff their
hashes' Hamming distance is within the tolerance (default 5/64). dHash is stable under
recompression and small antialiasing differences — the reason an exact byte hash is the
WRONG tool for a screenshot (it false-REFUTES on a 1-pixel difference) and a perceptual
hash with a tolerance is the right middle ground.

Stance grammar (the honest, conservative mapping)
=================================================

  * image read + reference read + distance ≤ tol → **ATTESTED** (the render matches)
  * image read + reference read + distance >  tol → **REFUTED**  (a positive
        disconfirmation — "the screen does NOT look like the baseline")
  * image unreadable / unparseable / reference unreachable / bad subject → **NO_SIGNAL**
        (abstain — never a fabricated REFUTE that would falsely fail an honest render)

Shape & layering
================

A driver — it has the I/O surface the kernel forbids (reading image files). Stdlib
only. It implements the `evidence.EvidenceSource` Protocol (class-level
`name`/`accountability`; a boundary `gather`) so it drops into `gather_evidence`, the
belief fold, and `dos witness visual_witness <subject>`. It imports the kernel; the
kernel never imports it. Advisory: it reports a read-back; it mutates nothing.
"""

from __future__ import annotations

import argparse
import json

# Imports the kernel — never the other way round (the driver rule).
from dos.evidence import Accountability, EvidenceFacts, believe_under_floor

_DEFAULT_TOLERANCE = 5  # max Hamming distance (of 64 bits) to still count as a match
_HASH_SIZE = 8          # dHash grid is (_HASH_SIZE+1) x _HASH_SIZE → _HASH_SIZE^2 bits


class VisualWitnessEvidenceSource:
    """An `evidence.EvidenceSource`: witness whether a captured image matches a reference.

    `name` is `visual_witness`. `accountability` is OS_RECORDED by default and the
    constructor REFUSES `AGENT_AUTHORED` (the `state_diff` soundness guard — a witness
    over an agent-authored image is not a witness). The `subject` is the comparison
    (see the module docstring / `_parse_subject`). `config` is accepted for Protocol
    conformance and unused.
    """

    name = "visual_witness"

    def __init__(self, *, accountability: Accountability = Accountability.OS_RECORDED) -> None:
        if accountability.is_agent_authored:
            raise ValueError(
                "visual_witness requires a non-forgeable rung (OS_RECORDED/THIRD_PARTY); "
                "an agent-authored screenshot is not a witness — route it to a JUDGE"
            )
        self.accountability = accountability

    def gather(self, subject: str, config: object) -> EvidenceFacts:
        """Parse the subject, read the image(s), compare perceptual hashes. Never raises:
        every failure degrades to NO_SIGNAL (the `os_acceptance.gather` discipline)."""
        parsed = _parse_subject(subject)
        if parsed is None:
            return EvidenceFacts.no_signal(
                self.name, self.accountability, subject or "",
                detail=(
                    "un-parseable subject — expected '<image>#ref:<reference>[/tol]' or "
                    "'<image>#phash:<hex>[/tol]' — nothing to witness"
                ),
            )
        image_path, mode, gold, tol = parsed

        img_hash = _hash_image_file(image_path)
        if img_hash is None:
            return EvidenceFacts.no_signal(
                self.name, self.accountability, image_path,
                detail=f"could not read/decode image {image_path!r} (need a PNM/PPM/PGM) — no signal",
            )

        if mode == "phash":
            gold_hash = _parse_hex_hash(gold)
            if gold_hash is None:
                return EvidenceFacts.no_signal(
                    self.name, self.accountability, image_path,
                    detail=f"malformed gold phash {gold!r} (need {_HASH_SIZE * _HASH_SIZE // 4} hex chars) — no signal",
                )
            gold_desc = f"phash {gold[:12]}…"
        else:  # mode == "ref"
            gold_hash = _hash_image_file(gold)
            if gold_hash is None:
                return EvidenceFacts.no_signal(
                    self.name, self.accountability, image_path,
                    detail=f"could not read/decode reference image {gold!r} — no signal",
                )
            gold_desc = f"reference {gold!r}"

        dist = _hamming(img_hash, gold_hash)
        subj = f"{image_path}#{mode}"
        if dist <= tol:
            return EvidenceFacts.attest(
                self.name, self.accountability, subj,
                detail=f"perceptual hash matches {gold_desc} (distance {dist} ≤ tol {tol} of {_HASH_SIZE*_HASH_SIZE})",
            )
        return EvidenceFacts.refute(
            self.name, self.accountability, subj,
            detail=f"perceptual hash DIFFERS from {gold_desc} (distance {dist} > tol {tol}) — the render does not match",
        )


# ---------------------------------------------------------------------------
# Pure helpers — subject grammar, PNM decode, perceptual hash. No I/O except the
# file read in `_hash_image_file` (the boundary).
# ---------------------------------------------------------------------------


def _parse_subject(subject: str) -> "tuple[str, str, str, int] | None":
    """`'<image>#<mode>:<gold>[/<tol>]'` → `(image_path, mode, gold, tol)`.

    `mode` is `ref` (gold is a reference image path) or `phash` (gold is a hex hash).
    `tol` defaults to `_DEFAULT_TOLERANCE`. Returns None on any shape error (empty
    image path, unknown mode, bad tol) — never a universal match."""
    s = (subject or "").strip()
    if not s or "#" not in s:
        return None
    image_path, _, spec = s.partition("#")
    image_path = image_path.strip()
    spec = spec.strip()
    if not image_path or ":" not in spec:
        return None
    mode, _, rest = spec.partition(":")
    mode = mode.lower().strip()
    if mode not in ("ref", "phash"):
        return None
    # an optional '/<tol>' suffix. A ref path could contain '/', so split on the LAST '/'
    # only when the tail is all digits (a tolerance); otherwise the whole rest is the gold.
    gold, tol = rest, _DEFAULT_TOLERANCE
    if "/" in rest:
        head, _, tail = rest.rpartition("/")
        if tail.isdigit() and head:
            gold, tol = head, int(tail)
    gold = gold.strip()
    if not gold:
        return None
    return image_path, mode, gold, tol


def _parse_hex_hash(s: str) -> "int | None":
    h = (s or "").strip().lower()
    want = _HASH_SIZE * _HASH_SIZE // 4  # hex chars for the bit-width (64 bits → 16)
    if len(h) != want or any(c not in "0123456789abcdef" for c in h):
        return None
    return int(h, 16)


def _hash_image_file(path: str) -> "int | None":
    """Read a PNM image from disk and return its dHash (an int), or None on any failure.

    The ONE file read (the boundary). The kernel opens the file — the agent did not hand
    us the bytes — which is what makes the read-back independent (the `state_diff`
    snapshot-reader contract)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    decoded = _decode_pnm(data)
    if decoded is None:
        return None
    w, h, gray = decoded
    return _dhash(w, h, gray)


def _decode_pnm(data: bytes) -> "tuple[int, int, list[int]] | None":
    """Decode a PNM image (P2/P3 ascii, P5/P6 binary; PGM gray / PPM RGB) to
    `(width, height, gray_pixels)`. RGB is reduced to luma. None on any malformed input.
    maxval>255 (16-bit) is not supported (capture pipelines emit 8-bit); returns None."""
    if len(data) < 2 or data[:2] not in (b"P2", b"P3", b"P5", b"P6"):
        return None
    magic = data[:2]
    rgb = magic in (b"P3", b"P6")
    binary = magic in (b"P5", b"P6")
    pos = 2

    def skip_ws_comments(p: int) -> int:
        while p < len(data):
            c = data[p:p + 1]
            if c.isspace():
                p += 1
                continue
            if c == b"#":
                while p < len(data) and data[p:p + 1] not in (b"\n", b"\r"):
                    p += 1
                continue
            break
        return p

    def read_token(p: int) -> "tuple[bytes, int]":
        p = skip_ws_comments(p)
        start = p
        while p < len(data) and not data[p:p + 1].isspace() and data[p:p + 1] != b"#":
            p += 1
        return data[start:p], p

    header: list[int] = []
    for _ in range(3):  # width, height, maxval
        tok, pos = read_token(pos)
        if not tok:
            return None
        try:
            header.append(int(tok))
        except ValueError:
            return None
    w, h, maxval = header
    if w <= 0 or h <= 0 or maxval <= 0 or maxval > 255:
        return None
    n = w * h
    per = 3 if rgb else 1

    if binary:
        # exactly one whitespace byte separates maxval from the raster
        if pos < len(data) and data[pos:pos + 1].isspace():
            pos += 1
        raster = data[pos:pos + n * per]
        if len(raster) < n * per:
            return None
        if rgb:
            gray = [
                (299 * raster[i * 3] + 587 * raster[i * 3 + 1] + 114 * raster[i * 3 + 2]) // 1000
                for i in range(n)
            ]
        else:
            gray = list(raster[:n])
        return w, h, gray

    # ascii (P2/P3)
    vals: list[int] = []
    need = n * per
    while len(vals) < need:
        tok, pos = read_token(pos)
        if not tok:
            break
        try:
            vals.append(int(tok))
        except ValueError:
            break
    if len(vals) < need:
        return None
    if rgb:
        gray = [(299 * vals[i * 3] + 587 * vals[i * 3 + 1] + 114 * vals[i * 3 + 2]) // 1000 for i in range(n)]
    else:
        gray = vals[:n]
    return w, h, gray


def _dhash(w: int, h: int, gray: "list[int]", size: int = _HASH_SIZE) -> int:
    """Difference hash: downscale to (size+1)×size luminance (nearest-neighbor), then
    emit one bit per horizontally-adjacent pair (next brighter than current?). Returns a
    `size*size`-bit int. PURE."""
    cols = size + 1
    rows = size
    bits = 0
    for ry in range(rows):
        sy = min(ry * h // rows, h - 1)
        prev: "int | None" = None
        for rx in range(cols):
            sx = min(rx * w // cols, w - 1)
            val = gray[sy * w + sx]
            if prev is not None:
                bits = (bits << 1) | (1 if val > prev else 0)
            prev = val
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# ---------------------------------------------------------------------------
# CLI — `python -m dos.drivers.visual_witness '<image>#ref:<reference>[/tol]'`
# (also reachable as `dos witness visual_witness '<subject>'`).
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="dos.drivers.visual_witness",
        description=__doc__.splitlines()[0],
    )
    ap.add_argument(
        "subject",
        help="'<image>#ref:<reference-image>[/<tol>]' or '<image>#phash:<hex>[/<tol>]' "
             "(images are PNM/PPM/PGM; tol is the max Hamming distance, default 5/64)",
    )
    ap.add_argument("--hash", action="store_true",
                    help="print the perceptual hash of the <image> path alone (before the '#') and exit")
    ap.add_argument("--json", action="store_true", help="machine-readable verdict")
    args = ap.parse_args(argv)

    if args.hash:
        path = args.subject.partition("#")[0].strip()
        h = _hash_image_file(path)
        if h is None:
            print(f"could not read/decode {path!r}", flush=True)
            return 3
        print(format(h, f"0{_HASH_SIZE * _HASH_SIZE // 4}x"))
        return 0

    source = VisualWitnessEvidenceSource()
    from dos.evidence import gather_evidence

    facts = gather_evidence(source, args.subject, None)
    belief = believe_under_floor((facts,))

    if args.json:
        print(json.dumps({"facts": facts.to_dict(), "belief": belief.to_dict()}, indent=2))
    else:
        print(f"SUBJECT   {args.subject}")
        print(f"SOURCE    {facts.source_name} ({facts.accountability.value})")
        print(f"STANCE    {facts.stance.value}   (reachable={facts.reachable})")
        print(f"WHY       {facts.detail}")
        print(f"BELIEVE   {belief.believe}   (refuted={belief.refuted})")

    if belief.refuted:
        return 1
    if belief.believe:
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
