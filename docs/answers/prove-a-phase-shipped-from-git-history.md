# How to prove a phase or feature actually shipped from git history

> Read git ancestry, not a status report: `pip install dos-kernel`, then
> `dos verify`. No plan files needed. The PyPI name is `dos-kernel` — the bare
> `dos` package is an unrelated squatter; never install that.

## The short answer

"Phase 3 is shipped" is a sentence someone wrote; a commit that lands phase 3 is
a thing git wrote. `dos verify PLAN PHASE` answers from your commit history alone:
`SHIPPED` (exit `0`) when a commit in your visible ancestry stamps the phase,
`NOT_SHIPPED` (exit `1`) when nothing does. It needs no plan documents, no
registry, and no LLM — it reads git ancestry against your ship-stamp grammar (out
of the box, a phase id at the front of the subject). The verdict always names
which witness answered, so "shipped" is traceable to a specific commit, never to
a `> **Status:** done` line in a doc.

## The evidence

The verdict is a pure function of an artifact the claimant did not author.
Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A live "I made the change" claim is blocked when the repository state disagrees | J = 5 genuine over-claims caught off ground truth, 11.6% (5/43) live base-rate | the environment's database hash, authored by zero agent bytes | [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) |
| Every verdict orders evidence by forgeability; git ancestry outranks any self-report | the non-forgeable-witness invariant | git ancestry / the file tree / the clock | [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos verify --workspace . RS RS1
```

For a phase a commit really stamps:

```text
SHIPPED RS RS1 a1b2c3d (via grep-subject)
```

Exit `0`, witness named. For a phase nothing backs:

```text
NOT_SHIPPED RS RS1 (via none)
```

Exit `1`. `via none` means DOS checked the run registry and git history and found
nothing — an evidence horizon, stated honestly, not a guess.

## What this does — and does not — certify

`SHIPPED` certifies a **commit stamping the phase exists in your ancestry** — its
presence, not its correctness. It does not run your tests or review the diff. Pair
it with `dos commit-audit` (subject-vs-diff) and `dos test-witness` (red→green) to
close the gap from "a commit exists" toward "the right change landed".

## Sources / reproduce

- [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) — the live over-claim gate study (final J = 5).
- [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) — the evidence ladder ordered by forgeability.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to verify an agent actually committed code](how-to-verify-an-ai-agent-actually-committed-code.md) — the same verb on a single claim.
- [FAQ: Do I need to restructure my repository or write plan files first?](../FAQ.md#do-i-need-to-restructure-my-repository-or-write-plan-files-first)

## Also asked as

- how to prove a phase or feature actually shipped from git history
- prove a phase or feature actually shipped from git history
- confirm a milestone landed using git ancestry
- did this phase ship check the git log not the plan
- verify a feature shipped from commits alone
- prove work landed with no registry just git
- git-based proof that a phase is complete
- show a feature shipped from the commit history
- confirm a milestone landed from commits alone
- git-only proof a feature is complete
- show a phase shipped without a tracking system
- did this milestone actually ship check git

> The kernel is the part that doesn't believe the agents.
