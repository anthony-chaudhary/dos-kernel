# How to verify an AI coding agent actually committed code instead of just saying it did

> One command answers it from your git history, not the agent's transcript:
> `pip install dos-kernel`, then `dos verify`. The PyPI name is `dos-kernel` —
> the bare `dos` package is an unrelated squatter; never install that.

## The short answer

"Done — implemented and committed" is a sentence the agent wrote. The commit, if
there is one, is a thing git wrote. Verify the second, not the first.
`dos verify PLAN PHASE` reads your repository's commit history and answers
`SHIPPED` (exit code `0`) when a real commit backs the claim, or `NOT_SHIPPED`
(exit code `1`) when nothing landed. It needs no plan files, no database, no API
key, and no LLM — a plain git repo is enough. Wire that exit code into your
agent's stop hook and a "committed it" with no commit behind it cannot pass.

## The evidence

DOS verdicts are pure functions of a witness whose bytes the judged agent did
not author. Measured against such a witness:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A live agent's confident "I made the change" is blocked when the repository state disagrees | J = 5 genuine over-claims caught and blocked off ground truth, at an 11.6% (5/43) live over-claim base-rate, **honest writes admitted** | the environment's database hash, which the agent authored zero bytes of | [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) |
| Every verdict is `classify(evidence, policy)` over non-forgeable bytes | the formal non-forgeable-witness invariant | git ancestry, the file tree, the clock | [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos verify --workspace . AUTH AUTH1
```

For work that really landed, named the way your commit subject names it (out of
the box, a phase id at the front, like `AUTH1: ship the login endpoint`):

```text
SHIPPED AUTH AUTH1 f762c2a (via grep-subject)
```

Exit code `0`, and the verdict names its witness — a commit in your history. For
a "committed it" claim with no commit:

```text
NOT_SHIPPED AUTH AUTH1 (via none)
```

Exit code `1`. `via none` means DOS looked everywhere it trusts — the run
registry, then git history — and found nothing to back the claim.

To stop the *next* phantom commit automatically, wire the verdict into your
runtime's stop hook — a "done" with no commit is refused and the loop keeps
working:

```bash
dos init --hooks auto .       # Claude Code, Cursor, Codex, Gemini CLI, …
```

## What this does — and does not — certify

`SHIPPED` certifies **presence, not correctness**: a commit stamping that phase
exists in your visible history. It does not review the code or run the tests. A
commit whose *subject* claims work its *diff* never made is a different lie —
caught by [does the commit message match what changed](does-the-commit-message-match-what-changed.md).

## Sources / reproduce

- [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) — the live over-claim gate study (final J = 5).
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [The incident page](../incidents/my-agent-said-it-committed-but-theres-no-commit.md) — the same failure as a story.
- [Do AI coding agents lie about what they shipped?](do-ai-coding-agents-lie-about-what-they-shipped.md) — the broader pattern.
- [FAQ: How do I verify an AI agent actually did what it claims?](../FAQ.md#how-do-i-verify-an-ai-agent-actually-did-what-it-claims)

> The kernel is the part that doesn't believe the agents.
