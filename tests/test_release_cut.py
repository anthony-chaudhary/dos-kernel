"""The mechanical CUT — the file set is the bump report, the commit is a pathspec.

`scripts/release_cut.py` performs the non-interactive half of `/release`: bump +
notes + tag-last witness + a pathspec commit, NO push, NO tag (those are the
workflow's gated job). This test pins the parts that must not regress without
mutating the repo:

  * the dry-run CLI contract emits a well-formed manifest with the right tag,
    pathspec, and `dry_run: true` (it writes nothing);
  * the pathspec is derived FROM the bump report (so a future bump target is
    picked up, never hardcoded) and always carries the notes file;
  * the notes drafter builds front-matter matching the shape
    `release_context.prior_release_style` parses, clusters by scope, and NEVER
    clobbers an existing notes file (a racing skill/cadence run).

Loaded by path — the `tests/test_release_bump.py` convention. Dev/release
TOOLING, never imported by the kernel.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import dos

_REPO_ROOT = Path(dos.__file__).resolve().parents[2]
_CUT_PY = _REPO_ROOT / "scripts" / "release_cut.py"


def _load():
    spec = importlib.util.spec_from_file_location("_release_cut", _CUT_PY)
    assert spec and spec.loader, f"cannot load {_CUT_PY}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- the file-set derivation (pure) ----------------------------------------

def test_paths_from_bump_report_collects_every_target():
    rc = _load()
    report = {
        "targets": {
            "pyproject": {"path": "pyproject.toml", "changed": True},
            "init": {"path": "src/dos/__init__.py", "changed": True},
            "server": {"path": "server.json", "changed": True},
            "docs": {"path": "docs+skills", "files_swept": {
                "README.md": 1, "docs/QUICKSTART.md": 2}},
            "llms_full": {"path": "llms-full.txt", "changed": True},
        }
    }
    paths = rc._paths_from_bump_report(report)
    assert "pyproject.toml" in paths
    assert "src/dos/__init__.py" in paths
    assert "server.json" in paths
    assert "llms-full.txt" in paths
    # the doc sweep's real files come through; the "docs+skills" LABEL never does
    assert "README.md" in paths
    assert "docs/QUICKSTART.md" in paths
    assert "docs+skills" not in paths
    # no duplicates
    assert len(paths) == len(set(paths))


def test_default_headline_is_machine_honest():
    rc = _load()
    h = rc._default_headline("0.27.0", "minor", ["arbiter", "docs"])
    assert h.startswith("Minor")
    assert "arbiter" in h
    # no themes → still a valid headline naming the version
    assert "0.27.0" in rc._default_headline("0.27.0", "patch", [])


# ---- the notes drafter (pure, tmp dir) -------------------------------------

def test_draft_notes_writes_parseable_frontmatter_and_clusters_by_scope(tmp_path):
    rc = _load()
    commits = [
        {"subject": "feat(arbiter): admit in-lane child edits"},
        {"subject": "fix(arbiter): resolve the ancestor lease"},
        {"subject": "docs(readme): trim the deep-dive"},
        {"subject": "v0.26.0: a prior release commit"},  # skipped
        {"subject": "an unscoped subject"},               # → general bucket
    ]
    path, wrote = rc.draft_notes(
        tmp_path, "0.27.0", level="minor", themes=["arbiter", "readme"],
        headline="Minor — arbiter, readme", commits=commits, today="2026-06-16",
    )
    assert wrote is True
    text = path.read_text(encoding="utf-8")
    # front-matter the release_context parser keys on
    assert text.startswith("---\n")
    assert "version: 0.27.0" in text
    assert "date: 2026-06-16" in text
    assert 'headline: "Minor — arbiter, readme"' in text
    assert "themes: [" in text and '"arbiter"' in text
    assert "highlights:" in text
    # body clustered by scope; the prior release commit is not echoed
    assert "## `arbiter`" in text
    assert "## `readme`" in text
    assert "## `general`" in text          # the unscoped subject landed here
    assert "v0.26.0: a prior release commit" not in text


def test_draft_notes_never_clobbers_existing(tmp_path):
    rc = _load()
    rel_dir = tmp_path / "docs" / "releases"
    rel_dir.mkdir(parents=True)
    existing = rel_dir / "v0.27.0.md"
    existing.write_text("hand-written notes\n", encoding="utf-8")
    path, wrote = rc.draft_notes(
        tmp_path, "0.27.0", level="minor", themes=[], headline="x",
        commits=[{"subject": "feat: y"}], today="2026-06-16",
    )
    assert wrote is False
    assert path.read_text(encoding="utf-8") == "hand-written notes\n"  # untouched


# ---- the CLI contract (subprocess, dry-run — writes nothing) ---------------

def test_dry_run_cli_emits_manifest_without_mutating(tmp_path):
    """A dry-run cut over the real repo plans the file set + tag and writes NOTHING.

    We assert the manifest shape + that it carries the markers and the notes file,
    and confirm the working tree is unchanged (the dry-run mutates no tracked file)."""
    before = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=_REPO_ROOT,
    ).stdout
    proc = subprocess.run(
        [sys.executable, str(_CUT_PY), "9.9.9", "--level", "minor", "--themes", "arbiter,docs"],
        capture_output=True, text=True, cwd=_REPO_ROOT,
    )
    assert proc.returncode == 0, f"dry-run failed: {proc.stderr or proc.stdout}"
    manifest = json.loads(proc.stdout)
    assert manifest["dry_run"] is True
    assert manifest["tag"] == "v9.9.9"
    assert manifest["version"] == "9.9.9"
    assert manifest["ok"] is True
    # the pathspec carries the lockstep markers + the notes file
    assert "pyproject.toml" in manifest["paths"]
    assert "src/dos/__init__.py" in manifest["paths"]
    assert manifest["notes_file"] == "docs/releases/v9.9.9.md"
    assert manifest["notes_file"] in manifest["paths"]
    # the dry-run wrote nothing — the tree is exactly as it was
    after = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=_REPO_ROOT,
    ).stdout
    assert after == before, "dry-run cut mutated the working tree"


def test_cli_rejects_a_bad_version():
    proc = subprocess.run(
        [sys.executable, str(_CUT_PY), "not-a-version"],
        capture_output=True, text=True, cwd=_REPO_ROOT,
    )
    assert proc.returncode == 2
    assert "not a valid semver" in (proc.stderr + proc.stdout)


# ---- tag-behind-source-drift RECOVERY (the cadence-wedge fix) ---------------
# When a prior cut bumped + committed the version but never tagged it, the bump
# is a no-op on the next tick and the notes file already exists, so there is
# NOTHING to commit. The old code aborted on the empty `git commit`, which wedged
# the cadence forever. The cut must instead report the existing HEAD as the commit
# to tag (idempotent recovery). We drive `cut()` against a throwaway git repo and
# stub only the two NON-git helper scripts (release_bump / build_plugin) so the
# real git interaction — the empty-stage probe + rev-parse — is exercised.

def _init_repo(tmp_path, version: str):
    """A minimal git repo whose source is ALREADY at `version` with notes drafted —
    the tag-behind-source state. Returns the root path."""
    root = tmp_path
    (root / "pyproject.toml").write_text(f'version = "{version}"\n', encoding="utf-8")
    rel = root / "docs" / "releases"
    rel.mkdir(parents=True)
    (rel / f"v{version}.md").write_text("notes already drafted by the prior cut\n",
                                        encoding="utf-8")
    skills = root / "claude-plugin" / "skills"
    skills.mkdir(parents=True)
    (skills / ".gitkeep").write_text("", encoding="utf-8")
    # Repo-LOCAL identity (not env): cut()'s own `git commit` runs via _run
    # without inheriting any env, and CI runners have no global git identity —
    # local config is what makes the in-cut commit succeed there (it passed on a
    # dev box only because of a global identity).
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"],
                ["git", "add", "-A"],
                ["git", "commit", "-q", "-m", "base"]):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True, text=True)
    return root


def _stub_helpers(rc, version: str, *, bump_changed: bool):
    """Patch rc._run so the bump/plugin scripts are canned but git runs for real.

    bump_changed=False → the no-op (recovery) report (old == new == version);
    True → a report whose old != version (a genuine fresh bump)."""
    orig = rc._run

    def fake_run(cmd, *, cwd, timeout=600):
        arg1 = str(cmd[1]) if len(cmd) > 1 else ""
        if arg1.endswith("release_bump.py"):
            old = version if not bump_changed else "0.0.1"
            return 0, json.dumps({
                "new_version": version, "old_version": old, "dry_run": False,
                "targets": {"pyproject": {"path": "pyproject.toml", "old": old,
                                          "new": version, "changed": bump_changed,
                                          "ok": True}},
            })
        if arg1.endswith("build_plugin.py"):
            return 0, ""
        return orig(cmd, cwd=cwd, timeout=timeout)

    rc._run = fake_run


def _head(root) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                          capture_output=True, text=True).stdout.strip()


def _count(root) -> int:
    return int(subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=root,
                              capture_output=True, text=True).stdout.strip())


def test_cut_recovers_when_version_already_bumped_but_untagged(tmp_path):
    rc = _load()  # a fresh module instance per test — stubbing rc._run is isolated
    version = "0.28.0"
    root = _init_repo(tmp_path, version)
    head_before, count_before = _head(root), _count(root)
    _stub_helpers(rc, version, bump_changed=False)

    manifest = rc.cut(root, version, execute=True, skip_dry_run=True,
                      level="minor", themes=["x"], headline="h", today="2026-06-17")

    # Recovery: NO new commit, HEAD reported as the commit to tag, ok=True.
    assert manifest["recovered"] is True, manifest
    assert manifest["ok"] is True
    assert manifest["aborted"] is None
    assert manifest["commit_sha"] == head_before
    assert _count(root) == count_before, "recovery must not create a commit"
    assert _head(root) == head_before


def test_cut_commits_normally_when_the_bump_actually_changes_the_tree(tmp_path):
    rc = _load()
    version = "0.28.0"
    root = _init_repo(tmp_path, version)
    count_before = _count(root)
    _stub_helpers(rc, version, bump_changed=True)
    # A real change to stage (the bump "moved" pyproject), so the cut commits.
    (root / "pyproject.toml").write_text(f'version = "{version}"  # bumped\n',
                                         encoding="utf-8")

    manifest = rc.cut(root, version, execute=True, skip_dry_run=True,
                      level="minor", themes=["x"], headline="h", today="2026-06-17")

    assert manifest["recovered"] is False, manifest
    assert manifest["ok"] is True
    assert _count(root) == count_before + 1, "a real bump must create exactly one commit"
