"""dos.drivers.content_diff — the content-diff read-back witness (docs/192 W2→W3).

The growth target docs/192 / docs/261 / docs/230 all name as the top unbuilt,
domain-free witness: the **W2→W3 climb** on DOS's most-used non-forgeable rung.

`verify()`'s flagship git rung is a **W2 presence** witness — it proves *a commit
touched file X* (`git log --oneline -- <path>`), never *X's content is RIGHT*. The
agent authored the file bytes and chose the commit; only WHICH-FILES-MOVED is
git-authored. docs/192 §3 calls this out as the W1↔W3 collapse reproduced inside DOS:
"wrote to ref.bib" = acceptance, not goal-achievement. This driver closes the gap —
it reads the actual committed blob and diffs its CONTENT against an expected value,
turning "the file changed" into "the file's content is the value it should be."

Why content-diff is a NON-FORGEABLE witness — and the one place it isn't
=======================================================================

A content-diff is a comparison of two operands, and (docs/192 §4, the W3*
gold-provenance sub-ladder) it is a sound witness ONLY when BOTH have an author who
is not the judged agent:

  1. the **evidence blob** — read via `git cat-file blob <sha>:<path>`. git's store is
     content-addressed and tamper-evident; the agent cannot make `<sha>:<path>` resolve
     to bytes other than the ones it committed. This operand is git-authored — free,
     always non-forgeable. (A *working-tree* read is NOT — the agent could have written
     the file this turn; actor==witness. So this driver reads a committed blob, and a
     `WORKTREE` evidence read is capped at the forgeable floor — see `_read_blob`.)
  2. the **gold** — the expected value. This is the load-bearing operand. If the gold
     is a value the agent or its plan supplied, the agent grades its own homework
     (docs/192 §4 row C — FORGEABLE); a checksum it cannot satisfy-by-construction, a
     committed reference file authored by someone else, or a host-wired third-party
     validator (row A/B) is sound.

The honest abstraction (the operator steer, 2026-06-15): "abstract third-party
validation that may trust that boundary." We do NOT hard-code which gold forms are
trusted. The gold is resolved to an `EvidenceFacts`-style operand carrying whatever
accountability rung its *source* declares, and the comparison is expressed as a
`evidence.derived_witness` derivation (`op="byte-eq"`) over the two operands. That
primitive caps the derived rung at the MINIMUM of its operands — so a forgeable gold
caps the whole content-diff at `AGENT_AUTHORED`, which `believe_under_floor` then
structurally refuses to let grant belief. The "agent grades its own homework" hole
closes by construction, not by a host knob.

The gold-ref grammar (the `<gold-ref>` field of the subject)
============================================================

| form                       | provenance (docs/192 §4)        | operand rung   |
|----------------------------|---------------------------------|----------------|
| `sha256:<hex>`             | (A) env-invariant checksum      | OS_RECORDED    |
| `source:<name>:<subject>`  | abstract — a host-wired gold    | the source's   |
|                            |     validator; trust its rung   | declared rung  |
| `inline:<value>`           | (C) agent/plan-supplied         | AGENT_AUTHORED |
| `plan:<token>`             | (C) plan-supplied               | AGENT_AUTHORED |
| (anything else / empty)    | none                            | NO_SIGNAL      |

For `sha256:` the gold is the *digest* (a value not reconstructible from the blob);
the agent cannot produce bytes whose digest equals a number it did not choose. For
`source:` the gold operand is whatever a named `dos.evidence_sources` validator
attests, at the rung that boundary declares — the kernel trusts the boundary the host
wired (the docs/93 move-B driver-oracle posture). For `inline:`/`plan:` the expected
value rode in on the agent-authored subject, so it is the forgeable floor — present so
the DEMOTION is provable, never to be believed.

Stance grammar (`evidence.derived_witness` does the rung math)
==============================================================

  * blob bytes == gold bytes → derived ATTESTED at min(blob-rung, gold-rung).
  * blob bytes != gold bytes → derived REFUTED (a positive disconfirmation — the
    W2→W3 value: "the file changed, but to the WRONG content", distinct from
    "could not tell").
  * blob unreadable / gold unreachable / unparseable subject → NO_SIGNAL (abstain;
    never a fabricated REFUTE that would falsely fail an honest commit).

Shape & layering
================

A driver — it has the I/O surface the kernel forbids (`git cat-file`, reading a gold
file, hashing). It implements the `evidence.EvidenceSource` Protocol (class-level
`name`/`accountability`; a boundary `gather(subject, config)` whose ONE subprocess
lives here, the `os_acceptance.gather` / `ci_status.gather` rule) so it drops straight
into `gather_evidence`, the belief fold, and `effect_witness.witness_effect`. The
source's class `accountability` is the CEILING it could ever mint (`THIRD_PARTY`); the
PER-CALL rung is computed by `derived_witness` from the operands and capped below it.
It imports the kernel; the kernel never imports it (the `drivers/__init__` rule).
Advisory: it reports a verdict, it reverts nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess

# Imports the kernel — never the other way round (the driver rule).
from dos.evidence import (
    Accountability,
    EvidenceFacts,
    believe_under_floor,
    derived_witness,
    resolve_evidence_source,
)

# Cap a `git cat-file` / gold-command read so a hung git or validator can't stall an
# evidence-gather — the `os_acceptance._DEFAULT_TIMEOUT_S` / `git_delta._GIT_TIMEOUT_S`
# discipline. A blob read is fast; the cap is generous for a `source:` validator.
_DEFAULT_TIMEOUT_S = 60

# The fixed, DECLARED comparison ops handed to `derived_witness`. They are constants,
# NOT reverse-searched to fit the answer: a "does the blob equal SOME gold?" search IS
# the agent-selection that forges the rung, and `derived_witness` refuses an undeclared
# op for exactly that reason (evidence.py §derived_witness). We commit to one op per
# gold form — raw-byte equality, or sha256-digest equality.
_OP_BYTE_EQ = "byte-eq"
_OP_SHA256_EQ = "sha256-eq"

# The sentinel `<sha>` that means "read the live working-tree file, not a committed
# blob." Such a read is the forgeable floor (the agent could have written the file this
# turn — actor==witness), so its evidence operand is capped at AGENT_AUTHORED.
_WORKTREE_SHA = "WORKTREE"


class ContentDiffEvidenceSource:
    """An `evidence.EvidenceSource`: witness whether a committed blob's CONTENT matches
    an expected gold value — the W2→W3 climb (docs/192).

    `name`/`accountability` are CLASS-LEVEL: this source is named `content_diff` and its
    `accountability` is the CEILING rung it could ever mint (`THIRD_PARTY` — a sound gold
    from a third-party validator). The per-call rung is NOT this constant: it is computed
    by `evidence.derived_witness` as the minimum over the two operands (the git blob and
    the gold), so a forgeable gold caps a given call at `AGENT_AUTHORED` regardless of the
    ceiling. (Contrast `os_acceptance`, whose rung is genuinely fixed because an OS exit
    code has exactly one honest rung; here the rung is a property of the gold's source.)

    The `subject` handed to `gather` is `"<path>@<sha>#<gold-ref>"` (see the module
    docstring). `config` is accepted for Protocol conformance and is unused (the subject
    is self-contained); a richer source could read a per-effect gold map out of
    `dos.toml [evidence]` via `config`.
    """

    name = "content_diff"
    accountability = Accountability.THIRD_PARTY  # the CEILING; per-call rung is capped below

    def __init__(self, *, timeout_s: int = _DEFAULT_TIMEOUT_S, cwd: str | None = None) -> None:
        self._timeout_s = timeout_s
        self._cwd = cwd

    # -- the Protocol entry point --------------------------------------------------

    def gather(self, subject: str, config: object) -> EvidenceFacts:
        """Parse the subject, read the committed blob + the gold, and fold a verdict.

        Boundary I/O — the subprocess(es) live here (the `ci_status.gather` rule); the
        returned facts are pure data `believe_under_floor` / `derived_witness` consume.
        Never raises: every failure mode degrades to an unreachable `no_signal`, so a
        missing blob / unreadable gold / timeout / OS error can never be mistaken for an
        attestation OR a refutation. Wrapped by `evidence.gather_evidence` at the call
        site for belt-and-braces, but defensive here too (a driver should not lean on its
        wrapper to be safe — the `os_acceptance.gather` discipline)."""
        parsed = _parse_subject(subject)
        if parsed is None:
            return EvidenceFacts.no_signal(
                self.name, self.accountability, subject or "",
                detail=(
                    "un-parseable content-diff subject — expected "
                    "'<path>@<sha>#<gold-ref>' — nothing to witness"
                ),
            )
        path, sha, gold_ref = parsed

        # 1. the evidence blob — git-authored (non-forgeable) when read from a commit;
        #    the forgeable floor when read from the live working tree.
        blob, blob_facts = self._read_blob(path, sha)
        if blob_facts.stance.value == "NO_SIGNAL":
            return blob_facts  # could not read the evidence side — abstain

        # 2. the gold operand — its rung is whatever its source declares. `mode` says how
        #    to compare it to the blob: raw bytes, a sha256 digest, or (for a pass-through
        #    validator) the validator's own stance IS the answer.
        gold, mode, gold_facts = self._resolve_gold(gold_ref, subject_echo=f"{path}@{sha}")
        if gold_facts.stance.value == "NO_SIGNAL":
            return gold_facts  # no/unreadable gold — abstain, never a fabricated refute

        # A pass-through validator (`source:` with no `gold_bytes`) already answered "is
        # the effect right?"; there is nothing for us to compare. Surface its facts as the
        # content-diff verdict (the trust-the-boundary contract — the validator's rung and
        # stance ride through unchanged).
        if mode == "stance":
            return gold_facts

        # 3. the comparison — a DECLARED-op derivation over the two operands. The op is
        #    fixed per `mode` (NEVER reverse-searched). The derived rung is
        #    min(blob-rung, gold-rung); a forgeable gold caps the whole thing at the floor,
        #    which `believe_under_floor` then refuses to believe (by construction).
        if mode == "sha256":
            op = _OP_SHA256_EQ
            actual = hashlib.sha256(blob).hexdigest().encode("ascii")
            within_tol = actual == gold  # gold is the expected digest (ascii hex)
            detail = (
                f"sha256({path}@{sha}) {'==' if within_tol else '!='} gold digest "
                f"({gold.decode('ascii', 'replace')[:12]}…); blob {len(blob)}B"
            )
        else:  # mode == "byte"
            op = _OP_BYTE_EQ
            within_tol = blob == gold
            detail = (
                f"content of {path}@{sha} {'==' if within_tol else '!='} gold "
                f"({gold_ref}); {len(blob)}B blob vs {len(gold)}B gold"
            )
        return derived_witness(
            self.name,
            op,
            [blob_facts, gold_facts],
            subject=f"{path}@{sha}#{gold_ref}",
            within_tol=within_tol,
            detail=detail,
        )

    # -- the evidence side ---------------------------------------------------------

    def _read_blob(self, path: str, sha: str) -> tuple[bytes, EvidenceFacts]:
        """Read the bytes at `<sha>:<path>` from git (non-forgeable), or the live file
        (the forgeable floor). Returns `(bytes, operand_facts)`; on any read failure the
        operand is a `no_signal` and the bytes are empty (the caller abstains)."""
        if sha == _WORKTREE_SHA:
            # A working-tree read: the agent could have written this file this turn, so
            # the evidence operand is the forgeable floor (actor==witness, docs/192 §5).
            try:
                with open(_join(self._cwd, path), "rb") as f:
                    data = f.read()
            except OSError as e:
                return b"", EvidenceFacts.no_signal(
                    self.name, self.accountability, f"{path}@{sha}",
                    detail=f"could not read working-tree file {path!r} ({e}) — no signal",
                )
            # ATTESTED-at-floor: the read succeeded, but the rung is AGENT_AUTHORED so the
            # derivation it feeds is capped at the floor.
            return data, EvidenceFacts.attest(
                self.name, Accountability.AGENT_AUTHORED, f"{path}@{sha}",
                detail=f"read working-tree {path!r} ({len(data)}B) — forgeable (actor==witness)",
            )

        # A committed blob: git-authored, content-addressed, tamper-evident → OS_RECORDED
        # (the kernel read it from git's object store, not from the agent's narration).
        # Validate the object is a blob before trusting its bytes (a path that resolves to
        # a tree would otherwise return directory listing bytes — the exit-0-with-garbage
        # trap; cf. ci_status._parse_check_runs defensive shape-check).
        obj = f"{sha}:{path}"
        otype = self._git(["cat-file", "-t", obj])
        if otype is None or otype.strip() != "blob":
            return b"", EvidenceFacts.no_signal(
                self.name, self.accountability, f"{path}@{sha}",
                detail=(
                    f"git object {obj!r} is not a blob "
                    f"(type={otype.strip() if otype else 'unreadable'}) — no signal"
                ),
            )
        data = self._git_bytes(["cat-file", "blob", obj])
        if data is None:
            return b"", EvidenceFacts.no_signal(
                self.name, self.accountability, f"{path}@{sha}",
                detail=f"could not read git blob {obj!r} — no signal",
            )
        return data, EvidenceFacts.attest(
            self.name, Accountability.OS_RECORDED, f"{path}@{sha}",
            detail=f"read git blob {obj!r} ({len(data)}B) — content-addressed, tamper-evident",
        )

    # -- the gold side -------------------------------------------------------------

    def _resolve_gold(
        self, gold_ref: str, *, subject_echo: str
    ) -> tuple[bytes, str, EvidenceFacts]:
        """Map a `<gold-ref>` to `(gold, mode, operand_facts)`.

        `mode` tells `gather` how to compare the gold to the blob:
          * `"byte"`   — `gold` is the expected raw bytes; compare `blob == gold`.
          * `"sha256"` — `gold` is the expected sha256 hex (ascii); compare
                         `sha256(blob).hexdigest() == gold`.
          * `"stance"` — there is nothing to compare; the gold validator already
                         answered, and `operand_facts` IS the verdict (pass-through).

        `operand_facts` carries the rung the gold's provenance earns — the load-bearing
        field `derived_witness` caps the result at. On any failure the facts are a
        `no_signal` (abstain, never a fabricated gold that would refute an honest
        commit)."""
        ref = (gold_ref or "").strip()
        if not ref:
            return b"", "byte", EvidenceFacts.no_signal(
                self.name, self.accountability, subject_echo,
                detail="empty gold-ref — nothing to diff against",
            )

        kind, _, rest = ref.partition(":")
        kind = kind.lower()

        if kind == "sha256":
            # (A) env-invariant: the agent cannot produce bytes whose digest equals a
            # number it did not choose (docs/192 §4 row A). OS_RECORDED.
            hexd = rest.strip().lower()
            if len(hexd) != 64 or any(c not in "0123456789abcdef" for c in hexd):
                return b"", "sha256", EvidenceFacts.no_signal(
                    self.name, self.accountability, subject_echo,
                    detail=f"malformed sha256 gold {rest!r} (need 64 hex chars) — no signal",
                )
            return hexd.encode("ascii"), "sha256", EvidenceFacts.attest(
                self.name, Accountability.OS_RECORDED, subject_echo,
                detail=f"gold is a sha256 digest invariant ({hexd[:12]}…) — env-enforced, OS_RECORDED",
            )

        if kind == "source":
            # Abstract third-party validation (the operator steer): resolve a named
            # validator and TRUST the rung it declares.
            return self._source_gold(rest, subject_echo)

        if kind in ("inline", "plan"):
            # (C) agent/plan-supplied — the forgeable floor (docs/192 §4 row C). Present so
            # the DEMOTION is provable; the bytes are the expected value verbatim.
            data = rest.encode("utf-8")
            return data, "byte", EvidenceFacts.attest(
                self.name, Accountability.AGENT_AUTHORED, subject_echo,
                detail=(
                    f"gold is {kind}-supplied ({len(data)}B) — FORGEABLE floor "
                    f"(agent/plan grades its own homework; capped at AGENT_AUTHORED)"
                ),
            )

        return b"", "byte", EvidenceFacts.no_signal(
            self.name, self.accountability, subject_echo,
            detail=f"unknown gold-ref kind {kind!r} — expected sha256:/source:/inline:/plan: — no signal",
        )

    def _source_gold(self, rest: str, subject_echo: str) -> tuple[bytes, str, EvidenceFacts]:
        """A `source:<name>:<subject>` gold — an abstract validator the host wired. We
        resolve it by name and trust the rung it declares. If the validator exposes its
        expected bytes via a `gold_bytes(subject)` method we compare bytes (`mode="byte"`,
        at the validator's rung); otherwise its own attest/refute stance IS the comparison
        (`mode="stance"`) — `source:` is an abstract pass-through to any EvidenceSource."""
        name, _, gsubject = rest.partition(":")
        name = name.strip()
        if not name:
            return b"", "byte", EvidenceFacts.no_signal(
                self.name, self.accountability, subject_echo,
                detail="source-gold missing a validator name — expected 'source:<name>:<subject>' — no signal",
            )
        try:
            src = resolve_evidence_source(name)
        except Exception as e:
            return b"", "byte", EvidenceFacts.no_signal(
                self.name, self.accountability, subject_echo,
                detail=f"could not resolve gold validator {name!r} ({e}) — no signal",
            )
        gold_bytes_fn = getattr(src, "gold_bytes", None)
        if callable(gold_bytes_fn):
            try:
                gb = gold_bytes_fn(gsubject)
            except Exception as e:
                return b"", "byte", EvidenceFacts.no_signal(
                    self.name, self.accountability, subject_echo,
                    detail=f"gold validator {name!r} raised reading gold ({e}) — no signal",
                )
            if not isinstance(gb, (bytes, bytearray)):
                return b"", "byte", EvidenceFacts.no_signal(
                    self.name, self.accountability, subject_echo,
                    detail=f"gold validator {name!r} returned non-bytes gold — no signal",
                )
            rung = getattr(src, "accountability", Accountability.AGENT_AUTHORED)
            if not isinstance(rung, Accountability):
                rung = Accountability.AGENT_AUTHORED
            return bytes(gb), "byte", EvidenceFacts.attest(
                self.name, rung, subject_echo,
                detail=f"gold bytes from validator {name!r} ({len(gb)}B) at rung {rung.value}",
            )
        # No gold_bytes(): the validator answered "is the effect right?" itself; surface
        # its facts as the verdict (mode="stance"). Re-stamp source_name so the operator
        # sees content_diff routed it, but keep the validator's rung + stance (the
        # trust-the-boundary contract).
        from dos.evidence import gather_evidence
        facts = gather_evidence(src, gsubject, None)
        return b"", "stance", EvidenceFacts(
            source_name=self.name,
            accountability=facts.accountability,
            stance=facts.stance,
            subject=subject_echo,
            detail=f"via gold validator {name!r}: {facts.detail}",
            reachable=facts.reachable,
        )

    # -- git boundary --------------------------------------------------------------

    def _git(self, args: list[str]) -> str | None:
        """Run a `git` subcommand, return stdout text, or None on any failure (the
        fail-safe boundary; never raises)."""
        try:
            p = subprocess.run(
                ["git", *args],
                capture_output=True, text=True, check=False,
                timeout=self._timeout_s, cwd=self._cwd,
                stdin=subprocess.DEVNULL,  # docs/295 — never leak a transport pipe
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if p.returncode != 0:
            return None
        return p.stdout

    def _git_bytes(self, args: list[str]) -> bytes | None:
        """`_git` for raw bytes — `git cat-file blob` content must be compared as BYTES,
        never text (text mode + core.autocrlf would make a byte-identical file compare
        unequal — the CRLF false-REFUTE trap, docs/192-adjacent)."""
        try:
            p = subprocess.run(
                ["git", *args],
                capture_output=True, check=False,
                timeout=self._timeout_s, cwd=self._cwd,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if p.returncode != 0:
            return None
        return p.stdout


# ---------------------------------------------------------------------------
# Pure helpers (no I/O) — the subject grammar.
# ---------------------------------------------------------------------------


def _parse_subject(subject: str) -> tuple[str, str, str] | None:
    """`'<path>@<sha>#<gold-ref>'` → `(path, sha, gold_ref)`, or None if un-parseable.

    `<sha>` defaults to `HEAD` if the `@<sha>` segment is omitted. The `#` splits the
    gold-ref (a gold-ref like `inline:a#b` keeps everything after the FIRST `#` as the
    gold-ref). Empty path or empty gold-ref → None (never a universal match — the
    empty-glob trap, self_modify.py:102-106)."""
    s = (subject or "").strip()
    if not s or "#" not in s:
        return None
    left, _, gold_ref = s.partition("#")
    gold_ref = gold_ref.strip()
    if not gold_ref:
        return None
    # left is '<path>' or '<path>@<sha>'
    if "@" in left:
        path, _, sha = left.partition("@")
        path = path.strip()
        sha = sha.strip() or "HEAD"
    else:
        path = left.strip()
        sha = "HEAD"
    if not path:
        return None
    return path, sha, gold_ref


def _join(cwd: str | None, path: str) -> str:
    """Join a working-tree path under the configured cwd (or the process cwd)."""
    import os
    return os.path.join(cwd, path) if cwd else path


# ---------------------------------------------------------------------------
# CLI — `python -m dos.drivers.content_diff '<path>@<sha>#<gold-ref>'`
# witnesses whether the committed blob's content matches the gold.
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="dos.drivers.content_diff",
        description=__doc__.splitlines()[0],
    )
    ap.add_argument(
        "subject",
        help="'<path>@<sha>#<gold-ref>' — e.g. 'src/x.py@HEAD#sha256:<hex>' or "
             "'out.txt@<sha>#source:<validator>:<subj>' or 'a.txt@HEAD#inline:expected'",
    )
    ap.add_argument("--cwd", default=None, help="repo root to run git in (default: process cwd)")
    ap.add_argument("--json", action="store_true", help="machine-readable verdict")
    args = ap.parse_args(argv)

    src = ContentDiffEvidenceSource(cwd=args.cwd)
    facts = src.gather(args.subject, None)
    # Show the floor decision too: would this read grant belief?
    belief = believe_under_floor((facts,))

    if args.json:
        out = facts.to_dict()
        out["would_believe"] = belief.believe
        out["refuted"] = belief.refuted
        print(json.dumps(out, indent=2))
    else:
        print(f"SUBJECT   {args.subject}")
        print(f"STANCE    {facts.stance.value}   (rung={facts.accountability.value} reachable={facts.reachable})")
        print(f"BELIEVE   {belief.believe}   refuted={belief.refuted}")
        print(f"WHY       {facts.detail}")

    if facts.stance.value == "REFUTED":
        return 1
    if belief.believe:
        return 0
    return 3  # ATTESTED-at-floor or NO_SIGNAL — abstain


if __name__ == "__main__":
    raise SystemExit(main())
