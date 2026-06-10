"""dos.drivers.memory_recall — the recall-honesty driver (docs/103).

> *The kernel is the part that doesn't believe the agents. Memory is the agent
> we forgot to stop believing.*

An agent's persistent file-based memory is a fleet of self-narrating workers
writing shared state, read back later without anyone checking whether what they
wrote is still true (docs/103). A memory file says "FIXED in cli.py:1000"; the
code moved two commits ago; the memory didn't; and at recall the claim is
injected into context wearing the authority of a fact. That is the founding DOS
problem, pointed inward — so the fix is not a new principle, it is the existing
syscalls re-aimed at the memory store, by a CONSUMER that lives outside the
kernel (the same one-way arrow as `dos_mcp` and `scripts/`: this module
`import dos`, nothing under `src/dos/*.py` imports it).

What it does
============

Given one memory file, it (1) parses the frontmatter (STRUCTURE — trusted, per
docs/102 clause-1), (2) extracts the body's *checkable claims* and the POLARITY
each asserts (is this code/commit claimed PRESENT, ABSENT, or SHIPPED?), (3)
re-probes each claim against ground truth NOW (the working tree + git ancestry,
never the memory's word), and (4) returns ONE closed `RecallVerdict`:

    RECALL_FRESH        — every checkable claim still confirms → safe to inject
    RECALL_STALE        — ≥1 checkable claim is contradicted by ground truth →
                          withhold or route to the operator, never inject as fact
    RECALL_UNVERIFIABLE — names nothing checkable, every probe abstained, or the
                          memory is a preference/positioning note (opinion-typed)

The single highest-leverage move (docs/103 §3.3): recall gains a way to say
"no, or not sure" instead of only "yes."

The kernel-discipline split (the `liveness.classify` shape, lifted)
===================================================================

`classify_recall(RecallEvidence) -> RecallVerdict` is PURE — no git, no file
read, no clock. All I/O (the file read, the frontmatter parse, every git/grep
probe, the wall clock) happens in `gather()` at the caller boundary, exactly as
`liveness`'s evidence-gather happens in the `dos liveness` CLI and `arbitrate`'s
reads happen outside `arbitrate()`. That is what lets the verdict be
replay-tested on frozen fixtures, away from anything needing a live repo.

`liveness.classify` is the *shape template*, NOT a call dependency: it answers
"is a RUN moving," a category error for "is a CLAIM still valid," and would need
a date→SHA map the kernel does not expose. The recall path consumes `oracle`
(the ONE narrow `PLAN_PHASE` case) + `git_delta` + a comment-aware working-tree
grep; it never calls `liveness.classify`.

Fail-safe is ABSTAIN, never AGREE
=================================

Every probe that cannot run (git missing, no anchor, ambiguous) returns
`ProbeStatus.UNKNOWN`, which is EXCLUDED from the `checkable` set. `RECALL_FRESH`
requires *every* checkable claim to affirmatively CONFIRM — so a probe that
failed can never satisfy FRESH, only fail to lift the verdict off
UNVERIFIABLE. The dangerous direction (launder an unchecked claim into FRESH) is
structurally impossible, the same property `run_judge`'s fail-to-abstain gives
the JUDGE rung.

What this does NOT claim (docs/103 §6)
======================================

It does not make memory trustworthy — it makes recall HONEST about un-trust. It
does not catch a lie shape-identical to truth (a memory could name a real commit
and mis-describe it; this raises the forgery cost to "a real artifact of the
right shape," no further). It governs the READ path only; *what deserves a
memory* is a write-side policy that stays a policy. And it NEVER auto-deletes —
STALE routes a *proposal* (archive or update), never an `rm`, the record-and-
propose stance of the watchdog (docs/101) and `liveness` (docs/82).

`RECALL_DRIFTING` (the 4th token docs/103 §3.2 names) is RESERVED, not shipped:
a true "the named region moved since the memory's date" verdict needs a
path/date-scoped git-delta the kernel does not yet expose (`git_delta`'s reads
are SHA-anchored), and approximating it makes a false-STALE machine on hot
files. v1 ships the three verdicts gatherable with today's surfaces; DRIFTING
waits for a path-scoped delta reader (the refusal-to-ship-an-uncomputable-verdict
discipline, the same one that keeps `verify` honest with `source="none"`).
"""

from __future__ import annotations

import enum
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dos import config as _config
from dos import git_delta, oracle

# git probes are boundary I/O — cap them so a pathological repo can't hang a
# recall sweep. Matches the 10s bound `git_delta` and the doctor calls use.
_GIT_TIMEOUT_S = 10


# ---------------------------------------------------------------------------
# The closed vocabularies — str-valued so they round-trip a CLI/JSON token
# without a lookup table (the `Liveness` / `gate_classify.Verdict` pattern).
# ---------------------------------------------------------------------------


class ClaimKind(str, enum.Enum):
    """What KIND of checkable thing a body claim names → which probe answers it."""

    SHA = "SHA"                # a 7- or 40-hex commit id → git ancestry probe
    CODE_TOKEN = "CODE_TOKEN"  # a literal source token (import line / flag) claimed
                               #   present in a named file → comment-aware grep
    PATH = "PATH"              # a bare repo-relative path → stat / glob
    PLAN_PHASE = "PLAN_PHASE"  # an explicit "docs/NN_*-plan <phase> SHIPPED" →
                               #   oracle.is_shipped (the ONE narrow correct use)
    OPINION = "OPINION"        # prose with no checkable referent → never probed

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Polarity(str, enum.Enum):
    """What the memory ASSERTS about the artifact NOW.

    The signal without which "X is broken" (stale once fixed) is
    indistinguishable from "X shipped" (fresh once shipped). `ProbeStatus` is
    computed RELATIVE to the polarity, which is what makes the dogfood case STALE
    on its merits.
    """

    ASSERTS_PRESENT = "ASSERTS_PRESENT"  # "cli.py does `from dos.drivers import watchdog`"
    ASSERTS_ABSENT = "ASSERTS_ABSENT"    # "the import is gone / now a comment"
    ASSERTS_SHIPPED = "ASSERTS_SHIPPED"  # "FIXED in a7a145d / SHIPPED 2600110"
    NEUTRAL = "NEUTRAL"                  # a bare reference, no truth-assertion attached

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class ProbeStatus(str, enum.Enum):
    """The CLOSED outcome of ONE probe against the working tree NOW. Fail-safe atom."""

    CONFIRMS = "CONFIRMS"        # ground truth AGREES with the claim's polarity
    CONTRADICTS = "CONTRADICTS"  # ground truth DISAGREES with the claim's polarity
    UNKNOWN = "UNKNOWN"          # the probe could not run (git absent / no anchor) — NO signal

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Recall(str, enum.Enum):
    """The closed recall verdict — three states (DRIFTING reserved, see module doc)."""

    RECALL_FRESH = "RECALL_FRESH"                # every checkable claim CONFIRMS → inject
    RECALL_STALE = "RECALL_STALE"                # ≥1 checkable claim CONTRADICTS → withhold / route
    RECALL_UNVERIFIABLE = "RECALL_UNVERIFIABLE"  # opinion-typed, nothing checkable, or all-UNKNOWN

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# Frontmatter types that are unfalsifiable by construction — a preference or a
# positioning take, not a checkable fact. Trust the STRUCTURE (the file is a
# well-formed feedback note), never adjudicate the CONTENT (docs/103 §4 clause-1,
# §6 bullet-1). Checked FIRST in classify_recall so an incidental path inside an
# opinion can't drag it onto the verifiable ladder.
_OPINION_TYPES = frozenset({"user", "feedback"})


