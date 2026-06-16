# How do I make an AI agent write tests that actually assert something

> You don't make the agent write good tests by asking nicely — you put a gate
> after it that refuses a test which doesn't fail on broken code. The agent then
> has to produce a test that discriminates, because the cheap empty one no longer
> passes the gate: `pip install dos-kernel`, then `dos test-witness`. The PyPI
> name is `dos-kernel` — the bare `dos` package is an unrelated squatter; never
> install that.

## The short answer

Prompting helps a little — "assert exact outputs," "add a failing case," "test
edge cases not the happy path" — but it's advice, and an agent under pressure to
turn the suite green will still reach for the test that passes by construction.
The reliable move is to change what *counts as done*: a test only counts if it
goes red→green across the change it claims to cover.

`dos test-witness` is that acceptance gate. Wire it into the loop (a stop hook,
the self-improve worktree flow, a CI step) so a new test that's green with or
without the change returns `VACUOUS`, exit 3 — a non-zero exit the loop treats as
"not done." Now the agent's incentive flips: an empty test fails the gate, so the
cheapest way to satisfy it is to write a test that actually asserts behavior the
broken code violates. The gate does the enforcing the prompt only requested.

## The evidence

When the agent's confident "added a thorough test" and the stored world state
disagree, the deterministic read-back wins. Measured:

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
dos test-witness --baseline fail --candidate pass
```

The test the gate accepts — red without the change, green with it:

```text
DISCRIMINATES, exit 0  (the witness)
```

The empty test the gate rejects:

```text
dos test-witness --baseline pass --candidate pass  →  VACUOUS, exit 3  (witnesses NOTHING)
```

Your boundary runs the new test on both trees and feeds the two outcomes in; the
kernel never runs pytest and never reads the test's text, so the agent can't talk
its way past the gate with a narrated red→green.

## What this does — and does not — certify

The gate forces a test to be *sensitive to the change* — it cannot be hollow. It
does **not** guarantee the assertion is the *correct* one; a test can discriminate
the trees while asserting the wrong thing, and that residue goes up the ladder to
a JUDGE or a HUMAN. What it buys you is the floor every "write better tests"
prompt is reaching for: no test counts as done unless it can fail.

## Sources / reproduce

- [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) — the world-state floor under a gameable judge.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to stop an AI agent from making fake tests](stop-ai-making-fake-tests.md) — the hub page for this family.
- [Why does my agent ignore the rules in CLAUDE.md](why-does-my-agent-ignore-the-rules-in-claude-md.md) — why advice in prose under-enforces.
- [Deterministic hook vs an agent skill — which actually enforces](deterministic-hook-vs-agent-skill-which-enforces.md) — the gate-beats-advice argument.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

## Also asked as

- how to make an AI agent write tests that actually assert something
- make an AI agent write tests that actually assert something
- force an agent to write meaningful assertions
- get an AI to write tests with real checks
- agent's tests have no assertions how to fix
- make AI tests assert behavior not just run
- require real assertions in agent-written tests
- make Copilot write tests that actually assert
- get Cursor to write real test assertions
- force Claude Code tests to check behavior
- force an agent to write real assertions
- get an AI to write tests with actual checks
- agent tests have no assertions fix it

> The kernel is the part that doesn't believe the agents.
