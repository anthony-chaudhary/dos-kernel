"""The canonical caught-lie example — one declared story, every copy pinned.

`dos._demo_story` declares the example every first-touch surface tells: the
agent claims "the login endpoint (AUTH1)" and "the password reset (AUTH2)"
shipped; one commit backs the first, nothing backs the second. The EXECUTABLE
demo (`dos quickstart`) interpolates the module, so it cannot drift. But the
prose/script/figure surfaces — the README parts, `docs/QUICKSTART.md`, the
`examples/demo/` scripts and visuals, the plan-doc example, the fleet-framework
fixture, the CI smoke step — each carry a hand-written COPY in their own genre,
and before this test nothing caught a miscopy (found live: the fleet fixture
spelled the subject with its own verb, and the plan example gave AUTH2 a
*different* feature than the story gives it — the same phase token meaning two
things on two teaching surfaces).

So this test scans every tracked text file and pins the two facts that must
agree across genres, while leaving genre-local prose free:

  1. **One subject.** Any concrete ship-stamp spelling of the shipped phase
     (``AUTH1: <word>…``) is exactly the canonical ``COMMIT_SUBJECT``.
     Elisions (``AUTH1: …``, ``AUTH1: <message>``) stay legal. A file whose
     *point* is divergence registers in the exemption table with its reason.
  2. **Features bind to their phases.** "the login endpoint" may only pair
     with AUTH1, "the password reset" only with AUTH2 — both in the
     parenthetical story form and at line level (a line naming AUTH2 next to
     the login endpoint without AUTH1 in sight is a swapped-token miscopy).

Propagation model: a NEW file that quotes the example automatically falls
under the same scan — copying the canonical strings is the whole protocol,
and this test is what catches a bad copy. The literals here are deliberately
HARDCODED (not read from the module) for the two-witness discipline: editing
the canonical story is a deliberate two-place change, never a silent one.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from dos import _demo_story as story

_REPO = Path(__file__).resolve().parents[1]

# The canonical literals, re-pinned INDEPENDENTLY of the module (see docstring).
_SUBJECT = "AUTH1: ship the login endpoint"
_SHIPPED = ("the login endpoint", "AUTH1")
_UNSHIPPED = ("the password reset", "AUTH2")

# Tracked files allowed to spell an AUTH1 stamp that is NOT the canonical
# subject — each with the adjudicated reason. Keep this short and reasoned.
_SUBJECT_EXEMPT = {
    "tests/test_skill_pack_generic.py":
        "a FOREIGN-repo fixture with its own fiction — the skill pack must be "
        "proven on repos that are NOT shaped like the canonical demo",
}

# Suffixes the scan skips (binary or generated-binary artifacts).
_SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".woff", ".woff2",
    ".ttf", ".zip", ".gz", ".whl", ".pyc", ".gguf",
}

# Load-bearing surfaces the example MUST appear on — the guard-the-guard: if a
# refactor renames the tokens everywhere, the scan above would pass vacuously,
# so pin that the canonical subject is still actually told where newcomers look.
# (src/dos/cli.py is absent on purpose: it interpolates dos._demo_story instead
# of quoting, which test_quickstart_consumes_the_declared_story pins.)
_MUST_TELL_THE_STORY = (
    "README.md",
    "docs/readme/10_try-it.md",
    "docs/QUICKSTART.md",
    "examples/demo/verify_demo.sh",
    "examples/demo/verify_demo.ps1",
    "examples/demo/verify_demo.tape",
    "examples/demo/verify_visual.html",
    "examples/demo/verify-moment.svg",
    "examples/plans/example-plan.md",
    "examples/fleet_frameworks/_fixture.py",
    ".github/workflows/ci.yml",
)


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    files = []
    for line in out.splitlines():
        p = _REPO / line
        if p.suffix.lower() in _SKIP_SUFFIXES or not p.is_file():
            continue
        files.append(p)
    return files


def _rel(p: Path) -> str:
    return p.relative_to(_REPO).as_posix()


# ---------------------------------------------------------------------------
# the module is the registry: self-pin + the executable demo consumes it
# ---------------------------------------------------------------------------

def test_demo_story_constants_are_the_canonical_example():
    """The declared story equals the canonical literals (the second witness)."""
    assert story.PLAN == "AUTH"
    assert (story.SHIPPED_FEATURE, story.SHIPPED_PHASE) == _SHIPPED
    assert (story.UNSHIPPED_FEATURE, story.UNSHIPPED_PHASE) == _UNSHIPPED
    assert story.COMMIT_SUBJECT == _SUBJECT
    # The claim line carries both named features, each bound to its phase.
    assert f"{_SHIPPED[0]} ({_SHIPPED[1]})" in story.AGENT_CLAIM
    assert f"{_UNSHIPPED[0]} ({_UNSHIPPED[1]})" in story.AGENT_CLAIM


def test_quickstart_consumes_the_declared_story():
    """`cmd_quickstart` interpolates `dos._demo_story` rather than re-spelling
    the story — the structural half of single-sourcing (the executable demo
    cannot drift from the registry because it IS the registry, rendered)."""
    cli_text = (_REPO / "src" / "dos" / "cli.py").read_text(encoding="utf-8")
    assert "_demo_story" in cli_text, (
        "cmd_quickstart no longer imports dos._demo_story — the executable demo "
        "has forked from the canonical-example registry."
    )


# ---------------------------------------------------------------------------
# the scan: every tracked copy of the example agrees on the invariants
# ---------------------------------------------------------------------------

def test_every_ship_stamp_spelling_is_the_canonical_subject():
    """Invariant 1: a concrete `AUTH1: <word>` spelling anywhere in the tracked
    tree is the canonical subject, byte for byte. Elisions don't count;
    adjudicated foreign fixtures are exempt by name, with a reason."""
    stamp = re.compile(re.escape(story.SHIPPED_PHASE) + r":\s+[A-Za-z]")
    violations: list[str] = []
    for p in _tracked_files():
        rel = _rel(p)
        if rel in _SUBJECT_EXEMPT:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in stamp.finditer(text):
            got = text[m.start():m.start() + len(_SUBJECT)]
            if got != _SUBJECT:
                snippet = text[m.start():m.start() + 60].splitlines()[0]
                violations.append(f"{rel}: {snippet!r}")
    assert not violations, (
        "non-canonical spellings of the canonical example's ship stamp "
        f"(expected {_SUBJECT!r} everywhere; fix the copy or register the file "
        f"in _SUBJECT_EXEMPT with a reason):\n  " + "\n  ".join(violations)
    )


def test_features_bind_to_their_phases():
    """Invariant 2: the story's feature names never pair with the wrong phase
    token — neither in the parenthetical story form (a feature followed by the
    other phase's token) nor at line level (the other phase's token on a line
    with a feature whose own token is nowhere on it)."""
    pairs = (_SHIPPED, _UNSHIPPED)
    violations: list[str] = []
    for p in _tracked_files():
        rel = _rel(p)
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "AUTH" not in text:
            continue
        # (a) the parenthetical story form must use the right token.
        for feature, phase in pairs:
            for m in re.finditer(re.escape(feature) + r"\s*\((AUTH\d+)\)", text):
                if m.group(1) != phase:
                    violations.append(f"{rel}: {m.group(0)!r}")
        # (b) line-level cross-binding: the OTHER phase token next to a feature
        # is only legal when the feature's own token is also present (as in the
        # claim line, which names both). Skipped for .jsonl evidence captures —
        # there a physical line is a whole document (e.g. the Go parity corpus
        # packs an entire git-log window into one case line), so "same line"
        # carries no sentence-level meaning and a corpus REGENERATION could red
        # this gate off unrelated commit subjects. Invariants (a) and the
        # subject pin still apply to those files in full.
        if p.suffix.lower() == ".jsonl":
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            for (feature, phase), (_, other) in (pairs, pairs[::-1]):
                if feature in line and other in line and phase not in line:
                    violations.append(f"{rel}:{i}: {line.strip()[:80]!r}")
    assert not violations, (
        "the canonical example's feature names are paired with the wrong phase "
        "token (a swapped/renamed miscopy):\n  " + "\n  ".join(violations)
    )


def test_the_story_is_still_told_where_newcomers_look():
    """Guard the guard: the canonical subject actually appears on every
    load-bearing newcomer surface, so the scan above cannot pass vacuously.
    If you MOVE the example to a new surface, update this roster."""
    missing = []
    for rel in _MUST_TELL_THE_STORY:
        p = _REPO / rel
        if not p.is_file():
            missing.append(f"{rel} (file gone)")
            continue
        if _SUBJECT not in p.read_text(encoding="utf-8", errors="ignore"):
            missing.append(rel)
    assert not missing, (
        f"the canonical subject {_SUBJECT!r} is no longer told on these "
        "newcomer surfaces:\n  " + "\n  ".join(missing)
    )
