"""NOS — the no-op-streak verdict: *how many turns in a row produced zero ground-truth delta?*

docs/259 §Follow-up 1 — the **generalization of the wait-marker budget** off its one
special case ("markers emitted") onto the general one ("no-op turns since the last
forward delta"). `loop_decide.wait_marker_budget` already answers a count-vs-cap
question for ONE flavor of no-op turn: a `claude -p` keep-alive marker — a full
assistant turn that replays the whole context out of cache and produces nothing but
"still waiting." But a `ScheduleWakeup`-poll loop that re-reads a `.output` file in a
tight tick, or any loop that wakes, finds no change, and goes back to sleep, is the
*same* pathology: a turn that paid the cache-replay cost and moved no ground truth.
This module is the verdict that makes those one verdict — "the run has taken N no-op
turns since it last made a forward delta; has it spent its budget?"

This is `wait_marker_budget`'s **generalization**, and it sits in the temporal-verdict
family (`liveness` / `tool_stream` / `productivity` / **`noop_streak`**) — the same
pure-verdict shape, re-aimed once more:

    liveness.classify     (ProgressEvidence, policy)  -> LivenessVerdict   (did state move AT ALL?)
    tool_stream.classify_stream (ToolStream, policy)  -> StreamVerdict     (is the tool stream repeating?)
    productivity.classify  (WorkHistory, policy)       -> ProductivityVerdict (is the work-RATE fading?)
    noop_streak.classify   (NoOpHistory, policy)       -> NoOpStreakVerdict (how many no-op turns since a forward delta?)
                           ^ THIS module

It is a COUNT-vs-cap verdict, not a TREND verdict — which is why it is `wait_marker_budget`'s
sibling and NOT a second mode of `productivity`. `productivity` reads a *vector* of
per-step work magnitudes and asks "is the rate falling" (`deltas[-1]`/`deltas[-2]`);
`noop_streak` reads a *single* monotone counter — the run-length of consecutive
no-op turns since the last forward delta — and asks "is that run-length past its
budget." Folding the streak into `productivity.WorkHistory` would overload its
well-defined "fading rate" semantics with a different question and collide its
"withhold the accusation / reject negatives" floor with this guard's opposite
conservative direction (below). So the streak gets its own small verdict, the way
`productivity` got its own rather than living inside `liveness`.

**The forward-delta reset is the load-bearing idea** (docs/259 §Follow-up 2). The
count is not "no-op turns ever" — it is "no-op turns *since the last forward delta*."
A forward delta (a commit, a real tool result, a host re-entering a fresh wait phase)
ZEROES the streak, the `tool_stream` ADVANCING analogue: progress earns the loop a
fresh budget. Without a reset the count is a strict lifetime monotone (what
`wait_marker_budget` is today); with one, a long-lived session that legitimately
makes progress and then re-enters a wait phase starts fresh instead of being refused
on a stale tally. The reset lives at the BOUNDARY (`marker_sensor.record_reset`
appends an `op:"RESET"` record; the replayed count is markers-after-the-last-reset);
this pure verdict just reads the resulting count.

**Byte-clean by construction.** A no-op turn is a turn the *runtime* observed to
produce zero ground-truth delta — it is counted by the durable accumulator
(`marker_sensor`), never threaded through the agent's own narration. So EXHAUSTED is
"the environment recorded N no-op turns since the last forward delta," never "the
agent says it has waited long enough" — a quantity, not a self-report (the docs/138
invariant `liveness`/`productivity`/`tool_stream` all keep).

**Advisory.** Like `wait_marker_budget` and `liveness.SPINNING`, EXHAUSTED REPORTS;
it never kills a process. A loop consults it and chooses to stop holding its turn
open (the marker hook is the first consumer — it blocks the Stop while the budget is
LIVE, allows the Stop once EXHAUSTED); nothing here enforces.

**The conservative direction is the OPPOSITE of `productivity`'s** — and that is why
it is a separate verdict. `productivity` withholds the DIMINISHING accusation when in
doubt (a missing delta is "still productive"). A *cost* guard must do the reverse:
when in doubt, count the no-op turn (so the guard refuses one *more* keep-alive turn,
never one *fewer*) — over-spending on a missed count is the failure to avoid. The
accumulator honors this by leaving a torn/unreadable RESET as "the reset didn't
happen" (the count stays HIGHER → EXHAUSTED sooner → refuse more); this verdict
honors it by treating `noop_turns >= max_streak` as EXHAUSTED (the `>=`, not `>`, so
the budget is spent the instant it is reached). Refusing one no-op turn too early
costs at most one missed poll; the real Bash `<task-notification>` (which fires on
the child's true exit regardless) is the safety net.

**No-telemetry / no-plan discipline** (the `test_verify_no_plan` sibling): NOS needs
*nothing* but the no-op-turn count the caller already replayed. No plan, no registry,
no clock — `classify()` makes no I/O at all (it is timeless, like `productivity`; it
reads a count, not an age). A caller with a count gets a verdict; a caller with 0 gets
the honest LIVE floor (a fresh wait phase has spent nothing yet).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path


class NoOpStreak(str, enum.Enum):
    """The typed no-op-streak verdict — two states, mutually exclusive.

    `str`-valued so it round-trips through a CLI stdout token / exit-code map without
    a lookup table (mirrors `liveness.Liveness` / `productivity.Productivity` /
    `gate_classify.Verdict`).
    """

    LIVE = "LIVE"            # the streak is under the cap — another no-op turn is permitted
    EXHAUSTED = "EXHAUSTED"  # the streak has reached the cap — refuse further no-op turns

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class NoOpStreakPolicy:
    """The cap that separates LIVE / EXHAUSTED — policy, not mechanism.

    The same "mechanism is kernel, threshold is config" split as `productivity`'s
    floor and `loop_decide`'s `max_iterations`. The default is GENERIC and equals
    `wait_marker_budget`'s default (4) — so the generalized verdict refuses at the
    same budget the shipped marker lever does (the marker case is the special case,
    and it must not drift). A workspace declares its own in `dos.toml [noop_streak]`,
    the closed-config-as-data pattern (`[tool_stream]` / `[productivity]` / `[stamp]`).

      max_streak — the **no-op-turn budget since the last forward delta**: the most
                   consecutive zero-delta turns a loop may take before the verdict
                   refuses the next one. `wait_marker_budget`'s `max_markers`,
                   generalized off "markers" onto "no-op turns." A streak that REACHES
                   this cap (`>=`, the cost-guard direction) is EXHAUSTED.

    Default: 4 — `wait_marker_budget`'s per-run cap, one below the `keepalive_poll`
    telemetry flag (>=5), so the runtime refusal lands one turn before the post-hoc
    alarm would fire.
    """

    max_streak: int = 4

    def __post_init__(self) -> None:
        if self.max_streak < 0:
            raise ValueError("max_streak must be non-negative")


DEFAULT_POLICY = NoOpStreakPolicy()


@dataclass(frozen=True)
class NoOpHistory:
    """The no-op-turn count `classify()` reads — gathered by the CALLER at the boundary.

    No clock, no I/O inside the verdict — the arbiter rule, sharpened: NOS is
    *timeless* (it reads a count, never an age). The caller's boundary
    (`marker_sensor.marker_count`, which replays the session's `.dos/markers/<sid>.jsonl`
    tally into markers-since-the-last-RESET) measures the streak and freezes it here.

      noop_turns — the count of no-op turns SINCE the last forward-delta reset. A
                   no-op turn is a turn the runtime observed to produce zero ground-truth
                   delta (today: one keep-alive wait-marker; the generalization also
                   admits a poll-tick that found no change). 0 is "a fresh wait phase,
                   nothing spent yet" — the LIVE floor. Negative is rejected: a streak
                   length is a non-negative count.

    The single load-bearing read is `noop_turns` vs the policy's `max_streak` — the
    same count-vs-cap `wait_marker_budget(markers_emitted, max_markers)` makes. The
    count is carried (not just a bool) so `--output json` can echo it and the verdict
    can hand back the incremented value to carry into the next decision.
    """

    noop_turns: int = 0

    def __post_init__(self) -> None:
        if self.noop_turns < 0:
            raise ValueError("noop_turns must be non-negative (a count of no-op turns)")

    @classmethod
    def of(cls, noop_turns: int) -> "NoOpHistory":
        """Build a history from a replayed no-op-turn count."""
        return cls(noop_turns)


@dataclass(frozen=True)
class NoOpStreakVerdict:
    """The single verdict `classify()` returns, with the count echoed back.

    `verdict` is the typed `NoOpStreak`. `allow` is the convenience bit the marker
    hook keys on (`allow == (verdict is NoOpStreak.LIVE)`): True to permit one more
    no-op turn, False to refuse it. `noop_turns` is the count to carry into the *next*
    decision — incremented when allowed (this no-op turn now happened), unchanged when
    refused (the refused turn did not happen) — byte-mirroring
    `WaitMarkerDecision.markers_emitted`. `reason` is operator-facing. `to_dict` is the
    json shape (the legible-distrust renderer seam: the operator sees not just
    EXHAUSTED but the count and cap behind it).
    """

    verdict: NoOpStreak
    allow: bool
    noop_turns: int
    reason: str

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "allow": self.allow,
            "noop_turns": self.noop_turns,
            "reason": self.reason,
        }


def classify(
    history: NoOpHistory, policy: NoOpStreakPolicy = DEFAULT_POLICY
) -> NoOpStreakVerdict:
    """Classify a run's no-op streak against its budget. PURE — no I/O.

    The `wait_marker_budget` arithmetic, generalized: a count vs a cap, with the
    cost-guard `>=` (the budget is spent the instant it is reached, not one past).

      * EXHAUSTED (refuse) — `noop_turns >= max_streak`: the run has taken its whole
        budget of no-op turns since the last forward delta. The next one would replay
        full context out of cache for no work; refuse it (the loop ends its turn and
        waits on the real completion signal). The count carried forward is UNCHANGED
        (a refused turn did not happen).
      * LIVE (allow) — `noop_turns < max_streak`: budget remains; permit one more no-op
        turn and carry `noop_turns + 1` (this turn now happened) into the next decision.

    A `max_streak == 0` policy refuses the FIRST no-op turn (`0 >= 0`) — the degenerate
    `wait_marker_budget(0, 0)` preserves, and the honest reading of "no budget at all."
    """
    if history.noop_turns >= policy.max_streak:
        return NoOpStreakVerdict(
            verdict=NoOpStreak.EXHAUSTED,
            allow=False,
            noop_turns=history.noop_turns,
            reason=(
                f"no-op streak budget exhausted "
                f"({history.noop_turns}/{policy.max_streak} no-op turns since the last "
                f"forward delta) — each further turn replays full context out of cache "
                f"for no work; wait on the real completion signal, a forward delta resets "
                f"the streak"
            ),
        )
    return NoOpStreakVerdict(
        verdict=NoOpStreak.LIVE,
        allow=True,
        noop_turns=history.noop_turns + 1,
        reason=(
            f"no-op streak {history.noop_turns + 1}/{policy.max_streak} since the last "
            f"forward delta — budget remains"
        ),
    )


# ---------------------------------------------------------------------------
# The declarative on-ramp — read a policy out of dos.toml (mirror tool_stream/productivity).
# ---------------------------------------------------------------------------
def policy_from_table(table: dict) -> NoOpStreakPolicy:
    """Turn a parsed `[noop_streak]` TOML table into a `NoOpStreakPolicy`. PURE (no I/O).

    `table` is `{max_streak?}` — the shape `tomllib.load(...)["noop_streak"]` yields. A
    missing key falls back to the generic default; a malformed value raises (via
    `NoOpStreakPolicy.__post_init__`), so a bad declaration fails loudly at load (the
    `tool_stream.policy_from_table` posture).
    """
    if not table:
        return DEFAULT_POLICY
    return NoOpStreakPolicy(
        max_streak=int(table.get("max_streak", DEFAULT_POLICY.max_streak)),
    )


def load_from_toml(
    path: "Path | str", *, base: NoOpStreakPolicy = DEFAULT_POLICY
) -> NoOpStreakPolicy:
    """Build a `NoOpStreakPolicy` from a `dos.toml`'s `[noop_streak]` table.

    Returns `base` unchanged when the file is absent, has no `[noop_streak]` table, or
    `tomllib` is unavailable — the declarative path is purely additive, so a missing/empty
    config degrades to the generic default, never an error (the `tool_stream.load_from_toml`
    contract). A *present but malformed* table raises (`NoOpStreakPolicy.__post_init__`).
    Reads with `utf-8-sig` to strip a PowerShell-written BOM (the
    `reasons`/`intervention`/`tool_stream` `load_from_toml` fix).
    """
    p = Path(path)
    if not p.exists():
        return base
    # ONE shared, mtime-keyed parse (`_tomlcache`) - collapses the per-config-layer
    # re-read/re-parse storm on `dos.toml`. A malformed file still raises here
    # (uncached), so the caller's existing handling is unchanged; the missing-file
    # guard above is untouched. The utf-8-sig BOM strip lives inside the helper.
    from dos._tomlcache import read_toml_cached
    data = read_toml_cached(p)
    table = data.get("noop_streak")
    if not isinstance(table, dict) or not table:
        return base
    return policy_from_table(table)
