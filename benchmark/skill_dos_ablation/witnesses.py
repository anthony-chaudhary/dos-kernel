"""The byte-clean witness rungs the `-dos` skill variants ground on — deterministic, $0, offline.

A skill concludes "done / shipped / found / completed" at a TRUST SEAM. The original skill closes
that seam on the agent's OWN self-report (`final_text`, forgeable). `dos-skillify` (docs/345) grounds
the seam on a witness the judged agent did NOT author. This module is that witness rung, modeled
faithfully on the shipped kernel seams but kept SELF-CONTAINED so the harness is a clean consumer
(the one-way arrow: nothing under `src/dos/` is imported, and the harness can never be entangled in a
SELF_MODIFY refusal on a hot tree).

Each witness reads ONLY env-authored / git-authored bytes in the fixture trajectory, never the
agent's `final_text` claim:

  * `witness_phase_shipped`   — git ancestry: is the claimed ship-commit in the recorded git log?
                                (the `dos verify` / `commit-audit` discipline — the subject is
                                forgeable, the log is not).
  * `witness_worker_alive`    — env-authored terminal: did the fan-out worker's recorded terminal
                                carry a non-zero exit / error channel? (the `terminal_error` rung).
  * `witness_loop_advanced`   — env-authored tool results: did the tool-result stream produce NEW
                                bytes, or return identical `result_digest` N times? (the
                                `tool_stream` no-advance rung — the env authors the *identity* of a
                                repeated output, not the agent).
  * `witness_memory_fresh`    — git working-tree: does the code token the recalled memory CLAIMS is
                                present still appear in the recorded working tree? (the `dos recall`
                                staleness rung).

THE NEGATIVE, named up front (docs/333 honesty floor): `witness_prose_taste` is the rung a pure-prose
/ taste skill would expose — and it has NOTHING env-authored to read. Its only available signal IS
the agent's own `final_text`, so grounding it changes nothing: a forgeable rung stays forgeable. The
harness uses this to demonstrate a skill DOS does NOT help, without crashing or hiding it.
"""

from __future__ import annotations

from typing import Optional

# The verdict a witness returns about a single task's recorded trajectory.
#   "FAILED"        — the witness has env/git evidence the work did NOT land (refuse the success claim).
#   "WITNESSED_OK"  — the witness has positive env/git evidence the work landed (corroborates success).
#   "UNWITNESSABLE" — this rung has nothing env-authored to read here; it cannot ground the claim.
FAILED = "FAILED"
WITNESSED_OK = "WITNESSED_OK"
UNWITNESSABLE = "UNWITNESSABLE"


def witness_phase_shipped(traj: dict) -> str:
    """Git-ancestry rung. The trajectory records a CLAIMED ship-commit subject and the actual recorded
    `git_log` (env/git-authored). The claim lands iff a log entry's subject matches the claim.

    A rigged claimed-but-not-shipped phase records a `claimed_commit` whose subject is absent from
    `git_log` — the witness returns FAILED (refuses the success claim) where the original skill, reading
    only the agent's `final_text`, would declare success.
    """
    claimed = traj.get("claimed_commit")
    log = traj.get("git_log")
    if not claimed or log is None:
        return UNWITNESSABLE
    return WITNESSED_OK if claimed in log else FAILED


def witness_worker_alive(traj: dict) -> str:
    """Env-authored-terminal rung (`terminal_error`). The fan-out worker's recorded terminal carries an
    env-authored `exit_code` and/or an error channel the harness/gym wrote — never the agent. A worker
    that died synthetically records a non-zero exit (or an error-channel line); the witness returns
    FAILED."""
    term = traj.get("worker_terminal")
    if term is None:
        return UNWITNESSABLE
    exit_code = term.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        return FAILED
    if term.get("error_channel"):
        return FAILED
    return WITNESSED_OK


def witness_loop_advanced(traj: dict) -> str:
    """Tool-stream no-advance rung (`tool_stream`). The recorded `tool_results` are env-authored byte
    payloads. The witness asks the pure byte question the kernel asks — "did the env return the same
    bytes again, with no new output?" — never "is the agent making real progress" (that is a
    forgeable satisfaction predicate the kernel refuses).

    A no-progress loop records >=2 identical `tool_results` and no distinct final result → FAILED. A
    loop that eventually advanced records a distinct trailing result → WITNESSED_OK.
    """
    results = traj.get("tool_results")
    if not results or len(results) < 2:
        return UNWITNESSABLE
    # The env authored the identity of these payloads. If every recorded result is byte-identical,
    # the stream never advanced (a doomed re-read / poll loop that made no progress).
    if len(set(results)) == 1:
        return FAILED
    return WITNESSED_OK


def witness_memory_fresh(traj: dict) -> str:
    """Recall-staleness rung (`dos recall`). The recalled memory CLAIMS a code token is PRESENT; the
    recorded `working_tree` is the git working-tree text at recall time (env-authored). The memory is
    stale iff the token it asserts present is absent from the tree → FAILED."""
    token = traj.get("recall_token")
    tree = traj.get("working_tree")
    if token is None or tree is None:
        return UNWITNESSABLE
    return WITNESSED_OK if token in tree else FAILED


def witness_prose_taste(traj: dict) -> str:
    """THE NEGATIVE rung (docs/333). A pure-prose / taste skill ("rewrite this paragraph more clearly")
    concludes from its own judgment. There is NOTHING env-authored to read — the trajectory carries no
    git log, no terminal, no tool-result stream, no recall token. So this rung is structurally
    UNWITNESSABLE on every task: `dos-skillify` cannot ground a seam that has no non-forgeable byte to
    ground on. The harness reports this honestly rather than pretending DOS helped.
    """
    return UNWITNESSABLE


# The witness rung each skill's trust seam maps to. The negative skill maps to the UNWITNESSABLE rung.
WITNESS_BY_SKILL = {
    "ship-verify": witness_phase_shipped,     # "did the phase ship?" -> git ancestry
    "fanout-collect": witness_worker_alive,   # "did the fan-out worker finish?" -> env terminal
    "loop-finish": witness_loop_advanced,     # "did the tool loop make progress?" -> tool-stream
    "memory-recall": witness_memory_fresh,    # "is this recalled fact still true?" -> recall staleness
    "prose-polish": witness_prose_taste,      # NEGATIVE: pure-prose, no env byte to ground -> no help
}
