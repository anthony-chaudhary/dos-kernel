# 300 — SPEND: the token-spend breakdown, the efficiency trend, and the loop surface

> **Status:** in flight (2026-06-11). The forcing question (operator `/goal`): *make
> token-effectiveness observability 10× deeper — prefill/decode, trends, and the
> self-improvement surface — grounded in what the field standardized in the last
> few months.* This plan widens the docs/263 efficiency family in three steps:
> a typed **spend breakdown** (P1–P2), an **efficiency trend** over the verdict
> journal's fossils (P3), and the **improve() pass-through** so a self-improving
> loop's keep/revert record carries its price facts (P4). Everything else found
> in the research is deliberately offloaded to issues (§7).

---

## 1. What the field standardized (the research base, early 2026)

Five facts from outside this repo drive the design. Each is recent and load-bearing:

1. **The five-way token split is now a standard.** OpenTelemetry's GenAI semantic
   conventions merged provider-neutral attributes in January 2026:
   `gen_ai.usage.input_tokens`, `output_tokens`, `cache_read.input_tokens`,
   `cache_creation.input_tokens`, and `reasoning.output_tokens` (a sub-count of
   output). One scalar "tokens" is no longer how anyone serious accounts spend.
2. **The two wire conventions disagree, and that disagreement is the #1 bug
   class.** One convention reports an input count that *excludes* cached tokens
   (the cache fields are siblings; total input = the sum) — call it **additive**.
   The other reports an input count that already *includes* the cached tokens
   (the cache field is a sub-count to subtract) — call it **inclusive**. Cost
   tools keep double-counting at exactly this seam (live 2026 issues in several
   major observability trackers). The fix everyone converges on: normalize ONCE,
   at the boundary, into disjoint counts.
3. **Cache-hit ratio is the headline efficiency KPI.** Cache reads bill at ~0.1×
   the base input price and writes at ~1.25–2×, so the fraction of context served
   from cache is the first number an operator looks at. Decode (output) tokens
   bill at ~4–5× input and dominate latency, so output share is the second.
4. **Reasoning tokens are billed as output but tracked separately.** The
   consolidated practice: the billed-inclusive output count is the efficiency
   denominator; the reasoning sub-count is an "overthinking" diagnostic. The
   known trap: a run that spends its whole output budget thinking and emits a
   truncated answer is exactly *meaningful spend, zero work* — the WASTEFUL shape
   docs/263 already names.
5. **Trend and regression gates are how spend feeds self-improvement.** Cost is
   now a leaderboard axis (accuracy-vs-cost Pareto fronts, cost-per-resolved-task
   columns), and per-change token-cost regression checks against a trailing
   baseline are an emerging CI practice. A loop that improves itself needs a read
   on whether its work-per-token is *getting better or worse across runs*, not
   just a single-run ratio.

## 2. The gap in DOS

The docs/263 verdict answers "did the tokens buy work?" from **one scalar**. The
audit behind this plan found, concretely:

- `EfficiencyEvidence.tokens` is a single count — no input/output/cache/reasoning
  split exists anywhere under `src/dos/` (the only code in the repo that reads a
  real usage record is dev tooling outside the kernel).
- **No trend accumulates.** `productivity` folds a per-step trend the caller
  hands it, but nothing remembers work/tokens across runs. The verdict journal
  (docs/262) is the natural fossil record — and today an `--observe`d efficiency
  verdict records **no evidence at all** (the journal's detail filter keeps only
  top-level scalars, and the work/tokens live one level down).
- The improve() keep-gate (docs/280) already runs an efficiency rung, but it is
  blind to the spend mix: a candidate's keep/revert record says KEEP or REVERT
  and never *what the spend looked like*.

So today the operator gets one word (EFFICIENT/COSTLY/WASTEFUL) from one number.
After this plan they get: the standardized five-way split with the wire
asymmetry killed at the seam, the derived diagnostics the field treats as
headline KPIs (cache-hit ratio, decode share, reasoning share), a cross-run
trend folded from journal fossils, and a self-improving loop whose keep records
carry the price facts. That is the 10× — not a new accusation, a much deeper
*legible-distrust* surface under the same closed verdict vocabulary.

## 3. The primitives

### P1 — `dos.spend`: the breakdown vocabulary

A new pure-stdlib kernel leaf, `src/dos/spend.py`:

