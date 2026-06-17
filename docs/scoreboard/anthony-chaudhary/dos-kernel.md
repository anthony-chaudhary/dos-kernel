# How AI built anthony-chaudhary/dos-kernel

> **5 of 315 AI commit messages here claimed work the commit's own diff doesn't show (1.6%). The rest checked out.**
> All five flags are subject-only doc claims with no contradicting effect —
> four are convention-driven empty re-stamp commits and one is a doc reflow;
> the receipts are below. This is **page #1 of the index, the self-grade**:
> the scoreboard names no other repository before publishing its own verdict,
> and ours is deliberately not airbrushed to zero. The check: an AI agent's
> commit message is just text it wrote — the diff is what git recorded. This
> page reports, for the AI-authored commits, whether each concrete claim in a
> message ("fix X", "add tests for Y") is backed by that commit's own diff. A
> message-vs-diff mismatch is **never** a correctness, honesty, or intent
> grade — only a note that a commit's words and its own diff disagree. Schema
> and the precise definition:
> [docs/311](../../311_scoreboard-per-repo-index-plan.md).

Audited the last 500 commits, ending 2026-06-16.

<details markdown="1">
<summary><strong>Technical provenance &amp; reproduce exactly</strong></summary>

## As of

| | |
|---|---|
| Audited range | `abe74e880309c98cdb38f3ac295218745ab9efeb` → `1ffdaff70a3282d6ad90940438f09b1f1705a44d` |
| Commits in range | 500 (the full visible history since the 2026-06-10 public seed) |
| Rendered | 2026-06-16 |
| Auditor | dos-kernel 0.27.0 at `1ffdaff` — the range's own end commit, so the auditor and the audited history pin together; includes the [#79](https://github.com/anthony-chaudhary/dos-kernel/issues/79)/[#81](https://github.com/anthony-chaudhary/dos-kernel/issues/81) fire-narrowing fixes |
| Tier | self |
| Attribution | all commits, author-neutral (the self page audits everything; foreign pages audit agent-attributed commits only) |

## The verdict

| Commits | Checkable | Backed by the diff | Claimed, not shown (raw) | Skipped | Raw rate | Final grade |
|---|---|---|---|---|---|---|
| 500 | 315 | 310 | 5 | 185 | 1.6% | **5 of 315 (1.6%)** |

Raw and adjudicated agree here: zero of the five flags is an auditor
artifact, so adjudication removed nothing. (Before the #79/#81
fire-narrowing landed in the auditor — `86f437f`, in this same range — the
sweep carried additional artifact fires; that history, hand-adjudicated, is
in the [methodology's false-positive section](../methodology.md).)

## By kind of claim

| Kind of claim | Backed by the diff | Claimed, not shown | Skipped |
|---|---|---|---|
| `fix / add / remove` (code) | 77 | 1 | 0 |
| `tests` | 9 | 0 | 0 |
| `docs` | 224 | 4 | 0 |
| no checkable claim (skipped) | — | — | 185 |

## The receipts — every flag, adjudicated

| Commit | Subject | Ruling | Rung | Rationale |
|---|---|---|---|---|
| [`841d38d`](https://github.com/anthony-chaudhary/dos-kernel/commit/841d38dc11a50581459f82c4ec3b812eeba9b5f8) | `fix(answers): keep the canonical ship-stamp on one line so the lockstep scan passes` | `CONFIRMED(unexplained)` | human | A `fix:` subject whose diff only reflows two doc lines so a literal example subject sits intact on one line — no rendered guidance changed. Not a re-stamp (so not the `convention` class); a true non-effecting doc edit whose claim rests on subject text alone. The auditor is right to count it, and we leave it flagged rather than airbrush it: a doc `fix:` that the diff doesn't strongly witness is exactly the kind of flag this scoreboard is honest about keeping. |
| [`c956d2a`](https://github.com/anthony-chaudhary/dos-kernel/commit/c956d2a84066b1b664d0b4fed4bb9bced22653e8) | `docs(plans): re-stamp under the full slug while docs/317 is contested (docs/317_duplicate-plan-number-disambiguation-plan P1)` | `CONFIRMED(convention)` | human | A deliberate re-stamp under the full plan slug after a docs/317 plan-number collision — the first live firing of the very slug-or-nothing rule that plan shipped. The claim rests on subject text by design; the original ship SHA it points at is the witness. |
| [`0843842`](https://github.com/anthony-chaudhary/dos-kernel/commit/08438426ebe3eed4e09e78ab34ba4c606fec92eb) | `docs(plans): re-stamp the work-account CLI verb post-renumber (docs/310 P3)` | `CONFIRMED(convention)` | human | A deliberate empty commit: this workspace's re-stamp convention re-anchors a plan phase's ship-stamp after a plan-number collision, so the claim rests on subject text alone **by design**. The auditor is right to count it. |
| [`cc00bf1`](https://github.com/anthony-chaudhary/dos-kernel/commit/cc00bf10ac0adfef6dda6b9f088a1b4e66be91f6) | `docs(plans): re-stamp the severity-gate wiring post-renumber (docs/310 P2)` | `CONFIRMED(convention)` | human | Same convention, same renumber event. |
| [`bf05e27`](https://github.com/anthony-chaudhary/dos-kernel/commit/bf05e276e0d757bc4dd2442713fab8a61dd6ae5e) | `docs(plans): re-stamp the work-kind account leaf post-renumber (docs/310 P1)` | `CONFIRMED(convention)` | human | Same convention, same renumber event. |

Four flags come from re-stamp events (the docs/310 and docs/317 plan-number
collisions); the fifth is a one-line doc reflow that changed no rendered
guidance. The re-stamp convention itself is under design review —
[#80](https://github.com/anthony-chaudhary/dos-kernel/issues/80) proposes
making re-stamps carry a plan-doc line so they witness themselves — and if
that lands, those flags drop to zero the honest way: by changing the
commits, never the auditor.

## Reproduce it

The auditor version is pinned to the same commit the range ends on, so one
checkout gives you both the tool and the history it graded:

```bash
git clone https://github.com/anthony-chaudhary/dos-kernel.git && cd dos-kernel
git checkout 1ffdaff70a3282d6ad90940438f09b1f1705a44d
pip install -e .
dos commit-audit --sweep --json --workspace . \
    abe74e880309c98cdb38f3ac295218745ab9efeb..1ffdaff70a3282d6ad90940438f09b1f1705a44d
```

A newer auditor over the same pinned range may count differently as
fire-narrowing continues (each narrowing is a public issue, e.g. #79/#81);
the as-of block above is what this page graded, with what.

</details>

## Corrections

A contested flag gets re-adjudicated and the page re-rendered (the
[docs/311](../../311_scoreboard-per-repo-index-plan.md) §3 path). Until the
`scoreboard-correction` template ships (docs/311 P4), open a plain issue
naming this page and the SHA. Methodology — what the witness reads, what it
abstains on, where it has been wrong:
[docs/scoreboard/methodology.md](../methodology.md).
