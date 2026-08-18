# 393 - Where the claim grammar collapses on a foreign repo: the overfit probe's findings

> **Status:** WORKED SLICE for
> [issue #199](https://github.com/anthony-chaudhary/dos-kernel/issues/199).
> The probe script and its committed report landed earlier
> ([docs/scoreboard/external-repo-conformance.md](scoreboard/external-repo-conformance.md)).
> This slice adds the measurement that report could not make — splitting
> "the subject declares no claim" from "the grammar could not read the claim" —
> and ships the smallest default-grammar widening the split motivates.

## The question

DOS grew up on one unusually disciplined repo. The risk issue #199 names: the
kernel's grammars may be tuned to the workflow that generated them. The honest
test is a foreign repo — Conventional Commits, no stamp grammar, operators who
did not write the kernel. "Run it on your own repo" is the adoption moment; if
every verdict reads `NOT_SHIPPED (via none)` and every third commit abstains,
the new adopter reads "DOS is broken", not "my commits carry no witness".

## What the probe already showed

The committed offline report folds the 19 published scoreboard sweeps
(2,834 audited commits):

- 61.4% of commits made a checkable claim; 99.7% of those were backed by
  their own diff. The claim-vs-diff rung travels well.
- 38.6% abstained — `commit-audit` found no checkable claim in the subject.
- 0 of 19 repos had a stamp-verifiable commit. The stamp rung does not travel
  at all without opt-in (`[stamp]` in `dos.toml`, docs/289).

The wall is that 38.6%. Before this slice it was one bucket, and one bucket
cannot answer the overfit question: is the subject *genuinely claim-free*
(`chore: bump deps` — nothing to check, abstaining is correct), or did the
grammar *fail to read a real claim* (`feat: new authentication module` — a
concrete code claim in Conventional-Commits form)? The first is honest
abstention. The second is DOS-overfit.

## The split this slice adds

`ClaimKind.NONE` has two structurally different roads in:

| road | meaning | overfit? |
|---|---|---|
| declared no-claim | the subject hits an explicit no-claim marker (`wip`, `chore`, `merge`, `bump`, …) | no — abstaining is the correct verdict |
| grammar-unparsed | the subject fell through every claim rung unread | yes — the claim may be real and the grammar missed it |

`commit_audit.noclaim_declared()` now exposes the marker predicate (it was
always the first check inside `classify_claim`; it is now callable on its
own), and the probe's live path classifies every subject in the audited range,
splitting the NONE bucket into `none_declared` vs `none_unparsed` per repo.
A repo where unparsed dominates gets the `claim-grammar-unparsed` failure
mode — the named DOS-overfit signal.

The committed offline sweeps cannot carry the split (they hold counts, not
subjects), so the report shows it only for live-probed clones; the reproduce
section documents the live run.

## The smallest widening the split motivates

Measured on this repo (live probe, `HEAD~200..HEAD`), the unparsed bucket was
dominated by one shape: a Conventional-Commits type token with no English verb
after the colon. `feat: new authentication module` and
`perf(io): faster buffer reuse` claim concrete code effects, but neither
`feat` nor `perf` was in `_CODE_VERBS`, so both fell through to NONE.

The shipped fix adds exactly those two type tokens to `_CODE_VERBS`. The
widening is monotone-safe: the no-claim guard fires first, so
`feat: bump version` / `feat: wip auth` still abstain; and a `feat:` claim
over an empty or doc-only diff now grades CLAIM_UNWITNESSED instead of
silently abstaining — the auditor sees *more*, never less. A sweep whose
abstain rate stays above 40% now prints a hint naming the two roads, so the
new adopter learns *why* commits abstain instead of reading a bare rate.

## What stays honest

- The stamp rung stays opt-in. Zero stamp-verifiable commits on a foreign
  repo is an evidence horizon, not a defect: no commit names a unit of work,
  so there is nothing for `verify` to bind. The fix for that is declared
  grammar (`[stamp]`, docs/289), never a looser oracle.
- The split is measurement, not a verdict change. `classify_claim`'s answers
  moved only where `feat`/`perf` lead a subject.
- The corpus-scale live split (all 19 repos, full clones) remains open — it
  needs the clone cache and rides the same rails as issue #98. This slice
  ships the instrument and the first measured repo.
