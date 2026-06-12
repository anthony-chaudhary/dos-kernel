"""The docs/STABILITY.md rot pins (docs/308, issue #67).

A promise document rots unless something pins it to the code it promises
about. This suite pins the three claims in STABILITY.md a consumer would
build on:

- the **seam roster**: the entry-point groups the doc's table names are
  exactly the groups the source declares (the ``*_ENTRY_POINT_GROUP``
  constants plus the two seams that select their group inline) — both
  directions, so a new seam without a doc row goes red, and a stale doc row
  without a seam goes red;
- the **warning category** the doc documents is the real, importable one,
  re-exported from ``dos``;
- the **deprecation window** the doc states (two minor releases) is present
  in so many words.

The naming pins (the entry docs that must point at STABILITY.md) live here
too — the issue's done-condition says the policy is "named from the README
and AGENTS.md", and a link nobody pins is a link that rots.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STABILITY = REPO / "docs" / "STABILITY.md"
SRC = REPO / "src" / "dos"

# The two spellings src/dos declares an entry-point group in: a module-level
# ``<NAME>_ENTRY_POINT_GROUP = "dos.xxx"`` constant, or an inline select
# against importlib.metadata (``eps.select(group="dos.xxx")`` /
# ``eps.get("dos.xxx", ...)`` — the hook_dialect/hook_install shape).
CONSTANT_RE = re.compile(
    r'^[A-Z_]*ENTRY_POINT_GROUP\s*=\s*"(dos\.[a-z_]+)"', re.MULTILINE
)
INLINE_RE = re.compile(r'(?:group=|\.get\()"(dos\.[a-z_]+)"')

# A roster row in STABILITY.md's seam table: ``| `dos.xxx` | ... |``.
DOC_ROW_RE = re.compile(r"^\s*\|\s*`(dos\.[a-z_]+)`\s*\|", re.MULTILINE)


def declared_groups() -> set[str]:
    groups: set[str] = set()
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        groups.update(CONSTANT_RE.findall(text))
        groups.update(INLINE_RE.findall(text))
    return groups


def documented_groups() -> set[str]:
    return set(DOC_ROW_RE.findall(STABILITY.read_text(encoding="utf-8")))


def test_doc_roster_matches_the_declared_seams_exactly() -> None:
    declared = declared_groups()
    documented = documented_groups()
    assert declared, "the source scan found no entry-point groups — scan broken?"
    missing_from_doc = declared - documented
    stale_in_doc = documented - declared
    assert not missing_from_doc, (
        f"src/dos declares seam group(s) docs/STABILITY.md does not promise: "
        f"{sorted(missing_from_doc)} — add a roster row (and decide its tier)."
    )
    assert not stale_in_doc, (
        f"docs/STABILITY.md promises seam group(s) the source no longer "
        f"declares: {sorted(stale_in_doc)} — a removed seam is a Stable "
        f"break; update the doc alongside it."
    )


def test_documented_category_is_the_real_importable_one() -> None:
    text = STABILITY.read_text(encoding="utf-8")
    assert "dos.deprecation.DosDeprecationWarning" in text
    assert "dos.deprecation.warn_deprecated" in text

    import dos
    from dos.deprecation import DosDeprecationWarning, warn_deprecated

    assert issubclass(DosDeprecationWarning, DeprecationWarning)
    assert dos.DosDeprecationWarning is DosDeprecationWarning
    assert dos.warn_deprecated is warn_deprecated


def test_doc_states_the_two_minor_release_window() -> None:
    assert "two minor releases" in STABILITY.read_text(encoding="utf-8")
