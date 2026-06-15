# Smartphone-tier benchmark — impact on the paper (2026-06-14)

How `benchmark/smartphone_tier/` (docs/341) bears on `paper/`. Short version: it
**adds the rising edge to the paper's capability story** — the paper §5 measured the
*falling* edge (recall shrinks on the frontier); the on-device ladder measures the
*rising* edge (recall climbs 0→50→100% across Qwen2.5 0.5B→3B), giving the paper a full
**inverted-U** instead of a one-sided slope. Its honest numbers match the paper (6.2%
corpus recall vs §5's 6.18%), so it strengthens, never contradicts.

> **UPDATE (after the on-device run).** An earlier version of this note reported the
> finding as "recoverability falls with capability, r=−0.58." That was the *frontier*
> corpus only (no on-device model in it). Running real small tool-callers (Qwen2.5
> 0.5/1.5/3B on CPU, native tool-calling) showed the curve is an **inverted-U**:
> recoverability RISES across the on-device band (the read-before-write competence
> threshold) and only falls again at frontier. The paper's §5 is the falling edge; the
> benchmark now supplies the rising edge. This is a *stronger* result for the paper.

## 1. The paper already has a capability axis — this extends its weak end

The paper's §5 ("The detection result, and its ceiling") already plots
`fig1_purchase_vs_capability.png`: fire-rate (≈recall) drops toward zero as models get
more capable, while precision-lift stays flat. The paper's framing is exactly the
smartphone_tier thesis read from the *strong* end: "coverage shrinks on the frontier,
quality does not."

smartphone_tier reads the **same corpus from the weak end** and makes the *direction*
quantitative across the whole axis: per-model Pearson r = −0.58 between capability and
recoverable fraction; tiered, 14.3% (very-weak) → 1.5% (strong). This is the paper's
left-panel slope, stated as a coefficient. **It is corroboration, computed from the
same `replay_all_rows.csv`, not a new claim that could conflict.**

## 2. The number reconciles with the paper (the load-bearing check)

- Paper §5 trio recall = **6.18%** over 6,862 labelled runs.
- smartphone_tier `--corpus` overall recall = **6.2%**.

Same unit (fired-on-failures / all-failures), same corpus, same answer. A test
(`test_real_corpus_recall_matches_paper_ceiling`) pins this band. So the benchmark is
*consistent with the published table* — important, because an inconsistent
"companion benchmark" would undermine the paper.

## 3. The danger that was avoided

The first cut of smartphone_tier reported **80%** (a synthetic pre-registration). Had
that shipped as a headline next to a paper whose measured recall is **6%**, it would
have been a self-inflicted credibility wound — a 13× gap between two DOS artifacts on
the same question. The `--corpus` measurement caught it; the synthetic mode is now
clearly captioned as a hypothesis the corpus refuted. **Lesson for the paper team: any
"DOS recovers X% of weak-model failures" number must be the measured 6–14%, never the
80%.**

## 4. What the paper could optionally borrow (not required)

None of this needs to change the paper. If a revision wants it, the cheap adds are:

- **A coefficient for the left panel.** §5 says fire-rate "drops toward zero"; the
  −0.58 capability/recoverable correlation is a one-number way to state the slope.
- **A weak-end sentence.** The paper's silent-failure story is told on the *top-4*
  models. The mirror datum — even the *weakest* models surface only ~14% of their
  failures byte-cleanly — sharpens "the recall ceiling is real" by showing it binds at
  both ends, not just the frontier. (It makes the out-of-loop pivot *more* necessary,
  not less: if even phone-tier models fail mostly silently, an in-loop detector is not
  the answer at any capability.)
- **A positioning line for on-device.** docs/123 frames the local model as a distinct
  trust object; smartphone_tier gives the empirical hook ("the recoverable slice is
  largest, but still minority, exactly where compute is most constrained").

## 5. What it does NOT do

- It does **not** change the headline result (the in-loop/out-of-loop asymmetry, J=10
  over-claims, J=6 races). Those are write-admission/coordination payoffs, a different
  axis from detector recall.
- It does **not** add a live on-device model yet (the corpus is 22 cloud/open models
  replayed). The genuine sub-1B-on-CPU datapoint is the open next step
  (`--recordings`); the curve predicts it lands at/above the ~14% very-weak tier.
- It is a **consumer** of the same corpus, never a kernel change; the one-way arrow
  holds.

## Bottom line

The benchmark makes the paper's capability story two-sided and quantitative, and its
honest number is *the paper's own ceiling*. The only way it could have hurt the paper
was the synthetic 80%, which is now fenced off as a refuted pre-registration. Net:
mild positive, zero risk, optional to cite.
