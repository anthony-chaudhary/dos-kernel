# smartphone_tier — how DOS-recoverable are real on-device tool-calling models? (docs/341)

<!-- dos-bench-stamp: kernel=0.26.0 sha=5422122 date=2026-06-14 -->

> **The question, corrected.** The first cut asked "does DOS help MORE as a model
> shrinks?" and answered with a synthetic 80%. That was wrong twice: (1) it conflated
> "small on-device model" with "frontier model that scores low on a hard benchmark,"
> and (2) the magnitude was invented. The right question is about the models people
> actually run on a phone for agents — **current small tool-calling models** (Qwen2.5
> 0.5–3B, Llama-3.2, SmolLM3, Phi-4-mini, xLAM). When you measure THOSE, the answer
> inverts: DOS-recoverability **RISES with competence** across the on-device band, then
> falls again at frontier. An **inverted-U**, and the on-device tool-callers sit on its
> rising edge.

## THE HEADLINE — a real on-device model ladder, run on CPU

We drove the **Qwen2.5-Instruct family on CPU** (no GPU) — the leading open small
tool-calling models — over a multi-step ITSM task using each model's **native
tool-calling API** (`<tool_call>` tags), then folded the real kernel detectors. The
trajectories are committed under [`_recordings/`](_recordings/) so this reproduces at
$0 with no model:

```bash
PYTHONPATH=. python -m benchmark.smartphone_tier._ladder \
    0.5:Qwen2.5-0.5B:benchmark/smartphone_tier/_recordings/q05 \
    1.5:Qwen2.5-1.5B:benchmark/smartphone_tier/_recordings/q15 \
    3.0:Qwen2.5-3B:benchmark/smartphone_tier/_recordings/q3
```

| model | params | failed | **recoverable fraction** | what the model did |
|---|---|---|---|---|
| SmolLM2-135M | 0.135B | 6/6 | **0%** | incoherent — hallucinated a tool *name*; no valid call |
| Qwen2.5-0.5B | 0.5B | 6/6 | **0%** | one well-formed call, but **skipped the reads** and minted `user_id="user_id"` → empty corpus → uncatchable |
| Qwen2.5-1.5B | 1.5B | 6/6 | **50%** | **read first** (`get_incident`→`get_user`), then minted a fake `USER001` on the write → **MINT fires** |
| Qwen2.5-3B | 3B | 6/6 | **100%** | read first every time, then minted `USR0010023` (mangled the incident id) on the write → **MINT fires every run** |

**Recoverability RISES with competence: 0% → 0% → 50% → 100%.** This is the *opposite*
of the naïve "weaker = more recoverable" guess, and it is the real, measured answer.

### Why it rises — the competence threshold

`arg_provenance` (MINT) catches a mutating call whose id argument appears in **no
env-authored byte** — a hallucinated foreign key. To catch it, the detector needs a
**non-empty corpus** of prior reads to refute the id against. So:

- **≤0.5B** mints on its *first* call (it skips the reads), into an **empty** corpus.
  `arg_provenance` correctly ABSTAINs (it cannot prove mintage with zero env bytes).
  The model fails, but in the detector's blind spot.
- **≥1.5B** is competent enough to **read before it writes** — `get_incident`,
  `get_user`, *then* `assign_incident`. Now the corpus is non-empty, the minted user id
  appears nowhere in it, and the kernel says **do not believe this call**. The very
  competence that makes the model a usable agent is what makes its failure *catchable*.

This is a genuine, citable property: **DOS needs the model to be good enough to fail
coherently.** Below that threshold the failures are real but structureless; above it
they are structured, and structure is exactly what a byte-clean detector reads.

### The 3B mint, verbatim (a real on-device failure DOS catches)

```
get_incident(INC0010023) -> {"status":"open","assignee":null,"team":"network"}
get_user(...)            -> {"error":"user not found"}
assign_incident(incident_id="INC0010023", user_id="USR0010023")   # <- minted: mangled the incident id
```
`USR0010023` appears in no tool result. `arg_provenance.classify_call(...).believe ==
False`. A peer that inherited this "assignment" would build on a phantom; the gate
refuses it. (Pinned by `test_ondevice_mint_is_the_real_failure_shape`.)

