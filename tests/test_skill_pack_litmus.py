"""SKP litmus — the shipped skill pack is generic, complete, and host-free.

This is the consolidated rail behind the CLAUDE.md litmus "a shipped generic
skill names no host": it sweeps EVERY `SKILL.md` under `src/dos/skills/` (not
just the ones an individual phase test names), so a future skill cannot slip a
host literal past the suite. It also pins the pack's shape:

  * every skill is package DATA, not code (no `__init__.py` under skills/);
  * every skill has YAML frontmatter with a `name`;
  * none names a host directory, a job lane, or a job commit prefix.
"""

from __future__ import annotations

from pathlib import Path

import dos


SKILLS_ROOT = Path(dos.__file__).parent / "skills"

# The generic skills SKP ships (the reference workflow pack). The five workflow
# skills + `dos-supervise-loop` — the supervisor (init/PID-1) loop that keeps a
# target population of `dos-dispatch-loop` workers alive across the lane roster.
EXPECTED_SKILLS = {
    "dos-next-up", "dos-dispatch", "dos-dispatch-loop",
    "dos-replan", "dos-replan-loop", "dos-supervise-loop",
    "dos-witness-claim",
    # docs/207 Phase 5 — the operator tier: lifecycle gardening, picker-unblock,
    # the recurring-wedge remediation sweep.
    "dos-unstick", "dos-promote", "dos-class-cycle",
    # docs/259 sibling — the single-agent self-stop analogue of dos-witness-claim:
    # ground a /goal-style completion condition on `dos hook stop`, not self-report.
    "dos-goal-gate",
    # docs/280 — the recursive-self-improvement loop: propose→verify→measure→
    # keep-or-revert, where the kernel's `improve` verdict (not the agent's say-so)
    # decides whether each candidate change actually improved the codebase.
    "dos-self-improve",
    # The fan-OUT analogue of dos-goal-gate: wave-launch one self-stopping goal
    # worker per independent objective, co-launch-safe via `dos arbitrate`, with
    # every claimed ship confirmed by `dos verify` / `commit-audit`, not narration.
    "dos-goal-fleet",
}

# Host literals a generic skill must never name (the skill analogue of "kernel
# imports no host"). Host directories + job commit prefixes are exact-substring;
# job lanes are whole-word (so the English verb "apply" can't false-trip — but
# the skills are written to avoid even that).
FORBIDDEN_SUBSTRINGS = (
    "docs/_plans", "output/next-up", "docs/_chained_runs", "docs/_dispatch_loops",
    "docs/dispatch:", "docs/dispatch-loop:", "decisions-pending.md",
    "findings-followup-queue", "next-hits.md", "replan-state.yaml",
)
# The job CLUSTER lanes — the distinctive `--scope` names a generic skill must
# not hardcode (the SKP Phase-2 litmus list). `orchestration` (the job exclusive
# lane) is deliberately NOT here: as a whole word it is common English ("mostly
# sequencing/orchestration") and would false-trip prose; the three cluster names
# are the meaningful, distinctive guard.
FORBIDDEN_LANE_WORDS = ("apply", "tailor", "discovery")


def _all_skill_files() -> list[Path]:
    return sorted(SKILLS_ROOT.rglob("SKILL.md"))


def _all_host_litmus_files() -> list[Path]:
    """Every shipped skill-pack text the names-no-host litmus must guard — the
    `SKILL.md` screenplays AND the `EXAMPLES.md` cookbook (docs/171). The cookbook
    is package-data shipped in the wheel beside the skills and quotes the same
    syscall transcripts, so it must clear the same host-leak bar; it is excluded
    only from the structural tests (frontmatter / one-dir-per-skill) it cannot
    satisfy by design."""
    extra = sorted(SKILLS_ROOT.rglob("EXAMPLES.md"))
    return _all_skill_files() + extra


def test_pack_ships_the_generic_skills():
    found = {p.parent.name for p in _all_skill_files()}
    assert found == EXPECTED_SKILLS, f"skill set drift: {found} != {EXPECTED_SKILLS}"


def test_skills_are_data_not_a_package():
    """`src/dos/skills/` must have NO `__init__.py` — it is package-data, not a
    sub-package (the wheel ships it via [tool.setuptools.package-data])."""
    assert not (SKILLS_ROOT / "__init__.py").exists()
    for sub in SKILLS_ROOT.iterdir():
        if sub.is_dir():
            assert not (sub / "__init__.py").exists(), sub


def test_every_skill_has_frontmatter_name():
    import re
    for skill in _all_skill_files():
        text = skill.read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{skill} missing YAML frontmatter"
        fm = text.split("---\n", 2)[1]
        assert re.search(r"^name:\s*\S+", fm, re.MULTILINE), f"{skill} has no name:"


def test_no_shipped_skill_names_a_host_literal():
    for skill in _all_host_litmus_files():
        text = skill.read_text(encoding="utf-8")
        for token in FORBIDDEN_SUBSTRINGS:
            assert token not in text, f"{skill.name} names host literal {token!r}"


def test_no_shipped_skill_names_a_job_lane():
    import re
    for skill in _all_host_litmus_files():
        text = skill.read_text(encoding="utf-8")
        for lane in FORBIDDEN_LANE_WORDS:
            assert not re.search(rf"\b{lane}\b", text), \
                f"{skill.name} names job lane {lane!r} (use the active "
            f"taxonomy from `dos doctor --json`)"


def test_skills_shell_only_dos_verbs_not_host_scripts():
    """A generic skill drives the `dos` CLI, never a host's fat scripts — so no
    `python scripts/<name>.py` invocation appears in a shipped skill or the cookbook."""
    import re
    for skill in _all_host_litmus_files():
        text = skill.read_text(encoding="utf-8")
        # the job skills shell `python scripts/next_up_render.py` etc; a generic
        # skill must not — it shells `dos <verb>`.
        assert not re.search(r"python\s+scripts/", text), \
            f"{skill.name} shells a host script (use a `dos` verb)"
        assert not re.search(r"\bnext_up_render\b|\bfanout_state\b|\breplan_autoclose\b",
                             text), f"{skill.name} names a host orchestrator"
