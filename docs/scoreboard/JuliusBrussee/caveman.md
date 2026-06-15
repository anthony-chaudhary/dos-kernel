# JuliusBrussee/caveman — drift scoreboard

> **Clean — every one of 49 checkable commit messages matched its diff. 0 drifts.**
> A drift is a commit whose subject claims something its own diff doesn't show
> — an empty commit that says "fixed it", a "tests pass" that deletes the
> test. A drift is a message-vs-diff mismatch — **never** a correctness,
> honesty, or intent grade. Schema and grade vocabulary:
> [docs/311](../../311_scoreboard-per-repo-index-plan.md).

### What a drift would have looked like (this repo had none)

> **would flag:** `fix: handle null user` → touched 0 files  
> **would flag:** `test: all green` → deleted test lines, added none

Neither happened here. Every "fix / add / remove" commit touched a real source file; every "tests" commit touched a real test file. That's what clean means — **not "nothing happened", but every checkable claim backed by the diff.**

## As of

| | |
|---|---|
| Audited range | `fb0bc2d1776ad0e3c425c4f98f42f4d2cead7269` → `25d22f864ad68cc447a4cb93aefde918aa4aec9f` |
| Commits in range | 65 (65 attributed commits audited) |
| Rendered | 2026-06-13 |
| Auditor | dos-kernel 0.26.0 |
| Tier | seeded |
| Attribution | agent-attributed commits only (the closed marker set, docs/scoreboard/methodology.md §3); a human commit is never audited here |

## The verdict

| Commits | Checkable | Backed by the diff | Drifted (raw) | Skipped | Raw rate | Final grade |
|---|---|---|---|---|---|---|
| 65 | 49 | 49 | 0 | 16 | 0.0% | **0 of 49 (0.0%)** |

## By kind of claim

| Kind of claim | Backed by the diff | Drifted | Skipped |
|---|---|---|---|
| `fix / add / remove` (code) | 32 | 0 | 0 |
| `tests` | 2 | 0 | 0 |
| `docs` | 15 | 0 | 0 |
| no checkable claim (skipped) | — | — | 16 |

## The receipts — every flag, adjudicated

No flags in range.

## Reproduce it

```bash
git clone https://github.com/JuliusBrussee/caveman.git && cd caveman
git checkout 25d22f864ad68cc447a4cb93aefde918aa4aec9f
pip install dos-kernel
dos commit-audit --sweep --json --workspace . \
    fb0bc2d1776ad0e3c425c4f98f42f4d2cead7269..25d22f864ad68cc447a4cb93aefde918aa4aec9f
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
