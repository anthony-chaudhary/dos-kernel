"""Recipe 6 — CrewAI: the SHIPPED task guardrail (the driver form of Recipe 2).

Recipe 2 wires the oracle in by hand (a tool + a post-kickoff gate). This is
the same distrust as one shipped line: `dos.drivers.crewai_guardrail` puts the
effect gate in CrewAI's task-guardrail seat, so a task whose declared
deliverable (here: a commit) is ABSENT from the git read-back FAILS with the
actionable reason — and CrewAI's own retry loop re-runs the task instead of
the crew believing the narration. In your real crew:

    from crewai import Task
    from dos.drivers.crewai_guardrail import dos_task_guardrail
    from dos.drivers._effect_gate import CommitClaim

    fix_task = Task(
        description="Fix the bug and commit the fix.",
        expected_output="A landed commit.",
        agent=fixer,
        # built BEFORE kickoff: CommitClaim() pins its baseline to HEAD here
        guardrail=dos_task_guardrail(".", expect=[CommitClaim()]),
        guardrail_max_retries=2,
    )

    python examples/fleet_frameworks/crewai_task_guardrail.py

Needs `dos` ONLY — the adapter imports nothing from crewai (its contract is a
plain `(task_output) -> (bool, reason_or_value)` tuple over `.raw`), so the
demo simulates the task outputs and you watch the retry loop's inputs.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from dos.drivers._effect_gate import CommitClaim
from dos.drivers.crewai_guardrail import dos_task_guardrail

from _fixture import make_demo_repo


def run_demo(repo=None) -> dict:
    cfg = make_demo_repo(repo)
    # Built before the "run": the CommitClaim baseline pins to HEAD here.
    guardrail = dos_task_guardrail(str(cfg.root), expect=[CommitClaim()])

    # Attempt 1 — the worker narrates success; no commit exists. CrewAI would
    # hand the False-reason back to the agent and re-run the task.
    attempt1 = guardrail(SimpleNamespace(
        raw="Done! Implemented the rate limiter and committed the fix."))

    # The retry actually does the work (scripted here — the seam is the demo).
    (cfg.root / "limiter.py").write_text("def limit(): ...\n", encoding="utf-8")
    subprocess.run(["git", "add", "limiter.py"], cwd=cfg.root, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "RATE1: land the rate limiter"],
                   cwd=cfg.root, check=True, capture_output=True)

    # Attempt 2 — the SAME guardrail instance now sees the commit.
    out2 = SimpleNamespace(raw="Done (for real this time).")
    attempt2 = guardrail(out2)

    return {"attempt1": attempt1, "attempt2": attempt2, "attempt2_output": out2}


def main() -> int:
    r = run_demo()
    ok1, reason = r["attempt1"]
    ok2, value = r["attempt2"]
    print(f"attempt 1 (over-claim) -> pass={ok1}")
    print(f"  retry feedback: {reason}")
    print(f"attempt 2 (work landed) -> pass={ok2}  (output passed through unchanged: "
          f"{value is r['attempt2_output']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
