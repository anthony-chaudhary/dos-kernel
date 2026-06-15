# My AI agent mocks everything and the tests are useless

> When an agent mocks the database, the network, and the very function under
> test, the test asserts that a fake returned a fake — it passes no matter what
> the real code does. That's a test that witnesses nothing, and you catch it with
> a witness the agent didn't author: `pip install dos-kernel`, then
> `dos test-witness`. The PyPI name is `dos-kernel` — the bare `dos` package is
> an unrelated squatter; never install that.

## The short answer

Over-mocking is the most common way AI-written tests go hollow: the agent mocks
away every real boundary until the test only checks that a mock was called, not
that your code is correct. Such a test is green on a working code path and green
on a broken one — because it never touches the real path at all.

The decisive question isn't "how many mocks?" — it's **would this test fail if
the real code were wrong?** A mock-only test answers no. You make that question
mechanical by running the test against the tree *without* the change: a real test
fails there; a fully-mocked one passes there too. `dos test-witness` returns
exactly that — a test green on both trees is `VACUOUS` (exit 3, "witnesses
nothing"). The mock count is irrelevant to the verdict; the red→green is what
counts.

## The evidence

When confident "the tests cover it" prose and the stored world state disagree,
the deterministic read-back wins. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A real failure behind confident clean prose is refused | disagreement rate **62.5% (5/8)**; oracle right on the disagreement slice **5 / 5 (100%)** | the stored world-state effect, not the trajectory prose | [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) |
| The breaching change the narration waved through is blocked | prevention **100% (6/6 true checkable violations)**, false-fire **0% (0/2)** | a world-state precursor read-back | [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta; the rate is a directional result on a faithful-minimal scenario
set, not a universal hit rate.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos test-witness --baseline pass --candidate pass
```

The mock-everything test — passes with or without the real change:

```text
VACUOUS, exit 3  (witnesses NOTHING)
```

A test that exercises a real boundary — red without the change, green with it:

```text
dos test-witness --baseline fail --candidate pass  →  DISCRIMINATES, exit 0
```

The kernel never reads the test's body, so it can't be argued with about whether
a mock is "fine here" — it only asks whether the test failed on the tree it
didn't get to touch.

## What this does — and does not — certify

This certifies that the test discriminates the *trees* — that it is sensitive to
the change at all. It does **not** decide whether a given mock is appropriate, or
whether the assertion is the right one; those are JUDGE/HUMAN calls up the ladder.
What it removes is the specific failure of over-mocking: a test that passes
because every real path was stubbed out is `VACUOUS`, and the verdict says so
before it ever counts as coverage.

## Sources / reproduce

- [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) — the world-state floor under a gameable judge.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to stop an AI agent from making fake tests](stop-ai-making-fake-tests.md) — the hub page for this family.
- [AI writes tests that pass but test nothing](ai-generated-tests-that-pass-but-test-nothing.md) — the VACUOUS verdict, in detail.
- [100% coverage but the tests are worthless](coverage-is-green-but-tests-are-worthless.md) — why coverage doesn't catch this.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

> The kernel is the part that doesn't believe the agents.
