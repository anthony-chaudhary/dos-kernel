# How do I know if my AI agent's commit message matches what it actually changed

> The subject is forgeable; the diff is not. Compare them: `pip install
> dos-kernel`, then `dos commit-audit`. The PyPI name is `dos-kernel` — the bare
> `dos` package is an unrelated squatter; never install that.

## The short answer

An agent writes the commit subject, so it can say "fix: handle null user in auth"
over a diff that only touched the README — or a `--allow-empty` "shipped" over no
diff at all. `dos commit-audit [REF]` reads the commit's subject *and* its own
diff and asks whether the diff did the *kind* of thing the subject claims. It is
author-neutral (it grades a human's commit the same way), needs no plan and no
config, and answers with an exit code: `OK` when the diff witnesses the subject,
`CLAIM_UNWITNESSED` when the subject rests on the message text alone.

## The evidence

DOS reads the diff git wrote, never the subject the agent wrote. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A confident live write-claim is blocked when the repository state contradicts it | J = 5 genuine over-claims caught off ground truth, 11.6% (5/43) live base-rate | the environment's database hash, authored by zero agent bytes | [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) |
| The verdict is built only on the un-authored evidence rung | a subject-only claim is `CLAIM_UNWITNESSED`, never folded as done | the diff git authored, parsed for the change kind | [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos commit-audit --workspace . HEAD
```

When the diff backs the subject:

```text
OK <sha> claim_kind=fix witness=diff-witnessed
```

When the subject claims work the diff doesn't contain:

```text
CLAIM_UNWITNESSED <sha> witness=subject-only — the diff does not witness the claim
```

Exit code non-zero on the second. `witness=subject-only` is the tell: the claim
rests on the message text, which the agent authored.

## What this does — and does not — certify

`dos commit-audit` grades whether the diff did the *kind* of thing the subject
claims — it catches a `fix:` over a docs-only change, or an empty "shipped". It
does **not** judge whether the code is correct (run the tests for that). A
subject and diff can agree on a change that is still wrong; this closes the
narrower, load-bearing gap where the *message* is the only evidence.

## Sources / reproduce

- [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) — the live over-claim gate study (final J = 5).
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [The incident page](../incidents/the-ai-wrote-tests-that-test-nothing.md) — a commit that claims tests it never added.
- [How to verify an agent actually committed code](how-to-verify-an-ai-agent-actually-committed-code.md) — the presence question, where this starts.
- [FAQ: How do I verify an AI agent actually did what it claims?](../FAQ.md#how-do-i-verify-an-ai-agent-actually-did-what-it-claims)

> The kernel is the part that doesn't believe the agents.
