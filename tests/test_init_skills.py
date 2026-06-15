"""`dos init --skills` — the editable-skill on-ramp (docs/207 Phase 7).

`dos init` scaffolds `dos.toml`; `--skills` ALSO copies the generic SKILL.md
screenplays into the workspace's `.claude/skills/` as EDITABLE LOCAL FILES (the
package-data is the seed, not a runtime binding). Pins:

  * `test_init_skills_copies_editable` — `--skills` writes the selected SKILL.md
    files as plain editable files; re-running is idempotent (no clobber of a
    diverged local copy without `--force`);
  * `test_init_skills_grep_clean` — a copied skill still names no host literal (the
    seed is the shipped generic, which already passed the grep litmus).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import dos


def _cli(*argv: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(Path(dos.__file__).parents[1])}
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *argv],
        capture_output=True, text=True, env=env,
    )


_CORE = {"dos-next-up", "dos-dispatch", "dos-dispatch-loop", "dos-replan"}


def test_init_skills_copies_editable(tmp_path: Path):
    dest = tmp_path / "svc"
    proc = _cli("init", "--skills", str(dest))
    assert proc.returncode == 0, proc.stderr
    skills_dir = dest / ".claude" / "skills"
    assert skills_dir.is_dir()
    found = {p.parent.name for p in skills_dir.rglob("SKILL.md")}
    assert found == _CORE, f"core set not copied: {found}"
    # The copies are plain editable files (real bytes, not symlinks/stubs).
    for name in _CORE:
        f = skills_dir / name / "SKILL.md"
        assert f.is_file() and not f.is_symlink()
        assert f.read_text(encoding="utf-8").startswith("---\n")  # the frontmatter


def test_init_skills_idempotent_no_clobber(tmp_path: Path):
    dest = tmp_path / "svc"
    _cli("init", "--skills", str(dest))
    # Diverge a local copy.
    local = dest / ".claude" / "skills" / "dos-dispatch" / "SKILL.md"
    local.write_text("--- DIVERGED LOCAL EDIT ---\n", encoding="utf-8")
    # Re-run without --force: the diverged copy is NOT clobbered.
    proc = _cli("init", "--skills", str(dest))
    assert proc.returncode == 0, proc.stderr
    assert "DIVERGED LOCAL EDIT" in local.read_text(encoding="utf-8")
    assert "skipped" in proc.stdout.lower()


def test_init_skills_force_overwrites(tmp_path: Path):
    dest = tmp_path / "svc"
    _cli("init", "--skills", str(dest))
    local = dest / ".claude" / "skills" / "dos-dispatch" / "SKILL.md"
    local.write_text("--- DIVERGED ---\n", encoding="utf-8")
    proc = _cli("init", "--skills", "--force", str(dest))
    assert proc.returncode == 0, proc.stderr
    # --force restores the shipped version.
    assert "DIVERGED" not in local.read_text(encoding="utf-8")
    assert local.read_text(encoding="utf-8").startswith("---\nname: dos-dispatch")


def test_init_skill_named_repeatable(tmp_path: Path):
    dest = tmp_path / "svc"
    proc = _cli("init", "--skill", "dos-promote", "--skill", "dos-unstick", str(dest))
    assert proc.returncode == 0, proc.stderr
    found = {p.parent.name for p in (dest / ".claude" / "skills").rglob("SKILL.md")}
    assert found == {"dos-promote", "dos-unstick"}


def test_init_all_copies_full_pack(tmp_path: Path):
    dest = tmp_path / "svc"
    proc = _cli("init", "--all", str(dest))
    assert proc.returncode == 0, proc.stderr
    found = {p.parent.name for p in (dest / ".claude" / "skills").rglob("SKILL.md")}
    assert len(found) == 13  # the full pack (7 SKP + 3 operator + dos-goal-gate + dos-self-improve + dos-goal-fleet)


def test_init_unknown_skill_warns_and_exits_nonzero(tmp_path: Path):
    dest = tmp_path / "svc"
    proc = _cli("init", "--skill", "bogus-skill", str(dest))
    assert proc.returncode == 1
    assert "unknown skill" in proc.stderr.lower()


def test_init_skills_into_existing_workspace(tmp_path: Path):
    dest = tmp_path / "svc"
    # First init writes dos.toml.
    _cli("init", str(dest))
    toml = (dest / "dos.toml").read_text(encoding="utf-8")
    # --skills into the existing workspace leaves dos.toml alone, adds skills.
    proc = _cli("init", "--skills", str(dest))
    assert proc.returncode == 0, proc.stderr
    assert (dest / "dos.toml").read_text(encoding="utf-8") == toml  # untouched
    assert (dest / ".claude" / "skills" / "dos-dispatch" / "SKILL.md").is_file()


def test_init_skills_grep_clean(tmp_path: Path):
    """A copied skill still names no host literal — the seed is the shipped generic
    that already passed the grep litmus. Re-checks the copies in the dest."""
    dest = tmp_path / "svc"
    _cli("init", "--all", str(dest))
    forbidden = (
        "docs/_plans", "output/next-up", "docs/_chained_runs", "docs/_dispatch_loops",
        "docs/dispatch:", "decisions-pending.md", "findings-followup-queue",
        "next-hits.md", "replan-state.yaml",
    )
    for f in (dest / ".claude" / "skills").rglob("SKILL.md"):
        text = f.read_text(encoding="utf-8")
        for tok in forbidden:
            assert tok not in text, f"{f.parent.name} names host literal {tok!r}"
        for lane in ("apply", "tailor", "discovery"):
            assert not re.search(rf"\b{lane}\b", text), \
                f"{f.parent.name} names job lane {lane!r}"
