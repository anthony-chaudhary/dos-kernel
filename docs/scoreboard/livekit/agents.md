# How AI built livekit/agents

> **Every one of the 58 AI commits here that made a checkable claim did what it said — each claim backed by the commit's own diff.**
> The check: an AI agent's commit message is just text it wrote — the diff is
> what git recorded. This page reports, for the AI-authored commits, whether
> each concrete claim in a message ("fix X", "add tests for Y") is backed by
> that commit's own diff. A message-vs-diff mismatch is **never** a
> correctness, honesty, or intent grade — only a note that a commit's words
> and its own diff disagree. Schema and the precise definition:
> [docs/311](../../311_scoreboard-per-repo-index-plan.md).

## How AI built this

AI agents wrote **2%** of the last 3,484 commits here — 71 agent-authored
commits. The agents behind them: **claude 45 · devin 17 · cursor 6 · copilot
3**.

Of those, **58** made a concrete claim you can check ("fix X", "add tests
for Y") — and **every one** was backed by the commit's own diff. No commit
claimed work its diff doesn't show.

### What a mismatch would have looked like (this repo had none)

> **would flag:** `fix: handle null user` → touched 0 files  
> **would flag:** `test: all green` → deleted test lines, added none

Neither happened here. Every "fix / add / remove" commit touched a real source file; every "tests" commit touched a real test file. That's what a clean page means — **not "nothing happened", but every checkable claim backed by the diff.**

## As of

| | |
|---|---|
| Audited range | `429a166a7bd43c628924112326bbe92f47bc45c2` → `25a6e5102e523b190c578341028c39f8483ba482` |
| Commits in range | 71 (71 attributed commits audited) |
| Rendered | 2026-06-15 |
| Auditor | dos-kernel 0.26.0 |
| Tier | seeded |
| Attribution | agent-attributed commits only (the closed marker set, docs/scoreboard/methodology.md §3); a human commit is never audited here |

## The verdict

| Commits | Checkable | Backed by the diff | Claimed, not shown (raw) | Skipped | Raw rate | Final grade |
|---|---|---|---|---|---|---|
| 71 | 58 | 58 | 0 | 13 | 0.0% | **0 of 58 (0.0%)** |

## By kind of claim

| Kind of claim | Backed by the diff | Claimed, not shown | Skipped |
|---|---|---|---|
| `fix / add / remove` (code) | 55 | 0 | 0 |
| `tests` | 2 | 0 | 0 |
| `docs` | 1 | 0 | 0 |
| no checkable claim (skipped) | — | — | 13 |

## The receipts — every flag, adjudicated

No flags in range.

## Reproduce it

```bash
git clone https://github.com/livekit/agents.git && cd agents
git checkout 25a6e5102e523b190c578341028c39f8483ba482
pip install dos-kernel
dos commit-audit --sweep --json --workspace . \
    429a166a7bd43c628924112326bbe92f47bc45c2..25a6e5102e523b190c578341028c39f8483ba482
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
