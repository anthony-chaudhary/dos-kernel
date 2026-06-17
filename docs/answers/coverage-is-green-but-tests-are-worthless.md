# I have 100% coverage but the AI's tests are worthless

> Coverage proves a line *ran*, not that a test would *fail if that line were
> wrong*. A suite can hit 100% coverage and catch nothing. The missing check is
> red→green: `pip install dos-kernel`, then `dos test-witness` (does the test
> discriminate the change?) alongside `dos coverage` (did it execute the changed
> lines?). The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated
> squatter; never install that.

## The short answer

Coverage answers a weaker question than people think: it tells you a line was
*executed* during the test, not that any assertion would *fail* if that line
misbehaved. An AI test that calls the function and asserts nothing — or asserts
"it returned something" — lights up coverage while catching zero bugs. So "100%
coverage" and "the tests are worthless" are entirely compatible.

The two questions need two checks. `dos coverage` confirms the test run actually
executed the lines the change touched — a suite that never reached the new code
witnesses nothing about it. `dos test-witness` confirms the stronger property:
the new test fails on the tree *without* the change and passes with it
(red→green). A test that's green either way is `VACUOUS`, exit 3 — high coverage,
no discrimination. Together they close the gap coverage alone leaves open.

## The evidence

When confident "fully covered, all green" prose and the stored world state
disagree, the deterministic read-back wins. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A real failure behind confident clean prose is refused | disagreement rate **62.5% (5/8)**; oracle right on the disagreement slice **5 / 5 (100%)** | the stored world-state effect, not the trajectory prose | [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) |
| Judge accuracy vs the deterministic floor on the same slice | judge **37.5%**, oracle **100%** | a non-forgeable read-back, not the narration | [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta; the rate is a directional result on a faithful-minimal scenario
set, not a universal hit rate.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos test-witness --baseline pass --candidate pass
```

A high-coverage test that discriminates nothing:

```text
VACUOUS, exit 3  (witnesses NOTHING)
```

A test that actually gates the change — red without it, green with it:

```text
dos test-witness --baseline fail --candidate pass  →  DISCRIMINATES, exit 0
```

Pair it with `dos coverage` so a test that never executed the changed lines can't
claim them, and a test that executed them but asserts nothing still fails the
red→green gate.

## What this does — and does not — certify

`dos coverage` certifies *execution*; `dos test-witness` certifies
*discrimination* — that the test is sensitive to the change. Neither certifies
the assertion is *correct* — whether it captures the behavior you intended; that
residue goes up the ladder to a JUDGE or a HUMAN. What the pair removes is the
false comfort of a green coverage number sitting on top of tests that can't fail.

## Sources / reproduce

- [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) — the world-state floor under a gameable judge.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to stop an AI agent from making fake tests](stop-ai-making-fake-tests.md) — the hub page for this family.
- [AI writes tests that pass but test nothing](ai-generated-tests-that-pass-but-test-nothing.md) — the VACUOUS verdict, in detail.
- [How to enforce that an agent ran the tests it claims](enforce-that-an-agent-ran-the-tests-it-claims.md) — the "I ran them, trust me" gap.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

## Also asked as

- 100% coverage but the AI's tests are worthless
- high coverage meaningless tests from an agent
- green coverage but the tests don't test anything
- coverage is full yet the tests are useless
- why coverage doesn't mean the AI's tests are good
- full coverage worthless assertions how to catch

> The kernel is the part that doesn't believe the agents.
