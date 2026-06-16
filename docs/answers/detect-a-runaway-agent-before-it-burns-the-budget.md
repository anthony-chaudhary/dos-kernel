# How to detect a runaway AI agent before it burns the token budget

> Read whether the run is still producing real output, not whether it says it is:
> `pip install dos-kernel`, then `dos liveness` / `dos breaker`. The PyPI name is
> `dos-kernel` — the bare `dos` package is an unrelated squatter; never install
> that.

## The short answer

A runaway agent keeps calling tools, keeps narrating "almost there", and keeps
spending — while landing nothing. Waiting for the bill is the expensive way to
find out. The cheap way is to measure the run against the artifacts it's supposed
to produce: `dos liveness` classifies a run as `ADVANCING`, `SPINNING`, or
`STALLED` from its actual git and journal deltas; `dos breaker` is the circuit
breaker that trips after a run of non-progress. Both read env-authored counts, not
the agent's status line, so a confident-but-idle loop is caught by an exit code a
supervisor can act on — pause it, escalate it, stop the spend — before the budget
is gone.

## The evidence

A witness-gated early-halt is the survivor; mid-run self-graded "fixes" are flat
to negative. Measured over a cross-benchmark replay:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| The gate never tells a run that was going to win to stop | **0 false-abandons / 1,634 winners across 22 models** (error-gated, K≥3) | each task's own oracle over a frozen replay corpus | [`benchmark/giveup_cross_benchmark.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/giveup_cross_benchmark.py) |
| Progress is read from artifacts, not narration | the temporal verdicts fold env-authored counts (commits, touches, elapsed) | the git log and the run's own fossils | [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta. **0 false-abandons** means the gate never stopped a run that was
actually going to win.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos liveness --workspace . --run-id RID-123
```

A run that keeps calling tools but lands nothing:

```text
SPINNING RID-123 — tool calls advancing, git/journal deltas flat
```

Exit code non-zero — the supervisor pauses or escalates before more budget burns.

## What this does — and does not — certify

It certifies whether the run is **producing real output** — moving the git log and
the journal, not just the token counter. It does not judge whether the output is
correct; it catches the specific runaway pattern of confident motion with no
landed effect, in time to stop the spend.

## Sources / reproduce

- [`benchmark/giveup_cross_benchmark.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/giveup_cross_benchmark.py) — the cross-benchmark give-up study.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to detect an agent loop spinning without progress](how-to-detect-an-agent-loop-spinning-without-progress.md) — the same verbs on the spinning case.
- [How to scavenge a stalled agent lease without killing a live one](scavenge-a-stalled-lease-without-killing-a-live-one.md) — what to do once a run is STALLED.
- [FAQ: How do I detect that an agent loop is spinning?](../FAQ.md#how-do-i-detect-that-an-agent-loop-is-spinning--running-but-not-progressing)

## Also asked as

- how to detect a runaway AI agent before it burns the token budget
- stop a runaway AI agent before it burns my token budget
- detect an agent burning tokens with nothing to show
- how to cap an agent that won't stop spending
- my coding agent is eating budget catch it early
- runaway agent token spend how to detect and halt
- early warning for an agent wasting money
- agent burning the budget on a loop how do I stop it
- detect cost-runaway in an autonomous agent
- trip a breaker when an agent spends without progress
- guard against an agent that runs up the bill
- agent spending money with no output stop it
- cost guard for an autonomous coding agent
- circuit breaker for a runaway agent loop
- agent won't stop and the bill is climbing
- halt an agent before it blows the token budget

> The kernel is the part that doesn't believe the agents.
