"""The truth-pointer: which kernel deciders are now Go-canonical (docs/385 TP1).

The ratchet inversion (docs/385 §3). The Go core began life as an *optional
accelerator* graded against Python (docs/100/124/125): Python was the spec, the
differential corpus was regenerated FROM the Python decider, and the Go binary
had to match it byte-for-byte or it did not ship. The strongly-typed mandate
(CLAUDE.md, 2026-06-17) reverses that default — Go is the substrate; Python
retreats to a named list of seams. The reversal happens *per decider*, on the
docs/385 §3 protocol (PORT → SOAK → FLIP), and THIS module records the FLIP
state: for each decider, which engine is canonical (the source of truth the
parity corpus pins the other side against).

TP1 (docs/385 §7) flips the longest-soaked surface — the hook deciders
(`pretool`/`posttool`/`marker`/`stop`) and the ports they already needed
(`admission`, `overlap`, `tree`, `claim_extract`, the dialect transcoder, proc
liveness). These are byte-exact and benchmarked (docs/125/270); the flip is a
pointer move, not new code. After the flip:

  * **Go is canonical** for these deciders — new behavior is authored Go-first,
    and the parity corpus is pinned to the *Go* decider's output (the Go
    `TestParityCorpus` asserts ``go == corpus`` on every CI run).
  * **Python is the corpus-pinned shadow** — the in-process Python decider
    survives as the docs/100 fallback (a pure-Python install with no native
    binary still decides, never breaks — docs/385 §6: the fallback stands until
    the Python copy is deleted), but it is no longer the SPEC. A divergence is
    resolved by fixing Python to match the Go-canonical corpus, not the reverse.

This is the §8 litmus made executable: *the truth-pointer is single and known per
decider*. `dos doctor` reads this registry and reports which deciders are
Go-canonical, so the flip state is an OBSERVED fact, not a doc sentence.

Layering. Pure stdlib (kernel layer 1) — no I/O, no host names, no workspace
path; it describes the package's OWN deciders. Go is the *substrate* here, not a
vendor — the same standing the litmus grants git / ``GitBackend`` (the
ground-truth engine, not a provider name). The registry is a CLOSED, NAMED set:
adding a decider is a reviewable flip gated by its own soak (like adding a refusal
reason or a lane), never a default an edit reaches for (docs/385 §8).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TruthPointer:
    """One decider's canonical-engine record (the docs/385 §3 flip state).

    ``canonical`` is ``"go"`` once a decider has FLIPped (the corpus pins the
    other side against Go) and ``"python"`` while Python is still its spec — the
    standing default for every decider NOT in the named set. ``soak`` names the
    shipped evidence the flip rests on (doc refs); ``shadow`` records the
    surviving non-canonical side (``"python-fallback"`` while the docs/100
    fallback stands, ``"none"`` once the Python copy is deleted at the end of the
    retreat).
    """

    decider: str
    canonical: str
    phase: str
    soak: tuple[str, ...]
    shadow: str


# The hook-decider cluster TP1 flips to Go-canonical (docs/385 §4.0 / §7). Each
# entry's ``soak`` names the shipped, benchmarked evidence (docs/125 build,
# docs/270 benchmarks) the flip rests on; ``shadow`` records that the Python
# decider survives as the docs/100 fallback (docs/385 §6) — pinned against the
# Go-canonical corpus, not the spec. CLOSED + named: a new entry is a reviewed
# flip, not a default.
_FLIPPED: tuple[TruthPointer, ...] = (
    TruthPointer("pretool", "go", "TP1", ("docs/125", "docs/270"), "python-fallback"),
    TruthPointer("posttool", "go", "TP1", ("docs/125", "docs/270"), "python-fallback"),
    TruthPointer("marker", "go", "TP1", ("docs/125",), "python-fallback"),
    TruthPointer("stop", "go", "TP1", ("docs/125",), "python-fallback"),
    # The ports the hook deciders already needed to be byte-exact (docs/385 §4.0):
    # they ship inside go/internal/hook and ride the same parity corpus.
    TruthPointer("admission", "go", "TP1", ("docs/125",), "python-fallback"),
    TruthPointer("overlap", "go", "TP1", ("docs/125",), "python-fallback"),
    TruthPointer("tree", "go", "TP1", ("docs/125",), "python-fallback"),
    TruthPointer("claim_extract", "go", "TP1", ("docs/125",), "python-fallback"),
    TruthPointer("dialect", "go", "TP1", ("docs/271",), "python-fallback"),
    TruthPointer("proc_liveness", "go", "TP1", ("docs/125",), "python-fallback"),
    # docs/386 §6 — the account-switcher's PURE ranking core (the pick / serving-pool
    # / allocate-seats decider) ported to Go (``go/internal/account``) on the SAME
    # ratchet, pinned by ``go/internal/account/parity`` (a value-exact corpus driven
    # from the real Python switcher). It is a DRIVER-tier decider (CLAUDE.md layer 4),
    # not one of the §4 kernel cores, so it carries its own phase label (``386``), not
    # the hook cluster's ``TP1``. Its Python copy survives as the runtime + the
    # corpus-pinned shadow (no Go runtime delegation yet — that is the later
    # I/O-orchestration step, docs/385 §4.3); the flip here is the SPEC pointer + the
    # parity pin, the same shape every TP1 flip took.
    TruthPointer("account_pick", "go", "386", ("docs/386",), "python-fallback"),
    TruthPointer("serving_pool", "go", "386", ("docs/386",), "python-fallback"),
    TruthPointer("allocate_seats", "go", "386", ("docs/386",), "python-fallback"),
)


def go_canonical_deciders() -> tuple[TruthPointer, ...]:
    """The deciders whose canonical engine is Go (the FLIPped set), name-sorted
    for a stable `dos doctor` surface."""
    return tuple(
        sorted((p for p in _FLIPPED if p.canonical == "go"), key=lambda p: p.decider)
    )


def truth_pointer(decider: str) -> TruthPointer | None:
    """The flip record for ``decider``, or ``None`` if it has not flipped (Python
    is still its spec — the default for everything outside the named set)."""
    for p in _FLIPPED:
        if p.decider == decider:
            return p
    return None


def canonical_engine(decider: str) -> str:
    """The canonical engine for ``decider``: ``"go"`` once flipped, else
    ``"python"`` (the standing default for any decider not in the §8 named set)."""
    p = truth_pointer(decider)
    return p.canonical if p is not None else "python"
