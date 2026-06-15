# How AI built danny-avila/LibreChat

> **Every one of the 24 AI commits here that made a checkable claim did what it said — each claim backed by the commit's own diff.**
> The check: an AI agent's commit message is just text it wrote — the diff is
> what git recorded. This page reports, for the AI-authored commits, whether
> each concrete claim in a message ("fix X", "add tests for Y") is backed by
> that commit's own diff. A message-vs-diff mismatch is **never** a
> correctness, honesty, or intent grade — only a note that a commit's words
> and its own diff disagree. Schema and the precise definition:
> [docs/311](../../311_scoreboard-per-repo-index-plan.md).

## How AI built this

AI agents wrote **1%** of the last 4,519 commits here — 38 agent-authored
commits. The agents behind them: **claude 24 · copilot 13 · cursor 1**.

Of those, **24** made a concrete claim you can check ("fix X", "add tests
for Y") — and **every one** was backed by the commit's own diff. No commit
claimed work its diff doesn't show.

### What a mismatch would have looked like (this repo had none)

> **would flag:** `fix: handle null user` → touched 0 files  
> **would flag:** `test: all green` → deleted test lines, added none

Neither happened here. Every "fix / add / remove" commit touched a real source file; every "tests" commit touched a real test file. That's what a clean page means — **not "nothing happened", but every checkable claim backed by the diff.**

## As of

| | |
|---|---|
| Audited range | `466dd013276cbb56b356a29d80e82739108891fb` → `9efe4878e7251d3b66d0ece7135706fdacf5a334` |
| Commits in range | 38 (38 attributed commits audited) |
| Rendered | 2026-06-15 |
| Auditor | dos-kernel 0.26.0 |
| Tier | seeded |
| Attribution | agent-attributed commits only (the closed marker set, docs/scoreboard/methodology.md §3); a human commit is never audited here |

## The verdict

| Commits | Checkable | Backed by the diff | Claimed, not shown (raw) | Skipped | Raw rate | Final grade |
|---|---|---|---|---|---|---|
| 38 | 24 | 24 | 0 | 14 | 0.0% | **0 of 24 (0.0%)** |

## By kind of claim

| Kind of claim | Backed by the diff | Claimed, not shown | Skipped |
|---|---|---|---|
| `fix / add / remove` (code) | 23 | 0 | 0 |
| `docs` | 1 | 0 | 0 |
| no checkable claim (skipped) | — | — | 14 |

## The receipts — every flag, adjudicated

No flags in range.

## Reproduce it

```bash
git clone https://github.com/danny-avila/LibreChat.git && cd LibreChat
git checkout 9efe4878e7251d3b66d0ece7135706fdacf5a334
pip install dos-kernel
dos commit-audit --sweep --json --workspace . \
    466dd013276cbb56b356a29d80e82739108891fb..9efe4878e7251d3b66d0ece7135706fdacf5a334
```

A newer auditor over the same pinned range may count differently as
fire-narrowing continues (each narrowing is a public issue, e.g. #79/#81);
the as-of block above is what this page graded, with what.

## Corrections

A contested flag gets re-adjudicated and the page re-rendered (the
[docs/311](../../311_scoreboard-per-repo-index-plan.md) §3 path). Until the
`scoreboard-correction` template ships (docs/311 P4), open a plain issue
naming this page and the SHA. Methodology — what the witness reads, what it
abstains on, where it has been wrong:
[docs/scoreboard/methodology.md](../methodology.md).
