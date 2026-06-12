# 214 — commit-audit: the author-neutral claim-vs-diff floor

> **One sentence.** A commit's message is written by whoever typed it (forgeable);
> the files the commit touched are written by git itself (not forgeable) — so you
> can check whether a commit's *claim* matches what it *did* without a plan, without
> a model, and without caring whether a **human** or an **agent** wrote it.

**Date:** 2026-06-07.
**Origin:** operator — *"think about how this could be useful for any git project …
even for 'human' authored commits."*
**Status:** shipped. Pure verdict `src/dos/commit_audit.py`, CLI `dos commit-audit`,
16 tests `tests/test_commit_audit.py`.

This is a **mechanism** design note (a `docs/NN_*.md`). The *why-this-is-DOS's-
universal-adoption-wedge* argument is the strategy genre and belongs in
`dos-private`, not here.

---

## 1. The reframe: byte-author ≠ *claimant* (not byte-author ≠ *agent*)

Everything DOS does rests on one invariant (`project-dos-what-is-truth-throughline`):
the verdict reads bytes whose author is **not** the party being judged. We have
always *stated* that invariant as "the kernel does not believe the **agents**." That
framing is too narrow, and the narrowness hid a much larger use.

The git verify() floor — *the commit subject claims X; the files-touched witness Y* —
never inspects **who** wrote the subject. Git ancestry and the diff's file set are
authored by the **commit machinery**, by the *act* of committing, not by the person
or process that wrote the message. So the honest invariant is:

> **byte-author ≠ claimant.** The evidence (what the commit did) is authored by a
> different party than the claim (what the message says it did) — and that is true
> whether the claimant is an autonomous agent or a human typing `git commit -m`.

A person writes `fix: resolve the auth race condition (closes #42)` in a commit that
touches only `README.md`. Not maliciously — through optimism, haste, a copy-pasted
template, or finishing the message before the work. The subject *claims* a code fix;
the diff *witnesses* a doc edit. That is the **same** unwitnessed claim as an agent's
`git commit --allow-empty -m "phase shipped"` (docs/206 §5 E3,
`benchmark/fleet_horizon/forge.py`) — and the kernel grades it the same way, because
the grading was never about the author.

This is why the floor is useful to *every* git project, not only to agent fleets.

## 2. What it checks — and what it refuses to claim

`commit_audit.classify(CommitClaim, DiffFacts, policy) -> ClaimVerdict` is **pure**;
the git read is at the boundary (`read_commit`/`audit_commit`/`audit_range`), the
`git_delta`/`liveness` discipline. It grades the *relationship between the claim's
KIND and the diff's SHAPE* — three things it can witness **soundly** (each rests on a
non-forgeable fact about the commit, never on the message):

