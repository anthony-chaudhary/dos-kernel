"""A reference value estimator for the value-aware picker (docs/91 Phase 4).

This is the `rank_key` the arbiter ranks ready candidates by — the concrete
demonstration of the seam, kept FIRMLY outside the kernel. `src/dos/` ships the
`rank_key` seam (`arbiter.py`) and a no-op default (rank everything equal ⇒
ladder order); it never ships a real estimator. This module IS a real one, and it
lives in the benchmark exactly as a host's own yield oracle would live in a
driver (docs/91 §4, the same kernel/driver split as the judge and renderer seams).

The litmus this module must keep (docs/91 §3):

* It imports NOTHING from `src/dos/` — value is a host concept, never a kernel one.
* It is BLIND to the failure model's lie/flake rolls. The numerator is EXPECTED
  real-ships (the phases an effort has left to do), never the actual outcomes —
  the picker cannot know which claims will be lies; `verify` catches lies, the
  picker does not. Peeking at the roll would be cheating, and would make the
  benchmark's win an artifact of an oracle no production picker could have.
* It is pure and total: it reads only the workload's static structure plus a
  reference to the live cursors, and returns `None` (no opinion) rather than
  raising when it cannot score — the same fail-soft discipline as the arbiter's
  `_safe_rank` (a raising estimator falls back to ladder order, never crashes).

The shape: rank a ready effort by its remaining VERIFIABLE yield, DEMOTING the
ones whose NEXT phase reaches into shared state while other efforts are live —
those are the picks that collide, get refused, and burn a retry action for no
bank. Front-loading the phases that admit cleanly banks more verified work per
loaded dollar before a budget cap (docs/91 §3). The win is concentrated at TIGHT
budgets and is an AGGREGATE over a seed ensemble, not a per-seed promise (a single
seed's pick order is noisy). Its falsifiers: the edge → 1.0 as the budget grows to
drain-everything, and ≈ 1.0 when there is no shared contention to route around.
"""
from __future__ import annotations

from typing import Callable

from .workload import Workload


def make_yield_rank_key(
    workload: Workload,
    cursors: dict[str, int],
) -> Callable[[str, str, list[str]], float | None]:
    """Build a `rank_key` over a workload, reading the LIVE `cursors` by reference.

    `cursors` is the SAME dict the bare-pick loop advances each step (effort name →
    next-phase index), passed by reference so the ranker always scores against the
    current remaining horizon. The returned closure has the arbiter's `rank_key`
    signature `(name, kind, tree) -> float | None`.

    Score = (remaining phases) − CONTENTION_PENALTY·[next phase reaches shared AND
    other efforts are live]. The remaining-phases term front-loads the efforts with
    the most verifiable work left; the penalty DEMOTES an effort whose immediate
    next phase would reach into shared state while siblings are live — that pick
    tends to collide, get refused, and burn a retry action for no bank, so under a
    tight budget it is the worst use of a step. A private next phase (no shared
    reach), or any phase when nothing else is live, carries no penalty. Unknown
    lanes / drained efforts return `None` (no opinion ⇒ ladder order among them).
    """
    # The penalty is comfortably larger than the remaining-phases spread so a
    # would-collide next phase always sinks below a clean-admitting one, but it is
    # a fixed scalar (mechanism here is a benchmark estimator; a host would tune or
    # learn it — docs/91 §4). It only reorders among ALREADY-disjoint candidates,
    # so a bad value can never admit a colliding lane (the soundness floor).
    CONTENTION_PENALTY = 1000.0

    # lane name -> effort, fixed for the run (each effort owns one lane).
    effort_of_lane = {e.lane: e.name for e in workload.efforts}
    phases_of = {e.name: e.phases for e in workload.efforts}

    def _live_effort_count() -> int:
        """How many efforts still have a phase to do — the concurrency pressure."""
        return sum(1 for e in workload.efforts
                   if cursors.get(e.name, 0) < len(phases_of[e.name]))

    def rank_key(name: str, kind: str, tree: list[str]) -> float | None:
        effort = effort_of_lane.get(name)
        if effort is None:
            return None  # not one of our lanes — no opinion, ladder order decides
        phases = phases_of.get(effort)
        if not phases:
            return None
        idx = cursors.get(effort, 0)
        if idx >= len(phases):
            return None  # drained — nothing left to value
        n_remaining = len(phases) - idx
        next_phase = phases[idx]
        concurrency = max(0, _live_effort_count() - 1)
        # demote a next phase that would reach shared state under live concurrency:
        # it is the pick most likely to refuse+retry. Blind to whether it ships —
        # it reads INTENT to touch shared, never a realized outcome.
        penalty = (CONTENTION_PENALTY
                   if (next_phase.reaches_shared and concurrency > 0) else 0.0)
        return float(n_remaining) - penalty

    return rank_key
