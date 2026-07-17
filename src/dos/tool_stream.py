"""TS — the stall verdict: *is the tool loop producing new bytes, or just spinning?*

docs/145 — the loop-economics axis. `arg_provenance` (docs/143) and the intervention
ladder (docs/144) are both *prevent-down* on the **Integrity** verifier slice, and both
**vanish on a strong model** — a model that reads-before-it-writes mints nothing, so the
detector catches nothing and adds nothing. Their ceiling is the *minting rate*, which → 0
as the model improves.

This module opens the axis neither doc built. A cheap model on a long horizon **thrashes**:
it re-issues the same read, polls for an eventual-consistency write to land, or loops
without progress until it times out at `max_iterations` with the task half-finished. The
benchmark's own headline is that success **decays monotonically with horizon** (~35 %@4 steps
→ <20 %@16). A slice of that decay is **not planning** (the off-limits +14–35 pp lever) — it
is the agent failing to *use* a value it already received. This leaf detects that slice and
lets a consumer **re-surface the env-authored value the agent already holds**, converting a
doomed re-read loop into a finished task on the **same budget**. It is the first DOS lever on
this benchmark that can move success **UP** (add a finishing step) rather than only prevent a
wrong one — and the first whose value is **independent of minting** (it fires on *any* looping
model, including a strong one stuck on eventual consistency).

This module is `liveness.classify`'s sibling — a **pure** verdict function, re-aimed off git
onto the in-process tool-result stream:

    liveness.classify     (ProgressEvidence, policy)  -> LivenessVerdict   (git/journal stream)
    tool_stream.classify_stream (ToolStream, policy)  -> StreamVerdict     (tool-result stream)
                          ^ THIS module

`liveness` asks "did the GIT/JOURNAL state advance?"; `tool_stream` asks "did the TOOL-RESULT
stream advance, or did the env return the same bytes again?" Same temporal-distrust shape,
different stream — the `journal_delta`-vs-`git_delta` "different input, separate leaf" split
the kernel already uses (and the `recurring_wedge`-vs-`wedge_reason` precedent).

Why it is byte-clean (the §5a survival argument)
================================================

The crux: does this survive the **mirror-verifier trap** (docs/141, docs/143 §5a) where the
obvious "is the agent making PROGRESS?" version does not? Walk the provenance of a `StreamStep`:

  * `tool_name` / `args_digest` — the **agent** authored these (it chose the call).
  * `result_digest`            — the **gym MCP server** authored these (it produced the result).

The reader's only question is **"is this env-authored `result_digest` byte-identical to one the
env already returned, N times in a row?"** — *provenance-of-the-identity-of-repeated-output*, a
pure byte question about **env-authored** bytes. The agent did **not** author the *identity* of
its own repeated tool results; the gym did. So the signal cannot be forged in the agent's favor.
The dangerous version — "is the agent making real progress / has it done the right thing yet?" —
is a **satisfaction predicate** the wrapper would author from agent-visible prose (forgeable);
this reader **never asks it.** "The same bytes came back N times" is a measured fact about the
env's outputs, not a judgment of the agent. No answer key, no held-out state, no oracle plan —
exactly `arg_provenance`'s provenance-of-a-string honesty, re-aimed from "is this id minted?" to
"did this exact output repeat?".

The honest hole, named (not buried)
===================================

**Eventual-consistency polling is a legitimate reason to re-read with the same result.** A task
that correctly waits for an async write to land produces identical reads until it lands — a true
REPEATING that is *not* a stall. This is why the intervention a consumer attaches to REPEATING
must be a **WARN that re-surfaces the value, never a cut**: re-presenting bytes the agent already
holds is harmless if the agent was right to wait (it ignores a value it does not yet need) and
helpful if it was stuck (it gets the value it kept failing to use). The `ignore_tools` allow-list
lets a host exempt known pollers from the reader entirely. The verdict itself only REPORTS; the
turn-preserving discipline lives in the consumer (the docs/99 advisory line, the docs/144 −9 pp
intervention-cost lesson made structural).

⚓ Pure kernel, I/O on the edge (the dos idiom — mirrors `liveness.classify`,
`arg_provenance.classify_call`, `churn.decide_coalesce`): `classify_stream(ToolStream, policy)
-> StreamVerdict` is a frozen tuple of digests in, a frozen verdict out. The CALLER computes the
`args_digest` / `result_digest` at the boundary (it hashes the normalized args + the result
bytes the gym returned); the kernel **hashes nothing live**, reads no clock, no disk. That is
what lets the whole verdict be replay-tested on frozen fixtures with zero benchmark/LLM/MCP
access — the keystone the audit calls "testable with zero benchmark access."
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# The typed stall verdict — three states, mutually exclusive (the Liveness shape).
# ---------------------------------------------------------------------------
class StreamState(str, enum.Enum):
    """The typed stall verdict over a tool-result stream — three states.

    `str`-valued so it round-trips through a CLI token / JSON / exit-code map without a
    lookup table (mirrors `liveness.Liveness`, `arg_provenance.ProvenanceStance`).

      ADVANCING — the recent window is producing NEW env-authored bytes: the most recent
                  `(tool, args, result_digest)` triple is not a long consecutive repeat.
                  The loop is moving; no intervention. Also the floor when the stream is too
                  short to judge a repeat (fewer than `repeat_n` steps) — too young to accuse.
      REPEATING — the SAME `(tool, args, result_digest)` triple has recurred `repeat_n`
                  consecutive times (but fewer than `stall_n`): the agent is re-issuing a
                  call the env answers identically — no new information is entering the loop.
                  The actionable rung — a consumer re-surfaces the repeated env value (WARN).
      STALLED   — the repeat run has reached `stall_n` consecutive identical triples: the
                  loop is almost certainly doomed to time out. The hard rung — a host MAY opt
                  a turn-preserving BLOCK to it (re-surface + reclaim the iteration); the
                  default still only re-surfaces (a STALLED on a legitimately-polling task is
                  a read the agent needed).
    """

    ADVANCING = "ADVANCING"  # the tool stream is producing new env-authored bytes
    REPEATING = "REPEATING"  # the same (tool, args, result) triple is recurring (no new info)
    STALLED = "STALLED"      # the repeat run is long enough to be near-certainly doomed

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# ---------------------------------------------------------------------------
# The thresholds — policy, not mechanism (the LivenessPolicy seam).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StreamPolicy:
    """The run-length windows that separate ADVANCING/REPEATING/STALLED — policy, not mechanism.

    The same "mechanism is kernel, thresholds are config" split as `LivenessPolicy`'s
    `grace_ms`/`spin_ms`. Defaults are GENERIC (no host tuning); a workspace declares its own
    in `dos.toml [tool_stream]` read back through `SubstrateConfig` (the closed-config-as-data
    pattern, like `[liveness]` / `[reasons]` / `[intervention]`).

      repeat_n     — the consecutive-identical-triple run-length at which the loop is REPEATING.
                     Default 3: the first two identical results may be a benign re-check; the
                     THIRD identical result is where a no-progress loop is established. Mirrors
                     `churn.DEFAULT_MIN_COALESCE_RUN`'s "the FIRST stands alone, the cycle starts
                     at the next" reasoning, one rung later (a tool re-read is cheaper to repeat
                     once than a no-op commit, so the floor is 3 not 2).
      stall_n      — the run-length at which REPEATING hardens to STALLED (the BLOCK-eligible
                     rung). Default 5. Must be ≥ repeat_n (validated) — STALLED is strictly more
                     repetition than REPEATING.
      ignore_tools — a host's allow-list of known-poller tool names (normalized lower). A step
                     whose tool is on this list is NEVER counted toward a repeat run — it breaks
                     the run as if it were new (eventual-consistency pollers a host KNOWS about
                     are exempted at the source, so the reader never false-fires on them).
    """

    repeat_n: int = 3
    stall_n: int = 5
    ignore_tools: frozenset = frozenset()

    def __post_init__(self) -> None:
        if self.repeat_n < 1:
            raise ValueError("repeat_n must be >= 1")
        if self.stall_n < self.repeat_n:
            raise ValueError(
                f"stall_n ({self.stall_n}) must be >= repeat_n ({self.repeat_n}) — "
                f"STALLED is strictly more repetition than REPEATING"
            )


DEFAULT_POLICY = StreamPolicy()


# ---------------------------------------------------------------------------
# Frozen inputs — the pure datum a caller gathers at the boundary and hands in.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StreamStep:
    """One step of the tool-result stream, as the pure datum the verdict sees.

    The caller computes the digests at the boundary (it normalizes the args and hashes them;
    it hashes the result BYTES the gym returned). The kernel never hashes a live value — it
    compares pre-computed digests, the `liveness` "clock is injected, never read inside"
    discipline applied to hashing.

      tool_name     — the tool that was called (normalized lower at the boundary is fine; the
                      verdict casefolds for the `ignore_tools` test). Agent-authored.
      args_digest   — a stable digest of the call's NORMALIZED arguments (sorted keys, canonical
                      scalar repr). Agent-authored. Part of the repeat-identity key so two
                      reads of DIFFERENT rows are not a repeat.
      result_digest — a stable digest of the RESULT bytes the env returned. ENV-AUTHORED — the
                      load-bearing field: two steps repeat only if the env returned the SAME
                      bytes, which the agent cannot forge. `None` marks a step with no result
                      (a call that errored / returned nothing) — it never matches another step,
                      so it breaks a run rather than extending it (the fail-safe: no result is
                      not "the same result").
    """

    tool_name: str
    args_digest: str
    result_digest: Optional[str] = None

    def _key(self, policy: "StreamPolicy") -> "Optional[tuple[str, str, str]]":
        """The repeat-identity key, or None if this step can never match another.

        None when: the result is absent (no result is not 'the same result'), OR the tool is on
        the `ignore_tools` allow-list (a known poller is exempt at the source). A None-keyed
        step breaks any in-progress repeat run — the fail-safe direction (when in doubt, the
        loop is NOT stalled)."""
        if self.result_digest is None:
            return None
        if self.tool_name.casefold() in {t.casefold() for t in policy.ignore_tools}:
            return None
        return (self.tool_name.casefold(), self.args_digest, self.result_digest)


@dataclass(frozen=True)
class ToolStream:
    """The whole tool-result stream accumulated so far — the `ProgressEvidence` analogue.

    `steps` is a tuple of `StreamStep` in call order. Empty (`()`) or short (< `repeat_n`) reads
    as ADVANCING — too little has happened to prove a no-progress loop (the load-bearing
    too-young-to-judge floor, the `liveness` young-and-alive guard's sibling). The stream is
    kept WHOLE (not pre-reduced) so the verdict measures the run-length ending at the LATEST
    step — the live "is it stuck right now?" question, not a whole-history histogram.
    """

    steps: tuple[StreamStep, ...] = ()


# ---------------------------------------------------------------------------
# Frozen verdict — the folded answer, advisory only (the LivenessVerdict shape).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StreamVerdict:
    """The single verdict `classify_stream` returns — typed state + the legible run detail.

    `state` is the typed `StreamState`. `repeat_run` is the length of the consecutive-identical
    run ending at the latest step (1 = the latest step is unique; ≥ `repeat_n` = REPEATING).
    `repeated_step` is the `StreamStep` that is repeating (None when ADVANCING) — the consumer
    re-surfaces ITS env-authored `result_digest`/value, so the WARN names the exact prior output
    the agent already holds (never a fabricated one). `reason` is the one-line operator summary.
    Advisory: never raises, never dispatches — the consumer reads `state` and decides whether to
    re-surface.
    """

    state: StreamState
    repeat_run: int
    repeated_step: Optional[StreamStep]
    reason: str

    def to_dict(self) -> dict:
        rs = self.repeated_step
        return {
            "state": self.state.value,
            "repeat_run": self.repeat_run,
            "repeated_step": (
                {
                    "tool_name": rs.tool_name,
                    "args_digest": rs.args_digest,
                    "result_digest": rs.result_digest,
                }
                if rs is not None
                else None
            ),
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# The pure verdict — consecutive-identical run-length, lifted from churn.
# ---------------------------------------------------------------------------
def _trailing_run(stream: ToolStream, policy: StreamPolicy) -> "tuple[int, Optional[StreamStep]]":
    """The length of the consecutive-identical-key run ENDING at the latest step, and the step
    that repeats. PURE — the `churn.decide_coalesce` consecutive-same-cause run-length, lifted
    off git history onto the tool stream.

    Walks backward from the last step while the repeat-identity key matches. A step whose key is
    None (no result, or an `ignore_tools` poller) cannot extend or start a run — the fail-safe:
    an absent/exempt result is never 'the same result', so it can only BREAK a run. Returns
    (run_length, repeated_step) — run_length 0 with an empty stream, 1 when the latest step does
    not match the one before it (or its key is None)."""
    steps = stream.steps
    if not steps:
        return 0, None
    last = steps[-1]
    last_key = last._key(policy)
    if last_key is None:
        # The latest step has no comparable result (absent / exempt poller) — not a repeat.
        return 1, None
    run = 1
    for prev in reversed(steps[:-1]):
        if prev._key(policy) == last_key:
            run += 1
        else:
            break
    return run, (last if run >= 2 else None)


def classify_stream(
    stream: ToolStream, policy: StreamPolicy = DEFAULT_POLICY
) -> StreamVerdict:
    """Classify the tool-result stream's stall state from the accumulated steps. PURE — no I/O.

    Reads the ladder top to bottom (this function IS the answer to "is the loop stuck?"):

      1. ADVANCING — the trailing identical-run is shorter than `repeat_n` (incl. an empty or
         too-short stream): the loop is producing new env-authored bytes, or has not repeated
         long enough to accuse. The benign / no-action verdict (the `liveness` young-and-alive
         floor's sibling).
      2. REPEATING — the trailing run has reached `repeat_n` but is shorter than `stall_n`: the
         same `(tool, args, result)` triple recurred — no new information is entering the loop.
         The actionable rung; the consumer re-surfaces `repeated_step`'s env value (WARN).
      3. STALLED   — the trailing run has reached `stall_n`: long enough to be near-certainly
         doomed. The hard rung (a host MAY opt a turn-preserving BLOCK).

    The ADVANCING/REPEATING boundary is the `repeat_n` run-length; the REPEATING/STALLED boundary
    is `stall_n`. Both are pure counts over env-authored `result_digest` identity — never a
    judgment of whether the agent is *succeeding* (that would be the §5a satisfaction predicate
    this module exists to avoid). The verdict only REPORTS; the turn-preserving re-surface (never
    a cut) is the consumer's, the docs/99 advisory line.
    """
    run, repeated = _trailing_run(stream, policy)

    if run >= policy.stall_n:
        return StreamVerdict(
            state=StreamState.STALLED,
            repeat_run=run,
            repeated_step=repeated,
            reason=(
                f"the same (tool, args, result) triple repeated {run} consecutive times "
                f"(>= stall {policy.stall_n}) — the loop is near-certainly doomed; the env "
                f"returned identical bytes each time (no new information)"
            ),
        )
    if run >= policy.repeat_n:
        return StreamVerdict(
            state=StreamState.REPEATING,
            repeat_run=run,
            repeated_step=repeated,
            reason=(
                f"the same (tool, args, result) triple repeated {run} consecutive times "
                f"(>= repeat {policy.repeat_n}) — no new env-authored bytes are entering the "
                f"loop; re-surface the value the agent already received"
            ),
        )
    return StreamVerdict(
        state=StreamState.ADVANCING,
        repeat_run=run,
        repeated_step=None,
        reason=(
            f"trailing identical-run {run} (< repeat {policy.repeat_n}) — the tool stream is "
            f"producing new env-authored bytes (or too short to judge a stall)"
        ),
    )


# ---------------------------------------------------------------------------
# The declarative on-ramp — read a policy out of dos.toml (mirror reasons/intervention).
# ---------------------------------------------------------------------------
def policy_from_table(table: dict) -> StreamPolicy:
    """Turn a parsed `[tool_stream]` TOML table into a `StreamPolicy`. PURE (no I/O).

    `table` is `{repeat_n?, stall_n?, ignore_tools?}` — the shape
    `tomllib.load(...)["tool_stream"]` yields. Missing keys fall back to the generic defaults;
    a malformed value raises (via `StreamPolicy.__post_init__`), so a bad declaration fails
    loudly at load. `ignore_tools` accepts a list or a single string.
    """
    if not table:
        return DEFAULT_POLICY
    ig = table.get("ignore_tools", ())
    if isinstance(ig, str):
        ig = (ig,)
    return StreamPolicy(
        repeat_n=int(table.get("repeat_n", DEFAULT_POLICY.repeat_n)),
        stall_n=int(table.get("stall_n", DEFAULT_POLICY.stall_n)),
        ignore_tools=frozenset(str(t) for t in ig),
    )


def load_from_toml(
    path: "Path | str", *, base: StreamPolicy = DEFAULT_POLICY
) -> StreamPolicy:
    """Build a `StreamPolicy` from a `dos.toml`'s `[tool_stream]` table.

    Returns `base` unchanged when the file is absent, has no `[tool_stream]` table, or
    `tomllib` is unavailable — the declarative path is purely additive, so a missing/empty
    config degrades to the generic default, never an error. A *present but malformed* table
    raises (`StreamPolicy.__post_init__`). Reads with `utf-8-sig` to strip a PowerShell-written
    BOM (the `reasons.load_from_toml` / `intervention.load_from_toml` fix). The OVERRIDE shape
    (the `[stamp]`/`[liveness]` pattern): a present table replaces the windows wholesale; an
    absent one inherits `base`.
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
    table = data.get("tool_stream")
    if not isinstance(table, dict) or not table:
        return base
    return policy_from_table(table)
