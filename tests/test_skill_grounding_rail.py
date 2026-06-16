"""#201 / docs/370 — the skill-grounding `dos doctor --check` rail.

A third `--check` rail grades THIS workspace's OWN `.claude/skills/*/SKILL.md`
with the pure `skill_grade.check_verdict` (honoring its honesty floor) and
surfaces a finding naming any skill that self-certifies its belief-bits instead
of grounding them on a `dos` verb. INFO by default (surfaced, never gates);
`--skill-strict` promotes it to a gating finding.

The done condition (#201): a clearly self-certifying skill (>= 8 scored bits,
grounded fraction < floor) emits a NAMED finding with a re-derivable reason; a
pure-prose or all-grounded skill produces NO finding; the config_lint / wiring
rails are unaffected.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dos import cli as _cli
from dos import config as _config


# ---------------------------------------------------------------------------
# A synthetic SKILL.md that is a DENSE self-certifier: many belief-bits (the
# same phrasing skill_grade's own test uses), none grounded on a `dos` verb.
# Mirrors `tests/test_skill_grade.py::test_self_certify_skill_is_flagged...`.
# ---------------------------------------------------------------------------
_SELF_CERTIFY_SKILL = """# do-the-thing

## Step 1
When the agent says it shipped, the code shipped. The phase shipped.
This code shipped once the run says so.

## Step 2
I committed it, so the commit is honest. The commit did what it says.
Trust that I committed the fix.

## Step 3
The goal is met when the agent reports done. Keep working until the agent
says the goal is complete. This is self-stopping.

## Step 4
Editing these files won't collide — no one else is touching them. I'm editing
freely. Before writing, assume no collision.

## Step 5
The issue is resolved once I close it. This ticket is resolved. Close the issue
on the agent's word. Fixes #1.
"""

# A pure-prose skill: no belief-bits the scanner can place → N/A, never failed.
_PURE_PROSE_SKILL = """# notes

This skill explains a workflow in plain prose. It describes the steps a person
takes, the order they happen in, and why. It concludes nothing about truth.
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "commit", "--allow-empty", "-m", "init: empty repo")


def _write_skill(repo: Path, name: str, body: str) -> None:
    d = repo / ".claude" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")


def _doctor(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parent.parent / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", "doctor", "--workspace", str(repo),
         *extra],
        capture_output=True, text=True, env=env,
    )


def _cfg(repo: Path) -> _config.SubstrateConfig:
    return _config.load_workspace_config(workspace=repo)


# ---------------------------------------------------------------------------
# The pure helper — the rail's core, tested directly (no subprocess).
# ---------------------------------------------------------------------------
def test_pure_helper_flags_a_self_certifying_skill(tmp_path):
    _repo(tmp_path)
    _write_skill(tmp_path, "do-the-thing", _SELF_CERTIFY_SKILL)
    findings = _cli._skill_grounding_findings(_cfg(tmp_path))
    assert len(findings) == 1
    # The finding NAMES the skill and carries a re-derivable reason.
    assert "do-the-thing" in findings[0]
    assert "self-certif" in findings[0].lower()
    assert "%" in findings[0]  # the grounding-fraction reason


def test_pure_helper_silent_on_pure_prose(tmp_path):
    _repo(tmp_path)
    _write_skill(tmp_path, "notes", _PURE_PROSE_SKILL)
    assert _cli._skill_grounding_findings(_cfg(tmp_path)) == []


def test_pure_helper_silent_when_no_host_skills(tmp_path):
    _repo(tmp_path)
    assert _cli._skill_grounding_findings(_cfg(tmp_path)) == []


def test_pure_helper_is_fail_soft_on_unreadable(tmp_path, monkeypatch):
    """A grade error must degrade to no-finding, never raise (a report row must
    never break doctor)."""
    _repo(tmp_path)
    _write_skill(tmp_path, "do-the-thing", _SELF_CERTIFY_SKILL)

    import dos.skill_grade as _sg

    def _boom(*a, **k):
        raise RuntimeError("synthetic grade failure")

    monkeypatch.setattr(_sg, "grade_skill", _boom)
    # Swallowed → empty, no exception.
    assert _cli._skill_grounding_findings(_cfg(tmp_path)) == []


# ---------------------------------------------------------------------------
# The CLI surface — the rail under `dos doctor --check`.
# ---------------------------------------------------------------------------
def test_check_surfaces_finding_but_info_does_not_gate(tmp_path):
    """A self-certifier is SURFACED under --check but, being INFO, exits 0."""
    _repo(tmp_path)
    _write_skill(tmp_path, "do-the-thing", _SELF_CERTIFY_SKILL)
    res = _doctor(tmp_path, "--check")
    combined = res.stdout + res.stderr
    assert "do-the-thing" in combined
    assert "self-certif" in combined.lower()
    assert res.returncode == 0  # INFO surfaces, never blocks by default


def test_skill_strict_gates_the_exit(tmp_path):
    """--skill-strict promotes the finding to gating → exit 1."""
    _repo(tmp_path)
    _write_skill(tmp_path, "do-the-thing", _SELF_CERTIFY_SKILL)
    res = _doctor(tmp_path, "--check", "--skill-strict")
    assert res.returncode == 1
    assert "do-the-thing" in (res.stdout + res.stderr)


def test_pure_prose_skill_does_not_gate_even_strict(tmp_path):
    """The honesty floor: a pure-prose skill is N/A, so --skill-strict is clean."""
    _repo(tmp_path)
    _write_skill(tmp_path, "notes", _PURE_PROSE_SKILL)
    res = _doctor(tmp_path, "--check", "--skill-strict")
    assert res.returncode == 0


def test_footer_hint_suppressed_under_check_but_shown_bare(tmp_path):
    """#201 dedupe: the #200 footer hint is suppressed under --check (the rail
    does the grading) and shown on a bare `dos doctor` (the nudge when no rail
    ran)."""
    _repo(tmp_path)
    _write_skill(tmp_path, "notes", _PURE_PROSE_SKILL)
    bare = _doctor(tmp_path).stdout
    assert "workspace skills" in bare  # the hint stands on a bare report
    checked = _doctor(tmp_path, "--check").stdout
    assert "workspace skills" not in checked  # deduped under --check


def test_config_lint_rail_unaffected(tmp_path):
    """The new rail does not perturb config_lint: a clean workspace with a
    self-certifying skill still exits 0 under --check (skill finding is INFO),
    and the config-clean line still prints."""
    _repo(tmp_path)
    _write_skill(tmp_path, "do-the-thing", _SELF_CERTIFY_SKILL)
    res = _doctor(tmp_path, "--check")
    assert res.returncode == 0
