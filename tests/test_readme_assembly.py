"""The README assembly drift gate.

README.md is generated: its source of truth is the section parts under
`docs/readme/` (one file per section, concatenated in filename order by
`scripts/build_readme.py`). That split exists so a section edit touches only
its own file — but a generated artifact invites the classic drift: someone
edits README.md directly, the parts silently fork, and the next regeneration
destroys the hand edit.

This gate pins the one fact that prevents that: README.md byte-equals the
assembly of the parts. The sibling of `tests/test_plugin_manifest.py` (which
pins the plugin's skills to their source) — same move, aimed at the front door.

Source-tree-only: an installed wheel ships neither the parts nor the script,
so the whole module skips when they're absent.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_BUILD_PY = _REPO / "scripts" / "build_readme.py"
_PARTS_DIR = _REPO / "docs" / "readme"
_README = _REPO / "README.md"

pytestmark = pytest.mark.skipif(
    not (_BUILD_PY.exists() and _PARTS_DIR.is_dir()),
    reason="README parts / build script only exist in the source tree",
)


def _load_builder():
    spec = importlib.util.spec_from_file_location("_build_readme", _BUILD_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_readme_matches_parts() -> None:
    """README.md is byte-identical to the assembled docs/readme/ parts."""
    mod = _load_builder()
    expected = mod.assemble(_PARTS_DIR)
    actual = _README.read_text(encoding="utf-8")
    assert actual == expected, (
        "README.md is out of sync with docs/readme/ — edit the part, then run: "
        "python scripts/build_readme.py"
    )


# README.md doubles as the PyPI long_description (`[project] readme` in
# pyproject.toml), and PyPI resolves relative targets against pypi.org — so a
# relative image/link 404s on the project page (the v0.22.0 upload shipped
# broken figures that way). Images go through raw.githubusercontent.com; links
# through github.com blob/tree URLs. In-page anchors (#…) are fine everywhere.
_RELATIVE_MD_LINK = re.compile(r"\]\((?!https?://|#|mailto:)[^)\s]+\)")
_RELATIVE_HTML_TARGET = re.compile(r'(?:src|href)="(?!https?://|#)[^"]+"')


def test_readme_targets_are_absolute() -> None:
    """No relative link/image target survives assembly — PyPI cannot resolve them."""
    mod = _load_builder()
    text = mod.assemble(_PARTS_DIR)
    offenders = [
        m.group(0)
        for rx in (_RELATIVE_MD_LINK, _RELATIVE_HTML_TARGET)
        for m in rx.finditer(text)
    ]
    assert not offenders, (
        "README parts carry relative link/image targets, which 404 on the PyPI "
        "project page (it renders README.md but hosts no repo files). Use absolute "
        f"GitHub URLs (raw.githubusercontent.com for images): {offenders}"
    )


def test_parts_are_nonempty_and_ordered() -> None:
    """Every part carries content; assembly order is the filename sort."""
    parts = sorted(p for p in _PARTS_DIR.glob("*.md") if p.is_file())
    assert parts, "docs/readme/ has no parts"
    for part in parts:
        assert part.read_text(encoding="utf-8").strip(), f"{part.name} is empty"
    # The front door must come first — a renumbering that demotes the title
    # section would assemble a README that doesn't open with the H1.
    first = parts[0].read_text(encoding="utf-8")
    assert first.lstrip().startswith("# DOS"), (
        f"first part by filename order ({parts[0].name}) does not open with the H1 title"
    )
