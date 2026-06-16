# How to use a hidden test split to stop agents overfitting the visible tests

> Grade the keep decision on a held-out witness the agent never saw, so overfitting
> the visible tests buys nothing. `pip install dos-kernel`, then `dos improve` /
> `dos reward`. The PyPI name is `dos-kernel` — the bare `dos` package is an
> unrelated squatter; never install that.

## The short answer

If an agent can see every test, it can hard-code its way to green — overfitting the
visible split instead of solving the task. The classic mitigation is a hidden test
split, and DOS makes that the load-bearing rung of its keep decision: a candidate is
KEPT only when a measurement on a *held-out* window (one the proposer did not
author and did not see) confirms a real gain; a candidate that only improves the
visible metric is reverted. `dos improve` enforces this for a self-improving loop;
`dos reward` enforces it for training data — the admission bit is a function of the
held-out witness, so conditioning on the visible split doesn't move it.

## The evidence

Same proposer, same visible tasks — one arm grades itself, the other reads a
held-out witness. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| Self-judged keeping banks over-claims; held-out-gated keeping does not | over-claims kept = **12** (self-judged, seed 0) vs **0** (held-out-gated) | a fresh held-out window the proposer did not author | [`benchmark/improve_ablation/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/improve_ablation/RESULTS.md) |
| The gap holds across seeds | mean witnessed final gain **A − B = +1553.6 mbits/char** over 10 seeds | fresh held-out windows, per seed | [`benchmark/improve_ablation/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/improve_ablation/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos improve --workspace . --work heldout.json
```

A candidate that only improves the visible split is reverted:

```text
REVERT — claimed gain unconfirmed on the held-out witness; not kept
```

## What this does — and does not — certify

It certifies a kept candidate **improved on a held-out witness** — so overfitting
the visible tests is worthless to the agent. It does not design your split for you;
it ensures the keep decision reads the held-out side, where the agent can't have
overfit, not the visible side, where it can.

## Sources / reproduce

- [`benchmark/improve_ablation/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/improve_ablation/RESULTS.md) — the ratchet curve and the bait scoreboard.
- [`benchmark/poisoned_pool/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/poisoned_pool/RESULTS.md) — the held-out over-claim gate in a training loop.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to keep an AI self-improvement loop from keeping bad changes](keep-a-self-improvement-loop-from-keeping-bad-changes.md) — the keep-gate in depth.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

## Also asked as

- how to use a hidden test split to stop agents overfitting the visible tests
- hidden test split to stop agents overfitting the visible tests
- keep agents from gaming the tests they can see
- held-out tests so an agent can't overfit
- secret test split for evaluating coding agents
- stop an agent memorizing the visible test cases
- use a hidden split to detect agent overfitting
- secret test split for evaluating agents
- stop an agent gaming the visible tests
- hidden eval set to catch agent overfitting

> The kernel is the part that doesn't believe the agents.
