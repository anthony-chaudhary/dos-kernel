# anthony-chaudhary/dos-kernel — drift scoreboard

> **We found 3 drifts — 3 of 129 checkable commit messages claim something the diff doesn't show (2.3%).**
> All three are convention-driven empty re-stamp commits; the receipts are
> below. This is **page #1 of the index, the self-grade**: the scoreboard
> names no other repository before publishing its own verdict, and ours is
> deliberately not airbrushed to zero. A drift is a commit whose subject
> claims something its own diff doesn't show — an empty commit that says
> "fixed it", a "tests pass" that deletes the test. A drift is a
> message-vs-diff mismatch — **never** a correctness, honesty, or intent
> grade. Schema and grade vocabulary:
> [docs/311](../../311_scoreboard-per-repo-index-plan.md).

## As of

| | |
|---|---|
| Audited range | `abe74e880309c98cdb38f3ac295218745ab9efeb` → `4ea6a2aa7d8707b7f6345de08e66d8ceb6719410` |
| Commits in range | 203 (the full visible history since the 2026-06-10 public seed) |
| Rendered | 2026-06-12 |
| Auditor | dos-kernel 0.25.0 at `4ea6a2a` — the range's own end commit, so the auditor and the audited history pin together; includes the [#79](https://github.com/anthony-chaudhary/dos-kernel/issues/79)/[#81](https://github.com/anthony-chaudhary/dos-kernel/issues/81) fire-narrowing fixes |
| Tier | self |
| Attribution | all commits, author-neutral (the self page audits everything; foreign pages audit agent-attributed commits only) |

## The verdict

| Commits | Checkable | Backed by the diff | Drifted (raw) | Skipped | Raw rate | Final grade |
|---|---|---|---|---|---|---|
| 203 | 129 | 126 | 3 | 74 | 2.3% | **3 of 129 (2.3%)** |

Raw and adjudicated agree here: zero of the three flags is an auditor
artifact, so adjudication removed nothing. (Before the #79/#81
fire-narrowing landed in the auditor — `86f437f`, in this same range — the
sweep carried additional artifact fires; that history, hand-adjudicated, is
in the [methodology's false-positive section](../methodology.md).)

## By kind of claim

| Kind of claim | Backed by the diff | Drifted | Skipped |
|---|---|---|---|
| `fix / add / remove` (code) | 25 | 0 | 0 |
| `tests` | 5 | 0 | 0 |
| `docs` | 96 | 3 | 0 |
| no checkable claim (skipped) | — | — | 74 |

## The receipts — every flag, adjudicated

| Commit | Subject | Ruling | Rung | Rationale |
|---|---|---|---|---|
| [`0843842`](https://github.com/anthony-chaudhary/dos-kernel/commit/08438426ebe3eed4e09e78ab34ba4c606fec92eb) | `docs(plans): re-stamp the work-account CLI verb post-renumber (docs/310 P3)` | `CONFIRMED(convention)` | human | A deliberate empty commit: this workspace's re-stamp convention re-anchors a plan phase's ship-stamp after a plan-number collision, so the claim rests on subject text alone **by design**. The auditor is right to count it. |
| [`cc00bf1`](https://github.com/anthony-chaudhary/dos-kernel/commit/cc00bf10ac0adfef6dda6b9f088a1b4e66be91f6) | `docs(plans): re-stamp the severity-gate wiring post-renumber (docs/310 P2)` | `CONFIRMED(convention)` | human | Same convention, same renumber event. |
| [`bf05e27`](https://github.com/anthony-chaudhary/dos-kernel/commit/bf05e276e0d757bc4dd2442713fab8a61dd6ae5e) | `docs(plans): re-stamp the work-kind account leaf post-renumber (docs/310 P1)` | `CONFIRMED(convention)` | human | Same convention, same renumber event. |

All three flags come from one renumber event (the docs/310 plan-number
collision). The convention itself is under design review —
[#80](https://github.com/anthony-chaudhary/dos-kernel/issues/80) proposes
making re-stamps carry a plan-doc line so they witness themselves — and if
that lands, this page's adjudicated count drops to zero the honest way: by
changing the commits, never the auditor.

## Reproduce it

The auditor version is pinned to the same commit the range ends on, so one
checkout gives you both the tool and the history it graded:

```bash
git clone https://github.com/anthony-chaudhary/dos-kernel.git && cd dos-kernel
git checkout 4ea6a2aa7d8707b7f6345de08e66d8ceb6719410
pip install -e .
dos commit-audit --sweep --json --workspace . \
    abe74e880309c98cdb38f3ac295218745ab9efeb..4ea6a2aa7d8707b7f6345de08e66d8ceb6719410
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
