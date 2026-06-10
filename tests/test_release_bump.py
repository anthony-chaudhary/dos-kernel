"""The release-bump tooling keeps EVERY version marker in lockstep.

`scripts/release_bump.py` is the one place a version is bumped. It single-sources
the package version across four markers (pyproject, the __init__ fallback, the
Claude Code plugin manifest, the marketplace plugin entry). This test pins the
DURABLE half of a fix: the v0.14/v0.15 cuts bumped pyproject + __init__ but left
the plugin bundle at 0.13.0, reddening `tests/test_plugin_manifest.py`. The cause
was that the bumper didn't know about the plugin files at all. So the regression
guard is structural — assert the bumper TARGETS all four markers and drives them
to one value — not just "today they happen to match" (that is
test_plugin_manifest's job; this is "they can't drift on the next release").

Like test_plugin_manifest, this loads the script by path (scripts/ is not an
importable package) so the test and the tool share one definition of the targets.

This is dev/release TOOLING, not kernel — it operates ON the package, never
imported BY it (the same one-way arrow as the rest of scripts/).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import dos

_REPO_ROOT = Path(dos.__file__).resolve().parents[2]
_BUMP_PY = _REPO_ROOT / "scripts" / "release_bump.py"

# The five markers the bumper must keep on the package's leash — the keys it
# reports under `targets`. The first four are LOCKSTEP markers (one canonical
# value, fed to the drift guard); `docs` is the FTUE doc/skill literal sweep,
# keyed on the old→new pair and excluded from the drift guard. If a refactor drops
# one (the exact way the plugin — then the docs — drifted), this set stops
# matching and the test fails loudly.
_LOCKSTEP_TARGETS = {"pyproject", "init", "plugin", "marketplace"}
_EXPECTED_TARGETS = _LOCKSTEP_TARGETS | {"docs"}


def _load_bump():
    spec = importlib.util.spec_from_file_location("_release_bump", _BUMP_PY)
    assert spec and spec.loader, f"cannot load {_BUMP_PY}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dry_run(version: str) -> dict:
    """Run the bumper in --dry-run (writes nothing) and return its JSON report."""
    proc = subprocess.run(
        [sys.executable, str(_BUMP_PY), version, "--dry-run"],
        capture_output=True, text=True, cwd=_REPO_ROOT,
    )
    assert proc.returncode == 0, f"bump --dry-run failed: {proc.stderr or proc.stdout}"
    return json.loads(proc.stdout)


def test_bump_covers_all_five_version_markers():
    """The bumper targets pyproject + __init__ + plugin + marketplace + docs.

    This is the structural regression guard: the plugin bundle drifted because the
    bumper had no `plugin`/`marketplace` target, then the FTUE docs/skills drifted
    because it had no `docs` target. Pin the full set so dropping one is caught
    here, not by a red plugin/version-drift test two releases later.
    """
    report = _dry_run("9.9.9")
    assert set(report["targets"]) == _EXPECTED_TARGETS, (
        f"release_bump targets {set(report['targets'])} != {_EXPECTED_TARGETS} — "
        "a version marker was added or dropped from the package-leash set")


def test_bump_drives_every_marker_to_one_value():
    """A dry-run bump reports the SAME new value for every marker (no drift).

    The four LOCKSTEP markers are driven to the requested value and feed the drift
    guard; the `docs` sweep is checked separately (it is keyed on old→new and is not
    a single-valued marker), so it is asserted only for `ok` + a `new` echo here.
    """
    report = _dry_run("9.9.9")
    assert report["drift_after_bump"] is False, report.get("drift_reason")
    for name, target in report["targets"].items():
        assert target.get("ok", True), f"{name} target failed: {target}"
        assert target.get("new") == "9.9.9", f"{name} new != 9.9.9: {target}"
        if name in _LOCKSTEP_TARGETS:
            assert target.get("changed") is True, (
                f"{name} should change when bumping to a fresh version: {target}")


def test_bump_docs_sweeps_the_ftue_literals_keyed_on_old_to_new():
    """The `docs` target sweeps the FTUE doc banners + skill samples old→new.

    The fifth target closes the gap the 2026-06-10 audit found: the doc/skill
    version literals were never on the bumper's leash, so v0.19.0 stranded them at
    0.18.0 (masked by stale install metadata). A dry-run to a fresh version must
    report a real sweep (changed, >0 literals rewritten) over the SAME files
    `test_docs_version_drift` checks — proving the bumper and that guard cover one
    surface, so a release can't strand the prose again.
    """
    report = _dry_run("9.9.9")
    docs = report["targets"]["docs"]
    assert docs.get("ok") is True, docs
    assert docs.get("changed") is True, f"docs sweep found nothing to rewrite: {docs}"
    assert docs.get("literals_rewritten", 0) > 0, docs
    # It must reach BOTH a doc banner and a skill sample (the two grammars), so the
    # sweep can never silently cover only half the surface.
    swept = docs.get("files_swept", {})
    assert any(f == "README.md" or f.startswith("docs/") or f.startswith("examples/")
               for f in swept), f"no FTUE doc banner swept: {swept}"
    assert any(f.startswith("src/dos/skills/") for f in swept), (
        f"no skill-pack sample swept: {swept}")
    # And `old` is the current package version (what it replaces FROM).
    assert docs.get("old") == report["old_version"], docs


def test_bump_sweep_covers_the_drift_guards_whole_roster():
    """Every doc the version-drift guard checks is on the bumper's sweep leash.

    `LIVE_ONBOARDING_DOCS` (the guard, tests/test_docs_version_drift.py) and
    `_DOC_BANNER_FILES` (the sweep) are two hand-kept lists — and they diverged
    exactly once: verify-action/README.md entered the guard's roster without
    entering the sweep's, so a bump stranded its `rev: vX.Y.Z` pin and the guard
    reddened one release later (caught by the 2026-06-10 go/version audit's bump
    simulation). Pin the RELATIONSHIP, not today's contents: guard ⊆ sweep, so
    the next roster addition reds here immediately, while it's being made.

    And because README.md is generated from docs/readme/ parts, the bumper must
    also sweep the parts dir — otherwise a bump desyncs the rendered README from
    its source and `test_readme_assembly` reds (same audit, same simulation).
    """
    bump = _load_bump()
    drift_spec = importlib.util.spec_from_file_location(
        "_drift_guard", Path(__file__).parent / "test_docs_version_drift.py")
    assert drift_spec and drift_spec.loader
    drift = importlib.util.module_from_spec(drift_spec)
    drift_spec.loader.exec_module(drift)

    guard = set(drift.LIVE_ONBOARDING_DOCS)
    sweep = set(bump._DOC_BANNER_FILES)
    assert guard <= sweep, (
        "the version-drift guard checks docs the bumper never sweeps — every "
        f"release strands them: {sorted(guard - sweep)}. Add them to "
        "_DOC_BANNER_FILES in scripts/release_bump.py."
    )
    parts_dir = _REPO_ROOT / bump._README_PARTS_DIR
    assert parts_dir.is_dir(), (
        f"the bumper's README-parts sweep points at a missing dir: {parts_dir}")


def test_bump_marketplace_targets_the_nested_plugin_version_not_the_catalog():
    """The marketplace has TWO version keys; the bumper must move only the plugin one.

    The top-level catalog version is a separate number; bumping it would be wrong.
    Confirm the bumper reads the nested `plugins[].version` (its reported `old` is
    the package version, not the catalog's) and leaves the catalog version intact.
    """
    marketplace = json.loads(
        (_REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    catalog_version = marketplace.get("version")
    plugin_entry = next(
        e for e in marketplace["plugins"] if e["name"] == "dos-kernel")
    nested_version = plugin_entry.get("version")

    report = _dry_run("9.9.9")
    mk = report["targets"]["marketplace"]
    # The bumper saw the NESTED version (matches the package today), not the catalog.
    assert mk.get("old") == nested_version, (
        f"marketplace bump read old={mk.get('old')!r}, expected the nested plugin "
        f"version {nested_version!r}")
    # ...and the catalog version is a different number it must not have touched.
    assert mk.get("old") != catalog_version or catalog_version == nested_version, (
        "the bump appears to have targeted the catalog version, not the plugin entry")


def test_bump_matches_test_plugin_manifest_expectation():
    """Lockstep with the package: a bump to dos.__version__ is a no-op (already synced).

    Closes the loop with test_plugin_manifest — after a release, re-bumping to the
    CURRENT package version changes nothing, proving every marker already tracks it.
    """
    report = _dry_run(dos.__version__)
    assert report["drift_after_bump"] is False, report.get("drift_reason")
    for name, target in report["targets"].items():
        assert target.get("changed") is False, (
            f"{name} is NOT in sync with the package version {dos.__version__}: "
            f"{target} — run `python scripts/release_bump.py {dos.__version__}`")
