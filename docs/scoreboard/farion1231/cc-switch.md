# farion1231/cc-switch — drift scoreboard

> **Clean — every one of 30 checkable commit messages matched its diff. 0 drifts.**
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
| Audited range | `e0a9c1ab4c46ecadf665dfb31dd967ce6f0019ac` → `11572b1337999a53eb6be6e657e2b17fd72f49d3` |
| Commits in range | 41 (41 attributed commits audited) |
| Rendered | 2026-06-13 |
| Auditor | dos-kernel 0.26.0 |
| Tier | seeded |
| Attribution | agent-attributed commits only (the closed marker set, docs/scoreboard/methodology.md §3); a human commit is never audited here |

## The verdict

| Commits | Checkable | Backed by the diff | Drifted (raw) | Skipped | Raw rate | Final grade |
|---|---|---|---|---|---|---|
| 41 | 30 | 30 | 0 | 11 | 0.0% | **0 of 30 (0.0%)** |

## By kind of claim

| Kind of claim | Backed by the diff | Drifted | Skipped |
|---|---|---|---|
| `fix / add / remove` (code) | 29 | 0 | 0 |
| `docs` | 1 | 0 | 0 |
| no checkable claim (skipped) | — | — | 11 |

## The receipts — every flag, adjudicated

No flags in range.

## Reproduce it

```bash
git clone https://github.com/farion1231/cc-switch.git && cd cc-switch
git checkout 11572b1337999a53eb6be6e657e2b17fd72f49d3
pip install dos-kernel
dos commit-audit --sweep --json --workspace . \
    e0a9c1ab4c46ecadf665dfb31dd967ce6f0019ac..11572b1337999a53eb6be6e657e2b17fd72f49d3
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
