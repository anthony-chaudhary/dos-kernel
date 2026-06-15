# 341 — Smartphone-tier models and the recoverable-fraction curve

> **The claim (corrected by measurement).** *DOS-recoverability is an inverted-U over
> model capability, and current on-device tool-calling models sit on its peak.* The
> kernel is "the part that doesn't believe the agents." A model has to be competent
> enough to fail *coherently* before a byte-clean detector can read its failure: a
> sub-1B model mints an id on its first call into an empty corpus (uncatchable); a
> frontier model fails silently (uncatchable); but a **1.5–4B tool-tuned on-device
> model** reads before it writes, then hallucinates a foreign key on the write — a
> structured failure the kernel catches. We measured this live on the Qwen2.5 family
> (0.5B→3B on CPU): the recoverable fraction climbs **0% → 50% → 100%** with size. So
> "DOS helps on-device" is not a slogan and not the naïve "smaller = more recoverable"
> — it is the measured fact that the phone-tier tool-calling class is exactly where
> structured, catchable failure lives.

Status: theory + a shipped $0 benchmark (`benchmark/smartphone_tier/`). In the
family of [`123`](123_the-local-model-and-the-independence-coordinate.md) (where a
model runs is a trust fact) and [`153`](153_can-dos-lift-a-weak-model.md) (can DOS
lift a weak model — the recoverable-fraction unit). The synthetic magnitudes here
are a declared pre-registration; the real number arrives when on-device recordings
are folded (§4).

---

## 1. The gap: the suite had no model-capability axis

The benchmark suite measures many DOS claims, but none of them varies the **model**.
Every program runs at one capability point (a frontier `gemini-2.5-flash`, or a
scripted corpus). That hides the single fact the whole trust thesis predicts: the
value of not-believing-the-agent is not constant across models. It should grow as
the agent gets less reliable. A trust substrate is a seatbelt — most useful in the
cars most likely to crash.

"Smartphone-level models" — Llama-3.2-1B, Qwen2.5-1.5B, Phi-3-mini, Gemma-2-2B —
are the high-crash end: small enough to run on a phone, weak enough to fail often.
They are exactly the "genuinely weaker model" that
[`enterpriseops/HANDOFF_next_agent.md`](../benchmark/enterpriseops/HANDOFF_next_agent.md)
named as the decisive missing experiment.

## 2. The unit: the recoverable fraction (docs/153 §5)

The honest unit is the one [`153`](153_can-dos-lift-a-weak-model.md) §5 and
[`weak_model_gate.py`](../benchmark/enterpriseops/weak_model_gate.py) already
defined: of a model's FAILED runs, what share would one of the three shipped
byte-clean detectors advisory-flag?

  * **DANGLE** (`dangling_intent`) — the agent's last words admit an open step and
    nothing ran after. Re-surface its own sentence.
  * **MINT** (`arg_provenance`) — a write references an id that appears in no
    env-authored bytes. Nudge a read first.
  * **LOOP** (`tool_stream`) — the same call returns the same bytes N times. Re-
    surface the value it already holds.

The remainder — a silent stop, a planning miss — is **unreachable**: the detectors
are honestly blind to it. The recoverable fraction is `recoverable / all failures`,
deduped per run, counting only detectors **enriched** on failures (fire more on
failures than on passes — the signal-vs-noise guard).

The load-bearing property: these detectors read trajectory **shape**, not model
identity. So the same fold runs on any model's recordings — synthetic today, real
tomorrow — with no per-model code (docs/153 §5).

## 3. The corrected prediction: an inverted-U, with the on-device class on the peak

The first cut of this note predicted a simple monotone "smaller = more recoverable"
and reported a synthetic 80%. **That was wrong on both counts**, and a real on-device
model run corrects it.

The error: "smaller" was conflated with "fails more in DOS-shaped ways." But a model
has to be *competent enough to fail coherently* before a byte-clean detector can read
its failure. So the real shape over capability is an **inverted-U**:

- **≤0.5B (the incoherence floor):** too weak to emit a usable tool call or to read
  before it writes. It mints an id on its very first call, into an empty corpus, so
  `arg_provenance` has nothing to refute against and ABSTAINs. Recoverable ≈ **0%**.
- **1.5–4B tool-tuned (the on-device sweet spot):** competent enough to read first,
  then fail in a *structured* way — a minted foreign key, a loop, a premature done.
  Structure is what the detectors read, so recoverability **PEAKS** here.
