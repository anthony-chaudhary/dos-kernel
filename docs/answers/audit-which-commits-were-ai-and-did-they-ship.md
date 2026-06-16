# How to audit AI-generated commits across a repo — which were AI, and did they ship real work

> Audit each commit's subject against its own diff, author-neutrally — a forgeable
> message vs the diff git wrote. `pip install dos-kernel`, then `dos commit-audit`.
> The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter;
> never install that.

## The short answer

When a chunk of your history was written by agents, you want two things: which
commits to scrutinize, and whether each one's message matches what it actually
changed. `dos commit-audit [REF]` answers the second for any commit, by anyone —
it reads the subject and the diff and returns `OK` (the diff witnesses the claim)
or `CLAIM_UNWITNESSED` (the subject rests on its own text). It is author-neutral,
so you run it across the range and the ones that come back unwitnessed are the
empty "shipped" commits, the docs-only "fixes", the subject-vs-diff mismatches —
exactly the commits an audit should flag, surfaced from the diff rather than from
trusting the message.

## The evidence

The verdict is built on the diff git authored, never the subject the committer
wrote. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A subject-only claim is never folded as real work | the `CLAIM_UNWITNESSED` / `subject-only` rung, by construction | the diff git authored, parsed for the change kind | [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) |
| A confident write-claim is blocked when the state shows no real effect | J = 5 genuine over-claims caught off ground truth, 11.6% (5/43) live base-rate | the environment's database hash | [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos commit-audit --workspace . HEAD        # run per-commit across the range you're auditing
```

A commit whose subject claims more than its diff delivers:

```text
CLAIM_UNWITNESSED <sha> witness=subject-only — the diff does not witness the claim
```

Run it per commit over the range; the unwitnessed ones are your audit shortlist.

## What this does — and does not — certify

It certifies, per commit, whether the **diff witnesses the subject** — surfacing
empty, mislabeled, or over-claiming commits. It does not fingerprint *which* commits
were AI-written (authorship is a separate signal), nor judge code correctness; it
tells you which commit messages your history can't trust.

## Sources / reproduce

- [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) — the forgeable floor vs the diff rung.
- [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) — the live over-claim gate study.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How do I know if my agent's commit message matches what it changed](does-the-commit-message-match-what-changed.md) — the single-commit version.
- [Do AI coding agents lie about what they shipped?](do-ai-coding-agents-lie-about-what-they-shipped.md) — the broader pattern.

## Also asked as

- how to audit AI-generated commits across a repo which were AI and did they ship
- audit which commits were AI and whether they shipped real work
- tell which commits an AI agent made across a repo
- audit AI-generated commits for real content
- which commits are agent-authored and did they land work
- review a repo's AI commits for actual changes
- separate real agent commits from no-op ones
- which commits in my repo were AI-authored
- audit agent commits for real content repo-wide
- separate real agent commits from no-ops
- sweep a repo for AI commits and check they shipped

> The kernel is the part that doesn't believe the agents.
