"""scripts/dev.py — the contributor inner-loop runner (light pins).

This is a convenience runner that operates ON the package (it shells out to
pytest / ruff / the dos CLI); it imports nothing from the kernel hot path and the
kernel is unaware it exists. So the pins are deliberately thin: the subcommand
table exists, `fast` is wired to the `slow` marker, and the `slow` marker is
registered so `-m "not slow"` is a first-class contract (not a warning).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DEV = _ROOT / "scripts" / "dev.py"


def _load_dev():
    spec = importlib.util.spec_from_file_location("dos_dev_runner", _DEV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dev_script_exists():
    assert _DEV.is_file()


def test_dev_subcommand_table():
    dev = _load_dev()
    parser = dev.build_parser()
    # Pull the registered subcommands off the subparsers action.
    import argparse
    verbs: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            verbs |= set(action.choices)
    assert {"test", "fast", "lint", "verify-self", "all"} <= verbs


def test_fast_passes_not_slow_marker():
    """`dev.py fast` must constrain pytest to `-m "not slow"` — the marker is the
    whole point of the inner loop."""
    src = _DEV.read_text(encoding="utf-8")
    assert '"-m", "not slow"' in src


def test_slow_marker_is_registered():
    """The `slow` marker must be declared in pyproject so `-m "not slow"` does not
    warn (and a contributor's fast loop is a real contract, not a typo)."""
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "slow:" in pyproject
    assert "markers" in pyproject


def test_not_slow_selection_collects_some_and_deselects_some():
    """`-m "not slow"` is a real partition: it collects most of the suite and
    deselects the modules tagged slow (the poisoned-pool / install heavies)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "not slow", "--co", "-q",
         "tests/test_pickable.py", "tests/test_install_levels.py",
         "-p", "no:cacheprovider"],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    out = proc.stdout + proc.stderr
    # pickable collects; install_levels (slow) is deselected.
    assert "deselected" in out