- **frontier (the silent ceiling):** competent enough to fail *silently* — no minted
  id, no loop, no open-obligation cue. Recoverable falls back toward the paper's
  measured ~1.5% (docs/149: ~92% of a strong model's failures are unreachable).

The smartphone tool-calling models people actually deploy (Qwen2.5 0.5–3B, Llama-3.2,
SmolLM3, Phi-4-mini, xLAM) sit **on the rising edge / peak** — the strongest version
of "DOS helps on-device," and now a measured one.

### 3a. The rising edge — a real on-device tool-caller ladder (the headline)

We drove the **Qwen2.5-Instruct family on CPU** (0.5B / 1.5B / 3B — the leading open
small tool-calling models) over a multi-step ITSM task using each model's **native
tool-calling API**, and folded the real kernel detectors. The trajectories are
committed under `benchmark/smartphone_tier/_recordings/`, so it reproduces at $0:

| model | params | recoverable | what it did |
|---|---|---|---|
| SmolLM2-135M | 0.135B | **0%** | incoherent — hallucinated a tool *name* |
| Qwen2.5-0.5B | 0.5B | **0%** | skipped the reads, minted `"user_id"` → empty corpus → uncatchable |
| Qwen2.5-1.5B | 1.5B | **50%** | read first, then minted a fake `USER001` → MINT fires |
| Qwen2.5-3B | 3B | **100%** | read first, then minted `USR0010023` (mangled the incident id) → MINT fires every run |

Recoverability **RISES** 0→0→50→100% with competence — the opposite of the naïve
guess. The mechanism is the competence threshold: `arg_provenance` needs the model to
**read before it writes** so there is a non-empty corpus to refute the minted id
against. A sub-1B model mints on its first call into an empty corpus (uncatchable); a
1.5–3B tool-tuned model does the reads, so its hallucinated foreign key is *caught*.
The competence that makes the model a usable agent is what makes its failure
catchable. (Pinned by `test_ondevice_ladder_recoverability_RISES_with_competence` and
`test_ondevice_mint_is_the_real_failure_shape`.)

### 3b. The falling edge — the Toolathlon frontier corpus (`--corpus`)

Fold the same detectors over the committed Toolathlon replay corpus (7,116 runs, 22
*frontier/large* models) and recoverability **falls** from the lowest-scoring to the
highest-scoring model (14.3% → 1.5%, overall **6.2%** — matching the paper §5 trio
recall of 6.18%). Frontier models fail silently: no minted id, no loop, no cue. This
is the paper's recall ceiling, and it is the inverted-U's right-hand side. (Note: this
corpus's axis is *pass-rate among frontier models*, NOT param size — it has no
on-device model in it. It measures the falling edge, not the rising one.)

### 3c. The synthetic mode (default) — instrument self-test only

The default (no flag) folds the detectors over a SYNTHETIC corpus whose per-tier
counts are a declared shape; it once reported **80%** at the weak tier. **That 80% was
wrong** — both because the counts were invented and because it assumed monotone-rising
rather than the inverted-U. It is kept ONLY as the instrument self-test (it proves the
detectors fold correctly and the directional falsifier fires). **Never cite the 80%**
([`145`](145_the-loop-economics-axis-and-the-stall-reader.md): a declared shape is a
hypothesis; the run is the verdict).

What is real in every mode: each fire is the live kernel detector (the harness never
re-encodes a rule — pinned by a test). What was a placeholder: the synthetic counts,
now superseded by the on-device ladder and the corpus.

## 4. The measurement: drop in on-device recordings

The harness reads the SAME detectors over real data with no code change:

```bash
PYTHONPATH=. python -m benchmark.smartphone_tier.harness \
    --recordings path/to/llama-3.2-1b/runs --tier-name "Llama-3.2-1B"
```

Each recorded run maps to the reduced `Trajectory` datum. `_drive_cpu_model.py` does
this for a real model: it drives a tiny instruct model on CPU (no GPU) over tool-use
tasks and dumps the trajectories. That is the docs/153 §5 experiment, now at on-device
tier and reproducible at $0 for the fold (the only cost is the local CPU generation).

### 4a. The surprise at the extreme low end (a real CPU run)

Driving **SmolLM2-135M-Instruct on CPU** gave 6/6 failures, **0% recoverable** — the
model failed *below the detectors' reach*. It said `DONE` with no open-obligation cue
(so `dangling_intent` abstains) and hallucinated a tool named after the user id rather
than minting an id on a real mutating tool (so `arg_provenance` abstains). So the curve
is **not monotone all the way down**: it rises from frontier toward the weak tiers, then
**falls again at the sub-0.5B extreme**, where failures stop being coherent enough to be
DOS-shaped. The byte-clean detectors need a model competent enough to fail *coherently*.
Silence dominates at **both** ends of the capability axis — frontier (succeeds or fails
cleanly) and sub-0.5B (fails incoherently) — for opposite reasons. That floor is a real
result the synthetic 80% hid, and it sharpens (not weakens) the paper's silent-failure
ceiling.

## 5. Why this matters beyond a benchmark

If the curve holds on real recordings, it is a market argument, not just a research
one: the cheaper and more private the model (the on-device case DOS [`123`](123_the-local-model-and-the-independence-coordinate.md)
already framed as a distinct trust object), the MORE a trust substrate buys you. The
seatbelt is worth most in the cheap car. The positioning half of that argument is a
[`dos-private`](../../dos-private) concern; this note ships the mechanism and the
instrument.

## 6. Litmus

- The recoverable-fraction curve is folded by the live kernel detectors, never a
  re-encoded rule (pinned by `test_kernel_verdict_not_reimplemented`).
- The directional prediction (monotone fall + frontier null) is a soundness check
  the harness asserts and the exit code enforces.
- The same detectors run on synthetic and on real recordings with no per-model code
  (`--recordings`).
- The synthetic magnitudes are captioned as a pre-registration, never as a
  measurement (docs/145).
