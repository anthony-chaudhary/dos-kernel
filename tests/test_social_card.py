"""The GitHub social-preview card — rendered from the real CLI, pinned.

The repo's social preview (the Slack/Discord/Twitter-X/LinkedIn unfurl, the
Open Graph card GitHub shows before anyone clicks) is a purpose-built
1280x640 asset, NOT the auto-generated default. These pins keep it honest the
same way `test_caught_lie_cast.py` pins the README cast:

  1. **The card is rendered from the real CLI and reproducible.** Running
     `scripts/build_social_card.py --check` re-drives the actual `dos verify`
     in a throwaway repo (commit identity/dates pinned -> deterministic) and
     byte-compares the committed SVG to the fresh render. A CLI output change
     reds this test; the fix is to re-run the script — the card can never
     silently rot into a hand-typed dramatization.
  2. **The card carries the money moment + is built for the social slot** —
     the verbatim SHIPPED / NOT_SHIPPED verdicts, the headline, the right
     viewBox (1280x640, GitHub's recommended 2:1), an accessible label, and —
     because the slot rasterizes a single still — NO animation to depend on.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "build_social_card.py"
_SVG = _REPO / "docs" / "assets" / "social-card.svg"


@pytest.mark.skipif(shutil.which("git") is None, reason="rendering needs git")
def test_committed_card_matches_a_fresh_render_of_the_real_cli():
    """Pin 1: `--check` re-renders the card (real `dos verify`, pinned dates)
    and byte-compares — the committed asset IS the CLI's output."""
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(_REPO),
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, (
        "the committed social card no longer matches a fresh render of the "
        "real CLI — re-run: python scripts/build_social_card.py\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_card_is_built_for_the_social_slot_and_carries_the_money_moment():
    """Pin 2: the right size for GitHub's slot, the verbatim verdicts, an
    accessible label, and STATIC (a single still is what gets rasterized)."""
    svg = _SVG.read_text(encoding="utf-8")
    assert svg.startswith("<svg "), "the card is not an SVG"
    # GitHub's recommended social-preview size — 1280x640, the 2:1 ratio.
    assert 'viewBox="0 0 1280 640"' in svg, (
        "the social card must be 1280x640 (GitHub's recommended size)"
    )
    # The money moment, verbatim from the CLI (the SHA is part of pin 1).
    assert "SHIPPED" in svg and "NOT_SHIPPED" in svg
    assert "via grep-subject" in svg and "via none" in svg
    assert "exit 0" in svg and "exit 1" in svg
    assert "dos verify AUTH AUTH1" in svg
    assert "dos verify AUTH AUTH2" in svg
    # The pitch + the install line a share card must carry.
    assert "pip install dos-kernel" in svg
    # Accessible, and STATIC — the social slot rasterizes one still, so the
    # card must not lean on animation to read.
    assert 'role="img"' in svg and "aria-label=" in svg
    assert "@keyframes" not in svg, (
        "the social card must be static — GitHub rasterizes a single frame"
    )
