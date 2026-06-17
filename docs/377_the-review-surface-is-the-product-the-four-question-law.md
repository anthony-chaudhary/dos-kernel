# 377 — The review surface is the product: the four-question law

> **The idea (operator, 2026-06-16).** "If I can't quickly answer *what files changed, why,
> what command proved it, and what did the agent skip?* then the tool feels worse even if the
> underlying model is smarter. Long thinking traces are useful for debugging a failed run, but
> terrible as the main UI when you're trying to review a diff."

This is a vision note in the family of [340](340_what-dos-means-the-winning-move-when-narration-dies.md),
[344](344_the-dark-fleet-coordination-when-no-one-is-reading.md), and
[362](362_where-dos-is-most-valuable-when-swe-is-programming-agents.md). It ships no mechanism
and carries no litmus. Every surface it cites is already shipped; the note's job is to state a
**product law** the corpus has not said out loud, and to wire each of the operator's four
questions to the DOS surface that already answers it.

## The law, in one line

A coding tool's value is capped by **how fast a human can answer four questions about what the
agent did** — *what changed · why · what command proved it · what did it skip.* The model
writes the code; the human still has to accept it. So the real product is not the model. It is
the **review surface** — the thing that turns a turn's work into those four answers.

## Why a smarter model can make the tool worse

This is the part that feels backwards, so say it plainly. There are two clocks running:

- **Model capability** — is the answer right? Rising fast.
- **Review cost** — how long does it take a human to be *sure* the answer is right? Fixed by
  human attention, which does not rise at all.

A better model moves the bottleneck off the first clock and onto the second. And it makes the
second clock *worse* in two specific ways, both of which come *with* the capability, not
against it:

1. **It touches more files per turn.** A stronger agent does more in one shot — so the diff a
   human must accept is bigger, not smaller.
2. **It emits a longer reasoning trace.** A stronger agent thinks more visibly — so the
   transcript a human might read to understand the turn is longer, not shorter.

Both *enlarge* the review surface while the attention budget stays flat. So capability and
reviewability pull in opposite directions unless someone engineers the surface. When they do
pull apart, the lived experience is exactly the operator's: the model got smarter and the tool
got worse, because "is the answer right" stopped being the slow step and "can I review the
answer" became it. **Improving the model without improving the review surface is spending on
the clock that was already fast.**

## The four questions, each pinned to a shipped surface

DOS already answers all four — and answers them off **non-forgeable** evidence (git, the file
set, the verdict), never the agent's narration. That is the whole point: a review surface built
on the agent's own account of what it did is a self-report wearing the authority of a fact. The
four questions are the acceptance test for a review surface; here is the surface that passes it.

| The question | What answers it | The surface (all shipped) |
|---|---|---|
| **What files changed?** | the diff, plus the file set git itself recorded | `git show --stat`; `commit-audit`'s `source_files` / `test_files` — the files the verdict was computed against, not a list the agent typed |
| **Why?** | the *kind* of change the commit claims | `commit-audit`'s `claim_kind` (`fix` / `add` / `test` / …), read off the subject and checked against the diff ([docs/214](214_commit-audit-the-author-neutral-claim-vs-diff-floor.md)) |
| **What command proved it?** | the truth syscall — git ancestry + the ship-stamp grammar, never a "tests pass" line | `dos verify PLAN PHASE` (no plan needed; it answers from artifacts) |
| **What did the agent skip?** | the **residual** — the claims the kernel could *not* witness | `examples/residual_review/` — the `subject-only` / `CLAIM_UNWITNESSED` band ([docs/358](358_review-the-residual-not-the-diff-the-product-wedge.md)) |

The first three are familiar; most review tools gesture at them. The fourth is the one only a
**distrust kernel** can answer, and it is the sharpest of the four. "What did the agent skip" is
not something the agent volunteers — a worker that skipped the assertions does not narrate "I
skipped the assertions." You can only find the skip by holding the agent's claim up against a
fact it did not author and seeing where they fail to meet. That gap *is* the residual: a `fix:`
that touched no source, a "tests pass" that net-deleted the assertions, an `--allow-empty
"shipped"`. The residual surface lists exactly those and nothing else, so the question stops
being "read all 40 commits and hope you spot the skip" and becomes "read the 1 the kernel
couldn't clear."

## The anti-pattern: the thinking trace as the front door

The operator's second sentence names the trap, and it is worth stating as its own rule.

A long reasoning trace is the **right** artifact for **debugging a failed run.** When something
broke, you want the whole chain — every step, every tool call, every dead end — because the bug
is *in* the chain and you are hunting it.

It is the **wrong** front door for **reviewing a passing diff.** Three reasons, each fatal on
its own:

- **It is a self-report.** The trace is the agent's account of its own thinking. Reviewing the
  trace to decide whether to accept the diff is consistency-checking the agent against itself —
  the exact move DOS exists to refuse. (For the narrow per-output version of this — an output
  that is *itself* a leaked trace rather than an answer — the `dos answer-shape` verb returns a
  structural verdict. The law here is the general case: even a *correct* trace is the wrong
  thing to put first.)
- **It is unbounded.** A trace's length tracks how hard the model thought, not how much the
  human needs to read. As models think more, the trace grows without limit; the four answers do
  not.
- **It is orthogonal to the four questions.** A 5,000-character trace can fail to answer a
  single one of *what changed / why / what proved it / what got skipped* — it narrates the
  journey, not the verdict.

So the default review surface should be the four answers. The trace is **one click down** — the
thing you open for the runs that failed, not the thing you open to accept the runs that passed.
A tool that makes the trace the front door has confused the debug artifact for the review UI,
and it will feel worse with every capability bump, because every bump makes the trace longer.

## The honest limit

The same boundary as [358](358_review-the-residual-not-the-diff-the-product-wedge.md) and
[362](362_where-dos-is-most-valuable-when-swe-is-programming-agents.md), stated before the
pitch, not after: these surfaces witness **shape and effect**, never **correctness.** The four
questions make review *fast and grounded* — they collapse the cheap, mechanical "did it do the
kind of thing it said" pass from forty commits to the residual. They do **not** replace the deep
correctness read. A commit can answer all four questions cleanly and still be the wrong change,
correctly executed. "What did it skip" surfaces a claim the machine could not witness; it does
not certify that the witnessed claims are *right*. The law is about where the human's attention
*starts* and how fast it gets oriented — not about removing the human.

## The throughline

DOS's thesis is "the kernel is the part that doesn't believe the agents." This note turns that
thesis on the **review surface itself.** As the model gets smarter, the scarce thing is no
longer the answer — it is the human's ability to accept the answer quickly and on evidence. A
review surface that answers *what changed, why, what proved it, and what got skipped* off
non-forgeable facts is the product. The thinking trace is the debugger. Confusing the two is how
a smarter model ships a worse tool.

## See also

- [docs/358](358_review-the-residual-not-the-diff-the-product-wedge.md) — the residual is the
  "what did it skip" answer: review what the kernel could not witness, not every changed line.
- [docs/362 §1](362_where-dos-is-most-valuable-when-swe-is-programming-agents.md) — the
  labor-economics sibling of this UX law: as code-production goes free, attention-allocation
  between mechanical witness and correctness judgment is the scarce input. This note is that
  same split seen from the reviewer's screen instead of the value stack.
- [docs/214](214_commit-audit-the-author-neutral-claim-vs-diff-floor.md) — `commit-audit`: the
  claim-vs-diff floor that answers *why* and grounds *what changed*.
- [`examples/residual_review/`](../examples/residual_review/README.md) — the shipped three-band
  surface; the residual band is the fourth question made navigable.
