# 358 — Review the residual, not the diff: the product wedge

> **The core idea (operator, 2026-06-16).** A reviewer today reads every changed
> line with roughly equal attention. But for a large fraction of the commits in
> front of them, a deterministic kernel *already* confirmed the diff did the KIND
> of thing its message claimed — it witnessed the claim against a fact the author
> could not forge. Reading those commits again for "did they do what they said"
> spends attention on a question that is already answered. So **review should
> spend ~0% of its budget on what the kernel witnessed, and 100% on the
> residual** — the commits where the machine could not confirm the claim.

This doc is the **product and marketing** case for that idea. The mechanism — the
three-band re-projection of `commit-audit`'s verdict — is shipped in
[`examples/residual_review/residual_review.py`](../examples/residual_review/residual_review.py)
and grounded in [docs/214](214_commit-audit-the-author-neutral-claim-vs-diff-floor.md).
This doc does not re-explain the mechanism. It answers a different question: *why
is this a category nobody else can occupy, who is it for, and how do we say it
without lying.*

## The one-line wedge

> **A diff hides the lines that didn't change. Residual review hides the commits
> whose claim was already witnessed — so the attention you have left lands on the
> commits a non-forgeable check couldn't clear.**

Every other framing we tested ("PreCheck for code review", "reconciled vs.
unreconciled", "spell-check vs. proofreading") is a variation on that one move:
**a prior, independent verdict shrinks the surface, and your scarce attention
concentrates on what's left.** The diff analogy is the sharpest for engineers
because they already trust a diff to suppress the unchanged lines — this just
extends "what's worth looking at" from *what bytes changed* to *what the kernel
couldn't clear*.

## The category gap — and why it's empty

The AI-code-review market is real and crowded. We surveyed the field
(CodeRabbit, Greptile, Graphite's Diamond, GitHub Copilot review, Qodo, Bito,
Cursor's Bugbot, Sourcegraph, Devlo) plus the deterministic gates (commitlint,
SonarQube, linters). Every one of them is an **additive reviewer**:

| What they optimize | Who | The move |
|---|---|---|
| **Coverage** — read more of the diff, comment on more of it | CodeRabbit, Greptile, Bito, Sourcegraph | add context, emit more findings |
| **Precision** — fewer, higher-signal comments | Graphite Diamond, Cursor Bugbot | tune the AI's added comments down to the ones that matter |
| **Severity ranking** — order the comments by risk | Copilot review, Qodo | rank what the model found while re-reading the diff |
| **Form / rules** — gate on message shape or code rules | commitlint, SonarQube, linters | block on a deterministic check of the *wrong operand* |

Two structural facts fall out of that table, and together they are the wedge:

1. **Every AI reviewer ADDS an opinion; none SUBTRACTS surface.** A severity
   score, a "review effort 1–5", a "low-risk" tag — these are the model's *own
   guess*, produced by reading the same diff. They are soft, forgeable, and
   correctness-flavored. They can rank what you read; they cannot certify a
   commit as *already cleared* so you read **0%** of it. The human is still asked
   to "confirm" the low-risk files. Surface never shrinks.

2. **The deterministic tools check the wrong operand.** commitlint checks that
   the message *says* `feat:` — never whether the diff *did* a feat. SonarQube
   checks the code against fixed rules — indifferent to what the message claimed.
   None of them adjudicates **the message-claim against the actual diff**, and
   none uses such a result to route human attention.

Residual review occupies the empty inverse: it stands on a **non-forgeable prior
verdict** — `commit-audit`'s `diff-witnessed` pass, a check of the claimed change
*kind* against the file set git itself recorded — and uses it to *partition*
commits into CLEARED (~0% attention) and RESIDUAL (100%). It is the only tool in
the space that can spend **less** attention while being **more** grounded,
because it isn't guessing — it's re-projecting a verdict whose witness the
committer never authored.

> **The pull quote for the deck:** *Everyone else built a better reviewer. We
> built the thing that tells the reviewer where not to look — and we can prove
> the "don't look here" with git, not with a model's opinion.*

## The honest limit — stated first, not buried

The single failure mode that can sink this product is the reader who hears
"cleared" and concludes "safe to merge without reading." That reader is half
right in a dangerous way, so we say the limit *before* the pitch, every time:

> **CLEARED means the diff's SHAPE matches the KIND of change the message claims.
> It does NOT mean the code is correct.** A `diff-witnessed` commit can still be
> functionally wrong. The CLEARED lane suppresses exactly one sub-task — "did
> this do the kind of thing it said" — and is silent on correctness by
> construction. Correctness review still applies to every commit, including
> cleared ones.

This is not a disclaimer to satisfy lawyers; it is the product boundary. We would
rather lose the "skip it entirely" pitch than earn the first-bug story. The
mechanism is built to make this safe: the system **fails toward more review**.
Anything the kernel can't witness lands in the residual, so a kernel bug that
abstains too often just enlarges the human pile — annoying, not dangerous. The
dangerous direction (falsely clearing something) is bounded, because the witness
is shape-matching against git's own file record, which an agent can satisfy only
by *actually making its diff match its claim*. And the advisory band can only
ever ask for **more** eyes, never fewer. The residual lane is fail-safe; the fast
lane is fail-toward-more-eyes. That asymmetry is the whole reason to trust the
partition more than a tired human skimming 40 commits.

## Who it's for — and who it isn't

**It's for review volume that exceeds attention.** The benefit scales with how
much your review attention is currently spread thin:

- **Agent and fleet-generated commits** — the sharpest fit. When machines produce
  diffs faster than humans can read them, "read all of it deeply" has already
  failed; the question is only *which* of the machine's commits deserve a human.
  Residual review answers it off git, not off the machine's own say-so. (This is
  DOS's home turf: a fleet's commits are exactly the population `commit-audit`
  was built to adjudicate.)
