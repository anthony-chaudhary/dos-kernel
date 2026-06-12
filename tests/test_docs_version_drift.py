"""The live onboarding docs must name the *current* kernel version, not a stale one.

DOS single-sources its version from `pyproject.toml` (mirrored into
`src/dos/__init__.py` as the source-tree fallback), and `scripts/release_bump.py`
keeps those two in lockstep on every release. The **docs**, however, were never on
that leash — so the version literal a newcomer reads in QUICKSTART / the README /
the onboard playbook silently rotted three minor versions behind the binary
(`DOS v0.13.0` in the doc, `0.18.0` from `dos doctor`). A first-time user who runs
the command "exactly as written" — which those docs explicitly promise — then sees
a different version than the page claims and reasonably wonders whether they
installed the wrong package.

This test makes that drift a CI failure instead of a quiet erosion: every
*live, newcomer-facing* doc that prints a `DOS v…` banner must print the version
the package actually reports. It is the doc analogue of the
`pyproject ↔ __init__` lockstep `release_bump.py` already enforces — extended to
the surface a stranger reads first.

Scope note: this pins only the **live FTUE docs**. Historical artifacts that name
an old version *on purpose* — `docs/releases/vX.Y.Z.md` changelog entries, dated
`docs/reports/*`, `docs/stable-releases/*` evidence files — are deliberately NOT
checked; a release note for v0.13.0 *should* say v0.13.0 forever.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import dos

# The repo root is two levels up from this test file (tests/ -> repo root).
REPO_ROOT = Path(__file__).resolve().parent.parent

# The live, newcomer-facing docs that render a `DOS v…` banner in a sample
# `dos doctor` output (or a headline badge). These are the pages a first-time
# user reads and copy-pastes from; their version literal must track the binary.
# Each entry is a path relative to the repo root.
LIVE_ONBOARDING_DOCS = (
    "README.md",
    "docs/QUICKSTART.md",
    "docs/HACKING.md",
    "examples/playbooks/01_onboard-a-repo.md",
    # The action README's pre-commit `rev: vX.Y.Z` is a copy-paste pin a consumer
    # checks out verbatim — a stale tag there 404s their hook install (the @v1 /
    # v0.13.0 forms shipped pointing at refs the fresh public history never had).
    "verify-action/README.md",
    # Same copy-paste-pin shape: the hooks manifest's header shows the consumer
    # `rev: vX.Y.Z` line (docs/304) — a stale tag there 404s the hook install.
    ".pre-commit-hooks.yaml",
)

# Matches any `DOS v0.13.0` / `kernel v0.18.0` / `**v0.16.0**` style literal.
# Group 1 is the dotted version (`0.18.0`). We deliberately anchor on a `v`
# immediately followed by the dotted triple so a bare `0.333` ratio or a Python
# version (`py 3.13.7`) is never mistaken for a kernel version.
_VERSION_LITERAL = re.compile(r"v(\d+\.\d+\.\d+)")


def _doc_text(rel: str) -> str:
    path = REPO_ROOT / rel
    assert path.is_file(), f"expected onboarding doc is missing: {rel}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", LIVE_ONBOARDING_DOCS)
def test_onboarding_doc_version_matches_package(rel: str) -> None:
    """Every `vX.Y.Z` literal in a live onboarding doc equals `dos.__version__`.

    A doc may mention the version more than once (a headline badge + a sample
    `doctor` banner + the `environment print` line); *all* of them must agree
    with the package. A single stale literal fails the test and names the file —
    that is the whole point: the next person to bump the version is told, by a
    red test, that the docs need the same bump.
    """
    current = dos.__version__
    text = _doc_text(rel)

    found = _VERSION_LITERAL.findall(text)
    assert found, (
        f"{rel} carries no `vX.Y.Z` version literal — if its `DOS v…` sample was "
        f"removed, drop it from LIVE_ONBOARDING_DOCS in this test."
    )

    stale = sorted({v for v in found if v != current})
    assert not stale, (
        f"{rel} names version(s) {stale} but the package reports {current}.\n"
        f"The onboarding docs drifted behind the binary. After "
        f"`scripts/release_bump.py`, update the `DOS v…` / `kernel v…` literals "
        f"in this doc to v{current} so a newcomer's `dos doctor` matches the page."
    )


def test_at_least_one_doc_pins_the_version() -> None:
    """Guard the guard: the fixture list itself must not silently empty out.

    If a refactor renamed every onboarding doc out from under this test, the
    parametrized cases above would simply not run and the suite would stay green
    while the protection was gone. This case fails loudly in that scenario.
    """
    present = [rel for rel in LIVE_ONBOARDING_DOCS if (REPO_ROOT / rel).is_file()]
    assert present, (
        "none of the LIVE_ONBOARDING_DOCS exist — the version-drift guard is "
        "protecting nothing; fix the paths in this test."
    )
    # And at least one of them must actually contain the current version, proving
    # the literal-matching regex still finds real banners (catches a format change
    # like `DOS 0.18.0` dropping the `v` prefix that would void every assertion).
    current = dos.__version__
    assert any(
        current in "".join(_VERSION_LITERAL.findall(_doc_text(rel)))
        for rel in present
    ), (
        f"no live onboarding doc names v{current} via a `vX.Y.Z` literal — the "
        f"banner format may have changed; update _VERSION_LITERAL in this test."
    )


# ---------------------------------------------------------------------------
# The generic skill pack carries sample `dos doctor` output too — and it rotted
# the SAME way the FTUE docs did (`dos_version "0.13.0"` / `dos 0.13.0` while the
# binary said 0.18.0), but the `v`-anchored guard above never saw it: the skill
# samples print the version with NO `v` prefix. These are package-DATA a host
# reads and copy-pastes from, so pin them on the same leash. (Only the source
# pack `src/dos/skills/` is checked; `claude-plugin/skills/` is a byte-identical
# generated copy already pinned in-sync by tests/test_plugin_manifest.py.)
SKILL_PACK_REL = Path("src") / "dos" / "skills"

# Matches the skill samples' no-`v` doctor-version forms:
#   "dos_version": "0.18.0"   (JSON sample)
#   # dos_version 0.18.0      (commented sample line)
#   `dos 0.18.0`              (prose "captured against dos X.Y.Z")
_SKILL_DOS_VERSION = re.compile(r'(?:dos_version"?\s*:?\s*"?|`?dos )(\d+\.\d+\.\d+)')


def _skill_pack_markdown() -> list[Path]:
    root = REPO_ROOT / SKILL_PACK_REL
    return sorted(root.rglob("*.md"))


def test_skill_pack_doctor_samples_match_package() -> None:
    """Every `dos_version`/`dos X.Y.Z` literal in the skill pack equals the binary.

    The doc analogue extended to the SKP samples: a stale version a host pastes
    out of a shipped skill is the same broken promise as a stale FTUE banner, and
    this pack drifted three minors behind before anyone noticed. A red test here
    names the file so the next `release_bump.py` is told to bump the samples too.
    """
    current = dos.__version__
    offenders: dict[str, list[str]] = {}
    for path in _skill_pack_markdown():
        found = _SKILL_DOS_VERSION.findall(path.read_text(encoding="utf-8"))
        stale = sorted({v for v in found if v != current})
        if stale:
            offenders[str(path.relative_to(REPO_ROOT))] = stale
    assert not offenders, (
        f"skill-pack samples drifted behind the binary (package reports {current}):\n"
        + "\n".join(f"  {f}: {v}" for f, v in offenders.items())
        + "\nUpdate the `dos_version`/`dos X.Y.Z` literals, then re-run "
        "`python scripts/build_plugin.py` to resync the plugin copy."
    )


def test_skill_pack_samples_pin_some_version() -> None:
    """Guard the guard: the regex must still match at least one real sample.

    If the sample format changes (the version literal is dropped or rewritten),
    the offender scan above would silently find nothing and stay green while the
    protection lapsed. Assert the current version is actually present somewhere.
    """
    current = dos.__version__
    seen = []
    for path in _skill_pack_markdown():
        seen += _SKILL_DOS_VERSION.findall(path.read_text(encoding="utf-8"))
    assert current in seen, (
        f"no skill-pack sample names {current} via a `dos_version`/`dos X.Y.Z` "
        f"literal — the sample format may have changed; update _SKILL_DOS_VERSION."
    )
