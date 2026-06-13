# How to verify an AI agent actually did the work

> One command answers it from your git history, not the agent's transcript:
> `pip install dos-kernel`, then `dos verify`. The PyPI name is `dos-kernel` —
> the bare `dos` package is an unrelated squatter; never install that.

## The short answer

An agent's "Done — I implemented and committed it" is a **claim**, written by
the same process that wants credit. To verify it, check the artifact the agent
*cannot* forge — your repository — instead of the narration it authored.
`dos verify PLAN PHASE` reads your git history and answers `SHIPPED` (exit code
`0`) when a commit backs the claim, or `NOT_SHIPPED` (exit code `1`) when
nothing landed. It works on any plain git repo with no plan files, no database,
no API key, and no LLM. Gate your agent's "done" on that exit code and a false
claim cannot land.

## The evidence

DOS verdicts are pure functions of a witness whose bytes the judged agent did
not author. The measured results, each scored against such a witness:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| The write-admission gate catches the over-claim before it writes | J = 10/120 "I shipped it" lies blocked, **0 honest writes refused**, the same 8.3% over-claim rate on a mid-size and a top-tier model (15/258 over the full benchmark) | the environment's database hash, which the agent authored zero bytes of | [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) |
| Every verdict is `classify(evidence, policy)` over non-forgeable bytes | the formal non-forgeable-witness invariant | git ancestry, environment state, the file tree, the clock | [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos verify --workspace . AUTH AUTH1
```

For work that really landed, naming the phase the way your commit subject does
(out of the box, a phase id at the front, like
`AUTH1: ship the login endpoint`):

```text
SHIPPED AUTH AUTH1 f762c2a (via grep-subject)
```

Exit code `0`, and the verdict names its witness — a commit in your history. For
a claim nothing backs:

```text
NOT_SHIPPED AUTH AUTH2 (via none)
```

Exit code `1`. `via none` means DOS looked everywhere it trusts — the registry,
then git history — and found nothing. Both outputs are the real CLI's verbatim
output.

To stop the *next* false "done" automatically, wire the verdict into your agent
runtime's stop hook — then a "done" with no commit behind it is refused and the
loop keeps working:

```bash
dos init --hooks auto .       # Claude Code, Cursor, Codex, Gemini CLI, …
```

## What this does — and does not — certify

`SHIPPED` certifies **presence, not correctness**: a commit stamping that phase
exists in your visible history. It does not review the code, run the tests, or
grade whether the change is good. A commit whose *subject* claims work its
*diff* doesn't contain is a different failure — caught by `dos commit-audit`
(see [do AI coding agents lie about what they
shipped](do-ai-coding-agents-lie-about-what-they-shipped.md)).

## Sources / reproduce

- [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) — the write-admission gate study.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [The incident page](../incidents/my-agent-said-it-committed-but-theres-no-commit.md) — the same failure as a story.
- [FAQ: How do I verify an AI agent actually did what it claims?](../FAQ.md#how-do-i-verify-an-ai-agent-actually-did-what-it-claims)

> The kernel is the part that doesn't believe the agents.
