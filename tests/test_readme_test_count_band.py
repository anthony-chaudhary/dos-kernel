"""The test-count honesty gate.

The README front door advertises a test count ("5,600+ tests"). In a project
whose whole thesis is *don't trust a self-reported number — check it against
the evidence*, a hardcoded count that has silently rotted past the real one is
the most on-the-nose self-own available: a skeptic runs `pytest --co` on launch
day and posts the diff.

So this gate does to that number exactly what `dos verify` does to an agent's
"done": it re-derives the ground truth (the live collected count) and refuses
to believe the advertised band unless the artifacts back it. The band is a
*floor claim* ("N+"), so the rule is:

    floor <= advertised_band <= collected_count

i.e. the page never claims MORE tests than exist (the dishonest direction), and
the advertised band hasn't fallen so far behind reality that "5,600+" is
absurd for a suite of (say) 9,000. A generous ceiling on the *staleness* keeps
a concurrent loop that adds a few hundred tests from reddening the suite — the
band is meant to be bumped at release time, not per-commit.

Source-tree-only: an installed wheel ships neither the parts nor the full test
corpus, so the module skips when either is absent.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_TESTS_DIR = _REPO / "tests"
_FRONT_DOOR = _REPO / "docs" / "readme" / "00_front-door.md"

pytestmark = pytest.mark.skipif(
    not (_FRONT_DOOR.exists() and _TESTS_DIR.is_dir()),
    reason="README parts / test corpus only exist in the source tree",
)

# Matches the advertised band, e.g. "5,600+ tests" — captures the integer.
_BAND_RE = re.compile(r"([\d,]+)\+\s*tests")

# How far the advertised floor may lag the live count before the band is
# considered stale enough to fail. A release bumps the band; this slack lets a
# burst of new tests between releases land green.
_MAX_STALENESS = 2_000


def _advertised_floor() -> int:
    text = _FRONT_DOOR.read_text(encoding="utf-8")
    m = _BAND_RE.search(text)
    assert m is not None, (
        f"no '<N>+ tests' band found in {_FRONT_DOOR.name} — the front door must "
        "advertise a test count as a floor claim (e.g. '5,600+ tests')"
    )
    return int(m.group(1).replace(",", ""))


def _collected_count() -> int:
    """The live number of collected tests — the ground truth the band claims."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--co", str(_TESTS_DIR)],
        cwd=_REPO,
        capture_output=True,
        text=True,
    )
    out = proc.stdout + proc.stderr
    m = re.search(r"(\d+)\s+tests?\s+collected", out)
    assert m is not None, (
        "could not parse a collected-test count from `pytest --co` output:\n"
        + out[-2000:]
    )
    return int(m.group(1))


def test_advertised_test_count_band_is_backed_by_the_suite() -> None:
    """The README's '<N>+ tests' band must be a TRUE floor on the live count,
    and not so stale it understates reality by more than the release slack."""
    floor = _advertised_floor()
    collected = _collected_count()

    # The dishonest direction: claiming more tests than exist.
    assert floor <= collected, (
        f"README front door advertises {floor:,}+ tests but only {collected:,} "
        "are collected — the band claims MORE than the suite has. Lower the band "
        "in docs/readme/00_front-door.md and rebuild (python scripts/build_readme.py)."
    )

    # The staleness direction: the band has fallen too far behind reality.
    assert collected - floor <= _MAX_STALENESS, (
        f"README front door advertises {floor:,}+ tests but {collected:,} are "
        f"collected — the band is stale by {collected - floor:,} (> {_MAX_STALENESS:,}). "
        "Bump the band in docs/readme/00_front-door.md and rebuild "
        "(python scripts/build_readme.py)."
    )
