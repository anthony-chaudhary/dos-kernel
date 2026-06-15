# kenn-io/roborev — drift scoreboard

> **Clean — every one of 96 checkable commit messages matched its diff. 0 drifts.**
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
| Audited range | `1aacb414133734f7d9c78014b17b74a8e6b72083` → `473c539e80f5e62a19141a840785786725aa79d7` |
| Commits in range | 150 (150 attributed commits audited) |
| Rendered | 2026-06-13 |
| Auditor | dos-kernel 0.26.0 |
| Tier | seeded |
| Attribution | agent-attributed commits only (the closed marker set, docs/scoreboard/methodology.md §3); a human commit is never audited here |

## The verdict

| Commits | Checkable | Backed by the diff | Drifted (raw) | Skipped | Raw rate | Final grade |
|---|---|---|---|---|---|---|
| 150 | 96 | 96 | 0 | 54 | 0.0% | **0 of 96 (0.0%)** |

## By kind of claim

| Kind of claim | Backed by the diff | Drifted | Skipped |
|---|---|---|---|
| `fix / add / remove` (code) | 87 | 0 | 0 |
| `tests` | 7 | 0 | 0 |
| `docs` | 2 | 0 | 0 |
| no checkable claim (skipped) | — | — | 54 |

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