# ---------------------------------------------------------------------------
# The evidence dataclasses — frozen values handed to the pure classifier.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryClaim:
    """One checkable artifact extracted from a memory body + the polarity it asserts.

    PURE data — produced by `extract_claims` from the body string alone, before
    any probe runs. `line_hint` is advisory ONLY and is never probed: line
    numbers drift constantly (every edit above shifts them), so verifying a
    `file:line` literally would manufacture false-STALE on every line move. The
    claim binds to the FILE + the TOKEN, not the line.
    """

    raw: str                 # the literal matched text ("from dos.drivers import watchdog", "a7a145d")
    kind: ClaimKind
    polarity: Polarity
    target_file: str = ""    # repo-relative file the claim is about (CODE_TOKEN/PATH), or a plan id (PLAN_PHASE)
    line_hint: int = 0       # advisory only — NEVER probed

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "kind": self.kind.value,
            "polarity": self.polarity.value,
            "target_file": self.target_file,
            "line_hint": self.line_hint,
        }


@dataclass(frozen=True)
class ClaimEvidence:
    """One claim + the result of re-probing it against ground truth now."""

    claim: MemoryClaim
    status: ProbeStatus
    ground_truth: str = ""   # operator-facing proof ("removed by a7a145d ('resolve the watchdog…')")
    source: str = ""         # which rung answered: "grep" | "ancestry" | "oracle" | "stat" | "none"

    def to_dict(self) -> dict:
        return {
            "claim": self.claim.to_dict(),
            "status": self.status.value,
            "ground_truth": self.ground_truth,
            "source": self.source,
        }


@dataclass(frozen=True)
class FrontmatterFacts:
    """The trusted STRUCTURE of a memory — parsed once, never re-verified."""

    name: str = ""
    description: str = ""
    mem_type: str = ""       # user | feedback | project | reference
    node_type: str = ""
    origin_session: str = ""
    body_offset: int = 0     # char offset where the body starts (excludes a frontmatter SHA from claims)

    @staticmethod
    def empty() -> "FrontmatterFacts":
        return FrontmatterFacts()


@dataclass(frozen=True)
class RecallEvidence:
    """Everything `classify_recall()` needs for ONE memory, gathered by the CALLER.

    The `ProgressEvidence` analogue: frozen, I/O-free to construct in a test, the
    sole input to the pure verdict. `now_ms` is carried for the JSON consumer and
    age framing; the verdict never reads a clock.
    """

    mem_name: str
    mem_type: str                                     # frontmatter type — the verifiability gate
    body_date_iso: Optional[str] = None               # the self-declared "as of" date; advisory in v1
    evidences: tuple[ClaimEvidence, ...] = ()
    now_ms: int = 0

    @property
    def checkable(self) -> tuple[ClaimEvidence, ...]:
        """The claims that carry verdict weight: not opinions, and actually probed.

        An UNKNOWN-status claim is EXCLUDED — a probe that could not run gets no
        vote, so it can never satisfy FRESH (the abstain-not-agree property).
        """
        return tuple(
            e for e in self.evidences
            if e.claim.kind is not ClaimKind.OPINION
            and e.status is not ProbeStatus.UNKNOWN
        )


@dataclass(frozen=True)
class RecallVerdict:
    """The single verdict `classify_recall()` returns, with the evidence echoed.

    `culprit` is the deciding CONTRADICTS claim on a STALE verdict (so a surface
    can lead with WHY), or None. `to_dict` is the JSON shape the CLI `--json` /
    MCP tool emit — legible distrust: the operator sees not just STALE but the
    ground-truth proof behind it.
    """

    verdict: Recall
    reason: str
    culprit: Optional[ClaimEvidence]
    evidence: RecallEvidence

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "memory": self.evidence.mem_name,
            "type": self.evidence.mem_type,
            "culprit": self.culprit.to_dict() if self.culprit is not None else None,
            "claims": [e.to_dict() for e in self.evidence.evidences],
        }


# ---------------------------------------------------------------------------
# The PURE classifier — no I/O. The faithful liveness.classify lift.
# ---------------------------------------------------------------------------


def classify_recall(ev: RecallEvidence) -> RecallVerdict:
    """Classify one memory's recall verdict from already-gathered evidence. PURE.

    First-match-wins ladder, worst-checkable-claim-wins fold:

      0. UNVERIFIABLE (structural) — an opinion-typed memory (user/feedback). Trust
         STRUCTURE, never adjudicate CONTENT. Checked FIRST and unconditionally so
         an incidental path inside a preference note can't drag it onto the ladder.
      1. UNVERIFIABLE (empty) — names no re-checkable artifact, or every probe
         abstained (all UNKNOWN). Nothing to bind against ground truth.
      2. STALE — ANY checkable claim CONTRADICTS (worst-wins, NOT majority — the
         "9 fresh + 1 stale = still STALE" rule that defeats the launder).
      3. FRESH — every checkable claim CONFIRMS.
    """
    # 0. Opinion-typed → unfalsifiable by construction (§4 clause-1, §6 bullet-1).
    if ev.mem_type in _OPINION_TYPES:
        return RecallVerdict(
            Recall.RECALL_UNVERIFIABLE,
            f"frontmatter type={ev.mem_type or '?'}: a preference/positioning note is "
            f"unfalsifiable by construction — surface it, mark it unverifiable, never "
            f"present it as a verified fact",
            None,
            ev,
        )

    checkable = ev.checkable

    # 1. Named nothing checkable, or every probe abstained (§7 second clause).
    if not checkable:
        return RecallVerdict(
            Recall.RECALL_UNVERIFIABLE,
            "names no re-checkable artifact (or every probe abstained) — there is "
            "nothing to bind against ground truth; surface it tagged unverifiable",
            None,
            ev,
        )

    # 2. STALE — any checkable claim contradicted. Worst-wins, first in extraction
    #    order (deterministic). The §1/§7 dogfood cell.
    contradicted = [e for e in checkable if e.status is ProbeStatus.CONTRADICTS]
    if contradicted:
        worst = contradicted[0]
        return RecallVerdict(
            Recall.RECALL_STALE,
            f"ground truth disagrees with {worst.claim.raw!r} "
            f"({worst.claim.kind.value}/{worst.claim.polarity.value}, via "
            f"{worst.source or 'none'}): {worst.ground_truth} — withhold or route to "
            f"decisions, do not inject as fact",
            worst,
            ev,
        )

    # 3. FRESH — every checkable claim affirmatively confirmed.
    return RecallVerdict(
        Recall.RECALL_FRESH,
        f"all {len(checkable)} checkable claim(s) confirmed against the working tree "
        f"— the memory's evidence is intact, safe to inject",
        None,
        ev,
    )


