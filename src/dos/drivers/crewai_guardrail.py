"""dos.drivers.crewai_guardrail — the dos verdict at CrewAI's task-output seam (docs/305).

CrewAI runs a task ``guardrail`` callable over each task's output: it returns
``(True, value)`` to pass ``value`` onward, or ``(False, reason)`` to send
``reason`` back to the agent and RE-RUN the task (up to
``guardrail_max_retries``, default 3). That retry loop is the seat for an
env-evidence verdict: when a crew task's declared deliverable (a commit, a
file, a shipped phase) is ABSENT from a read-back the agent did not author,
the guardrail fails the task with the typed, actionable reason — and the crew
re-runs it instead of believing the narration.

    from crewai import Agent, Task, Crew
    from dos.drivers.crewai_guardrail import dos_task_guardrail
    from dos.drivers._effect_gate import CommitClaim

    fix_task = Task(
        description="Fix the bug in parser.py and commit the fix.",
        expected_output="A landed commit.",
        agent=fixer,
        # Build the guardrail BEFORE kickoff: a CommitClaim() pins its
        # baseline to HEAD here, so anything the task lands is visible.
        guardrail=dos_task_guardrail(".", expect=[CommitClaim()]),
        guardrail_max_retries=2,
    )

The dependency arrow points at us — and further than usual: this module
imports NOTHING from crewai, ever. CrewAI's guardrail contract is a plain
callable returning a plain tuple, and the input only needs ``.raw`` — so the
adapter is fully importable, testable, and runnable without the host package
(no install hint needed; there is nothing to install on OUR side). Layer-4
driver — the same rule that lets ``agt_backend.py`` name AGT lets this module
name CrewAI in prose.

The verdict mapping (docs/305 §1)
=================================

  * gate ``TRIPPED``   → ``(False, reason)`` — the reason names what was
    claimed, what the witness saw, and what would make it pass; CrewAI hands
    it to the agent and re-runs the task. The retry loop becomes a
    do-the-work loop instead of a reword-the-narration loop.
  * gate ``CLEAR`` / ``ABSTAINED`` / ``NO_CLAIM`` → ``(True, result)`` — the
    received output object, unchanged (identity-safe in a ``guardrails=[...]``
    chain). Fail-to-abstain (docs/86): only a refutation fails the task;
    "could not tell" never burns a retry.

A host that wants the abstain VISIBLE (not just non-blocking) reads the gate
directly: build an `EffectGate` and call ``adjudicate`` post-kickoff — the
``examples/fleet_frameworks`` recipes show both layers.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from dos.drivers._effect_gate import Claim, EffectGate

__all__ = ["dos_task_guardrail"]


def dos_task_guardrail(
    workspace: str | None = None,
    *,
    config: Any = None,
    expect: Sequence[Claim] = (),
    extract: Callable[[str], Sequence[Claim]] | None = None,
) -> Callable[[Any], tuple[bool, Any]]:
    """Build the CrewAI task guardrail callable over the shared effect gate.

    Parameters mirror `EffectGate` (workspace/config/expect/extract). Returns
    a plain ``(task_output) -> (bool, Any)`` callable obeying CrewAI's
    contract — attach via ``Task(guardrail=...)``. The input is duck-typed:
    ``task_output.raw`` when present (CrewAI's ``TaskOutput``), else
    ``str(task_output)``.
    """
    gate = EffectGate(workspace, config=config, expect=expect, extract=extract)

    def dos_effect_gate(task_output: Any) -> tuple[bool, Any]:
        raw = getattr(task_output, "raw", None)
        text = str(task_output if raw is None else raw)
        verdict = gate.adjudicate(text)
        if verdict.tripped:
            return (False, verdict.reason)
        return (True, task_output)

    return dos_effect_gate