## The full inverted-U (the on-device rising edge + the frontier falling edge)

The ladder above is the **rising edge**. The **falling edge** is the paper's existing
result: fold the same detectors over the Toolathlon replay corpus (7,116 runs, 22
*frontier/large* models — `--corpus`) and recoverability **falls** from the
lowest-scoring to the highest-scoring model (14.3% → 1.5%, overall **6.2%** — matching
the paper §5 trio recall of 6.18%). Frontier models fail **silently**: no minted id, no
loop, no open-obligation cue, so nothing to read.

Put the two together and the shape is an **inverted-U** over capability:

```
 recoverable
  fraction
   100% |                 * 3B
        |
    50% |          * 1.5B
        |                        (Toolathlon frontier band, --corpus)
    ~6% |                          o o o o o
     0% | *135M *0.5B                          (frontier ~1.5%)
        +----------------------------------------------
          incoherent   tool-tuned        silent
            floor      on-device         frontier
                       (DOS sweet spot)
```

- **Left of the peak (≤0.5B):** too weak to fail coherently → uncatchable.
- **The peak (1.5–4B tool-tuned, the on-device class):** competent enough to fail in
  *structured* ways (minted ids, loops, premature done) → **most DOS-recoverable**.
- **Right of the peak (frontier):** competent enough to fail *silently* → uncatchable
  again (the paper's recall ceiling).

The smartphone tool-calling models people actually deploy sit **on the peak** — which
is the strongest version of the "DOS helps on-device" claim, and a measured one.

## Honest edges

- **n is small.** 4 models × 6 runs on 2 tasks, greedy decoding. This is an
  *existence + direction* result (recoverability rises 0→100% across the on-device
  band, by a mechanism we can name), not a calibrated rate. The committed fixtures let
  anyone re-fold or extend it.
- **One task family.** The mint catch is on an assign-after-lookup task; a task with no
  mutating call would surface dangle/loop instead. The point is the *mechanism* (read
  competence gates catchability), not this exact 100%.
- **CPU greedy ≠ phone runtime.** The models are the real weights; the harness is a
  scripted tool world, not a production agent stack. The failure *shapes* are what
  transfer.
- **The synthetic mode (default) is the instrument self-test only.** Its 80% is a
  refuted pre-registration — kept solely to prove the detectors fold correctly and the
  directional falsifier fires. **Never cite the 80%.**

## Reproduce / extend

```bash
# fold the committed on-device fixtures (no model needed, $0):
PYTHONPATH=. python -m benchmark.smartphone_tier._ladder \
    0.5:Qwen2.5-0.5B:benchmark/smartphone_tier/_recordings/q05 \
    1.5:Qwen2.5-1.5B:benchmark/smartphone_tier/_recordings/q15 \
    3.0:Qwen2.5-3B:benchmark/smartphone_tier/_recordings/q3

# generate a NEW model's runs on CPU (needs torch + transformers):
python -m benchmark.smartphone_tier._drive_cpu_model --model Qwen/Qwen2.5-1.5B-Instruct --out /tmp/q15
python -m benchmark.smartphone_tier.harness --recordings /tmp/q15 --tier-name Qwen2.5-1.5B

# the frontier falling edge (committed Toolathlon corpus):
PYTHONPATH=. python -m benchmark.smartphone_tier.harness --corpus
```

## Reading order

- **docs/341** — the design note: the inverted-U and the competence threshold.
- **paper §5 (`paper/sections/05_detectors.html`)** — the recall ceiling and
  `fig1_purchase_vs_capability.png` (the falling edge); this benchmark adds the rising
  edge on real on-device tool-callers.
- **docs/123** — where a model runs is a trust coordinate, not a deployment detail.
- **`benchmark/enterpriseops/weak_model_gate.py`** — the recoverable-fraction unit and
  the enrichment guard this reuses.
