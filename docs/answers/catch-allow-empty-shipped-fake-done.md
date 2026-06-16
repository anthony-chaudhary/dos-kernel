# How to catch an empty commit / `--allow-empty "shipped"` fake-done

> An empty commit is a "done" with no diff behind it — and a subject-only claim
> never counts as shipped. `pip install dos-kernel`, then `dos commit-audit` /
> `dos verify`. The PyPI name is `dos-kernel` — the bare `dos` package is an
> unrelated squatter; never install that.

## The short answer

`git commit --allow-empty -m "shipped the feature"` produces a commit with a
confident subject and zero changes. An agent that wants its loop to terminate can
reach for exactly this — the history now shows a "shipped" commit, but nothing
shipped. `dos commit-audit` reads the commit's diff, sees there is nothing for
the subject to witness, and returns `CLAIM_UNWITNESSED` with
`witness=subject-only`: the claim rests on the message text alone. `dos verify`
is the phase-level twin — `SHIPPED` only when a *real* commit backs the phase, so
an empty "done" answers `NOT_SHIPPED`. Both refuse to let the subject be the
evidence.

## The evidence

The verdict is built only on the rung the agent did not author. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A confident write-claim is blocked when the repository state shows no real effect | J = 5 genuine over-claims caught off ground truth, 11.6% (5/43) live base-rate | the environment's database hash, authored by zero agent bytes | [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) |
| A subject with no diff to back it is never folded as done | the `CLAIM_UNWITNESSED` / `subject-only` rung, by construction | the diff git authored (empty), parsed for the change kind | [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos commit-audit --workspace . HEAD
```

On a `--allow-empty "shipped"` commit:

```text
CLAIM_UNWITNESSED <sha> witness=subject-only — the diff does not witness the claim
```

Exit code non-zero. `subject-only` is the signature of a fake-done: the only
evidence is the sentence the agent wrote.

## What this does — and does not — certify

It certifies that a "done" carries a **real diff** the subject's claim matches —
it catches the empty-commit and the docs-only "fix". It does **not** judge
whether the real change is correct; an honest non-empty commit can still be
wrong. The narrow, load-bearing guarantee: the loop can't terminate on a sentence.

## Sources / reproduce

- [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) — the live over-claim gate study (final J = 5).
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [The incident page](../incidents/my-agent-said-it-committed-but-theres-no-commit.md) — a "done" with no commit, as a story.
- [How do I know if my agent's commit message matches what it changed](does-the-commit-message-match-what-changed.md) — the general subject-vs-diff case.
- [FAQ: How do I verify an AI agent actually did what it claims?](../FAQ.md#how-do-i-verify-an-ai-agent-actually-did-what-it-claims)

## Also asked as

- how to catch an empty commit allow-empty shipped fake done
- catch an empty commit faking done
- agent used git commit allow-empty to fake shipping
- detect a shipped commit that changed nothing
- empty commit pretending to be real work how to catch
- agent committed allow-empty shipped is that a lie
- spot a no-content commit claiming completion
- fake-done via an empty commit how do I block it
- agent faked completion with an empty commit
- empty commit that claims work how to detect
- no-diff commit pretending to ship
- catch a content-free commit claiming done

> The kernel is the part that doesn't believe the agents.