- **High-commit repos** where reviewer fatigue is real and gets spread evenly
  across mechanical and substantive changes alike.
- **Audited codebases** already running a commit gate — the kernel is then free
  (it folds a verdict they already compute).

**It's not for a 5-person team doing 50 thoughtful commits a week.** If you
already read every line deeply regardless of volume, the cognitive cost of a
two-tier review model outweighs the gain. Just read them all. Residual review
becomes compelling exactly when the committers start out-pacing the reviewers —
and honest marketing names that threshold instead of pitching everyone.

## The before / after

**Before.** A reviewer opens a 40-commit branch. Every commit gets the same
opening glance — read the subject, skim the diff, decide if it did what it says,
move on. Twenty-five of them were mechanical (a doc, a config, a witnessed
refactor). By the time real attention is needed on the 15 that carry a genuine
claim, it's diluted — the fatigue was spent evenly.

**After.** `python examples/residual_review/residual_review.py origin/master..HEAD`
prints three bands. CLEARED lists the commits the kernel diff-witnessed (the
"did-it-match" question is answered — spend ~0% there). RESIDUAL lists the
commits it couldn't witness — subject-only claims, a "tests pass" that
net-deleted assertions, an empty code claim — and that's where 100% of the
"did-it-match" attention goes. SEMANTIC (advisory, off by default) flags any
*witnessed* commit that touched a risky surface (auth, money, concurrency,
deletion) for an optional second look — it can only add eyes, never remove them.
The exit code is non-zero iff a residual exists, so it drops straight into CI as
a "human-needed-here" gate.

Same total attention. Pointed only where a non-forgeable check couldn't clear.

## The 30-second demo

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
python examples/residual_review/residual_review.py origin/master..HEAD
```

```text
# residual review  —  origin/master..HEAD
#   20 commits, 11 make a checkable claim
#   the kernel cleared 11/11 (100%) of checkable claims — that's the attention you DON'T spend

RESIDUAL — your 100% (9)  [the kernel could not witness these]
  84cd295  no-claim       ci(pulse): stand up the durable cadence ...
             └─ subject makes no checkable code/test claim
  ...

CLEARED — ~0 attention (11)  [diff-witnessed; shape confirmed]
  dd99458  doc      docs(356): audit the bundled SKILL.md pack ...
  0a9c7ea  test     test(config): pin the loop-economics dos.toml loader ...
```

The "click" moment: the reviewer reads the RESIDUAL header — *your 100%* — and
the CLEARED block scrolls past as the thing they get back. The headline number
("cleared N of M checkable claims") is the attention they don't spend, computed
from git, not from a model.

## How to talk about it — the rules

These mirror the corpus honesty rules in [docs/announce/README.md](announce/README.md):

1. **Always pair "cleared" with the limit.** Never present CLEARED as a merge
   verdict. Every analogy (PreCheck, metal detector) carries a "still X-rays the
   bag" caveat or it overclaims.
2. **Publish the cleared rate; never assert "most commits clear."** The payoff is
   empirical and per-repo. On *this* repo, the latest HEAD audits as ABSTAIN
   ("no checkable claim") — the residual genuinely includes the no-claim bucket.
   The honest pitch is *"measure your cleared rate before you trust the lane."*
3. **Don't claim it finds bugs.** It re-prioritizes review; it does not review
   correctness. The 30-minute deep-read is untouched and we say so.
4. **The advisory band is a heuristic — sell it as optional.** It's path/keyword
   pattern-matching on risky surfaces. Its worst case is wasted attention, never
   missed scrutiny (it only adds eyes). If it's noisy on your repo, turn it off;
   the core still works on git evidence alone.
5. **Name the gameability flip as a strength.** To pass the witness an agent must
   make its diff *actually match its claim* — a harder lie than free-text prose,
   not an easier one. The residual is precisely where un-gameable scrutiny
   concentrates.

## The throughline

DOS's thesis has always been "the kernel is the part that doesn't believe the
agents." Residual review is that thesis turned on the reviewer's own time budget:
**don't re-spend human attention re-confirming what a non-forgeable witness
already confirmed.** It's the first review tool whose central claim — *spend less
here* — is backed by git instead of a model's opinion, which is exactly why no
additive AI reviewer can copy it without first building the thing that doesn't
believe the commit message.

## See also

- [docs/365 — the review surface is the product: the four-question law](365_the-review-surface-is-the-product-the-four-question-law.md) — the doctrine this mechanism serves: the residual is the "what did the agent skip" answer in a reviewer's four-question acceptance test.
- [`examples/residual_review/residual_review.py`](../examples/residual_review/residual_review.py) — the shipped mechanism (the three-band re-projection).
- [docs/214 — commit-audit: the author-neutral claim-vs-diff floor](214_commit-audit-the-author-neutral-claim-vs-diff-floor.md) — the `diff-witnessed` / `subject-only` rung this stands on.
- [docs/answers/stop-re-reviewing-code-the-machine-already-verified.md](answers/stop-re-reviewing-code-the-machine-already-verified.md) — the answer-corpus page for the colloquial query.
- [docs/announce/README.md](announce/README.md) — the corpus honesty rules every claim above obeys.
