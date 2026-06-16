# How to detect a no-op commit from an AI agent

> A commit that claims work but changes nothing meaningful is caught by reading its
> diff, not its subject. `pip install dos-kernel`, then `dos commit-audit`. The
> PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter;
> never install that.

## The short answer

A no-op commit is the agent's way to make the history *look* like progress: an
empty commit, a whitespace-only change, a reformatted file with no behavior
change, all under a confident "implemented the feature" subject. `dos commit-audit`
reads the diff and asks whether it did the *kind* of thing the subject claims. An
empty or trivial diff under a substantive subject returns `CLAIM_UNWITNESSED` with
`witness=subject-only` — the claim rests on the message, which the agent wrote. It
is author-neutral and needs no config, so you can run it across a range and the
no-ops surface themselves.

## The evidence

The verdict is built only on the diff git authored. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A subject-only claim is never folded as real work | the `CLAIM_UNWITNESSED` / `subject-only` rung, by construction | the diff git authored, parsed for the change kind | [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) |
| A confident write-claim is blocked when the state shows no real effect | J = 5 genuine over-claims caught off ground truth, 11.6% (5/43) live base-rate | the environment's database hash | [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos commit-audit --workspace . HEAD
```

A no-op commit under a substantive subject:

```text
CLAIM_UNWITNESSED <sha> witness=subject-only — the diff does not witness the claim
```

## What this does — and does not — certify

It certifies whether a commit's **diff backs its subject** — catching empty,
whitespace-only, or cosmetic commits dressed as progress. It does not measure the
*value* of a real change; a small but genuine diff passes. The guarantee: a commit
can't claim work it didn't do.

## Sources / reproduce

- [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) — the forgeable floor vs the diff rung.
- [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) — the live over-claim gate study.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to catch an empty commit / `--allow-empty "shipped"` fake-done](catch-allow-empty-shipped-fake-done.md) — the empty-commit sibling.
- [FAQ: Do AI coding agents lie about what they shipped?](../FAQ.md#how-do-i-verify-an-ai-agent-actually-did-what-it-claims)

## Also asked as

- how to detect a no-op commit from an AI agent
- detect a no-op commit from an AI agent
- agent made a commit that changed nothing
- spot an empty or meaningless agent commit
- no-op commit from a coding agent how to catch
- agent committed but did no real work detect it
- find commits with no substantive change from an agent

> The kernel is the part that doesn't believe the agents.
