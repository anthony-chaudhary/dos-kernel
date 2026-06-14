# 341 — Smartphone-tier models and the recoverable-fraction curve

> **The claim.** *DOS is worth more on a small model than on a big one — and that
> is measurable, not a slogan.* The kernel is "the part that doesn't believe the
> agents." A weak, on-device model fails in ways the kernel can catch: it says "I
> still need to do X" and stops, it invents an id it never read, it loops on the
> same call. A frontier model that reads before it writes fails mostly in silence —
> it stops because it could not form the step, not because it forgot one. So the
> share of a model's failures that DOS can flag should be HIGH for a phone-sized
> model and LOW for a frontier one. This note names that share — the
> **recoverable fraction** — as a capability coordinate, and points at the
> benchmark that now measures its shape over the live kernel detectors.

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

## 3. The prediction is directional, and the benchmark checks it

The thesis does not predict a magnitude; it predicts a **direction**. As the model
shrinks, the recoverable fraction should RISE. As it grows, the fraction should
fall toward the measured frontier null ([`149`](149_the-real-failure-distribution-sorts-the-priorities.md):
~9% of a strong model's failures are dangling-detectable, ~92% are
premature-but-unreachable).

`benchmark/smartphone_tier/` folds the three real kernel detectors over a corpus
parameterized by param tier (`<=1B` / `1-3B` / `3-7B` / `frontier`) and reports the
curve. On the pre-registered corpus it is a clean monotone fall:

| tier | recoverable fraction |
|---|---|
| `<=1B` (phone) | 80.0% |
| `1-3B` | 65.7% |
| `3-7B` | 40.0% |
| `frontier` | 11.8% |

The monotone fall and the frontier null are **soundness checks the harness asserts**
and the exit code enforces — if the direction ever breaks, the benchmark goes red.
The `frontier` 11.8% sits on the gemini datum: the instrument's self-test.

What is real: every number is folded by the live kernel verdicts (the harness never
re-encodes a detector — pinned by a test), and every declared failure trajectory
genuinely fires its detector. What is a placeholder: the per-tier failure **counts**
are a declared model of the shape, not a measurement. Publishing a simulated guess
as a measured number is what this repo refuses to do ([`145`](145_the-loop-economics-axis-and-the-stall-reader.md)).

## 4. The measurement: drop in on-device recordings

The harness reads the SAME detectors over real data with no code change:

```bash
PYTHONPATH=. python -m benchmark.smartphone_tier.harness \
    --recordings path/to/llama-3.2-1b/runs --tier-name "Llama-3.2-1B"
```

Each recorded run maps to the reduced `Trajectory` datum. Run it once per tier — a
`none`-arm dump from Llama-3.2-1B, Qwen2.5-1.5B, and Phi-3-mini beside a frontier
reference — and the synthetic table becomes a measured one. That is the docs/153 §5
experiment, now at on-device tier and reproducible at $0 for the fold itself. The
only cost is generating the trajectories (a local GGUF run; no API needed).

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
