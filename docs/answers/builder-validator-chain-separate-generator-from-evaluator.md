# How to build a builder-validator chain — separate the generator from the evaluator

> Make the validator read an effect the generator didn't author, so the two can't
> collude. `pip install dos-kernel`, then `dos verify` / `dos commit-audit`. The
> PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter;
> never install that.

## The short answer

The builder-validator pattern fails when the validator is just another prompt to
the same kind of model: a fluent generator talks a fluent validator into a pass.
The split only works if the validator's verdict is a function of something the
generator could not author — git ancestry, a test exit code, a world-state
read-back. `dos verify` validates a claimed phase against the commit history;
`dos commit-audit` validates a claimed change against its own diff;
`dos test-witness` validates a claimed fix against a red→green test. Put one of
those between builder and validator and the generator can't pass by being
persuasive — only by producing a real effect.

## The evidence

A text-believing validator is gamed; a witness-reading one isn't. Measured on
identical inputs:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A witness floor admits zero forged successes; a text-believing validator admits all | witness floor **0 / 18 = 0.0%** vs text-believing **18 / 18 = 100.0%** | an OS exit code / git ancestry the generator never touches | [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) |
| Where a model-validator and a deterministic oracle disagree, the oracle is right | disagreement rate 62.5% (5/8); oracle right on the slice **5 / 5 (100%)** | the stored world-state effect, not the trajectory | [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos verify --workspace . BUILD BUILD1        # the validator rung, reading git
```

The builder's claim passes only when the artifact backs it:

```text
NOT_SHIPPED BUILD BUILD1 (via none)   # validator rejects — no effect to validate
```

## What this does — and does not — certify

It makes the validator's verdict a function of an **un-authored effect** — so the
generator and validator can't collude through prose. It does not replace a human
or model reviewer for judgment calls (those route up the trust ladder); it gives
the deterministic rung that a builder-validator chain needs to actually separate
the two roles.

## Sources / reproduce

- [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) — the witness-forgery challenge.
- [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) — the oracle-beats-judge study.
- [`docs/87`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/87_the-adjudicator-trust-ladder.md) — the adjudicator trust-ladder.
- [Why you can't trust a model to judge its own work](why-you-cant-trust-a-model-to-judge-its-own-work.md) — why the validator must read a witness.
- [FAQ: How is DOS different from agent evals or observability platforms?](../FAQ.md#how-is-dos-different-from-agent-evals-or-observability-platforms)

## Also asked as

- how to build a builder-validator chain that separates generator from evaluator
- builder-validator chain separate generator from evaluator
- split the agent that builds from the one that checks
- generator-evaluator separation for coding agents
- why the validator must not be the generator
- two-stage agent build then independently verify
- separate generation and evaluation in an agent pipeline
- separate the agent that writes from the one that checks
- generator and validator must be different agents
- two-stage build-then-verify agent pipeline
- why the evaluator can't be the generator

> The kernel is the part that doesn't believe the agents.
