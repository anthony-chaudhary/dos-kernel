"""The install docs/scripts must not name an extra or script `pyproject` lacks.

DOS ships many install paths now (uv tool / uvx, pip, the repo-local
`install.sh`/`install.ps1` wrappers, the Claude Code plugin). Each path is
*documented* (README, `docs/INSTALL.md`) and *scripted* (the wrappers) — and each
repeats facts that actually live in `pyproject.toml`: the distribution name
(`dos-kernel`), the extras (`mcp`, `tui`, …), the console scripts (`dos`,
`dos-mcp`), the Python floor (`>=3.11`). Hand-copied, those rot: rename the
`[mcp]` extra and the README keeps telling people to `pip install dos-kernel[mcp]`.

This is the **install-surface drift gate** — the sibling of
`tests/test_docs_version_drift.py` (which leashes the *version* literal) on the
*extras/scripts/name* axis. It reads the authoritative facts from
`scripts/install_facts.py` (the single source of truth) and asserts every claim a
human-written install doc or script makes is backed by `pyproject.toml`. A
renamed extra or dropped console script becomes a red test that names the file,
instead of a silently-wrong instruction a newcomer copy-pastes.

What it does NOT do: it does not require the docs to mention *every* extra (a doc
may legitimately omit `paper` or `export-otlp`). It only fails on a claim the
package can't honor — a `[foo]` bracket or `dos-foo` command that doesn't exist.
The reverse direction (an extra with no doc) is a softer concern checked
advisorily by `test_core_extras_are_documented_somewhere`.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_install_facts():
    """Import `scripts/install_facts.py` by path (scripts/ is not a package).

    We register the module in `sys.modules` before `exec_module` so that the
    frozen `@dataclass` it defines can resolve its own `__module__` — without
    this, `dataclasses._is_type` raises on `field(default_factory=...)` because
    the module isn't yet in `sys.modules` (a known path-import quirk).
    """
    spec = importlib.util.spec_from_file_location(
        "install_facts", REPO_ROOT / "scripts" / "install_facts.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["install_facts"] = mod
    spec.loader.exec_module(mod)
    return mod.read_install_facts()


FACTS = _load_install_facts()

# The install-bearing files a newcomer reads or runs. Relative to the repo root.
# Each makes install claims (`[extra]`, `--extras X`, `dos-mcp`, `dos-kernel`)
# that must be backed by pyproject. The wrappers are included because they name
# `dos`/`dos-mcp` and forward `--extras`.
INSTALL_SURFACE = (
    "README.md",
    "docs/INSTALL.md",
    "docs/QUICKSTART.md",
    "install.sh",
    "install.ps1",
)

# A `pip install dos-kernel[mcp]` / `dos-kernel[dev,mcp]` / `".[mcp]"` bracket.
# Group 1 is the inside of the brackets (a comma-separated extras list). We
# anchor on `dos-kernel[` or a bare `.[`/`".[` (the editable/local-clone form
# uv and pip accept) so we don't sweep up an unrelated `[...]` in prose.
_EXTRA_BRACKET = re.compile(r'(?:dos-kernel|"?\.)\[([a-z0-9,\s-]+)\]')

# An `--extras mcp` / `--extras "dev,mcp"` flag passed to install.py.
_EXTRAS_FLAG = re.compile(r'--extras[=\s]+["\']?([a-z0-9,\s-]+)["\']?')

# A `dos-mcp` / `dos-foo` console-script mention. We match the hyphenated form
# only — bare `dos` is the umbrella CLI and appears constantly in prose, so
# checking it would be noise; the hyphenated subcommands are the forgeable ones.
_HYPHEN_SCRIPT = re.compile(r"\bdos-([a-z]+)\b")

# `dos-*` tokens that appear in the docs and are NOT console scripts, so the
# script check must not treat them as a promised command:
#   * `dos-kernel`              — the distribution/marketplace name
#   * `dos-strategy`/`dos-private` — the private sibling repo (renamed 2026-06-10;
#                                 the old name survives in dated docs)
#   * `dos-hook`                — the bundled Go hook binary (not a [project.scripts] entry)
#   * skill-namespace prefixes  — `/dos-kernel:dos-next-up`, `dos-dispatch`, `dos-setup`,
#                                 `dos-promote`, … are SKILL names (the SKP), never console
#                                 scripts; they're invoked as `/dos-kernel:dos-<x>`, not run
#                                 from a shell.
#   * prose fragments           — `dos-is` (from "what DOS-is / is-not"), `dos-next` /
#                                 `dos-dispatch` (skill stems), `dos-with` (the docs/278
#                                 filename `…integrating-dos-with-hermes…`)
# The console scripts are exactly `[project.scripts]` (`dos`, `dos-mcp`); anything
# else hyphenated is one of the above, so we allowlist by suffix.
_NOT_A_SCRIPT = {
    "kernel", "strategy", "private", "hook",            # names, not commands
    "next", "dispatch", "setup", "promote", "replan",   # skill stems
    "supervise", "unstick", "witness", "goal", "self",  # skill stems
    "class", "is", "with",                               # skill stem / prose fragments
    "gate",                                              # dos-gate.yml — a workflow slug (the README badge), not a command
}

# The teaching sentence — "the dist name is `dos-kernel`, not `dos`; a bare
# `pip install dos` pulls a squatter" — deliberately QUOTES the wrong command to
# warn against it. The dist-name check must not flag the very sentence that
# teaches the lesson, so we strip lines that also contain the corrective marker.
_DIST_WARNING_MARKERS = ("not `dos`", "squat", "unrelated package", "pulls an")


def _text(rel: str) -> str:
    path = REPO_ROOT / rel
    assert path.is_file(), f"install-surface file is missing: {rel}"
    return path.read_text(encoding="utf-8")


def _split_extras(blob: str) -> list[str]:
    return [e.strip() for e in blob.split(",") if e.strip()]


@pytest.mark.parametrize("rel", INSTALL_SURFACE)
def test_bracketed_extras_exist_in_pyproject(rel: str) -> None:
    """Every `dos-kernel[X]` / `.[X]` extra named in a doc/script is a real extra."""
    text = _text(rel)
    claimed: set[str] = set()
    for blob in _EXTRA_BRACKET.findall(text):
        claimed.update(_split_extras(blob))
    unknown = sorted(claimed - set(FACTS.extras))
    assert not unknown, (
        f"{rel} installs extra(s) {unknown} that pyproject.toml does not declare "
        f"(declared: {list(FACTS.extras)}).\n"
        f"Either the extra was renamed/removed in [project.optional-dependencies] "
        f"and this doc/script still names the old one, or this is a typo. Fix the "
        f"doc to a real extra, or add the extra to pyproject."
    )


@pytest.mark.parametrize("rel", INSTALL_SURFACE)
def test_extras_flag_values_exist_in_pyproject(rel: str) -> None:
    """Every `--extras X` value documented for install.py is a real extra."""
    text = _text(rel)
    claimed: set[str] = set()
    for blob in _EXTRAS_FLAG.findall(text):
        claimed.update(_split_extras(blob))
    unknown = sorted(claimed - set(FACTS.extras))
    assert not unknown, (
        f"{rel} shows `--extras {unknown}` but pyproject declares "
        f"{list(FACTS.extras)}. Update the doc or the extra."
    )


@pytest.mark.parametrize("rel", INSTALL_SURFACE)
def test_hyphenated_dos_commands_are_real_scripts(rel: str) -> None:
    """Every `dos-<x>` command a doc/script promises is a real console script."""
    text = _text(rel)
    script_suffixes = {s.split("dos-", 1)[1] for s in FACTS.console_scripts
                       if s.startswith("dos-")}
    promised = {m for m in _HYPHEN_SCRIPT.findall(text) if m not in _NOT_A_SCRIPT}
    unknown = sorted(promised - script_suffixes)
    assert not unknown, (
        f"{rel} names command(s) {['dos-' + u for u in unknown]} that are not in "
        f"[project.scripts] (real scripts: {list(FACTS.console_scripts)}).\n"
        f"If a console script was renamed/removed, fix the doc; if this token is a "
        f"non-command `dos-*` name (like a marketplace slug), add it to "
        f"_NOT_A_SCRIPT in this test."
    )


@pytest.mark.parametrize("rel", INSTALL_SURFACE)
def test_dist_name_is_dos_kernel_not_dos(rel: str) -> None:
    """No install-surface file tells the reader to `pip install dos` (the squatter).

    The bare `dos` name on PyPI is an unrelated package; the whole point of the
    `dos-kernel` distribution name is that a `pip install dos` resolves wrong.
    A doc that says `pip install dos` (no `-kernel`, no extra bracket, not `-e`)
    is the exact footgun the naming was chosen to avoid.
    """
    text = _text(rel)
    # `pip install dos` followed by end-of-token that is NOT `-kernel`, `-e`,
    # `[`, or a path char. We allow `pip install -e .` and `pip install
    # dos-kernel...`; we catch `pip install dos\n` / `pip install dos `.
    # A line that ALSO carries a "not `dos` / squatter" warning marker is the
    # teaching sentence quoting the wrong command on purpose — exempt it.
    candidate_lines = [
        ln for ln in text.splitlines()
        if not any(marker in ln for marker in _DIST_WARNING_MARKERS)
    ]
    bad = re.findall(r"pip install (?:-[^\s]+\s+)*dos(?![\w./\[-])",
                     "\n".join(candidate_lines))
    assert not bad, (
        f"{rel} contains a bare `pip install dos` — that resolves to the PyPI "
        f"squatter, not this package. Use `{FACTS.dist_name}` (or `pip install "
        f"-e .` for a clone). See SECURITY.md 'Supply chain'."
    )


def test_facts_are_nonempty() -> None:
    """Guard the guard: the facts reader must actually find extras + scripts.

    If a refactor broke `install_facts.read_install_facts` so it returned empty
    tuples, every assertion above would vacuously pass and the gate would protect
    nothing. Pin that the source of truth is non-empty and self-consistent.
    """
    assert FACTS.dist_name == "dos-kernel"
    assert FACTS.console_scripts, "no console scripts read from pyproject"
    assert "dos" in FACTS.console_scripts
    assert "mcp" in FACTS.extras, (
        "the [mcp] extra vanished from pyproject — either it was renamed "
        "(update every install doc) or the facts reader broke."
    )


def test_core_extras_are_documented_somewhere() -> None:
    """Advisory: the user-facing extras should be findable in the install docs.

    Not every extra needs a mention, but the two an end-user actually reaches for
    — `mcp` (the server) and `tui` (the live screens) — should appear somewhere a
    newcomer can find them, or the surface is under-documented. `dev`/`paper` are
    contributor/build extras and are exempt; `notify-slack`/`export-otlp` are
    driver extras documented in HACKING, not the install page.
    """
    user_facing = {"mcp", "tui"}
    declared_user_facing = user_facing & set(FACTS.extras)
    corpus = "\n".join(_text(rel) for rel in INSTALL_SURFACE
                       if (REPO_ROOT / rel).suffix == ".md")
    documented = {
        e for e in declared_user_facing
        if re.search(rf"\[{re.escape(e)}\]|\b{re.escape(e)}\b", corpus)
    }
    missing = sorted(declared_user_facing - documented)
    assert not missing, (
        f"user-facing extra(s) {missing} are declared in pyproject but mentioned "
        f"in none of the install docs {[r for r in INSTALL_SURFACE if r.endswith('.md')]}. "
        f"Add a one-line `pip install dos-kernel[{missing[0]}]` to the install page."
    )
