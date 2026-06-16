# residual_review — the next-generation diff

> **The idea (operator, 2026-06-16).** A reviewer reads a diff line by line, with
> roughly equal attention. But DOS already knows, for a large share of those
> lines, that the change *did the kind of thing its commit claimed* — the diff
> **witnessed** the claim. Reading those lines to answer "did they do what they
> said" is wasted: the question is already answered, by a party that did not
> write the claim. So spend ~0% there and 100% on the **residual** — the claims
> the kernel could *not* witness. That residual is a new kind of diff. Its unit
> is not "a line changed" but "a claim the machine couldn't confirm."

```
$ python examples/residual_review/residual_review.py origin/master..HEAD

RESIDUAL — your 100% (1)  [a CLAIM the kernel could not witness]
  841d38d  subject-only   fix(answers): keep the canonical ship-stamp …
             └─ code-effect claim but the diff touches no SOURCE file

SEMANTIC (advisory, witnessed but worth a look) (2)
  653689d  fix(pretool): resolve a subagent's ancestor lease …
             ⚠ concurrency / shared-state primitive

CLEARED — ~0 attention (167)  [diff-witnessed; shape confirmed]
  …
```

## Why this is a diff, and a next-generation one

A classic `git diff`'s unit of attention is **a line that changed**. That unit
has a known blind spot: *an unchanged line can still be part of a bug.* The diff
shows you what bytes moved, not where the risk is — it just happens to be the
cheapest proxy we had.

DOS gives a better unit. For every commit, `commit-audit` (docs/214) already
computes whether the commit's **claim** is corroborated by a **non-forgeable
fact** — the file set git itself recorded. That partition is the residual diff's
unit: not "what changed" but **"what the kernel couldn't confirm did what it
claimed."**

The definition of "what to look at" became more flexible than "what bytes
changed." Just as a line you didn't touch can still carry a bug, a change the
machine *witnessed* can still be wrong — so this surface keeps a door open for
that (the advisory band below). But the **default** sort of attention now
follows where verification ran dry, not where the text differs.

## The three bands