# ---------------------------------------------------------------------------
# Frontmatter parse — boundary I/O lives in `gather`; this is a pure string fold.
# ---------------------------------------------------------------------------

_FM_DELIM = "---"


def parse_frontmatter(text: str) -> FrontmatterFacts:
    """Parse the leading `--- … ---` YAML block. Fail-safe → empty facts.

    Trusts the STRUCTURE: `yaml.safe_load` over the block, lifting the handful of
    fields the verdict needs. A torn/absent frontmatter, or missing PyYAML, yields
    empty facts (the memory is then treated as un-typed → its body is the only
    signal), never a crash — the defensive posture every kernel loader uses.
    """
    if not text.startswith(_FM_DELIM):
        return FrontmatterFacts.empty()
    # Find the closing delimiter on its own line.
    end = text.find("\n" + _FM_DELIM, len(_FM_DELIM))
    if end == -1:
        return FrontmatterFacts.empty()
    block = text[len(_FM_DELIM):end]
    # The body starts after the closing "---" line.
    close_line_end = text.find("\n", end + 1 + len(_FM_DELIM))
    body_offset = (close_line_end + 1) if close_line_end != -1 else len(text)
    try:
        import yaml  # type: ignore
    except ImportError:
        return FrontmatterFacts(body_offset=body_offset)
    try:
        data = yaml.safe_load(block) or {}
    except Exception:
        return FrontmatterFacts(body_offset=body_offset)
    if not isinstance(data, dict):
        return FrontmatterFacts(body_offset=body_offset)
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    return FrontmatterFacts(
        name=str(data.get("name") or ""),
        description=str(data.get("description") or ""),
        mem_type=str(meta.get("type") or "").strip().lower(),
        node_type=str(meta.get("node_type") or ""),
        origin_session=str(meta.get("originSessionId") or ""),
        body_offset=body_offset,
    )


# ---------------------------------------------------------------------------
# The body-confession guard — strip the file's own RECALL_* banner before
# extraction so the verdict is computed from a re-check, NEVER parroted from a
# hand-written self-annotation. Without this the driver would read its own prior
# verdict + the fixing SHA out of the banner and self-confirm circularly.
# ---------------------------------------------------------------------------

_RECALL_TOKEN = re.compile(r"RECALL_(?:FRESH|STALE|UNVERIFIABLE|DRIFTING)")


def strip_recall_banner(body: str) -> str:
    """Remove a leading block-quote region that contains a RECALL_* token. PURE.

    A memory may carry a hand-written `> **⚠ RECALL_STALE — re-verified … FIXED in
    a7a145d**` banner (the dogfood file does). If extraction read the verdict + the
    fixing SHA out of THAT, it would self-confirm. We drop any contiguous leading
    block of `>`-quoted lines (and the blank lines between/after them) when that
    block names a RECALL_* token, so the verdict is computed from the ORIGINAL
    audit prose below it. Only a LEADING banner is stripped — a `>`-quote deeper in
    the body that happens to mention a token is left alone (it is real content).
    """
    lines = body.splitlines(keepends=True)
    i = 0
    # Skip leading blank lines.
    while i < len(lines) and not lines[i].strip():
        i += 1
    start = i
    saw_token = False
    # Consume a contiguous run of block-quote lines (and blank lines embedded in it).
    while i < len(lines):
        stripped = lines[i].lstrip()
        if stripped.startswith(">"):
            if _RECALL_TOKEN.search(lines[i]):
                saw_token = True
            i += 1
        elif not lines[i].strip():
            # a blank line: part of the banner only if more quote follows
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and lines[j].lstrip().startswith(">"):
                i = j
            else:
                break
        else:
            break
    if not saw_token:
        return body
    return "".join(lines[i:]) if i > start else body


# ---------------------------------------------------------------------------
# The extractor — PURE: regex over the body string. Conservative by design; an
# ambiguous match is extracted NEUTRAL (→ UNKNOWN probe → no verdict weight),
# never guessed into a CONFIRMS/CONTRADICTS.
# ---------------------------------------------------------------------------

# A git SHA in prose: the two REAL git widths only (7 short, 40 full). Dropping
# the 8–39 band kills session-id fragments (e.g. "7d0fa2aa" is 8 → not matched).
# `(?=[0-9a-f]*[0-9])` requires at least one digit, dropping all-letter "hex
# words" (facade / decade / defaced / faced). Bounded by a non-alphanumeric on
# both sides so a substring of a longer token never matches — but a BACKTICK is a
# valid delimiter, NOT part of the token, so the common backticked citation form
# `` `a7a145d` `` / `` `9866239` `` matches (the lookbehind excludes alphanumerics
# only, never the backtick that wraps the most reliable SHA references).
_SHA = re.compile(
    r"(?<![0-9a-zA-Z])(?=[0-9a-f]*[0-9])(?:[0-9a-f]{7}|[0-9a-f]{40})(?![0-9a-zA-Z])"
)
# A ship verb tight before a SHA flips a bare hex into an ASSERTS_SHIPPED claim.
_SHIP_VERB = re.compile(r"\b(?:fixed|shipped|landed|cut|committed|merged)\b", re.I)

# A backticked import statement or flag claimed about a file — the dogfood spine.
_IMPORT_TOK = re.compile(r"`(from [\w.]+ import [\w, ]+|import [\w.]+)`")
_FLAG_TOK = re.compile(r"`(--[\w][\w-]*)`")
# A file:line ref (also matched on its own as a weaker file anchor).
_FILE_REF = re.compile(
    r"\b((?:[\w./-]+/)?[\w-]+\.(?:py|toml|yaml|yml|md|cfg|txt|sh|ini)):(\d{1,6})\b"
)
# A bare repo-relative source path (no line). Anchored on a small set of
# top-level dir names so an arbitrary "x/y.md" fragment in prose isn't a claim.
# The left boundary forbids a preceding word-char OR slash, so a FOREIGN-repo
# prefix (`job/scripts/ship_oracle.py`, the reference userland app) does NOT
# silently strip to `scripts/ship_oracle.py` and get probed against THIS repo — a
# backtick or whitespace is still a valid left edge, so a backticked path matches.
_BARE_PATH = re.compile(
    r"(?<![\w/])((?:src|tests|docs|scripts|examples|benchmark|spikes)/[\w./-]+"
    r"\.(?:py|toml|yaml|yml|md|cfg|txt|sh|ini))\b"
)

# Imports too generic to bind to a file-specific present/absent claim — they
# appear as both code AND comment in nearly every module, so the comment-aware
# grep cannot disambiguate a real "X still imports this" claim from incidental
# prose. The dogfood `from dos.drivers import watchdog` is specific and NOT here.
_GENERIC_IMPORTS = frozenset({
    "import dos", "import os", "import re", "import sys", "import enum",
    "import json", "import time", "import subprocess",
})

