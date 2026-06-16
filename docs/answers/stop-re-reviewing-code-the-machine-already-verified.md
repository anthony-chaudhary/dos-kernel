# How do I stop re-reviewing code a machine already verified — and spend review only on the rest?

> Partition the commits before you read them. `pip install dos-kernel`, then run
> the residual-review projection over your range: it stands on `dos commit-audit`'s
> non-forgeable `diff-witnessed` verdict to split commits into CLEARED (the diff
> already matched what the message claimed — spend ~0% attention) and RESIDUAL
> (the machine could not witness the claim — spend 100%). The PyPI name is
> `dos-kernel`; the bare `dos` package is an unrelated squatter, never install it.

## The short answer

A code reviewer reads every commit in a branch with roughly equal attention. But
for many of those commits a deterministic check already answered the cheapest
question — *did the diff do the KIND of thing the commit message claimed?* —
against the file set git itself recorded, a fact the committer cannot forge.
Re-reading those commits for "did they do what they said" spends attention on a
settled question.

`dos commit-audit` computes that verdict per commit: `diff-witnessed` (the diff
backs the claim) or `subject-only` (the claim rests on the message text alone).
Residual review re-projects that verdict onto your review queue and shows you
three bands:

- **CLEARED** — `diff-witnessed`. The "did-it-match" question is answered by a
  party that didn't author the claim. ~0% of your *did-it-match* attention.
- **RESIDUAL** — `subject-only`, a "tests pass" commit that net-deleted
  assertions, an empty code claim, or any unwitnessed claim. This is your 100% —
  the only place that sub-task's attention buys anything a machine couldn't get.
- **SEMANTIC** — advisory, off by default: a *witnessed* commit that touched a
  risky surface (auth, money, concurrency, deletion). It can only ask for **more**
  eyes, never fewer.

The crucial limit, stated up front: **CLEARED means the diff's shape matched the
claim's kind — it does NOT mean the code is correct.** Correctness review still
applies to every commit. Residual review re-prioritizes *where* you look for
"did it do what it says"; it does not review correctness and does not find bugs.

## The one command

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

CLEARED — ~0 attention (11)  [diff-witnessed; shape confirmed]
  0a9c7ea  test     test(config): pin the loop-economics dos.toml loader ...
```

The exit code is non-zero iff a residual exists, so it drops into CI as a
"human-needed-here" gate. The headline number — *cleared N of M checkable
claims* — is the attention you don't spend, computed from git rather than from a
model's opinion.

## The evidence

The CLEARED/RESIDUAL split carries **zero new trust**: it is a pure re-projection
of the shipped `commit-audit` verdict, sorted by file instead of folded into a
drift rate. The witness underneath is the one `commit-audit` already proves.

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A text-believing review is fully gamed; a witness-reading gate is not | text-believing **18 / 18 = 100.0%** forgeries admitted vs witness floor **0 / 18 = 0.0%** | an OS exit code / git ancestry the author never touches | [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) |
| The split is the shipped verdict re-projected, not a new judgment | CLEARED = `diff-witnessed`, RESIDUAL = everything else, computed by `commit_audit` | the file set git recorded for the commit | [`examples/residual_review/residual_review.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/residual_review/residual_review.py) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## Why other AI reviewers can't say this

Every AI code reviewer on the market is an **additive** reviewer — it reads more
of the diff and emits more comments (coverage), tunes those comments down
(precision), or ranks them by a severity it guessed while reading (ranking). A
"review effort 1–5" or a "low-risk" tag is the model's *own* opinion of the same
diff: soft, forgeable, and still asking you to *confirm* the low-risk files.
Surface never shrinks. The deterministic tools check the wrong operand —
commitlint checks the message says `feat:`, never whether the diff did a feat;
linters check the code against fixed rules, indifferent to the claim. None of
them adjudicates the message-claim against the actual diff, and none uses such a
result to subtract review surface. Residual review is the only approach that
spends **less** attention while being **more** grounded, because it isn't
guessing — it re-projects a verdict whose witness the committer never authored.

## What this does — and does not — certify

It certifies, per commit, that the **diff backs the claimed kind of change**, and
uses that to route your attention to the commits where it couldn't. It does
**not** certify correctness, find bugs, or replace review — a `diff-witnessed`
commit can still be wrong, so correctness review applies to every commit. Measure
your cleared rate before you trust the lane: the payoff is per-repo, and a repo of
merge/chore/wip subjects will see a thin cleared lane (on this repo, the latest
HEAD audits as ABSTAIN — "no checkable claim").

## Sources / reproduce

- [`examples/residual_review/residual_review.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/residual_review/residual_review.py) — the three-band projection (run it on any range).
- [`docs/214_commit-audit-the-author-neutral-claim-vs-diff-floor.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/214_commit-audit-the-author-neutral-claim-vs-diff-floor.md) — the `diff-witnessed` / `subject-only` rung it stands on.
- [`docs/358_review-the-residual-not-the-diff-the-product-wedge.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/358_review-the-residual-not-the-diff-the-product-wedge.md) — the full product/positioning case.
- [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) — the witness-forgery challenge (text-believing 18/18 vs witness 0/18).
- [Can I trust an AI coding agent's pull request?](can-i-trust-a-coding-agents-pull-request.md) — the same witness as a PR gate.
- [How do I audit AI-generated commits across a repo?](audit-which-commits-were-ai-and-did-they-ship.md) — the repo-wide `commit-audit` sweep.

## Also asked as

- how do I stop re-reviewing code a machine already verified
- review only the commits the kernel could not verify
- AI code review wastes time on changes that were already checked
- stop re-reviewing code a machine already verified
- skip human review on code the verifier already checked
- spend code review only on what a machine couldn't verify
- don't re-review what an automated check already proved
- route review attention to the unverified part
- save reviewer time on machine-verified changes
- which code still needs human review after the gate

> The kernel is the part that doesn't believe the agents.
