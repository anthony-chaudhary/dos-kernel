# What does "true" mean for an AI agent's verdict — and why does it matter

> Truth, for a kernel that won't believe the agents, is the narrow thing it can
> establish *without* trusting the author of the claim. `pip install dos-kernel`,
> then `dos verify`. The PyPI name is `dos-kernel` — the bare `dos` package is an
> unrelated squatter; never install that.

## The short answer

DOS doesn't try to know "what is the case". It knows the narrower, mechanizable
thing: a verdict is true when it stands on an artifact the judged agent could not
have authored its way around. Everything else — the transcript, the commit
subject, the agent's "I'm confident" — is a self-report, demoted to a pointer that
must clear a non-forgeable checkpoint before it anchors any work. The axis is
forgeability: a `SHIPPED` requires real git ancestry plus a real file footprint;
an empty "done" stays at the bottom rung and is never believed. Where no
un-authored witness exists, the kernel says so and routes to a human rather than
pretending to know. That is why "verified" here is always traceable to specific
bytes the agent didn't write.

## The evidence

Make most cheap lies expensive, and the ones that stay cheap are the named bottom
rungs. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A gate reading an un-authored witness admits zero forged "it succeeded" claims | witness floor **0 / 18 = 0.0%** vs text-believing **18 / 18 = 100.0%** | an OS exit code / git ancestry the attacker never touches | [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) |
| Truth is `classify(evidence, policy)` over the un-authored rung, by invariant | the non-forgeable-witness invariant; the actor's absence from the witness | git ancestry / the file tree / the clock | [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos verify --workspace . PLAN PHASE
```

The verdict names its witness — so "true" is never a mood, always a rung:

```text
SHIPPED PLAN PHASE a1b2c3d (via grep-subject)
```

## What this does — and does not — certify

It certifies a claim against an **un-authored effect** — presence, ancestry,
subject-vs-diff agreement. It does not certify correctness, and where there is no
witness it deliberately abstains to a human. The thesis in one line: truth in DOS
is the consequence of making most cheap lies expensive.

## Sources / reproduce

- [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) — what is truth, the throughline (the evidence ladder).
- [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) — the forgeability advantage, measured.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [Why you can't trust a model to judge its own work](why-you-cant-trust-a-model-to-judge-its-own-work.md) — the same axis, applied to self-grading.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

## Also asked as

- what does true mean for an AI agent's verdict
- how is truth defined for an agent's decision
- what counts as ground truth for an agent verdict
- the meaning of true in an automated verdict
- why does an agent verdict need a definition of truth
- truth as a non-forgeable witness for agents
- ground truth definition for an agent decision
- what makes an automated verdict trustworthy
- how do we define correct for an agent verdict

> The kernel is the part that doesn't believe the agents.
