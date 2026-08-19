# Residual review beyond commits — choose coverage joins before hunk semantics

> **Issue:** #204  
> **Status:** design plan. Selects two follow-ons: a branch/range claim fold and
> a test-witness residual join. Explicitly defers semantic hunk subtraction.

## 1. Decision in one page

The first residual-review cut is sound because it subtracts only commits whose
claim is corroborated by `commit-audit`; everything else remains visible. Its
weakness is granularity, not safety.

The next two highest-value avenues are:

1. **Cross-commit / branch-level claim fold.** Re-evaluate an unwitnessed commit
   claim against the additive diff of a declared range, without rewriting the
   per-commit verdict. This addresses the common `add X` then `wire/test X`
   sequence and is buildable from git evidence already gathered at the CLI
   boundary.
2. **Test-witness residual dimension.** Join the existing test-witness verdict
   to each source-changing review unit and keep `UNWITNESSED_TEST` visible even
   when claim-vs-diff shape is witnessed. This finds the sharper “the diff fits
   the prose, but no failing-before/passing-after test exercised it” residue.

Do **not** implement semantic per-hunk subtraction yet. A hunk is a textual
partition chosen by diff context; it has no stable claim identity and no
independent effect receipt. An LLM or regex can rank hunks for attention, but it
cannot safely remove them from review. Hunk navigation remains advisory until a
line/hunk is linked to a stronger artifact such as symbol ownership plus an
executed assertion receipt.

## 2. Current floor and invariants

`dos review` projects `commit-audit` into a `ReviewPlan`: priority bands,
per-commit items, touched paths, reasons, and a walk order. The deterministic
floor is one-sided:

- an unwitnessed checkable claim is always Band 1;
- an abstention stays visible;
- semantic policy may ask for more review, never less;
- JSON/cards/navigation are projections of the same plan.

The extensions below preserve those laws. They add axes to a review unit; they
do not reinterpret old green as proof of a new property.

## 3. Follow-on A — range claim fold

### 3.1 Evidence and pure verdict

The I/O boundary gathers:

```text
commit:       sha, subject, parent, per-commit diff facts
range:        base..head, ordered commits, aggregate content diff
policy:       maximum range, merge handling, allowed claim kinds
```

A pure classifier returns, for each per-commit `CLAIM_UNWITNESSED` item:

```text
RANGE_WITNESSED     aggregate diff contains the missing artifact shape
RANGE_UNWITNESSED   aggregate diff still lacks it
RANGE_ABSTAIN       merge/rename/size/claim shape exceeds deterministic policy
```

Example: `feat(parser): add parser` touches only a new source file and is
unwitnessed under a policy requiring tests; a later `test(parser): exercise
parser` adds the matching test in the same range. The per-commit receipt remains
unchanged. The range projection may annotate the first item `RANGE_WITNESSED`
and move it below the hard residual **only when the aggregate deterministic
facts satisfy the same claim-kind floor**.

### 3.2 Soundness boundary

A range fold proves only “the declared branch/range contains corroborating
artifacts.” It does not prove which follow-up caused which runtime effect, and it
must not silently launder unrelated later work.

Therefore:

- require an explicit contiguous ancestry range; never search arbitrary future
  history;
- cap commit count and diff size; over cap means `RANGE_ABSTAIN`;
- refuse/abstain on non-linear merge topology in v1;
- retain original SHA, verdict, and reason in every projection;
- show the corroborating later SHA/path facts;
- never change `dos commit-audit SHA`; this is a review-layer annotation;
- `RANGE_WITNESSED` may lower review priority only for deterministic artifact
  predicates already accepted by the per-commit floor. Semantic similarity can
  only promote/escalate.

This is subtraction based on git structure, not a model opinion.

### 3.3 Acceptance witness

A fixture branch with two commits must show:

1. commit 1 is unwitnessed alone;
2. the explicit `base..head` fold is range-witnessed with commit 2 named;
3. removing commit 2 returns range-unwitnessed;
4. adding an unrelated test does not clear a path/symbol-scoped requirement;
5. a merge range abstains;
6. `dos commit-audit <commit1>` remains unchanged.

## 4. Follow-on B — test-witness residual join