| Verdict | Fires when | The non-forgeable fact |
|---|---|---|
| `CLAIM_UNWITNESSED` (empty/doc-only) | subject uses a **code verb** (`fix`/`add`/`implement`/…) but the commit touched **zero source files** (empty, or only docs/config/binary) | "touched 0 source files" |
| `CLAIM_UNWITNESSED` (test) | subject claims **tests** ("add tests", "tests pass/green") but the diff touches **no test file**; OR the subject claims the suite **passes/green** yet **net-deletes** test lines (the delete-the-assertion shape). The net-delete catch is scoped to the *pass/green* claim only — an honest `test: update tests` may legitimately shrink, so a net-delete under the *update* shape grades `OK`, not a contradiction ([issue #82](https://github.com/anthony-chaudhary/dos-kernel/issues/82)) | which files / the line delta |
| `OK` + a **rung** | the diff touches source/tests the claim refers to | the touched file set |
| `ABSTAIN` | the subject makes **no checkable claim** (`wip`/`merge`/`bump`/`chore`) | — |

Every commit that makes a code/test claim also carries its **witness rung** — the
generalization of `oracle._grade_grep_source` to a plan-free setting:

- **`diff-witnessed`** — the diff corroborates the claim (non-forgeable). Trust it.
- **`subject-only`** — the claim rests on the message text alone (forgeable). This is
  exactly the `grep-subject` rung `dos verify` warns about, surfaced for an arbitrary
  commit.

**What it deliberately does NOT do (state it; do not pretend):**

1. **Correctness.** A real fix to the *wrong* bug touches source and passes here. It
   grades *did the diff do the KIND of thing claimed*, never *was it right* — Wall 3,
   `project-dos-wall-presence-not-goal`. This is the same presence-not-goal ceiling
   the whole kernel honors.
2. **Issue references.** "closes #42" needs an issue→files map this module does not
   have. It never claims to verify the content of a reference.
3. **Anything where no concrete claim exists.** Most commits ABSTAIN. The verdict
   *fires* only where a concrete claim and a contradicting diff coexist — the
   conservative direction, because a false WARN on a legitimate doc-only "fix" is the
   real cost, so the verb taxonomy is tight and the source/non-source split errs
   toward calling files source (fewer false `EMPTY_CLAIM` fires).

## 3. The soundness argument (why this isn't just a heuristic linter)

The fire conditions are one-sided. `CLAIM_UNWITNESSED` asserts only the **negative**:
*the claimed kind of change is not present in the diff.* It never asserts a positive
("this code is correct"), so it cannot be wrong in the dangerous direction. A commit
that touches source under a code claim is reported `OK`/`diff-witnessed` — which means
only "the diff did the kind of thing claimed," not "the work is good." The verdict is
**occupancy, not flow** (`project-dos-eval-and-benchmark-survey`): file-touched is a
one-sided bound, and we only ever read it as the bound it is.

That is the same epistemic shape as `verify()` itself: report the rung that answered,
abstain where none does. `commit-audit` is `verify()` with the plan removed and the
author-question dropped — the smallest sound thing the floor can say about a commit.

## 4. How any git project binds it (zero adoption cost)

The exit code is the verdict (the CI-cookbook convention): `0` clean · `1` an
unwitnessed claim found · `2` unreadable ref. No `dos.toml`, no plan, no vocabulary.

- **Pre-commit hook** (catch it before it lands):
  ```bash
  # .git/hooks/post-commit  (advisory) — or pre-push for a range
  dos commit-audit --warn-only HEAD
  ```
- **CI gate on a PR's commits** (fail a PR whose commits over-claim):
  ```yaml
  - run: dos commit-audit "origin/${{ github.base_ref }}..HEAD"
  ```
- **History audit** (one-shot over the log): `dos commit-audit HEAD~50..HEAD`.
- **Agent runtime** (the original DOS use): the MCP server exposes
  `dos_commit_audit(ref, workspace)` (shipped, `14e8c1b`) so an agent audits its
  **own** commit before reporting "done" — the same tool, the agent now the claimant.

## 4b. The sweep — "how honest are this repo's commit messages?" (a RATE, measured)

`dos commit-audit --sweep <A..B>` folds a range into the aggregate **drift rate** =
`unwitnessed / checkable`, where *checkable* excludes the ABSTAINs (a `wip`/`merge`
makes no claim to be honest about, so counting it would dilute the rate toward zero
and hide real drift). It is a RATE over real history — per the docs/179 caution it
*re-describes* the corpus it is given (mints no new label), so it is a description of
THIS repo's commit hygiene, not a generalization.

**Measured on DOS's own full history (458 commits, 2026-06-07):** 223 checkable, 222
witnessed, **1 unwitnessed → drift rate 0.4%.** The single flag was
`e2d5aa9 ".claude/settings.json: wire the PostToolUse sensor live"` — the verb "wire
… live" claims a behavioral change, but the artifact is a 12-line config edit; a
defensible "the message says more than the diff shows" case for a human to confirm.
This is the validating result: on a meticulously-maintained repo the tool is **silent
on 222 honest commits and surfaces the one borderline** — correctly conservative,
zero false positives. (Two early `CLAUDE.md: add …` flags were the §5 doc-scope
limitation; fixing it — a `<doc-file>: <verb>` subject is a DOC claim — dropped the
rate from 1.3% to 0.4% and is now pinned by a test.)

`--warn-only` makes it purely advisory (always exit 0) for a non-blocking nudge;
`--docs-ok` silences the code-claim-on-doc-diff fire for repos that legitimately
commit fixes as documentation.

## 5. Honest limitations (the seams a user hits first)

- **`scope:`-prefix grammar — RESOLVED, conservatively.** A subject like
  `benchmark/foo: implement X` puts the verb AFTER the path-scope; the first parser
  only read the pre-colon head and false-ABSTAINed on it. `_leading_words` now reads
  the leading word of BOTH the prefix and the remainder, so a verb leading either is
  seen — but only the *leading* word of each segment (a buried verb, `kernel: report
  the fix status`, still does not fire), which is what keeps the widening from adding
  false positives (the `phase_shipped` six-widenings lesson, respected). A scope that
  names a doc file (`CLAUDE.md: add …`) is read as a DOC claim, not an over-claiming
  code one. Pinned by `test_scope_prefix_grammar_engages` +
  `test_scope_widening_stays_conservative` + `test_doc_scope_prefix_is_a_doc_claim`.
  Residual: rarer verbs (`expose`, `surface`) are deliberately NOT in the verb set,
  so `mcp: expose X` still ABSTAINs — the under-fire (safe) direction.
- **One commit at a time.** A claim split across a commit + its follow-up ("add X" /
  "wire X up") reads the first as possibly unwitnessed. Range mode shows the pair, but
  the per-commit verdict is per-commit.
- **Language coverage is a fixed suffix list.** An exotic language not in
  `_SOURCE_SUFFIXES` is treated as source only via the extensionless/unknown fallback;
  a `.foo` code file with a doc-looking suffix could miscount. The list errs toward
  inclusion to keep this rare and in the safe direction.
- **Correctness is out of scope, permanently.** Not a limitation to fix — the
  abstention is the discipline. For correctness you need a test/goal witness
  (`verify`'s artifact+test rung, a CI run), not a claim-vs-diff grade.

## 6. Relationship to the rest of the kernel

`commit-audit` is the **plan-free, author-free floor**; `oracle.is_shipped` is the
**plan-anchored** verdict above it. E1 (`benchmark/fleet_horizon/real_trajectory.py`)
proved the floor's *label* is non-distillable on real behavior; E3
(`benchmark/fleet_horizon/forge.py`) proved the same forgeable-subject /
non-forgeable-artifact split this module grades. `commit-audit` is that split made
into a tool a non-agentic, non-DOS-native git project can run on day one — which is
the answer to "useful for any git project, even human commits."

Links: `project-dos-what-is-truth-throughline`, docs/206 (E1/E3), docs/118 (the
forgeability rung), `project-dos-wall-presence-not-goal` (Wall 3), the CI cookbook
(`examples/playbooks/cookbook-ci-integration.md`).