- `SpendBreakdown(input, output, cache_read, cache_creation, reasoning)` — five
  non-negative counts, **canonically disjoint** (input excludes the cache
  fields; reasoning is the one exception, a sub-count of output, validated
  `reasoning <= output`). Frozen, hashable, no I/O.
- Derived properties (the diagnostics, each division-safe): `total` (all four
  disjoint counts), `prefill` (input + cache_read + cache_creation — the context
  side), `decode` (output — the generation side), `cache_hit_ratio`
  (cache_read / prefill), `output_share` (output / total), `reasoning_share`
  (reasoning / output).
- Boundary constructors named by **wire semantics, never by vendor** (the
  `test_vendor_agnostic_kernel` rule — vendor names appear in prose only):
  `from_additive_usage(mapping)` for records whose input count excludes cached
  tokens; `from_inclusive_usage(mapping)` for records whose input count includes
  them (the constructor subtracts; an inconsistent record — cached > prompt — is
  a loud `ValueError`, never a silent clamp); `parse_usage(mapping)` detects the
  shape by its key names and refuses ambiguity. This is the docs/217
  dialect-seam move applied to usage records: normalize the wire shapes ONCE, at
  the boundary, so the double-count bug class (§1.2) cannot enter the kernel.

### P2 — the evidence widening: efficiency AND improve carry the breakdown

`EfficiencyEvidence` and `CandidateEvidence` (docs/280) each gain an optional
`breakdown: SpendBreakdown | None` — the pure layer only, no CLI yet:

- When given with `tokens=0`, the scalar derives from `breakdown.total`. When
  both are given they must agree — a mismatch is a `ValueError` (an inconsistent
  evidence pair is a contract error, never silently reconciled).
- `classify()` ladders are **unchanged**: the verdicts still ride the scalar,
  so every existing consumer and test stays byte-identical. The breakdown is
  *legibility* — `EfficiencyVerdict.to_dict()` carries the diagnostics when
  present, so a WASTEFUL verdict can say *what kind of spend bought nothing*
  (cold cache? all decode? half thinking?).
