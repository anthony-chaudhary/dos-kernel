"""dos.drivers.provider_ledger — the provider-ledger read-back witness (the THIRD_PARTY band).

The next unbuilt, domain-free witness named by the witness-ladder benchmark and
docs/261 §"What growth looks like": the **external-effect rung**. The
`content_diff` driver (docs/349) climbs `verify`'s OS_RECORDED band — "the file's
content is RIGHT, not just changed." This driver climbs ONE rung higher, to
**THIRD_PARTY**: claims of the form *"I made an external effect"* — a charge was
made, a message was sent, a cloud resource was provisioned — where the ground
truth lives on **a different principal's ledger** the agent does not control.

content_diff cannot reach this band: a committed blob is git-authored, but a charge
is recorded on the PAYMENT PROCESSOR's books, an SMS delivery on the GATEWAY's, a
VM on the CLOUD's. The agent under adjudication authored none of those rows — that
is exactly what makes a different-principal ledger a strictly more accountable
referent than anything in the agent's own repo (the docs/85 §1 accountability
spectrum: mutable third-party state on infrastructure the agent does not control).

Why a provider-ledger read-back is a NON-FORGEABLE witness — and the one place it isn't
======================================================================================

The witness is a keyed lookup: the agent claims it made an external effect under a
**reference** (`ch_1a2b`, an SMS sid, an instance-id). The driver looks that
reference up in a ledger and checks the claimed fields are present and equal. The
reference is the un-forgeable hook: the agent NAMES the row; the ledger either has
that row with those fields or it does not. It is a comparison of two operands, and
(docs/192 §4, the W3* gold-provenance sub-ladder) sound ONLY when BOTH have an
author who is not the judged agent:

  1. the **ledger record** — read either from a committed blob (`<path>@<sha>`,
     git-authored → OS_RECORDED, the host captured the provider's webhook/API dump
     and committed it) or via an abstract `source:<validator>:<ref>` delegation to a
     host-wired provider poller that declares its own rung (a live charge lookup →
     THIRD_PARTY). A `@WORKTREE` read is the forgeable floor (the agent could have
     written the ledger file this turn — actor==witness), capped at AGENT_AUTHORED.
  2. the **expectation** — the fields the agent claims the record holds. If the
     agent or its plan supplied them (`inline:`/`plan:`), the agent grades its own
     homework (docs/192 §4 row C — FORGEABLE); a host-pinned expectation
     (`source:<validator>:<ref>`) is at the validator's rung.

The honest abstraction is content_diff's, lifted one rung: we do NOT hard-code a
vendor. No payment processor, gateway, or cloud is named anywhere here — the kernel
and this driver name no vendor (the domain-free litmus). A live provider is reached
only through the abstract `source:` seam a HOST wires; the built-in concrete path is
a host-captured ledger committed into git. The comparison is expressed as a
`evidence.derived_witness` derivation (`op="ledger-match"`) over the two operands;
that primitive caps the derived rung at the MINIMUM of its operands — so a forgeable
expectation (or a worktree-read record) caps the whole thing at AGENT_AUTHORED,
which `believe_under_floor` then structurally refuses to let grant belief. The
"agent grades its own homework" hole closes by construction, not by a host knob.

The subject grammar (`'<ref>@<ledger>#<expect>'`)
=================================================

| field      | meaning                                                              |
|------------|----------------------------------------------------------------------|
| `<ref>`    | the provider reference the agent claims (the row key into the ledger) |
| `<ledger>` | where the record lives — `<path>` / `<path>@<sha>` / `source:<v>:<s>` |
| `<expect>` | the expected fields, supplied with a provenance prefix (below)        |

`<ledger>` forms (the record operand, mirroring content_diff's evidence side):

| form                       | provenance                     | record rung    |
|----------------------------|--------------------------------|----------------|
| `<path>` or `<path>@<sha>` | committed blob (git-authored)  | OS_RECORDED    |
| `<path>@WORKTREE`          | live working-tree file         | AGENT_AUTHORED |
| `source:<name>:<subject>`  | a host-wired provider validator| its declared   |

`<expect>` forms (the expectation operand, mirroring content_diff's gold side):

| form                       | provenance (docs/192 §4)        | operand rung   |
|----------------------------|---------------------------------|----------------|
| `inline:k=v,k2=v2`         | (C) agent/plan-supplied         | AGENT_AUTHORED |
| `plan:k=v`                 | (C) plan-supplied               | AGENT_AUTHORED |
| `source:<name>:<subject>`  | a host-wired expectation        | its declared   |
| (anything else / empty)    | none                            | NO_SIGNAL      |

Stance grammar (`evidence.derived_witness` does the rung math)
==============================================================

  * the referenced row exists AND every expected field matches → derived ATTESTED at
    min(record-rung, expect-rung).
  * the row exists but a field disagrees → derived REFUTED (a positive
    disconfirmation — "you claimed `ch_1a2b` charged 4200; the ledger says 5000",
    distinct from "could not tell").
  * the ref is absent / ledger unreadable / unparseable subject → NO_SIGNAL
    (abstain; never a fabricated REFUTE that would falsely fail an honest effect —
    an effect genuinely made may simply not be in THIS captured ledger).

Shape & layering
================

A driver — it has the I/O surface the kernel forbids (`git cat-file`, reading a
ledger file, resolving a host validator). It implements the `evidence.EvidenceSource`
Protocol (class-level `name`/`accountability`; a boundary `gather(subject, config)`
whose I/O lives here, the `content_diff.gather` / `ci_status.gather` rule) so it
drops straight into `gather_evidence`, the belief fold, and
`effect_witness.witness_effect`. The class `accountability` is the CEILING it could
ever mint (`THIRD_PARTY`); the PER-CALL rung is computed by `derived_witness` from
the operands and capped below it. It imports the kernel; the kernel never imports it
(the `drivers/__init__` rule). Advisory: it reports a verdict, it reverts nothing.
"""

