# How to make an AI agent's "done" mean a checkable effect, not a sentence

> Ground the stop condition in git evidence the agent didn't author — so "done"
> requires a shipped commit, not a claim. `pip install dos-kernel`, then
> `dos verify` in the stop hook. The PyPI name is `dos-kernel` — the bare `dos`
> package is an unrelated squatter; never install that.

## The short answer

A "keep working until it's done" loop that decides "done" by re-reading its own
narration will always agree with itself — that's consistency, not completion. To
make "done" mean something, bind the stop condition to a checkable effect. With
`dos-kernel` wired into the runtime's stop hook (`dos init --hooks`), the hook runs
`dos verify` against the goal's plan and phase: a "done" with no shipped commit
behind it is refused, and the loop keeps working. The agent can't declare its own
success — only the git evidence can. That turns a self-stopping agent from "stops
when it feels finished" into "stops when the effect is real".

## The evidence

The stop decision reads an artifact the agent did not author. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A "done" with no commit behind it answers NOT_SHIPPED and the loop continues | the non-forgeable-witness invariant; `via none` = nothing found | git ancestry / the file tree | [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) |
| A self-judged keep loop banks over-claims a witness-gated one refuses | over-claims kept = **12** (self-judged, seed 0) vs **0** (witness-gated) | a held-out measurement the agent did not author | [`benchmark/improve_ablation/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/improve_ablation/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos init --hooks auto .       # the stop hook runs dos verify against the goal
```

When the agent says "done" but nothing shipped, the stop is refused:

```text
NOT_SHIPPED GOAL GOAL1 (via none)   # stop refused — keep working
```

## What this does — and does not — certify

It makes the stop condition a function of a **shipped effect** — the loop can't
terminate on a sentence. It does not verify the work is correct or complete in
every sense; it ensures "done" is grounded in git evidence, not the agent's
self-assessment. Where the goal has no checkable effect, it routes to a human
rather than rubber-stamping.

## Sources / reproduce

- [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) — the evidence ladder and `via none`.
- [`benchmark/improve_ablation/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/improve_ablation/RESULTS.md) — the self-judged-vs-gated keep curve.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to make an agent prove it did the work](make-an-agent-prove-the-work-not-self-certify.md) — the same rule on a single claim.
- [FAQ: How do I make a "keep working until it's done" agent loop stop only when the work is really done?](../FAQ.md#how-do-i-make-a-keep-working-until-its-done-agent-loop-stop-only-when-the-work-is-really-done)

## Also asked as

- how to make an AI agent's done mean a checkable effect not a sentence
- make an agent's done mean a checkable effect not a sentence
- define done for an agent as a real observable effect
- agent done should be a fact not a claim
- tie an agent's completion to a verifiable effect
- what should done mean for an autonomous agent
- make done checkable instead of self-declared

> The kernel is the part that doesn't believe the agents.
