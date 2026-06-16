# CI passed but the feature isn't there — how do I catch that

> A green pipeline proves the tests it ran passed, not that the feature shipped.
> Check the feature's own evidence: `pip install dos-kernel`, then `dos verify` /
> `dos test-witness`. The PyPI name is `dos-kernel` — the bare `dos` package is an
> unrelated squatter; never install that.

## The short answer

CI can be green while the feature is missing — because nothing tied the green run
to the feature actually existing. The suite passed, but no new test exercises the
new behavior, or the phase that was supposed to ship never landed a commit.
`dos verify PLAN PHASE` answers whether a commit backs the phase (not whether the
pipeline is green); `dos test-witness` answers whether a *new* test went red→green
on the change (a suite that's green because it never tested the feature proves
nothing about it). Both are exit codes you can add as a CI step, so "CI passed" no
longer stands in for "the feature is here".

## The evidence

The verdict is a function of an artifact the agent did not author. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A "shipped" claim with no commit behind it answers NOT_SHIPPED | the non-forgeable-witness invariant; `via none` = checked everywhere, found nothing | git ancestry / the file tree | [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) |
| A live "I made the change" is blocked when the state disagrees | J = 5 genuine over-claims caught off ground truth, 11.6% (5/43) live base-rate | the environment's database hash | [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos verify --workspace . FEAT FEAT4        # did the feature's phase actually land?
```

A green pipeline, but the phase never shipped:

```text
NOT_SHIPPED FEAT FEAT4 (via none)
```

Exit code `1` — add it as a CI step and a green build with a missing feature fails
loudly.

## What this does — and does not — certify

It certifies the feature's phase **landed a commit** and (with `test-witness`) that
a test actually covers it — closing the "CI is green so it must be done" gap. It
does not judge whether the feature is correct or complete in behavior; it ensures
green doesn't substitute for shipped.

## Sources / reproduce

- [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) — the evidence ladder and `via none`.
- [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) — the live over-claim gate study.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to prove a phase or feature actually shipped from git history](prove-a-phase-shipped-from-git-history.md) — the verify verb in depth.
- [FAQ: How is DOS different from agent evals or observability platforms?](../FAQ.md#how-is-dos-different-from-agent-evals-or-observability-platforms)

## Also asked as

- CI passed but the feature isn't there how to catch that
- green CI but the feature was never implemented
- pipeline is green yet the work is missing
- CI green but nothing actually shipped
- passing CI doesn't mean the feature exists how to verify
- feature absent despite a passing build
- green pipeline missing feature how to catch
- CI passed but nothing was actually built
- passing build doesn't mean the work shipped
- feature absent despite green CI verify it

> The kernel is the part that doesn't believe the agents.
