# smartphone_tier — does DOS help more as the model shrinks toward a phone? (docs/341)

<!-- dos-bench-stamp: kernel=0.26.0 sha=5422122 date=2026-06-14 -->

> **The question:** DOS is "the part that doesn't believe the agents." If that is
> worth anything, it should be worth MORE on a weak, on-device model than on a
> frontier one — because a weak model fails in ways DOS can flag (it narrates a
> step then stops, invents an id it cannot resolve, loops on the same read), while
> a frontier model that reads-before-it-writes mostly fails SILENTLY, where the
> byte-clean detectors are blind. This benchmark measures that prediction as a
> **capability curve**: the DOS-recoverable failure fraction across param tiers
> from `<=1B` (smartphone) to `frontier`.

Run (free — no model, no network, no Docker; the real kernel detectors only):

```bash
PYTHONPATH=. python -m benchmark.smartphone_tier.harness          # the curve table
PYTHONPATH=. python -m benchmark.smartphone_tier.harness --json   # machine-readable
```

## The headline (synthetic pre-registration corpus)

The deduped DOS-recoverable failure fraction — the share of FAILED runs that at
least one **enriched** byte-clean detector advisory-flags — falls monotonically as
the model grows:

| tier | exemplars | failed runs | recoverable | unreachable | **recoverable fraction** |
|---|---|---|---|---|---|
| **`<=1B`** | Llama-3.2-1B, Qwen2.5-0.5B/1.5B | 40 | 32 | 8 | **80.0%** |
| `1-3B` | Phi-3-mini, Qwen2.5-3B, Gemma-2-2B | 35 | 23 | 12 | **65.7%** |
| `3-7B` | Llama-3.1-8B, Qwen2.5-7B, Mistral-7B | 30 | 12 | 18 | **40.0%** |
| `frontier` | gemini-2.5-flash, frontier cloud | 34 | 4 | 30 | **11.8%** |

The smartphone-tier model's failures are **majority DOS-recoverable** (80%); the
frontier model's are **almost all unreachable** (88%). The `frontier` 11.8% lands
right on the measured gemini null (docs/149: ~9% dangling-detectable, ~92%
premature-but-unreachable) — the instrument's self-test.

### Per-detector fire-rate on FAILED runs (the enrichment guard)

A detector counts toward the recoverable fraction **only if it is enriched** on
failures (fires more on failures than on passes); otherwise it is excluded as
noise. This is the `weak_model_gate.py` signal-vs-noise honesty.

| tier | DANGLE (`dangling_intent`) | MINT (`arg_provenance`) | LOOP (`tool_stream`) |
|---|---|---|---|
| `<=1B` | 35% | 25% | 20% |
| `1-3B` | 29% | 20% | 17% |
| `3-7B` | 20% | 10% | 10% |
| `frontier` | 9% | 0% *(excluded — noise)* | 3% |

On `frontier` the MINT detector never fires (a strong model mints no ids) and is
correctly dropped from the recoverable count — the fraction is not inflated by a
detector that adds nothing.

## What is real here, and what is a placeholder

**Real:** every number above is folded by the **live kernel detectors** —
`dos.dangling_intent.classify_stop`, `dos.arg_provenance.classify_call`,
`dos.tool_stream.classify_stream` — over each trajectory. The harness never
re-encodes a detector rule (pinned by
`tests/test_smartphone_tier_bench.py::test_kernel_verdict_not_reimplemented`), and
every declared failure trajectory genuinely fires its detector while every
silent/passed one fires none (pinned by `test_each_declared_kind_actually_fires_its_detector`).
The directional shape — the monotone fall and the frontier null — is a soundness
check the harness asserts and the exit code enforces.

**A placeholder (honest, docs/145):** the *magnitudes* — how many failures of each
kind a tier has — are a **declared pre-registration of the failure-mode shape**,
not a measurement. No model was run; the corpus is synthetic
(`tiers.py::default_tiers`). Publishing a simulated guess as a measured number is
exactly what this repo refuses to do. The shape is grounded (smaller tier ⇒ more
DOS-shaped failures + more silent ones; frontier ≈ the gemini datum), but the
real curve is the next experiment.

## The measurement — drop in real on-device recordings

The harness reads the SAME detectors over real data with no code change:

```bash
# point it at a directory of recorded on-device model runs (one JSON per run):
PYTHONPATH=. python -m benchmark.smartphone_tier.harness \
    --recordings path/to/llama-3.2-1b/runs --tier-name "Llama-3.2-1B"
```

Each record maps to the reduced `Trajectory` datum (the loader is tolerant: a
record missing a stream just yields no LOOP signal; a leading BOM is handled). Run
it once per tier — e.g. a `none`-arm dump from Llama-3.2-1B, Qwen2.5-1.5B, and
Phi-3-mini next to a frontier reference — and the synthetic table above becomes a
measured one. This is the docs/153 §5 / `enterpriseops/HANDOFF_next_agent.md`
"genuinely weaker model" experiment, now on-device-tier and reproducible at $0 for
the detector fold.

## Reading order

- **docs/341** — the design note: smartphone-tier as a measurable capability
  coordinate on the DOS lift thesis.
- **docs/123** — where a model runs is a trust coordinate, not a deployment detail.
- **docs/153 §5 / docs/149** — "can DOS lift a weak model?" and the measured
  gemini failure distribution the frontier null is calibrated to.
- **`benchmark/enterpriseops/weak_model_gate.py`** — the recoverable-fraction unit
  and the enrichment guard this benchmark reuses (the discipline, not the code).
