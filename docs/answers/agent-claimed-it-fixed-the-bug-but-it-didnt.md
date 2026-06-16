# My AI agent claimed it fixed the bug, but it didn't

> "Fixed it" is a claim; a commit that changes the buggy code (and a test that
> goes red→green) is the evidence. `pip install dos-kernel`, then `dos verify` /
> `dos commit-audit` / `dos test-witness`. The PyPI name is `dos-kernel` — the
> bare `dos` package is an unrelated squatter; never install that.

## The short answer

An agent reports "fixed the bug" in three ways that aren't a fix: no commit at
all, a commit whose diff doesn't touch the buggy path, or a commit with no test
that fails-without-it. Each is a claim the agent authored; each is checkable
against something it didn't. `dos verify` confirms a commit backs the claim;
`dos commit-audit` confirms the diff did the kind of change the subject claims;
`dos test-witness` confirms a test went red→green on the fix (a "fix" with no
failing test behind it witnesses nothing). Gate on those exit codes and "fixed it"
stops being something you take on faith.

## The evidence

The verdict reads the artifact, not the report. Measured live:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A confident "I made the change" is blocked when the repository state disagrees | J = 5 genuine over-claims caught off ground truth, 11.6% (5/43) live base-rate | the environment's database hash, authored by zero agent bytes | [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) |
| Where a fluent "it's fixed" and the world disagree, the read-back is right | disagreement rate 62.5% (5/8); oracle right on the slice **5 / 5 (100%)** | the stored world-state effect, not the trajectory prose | [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos commit-audit --workspace . HEAD        # did the "fix" diff actually touch the bug?
```

A "fix the null-user crash" subject over a diff that only edited a comment:

```text
CLAIM_UNWITNESSED <sha> witness=subject-only — the diff does not witness the claim
```

## What this does — and does not — certify

It certifies a fix is **backed by a real diff and a witnessing test** — it catches
"fixed it" with no commit, no relevant change, or no failing test. It does not
prove the bug is gone for every input; pair it with the test that reproduces the
bug, and require that test to go red→green.

## Sources / reproduce

- [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) — the live over-claim gate study (final J = 5).
- [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) — the world-state floor under a gameable judge.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [My agent said "all tests pass" but the app is broken](ai-agent-said-tests-pass-but-app-is-broken.md) — the sibling failure.
- [FAQ: How do I verify an AI agent actually did what it claims?](../FAQ.md#how-do-i-verify-an-ai-agent-actually-did-what-it-claims)

## Also asked as

- my AI agent claimed it fixed the bug but it didn't
- agent says bug fixed but it's still broken
- verify an agent actually fixed the bug
- agent reports a fix that didn't work how to catch
- is the bug really fixed or did the agent just say so
- agent's fix claim is false how do I detect it
- Cursor claimed it fixed the bug but it didn't
- Copilot says fixed but the bug is still there
- Claude Code reported a fix that didn't work
- agent says fixed but the bug repro still fails
- verify an agent's bug fix actually works
- false fix claim from a coding agent
- agent closed the bug but it's not fixed

> The kernel is the part that doesn't believe the agents.
