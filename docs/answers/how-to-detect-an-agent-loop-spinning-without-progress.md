# How to detect an agent loop spinning without progress

> Read progress from the artifacts the loop leaves behind, not from its own
> "making progress": `pip install dos-kernel`, then `dos liveness` /
> `productivity` / `efficiency`. The PyPI name is `dos-kernel` — the bare `dos`
> package is an unrelated squatter; never install that.

## The short answer

A "keep going until it's done" loop that grades itself by re-reading its own
narration always passes — so it can run all night, say "making progress" every
turn, and land nothing. To catch that, measure progress against the real
world the loop is supposed to change: new commits, files touched, work that
actually shipped. `dos liveness` asks whether the run is still moving;
`dos productivity` and `dos efficiency` ask whether the motion is producing
real output or just burning turns. Each reads env-authored counts, not the
agent's status line, so a confident-but-idle loop is visible instead of hidden.

## The evidence

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| Witness-gated early-halt is the survivor; mid-run "fixes" are flat-to-negative | **0 false-abandons / 1,634 winners across 22 models** (error-gated, K≥3) — and the same test falsifies the naive raw-repeat gate | each task's own oracle over a frozen replay corpus | [`benchmark/giveup_cross_benchmark.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/giveup_cross_benchmark.py) |
| Progress is read from artifacts, not narration | the temporal/economic verdicts fold env-authored counts (commits, touches, elapsed) | the git log and the run's own fossils | [the corpus ledger](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/toolathlon/_results/additivity_claims.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta. The give-up result is read precisely: **0 false-abandons** means
the gate never told a run that was actually going to win to stop.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos liveness --workspace .
```

`dos liveness` reports whether the run is still moving against ground truth;
`dos productivity` and `dos efficiency` report whether that motion is producing
real output per unit of work. A loop that narrates progress while the git log
stays flat shows up as stalled — the verdict reads the artifacts, not the
status line.

To make a self-stopping loop honest, gate its stop on the verdict so it can't
declare "done" on its own say-so:

```bash
dos init --hooks auto .       # Claude Code, Cursor, Codex, Gemini CLI, …
```

## What this does — and does not — certify

These verdicts certify **motion and output against the artifacts**: is the run
still landing real changes, and at what rate. They are advisory temporal/economic
signals — they tell you a loop is spinning; they do not judge whether the work
it *does* land is correct (that's `dos verify` for presence and your tests for
correctness). "Stalled" is read from the world, not from the transcript.

## Sources / reproduce

- [`benchmark/giveup_cross_benchmark.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/giveup_cross_benchmark.py) — the cross-benchmark give-up study.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [The incident page](../incidents/my-agent-loop-ran-all-night-and-landed-nothing.md) — the same failure as a story.
- [FAQ: How do I detect that an agent loop is spinning — running but not progressing?](../FAQ.md#how-do-i-detect-that-an-agent-loop-is-spinning--running-but-not-progressing)

> A loop that grades itself by re-reading its own narration always passes.

## Also asked as

- how to detect an agent loop spinning without progress
- my AI agent loop is running but not making progress
- detect an agent stuck spinning in circles
- how to tell an agent loop is making no progress
- agent keeps looping without finishing anything
- catch a coding agent that's busy but accomplishing nothing
- is my agent making progress or just burning turns
- detect a no-progress agent loop automatically
- agent loop never terminates how to detect the stall
- measure whether an agent loop is actually advancing
- spot a spinning agent before it wastes the budget
- how do I know my agent isn't just idling in a loop
- Cursor agent loop running but not progressing
- Claude Code stuck in a loop making no progress
- Aider keeps looping without finishing
