# How to scavenge a stalled agent's lease without killing a live one

> Distinguish STALLED (dead, safe to reclaim) from SPINNING (alive but stuck,
> surface don't kill): `pip install dos-kernel`, then `dos liveness` / `dos reap`.
> The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter;
> never install that.

## The short answer

When a fleet runs many agents on leased regions, some die mid-work and leave a
lease nobody will release — and a supervisor that reclaims leases too eagerly will
yank one out from under an agent that's merely slow. The distinction is the whole
job: a **STALLED** run (no heartbeat, no deltas — actually dead) can have its lease
scavenged so the region frees up; a **SPINNING** run (still alive, just stuck) must
be *surfaced* to a human, never killed. `dos liveness` makes that call from
env-authored signals — the heartbeat, the git and journal deltas — not from
whether the agent looks busy. Only the dead-and-proven leases are reaped; a live
worker is protected.

## The evidence

The reap/surface decision reads the run's own fossils, not its self-report.
Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A witness-gated halt never abandons a run that was going to win | **0 false-abandons / 1,634 winners across 22 models** (error-gated, K≥3) | each task's own oracle over a frozen replay corpus | [`benchmark/giveup_cross_benchmark.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/giveup_cross_benchmark.py) |
| The lease is a leased region-lock with a journaled lifecycle | the lease is written to a journal before it is believed, so a crashed agent leaves no phantom lock | the lane write-ahead log, not the agent's claim | [`docs/89`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/89_the-lane-is-a-region-lock.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos liveness --workspace . --run-id RID-7
```

A dead run is safe to reclaim; a stuck-but-live one is surfaced, not reaped:

```text
STALLED RID-7 — no heartbeat, no deltas; lease safe to scavenge
```

`dos reap` then frees only the proven-dead lease; a `SPINNING` run is routed to a
human instead.

## What this does — and does not — certify

It certifies a lease is **dead before it is reclaimed** — separating the truly
crashed run from the merely slow one. It does not decide a slow run is hopeless;
that is surfaced for a human. The guarantee: scavenging frees the region without
yanking it from a worker that's still alive.

## Sources / reproduce

- [`benchmark/giveup_cross_benchmark.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/giveup_cross_benchmark.py) — the cross-benchmark give-up study.
- [`docs/89`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/89_the-lane-is-a-region-lock.md) — the lane as a leased region-lock with a journaled lifecycle.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to detect a runaway agent before it burns the budget](detect-a-runaway-agent-before-it-burns-the-budget.md) — the liveness read this acts on.
- [FAQ: How do I detect that an agent loop is spinning?](../FAQ.md#how-do-i-detect-that-an-agent-loop-is-spinning--running-but-not-progressing)

## Also asked as

- how to scavenge a stalled agent's lease without killing a live one
- scavenge a stalled agent's lease without killing a live one
- reclaim a dead agent's file lock safely
- free a stuck lease but don't kill a working agent
- stale lease cleanup that won't disrupt a live agent
- recover a crashed agent's lease without collateral
- safely scavenge a stalled lease in an agent fleet
- reclaim a dead agent's lock without hurting a live one
- clean up a crashed agent's lease safely
- free a stuck lease but spare working agents
- scavenge stalled leases in an agent fleet

> The kernel is the part that doesn't believe the agents.