from __future__ import annotations

import argparse
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

# Cap a `git cat-file` / validator read so a hung git or provider poller can't stall
# an evidence-gather — the `content_diff._DEFAULT_TIMEOUT_S` discipline.
_DEFAULT_TIMEOUT_S = 60

# The fixed, DECLARED comparison op handed to `derived_witness`. It is a constant,
# NOT reverse-searched to fit the answer: a "does the row match SOME expectation?"
# search IS the agent-selection that forges the rung, and `derived_witness` refuses an
# undeclared op for exactly that reason (evidence.py §derived_witness).
_OP_LEDGER_MATCH = "ledger-match"

# The sentinel `<sha>` that means "read the live working-tree file, not a committed
# blob." Such a read is the forgeable floor (the agent could have written the ledger
# this turn — actor==witness), so its record operand is capped at AGENT_AUTHORED.
_WORKTREE_SHA = "WORKTREE"

# Field names a ledger row might use for its primary reference, tried in order. A
# host-captured provider dump keys its rows under one of these; the lookup is by
# VALUE-equality against `<ref>`, so the agent cannot make a row it did not create
# resolve to its claimed reference.
_REF_FIELDS = ("id", "reference", "ref", "object_id", "sid", "txn_id", "transaction_id")


class ProviderLedgerEvidenceSource:
    """An `evidence.EvidenceSource`: witness whether a different-principal ledger
    confirms an agent's external-effect claim — the THIRD_PARTY band (docs/261).

    `name`/`accountability` are CLASS-LEVEL: this source is named `provider_ledger`
    and its `accountability` is the CEILING rung it could ever mint (`THIRD_PARTY` — a
    sound record from a host-wired provider validator). The per-call rung is NOT this
    constant: it is computed by `evidence.derived_witness` as the minimum over the two
    operands (the ledger record and the expectation), so a forgeable expectation — or
    a worktree-read record — caps a given call at `AGENT_AUTHORED` regardless of the
    ceiling.

    The `subject` handed to `gather` is `"<ref>@<ledger>#<expect>"` (see the module
    docstring). `config` is accepted for Protocol conformance and is unused (the
    subject is self-contained); a richer source could read a per-effect ledger map out
    of `dos.toml [evidence]` via `config`.
    """

    name = "provider_ledger"
    accountability = Accountability.THIRD_PARTY  # the CEILING; per-call rung is capped below

    def __init__(self, *, timeout_s: int = _DEFAULT_TIMEOUT_S, cwd: str | None = None) -> None:
        self._timeout_s = timeout_s
        self._cwd = cwd

    # -- the Protocol entry point --------------------------------------------------

    def gather(self, subject: str, config: object) -> EvidenceFacts:
        """Parse the subject, read the ledger record + the expectation, and fold a verdict.

        Boundary I/O — the subprocess(es) and validator resolution live here (the
        `content_diff.gather` rule); the returned facts are pure data
        `believe_under_floor` / `derived_witness` consume. Never raises: every failure
        mode degrades to an unreachable `no_signal`, so a missing ledger / absent ref /
        timeout / OS error can never be mistaken for an attestation OR a refutation."""
        parsed = _parse_subject(subject)
        if parsed is None:
            return EvidenceFacts.no_signal(
                self.name, self.accountability, subject or "",
                detail=(
                    "un-parseable provider-ledger subject — expected "
                    "'<ref>@<ledger>#<expect>' — nothing to witness"
                ),
            )
        ref, ledger_ref, expect_ref = parsed

        # 1. the ledger record — git-authored (non-forgeable) from a commit, the
        #    forgeable floor from the live tree, or a host validator's declared rung.
        record_bytes, record_facts = self._read_record(ref, ledger_ref)
        if record_facts.stance.value == "NO_SIGNAL":
            return record_facts  # could not read the ledger side — abstain

        # 2. the expectation operand — its rung is whatever its source declares.
        expect, expect_facts = self._resolve_expectation(expect_ref, subject_echo=f"{ref}@{ledger_ref}")
        if expect_facts.stance.value == "NO_SIGNAL":
            return expect_facts  # no/unreadable expectation — abstain, never a fabricated refute

        # 3. the keyed lookup — find the row the agent named. A genuinely-made effect
        #    that is simply not in THIS captured ledger is NO_SIGNAL (abstain), NOT a
        #    refute: absence-from-a-partial-ledger is not disconfirmation.
        row = _lookup_ref(record_bytes, ref)
        if row is None:
            return EvidenceFacts.no_signal(
                self.name, self.accountability, f"{ref}@{ledger_ref}",
                detail=(
                    f"reference {ref!r} not found in the ledger ({len(record_bytes)}B) — "
                    f"no record either way (abstain, never a fabricated refute)"
                ),
            )

        # 4. the comparison — a DECLARED-op derivation over the two operands. The op is
        #    fixed (NEVER reverse-searched). The derived rung is min(record, expect); a
        #    forgeable expectation (or worktree record) caps the whole thing at the
        #    floor, which `believe_under_floor` then refuses to believe (by construction).
        matched, mismatch = _fields_match(row, expect)
        if matched:
            detail = (
                f"ledger row {ref!r} confirms {len(expect)} field(s) "
                f"({', '.join(f'{k}={v}' for k, v in sorted(expect.items()))})"
            )
        else:
            detail = (
                f"ledger row {ref!r} DISAGREES on {mismatch} — claimed vs recorded differ "
                f"(the external effect did not happen as claimed)"
            )
        return derived_witness(
            self.name,
            _OP_LEDGER_MATCH,
            [record_facts, expect_facts],
            subject=f"{ref}@{ledger_ref}#{expect_ref}",
            within_tol=matched,
            detail=detail,
        )

    # -- the record (evidence) side ------------------------------------------------

    def _read_record(self, ref: str, ledger_ref: str) -> tuple[bytes, EvidenceFacts]:
        """Read the ledger bytes from a committed blob (non-forgeable), the live tree
        (the forgeable floor), or a host-wired `source:` validator. Returns
        `(bytes, operand_facts)`; on any read failure the operand is a `no_signal` and
        the bytes are empty (the caller abstains)."""
        # A `source:<name>:<subject>` ledger: a host wired a live provider poller.
        # Resolve it and trust the rung it declares. The validator's `gather` returns a
        # NORMALIZED ledger (a JSON object/array its `gather` produced); we read its
        # bytes from the facts' detail is NOT how this works — instead, a `source:`
        # ledger validator exposes its bytes via `ledger_bytes(subject)` (the
        # content_diff `gold_bytes` duck-type), else we cannot diff and abstain.
        kind, _, rest = ledger_ref.partition(":")
        if kind.lower() == "source":
            return self._source_record(rest, subject_echo=f"{ref}@{ledger_ref}")

        # Otherwise `<path>` or `<path>@<sha>`: a committed blob or worktree read.
        path, _, sha = ledger_ref.partition("@")
        path = path.strip()
        sha = sha.strip() or "HEAD"
        if not path:
            return b"", EvidenceFacts.no_signal(
                self.name, self.accountability, f"{ref}@{ledger_ref}",
                detail="empty ledger path — nothing to read the record from",
            )

        if sha == _WORKTREE_SHA:
            # A working-tree read: the agent could have written this ledger this turn,
            # so the record operand is the forgeable floor (actor==witness, docs/192 §5).
            try:
                with open(_join(self._cwd, path), "rb") as f:
                    data = f.read()
            except OSError as e:
                return b"", EvidenceFacts.no_signal(
                    self.name, self.accountability, f"{ref}@{ledger_ref}",
                    detail=f"could not read working-tree ledger {path!r} ({e}) — no signal",
                )
            return data, EvidenceFacts.attest(
                self.name, Accountability.AGENT_AUTHORED, f"{ref}@{ledger_ref}",
                detail=f"read working-tree ledger {path!r} ({len(data)}B) — forgeable (actor==witness)",
            )

        # A committed blob: git-authored, content-addressed, tamper-evident → OS_RECORDED
        # (the host captured the provider's dump and committed it; the agent cannot make
        # `<sha>:<path>` resolve to bytes other than the ones committed). Validate the
        # object is a blob before trusting its bytes (a path that resolves to a tree
        # would return directory-listing bytes — the exit-0-with-garbage trap).
        obj = f"{sha}:{path}"
        otype = self._git(["cat-file", "-t", obj])
        if otype is None or otype.strip() != "blob":
            return b"", EvidenceFacts.no_signal(
                self.name, self.accountability, f"{ref}@{ledger_ref}",
                detail=(
                    f"git object {obj!r} is not a blob "
                    f"(type={otype.strip() if otype else 'unreadable'}) — no signal"
                ),
            )
        data = self._git_bytes(["cat-file", "blob", obj])
        if data is None:
            return b"", EvidenceFacts.no_signal(
                self.name, self.accountability, f"{ref}@{ledger_ref}",
                detail=f"could not read git blob {obj!r} — no signal",
            )
        return data, EvidenceFacts.attest(
            self.name, Accountability.OS_RECORDED, f"{ref}@{ledger_ref}",
            detail=f"read committed ledger blob {obj!r} ({len(data)}B) — content-addressed, tamper-evident",
        )

    def _source_record(self, rest: str, *, subject_echo: str) -> tuple[bytes, EvidenceFacts]:
        """A `source:<name>:<subject>` ledger — a host-wired provider validator. Resolve
        it by name and trust the rung it declares. The validator exposes its normalized
        ledger bytes via `ledger_bytes(subject)` (the content_diff `gold_bytes`
        duck-type); we read those at the validator's rung. Without `ledger_bytes` there
        is nothing to diff our keyed lookup against → abstain."""
        name, _, vsubject = rest.partition(":")
        name = name.strip()
        if not name:
            return b"", EvidenceFacts.no_signal(
                self.name, self.accountability, subject_echo,
                detail="source-ledger missing a validator name — expected 'source:<name>:<subject>' — no signal",
            )
        try:
            src = resolve_evidence_source(name)
        except Exception as e:
            return b"", EvidenceFacts.no_signal(
                self.name, self.accountability, subject_echo,
                detail=f"could not resolve ledger validator {name!r} ({e}) — no signal",
            )
        ledger_bytes_fn = getattr(src, "ledger_bytes", None)
        if not callable(ledger_bytes_fn):
            return b"", EvidenceFacts.no_signal(
                self.name, self.accountability, subject_echo,
                detail=(
                    f"ledger validator {name!r} exposes no ledger_bytes() — cannot do a keyed "
                    f"lookup against it — no signal"
                ),
            )
        try:
            lb = ledger_bytes_fn(vsubject)
        except Exception as e:
            return b"", EvidenceFacts.no_signal(
                self.name, self.accountability, subject_echo,
                detail=f"ledger validator {name!r} raised reading the ledger ({e}) — no signal",
            )
        if not isinstance(lb, (bytes, bytearray)):
            return b"", EvidenceFacts.no_signal(
                self.name, self.accountability, subject_echo,
                detail=f"ledger validator {name!r} returned non-bytes ledger — no signal",
            )
        rung = getattr(src, "accountability", Accountability.AGENT_AUTHORED)
        if not isinstance(rung, Accountability):
            rung = Accountability.AGENT_AUTHORED
        return bytes(lb), EvidenceFacts.attest(
            self.name, rung, subject_echo,
            detail=f"ledger bytes from validator {name!r} ({len(lb)}B) at rung {rung.value}",
        )

    # -- the expectation (gold) side -----------------------------------------------

    def _resolve_expectation(
        self, expect_ref: str, *, subject_echo: str
    ) -> tuple[dict, EvidenceFacts]:
        """Map an `<expect>` ref to `(fields, operand_facts)`.

        `fields` is the `{k: v}` the agent claims the ledger row holds. `operand_facts`
        carries the rung the expectation's provenance earns — the load-bearing field
        `derived_witness` caps the result at. On any failure the facts are a `no_signal`
        (abstain, never a fabricated expectation that would refute an honest effect)."""
        ref = (expect_ref or "").strip()
        if not ref:
            return {}, EvidenceFacts.no_signal(
                self.name, self.accountability, subject_echo,
                detail="empty expectation — nothing to check the ledger row against",
            )

        kind, _, rest = ref.partition(":")
        kind = kind.lower()

        if kind in ("inline", "plan"):
            # (C) agent/plan-supplied — the forgeable floor (docs/192 §4 row C). Present
            # so the DEMOTION is provable; the fields are the claimed values verbatim.
            fields = _parse_fields(rest)
            if not fields:
                return {}, EvidenceFacts.no_signal(
                    self.name, self.accountability, subject_echo,
                    detail=f"{kind}-expectation parsed to no 'k=v' fields ({rest!r}) — no signal",
                )
            return fields, EvidenceFacts.attest(
                self.name, Accountability.AGENT_AUTHORED, subject_echo,
                detail=(
                    f"expectation is {kind}-supplied ({len(fields)} field(s)) — FORGEABLE floor "
                    f"(agent/plan grades its own homework; capped at AGENT_AUTHORED)"
                ),
            )

        if kind == "source":
            return self._source_expectation(rest, subject_echo)

        return {}, EvidenceFacts.no_signal(
            self.name, self.accountability, subject_echo,
            detail=f"unknown expectation kind {kind!r} — expected inline:/plan:/source: — no signal",
        )

    def _source_expectation(self, rest: str, subject_echo: str) -> tuple[dict, EvidenceFacts]:
        """A `source:<name>:<subject>` expectation — a host-wired validator that vouches
        for the expected fields. Resolve it by name and trust the rung it declares. The
        validator exposes its expected fields via `expected_fields(subject)` (a dict);
        we compare at the validator's rung."""
        name, _, vsubject = rest.partition(":")
        name = name.strip()
        if not name:
            return {}, EvidenceFacts.no_signal(
                self.name, self.accountability, subject_echo,
                detail="source-expectation missing a validator name — expected 'source:<name>:<subject>' — no signal",
            )
        try:
            src = resolve_evidence_source(name)
        except Exception as e:
            return {}, EvidenceFacts.no_signal(
                self.name, self.accountability, subject_echo,
                detail=f"could not resolve expectation validator {name!r} ({e}) — no signal",
            )
        fields_fn = getattr(src, "expected_fields", None)
        if not callable(fields_fn):
            return {}, EvidenceFacts.no_signal(
                self.name, self.accountability, subject_echo,
                detail=f"expectation validator {name!r} exposes no expected_fields() — no signal",
            )
        try:
            ef = fields_fn(vsubject)
        except Exception as e:
            return {}, EvidenceFacts.no_signal(
                self.name, self.accountability, subject_echo,
                detail=f"expectation validator {name!r} raised reading fields ({e}) — no signal",
            )
        if not isinstance(ef, dict) or not ef:
            return {}, EvidenceFacts.no_signal(
                self.name, self.accountability, subject_echo,
                detail=f"expectation validator {name!r} returned no/invalid fields — no signal",
            )
        rung = getattr(src, "accountability", Accountability.AGENT_AUTHORED)
        if not isinstance(rung, Accountability):
            rung = Accountability.AGENT_AUTHORED
        # Normalize values to strings so the subset match is rung-agnostic (the ledger
        # row's JSON values are compared as strings too — see `_fields_match`).
        fields = {str(k): str(v) for k, v in ef.items()}
        return fields, EvidenceFacts.attest(
            self.name, rung, subject_echo,
            detail=f"expected {len(fields)} field(s) from validator {name!r} at rung {rung.value}",
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
        """`_git` for raw bytes — `git cat-file blob` content must be parsed as BYTES,
        never text (text mode + core.autocrlf would corrupt the JSON the CRLF way)."""
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
# Pure helpers (no I/O) — the subject grammar + the keyed lookup + field match.
# ---------------------------------------------------------------------------


def _parse_subject(subject: str) -> tuple[str, str, str] | None:
    """`'<ref>@<ledger>#<expect>'` → `(ref, ledger, expect)`, or None if un-parseable.

    The `@` splits the ref from the ledger locator; the `#` splits the ledger from the
    expectation (the FIRST `#` — an expectation value may contain `#`). A `source:`
    ledger keeps its own colons; only the leading `ref@` is peeled here. Empty ref,
    ledger, or expectation → None (never a universal match — the empty-glob trap)."""
    s = (subject or "").strip()
    if not s or "#" not in s or "@" not in s:
        return None
    left, _, expect = s.partition("#")
    expect = expect.strip()
    if not expect:
        return None
    ref, _, ledger = left.partition("@")  # first '@' peels the ref; ledger keeps the rest
    ref = ref.strip()
    ledger = ledger.strip()
    if not ref or not ledger:
        return None
    return ref, ledger, expect


def _parse_fields(raw: str) -> dict:
    """`'k=v,k2=v2'` → `{k: v}` (values as strings; surrounding whitespace stripped).

    A field with no `=` is skipped (not a key/value pair). Duplicate keys: last wins.
    Empty input → `{}` (the caller treats an empty expectation as no_signal)."""
    out: dict = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        if k:
            out[k] = v.strip()
    return out


def _lookup_ref(record_bytes: bytes, ref: str) -> dict | None:
    """Find the ledger row whose reference == `ref`, return its fields as a dict, or
    None if absent / the ledger is unparseable.

    Tolerant of two shapes a host-captured provider dump takes:
      * a JSON ARRAY of row objects (`[{...}, {...}]`)
      * JSONL — one JSON object per line (a webhook append-log)
    A row matches when ANY of its `_REF_FIELDS` equals `ref` (string-compared). On any
    parse failure or non-object rows → None (the caller abstains, never refutes)."""
    text = _decode(record_bytes)
    if text is None:
        return None
    rows = _iter_rows(text)
    if rows is None:
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        for f in _REF_FIELDS:
            if f in row and str(row[f]) == ref:
                return row
    return None


def _iter_rows(text: str) -> list | None:
    """Parse a ledger text into a list of row objects, accepting a JSON array or JSONL.

    Returns the list, or None if neither shape parses (→ caller abstains). A whole-text
    JSON array is tried first; on failure each non-blank line is parsed as one object
    (JSONL), and ANY undecodable line makes the whole parse fail (a corrupt ledger is
    no signal, never a partial trusted read)."""
    stripped = text.strip()
    if not stripped:
        return None
    # 1. a single JSON array (or object) for the whole file.
    try:
        data = json.loads(stripped)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    # 2. JSONL — one object per line.
    rows: list = []
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            return None  # a corrupt line poisons the whole ledger — abstain
        rows.append(obj)
    return rows or None


def _fields_match(row: dict, expect: dict) -> tuple[bool, str]:
    """Does the ledger `row` confirm every expected field? Returns `(matched, mismatch)`.

    A subset match, string-compared (the row's JSON values and the expectation are both
    normalized to `str`, so `4200`==`"4200"`). `matched` is True iff every `k` in
    `expect` is present in `row` and equal; `mismatch` names the first failing field
    (for the operator-facing refute detail), or "" when matched."""
    for k, v in expect.items():
        if k not in row:
            return False, f"{k} (absent from the row)"
        if str(row[k]) != str(v):
            return False, f"{k} (claimed {v!r}, recorded {str(row[k])!r})"
    return True, ""


def _decode(data: bytes) -> str | None:
    """Decode ledger bytes as UTF-8, or None on failure (binary garbage → abstain)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _join(cwd: str | None, path: str) -> str:
    """Join a working-tree path under the configured cwd (or the process cwd)."""
    import os
    return os.path.join(cwd, path) if cwd else path


# ---------------------------------------------------------------------------
# CLI — `python -m dos.drivers.provider_ledger '<ref>@<ledger>#<expect>'`
# witnesses whether the different-principal ledger confirms the external effect.
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="dos.drivers.provider_ledger",
        description=__doc__.splitlines()[0],
    )
    ap.add_argument(
        "subject",
        help="'<ref>@<ledger>#<expect>' — e.g. "
             "'ch_1a2b@receipts.jsonl@HEAD#inline:status=succeeded,amount=4200' or "
             "'SMabc@source:twilio_poll:SMabc#source:twilio_poll:SMabc'",
    )
    ap.add_argument("--cwd", default=None, help="repo root to run git in (default: process cwd)")
    ap.add_argument("--json", action="store_true", help="machine-readable verdict")
    args = ap.parse_args(argv)

    src = ProviderLedgerEvidenceSource(cwd=args.cwd)
    facts = src.gather(args.subject, None)
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