| Band | What's in it | Your attention |
|---|---|---|
| **CLEARED** (0) | the claim is witnessed on a **non-forgeable rung**: `diff-witnessed` (the diff corroborates the *kind* of change claimed) or `data-witnessed` (a lockfile/config/template change that IS the claimed effect — the kernel's own ladder, one rung below, docs/214 §1). Both rest on the file set git recorded, not the message | ~0 for "did this do what it said". The item carries its rung, so you can choose to look harder at a `data-witnessed` one. Still reviewable for correctness — but that's a **choice**, not a cost the diff forces on you |
| **RESIDUAL** (1) | a claim the diff did *not* witness: `subject-only`, an unwitnessed code/test claim, a tests-pass claim that net-deleted assertions | **100%.** The only place a human's attention buys something the machine couldn't get |
| **UNVERIFIABLE** (1b) | `ABSTAIN` — the commit made no checkable claim (`wip`/`chore`/most `feat(...)` conventional subjects) | Look, but lower priority: there's no claim-vs-diff gap to concentrate on |
| **SEMANTIC** (2) | *advisory.* a **cleared** commit whose files touch a risk surface (concurrency, auth, money, crypto, deletion) | A second human look is worth more here than on the average cleared hunk — but the kernel found nothing wrong, and this band can never block |

A `data-witnessed` commit is **cleared, not residual**: the kernel *did* witness
it (on the weaker rung), so placing it in the must-read pile would overstate the
residual and contradict "residual = what the kernel could not witness." The
cleared list shows each commit's rung so a reviewer who wants to scrutinize the
data-witnessed ones still can.

The **residual / unverifiable split is load-bearing.** A reviewer's scarce
attention should land first on a claim the machine could not confirm — not on a
`docs(...)` commit that simply made no claim. Folding both into one bucket would
dilute the exact signal the surface exists to concentrate. (See
`test_no_claim_is_unverifiable_not_residual`.)

## The semantic side — advisory by construction

The operator's second ask was to "add back the semantic side." DOS witnesses the
*shape* of a change against its claim; it permanently does **not** witness
correctness (docs/214 §3, Wall 3 — a real fix to the *wrong* bug touches source
and clears here). Band 2 re-adds a correctness-adjacent lens without breaking
that boundary, by obeying two rules:

1. **It runs only over already-CLEARED commits.** It never rescues a residual
   item (that would be the dangerous direction) and never demotes one.
2. **It is one-sided: it can only ask for MORE eyes, never fewer.** A
   fail-to-ABSTAIN lens (the kernel's JUDGE-rung discipline). So it cannot hide a
   real residual, and growing its pattern table is always safe.

This means Band 2 over-asks. On DOS's own history it flagged a `docs(answers)`
commit as "money / billing path" because a *doc about pricing* matched the word
`price`. That is **correct** behavior for an advisory lens — coarse patterns that
err toward asking — and it is why the band is advisory and separate, never mixed
into the witnessed verdict. A real consumer sources the risk-surface table from
its own config; we ship a generic default so the example runs standalone.

## Measured on DOS's own history

Over `HEAD~200..HEAD` (272 commits, 2026-06-16):

```
checkable=170  cleared=169 (99.4%)  residual=1  unverifiable=102  semantic=11
```

A reviewer asking "did each commit do what it claimed" reads **1 commit, not
170.** The kernel cleared the shape of the other 169 against non-forgeable
evidence. The single residual was real and defensible: `fix(answers): …` used a
`fix` verb but touched no source file — a docs-only change under a code claim,
exactly the over-claim `commit-audit` is built to surface.

This is the validating result, the same shape as docs/214's 0.4% drift rate: on
a disciplined repo the residual is **tiny** — which is precisely when the
line-based diff wastes the most reviewer attention, because almost every line it
shows you is already cleared. The surface is the inverse of the
`commit-audit --sweep` rate: the sweep folds a range into one number; this
projects it back onto the commits and sorts them by where review pays.

## Navigating the residual (`--walk`)

A three-band listing answers *where* attention is owed. The operator's next ask
was being able to **navigate through** it. `--walk` turns the residual into a
sequence of self-contained **review cards** — one per unwitnessed commit, each
carrying everything needed to adjudicate that single claim: the subject, the
kernel's reason it could not be witnessed, the files, and the diffstat. You step
through cards instead of scrolling a wall, and the cleared commits never appear —
showing them would defeat the ~0-attention promise.

```
$ python examples/residual_review/residual_review.py 841d38d~1..841d38d --walk

┌─ [1/1]  841d38d  (subject-only)
│  fix(answers): keep the canonical ship-stamp on one line so the lockstep scan passes
│  why residual: code-effect claim but the diff touches no SOURCE file
│                 (only: docs/answers/how-to-verify-...md) — the claim rests on the subject text
│    docs/answers/how-to-verify-an-ai-agent-actually-did-the-work.md | 4 ++--
│     1 file changed, 2 insertions(+), 2 deletions(-)
└──────────────────────────────────────────────────
```

The card makes the kernel's verdict legible at a glance: the subject *claims a
fix*, the diffstat *proves it touched one doc file* — the exact claim-vs-diff gap,
shown as the unit you navigate. This is a static render (every TUI is downstream
of the same data); a host wires the same `ReviewPlan` to a pager, an editor's
quickfix list, or a PR-comment thread. `--json` carries the bands for any such UI.

## The bottleneck this addresses

Code review is the classic throughput wall: a change isn't merged until a human
reads it, and that human is the scarce resource. The line-based diff makes the
wall worse than it needs to be — it spends the reviewer's fixed attention budget
*evenly* across every changed line, including the lines a non-forgeable check
already cleared. As machines start producing commits faster than humans can read
them, "read all of it deeply" has already failed; the only question left is
*which* commits deserve the human.

Residual review attacks the bottleneck at its operand. It doesn't make the human
read faster or replace them with a model's guess — it **shrinks what the human
must read** to the residual, and proves the shrink with git rather than a
confidence score. The cleared rate *is* the throughput multiplier: on the DOS
history above, the "did each commit do what it claimed" pass goes from 168 commits
to 1. The reviewer's deep-correctness pass is untouched and still applies to every
commit — but the cheap, mechanical "did-it-match" sub-task, the one that dilutes
attention most when volume is high, is answered before the human opens the branch.

## Soundness — zero new trust

Bands CLEARED, RESIDUAL, and UNVERIFIABLE carry **no new trust**. They are a pure
re-projection of the shipped `commit_audit.audit_range` verdict — the same
`diff-witnessed` / `subject-only` rung the reactive tool already computes, sorted
by commit instead of folded by rate. The residual cannot invent a claim the
kernel didn't already produce, nor hide one it did
(`test_projection_equals_the_shipped_verdict`, run against real git history).
Band 2 is the only judgment, and it is advisory and one-sided.

## The seam this exposes (honest limitations)

- **Conventional `feat(...)` subjects ABSTAIN.** `commit-audit`'s verb taxonomy
  fires on bare code verbs (`fix`/`add`/`implement`), so a `feat(pulse): …`
  subject reads as no-claim and lands in UNVERIFIABLE, not RESIDUAL. That is a
  property of the underlying oracle (deliberately tight, to avoid false fires),
  not of this projection. The honest read: the residual is conservative — it
  under-fires before it over-fires.
- **Per-commit, not per-line.** This surface sorts attention at commit
  granularity, because that's the unit `commit-audit` witnesses. A finer
  per-hunk residual would need a hunk-level witness rung, which the kernel does
  not have today — a real follow-on, not a fix.
- **Correctness stays out of scope, permanently.** For correctness you need a
  test/goal witness (`verify`'s artifact+test rung, a CI run), not a claim-vs-diff
  grade. Band 2 points a human at where to apply that judgment; it does not
  substitute for it.

## Run it

```bash
python examples/residual_review/residual_review.py origin/master..HEAD
python examples/residual_review/residual_review.py --json HEAD~20..HEAD
python examples/residual_review/residual_review.py            # default: HEAD~20..HEAD
```

Exit code mirrors `commit-audit`'s CI convention: **non-zero iff there is a
residual a human must read.** A fully-cleared range exits 0 — drop it in CI to
make "the residual is empty" a gate.

## Related

- **docs/214** — `commit-audit`: the author-neutral claim-vs-diff floor this
  stands on (and its `--sweep` drift rate, which this inverts).
- **docs/192 / docs/349** — the witness ladder (W2→W3); `diff-witnessed` is the
  rung that clears Band 0.
- **docs/118** — the forgeability rung (`subject-only` vs `diff-witnessed`).
- **examples/plan_price** — the other forward-looking projection of a shipped
  kernel verdict (the predictive flip, docs/347).
