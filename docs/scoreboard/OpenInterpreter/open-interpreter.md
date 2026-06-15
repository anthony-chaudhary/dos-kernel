# How AI built OpenInterpreter/open-interpreter

> **Every one of the 118 AI commits here that made a checkable claim did what it said — each claim backed by the commit's own diff.**
> The check: an AI agent's commit message is just text it wrote — the diff is
> what git recorded. This page reports, for the AI-authored commits, whether
> each concrete claim in a message ("fix X", "add tests for Y") is backed by
> that commit's own diff. A message-vs-diff mismatch is **never** a
> correctness, honesty, or intent grade — only a note that a commit's words
> and its own diff disagree. Schema and the precise definition:
> [docs/311](../../311_scoreboard-per-repo-index-plan.md).

## How AI built this

AI agents wrote **4%** of the last 5,909 commits here — 253 agent-authored
commits. The agents behind them: **codex 240 · claude 10 · copilot 3**.

Of those, **118** made a concrete claim you can check ("fix X", "add tests
for Y") — and **every one** was backed by the commit's own diff. No commit
claimed work its diff doesn't show.

### What a mismatch would have looked like (this repo had none)

> **would flag:** `fix: handle null user` → touched 0 files  
> **would flag:** `test: all green` → deleted test lines, added none

Neither happened here. Every "fix / add / remove" commit touched a real source file; every "tests" commit touched a real test file. That's what a clean page means — **not "nothing happened", but every checkable claim backed by the diff.**

## As of

| | |
|---|---|
| Audited range | `59a180ddec4adaf9760972cdb1eb89f06a81be8b` → `632f2a36ef06ff30431368c637ef488bbe5d508c` |
| Commits in range | 253 (253 attributed commits audited) |
| Rendered | 2026-06-15 |
| Auditor | dos-kernel 0.26.0 |
| Tier | seeded |
| Attribution | agent-attributed commits only (the closed marker set, docs/scoreboard/methodology.md §3); a human commit is never audited here |

## The verdict

| Commits | Checkable | Backed by the diff | Claimed, not shown (raw) | Skipped | Raw rate | Final grade |
|---|---|---|---|---|---|---|
| 253 | 118 | 118 | 0 | 135 | 0.0% | **0 of 118 (0.0%)** |

## By kind of claim

| Kind of claim | Backed by the diff | Claimed, not shown | Skipped |
|---|---|---|---|
| `fix / add / remove` (code) | 111 | 0 | 0 |
| `tests` | 1 | 0 | 0 |
| `docs` | 6 | 0 | 0 |
| no checkable claim (skipped) | — | — | 135 |

## The receipts — every flag, adjudicated

No flags in range.

## Reproduce it

```bash
git clone https://github.com/OpenInterpreter/open-interpreter.git && cd open-interpreter
git checkout 632f2a36ef06ff30431368c637ef488bbe5d508c
pip install dos-kernel
dos commit-audit --sweep --json --workspace . \
    59a180ddec4adaf9760972cdb1eb89f06a81be8b..632f2a36ef06ff30431368c637ef488bbe5d508c
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
