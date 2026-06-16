# Reward hacking in LLM coding agents — how to measure and prevent it

> Score the reward off an effect the agent can't author, not off its own claim of
> success. `pip install dos-kernel`, then `dos reward` / `dos improve`. The PyPI
> name is `dos-kernel` — the bare `dos` package is an unrelated squatter; never
> install that.

## The short answer

Reward hacking is an agent optimizing the *measurement* instead of the *work* —
claiming `RESOLVED` on a task it didn't solve, banking the credit, and (in a
self-improvement loop) conditioning future behavior on the lie. You measure and
prevent it the same way: make the reward label a function of a witness the agent
did not author. `dos reward` admits a training example only when an env-authored
witness (a test exit code, a DB-state read-back) confirms the claimed effect;
`dos improve` keeps a candidate only when an independent measurement shows a real
gain. A confidently-claimed success that the witness refutes is purged, not
banked — so the pool the loop learns from stays clean by construction.

## The evidence

Same proposer, same tasks — one arm trusts its own judgment, the other reads the
test exit code. Measured over 4 expert-iteration generations:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| The self-judged pool banks poison; the witness-gated pool does not | final pool poison **5% (2/40)** self-judged vs **0/35** witness-gated, zero by construction | the subprocess test exit code (`dos.reward.admit`) | [`benchmark/poisoned_pool/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/poisoned_pool/RESULTS.md) |
| Every over-claim the witness saw was purged | pool poison fraction Arm W **0.00 -> 0.00 -> 0.00 -> 0.00** across all generations | the env-authored test result, not the agent's `RESOLVED` | [`benchmark/poisoned_pool/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/poisoned_pool/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos reward --workspace . --candidate claim.json
```

A confidently-claimed `RESOLVED` the test refutes:

```text
REJECT_POISON — the claimed success is refuted by the witness; not admitted
```

The poisoned example never enters the pool the loop conditions on.

## What this does — and does not — certify

It certifies a reward label is **backed by an un-authored effect** — the example
admitted was really a success. It does not claim to catch *every* gaming strategy
(a prose-shaped lie with no checkable witness abstains, and is contained by typing,
not refused). The load-bearing guarantee: the agent cannot earn reward by asserting
success the witness can refute.

## Sources / reproduce

- [`benchmark/poisoned_pool/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/poisoned_pool/RESULTS.md) — the self-judged-vs-witness-gated pool study.
- [`benchmark/improve_ablation/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/improve_ablation/RESULTS.md) — the keep-gate ratchet curve.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [Process-reward training data that can't be gamed](process-reward-model-training-data-that-cant-be-gamed.md) — the data-generation angle.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

## Also asked as

- reward hacking in LLM coding agents how to measure and prevent
- reward hacking in LLM coding agents how to measure it
- how do coding agents reward-hack the objective
- detect reward hacking in an AI coding agent
- agent gaming the reward signal how to prevent
- examples of reward hacking in code agents
- measure and stop reward hacking in LLM agents
- agent optimizes the metric not the task how to catch
- agent optimizes the metric not the goal
- measure reward hacking in a coding agent
- stop an agent gaming the objective
- examples of LLM agents exploiting the reward
- stop a coding agent gaming its reward
- agent cheats the objective how to prevent
- reward hacking by AI coding agents
- keep an agent from optimizing the wrong thing

> The kernel is the part that doesn't believe the agents.
