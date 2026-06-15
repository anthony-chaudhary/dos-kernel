# How do I combine a deterministic check, an LLM judge, and a human reviewer for agent oversight

> Order them by trust: a deterministic oracle decides what it can, an advisory
> judge rules on the residue, a human takes what neither can — and an error in the
> untrusted rung can never manufacture an approval. `pip install dos-kernel`, then
> `dos verify`. The PyPI name is `dos-kernel` — the bare `dos` package is an
> unrelated squatter; never install that.

## The short answer

Scalable oversight is a hierarchy, not a single grader. DOS composes three rungs:
the **oracle** (deterministic — git ancestry, a world-state read-back; runs first
and is trusted), the **judge** (a model or heuristic — advisory only, for the
residue the oracle abstained on, and fail-to-abstain so a judge error can never
mint an approval), and the **human** (the operator queue, for what neither can
settle). The discipline that makes this safe: the untrusted rungs can only refuse
*more*, never approve what the trusted rung didn't. A ruling judge is a driver,
resolved by name and hedged by design — so you can extend or swap it without
touching the kernel's trusted core.

## The evidence

Where the advisory judge and the deterministic oracle disagree, the oracle wins —
which is exactly why the judge is advisory. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| On the disagreement slice, the deterministic oracle is right and the judge is not | disagreement rate 62.5% (5/8); oracle right **5 / 5 (100%)**, judge accuracy 37.5% | the stored world-state effect, not the trajectory prose | [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) |
| The deterministic floor cannot be talked past | witness floor **0 / 18 = 0.0%** forgeries admitted | an OS exit code / git ancestry the attacker never touches | [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos verify --workspace . PLAN PHASE        # the oracle rung, first
```

The oracle answers what it can deterministically; what it can't, it abstains on —
and the abstention routes up the ladder, never down into a false approval.

## What this does — and does not — certify

It certifies the **ordering**: trusted-first, advisory-second, human-last, with a
one-way guarantee that the untrusted rungs can only refuse more. It does not claim
the judge is reliable (it isn't trusted to be — that's the point) or that the
oracle answers everything (it abstains, by design). The load-bearing property: a
weak rung's error can subtract approval, never add it.

## Sources / reproduce

- [`docs/87`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/87_the-adjudicator-trust-ladder.md) — the adjudicator trust-ladder (oracle / judge / human).
- [`docs/102`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/102_when-to-trust-an-agent.md) — when to trust an agent.
- [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) — the oracle-beats-judge disagreement study.
- [Why you can't trust a model to judge its own work](why-you-cant-trust-a-model-to-judge-its-own-work.md) — why the judge rung is advisory.
- [FAQ: How is DOS different from agent evals or observability platforms?](../FAQ.md#how-is-dos-different-from-agent-evals-or-observability-platforms)

> The kernel is the part that doesn't believe the agents.
