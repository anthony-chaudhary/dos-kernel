"""dos.drivers.fs_artifact — the on-disk artifact read-back witness (docs/384).

An agent's claim "the build produced `dist/app.tar.gz`" witnessed by **the
filesystem the kernel reads**, not the agent's narration. This is the everyday
"I made a file" effect — the ergonomic, direct-on-disk sibling of `content_diff`
(which reads a *committed git blob*). It is the last roadmap backend of the
docs/384 witness-spectrum arc.

Why existence ALONE is the forgeable floor — and where the gold lifts it
========================================================================

A turn-time on-disk read is `actor==witness` for content the agent could have
written this turn: the agent can `touch dist/app.tar.gz` to fake presence, or pad
a file to any size. So **existence and size are the forgeable floor**
(`AGENT_AUTHORED`) — recorded and shown, never believed (`believe_under_floor`
filters them out structurally). A self-reported "I built it" cannot, by itself,
move a verdict to SHIPPED.

Soundness comes from the **gold**. Comparing the on-disk bytes to a `sha256:<hex>`
an independent source supplied is **preimage-sound**: the agent cannot produce
bytes whose sha256 equals a digest it did not choose, except by producing the
exact intended artifact (second-preimage resistance). So a sha256 MATCH is
non-forgeable — the agent cannot satisfy it without actually having built the
right thing. The comparison is expressed via `evidence.derived_witness` (the op
`sha256-eq`, declared up front, never reverse-searched), and the file-read
operand is fed at `OS_RECORDED` *for that comparison only* — so the derived rung
is `min(OS_RECORDED file-read, OS_RECORDED gold) = OS_RECORDED` and the match
grants belief.

This reaches the same rung as `content_diff` by a DIFFERENT route, and both are
honest:

  * `content_diff`'s evidence operand is `OS_RECORDED` because git's object store
    is content-addressed (the kernel read the bytes from the tamper-evident
    fossil, not from the agent).
  * `fs_artifact`'s evidence operand is `OS_RECORDED` because the gold digest is
    **preimage-resistant** (the match certifies the content regardless of who
    wrote the bytes).

Neither trusts the agent's word; a `size:` or bare-existence subject has no
preimage-sound gold, so its read stays on the floor.

The asymmetry that makes it useful even with NO gold (the REFUTE direction)
===========================================================================

Presence is forgeable, so it ATTESTS only at the floor. But ABSENCE REFUTES at
`OS_RECORDED`: the agent cannot make a file the kernel cannot find appear to be
there, and a missing artifact is a positive disconfirmation of "I built X." The
forgeable direction (presence) is floored; the disconfirming direction (absence)
is trusted. That is the whole DOS posture — believe nothing the agent can fake,
but catch the lie when the claimed effect simply is not there. A definitive
"not found" REFUTES; a read we could not perform at all (a permission error, a
path that is a directory, an I/O fault) degrades to `NO_SIGNAL` — never a
fabricated refute on a transient failure (the `http_probe` reached-vs-unreachable
discipline).

The honest caveat (the host's wiring responsibility)
====================================================

As with `os_acceptance` / `content_diff` / `state_diff`, the witness is sound only
when the host names the right path and a gold the agent did not author. The kernel
reads whatever path the host chose and compares to whatever gold the host wired;
choosing a path that holds the real artifact, and a `sha256:` an independent party
produced, is the host's job — the kernel reads the bytes and does the math.

The subject grammar — `<path>[#<gold-ref>]`
===========================================

  * `<path>`               — existence; present → ATTESTED (floor), absent → REFUTED.
  * `<path>#sha256:<hex>`   — preimage-sound; match → ATTESTED at OS_RECORDED
        (grants belief), mismatch → REFUTED, absent → REFUTED.
  * `<path>#size:<n>`       — the file's byte length equals n; a forgeable-floor
        convenience (the agent can pad), recorded never believed.

Shape & layering
================

A driver — it has the I/O surface the kernel forbids (reading the filesystem).
Stdlib only (`os`, `hashlib`). It implements the `evidence.EvidenceSource`
Protocol (class-level `name`; a boundary `gather(subject, config)` whose ONE file
read lives here, the `content_diff.gather` rule) so it drops straight into
`gather_evidence`, the belief fold, and `dos witness fs_artifact <subject>`. The
class `accountability` is the CEILING it can mint (`OS_RECORDED` — a sha256-matched
read; there is no third party in the picture); the PER-CALL rung is computed by
`derived_witness` and capped below it for existence/size. It imports the kernel;
the kernel never imports it (the `drivers/__init__` rule). Advisory: it reports a
read-back; it takes no lease and mutates nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os

# Imports the kernel — never the other way round (the driver rule).
from dos.evidence import (
    Accountability,
    EvidenceFacts,
    believe_under_floor,
    derived_witness,
)

# The fixed, DECLARED comparison ops handed to `derived_witness`. Constants, NOT
# reverse-searched to fit the answer (the `content_diff` discipline): a "does the
# file match SOME gold?" search IS the agent-selection that forges a rung, and
# `derived_witness` refuses an undeclared op for exactly that reason. One op per
# gold form — preimage-sound digest equality, or forgeable size equality.
_OP_SHA256_EQ = "sha256-eq"
_OP_SIZE_EQ = "size-eq"

# Stream the hash in chunks so a multi-GB artifact never has to fit in memory.
_HASH_CHUNK = 1 << 20  # 1 MiB


class FsArtifactEvidenceSource:
    """An `evidence.EvidenceSource`: witness whether an on-disk artifact exists and,
    with a `sha256:` gold, whether its content is the intended one.

    `name` is `fs_artifact`. `accountability` is the CEILING this source could mint
    (`OS_RECORDED`); the per-call rung is NOT this constant — it is computed by
    `derived_witness` from the operands and capped below (existence/size stay on the
    `AGENT_AUTHORED` floor; only a preimage-sound sha256 match reaches `OS_RECORDED`).
    `config` is accepted for Protocol conformance and unused (the subject is
    self-contained); a richer source could read a per-effect gold map out of
    `dos.toml [evidence]` via `config`.
    """

    name = "fs_artifact"
    accountability = Accountability.OS_RECORDED  # the CEILING; per-call rung is computed below

    def __init__(self, *, cwd: str | None = None) -> None:
        self._cwd = cwd

    def gather(self, subject: str, config: object) -> EvidenceFacts:
        """Parse the subject, read the artifact, and fold a verdict.

        Boundary I/O — the ONE file read lives here (the `content_diff.gather` rule);
        the returned facts are pure data `believe_under_floor` / `derived_witness`
        consume. Never raises: every failure mode degrades safely — a definitively
        absent file REFUTES, any other unreadable state is NO_SIGNAL, and a malformed
        subject / gold is NO_SIGNAL — so a transient I/O fault can never be mistaken
        for an attestation OR a fabricated refute."""
        parsed = _parse_subject(subject)
        if parsed is None:
            return EvidenceFacts.no_signal(
                self.name, self.accountability, subject or "",
                detail=(
                    "un-parseable subject — expected '<path>' / '<path>#sha256:<hex>' / "
                    "'<path>#size:<n>' — nothing to witness"
                ),
            )
        path, mode, gold = parsed
        full = _join(self._cwd, path)

        if mode == "exists":
            return self._witness_exists(path, full)
        if mode == "sha256":
            return self._witness_sha256(path, full, gold)
        return self._witness_size(path, full, gold)  # mode == "size"

    # -- existence: presence is the floor; absence is an accountable refute --------

    def _witness_exists(self, path: str, full: str) -> EvidenceFacts:
        present, err = _stat_size(full)
        if err == "absent":
            return EvidenceFacts.refute(
                self.name, Accountability.OS_RECORDED, path,
                detail=f"claimed artifact {path!r} is ABSENT — the OS cannot find it",
            )
        if err is not None:  # could not tell (permission, not-a-dir, I/O) — abstain
            return EvidenceFacts.no_signal(
                self.name, self.accountability, path,
                detail=f"could not stat {path!r} ({err}) — cannot tell",
            )
        # Present, but a turn-time existence read is forgeable (the agent can touch a
        # file): ATTESTED at the floor — recorded, never believed.
        return EvidenceFacts.attest(
            self.name, Accountability.AGENT_AUTHORED, path,
            detail=f"{path!r} exists ({present}B) — forgeable floor (agent could touch it; recorded, not believed)",
        )

    # -- sha256: preimage-sound; the gold lifts a match to OS_RECORDED --------------

    def _witness_sha256(self, path: str, full: str, gold: str) -> EvidenceFacts:
        hexd = gold.strip().lower()
        if len(hexd) != 64 or any(c not in "0123456789abcdef" for c in hexd):
            return EvidenceFacts.no_signal(
                self.name, self.accountability, path,
                detail=f"malformed sha256 gold {gold!r} (need 64 hex chars) — no signal",
            )
        actual, err = _sha256_file(full)
        if err == "absent":
            return EvidenceFacts.refute(
                self.name, Accountability.OS_RECORDED, path,
                detail=f"claimed artifact {path!r} is ABSENT — its content cannot match the gold",
            )
        if err is not None:
            return EvidenceFacts.no_signal(
                self.name, self.accountability, path,
                detail=f"could not read {path!r} ({err}) — cannot tell",
            )
        within_tol = actual == hexd
        # The evidence operand: the kernel read the bytes AND the comparison is against
        # a preimage-resistant gold, so the MATCH is non-forgeable — fed at OS_RECORDED
        # for this comparison only (the preimage-soundness route to the rung; see the
        # module docstring). For any non-sha256 gold this read stays on the floor.
        file_facts = EvidenceFacts.attest(
            self.name, Accountability.OS_RECORDED, path,
            detail=f"sha256({path}) = {actual[:12]}… (kernel-read, preimage-checked)",
        )
        gold_facts = EvidenceFacts.attest(
            self.name, Accountability.OS_RECORDED, path,
            detail=f"gold is a sha256 digest invariant ({hexd[:12]}…) — env-enforced, OS_RECORDED",
        )
        detail = (
            f"sha256({path}) {'==' if within_tol else '!='} gold ({hexd[:12]}…) "
            f"— preimage-sound"
        )
        return derived_witness(
            self.name, _OP_SHA256_EQ, [file_facts, gold_facts],
            subject=f"{path}#sha256:{hexd[:12]}…", within_tol=within_tol, detail=detail,
        )

    # -- size: a forgeable-floor convenience ---------------------------------------

    def _witness_size(self, path: str, full: str, gold: str) -> EvidenceFacts:
        try:
            want = int(gold.strip())
        except ValueError:
            return EvidenceFacts.no_signal(
                self.name, self.accountability, path,
                detail=f"malformed size gold {gold!r} (need an integer) — no signal",
            )
        if want < 0:
            return EvidenceFacts.no_signal(
                self.name, self.accountability, path,
                detail=f"negative size gold {want} — no signal",
            )
        size, err = _stat_size(full)
        if err == "absent":
            return EvidenceFacts.refute(
                self.name, Accountability.OS_RECORDED, path,
                detail=f"claimed artifact {path!r} is ABSENT — it has no size to match",
            )
        if err is not None:
            return EvidenceFacts.no_signal(
                self.name, self.accountability, path,
                detail=f"could not stat {path!r} ({err}) — cannot tell",
            )
        within_tol = size == want
        # Both operands are forgeable: a file's size is trivially padded, and the
        # expected size rode in on the agent-authored subject. So this derivation is
        # capped at the floor — recorded, never believed (a weak operator-visible check).
        file_facts = EvidenceFacts.attest(
            self.name, Accountability.AGENT_AUTHORED, path,
            detail=f"size({path}) = {size}B — forgeable (agent can pad)",
        )
        gold_facts = EvidenceFacts.attest(
            self.name, Accountability.AGENT_AUTHORED, path,
            detail=f"gold size {want}B — subject-supplied (forgeable floor)",
        )
        detail = f"size({path}) {'==' if within_tol else '!='} gold {want}B — forgeable floor"
        return derived_witness(
            self.name, _OP_SIZE_EQ, [file_facts, gold_facts],
            subject=f"{path}#size:{want}", within_tol=within_tol, detail=detail,
        )


# ---------------------------------------------------------------------------
# Boundary file reads — the ONE place I/O happens. Each returns (value, err) where
# err is None on success, "absent" on a definitive not-found (→ REFUTE), or a short
# error string on a cannot-tell failure (→ NO_SIGNAL). Never raises.
# ---------------------------------------------------------------------------


def _stat_size(full: str) -> "tuple[int, str | None]":
    """`(size_bytes, None)` if the path exists, `(0, "absent")` if definitively
    not-found, `(0, "<err>")` on any other failure (permission, not-a-dir, I/O)."""
    try:
        return os.stat(full).st_size, None
    except FileNotFoundError:
        return 0, "absent"
    except OSError as e:
        return 0, e.__class__.__name__


def _sha256_file(full: str) -> "tuple[str, str | None]":
    """`(hexdigest, None)` for the file's content, `("", "absent")` if not-found,
    `("", "<err>")` on any other failure. Streams the hash so a huge artifact never
    has to fit in memory."""
    h = hashlib.sha256()
    try:
        with open(full, "rb") as f:
            for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
                h.update(chunk)
    except FileNotFoundError:
        return "", "absent"
    except OSError as e:
        # IsADirectoryError / PermissionError / a read fault — cannot tell, never a
        # fabricated refute (the path is reachable but not a readable artifact).
        return "", e.__class__.__name__
    return h.hexdigest(), None


# ---------------------------------------------------------------------------
# Pure helpers (no I/O) — the subject grammar + cwd join.
# ---------------------------------------------------------------------------


def _parse_subject(subject: str) -> "tuple[str, str, str] | None":
    """`'<path>[#<kind>:<gold>]'` → `(path, mode, gold)`, or None if un-parseable.

    `mode` is `exists` (no `#`), `sha256`, or `size`. The `#` splits on the FIRST
    occurrence (a path with a literal `#` would be mis-split — rare for an artifact
    path; a host that needs one should avoid it). Empty path → None (never a
    universal match — the empty-glob trap). An unknown gold kind → None."""
    s = (subject or "").strip()
    if not s:
        return None
    if "#" not in s:
        return s, "exists", ""
    path, _, spec = s.partition("#")
    path = path.strip()
    spec = spec.strip()
    if not path or ":" not in spec:
        return None
    kind, _, gold = spec.partition(":")
    kind = kind.lower().strip()
    gold = gold.strip()
    if kind not in ("sha256", "size") or not gold:
        return None
    return path, kind, gold


def _join(cwd: "str | None", path: str) -> str:
    """Join an artifact path under the configured cwd (or the process cwd) — the
    `content_diff._join` discipline so a relative subject resolves predictably."""
    return os.path.join(cwd, path) if cwd else path


# ---------------------------------------------------------------------------
# CLI — `python -m dos.drivers.fs_artifact '<path>[#<gold-ref>]'`
# (also reachable as `dos witness fs_artifact '<subject>'`).
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="dos.drivers.fs_artifact",
        description=__doc__.splitlines()[0],
    )
    ap.add_argument(
        "subject",
        help="'<path>' (existence) / '<path>#sha256:<hex>' / '<path>#size:<n>'",
    )
    ap.add_argument("--cwd", default=None, help="root to resolve a relative path under (default: process cwd)")
    ap.add_argument("--json", action="store_true", help="machine-readable verdict")
    args = ap.parse_args(argv)

    source = FsArtifactEvidenceSource(cwd=args.cwd)
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

    # Exit map mirrors `dos verify` / os_acceptance / http_probe: believed attest 0,
    # refute 1 (the artifact disconfirms the claim), abstain (NO_SIGNAL or floor-only) 3.
    if belief.refuted:
        return 1
    if belief.believe:
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
