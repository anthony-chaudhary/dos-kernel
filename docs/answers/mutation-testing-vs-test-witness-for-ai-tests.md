# Mutation testing vs a test-witness gate for checking AI-generated tests

> Mutation testing asks "does my whole suite catch injected bugs?" — a thorough,
> expensive batch job. A test-witness gate asks "does *this one new* test catch
> the change it claims to cover?" — a cheap per-change floor you can put in CI.
> Use both: `pip install dos-kernel`, then `dos test-witness`. The PyPI name is
> `dos-kernel` — the bare `dos` package is an unrelated squatter; never install
> that.

## The short answer

The standard advice for AI-written tests is mutation testing: flip operators,
remove null checks, change return values across your code, then see which mutants
your suite fails to kill. It's the gold standard for test-suite quality — and
it's a heavy, whole-suite sweep you run occasionally, not on every commit.

`dos test-witness` is the cheap complement aimed at the moment a test is born.
The mutation-testing intuition is "a good test fails on a wrong version of the
code." The single most informative mutant for a brand-new test is *the absence of
the change it was added for*. So test-witness runs the new test against the tree
**without** the change: a test that doesn't fail there is `VACUOUS` — it would
survive the most basic mutant of all. Red→green is `DISCRIMINATES`. One exit
code, runnable on every PR, with no mutation engine and no minutes-long sweep.

Run a full mutation pass for suite-wide confidence; run test-witness as the
per-change gate that refuses a hollow test the moment it's written.

## The evidence

When confident "the suite is solid" prose and the stored world state disagree,
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

A test that survives the no-change "mutant" — green both ways:

```text
VACUOUS, exit 3  (witnesses NOTHING)
```

A test that kills it — red without the change, green with it:

```text
dos test-witness --baseline fail --candidate pass  →  DISCRIMINATES, exit 0
```

## What this does — and does not — certify

Test-witness certifies one mutant per new test — the absence of its own change —
so it is a floor, not a substitute for a full mutation pass. It does **not**
measure suite-wide mutation score, and it does **not** judge whether the
assertion is the right one (that goes up the ladder to a JUDGE or a HUMAN). Its
job is to make "this test catches nothing" fail fast and cheap, on every change.

## Sources / reproduce

- [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) — the world-state floor under a gameable judge.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How do I tell if my AI-generated tests are real](are-my-ai-generated-tests-real.md) — the red→green check, plainly.
- [How to stop an AI agent from making fake tests](stop-ai-making-fake-tests.md) — the hub page for this family.
- [Deterministic hook vs an agent skill — which actually enforces](deterministic-hook-vs-agent-skill-which-enforces.md) — why a gate beats advice.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

## Also asked as

- mutation testing vs a test-witness gate for AI-generated tests
- mutation testing vs a test-witness gate for AI tests
- is mutation testing enough to catch fake AI tests
- test-witness vs mutation testing for agent tests
- how to prove AI tests catch real bugs
- compare mutation testing and a witness gate for tests
- best way to validate AI-generated tests really assert

> The kernel is the part that doesn't believe the agents.
