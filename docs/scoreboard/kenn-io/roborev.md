# kenn-io/roborev — drift scoreboard

> **CLEAN — 0 confirmed unwitnessed of 96 checkable claims.**
> Schema and grade vocabulary:
> [docs/311](../../311_scoreboard-per-repo-index-plan.md). Drift is a
> claim-vs-diff mismatch — **never** a correctness, honesty, or intent grade.

## As of

| | |
|---|---|
| Audited range | `1aacb414133734f7d9c78014b17b74a8e6b72083` → `473c539e80f5e62a19141a840785786725aa79d7` |
| Commits in range | 150 (150 attributed commits audited) |
| Rendered | 2026-06-13 |
| Auditor | dos-kernel 0.26.0 |
| Tier | seeded |
| Attribution | agent-attributed commits only (the closed marker set, docs/scoreboard/methodology.md §3); a human commit is never audited here |

## The verdict

| Commits | Checkable | Witnessed | Unwitnessed (raw) | Abstained | Raw rate | Adjudicated |
|---|---|---|---|---|---|---|
| 150 | 96 | 96 | 0 | 54 | 0.0% | **0 of 96 (0.0%)** |

## By claim kind

| Kind | Witnessed | Unwitnessed | Abstained |
|---|---|---|---|
| `code_effect` | 87 | 0 | 0 |
| `test` | 7 | 0 | 0 |
| `doc` | 2 | 0 | 0 |
| `none` (no checkable claim) | — | — | 54 |

## The receipts — every flag, adjudicated

No flags in range.

## Reproduce it

```bash
git clone https://github.com/kenn-io/roborev.git && cd roborev
git checkout 473c539e80f5e62a19141a840785786725aa79d7
pip install dos-kernel
dos commit-audit --sweep --json --workspace . \
    1aacb414133734f7d9c78014b17b74a8e6b72083..473c539e80f5e62a19141a840785786725aa79d7
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
