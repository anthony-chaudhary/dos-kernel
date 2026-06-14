# 153 — Can DOS lift a weaker model toward a stronger one? (the honest proof point)

> **Status:** design + ruling + the Stage-1 gate BUILT & self-validated (2026-06-05). The
> `weak_model_gate.py` instrument is committed and reproduces the known gemini null (§5); the
> experiment is replay-first so it cannot headline a guessed magnitude (no pp number is claimed —
> only the formula, the bounds, and the gate). Motivated by the operator's question — *"all of this is
> relative to a good model; how does DOS help a WEAKER model, and what's a good proof-point
> middle-ground model?"* — which reframes the whole EnterpriseOps arc: everything measured so far
> tested DOS against a **strong** model (gemini-2.5-flash) and found ~0, because a strong model
> rarely fails at the execution substrate DOS guards. The weak model is where the thesis was always
> supposed to live ("DOS hardens the substrate UNDER a cheap agent's plan," docs/143).
>
> **One line.** "DOS lifts a weak model toward a strong one" is **false as a gap-closer and true
> only in a bounded form**: DOS recovers the *execution-substrate fraction* of a weak model's
> failures (minted ids, loops, the *narrating* premature stop) — a **measured low-single-digit-pp
> integrity lift, WARN-only, with a credible floor of zero** — and owns **0 %** of the ~90 % of
> failures that are planning + *silent* stops (the +14–35 pp lever forfeit by doctrine). And the
> entire magnitude rests on a weak-model failure-rate **nobody has measured** — so the honest first
> move is a **~$50 corpus generation + a $0 replay gate** that either earns a live A/B or kills the
> thesis cheaply, exactly the docs/145 discipline applied before the claim, not after.

---

## 0. Why the strong-model nulls do not settle it

Measured against gemini-2.5-flash, all three shipped detectors are ~0: `arg_provenance` (mints) ≈ 0
because the model reads its FKs first; `tool_stream` (loops) `p_stuck = 0`; `dangling_intent`
(narrating premature stop) 13 % recall / 0 % false-fire — real but small. That is the *least*
favorable case for DOS by construction: a capable model rarely fumbles the execution substrate. The
operator's question is the right one because the thesis was always **"DOS hardens the substrate
under a *cheap/failing* agent"** — and we have tested the numerator (a capable agent) and never the
denominator. A weaker model fails *more* at exactly the substrate DOS guards — so the recovery
should be *larger*, and "weak → strong" becomes the real proof point.

---

## 1. The hard ceiling (the formula, graded by what is actually measured)

The lift is not the gap; it is a product, and every factor is bounded:

```
lift = (per-task failure rate)
     × Σ_detector [ recoverable-fraction × heed-rate × can-do-step-when-nudged ]
     × (1 − disruption)          # WARN ≈ 0
     , capped at the execution-substrate slice, planning-wall-bounded.
```

| Factor | Value (DeepSeek-shaped middle model) | Status |
|---|---|---|
| per-task failure rate | ~0.75 | fair interpolation from the ladder |
| recoverable-fraction (detection ceiling) | ~0.13 narrating-stop + a thin mint/loop tail | **measured on gemini ONLY** |
| heed-rate (model issues a follow-up after the nudge) | ~0.75 | measured — but this is **not** recovery |
| **can-do-the-step-when-nudged (the verifier actually flips)** | **~0.2–0.3** | the load-bearing unknown; **decays as the model weakens** |
| disruption (WARN-only) | ≈ 0 | measured, robust |

**The single biggest correction the adversarial pass forced:** ~75 % is the *follow-up-issued*
rate, not the *task-recovery* rate. A nudge that makes the model issue *a* call is not a nudge that
makes the *verifier flip* — the verifier-flip rate is `mattered × (help − hurt)`, ~27 % on the
headline run. Using 75 % as "recovery" inflates the ceiling ~3×. (Note: the `intervention_cases.jsonl`
`recovered_if_*` labels DO encode the verifier-flip outcome — so the eval is honest; the inflation
risk is in *prose summaries* that conflate the follow-up rate with recovery. Keep them separate.)

**The number, with honest error bars:** `0.75 × 0.12 × 0.25 ≈ 2.3 %` of *tasks* recovered →
**roughly +0.5 to +1.5 pp on overall verifier-pass, WARN-only**, with a fully credible **floor of
~0** (the same collapse that took `stall_sim` from +17 pp to a measured ~0, and `arg_provenance`
from a sim-lift to ~0 natural mints on gemini). The integrity *slice* number can look bigger
(+2–3 pp) because integrity is a fraction of all verifiers — report it per-slice, never as the
headline.