### 4.1 Evidence and plan shape

The test-witness verdict (TWV, [docs/288](288_twv-the-test-witness-verdict-reverse-classical-testing-as-a-kernel-rung.md))
already asks whether a test materially distinguishes the pre-change and
post-change trees. Residual review should consume that verdict, not reimplement
it.

Extend each source-changing review item with an orthogonal field:

```json
{
  "claim_verdict": "WITNESSED",
  "test_witness": {
    "outcome": "WITNESSED_TEST | UNWITNESSED_TEST | ABSTAIN",
    "receipt": "...",
    "reason": "..."
  }
}
```

Plan ordering becomes a product, not a replacement:

| Claim/diff | Test witness | Review result |
|---|---|---|
| unwitnessed | any | hard claim residual (Band 1) |
| witnessed | unwitnessed | hard test residual (Band 1) |
| witnessed | witnessed | may leave the hard residual; semantic policy can still promote |
| abstain | any, or any | abstain remains visible |

Docs-only/data-only changes can be policy-exempt, but unknown source shapes
abstain rather than assume no test is needed.

### 4.2 Soundness boundary

A green suite is not a test witness. The join accepts only the TWV receipt that
identifies the assertion/test command and the distinguishing pre/post outcomes.
Missing test infrastructure, timeouts, flaky contradictory runs, or an absent
baseline are `ABSTAIN`/`UNWITNESSED_TEST`, never clean.

The semantic reviewer may say a test looks relevant and escalate risk. It cannot
mint `WITNESSED_TEST` or lower a hard test residual.

### 4.3 Acceptance witness

A fixture must show:

1. claim-vs-diff is witnessed but a permanently green test yields a test
   residual;
2. failing-before/passing-after clears only the test axis;
3. the claim axis remains unchanged;
4. timeout/flaky/unsupported environments abstain visibly;
5. JSON, card, walk, and MCP projections carry identical axis outcomes.

## 5. Why per-hunk residual is deferred

Three tempting hunk heuristics are not subtraction-grade:

- “this hunk is only formatting” depends on semantic interpretation;
- “this hunk is covered by that test” needs executed line/symbol provenance,
  not path-name similarity;
- “this hunk corroborates the subject” confuses textual proximity with an
  independently authored witness.

A future hunk design can proceed when it has a stable unit and receipt, for
example `(blob_before, blob_after, symbol_id, hunk_hash)` joined to coverage or
mutation evidence. Until then, expose hunks as navigation anchors under their
parent residual item and allow one-sided risk promotion only.

This answers issue #204's central question: modern semantic/AST/review-effort
scores **add an opinion** unless they carry an independent receipt. They may
order the residual; they may not subtract it.

## 6. Concrete work split

Two ready follow-ons are filed from this plan:

- **[#250 — Range-aware residual fold](https://github.com/anthony-chaudhary/dos-kernel/issues/250)** — implement the evidence/classifier/projection and the six-fixture witness in §3.
- **[#251 — TWV residual dimension](https://github.com/anthony-chaudhary/dos-kernel/issues/251)** — join the existing TWV receipt into `ReviewPlan` and pin parity across CLI/MCP/renderers as in §4.

They are separable. Range folding changes what git artifact can corroborate a
claim; the TWV join adds an orthogonal runtime-test axis. Neither waits for
semantic hunk classification, editor UI, or an LLM judge.

## 7. Relationship to the existing arc

- [docs/358](358_review-the-residual-not-the-diff-the-product-wedge.md) — product
  wedge and deterministic-floor rule.
- [docs/214](214_commit-audit-the-author-neutral-claim-vs-diff-floor.md) — current
  per-commit claim/diff witness and its cross-commit limit.
- [docs/288](288_twv-the-test-witness-verdict-reverse-classical-testing-as-a-kernel-rung.md) —
  distinguishing test receipt consumed by follow-on B.
- [docs/181](181_effect-witness-the-result-state-witness.md) — the broader rule
  that effect claims need read-back; future non-code residual axis.
- [docs/349](349_the-content-diff-rung-the-w2-to-w3-climb-shipped.md) — content
  evidence outranks existence, the same direction both selected axes preserve.