# Presence / absence cues scanned in a window around a code-token match.
_PRESENT_CUE = re.compile(
    r"\b(?:do|does|did|imports?|import|contains?|has|have|still|carr(?:y|ies)|"
    r"is in|lives? in|present)\b",
    re.I,
)
# Absence cues for a PATH/CODE_TOKEN polarity flip. Each NAMES a removal or a
# comment-only state directly — the unbounded `is now` / `only inside` tokens
# were dropped (they matched any "is now …" / "only inside …" clause about a
# DIFFERENT noun, the window-bleed false-STALE source).
_ABSENT_CUE = re.compile(
    r"\b(?:gone|removed|deleted|no longer|now a comment|now only a comment|"
    r"only inside a comment|inside a comment|inside a docstring|not a static import|"
    r"dropped|stripped|eliminated)\b",
    re.I,
)
# A STRONG present/creation cue: prose that ties THIS repo to the artifact ("we
# wrote/committed/added X"). A bare PATH mention is a REFERENCE, not a claim — only
# a strong cue makes it an ASSERTS_PRESENT claim about this repo. This is the
# signal (prose, not filesystem) that separates a TRUE "we created docs/_business/X"
# from a FALSE "the job repo has docs/_business/Y".
_STRONG_PRESENT_CUE = re.compile(
    r"\b(?:written|created|wrote|added|committed|shipped|landed|deliverables?|"
    r"refreshed|now exists|sketches|introduces?|emit(?:s|ted)?|built|ships?)\b",
    re.I,
)

_WINDOW = 90  # ±chars scanned around a match for polarity cues


def _window(body: str, start: int, end: int) -> str:
    return body[max(0, start - _WINDOW): min(len(body), end + _WINDOW)]


# A PATH polarity cue must sit TIGHT to the path (the path is the verb's object),
# not anywhere in the sentence — a wider window lets a cue for a different noun
# bleed ("the artifact being BUILT is …" near a referenced path; "crashed on the
# DELETED PDFs" near a present file). These are deliberately small.
_CUE_LEFT = 40   # chars before the path a creation/removal cue may sit in
_CUE_RIGHT = 35  # chars after the path a trailing cue ("… already sketches") may sit in


def _clause_window(body: str, start: int, end: int) -> str:
    """The TIGHT window for PATH polarity — the path's immediate neighbourhood only.

    A cue describing a DIFFERENT noun in the same sentence must not flip the path
    (the `stamp.py` / `release_context.py` / `agent-ops` window-bleed bugs). The
    window is a small span hugging the path, additionally clipped at any clause
    break (`.`/`;`/`—`/`)`/newline) on EITHER side so only the path's own clause
    votes. Now that the path PROBE is git-grounded (created-here-then-removed is
    decided by history, not prose), this cue only has to catch a creation/removal
    verb that is grammatically ABOUT the path — so a tight window is correct, not
    lossy.
    """
    lo = max(0, start - _CUE_LEFT)
    # left clause boundary: the last break char before the path within the span
    left = lo
    for brk in (". ", "; ", "—", "\n", ") ", ": "):
        j = body.rfind(brk, lo, start)
        if j != -1:
            left = max(left, j + len(brk))
    hi = min(len(body), end + _CUE_RIGHT)
    seg = body[left:hi]
    rel_end = end - left
    for brk in (". ", "; ", " — ", ")", "\n", ","):
        i = seg.find(brk, rel_end)
        if i != -1:
            seg = seg[:i]
    return seg


def _nearest_file(body: str, pos: int) -> tuple[str, int]:
    """The file ref closest to `pos` (a code token's home), as (repo_path, line)."""
    best: tuple[str, int] = ("", 0)
    best_dist = 10 ** 9
    for m in _FILE_REF.finditer(body):
        dist = abs(m.start() - pos)
        if dist < best_dist:
            best_dist = dist
            best = (m.group(1), int(m.group(2)))
    return best


def extract_claims(body: str, mem_type: str) -> list[MemoryClaim]:
    """Extract the checkable claims + their polarity from a memory body. PURE.

    Conservative: only `ASSERTS_*`-polarity claims carry verdict weight; a NEUTRAL
    match is extracted but its probe abstains (UNKNOWN). A body that yields zero
    non-OPINION matches contributes an empty list → classify_recall rung-1
    UNVERIFIABLE (the §7 "names nothing checkable" floor, realized as the absence
    of any extraction).
    """
    claims: list[MemoryClaim] = []
    seen: set[tuple[str, str]] = set()

    def _add(raw: str, kind: ClaimKind, pol: Polarity, target: str = "", line: int = 0) -> None:
        key = (kind.value, raw)
        if key in seen:
            return
        seen.add(key)
        claims.append(MemoryClaim(raw=raw, kind=kind, polarity=pol, target_file=target, line_hint=line))

    # --- CODE_TOKEN: a backticked import statement claimed about a nearby file.
    for m in _IMPORT_TOK.finditer(body):
        tok = m.group(1)
        if tok in _GENERIC_IMPORTS:
            continue  # too generic to bind to a file-specific present/absent claim
        win = _window(body, m.start(), m.end())
        target, line = _nearest_file(body, m.start())
        if _ABSENT_CUE.search(win):
            pol = Polarity.ASSERTS_ABSENT
        elif _PRESENT_CUE.search(win):
            pol = Polarity.ASSERTS_PRESENT
        else:
            pol = Polarity.NEUTRAL
        _add(tok, ClaimKind.CODE_TOKEN, pol, target, line)

    # --- CODE_TOKEN: a backticked flag claimed present in a file.
    for m in _FLAG_TOK.finditer(body):
        tok = m.group(1)
        win = _window(body, m.start(), m.end())
        target, line = _nearest_file(body, m.start())
        if not target:
            continue  # a flag with no nearby file has no probe anchor → skip
        if _ABSENT_CUE.search(win):
            pol = Polarity.ASSERTS_ABSENT
        elif _PRESENT_CUE.search(win):
            pol = Polarity.ASSERTS_PRESENT
        else:
            pol = Polarity.NEUTRAL
        _add(tok, ClaimKind.CODE_TOKEN, pol, target, line)

    # --- SHA: ASSERTS_SHIPPED only when a ship verb sits TIGHT before the SHA
    #     ("FIXED in `a7a145d`", "SHIPPED commit 9866239") — not anywhere in a wide
    #     window, where a verb about a DIFFERENT subject bleeds (a branch-tip SHA in
    #     a parenthetical list near "master re-landed …" is NOT a ship claim). A SHA
    #     that is a parenthetical branch annotation — `(b571fc6)` — is a bare
    #     reference (NEUTRAL → the probe abstains), never an ASSERTS_SHIPPED claim.
    for m in _SHA.finditer(body):
        sha = m.group(0)
        in_parens = m.start() > 0 and body[m.start() - 1] == "(" \
            and m.end() < len(body) and body[m.end()] == ")"
        pre = body[max(0, m.start() - _CUE_LEFT): m.start()]
        # cut the pre-window at a clause break so a far verb can't bleed in
        for brk in (". ", "; ", "\n", ", ", ") "):
            j = pre.rfind(brk)
            if j != -1:
                pre = pre[j + len(brk):]
        ships = bool(_SHIP_VERB.search(pre)) and not in_parens
        pol = Polarity.ASSERTS_SHIPPED if ships else Polarity.NEUTRAL
        _add(sha, ClaimKind.SHA, pol, "")

    # --- PATH: a bare repo-relative source path. NEUTRAL by DEFAULT — a bare path
    #     mention is a REFERENCE, not a claim ("the plan in docs/77", "the host's
    #     scripts/", "an example like src/foo.py"). It becomes ASSERTS_PRESENT only
    #     on an explicit in-clause creation/ship cue ("we wrote/committed/added X"),
    #     ASSERTS_ABSENT only on an in-clause removal cue. The clause-bounded window
    #     stops a cue for a DIFFERENT noun from bleeding onto the path.
    for m in _BARE_PATH.finditer(body):
        p = m.group(1)
        win = _clause_window(body, m.start(), m.end())
        if _ABSENT_CUE.search(win):
            pol = Polarity.ASSERTS_ABSENT
        elif _STRONG_PRESENT_CUE.search(win):
            pol = Polarity.ASSERTS_PRESENT
        else:
            pol = Polarity.NEUTRAL
        _add(p, ClaimKind.PATH, pol, p)

    return claims