- The improve() pass-through: the keep-gate's efficiency rung receives the
  candidate's breakdown, and `CandidateVerdict.to_dict()` carries an
  `efficiency` sub-object (the rung's verdict + diagnostics) whenever the rung
  ran on breakdown-carrying evidence — the scalar path keeps its exact old JSON
  shape. A self-improving loop's keep/revert record now states the price of
  each candidate (the §1.5 regression-gate input), with nothing newly enforced
  (the floor stays host-armed, the docs/263 line).

### P3 — `dos.efficiency_trend`: is the ratio fading across runs?

The trend completion of the family — `productivity` re-aimed from per-step work
deltas onto cross-run work-per-token ratios:

- `TrendHistory(samples)` — ordered (work, tokens) pairs, OLDEST first, each a
  frozen env-authored pair (the same two counts docs/263 reads).
- `classify(history, policy) -> IMPROVING / STEADY / DEGRADING`, PURE, timeless.
  The ladder mirrors `productivity`'s withhold-then-sustained discipline:
  fewer than `min_samples` → STEADY-benign ("not enough history"); DEGRADING
  only when the **last two** ratios both fall more than `tolerance` below the
  **median of the prior samples** (sustained, robust to one outlier run — the
  multi-signal AND that keeps one bad run from false-tripping); IMPROVING when
  the last two both clear the median by more than `tolerance`; else STEADY.
  A formal significance rung (the Welch's-t-style gate the CI-practice research
  found) is deliberately NOT here — that is issue #34's thread.
- `TrendPolicy(min_samples=3, tolerance=0.25)` — generic defaults, policy not
  mechanism, declarable later alongside `[efficiency]` (§7).

### P4 — the CLI boundary: usage records in, fossils out, the trend verb

All the I/O, in one place (row-3 helper edits only):

- `dos efficiency --usage-json PATH|-` and `dos improve --usage-json PATH|-`
  read a provider usage record at the boundary (file or stdin), normalize via
  `parse_usage`, and freeze it into the evidence. The scalar `--tokens` paths
  are untouched.
- The journal fossil fix: the CLI's observe path flattens one level of nested
  evidence scalars into the verdict-journal detail (dotted keys, e.g.
  `evidence.work`), so an `--observe`d efficiency verdict finally records the
  two counts the trend folds. Additive for every other observed verb.
- `dos efficiency-trend --samples "w:t,w:t,…"` (caller-assembled, like
  `dos productivity --deltas`), and `dos efficiency-trend --from-journal
  [--run RID] [--last N]` — the CLI reads the verdict journal at the boundary
  (read-only, the same row-3 discipline as `dos observe`), extracts the
  efficiency events' fossilized work/tokens, and hands the pure fold its
  samples. Exit codes: IMPROVING 0 / STEADY 0 / DEGRADING 3 (unknown 5,
  contract 2), in the `dos exit-codes` contract.

This is the first place a recorded verdict feeds a later verdict — and it does
so as *evidence read at a boundary*, never as an adjudicator reaching into the
journal: the fold is pure, the read is the CLI's, the journal stays advisory.

## 4. Why it stays byte-clean (docs/138)

Every new count is env-authored: the breakdown's fields come from the provider's
billing record (the run cannot author its own usage object any more than it
could author the scalar), the trend's samples are the same two witnessed counts
docs/263 reads, frozen per run by the CLI that recorded them. No narration is
parsed anywhere. And the safe direction is preserved structurally: the verdict
ladders are unchanged, so a richer breakdown can never *flip* a verdict toward
EFFICIENT — it can only explain one.

## 5. What this is NOT

- **Not a dollar model.** The breakdown carries counts; pricing them (0.1× cache
  reads, 4–5× output) is a consumer's multiplication — the docs/260 line, held.
- **Not a telemetry reader in the kernel.** `--usage-json` and `--from-journal`
  are CLI-boundary reads; every `classify` stays pure. No transcript sweeper, no
  provider client, no daemon.
- **Not new verdict states.** EFF's vocabulary stays EFFICIENT/COSTLY/WASTEFUL;
  the trend adds its own small closed set. No "COLD_CACHE" accusation — a cache
  verdict would need its own plan and floor discipline.
- **Not a vendor coupling.** No vendor name as a code identifier; the two
  constructors are named by the only distinction that matters (does the input
  count include cached tokens), with providers named in prose only.

## 6. Phases and done-conditions

| Phase | Ships | Done when |
|---|---|---|
| **P1** | `src/dos/spend.py` (`SpendBreakdown`, diagnostics, `from_additive_usage` / `from_inclusive_usage` / `parse_usage`) + `tests/test_spend.py` | new tests green; `test_vendor_agnostic_kernel` still green |
| **P2** | `EfficiencyEvidence.breakdown` + verdict diagnostics, and `CandidateEvidence.breakdown` → efficiency rung → `efficiency` sub-object in the improve verdict JSON (`tests/test_spend_evidence.py`) | existing efficiency/improve tests byte-identical; new tests pin derive/mismatch/JSON shapes + the pass-through |
| **P3** | `src/dos/efficiency_trend.py` (the pure fold) + `tests/test_efficiency_trend.py` | ladder tests green (sustained-fall, one-outlier, zero-baseline, robust-median) |
| **P4** | the CLI boundary: `--usage-json` on efficiency/improve, the observe-path detail flattening, `dos efficiency-trend` (`--samples`, `--from-journal`) + exit-code contract row (`tests/test_spend_cli.py`, `tests/test_efficiency_trend_cli.py`) | an `--observe`d efficiency verdict records `evidence.work`/`evidence.tokens`; an end-to-end test records ≥3 efficiency verdicts via `--observe` then folds them via `--from-journal` to DEGRADING |

## 7. Offloaded (issues, not phases)

Found by the same research; each is real, none belongs in this change-set:

- **`dos.toml [efficiency] / [productivity] / [improve] / [trend]` loaders** —
  the declared config seam (docs/263 §6); blocked on the T1-guarded `config.py`
  (the docs/296 operator-armed override seam, issue #25).
- **`dos top` spend column** — the docs/263 §6 follow-up, now with a concrete
  shape (cache-hit ratio + verdict chip).
- **Exporter dimensions** — statsd/OTLP egress of the breakdown diagnostics,
  aligned with the OTel GenAI attribute names (docs/266 consumer).
- **`loop_decide` rung** — convert a sustained DEGRADING/WASTEFUL into a
  stop-when-unproductive transition (docs/218/263 both anticipate it).
- **`liveness.tokens_spent_since` feed** (docs/82 P3) — the breakdown's `total`
  is the natural value for the long-unfed slot.
- **Significance rung** — replace the tolerance band with a real statistical
  gate (issue #34's thread, at trend scale).
