"""skill_dos_ablation — does a `dos-skillify`-converted skill over-claim less? (issue #176, docs/345 §6)

The product claim of `dos-skillify` (docs/345): *a DOS-aware skill over-claims less.* That claim
must be MEASURED, not asserted — the kernel's own discipline (docs/333) applied to its own growth
pitch. This package is the ablation harness.

It is a CPU-only, no-API-key, fully deterministic replay over a COMMITTED fixture corpus. The
over-claim detection is a deterministic function over fixture trajectory bytes — NOT a live LLM run
(this worker has no model/key). It is the docs/341/iot_tier instrument (a calibrated synthetic
corpus + real byte-clean witness logic, exit-coded falsifier) re-aimed at the *skill* layer instead
of the *model* layer. Nothing here is imported by `src/dos/` (the one-way consumer arrow).
"""
