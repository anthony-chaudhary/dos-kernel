#!/usr/bin/env python3
"""Bump the DOS version markers in one shot.

DOS single-sources its version from `pyproject.toml`. `src/dos/__init__.py`
reads it back at runtime via `importlib.metadata.version("dos-kernel")` (the
distribution name is `dos-kernel`, not the import name `dos` — the bare `dos`
name on PyPI is an unrelated package), but carries a literal fallback string for
the uninstalled-source-tree case (running from a bare checkout that was never
`pip install`-ed). That literal MUST equal pyproject's value — it drifted exactly once (init said `0.1.0` while pyproject
shipped `0.2.0`, so every `dos` CLI command misreported its version from a
source checkout), and the comment in `__init__.py` records the scar.

So there are seven targets, and the whole point of this script over seven hand
edits is that it keeps them in lockstep:

  1. pyproject.toml          — `version = "X.Y.Z"`  (the source of truth)
  2. src/dos/__init__.py     — `__version__ = "X.Y.Z"`  (the fallback literal)
  3. claude-plugin/.claude-plugin/plugin.json — `"version": "X.Y.Z"`  (the bundle manifest)
  4. .claude-plugin/marketplace.json — the `dos-kernel` plugin entry's `"version"`
  5. the FTUE doc banners + skill-pack samples — every `DOS vX.Y.Z` /
     `dos_version "X.Y.Z"` literal a newcomer reads (the surface
     `tests/test_docs_version_drift.py` guards).
  6. server.json — the MCP Registry manifest's three version references
     (`.version`, `.packages[0].version`, and the `dos-kernel[mcp]==X.Y.Z`
     `--from` pin), the surface `tests/test_server_json_version.py` guards.
  7. gemini-extension.json — the Gemini CLI extension manifest's `"version"`
     (the repo-root manifest fronting the auto-indexed Gemini extensions
     gallery), the surface `tests/test_gemini_extension.py` guards.

Target 6 was added after the SAME class of drift recurred on the registry surface
(issue #30): server.json was authored for the registry publish but never on the
bumper's leash, so every `/release` after the first publish stranded it at the
previous version and a later `mcp-registry-publish.yml` dispatch refused at its
version-skew preflight until someone hand-bumped it. Teaching the ONE bumper about
it is the durable fix — the same move as targets 3–4.

Targets 3–4 were added after the plugin bundle drifted: the v0.14/v0.15 cuts
bumped 1–2 but left the plugin manifest at 0.13.0, reddening
`tests/test_plugin_manifest.py`. The fix is to teach the ONE bumper about every
marker that must track the package — so a release can't strand the bundle again.
(The marketplace's *top-level* catalog version is a SEPARATE number and is left
alone; only the nested plugin entry tracks the package.)

Target 5 was added after the SAME failure recurred on the prose surface: the
v0.19.0 cut bumped 1–4 but left the doc/skill version literals at 0.18.0, and the
drift guard stayed green only because a not-yet-reinstalled dev env's
`dos.__version__` read the stale install metadata — which happened to match the
stale docs (2026-06-10 supervisor-audit report). Unlike 1–4 it is keyed on the
OLD→NEW pair (it rewrites only literals naming the old version, so a deliberate
cross-version reference is never clobbered) and is NOT part of the drift guard.
After it runs, the release skill runs `scripts/build_plugin.py` to resync the
generated `claude-plugin/skills/` mirror of the swept `src/dos/skills/` samples.

This is **dev / release tooling, not kernel** — it operates ON the package but
is never imported BY it. It is the DOS analogue of the reference userland app's
release-bump tooling, stripped of the host-only targets that have no DOS
equivalent: there is no
bare `VERSION` file, no Go `version.txt`, no `docs/06_implementation-status.md`
header, and the auto-memory `MEMORY.md` bump was always a no-op against a flat
index. DOS adds none of them back — fewer markers is the feature.

Usage:
  python scripts/release_bump.py 0.3.0
  python scripts/release_bump.py 0.3.0 --dry-run
  python scripts/release_bump.py 0.3.0 --skip init   # pyproject only

Prints a JSON report of what changed so the skill (or a human) can verify.
Exit code is non-zero iff a target failed OR the markers ended up out of
sync (the drift guard).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def repo_root() -> Path:
    """The git top-level of the repo this script runs inside (see release_context)."""
    try:
        top = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        top = ""
    return Path(top) if top else Path.cwd()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str, *, dry_run: bool) -> None:
    # newline="" disables the platform newline translation: on Windows the default
    # text mode rewrites every "\n" in `text` to "\r\n" on disk, which would flip an
    # LF-committed marker file (pyproject / __init__ / the JSON manifests / the
    # skill samples) to CRLF in the working tree — invisible to `git diff` (the
    # autocrlf=input filter normalizes it back before hashing) but VISIBLE to the
    # byte-level `build_plugin.check` skill-sync test, which then reds. We only ever
    # substitute a version literal, never a newline, so writing the bytes verbatim
    # preserves whatever line endings the file already had. (The 2026-06-10 audit's
    # Issue 3 was this exact CRLF-vs-LF skill mismatch; don't manufacture it here.)
    if not dry_run:
        path.write_text(text, encoding="utf-8", newline="")


def _current_pyproject_version(root: Path) -> "str | None":
    """Read the CURRENT `version = "X.Y.Z"` from pyproject (the old version).

    Returns None if the file or the version line is absent — the caller then skips
    the doc sweep rather than guessing. Read-only; runs before bump_pyproject so
    the doc sweep knows which literal to replace."""
    path = root / "pyproject.toml"
    if not path.exists():
        return None
    m = re.search(r'^version\s*=\s*"([^"]+)"', read(path), re.MULTILINE)
    return m.group(1) if m else None


def bump_pyproject(root: Path, new: str, *, dry_run: bool) -> dict:
    path = root / "pyproject.toml"
    if not path.exists():
        return {"path": "pyproject.toml", "ok": False, "reason": "not found"}
    text = read(path)
    pattern = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
    m = pattern.search(text)
    if not m:
        return {"path": "pyproject.toml", "ok": False, "reason": "no version= line found"}
    old = m.group(1)
    new_text = pattern.sub(f'version = "{new}"', text, count=1)
    changed = new_text != text
    if changed:
        write(path, new_text, dry_run=dry_run)
    return {"path": "pyproject.toml", "old": old, "new": new, "changed": changed, "ok": True}


def bump_init(root: Path, new: str, *, dry_run: bool) -> dict:
    """Bump the literal fallback `__version__ = "X.Y.Z"` in src/dos/__init__.py.

    The literal lives inside an `except` branch; there is exactly one
    `__version__ = "..."` assignment in the file, so a non-anchored match is
    safe and avoids coupling to the surrounding try/except indentation.
    """
    path = root / "src" / "dos" / "__init__.py"
    if not path.exists():
        return {"path": "src/dos/__init__.py", "ok": False, "reason": "not found"}
    text = read(path)
    pattern = re.compile(r'__version__\s*=\s*"([^"]+)"')
    m = pattern.search(text)
    if not m:
        return {"path": "src/dos/__init__.py", "ok": False,
                "reason": "no __version__ literal found"}
    old = m.group(1)
    new_text = pattern.sub(f'__version__ = "{new}"', text, count=1)
    changed = new_text != text
    if changed:
        write(path, new_text, dry_run=dry_run)
    return {"path": "src/dos/__init__.py", "old": old, "new": new,
            "changed": changed, "ok": True}


def bump_plugin_manifest(root: Path, new: str, *, dry_run: bool) -> dict:
    """Bump the Claude Code plugin manifest `version` (claude-plugin/.claude-plugin/plugin.json).

    The bundle fronts the package, so its advertised version must track it — the
    same lockstep rule as the __init__ fallback, enforced by
    `tests/test_plugin_manifest.py::test_plugin_version_tracks_package_version`.
    It drifted exactly once (the v0.14/v0.15 cuts bumped the package but left the
    plugin at 0.13.0); teaching the bumper about it is the durable fix. The
    manifest has a single top-level `"version"` key, so an anchored first-match
    sub is safe and preserves the file's formatting (vs a JSON round-trip).
    """
    rel = "claude-plugin/.claude-plugin/plugin.json"
    path = root / "claude-plugin" / ".claude-plugin" / "plugin.json"
    if not path.exists():
        return {"path": rel, "ok": False, "reason": "not found"}
    text = read(path)
    pattern = re.compile(r'("version"\s*:\s*)"([^"]+)"')
    m = pattern.search(text)
    if not m:
        return {"path": rel, "ok": False, "reason": 'no "version" key found'}
    old = m.group(2)
    new_text = pattern.sub(rf'\g<1>"{new}"', text, count=1)
    changed = new_text != text
    if changed:
        write(path, new_text, dry_run=dry_run)
    return {"path": rel, "old": old, "new": new, "changed": changed, "ok": True}


def bump_gemini_extension(root: Path, new: str, *, dry_run: bool) -> dict:
    """Bump the Gemini CLI extension manifest `version` (gemini-extension.json).

    The repo-root manifest fronts the package on Google's auto-indexed extensions
    gallery (geminicli.com/extensions), so its advertised version must track the
    package the same way the Claude plugin manifest does — pinned by
    `tests/test_gemini_extension.py::test_gemini_extension_version_tracks_package`.
    The manifest has a single top-level `"version"` key, so an anchored first-match
    sub is safe and preserves the file's formatting (vs a JSON round-trip), the same
    choice and reason as bump_plugin_manifest.
    """
    rel = "gemini-extension.json"
    path = root / "gemini-extension.json"
    if not path.exists():
        return {"path": rel, "ok": False, "reason": "not found"}
    text = read(path)
    pattern = re.compile(r'("version"\s*:\s*)"([^"]+)"')
    m = pattern.search(text)
    if not m:
        return {"path": rel, "ok": False, "reason": 'no "version" key found'}
    old = m.group(2)
    new_text = pattern.sub(rf'\g<1>"{new}"', text, count=1)
    changed = new_text != text
    if changed:
        write(path, new_text, dry_run=dry_run)
    return {"path": rel, "old": old, "new": new, "changed": changed, "ok": True}


def bump_marketplace(root: Path, new: str, *, dry_run: bool) -> dict:
    """Bump the `dos-kernel` plugin entry's `version` in .claude-plugin/marketplace.json.

    The marketplace carries TWO version keys: a top-level CATALOG version (left
    alone) and the nested `plugins[].version` for the bundle (which must agree
    with the plugin manifest — `test_marketplace_plugin_version_matches_plugin_manifest`).
    So the regex is anchored on the plugin entry's `"source"` line to target the
    nested version only, never the catalog version. If the file ever omits the
    nested version (the entry then inherits the manifest's), this is a clean no-op.
    """
    rel = ".claude-plugin/marketplace.json"
    path = root / ".claude-plugin" / "marketplace.json"
    if not path.exists():
        return {"path": rel, "ok": False, "reason": "not found"}
    text = read(path)
    # Anchor on the plugin entry: its `"source": "./claude-plugin"` line, then the
    # next `"version": "..."` within the same object. Non-greedy across the gap so
    # we can't accidentally span into a later object.
    pattern = re.compile(
        r'("source"\s*:\s*"\./claude-plugin"\s*,.*?"version"\s*:\s*)"([^"]+)"',
        re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        # The nested version is optional — absence is a legitimate no-op, not a failure.
        return {"path": rel, "old": None, "new": new, "changed": False, "ok": True,
                "note": "no nested plugin version (inherits the manifest)"}
    old = m.group(2)
    new_text = pattern.sub(rf'\g<1>"{new}"', text, count=1)
    changed = new_text != text
    if changed:
        write(path, new_text, dry_run=dry_run)
    return {"path": rel, "old": old, "new": new, "changed": changed, "ok": True}


def bump_server_json(root: Path, new: str, *, dry_run: bool) -> dict:
    """Bump every version reference in the MCP Registry manifest (server.json).

    The registry publish (`mcp-registry-publish.yml`) refuses on version skew, so
    server.json's version literals must track the package the same way the plugin
    manifest does. The file carries the version in THREE places, all equal to the
    package version:

      * `.version`              — the server release version
      * `.packages[0].version`  — the PyPI package pin
      * the `dos-kernel[mcp]==X.Y.Z` value of the `--from` runtimeArgument

    The two `"version"` keys are always the package version, so a single regex
    rewrites both; the `==` pin is rewritten by a second. Targeted subs (not a JSON
    round-trip) preserve the file's formatting — the same choice, and reason, as
    bump_plugin_manifest. Pinned by `tests/test_server_json_version.py`.
    """
    rel = "server.json"
    path = root / "server.json"
    if not path.exists():
        return {"path": rel, "ok": False, "reason": "not found"}
    text = read(path)
    ver_pat = re.compile(r'("version"\s*:\s*)"([^"]+)"')
    first = ver_pat.search(text)
    if not first:
        return {"path": rel, "ok": False, "reason": 'no "version" key found'}
    old = first.group(2)
    # Rewrite BOTH `"version"` keys (top-level + package): they are the same package
    # version, and a future second package version should track the release too.
    new_text, n_ver = ver_pat.subn(rf'\g<1>"{new}"', text)
    # The `dos-kernel[mcp]==X.Y.Z` pin inside the `--from` runtimeArgument value.
    from_pat = re.compile(r"(dos-kernel\[mcp\]==)(\d+\.\d+\.\d+)")
    new_text, n_from = from_pat.subn(rf"\g<1>{new}", new_text)
    changed = new_text != text
    if changed:
        write(path, new_text, dry_run=dry_run)
    return {"path": rel, "old": old, "new": new, "changed": changed, "ok": True,
            "refs_rewritten": n_ver + n_from}


# The live, newcomer-facing docs that render a `DOS v…` banner, plus the skill
# pack samples — the SAME surface `tests/test_docs_version_drift.py` guards. They
# are NOT on the lockstep leash that pyproject/__init__/plugin/marketplace are, so
# a release that bumped only the code markers left them naming a stale version
# (and the guard stayed green only because a not-yet-reinstalled dev env's
# `dos.__version__` read the stale metadata, which matched the stale docs). Sweep
# them here so the ONE bumper covers the whole FTUE version surface — the exact
# follow-up the 2026-06-10 supervisor-audit report named.
#
# Scope MUST mirror the test: historical artifacts that name an old version ON
# PURPOSE (docs/releases/*, docs/reports/*, docs/stable-releases/*) are NEVER
# swept. Only literals whose captured version equals the OLD package version are
# rewritten, so an incidental reference to some other version is left untouched.
_DOC_BANNER_FILES = (
    "README.md",
    "docs/QUICKSTART.md",
    "docs/HACKING.md",
    "examples/playbooks/01_onboard-a-repo.md",
    # The action README's pre-commit `rev: vX.Y.Z` pin — in the drift guard's
    # roster since the public-repo flip, so it must be on the bumper's leash too
    # (the 2026-06-10 go/version audit found it stranded by a simulated bump).
    "verify-action/README.md",
    # The hooks manifest's consumer `rev: vX.Y.Z` example (docs/304) — same
    # copy-paste-pin shape, same leash.
    ".pre-commit-hooks.yaml",
)
# README.md is GENERATED from the section parts under docs/readme/ (one file per
# section, concatenated verbatim by scripts/build_readme.py). Sweeping only the
# rendered README desyncs it from its parts — the assembly gate
# (tests/test_readme_assembly.py) reds, and the next rebuild would resurrect the
# stale literal. Because assembly is pure concatenation, sweeping the parts with
# the SAME regex/old→new keeps rendered-and-parts byte-consistent with no
# rebuild step. (Found by the same 2026-06-10 audit, the day README went
# generated.)
_README_PARTS_DIR = "docs/readme"
# `v0.18.0` / `kernel v0.18.0` — the same `v`-anchored literal the drift test's
# `_VERSION_LITERAL` matches (group 1 is the dotted triple).
_DOC_BANNER_RE = re.compile(r"v(\d+\.\d+\.\d+)")
# The skill samples print the version with NO `v` prefix:
#   "dos_version": "0.18.0"   | # dos_version 0.18.0   | `dos 0.18.0`
# Mirror `_SKILL_DOS_VERSION` from the drift test EXACTLY so the bumper and the
# guard can never disagree on what counts as a version literal.
_SKILL_VERSION_RE = re.compile(r'(?:dos_version"?\s*:?\s*"?|`?dos )(\d+\.\d+\.\d+)')


def _sweep_version_literals(
    path: Path, pattern: re.Pattern, old: str, new: str, *, dry_run: bool
) -> "tuple[str, int]":
    """Rewrite every `pattern` match whose captured version == ``old`` to ``new``.

    Returns ``(rel_label_unused, count)`` — actually ``(new_text, count)``. Only
    the OLD version is touched (a literal naming a different version is left as-is,
    so an incidental cross-version reference is never clobbered). The capture group
    is the dotted triple; we rebuild each match by swapping just that group's span
    so the surrounding banner text (`DOS v…`, `dos_version "…"`) is preserved.
    """
    text = path.read_text(encoding="utf-8")
    count = 0

    def _repl(m: re.Match) -> str:
        nonlocal count
        if m.group(1) != old:
            return m.group(0)  # a different version on purpose — leave it
        count += 1
        # Swap only the captured triple's span within the whole match.
        start, end = m.start(1) - m.start(0), m.end(1) - m.start(0)
        whole = m.group(0)
        return whole[:start] + new + whole[end:]

    new_text = pattern.sub(_repl, text)
    # newline="" — preserve the file's existing line endings (see `write()`): a
    # skill sample written with CRLF here would mismatch its LF-bundled plugin copy.
    if count and not dry_run:
        path.write_text(new_text, encoding="utf-8", newline="")
    return new_text, count


def rebuild_llms_full(root: Path, *, dry_run: bool) -> dict:
    """Regenerate ``llms-full.txt`` after the doc sweep (#139).

    ``llms-full.txt`` is a SECOND generated artifact (after README.md) derived from
    the rostered docs `llms.txt` indexes — and `bump_docs` sweeps the version literal
    in two of those rostered docs (`docs/QUICKSTART.md`, `docs/HACKING.md`). Before
    this, the bumper swept the sources but left the assembly stale, so every release
    reddened `tests/test_llms_full.py::test_llms_full_matches_assembly` on all four
    CI legs and needed a manual follow-up rebuild (it bit v0.26.0: bump `a40ce65`,
    rebuild `b900599`). The generated-file leash must cover BOTH derived artifacts —
    same reasoning as the README-parts rebuild the release skill already runs.

    Reassembles via `build_llms_full.assemble(root)` (the single source of the
    assembly the test pins) and writes only on a real change. Degrades to a soft,
    non-failing result if the builder can't be imported/run — the doc sweep already
    ran, and `test_llms_full` would fail louder on its own; we record the gap rather
    than crash the bump.
    """
    target = root / "llms-full.txt"
    if not target.exists():
        return {"path": "llms-full.txt", "ok": True, "changed": False,
                "note": "llms-full.txt absent — nothing to rebuild"}
    try:
        sys.path.insert(0, str(root / "scripts"))
        import build_llms_full  # noqa: E402 — sibling release tool, lazy import
        new_text = build_llms_full.assemble(root)
    except Exception as exc:  # surface, never crash the bump (skill can rebuild by hand)
        return {"path": "llms-full.txt", "ok": False,
                "reason": f"{type(exc).__name__}: {exc}",
                "note": "could not reassemble — run `python scripts/build_llms_full.py`"}
    finally:
        if str(root / "scripts") in sys.path:
            sys.path.remove(str(root / "scripts"))
    old_text = read(target)
    changed = new_text != old_text
    if changed:
        write(target, new_text, dry_run=dry_run)
    return {"path": "llms-full.txt", "ok": True, "changed": changed,
            "note": "reassembled from the rostered docs" if changed
            else "already in sync with the rostered docs"}


def bump_docs(root: Path, old: str, new: str, *, dry_run: bool) -> dict:
    """Sweep the FTUE doc banners + skill-pack samples from ``old`` to ``new``.

    The fifth target, closing the gap the 2026-06-10 audit found: the doc/skill
    version literals were never on the bumper's leash, so a release stranded them
    behind the binary. This sweeps the SAME files `test_docs_version_drift.py`
    checks, with the SAME literal grammars, replacing only the old version. After
    this runs, a `python scripts/build_plugin.py` resyncs the bundled skill copies
    (the skill samples live in `src/dos/skills/`; the bundle is a generated mirror,
    so the release skill rebuilds it — the same step the manifest test instructs).

    A no-op (count 0 everywhere) is a clean success: it just means the docs already
    named ``new`` (e.g. a re-run, or a release where someone bumped them by hand).
    """
    if old == new:
        return {"path": "docs+skills", "old": old, "new": new, "changed": False,
                "ok": True, "note": "old == new; nothing to sweep"}
    swept: dict[str, int] = {}
    missing: list[str] = []
    for rel in _DOC_BANNER_FILES:
        p = root / rel
        if not p.exists():
            missing.append(rel)
            continue
        _txt, n = _sweep_version_literals(p, _DOC_BANNER_RE, old, new, dry_run=dry_run)
        if n:
            swept[rel] = n
    # The README section parts — swept with the same grammar as the rendered
    # README.md above so the two stay byte-consistent (see _README_PARTS_DIR).
    parts_root = root / _README_PARTS_DIR
    if parts_root.is_dir():
        for p in sorted(parts_root.glob("*.md")):
            _txt, n = _sweep_version_literals(p, _DOC_BANNER_RE, old, new, dry_run=dry_run)
            if n:
                swept[str(p.relative_to(root)).replace("\\", "/")] = n
    skills_root = root / "src" / "dos" / "skills"
    if skills_root.exists():
        for p in sorted(skills_root.rglob("*.md")):
            _txt, n = _sweep_version_literals(p, _SKILL_VERSION_RE, old, new, dry_run=dry_run)
            if n:
                swept[str(p.relative_to(root)).replace("\\", "/")] = n
    total = sum(swept.values())
    result = {
        "path": "docs+skills", "old": old, "new": new,
        "changed": total > 0, "ok": True,
        "files_swept": swept, "literals_rewritten": total,
    }
    if missing:
        # A missing FTUE doc is a soft warning, not a failure (the drift test would
        # fail louder on its own); record it so the skill sees the gap.
        result["missing_docs"] = missing
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=("Bump the DOS version markers (pyproject + __init__ fallback "
                     "+ the plugin manifest + marketplace entry + the FTUE "
                     "doc/skill version literals + the server.json registry manifest).")
    )
    parser.add_argument("version", help="New semver, e.g. 0.3.0 (no leading v)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print JSON plan without writing")
    parser.add_argument("--skip", action="append", default=[],
                        choices=["pyproject", "init", "plugin", "marketplace", "server", "docs", "llms_full"],
                        help="Skip a target (repeatable)")
    args = parser.parse_args()

    new_version = args.version.lstrip("v")
    if not SEMVER_RE.match(new_version):
        print(f"ERROR: {args.version!r} is not a valid semver (expected X.Y.Z)",
              file=sys.stderr)
        return 2

    root = repo_root()

    # The doc/skill sweep replaces the OLD version literal, so capture it from
    # pyproject BEFORE bump_pyproject overwrites it. Read-only, tolerant of a
    # missing/odd file (the doc sweep then no-ops on `old is None`).
    old_version = _current_pyproject_version(root)

    actions = {
        "pyproject": lambda: bump_pyproject(root, new_version, dry_run=args.dry_run),
        "init": lambda: bump_init(root, new_version, dry_run=args.dry_run),
        "plugin": lambda: bump_plugin_manifest(root, new_version, dry_run=args.dry_run),
        "gemini": lambda: bump_gemini_extension(root, new_version, dry_run=args.dry_run),
        "marketplace": lambda: bump_marketplace(root, new_version, dry_run=args.dry_run),
        "server": lambda: bump_server_json(root, new_version, dry_run=args.dry_run),
        # The FTUE doc/skill sweep is the ONE target keyed on the old→new pair (the
        # others write `new` unconditionally). It is intentionally LAST so a dry-run
        # plan reads top-down as code-markers-then-prose, and excluded from the
        # drift guard below (its "old" differs from "new" by design).
        "docs": lambda: bump_docs(root, old_version, new_version, dry_run=args.dry_run)
        if old_version else
        {"path": "docs+skills", "ok": True, "changed": False,
         "note": "could not read old version from pyproject — doc sweep skipped"},
        # LAST: rebuild llms-full.txt from the docs the sweep just touched (#139).
        # Runs unconditionally (not keyed on old→new) because the assembly can drift
        # for reasons beyond the version literal; reassembling is always the safe,
        # idempotent close — a no-op when already in sync.
        "llms_full": lambda: rebuild_llms_full(root, dry_run=args.dry_run),
    }

    report: dict = {"new_version": new_version, "old_version": old_version,
                    "dry_run": args.dry_run, "targets": {}}
    any_error = False
    final_values: list[str] = []
    for key, fn in actions.items():
        if key in args.skip:
            report["targets"][key] = {"skipped": True}
            continue
        try:
            result = fn()
        except Exception as exc:  # surface but don't crash — skill can edit by hand
            result = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        report["targets"][key] = result
        if not result.get("ok", True):
            any_error = True
        # The doc sweep is NOT a lockstep marker (it rewrites prose literals, not a
        # single canonical `new` scalar), so it never feeds the drift guard.
        elif key != "docs" and "new" in result:
            final_values.append(result["new"])

    # Drift guard: every marker must agree post-bump. This is the exact failure
    # mode the __init__.py comment + the plugin-manifest test record — catch it
    # mechanically rather than discovering it from a mis-versioned CLI or a red
    # plugin test later.
    drift = len(set(final_values)) > 1
    report["drift_after_bump"] = drift
    if drift:
        report["drift_reason"] = (
            "the version markers disagree after the bump (pyproject / __init__ "
            "fallback / plugin manifest / marketplace entry / server.json) — a "
            "source checkout would misreport, the plugin bundle would front the "
            "wrong version, or the registry publish would refuse on skew. "
            f"Reconcile all to the same value. Saw: {sorted(set(final_values))}"
        )

    print(json.dumps(report, indent=2))
    return 1 if (any_error or drift) else 0


if __name__ == "__main__":
    raise SystemExit(main())
