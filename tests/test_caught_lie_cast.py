"""The README's first-screen caught-lie cast — recorded, embedded, pinned.

Issue #64's done-condition, as tests: the README's first screen renders an
animated caught-lie demo; the asset is committed; and the recording is
reproducible — the script that drives the recorded session is checked in so
the cast can be re-made when CLI output changes.

Three pins keep it true:

  1. **The recording is real and reproducible.** Running the checked-in
     generator (`scripts/build_caught_lie_cast.py --check`) re-drives the
     actual `dos` CLI in a throwaway repo and compares the committed SVG to
     the fresh recording byte for byte (the commit identity/dates are pinned,
     so the bytes are deterministic). A CLI output change reds this test, and
     the fix is to re-run the script — the cast can never silently rot into a
     hand-typed dramatization. (The same genre as `test_readme_assembly`.)
  2. **The cast sits in the README's first screen** — referenced by the
     assembled README before the loop hero, i.e. before any prose.
  3. **The cast carries the caught-lie moment + its robustness fallbacks** —
     the verbatim SHIPPED / NOT_SHIPPED verdict lines, the canonical commit
     subject, an accessibility description, and the `prefers-reduced-motion`
     fallback the repo's animated SVGs all ship.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "build_caught_lie_cast.py"
_SVG = _REPO / "docs" / "assets" / "caught-lie-cast.svg"
_README = _REPO / "README.md"
_FRONT_DOOR = _REPO / "docs" / "readme" / "00_front-door.md"


@pytest.mark.skipif(shutil.which("git") is None, reason="recording needs git")
def test_committed_cast_matches_a_fresh_recording_of_the_real_cli():
    """Pin 1: `--check` re-records the session (real `dos verify`, pinned
    dates) and byte-compares — the committed asset IS the CLI's output."""
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(_REPO),
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, (
        "the committed caught-lie cast no longer matches a fresh recording "
        "of the real CLI — re-run: python scripts/build_caught_lie_cast.py\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_cast_is_embedded_in_the_readme_first_screen():
    """Pin 2: the assembled README references the cast, and does so BEFORE
    the loop hero — the catch is shown before the reader scrolls or reads."""
    readme = _README.read_text(encoding="utf-8")
    cast_at = readme.find("docs/assets/caught-lie-cast.svg")
    hero_at = readme.find("docs/assets/loop-hero.svg")
    assert cast_at != -1, "README.md no longer embeds the caught-lie cast"
    assert hero_at != -1, "README.md no longer embeds the loop hero"
    assert cast_at < hero_at, (
        "the caught-lie cast must sit in the README's FIRST screen — above "
        "the loop hero (issue #64: show the catch before any prose)"
    )
    # The part is the source of truth the README is assembled from.
    assert "docs/assets/caught-lie-cast.svg" in _FRONT_DOOR.read_text(
        encoding="utf-8"
    ), "docs/readme/00_front-door.md no longer carries the cast embed"


def test_cast_carries_the_caught_lie_moment_and_its_fallbacks():
    """Pin 3: the verdict lines are present verbatim, the asset is animated,
    and the robustness fallbacks the repo's animated SVGs promise are real."""
    svg = _SVG.read_text(encoding="utf-8")
    assert svg.startswith("<svg "), "the cast is not an SVG"
    # The caught-lie moment, verbatim (the SHA is part of pin 1, not pin 3).
    assert "SHIPPED AUTH AUTH1" in svg
    assert "(via grep-subject)" in svg
    assert "NOT_SHIPPED AUTH AUTH2 (via none)" in svg
    assert "AUTH1: ship the login endpoint" in svg  # the canonical subject
    assert "CAUGHT" in svg
    # Animated, accessibly described, and fallback-safe.
    assert "@keyframes" in svg, "the cast is no longer animated"
    assert 'role="img"' in svg and "aria-label=" in svg
    assert "prefers-reduced-motion" in svg
    assert "animation-fill-mode" not in svg and "backwards" in svg, (
        "the intro must hide lines via `backwards` fill inside the "
        "animation shorthand so a stripped stylesheet shows the final frame"
    )
