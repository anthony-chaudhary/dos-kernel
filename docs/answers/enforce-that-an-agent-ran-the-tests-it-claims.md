# How to enforce that an AI agent actually ran the tests it claims it ran

> "I ran the tests" is a claim; the test runner's exit code and a red→green
> witness are the evidence. `pip install dos-kernel`, then `dos test-witness` /
> `dos coverage`. The PyPI name is `dos-kernel` — the bare `dos` package is an
> unrelated squatter; never install that.

## The short answer

An agent can say "I ran the tests and they pass" without running anything, or run
a suite that never touches the change. The fix is to stop accepting the claim and
read the effect: `dos test-witness` requires a *new* test that went red→green on
the change (a test that passes before and after witnesses nothing); `dos coverage`
requires the test run to have actually executed the changed lines. Both are exit
codes a CI step or stop hook can gate on, so "I ran the tests" only counts when an
un-authored witness — the runner's result, the coverage of the new lines — agrees.

## The evidence

When the agent's "it's clean" and the world disagree, the read-back wins.
Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A real failure behind confident clean prose is refused | disagreement rate 62.5% (5/8); oracle right on the slice **5 / 5 (100%)** | the stored world-state effect, not the trajectory prose | [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) |
| The breaching write the narration waved through is blocked | prevention **100% (6/6 true checkable violations)**, false-fire **0% (0/2)** | a world-state precursor read-back | [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos test-witness --workspace . --before before.json --after after.json
```

A suite that was already green before the change witnesses nothing about it:

```text
NON_WITNESS — the test passed before AND after; it does not gate this change
```

Exit code non-zero — "I ran the tests" doesn't count unless a test actually went
red→green on the change.

## What this does — and does not — certify

It certifies a test run **happened and gated the change** (red→green; the new lines
executed) — not that the assertions are meaningful or the code correct. It closes
the "I ran them, trust me" gap: the claim only passes when the runner's own result
agrees.

## Sources / reproduce

- [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) — the world-state floor under a gameable judge.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [My AI agent said "all tests pass" but the app is broken](ai-agent-said-tests-pass-but-app-is-broken.md) — the sibling failure.
- [AI agent deleted my tests to make the build pass](ai-agent-deleted-my-tests-to-pass-the-build.md) — the adversarial version.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

## Also asked as

- how to enforce that an AI agent actually ran the tests it claims it ran
- enforce that an AI agent actually ran the tests it claims
- did the agent really run the tests or just say so
- require proof an agent executed its tests
- agent claims tests ran verify it
- make an agent show it ran the test suite
- confirm an agent's test run actually happened

> The kernel is the part that doesn't believe the agents.
