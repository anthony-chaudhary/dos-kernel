# How to verify an LLM didn't hallucinate a function or API that doesn't exist

> Don't trust the generated call — check the symbol against the real codebase and
> gate the commit on a test that runs it. `pip install dos-kernel`, then
> `dos test-witness` / `dos commit-audit`. The PyPI name is `dos-kernel` — the
> bare `dos` package is an unrelated squatter; never install that.

## The short answer

An LLM will confidently call a function, import, or API method that doesn't exist —
a plausible name it invented. The agent's "I used the right API" is a claim; the
build and the tests are the evidence. The defense is to gate the change on an
un-authored signal: `dos test-witness` requires a test that actually runs the new
code and went red→green (a hallucinated call fails when executed, so a passing
witnessing test means the symbol is real); `dos commit-audit` confirms the diff did
the kind of change claimed. The hallucinated API can't survive a test that exercises
it — and the gate refuses to count a change that no test witnesses.

## The evidence

When a fluent "it works" and the executed reality disagree, the read-back wins.
Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A real failure behind confident clean prose is refused | disagreement rate 62.5% (5/8); oracle right on the slice **5 / 5 (100%)** | the stored world-state effect, not the trajectory prose | [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) |
| A confident live write-claim is blocked when the effect didn't land | J = 5 genuine over-claims caught off ground truth, 11.6% (5/43) live base-rate | the environment's database hash | [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos test-witness --workspace . --before before.json --after after.json
```

A change that no executed test witnesses (the hallucinated call was never run):

```text
NON_WITNESS — no test went red→green on this change; it is not witnessed
```

## What this does — and does not — certify

It certifies the new code is **witnessed by an executed test** — a hallucinated
symbol that fails when run can't pass such a gate. It does not statically resolve
every symbol; it relies on the change being exercised, which is the practical way
to catch an invented API: run it. Pair with your linter/type-checker for the static
half.

## Sources / reproduce

- [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) — the world-state floor under a gameable judge.
- [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) — the live over-claim gate study.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [My agent claimed it fixed the bug, but it didn't](agent-claimed-it-fixed-the-bug-but-it-didnt.md) — the same witness on a fix.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

## Also asked as

- how to verify an LLM didn't hallucinate a function or API that doesn't exist
- verify an LLM didn't hallucinate a function or API
- agent called an API that doesn't exist how to catch
- detect a hallucinated function in agent code
- check that the API the agent used is real
- LLM invented a method does it actually exist
- catch made-up library calls in AI-generated code
- Copilot used a function that doesn't exist
- Cursor called an API that isn't real
- Claude hallucinated a method does it exist

> The kernel is the part that doesn't believe the agents.