**Two discounts the optimistic framings under-weighted:**
- **The horizon discount.** A nudge buys *one* turn; a task needs *all* its missing steps. A model
  that lost the thread at step 4 of 9, nudged "you said you need X," often produces X and stops
  again at step 6. **Per-turn recovery ≠ per-task recovery**, and it is strictly smaller.
- **The can-do-it-when-told decay.** Every recovery assumes the weak model can *do* the step once
  reminded. A weak model often narrates "I need to X" *because* X is the step it cannot form — the
  narration is the residue of a failed attempt. So `can-do-step-when-nudged` **decays toward 0 as
  the model weakens**, which is precisely why the proof point must be the *middle* ground, not the
  weakest model.

---

## 2. The proof-point model — DeepSeek-V3.2, reported beside Qwen (not alone)

**DeepSeek-V3.2 (24.5 %, $0.014) as the middle ground; Qwen3-235B (16.1 %, $0.007) reported
alongside as the floor.** Two models, not one — because *which* model has a recoverable failure mix
is the experimental **result**, not an input you may assume. Reporting one is the cherry-pick;
reporting two with per-failure-shape attribution is not.

- **Qwen (16 %) is the trap:** too weak — by definition failing the *strategy* on most tasks, so
  DOS's execution-substrate slice is a small share of a huge gap, and `can-do-step-when-nudged` is
  lowest (it often *can't* do the named step). The floor, not the proof.
- **DeepSeek (24.5 %) is the principled middle:** a model good enough to solve a quarter of these
  tasks demonstrably *can* plan many it then fumbles in execution — so execution fumbles are a
  *bigger share* of its gap, and the nudge is more likely to land. The **$0.014 vs Opus $0.36 = 25×
  cost gap** is the literal "harden the substrate under a cheap agent" deployment.
- **The gap is real but not hopeless:** DeepSeek → Gemini is **~7.4 pp**, and the whole closed-source
  field spans ~9 pp — so closing even a fraction is a multi-rank move (if it lands).

**The exact claim, phrased to survive a cherry-picking accusation:**

> *"On the **measured** execution-substrate fraction of DeepSeek-V3.2's failures — its natural
> mint-rate, loop-rate, and narrating-premature-stop rate folded from its own real trajectories by
> the shipped classifiers — WARN-only DOS recovers a measured Z % of scored verifiers at zero
> feasible-task regression, for +N pp on the integrity slice. Reported beside Qwen on identical
> tasks / horizon / hidden-SQL scorer, with the ~90 % premature-completion head DOS owns 0 % of
> shown in the same table."*

The model is fixed *before* results, both weak models are shown, recovery is the *verifier-flip*
rate, and the planning wall sits in the same sentence as the lift.

---

## 3. The experiment — replay-first, zero-faith, a cheap decisive kill

**Stage 0 — generate the corpus (the only model-call spend, ~$50).** One cheap recording pass each
for DeepSeek-V3.2 and Qwen3-235B through the existing `dos_react` harness, same task slice / horizon
/ hidden-SQL scorer, **no intervention in the loop** — just record. Needs an OpenRouter key + a
config edit (the shipped `conf/llm/openrouter.json` is a bare template, `sk-or-v1-<your-api-key>`,
pointed at `kimi-k2-thinking` — repoint it to DeepSeek/Qwen). ~$0.014/task DeepSeek, ~$0.007 Qwen.
No Docker A/B, no live intervention yet.

**Stage 1 — replay the shipped classifiers (≈$0, the honest gate).** Fold `failure_distribution.py`
+ `replay_recall.py` (`classify_call` → natural mint-rate) + `replay_stall.py` (`classify_stream` →
`p_stuck`) + `replay_dangling.py` (narrating-stop recall) over those recordings. This **measures the
three rates every prior estimate guessed**, plus the per-task *intersection* (do detectors fire on
the same failing task → the dedupe answer, measured not assumed). The replay scripts import only
stdlib + the shipped classifiers — genuinely $0, zero model surface.

**The pre-registered falsifiable prediction (write before looking):**

> *DeepSeek's natural mint-rate and `p_stuck` will be materially > gemini's ~0, and its
> execution-substrate failure fraction (mint + loop + narrating-stop, deduped per task) will exceed
> 15 %.*

**If the weak corpus is still ~92 % silent-stopper like gemini, the thesis is falsified at ~$50
total** — the detectors are blind to its dominant failure regardless of model strength, and "DOS
lifts a weak model" dies on real data, docs/149 repeating one rung down. **That cheap decisive kill
is the win condition for honesty.**

**Stage 2 — live WARN-only A/B (only if Stage 1 clears the gate).** `none` vs `WARN` (skip
SKIP/DEFER/BLOCK — WARN is the measured robust optimum; BLOCK's hurt-flips and SKIP's −9 pp are
settled). Ablation ladder, each marginal **deduped per failing task, not bundled** (the docs/143 §8
rule): **A0 bare ReAct / A1 +arg_provenance / A2 +tool_stream / A3 +dangling_intent**, paired-seed.
Headline metrics: **per-verifier-slice (Integrity / Task-Completion / Permission) + feasible-rate +
the verifier-flip recovery rate** — never a single avg-success number (at n≈55 the ±5 pp success
noise band swallows a +1 pp lift whole). Treat the live A/B as *confirmation of a pre-registered
replay prediction*, not a first data point.

**Stage 3 — cost-per-success (a build, not a free read).** `score_ab.py` has **zero** cost/token
code today (verified); the only token capture is raw `usage_metadata` dropped into trajectories,
never read back. So the Pareto "cost-per-success" framing is a multi-day build (a token-rollup +
honest accounting of the recovery loop's *added* calls — a nudge that issues a follow-up IS an added
call, on ~75 % of catches). The Pareto *direction* is sound; the "free / runnable-today" labeling is
false. Defer.

---

## 4. The story, honestly — and the better framing

**"DOS lifts a weak model toward a stronger one" is FALSE as a gap-closer and TRUE only in a bounded
form.** It does not close the 8–13 pp gap to Opus, or even half the 7.4 pp gap to Gemini, because
~90 % of even a weak model's failures are planning + *silent* stops — the lever DOS forfeits by
doctrine (91.6 % MISSING ROW, the *majority* of it the silent stopper: median 5 calls vs ~9 needed,
no narration, no loop, no mint — structurally invisible to all three detectors). The honest center
is **~+1 pp overall verifier-pass (≈10–20 % of the DeepSeek→Gemini gap), inside the small-N noise
band, with a credible floor of zero** until the weak corpus is measured.

**The better framing is not lift at all — it is LEGIBILITY / triage**, and it is the one framing
grounded in *already-measured* real data (13 % recall, 0 % false-fire): DOS labels a cheap model's
runs with byte-clean verdicts (`DANGLING_INTENT` / minted-id / `STALLED`) so a human or a strong
model reviews the **flagged high-precision slice** instead of all runs. This is DETECT-only — the
doctrine DOS actually owns — it never touches the planning wall, never assumes the model can do the
step when told, and cannot be killed by the can-do-it-when-nudged decay. It is the FleetHorizon
review-fraction story (verified-per-$ via cutting human review) re-aimed at a single cheap model.

**The one defensible sentence:**

> *On a cheap model, DOS does not close the planning gap to a strong one — that gap is ~90 %
> strategic reasoning DOS forfeits by doctrine — but it makes the cheap model's execution-substrate
> failures **legible and recoverable on a high-precision slice**, worth a measured
> low-single-digit-pp integrity lift WARN-only where the model fumbles a step it could do, and
> exactly zero where the step was never planned or cannot be done when reminded — and which of those
> dominates is a ~$50 replay measurement nobody has yet run.*

**The immediate action:** spend the ~$50 to generate DeepSeek + Qwen trajectories and run the
existing $0 replay. That single measurement either earns the live A/B or kills the thesis honestly —
the only move that replaces a guessed magnitude with a measured one. Until then, this doc headlines
**no pp number** — only the formula, the bounds, and the gate.

---

## 5. The reusable instrument — BUILT + self-validated on gemini

The Stage-1 gate is now a committed harness: `benchmark/enterpriseops/weak_model_gate.py`. Given a
recordings folder for ANY model, it folds the three shipped detectors per run and prints the
**deduped execution-substrate failure fraction** + the per-detector breakdown + the pre-registered
threshold check (≥15 % → run the A/B; <15 % → thesis falsified for this model). It is the
`failure_distribution.py` sibling, model-agnostic — the same gate runs on any model's corpus.

**The load-bearing honesty mechanism (the gemini self-test forced it):** a detector's fires count
toward the recoverable fraction ONLY if it is **enriched on failures vs passes** (fires more on
failed runs than passed ones). A detector that fires equally on passes is **noise** (false
positives), not a recoverable-failure signal, and is excluded. Building the gate caught two bugs in
the optimistic narrative:

1. A hand-rolled mint fold (gating reads, dropping the task-text corpus) over-fired to **47 %** — fixed
   by reusing the validated `replay_recall` path (`is_mutating_tool` + `evaluate_tool_call`), → 29 %.
2. Even fixed, **MINT fires 24 % on passed runs vs 17 % on failed** — pure noise (the residual
   false-flag rate `replay_recall` measured; confirming the "~0 real mints on gemini" claim). The
   enrichment test correctly **excludes MINT** as noise on this corpus.

**Self-validated result on gemini-2.5-flash** (the known null, the instrument's self-test):

| Detector | fail-rate | pass-rate | verdict |
|---|---|---|---|
| MINT (arg_provenance) | 17 % | 24 % | **NOISE — excluded** |
| LOOP (tool_stream) | 2 % | 0 % | SIGNAL |
| DANGLE (dangling_intent) | 11 % | 0 % | SIGNAL (matches the docs/150 13 % recall / 0 % false-fire) |

→ **deduped DOS-recoverable = 13 % of failures < 15 % threshold → FALSIFIED for gemini.** Exactly
right: on a strong model the gate says "DOS's failure mix is not recoverable enough — the thesis
dies here," reproducing the established null (87 % unreachable = silent-stop / planning). **The
instrument is validated and ready for the $50 weak-model corpus** — and the enrichment test means a
weak model only clears the gate if its detectors fire on *real* failures, not noise. That is the
honest unit of progress: it turns "does DOS help a weak model?" into a $0 replay over a $50 corpus,
per model, with a built-in noise filter.

### 5.1 The instrument is now a model-tier SWEEP — `benchmark/iot_tier/` (2026-06-14)

The gate above self-tests on ONE corpus. It is now also swept across a declared model-size
ladder (frontier → mid → small → **IoT-class**) by `benchmark/iot_tier/`, which folds the SAME
validated enrichment logic (`weak_model_gate.gate_fraction`, extracted) over a synthetic,
**declared-and-cited** per-tier failure mix. The result is the §1 prediction made visible: the
recoverable fraction is **non-monotone** — it reproduces the gemini null at the frontier (~13 %),
rises to a **peak on the middle (DeepSeek-shaped) model** (~34 %), then **collapses** at the IoT
(sub-3B) end (~10 %) as the `can-do-when-nudged` decay migrates narration into silent-stops. So
the §2 claim — *the proof point is the middle, not the weakest model* — is now shown on a
calibrated ladder, not only asserted. This is a **calibrated simulation** (synthetic corpora,
real detectors); the measurement that replaces the calibration is the Stage-0 ~$50 real
IoT-corpus run (§3), tracked as a GitHub issue. See `benchmark/iot_tier/README.md`.

> **Denominator reconciliation (docs/152, concurrent).** The 11–13 % DANGLE numbers above are over
> the *full* `live_results` corpus (280 failed runs, inflated by duplicate `none`-arm + zero-call
> rows). docs/152 re-measured `dangling_intent` over a **deduplicated, paired** subset (~100 paired
> by `task_id`, 35 failed) and found **26 % recall (9/35), 0 % false-fire** — the cleaner, sharper
> number, and *higher* than the 3-flash 13 % (refuting grammar-drift). Both are correct on their own
> corpus; **26 % on the clean paired set is the project's best current DANGLE recall.** docs/152 also
> independently confirms this doc's core: arg_provenance fires ~0 naturally, tool_stream `p_stuck=0`,
> precursor ~0 — so `dangling_intent` is the *one* naturally-firing axis, and the open question is
> the **FIX half** (does re-surfacing the agent's own sentence *convert* a stop into a finish?) —
> exactly the `can-do-step-when-nudged` unknown §1 flags as load-bearing.

**Cross-refs:** the strong-model nulls this reframes = docs/149 (failure distribution) + docs/150
(dangling_intent) + `RESULTS.md` (arg_provenance ~0 real mints); the simulator-magnitude-is-a-guess
lesson held throughout = docs/145; the WARN-only / −9 pp intervention-cost discipline = docs/143 §13
+ docs/144; the per-verifier-slice + feasible-rate attribution discipline = docs/143 §8; the $0
replay precedent = `failure_distribution.py` / `replay_dangling.py` / `replay_stall.py`; the
legibility/triage framing = the FleetHorizon review-fraction story.
