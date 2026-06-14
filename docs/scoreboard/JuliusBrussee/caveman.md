# JuliusBrussee/caveman — drift scoreboard

> **CLEAN — 0 confirmed unwitnessed of 49 checkable claims.**
> Schema and grade vocabulary:
> [docs/311](../../311_scoreboard-per-repo-index-plan.md). Drift is a
> claim-vs-diff mismatch — **never** a correctness, honesty, or intent grade.

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

| Commits | Checkable | Witnessed | Unwitnessed (raw) | Abstained | Raw rate | Adjudicated |
|---|---|---|---|---|---|---|
| 65 | 49 | 49 | 0 | 16 | 0.0% | **0 of 49 (0.0%)** |

## By claim kind

| Kind | Witnessed | Unwitnessed | Abstained |
|---|---|---|---|
| `code_effect` | 32 | 0 | 0 |
| `test` | 2 | 0 | 0 |
| `doc` | 15 | 0 | 0 |
| `none` (no checkable claim) | — | — | 16 |

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
