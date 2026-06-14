"""smartphone_tier — does the DOS-recoverable failure fraction RISE as the model
shrinks toward on-device / smartphone size? (docs/341, the docs/153 §5 capability
sweep).

The benchmark suite measures many DOS claims, but it has never had a MODEL-CAPABILITY
axis. The thesis the kernel rests on — "the part that doesn't believe the agents" —
predicts that a trust substrate helps a WEAK model more than a strong one: a weak model
fails in DOS-shaped ways (it narrates a step then stops, mints an id from nowhere, loops
on the same read), and those are exactly the failures the three shipped byte-clean
detectors advisory-flag. A frontier model that reads-before-it-writes mints nothing and
stops because it could not form the step, not because it forgot — so the detectors catch
nothing and add nothing (the measured gemini null, docs/149). The prediction is therefore
DIRECTIONAL: the deduped DOS-recoverable failure fraction should be HIGH on a
smartphone-tier model and LOW on a frontier one.

This benchmark folds the SAME three real kernel detectors `weak_model_gate.py` uses —
`dangling_intent`, `tool_stream`, `arg_provenance` — over a corpus parameterized by
PARAM TIER (`<=1B` / `1-3B` / `3-7B` / `frontier`), and reports the recoverable-fraction
curve. The detectors are model-agnostic by construction (they read trajectory SHAPE, not
model identity, docs/153 §5), so the same fold runs on a synthetic tier corpus today and
on real on-device recordings the moment they exist (`--recordings`).

HONESTY (load-bearing, docs/145): the synthetic tier magnitudes are a DECLARED
PRE-REGISTRATION of the failure-mode shape, not a measured result. RESULTS.md captions
them as such. The benchmark's job here is to (a) prove the instrument folds the REAL
kernel verdicts, (b) fix the directional shape the thesis predicts, and (c) be wired so
that pointing `--recordings` at a real Llama-3.2-1B / Qwen2.5-1.5B / Phi-3-mini dump
changes nothing but the data.

Consumer side: imports `dos.dangling_intent` / `dos.tool_stream` / `dos.arg_provenance`
and CALLS them; it never re-encodes a detector rule (pinned by the
kernel-not-reimplemented test). The kernel one-way arrow — nothing under `src/dos/`
imports `benchmark` — is untouched.
"""