def extract_body_date(body: str, fm: FrontmatterFacts) -> Optional[str]:
    """The memory's self-declared "as of" date (ISO `YYYY-MM-DD`), if any. Advisory.

    Decoupled from the verdict in v1 (the RECALL_DRIFTING axis it would feed is
    reserved, not shipped). Carried for the JSON consumer + a future date-scoped
    delta reader. We prefer the FIRST date in the body (the write stamp) and never
    use the file mtime (forgeable, rejected in docs/95) or git-blame (the store is
    not under git here).
    """
    m = re.search(r"\b(20\d\d-[01]\d-[0-3]\d)\b", body)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# The probes — BOUNDARY I/O. Each is fail-safe → UNKNOWN (never a guessed AGREE).
# ---------------------------------------------------------------------------


def _grep_code_vs_comment(text: str, literal: str) -> tuple[int, int]:
    """(code_hits, comment_hits) for `literal` in `text`.

    A line-based heuristic with a triple-quote span tracker: an occurrence counts
    as a COMMENT hit if its line (after lstrip) starts with a hash, or the line
    falls inside an open triple-quoted docstring span, or the occurrence sits after
    a hash on its line. Otherwise it is a CODE hit. This is the FRESH/STALE hinge for the
    dogfood case: a token that survives only inside a docstring is present-as-
    comment, NOT present-as-code, so an ASSERTS_PRESENT claim about it is
    contradicted.

    Deliberately simple (no full tokenizer) — it errs toward calling an ambiguous
    line CODE, which is the conservative direction for an ASSERTS_PRESENT claim (it
    biases toward CONFIRMS/FRESH, never toward a false STALE).
    """
    code = comment = 0
    in_triple: str = ""  # empty, or whichever triple-quote opener is currently open
    for line in text.splitlines():
        stripped = line.lstrip()
        # Track triple-quote spans (count balanced toggles on a line).
        if not in_triple:
            line_is_comment = stripped.startswith("#")
        else:
            line_is_comment = True
        # Find occurrences on this line.
        idx = line.find(literal)
        while idx != -1:
            if in_triple or line_is_comment:
                comment += 1
            else:
                # an inline-# before the occurrence makes it a comment hit
                hash_pos = line.find("#")
                if 0 <= hash_pos < idx:
                    comment += 1
                else:
                    code += 1
            idx = line.find(literal, idx + 1)
        # Update the triple-quote state AFTER counting this line's hits.
        for q in ('"""', "'''"):
            n = line.count(q)
            if n == 0:
                continue
            if in_triple == q:
                # closes (odd count) or stays (even)
                if n % 2 == 1:
                    in_triple = ""
            elif not in_triple:
                if n % 2 == 1:
                    in_triple = q
    return code, comment


def _pickaxe_fix(literal: str, repo_file: str, root: Path) -> str:
    """`git log -S<literal> -- <file>` newest match → "shortsha ('subject')". Fail-safe → "".

    The forensic evidence the §7 litmus demands: when an ASSERTS_PRESENT code token
    is no longer present-as-code, this names the commit that removed it — obtained
    BY RE-CHECK (git pickaxe), never parroted from the memory body.
    """
    try:
        raw = subprocess.run(
            ["git", "log", "-S", literal, "-n", "1", "--pretty=format:%h\t%s", "--", repo_file],
            cwd=str(root), capture_output=True, text=True, check=False, timeout=_GIT_TIMEOUT_S,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if raw.returncode != 0 or not raw.stdout.strip():
        return ""
    parts = raw.stdout.splitlines()[0].split("\t", 1)
    if len(parts) != 2:
        return ""
    return f"{parts[0]} ({parts[1]!r})"


# Where to look for a bare-basename file ref (the memory writes `cli.py`, the repo
# path is `src/dos/cli.py`). Searched in order; the first dir containing a UNIQUE
# match wins. Kept small + repo-shaped so an ambiguous basename abstains rather
# than guessing the wrong file.
_BASENAME_SEARCH_DIRS = ("src/dos", "src/dos_mcp", "src", "tests", "docs", "scripts")


def _resolve_repo_file(target: str, root: Path) -> tuple[Optional[Path], str]:
    """A repo-relative or bare-basename file ref → (real file, status). BOUNDARY I/O.

    Returns `(path, "found")` on a unique resolution; `(None, "absent")` when the
    file genuinely does not exist; `(None, "ambiguous")` when a bare basename
    matches more than one repo file. The two None cases are DISTINCT: an absent
    ASSERTS_PRESENT file is a contradiction, but an ambiguous basename must ABSTAIN
    (it cannot bind to the right file) — collapsing them would manufacture
    false-STALE on a common basename.
    """
    verbatim = root / target
    if verbatim.is_file():
        return verbatim, "found"
    if "/" in target or "\\" in target:
        return None, "absent"  # an explicit path that doesn't exist
    base = Path(target).name
    hits: list[Path] = []
    for d in _BASENAME_SEARCH_DIRS:
        cand = root / d / base
        if cand.is_file():
            hits.append(cand)
    if len(hits) == 1:
        return hits[0], "found"
    if not hits:
        return None, "absent"
    return None, "ambiguous"


def _probe_code_token(claim: MemoryClaim, root: Path) -> ClaimEvidence:
    """Re-grep the NAMED file for the literal token NOW, comment-aware."""
    if not claim.target_file:
        return ClaimEvidence(claim, ProbeStatus.UNKNOWN, "no file anchor for the token", "none")
    f, fstatus = _resolve_repo_file(claim.target_file, root)
    if fstatus == "ambiguous":
        # A bare basename matching >1 file: cannot bind to the right one → abstain.
        return ClaimEvidence(claim, ProbeStatus.UNKNOWN,
                             f"file {claim.target_file!r} is ambiguous (matches several repo "
                             f"files); cannot bind the token to one", "none")
    if f is None:  # genuinely absent
        if claim.polarity is Polarity.ASSERTS_PRESENT:
            return ClaimEvidence(claim, ProbeStatus.CONTRADICTS,
                                 f"named file {claim.target_file} is absent", "stat")
        if claim.polarity is Polarity.ASSERTS_ABSENT:
            return ClaimEvidence(claim, ProbeStatus.CONFIRMS,
                                 f"named file {claim.target_file} is absent", "stat")
        return ClaimEvidence(claim, ProbeStatus.UNKNOWN,
                             f"named file {claim.target_file} is absent; no assertion to bind", "stat")
    rel = f.relative_to(root).as_posix()
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ClaimEvidence(claim, ProbeStatus.UNKNOWN, f"could not read {rel}", "none")
    code_hits, comment_hits = _grep_code_vs_comment(text, claim.raw)
    present_as_code = code_hits > 0

    if claim.polarity is Polarity.ASSERTS_PRESENT:
        if present_as_code:
            return ClaimEvidence(claim, ProbeStatus.CONFIRMS,
                                 f"still present as code in {rel} "
                                 f"({code_hits} occurrence(s))", "grep")
        fix = _pickaxe_fix(claim.raw, rel, root)
        detail = f"no longer present as code in {rel}"
        if comment_hits:
            detail += "; only inside a comment/docstring now"
        if fix:
            detail += f"; removed by {fix}"
        return ClaimEvidence(claim, ProbeStatus.CONTRADICTS, detail, "grep")

    if claim.polarity is Polarity.ASSERTS_ABSENT:
        if present_as_code:
            return ClaimEvidence(claim, ProbeStatus.CONTRADICTS,
                                 f"still present as code in {rel}", "grep")
        return ClaimEvidence(claim, ProbeStatus.CONFIRMS,
                             f"absent as code in {rel}", "grep")

    # NEUTRAL — a token reference with no truth-assertion: nothing to confirm.
    return ClaimEvidence(claim, ProbeStatus.UNKNOWN, "no truth-assertion on the token", "none")


def _probe_sha(claim: MemoryClaim, root: Path) -> ClaimEvidence:
    """Ancestry, not mere existence: is the SHA on HEAD's history NOW?

    `git merge-base --is-ancestor` (not bare `cat-file -e`): a trust kernel checks
    ancestry, not existence — an orphaned/dropped commit "exists" but is not on the
    trunk the memory's "SHIPPED" claim asserts. Only an ASSERTS_SHIPPED SHA carries
    weight; a NEUTRAL SHA reference abstains (UNKNOWN — a bare hex is not a claim).
    """
    if claim.polarity is not Polarity.ASSERTS_SHIPPED:
        return ClaimEvidence(claim, ProbeStatus.UNKNOWN, "bare SHA reference, no ship assertion", "none")
    sha = claim.raw
    try:
        r = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, "HEAD"],
            cwd=str(root), capture_output=True, check=False, timeout=_GIT_TIMEOUT_S,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
    except (OSError, subprocess.TimeoutExpired):
        return ClaimEvidence(claim, ProbeStatus.UNKNOWN, "git unavailable", "none")
    # `--is-ancestor` exits 0=ancestor, 1=not, 128=bad object (unknown sha).
    if r.returncode == 0:
        subj = ""
        for c in git_delta.recent_commits(300, root=root):
            if c["sha"].startswith(sha[:7]) or sha.startswith(c["sha"]):
                subj = c["subject"]
                break
        gt = f"{sha} is an ancestor of HEAD" + (f" ({subj!r})" if subj else "")
        return ClaimEvidence(claim, ProbeStatus.CONFIRMS, gt, "ancestry")
    if r.returncode == 1:
        return ClaimEvidence(claim, ProbeStatus.CONTRADICTS,
                             f"{sha} is NOT an ancestor of HEAD (orphaned, dropped, or rebased away)",
                             "ancestry")
    # 128 / anything else: the object is unknown to this repo — can't bind a
    # SHIPPED claim against a SHA git doesn't know. Abstain (it may be a different
    # repo's SHA quoted in prose), never a false CONTRADICTS.
    return ClaimEvidence(claim, ProbeStatus.UNKNOWN,
                         f"{sha} is unknown to this repo (cannot verify ancestry)", "none")


def _path_deleting_commit(repo_file: str, root: Path) -> str:
    """The commit that DELETED `repo_file` here → "shortsha ('subject')", or "".

    `git log --diff-filter=D -1 -- <file>` names the commit that removed a path
    that once lived in this tree (a relocation/strip). The path-analogue of
    `_pickaxe_fix`. Fail-safe → "" (git absent / never tracked / still present).
    """
    try:
        raw = subprocess.run(
            ["git", "log", "--diff-filter=D", "-n", "1", "--pretty=format:%h\t%s", "--", repo_file],
            cwd=str(root), capture_output=True, text=True, check=False, timeout=_GIT_TIMEOUT_S,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if raw.returncode != 0 or not raw.stdout.strip():
        return ""
    parts = raw.stdout.splitlines()[0].split("\t", 1)
    return f"{parts[0]} ({parts[1]!r})" if len(parts) == 2 else ""


def _path_ever_tracked(repo_file: str, root: Path) -> Optional[bool]:
    """Did `repo_file` EVER exist in this repo's history? True/False, or None on error.

    `git log --all -- <file>` over the served repo. The ground-truth signal that
    separates a path that was CREATED-HERE-then-removed (a real STALE, the memory's
    claim no longer holds) from one that was NEVER HERE (a foreign/illustrative
    reference — the job repo's `scripts/`, the strategy repo's `docs/_business/`, an
    `src/foo.py` example — which the driver must ABSTAIN on, not contradict). This
    replaces fragile prose-cue guessing with what git actually records.
    """
    try:
        raw = subprocess.run(
            ["git", "log", "--all", "-n", "1", "--pretty=format:%h", "--", repo_file],
            cwd=str(root), capture_output=True, text=True, check=False, timeout=_GIT_TIMEOUT_S,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if raw.returncode != 0:
        return None
    return bool(raw.stdout.strip())


def _probe_path(claim: MemoryClaim, root: Path) -> ClaimEvidence:
    """A bare repo-relative path: does it exist NOW, grounded in git history?

    The honest rule rests on GIT, not prose cues (the driver's own "ground truth
    over narration" discipline). For an absent file:
      * git shows it WAS here and was deleted → STALE, evidence = the deleting
        commit (a created-here-then-relocated/stripped path; the memory's "it's
        here" no longer holds).
      * git shows it was NEVER here → a foreign/illustrative reference (the job
        repo's `scripts/`, the strategy repo's `docs/_business/`, an `src/foo.py`
        example) → ABSTAIN (UNKNOWN), UNLESS an explicit ASSERTS_PRESENT cue claims
        THIS repo created it (then a never-here file genuinely contradicts the claim).
      * git unavailable → fall back to the existence check (fail-safe).
    A present file CONFIRMS an ASSERTS_PRESENT / CONTRADICTS an ASSERTS_ABSENT.
    """
    p = root / claim.target_file
    rel = claim.target_file
    if p.exists():
        if claim.polarity is Polarity.ASSERTS_PRESENT:
            return ClaimEvidence(claim, ProbeStatus.CONFIRMS, f"{rel} exists", "stat")
        if claim.polarity is Polarity.ASSERTS_ABSENT:
            # A path asserted ABSENT that actually EXISTS is almost always cue-bleed
            # — an absence word ("crashed on the DELETED PDFs") describing a nearby
            # noun, not this path. A bare path is rarely the grammatical subject of
            # a removal. Abstain rather than emit a false STALE (the trust-preserving
            # choice); a genuine "X was removed" where X is gone still CONFIRMS below.
            return ClaimEvidence(claim, ProbeStatus.UNKNOWN,
                                 f"{rel} exists, but an absence cue near it likely described "
                                 f"another noun — abstaining rather than contradict", "stat")
        return ClaimEvidence(claim, ProbeStatus.UNKNOWN, "no assertion on the path", "none")

    # Absent now — let git history decide created-here-then-removed vs never-here.
    ever = _path_ever_tracked(rel, root)
    if ever is None:
        # git unavailable: fall back to the bare existence verdict (fail-safe).
        if claim.polarity is Polarity.ASSERTS_PRESENT:
            return ClaimEvidence(claim, ProbeStatus.CONTRADICTS, f"{rel} is gone", "stat")
        if claim.polarity is Polarity.ASSERTS_ABSENT:
            return ClaimEvidence(claim, ProbeStatus.CONFIRMS, f"{rel} is gone", "stat")
        return ClaimEvidence(claim, ProbeStatus.UNKNOWN, "no assertion on the path", "none")

    if ever:
        # Was here, now gone → relocated/stripped. A real "no longer in this repo".
        delc = _path_deleting_commit(rel, root)
        gt = f"{rel} was in this repo and is now gone" + (f"; removed by {delc}" if delc else "")
        if claim.polarity is Polarity.ASSERTS_ABSENT:
            return ClaimEvidence(claim, ProbeStatus.CONFIRMS, gt, "git")
        # PRESENT or NEUTRAL: the memory points at a path no longer here.
        if claim.polarity is Polarity.ASSERTS_PRESENT:
            return ClaimEvidence(claim, ProbeStatus.CONTRADICTS, gt, "git")
        return ClaimEvidence(claim, ProbeStatus.UNKNOWN, f"{rel}: " + gt + " (no assertion)", "git")

    # NEVER here → a foreign/illustrative reference. Abstain unless the prose
    # explicitly claims THIS repo created it (then never-here contradicts).
    if claim.polarity is Polarity.ASSERTS_PRESENT:
        return ClaimEvidence(claim, ProbeStatus.CONTRADICTS,
                             f"{rel} was never in this repo, yet the memory asserts it was "
                             f"created here", "git")
    return ClaimEvidence(claim, ProbeStatus.UNKNOWN,
                         f"{rel} was never in this repo — a foreign/illustrative reference, "
                         f"not a claim about this tree", "git")


def _probe_plan_phase(claim: MemoryClaim, cfg: "_config.SubstrateConfig") -> ClaimEvidence:
    """The ONE narrow correct use of `oracle.is_shipped`. source='none' → UNKNOWN.

    A `source="none"` answer is the oracle ABSTAINING (no registry row, no matching
    commit), NOT disagreeing — so it maps to UNKNOWN, never CONTRADICTS. Only a real
    registry/grep `shipped=False` is a disagreement. This is the false-STALE-by-
    abstention guard.
    """
    plan, phase = claim.target_file, claim.raw
    v = oracle.is_shipped(plan, phase, cfg=cfg)
    if v.shipped:
        return ClaimEvidence(claim, ProbeStatus.CONFIRMS,
                             f"oracle: shipped via {v.source} ({v.sha or '-'})", "oracle")
    if v.source == "none":
        return ClaimEvidence(claim, ProbeStatus.UNKNOWN,
                             "oracle abstained (no registry row, no matching commit)", "oracle")
    return ClaimEvidence(claim, ProbeStatus.CONTRADICTS, f"oracle: not shipped (via {v.source})", "oracle")


def probe(claim: MemoryClaim, cfg: "_config.SubstrateConfig") -> ClaimEvidence:
    """Route a claim to its probe. BOUNDARY I/O; each rung is fail-safe → UNKNOWN."""
    root = cfg.paths.root
    if claim.kind is ClaimKind.CODE_TOKEN:
        return _probe_code_token(claim, root)
    if claim.kind is ClaimKind.SHA:
        return _probe_sha(claim, root)
    if claim.kind is ClaimKind.PATH:
        return _probe_path(claim, root)
    if claim.kind is ClaimKind.PLAN_PHASE:
        return _probe_plan_phase(claim, cfg)
    return ClaimEvidence(claim, ProbeStatus.UNKNOWN, "opinion / no probe", "none")


# ---------------------------------------------------------------------------
# The boundary gatherer + public API.
# ---------------------------------------------------------------------------


def gather(path: Path, *, cfg: "_config.SubstrateConfig", now_ms: int) -> RecallEvidence:
    """Read one memory file and gather all its evidence. ALL I/O lives here.

    The boundary, exactly like `cmd_liveness`'s evidence-gather: read the file,
    parse the frontmatter (structure), strip a self-annotation banner, extract the
    body's claims, probe each against ground truth, freeze a `RecallEvidence`. The
    pure `classify_recall` is then called on the result.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return RecallEvidence(mem_name=path.stem, mem_type="", now_ms=now_ms)
    fm = parse_frontmatter(text)
    body = strip_recall_banner(text[fm.body_offset:])
    date_iso = extract_body_date(body, fm)
    claims = extract_claims(body, fm.mem_type)
    evidences = tuple(probe(c, cfg) for c in claims)
    return RecallEvidence(
        mem_name=fm.name or path.stem,
        mem_type=fm.mem_type,
        body_date_iso=date_iso,
        evidences=evidences,
        now_ms=now_ms,
    )


def default_store(cfg: "_config.SubstrateConfig") -> Optional[Path]:
    """Best-effort guess at this workspace's agent-memory dir. None if not found.

    The memory store is a host/harness convention, not a DOS path, so it is NOT in
    the config seam. We probe the documented Claude Code layout
    (`~/.claude/projects/<slugified-workspace>/memory`) where the slug replaces
    path separators and the drive colon with `-` (a Windows workspace at
    `<drive>:\a\b` slugifies to `<drive>--a-b`). Returns the dir if it exists, else
    None — a caller with no store passes `--store` explicitly. Never hardcodes a
    user path.
    """
    root = cfg.paths.root.resolve()
    slug = str(root).replace(":", "-").replace("\\", "-").replace("/", "-")
    # collapse a leading separator-dash so "<drive>:\a\b" → "<drive>--a-b"
    slug = re.sub(r"-{3,}", "--", slug).strip("-")
    cand = Path.home() / ".claude" / "projects" / f"C--{slug.split('-', 1)[-1]}" / "memory"
    # Try the exact documented slug first, then a couple of tolerant variants.
    candidates = [
        Path.home() / ".claude" / "projects" / slug / "memory",
        cand,
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def _resolve_store(store: Optional[str], cfg: "_config.SubstrateConfig") -> Path:
    if store:
        return Path(store)
    d = default_store(cfg)
    if d is None:
        raise ValueError(
            "could not locate the agent-memory store for this workspace; pass "
            "--store <dir> (the recall driver does not assume a memory layout — "
            "it is a harness convention, not a DOS path)")
    return d


def _resolve_memory_path(name_or_path: str, store: Path) -> Path:
    """A frontmatter `name`, a bare slug, or a direct path → the memory file."""
    p = Path(name_or_path)
    if p.is_file():
        return p
    cand = store / (name_or_path if name_or_path.endswith(".md") else f"{name_or_path}.md")
    if cand.is_file():
        return cand
    raise ValueError(f"no memory named {name_or_path!r} under {store}")


def recall_one(
    name_or_path: str,
    *,
    cfg: "Optional[_config.SubstrateConfig]" = None,
    store: Optional[str] = None,
    now_ms: Optional[int] = None,
) -> RecallVerdict:
    """Re-verify ONE memory at recall time → its closed RecallVerdict.

    `name_or_path` is a frontmatter `name` / slug (resolved against the store) or a
    direct path. `store` overrides the memory dir; default via `default_store`.
    `now_ms` is injected here at the boundary (clock never read inside the verdict).
    """
    cfg = _config.ensure(cfg)
    nm = now_ms if now_ms is not None else int(time.time() * 1000)
    store_dir = _resolve_store(store, cfg)
    path = _resolve_memory_path(name_or_path, store_dir)
    ev = gather(path, cfg=cfg, now_ms=nm)
    return classify_recall(ev)


def sweep(
    *,
    cfg: "Optional[_config.SubstrateConfig]" = None,
    store: Optional[str] = None,
    now_ms: Optional[int] = None,
) -> list[RecallVerdict]:
    """Re-verify EVERY memory in the store → a list of verdicts (STALE first).

    The whole-store projection: `verify` fanned out over a memory store instead of
    a plan registry (docs/103 §5). Read-only. Ranked STALE → UNVERIFIABLE → FRESH
    so the rows that need attention lead.
    """
    cfg = _config.ensure(cfg)
    nm = now_ms if now_ms is not None else int(time.time() * 1000)
    store_dir = _resolve_store(store, cfg)
    out: list[RecallVerdict] = []
    for path in sorted(store_dir.glob("*.md")):
        if path.name == "MEMORY.md":
            continue  # the index, not a memory record
        ev = gather(path, cfg=cfg, now_ms=nm)
        out.append(classify_recall(ev))
    rank = {Recall.RECALL_STALE: 0, Recall.RECALL_UNVERIFIABLE: 1, Recall.RECALL_FRESH: 2}
    out.sort(key=lambda v: (rank.get(v.verdict, 9), v.evidence.mem_name))
    return out


# ---------------------------------------------------------------------------
# The agent-facing gloss — lives in the DRIVER, not dos.interpret. RECALL_* is
# driver vocabulary the kernel does not know; putting it in the kernel's
# presentation seam would import a driver's closed set into the kernel layer.
# Single-sourced here; both the CLI (`--explain`) and the MCP tool call it.
# ---------------------------------------------------------------------------


def interpret(verdict: dict) -> str:
    """One line on what a recall verdict means for the next action. PURE presentation."""
    v = str(verdict.get("verdict", "")).strip().upper()
    cul = verdict.get("culprit") or {}
    gt = f" ({cul.get('ground_truth')})" if isinstance(cul, dict) and cul.get("ground_truth") else ""
    if v == Recall.RECALL_FRESH.value:
        return ("FRESH — every checkable claim in this memory still confirms against the "
                "working tree, so its evidence is intact. Safe to rely on. (Still its own "
                "claim, not proof of good judgment — only that what it points at hasn't moved.)")
    if v == Recall.RECALL_STALE.value:
        return ("STALE — git/the working tree DISAGREES with this memory now: something it "
                "asserts is present/fixed/shipped no longer matches the code" + gt + ". Do NOT "
                "act on its instruction. Surface it as a stale claim to archive or update; "
                "never present it as fact.")
    if v == Recall.RECALL_UNVERIFIABLE.value:
        return ("UNVERIFIABLE — this memory names no concrete artifact to re-check (or it is a "
                "preference/positioning note), so it is an opinion, not a checkable fact. Fine "
                "to surface, but MARK it unfalsifiable — never dress it as something recall confirmed.")
    return ("UNKNOWN recall verdict — treat the memory as unverified: present it hedged, not as "
            "a fact, until a real check classifies it.")


# ---------------------------------------------------------------------------
# Routing (opt-in) — cross-post a non-FRESH verdict to `dos decisions` via the
# EXISTING OP_REFUSE journal source + a host-declared RECALL_* reason token. NO
# kernel edit: `decisions._from_lane_journal` already lifts any OP_REFUSE with a
# reason_class into the queue. A recall refusal IS a refusal ("I decline to
# surface this memory as fact"), so OP_REFUSE is the honest carrier — NOT a fake
# OP_HALT (a stale memory is not a hung process). Never auto-deletes: it records
# a PROPOSAL (archive or update), the record-and-propose stance (§6).
# ---------------------------------------------------------------------------


def route(verdicts: list[RecallVerdict], *, cfg: "Optional[_config.SubstrateConfig]" = None) -> int:
    """Append an OP_REFUSE for each non-FRESH verdict. Returns the count routed.

    Requires the host to have DECLARED `RECALL_STALE` / `RECALL_UNVERIFIABLE` in
    `dos.toml [reasons]` (the `dos check-reason` discipline — never auto-declare an
    unknown token). An undeclared token raises, loudly, rather than emit drift.
    """
    from types import SimpleNamespace

    from dos import lane_journal

    cfg = _config.ensure(cfg)
    reg = cfg.reasons
    routed = 0
    for v in verdicts:
        if v.verdict is Recall.RECALL_FRESH:
            continue
        token = v.verdict.value
        if reg.get(token) is None:
            raise ValueError(
                f"cannot route: reason token {token!r} is not declared in this workspace. "
                f"Add it to dos.toml [reasons] (category STALE_CLAIM) before --route, the "
                f"same way every refusal reason is declared (dos man wedge).")
        slug = re.sub(r"[^a-z0-9]+", "-", v.evidence.mem_name.lower()).strip("-")
        carrier = SimpleNamespace(
            reason=f"{token}: {v.reason}",
            lane=f"memory:{slug}",
        )
        entry = lane_journal.refuse_entry(
            carrier,
            owner="memory-recall",
            run_id=v.evidence.mem_name,
            reason_class=token,
        )
        lane_journal.append(entry, cfg.paths.lane_journal)
        routed += 1
    return routed
