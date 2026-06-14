# DOS benchmarks — start here

> **Just want to run one?** → [`BENCHMARKS.md`](BENCHMARKS.md). The standardized
> runner gives all six a single surface:
> `python -m benchmark._run list | preflight <b> | run <b> [--arm A] | status`.
> It shells the existing entrypoints (never reimplements them), resolves a named
> arm to its `DOS_*` env via the shared [`_arms.py`](_arms.py) vocabulary, loads
> the Gemini key from `.env` for paid arms, and stamps each run with the kernel
> SHA so `status` can flag stale numbers. The full run-surface inventory behind it
> is [`_BENCH_MAP.md`](_BENCH_MAP.md).

This directory holds **six independent research programs**, each measuring a
different DOS claim against a real corpus. They share no measurement code path
(only the run *surface* above); pick the one that matches your question. The four
tabled below are the load-bearing programs; `fleetforge/` (newest fleet-collision
sim) and the two offline detector replays (`agenthallu/`, `agentprocessbench/`)
are in `list`. The numbered design notes that interpret these runs live in
[`../docs/`](../docs/) (see [`../docs/README.md`](../docs/README.md)'s research-arc
tables and [`../docs/ENTERPRISEOPS_ARC.md`](../docs/ENTERPRISEOPS_ARC.md)).

| Benchmark | Research question | Read first | Numbers | Vocabulary |
|---|---|---|---|---|
| **`enterpriseops/`** | Does the intervention ladder (OBSERVE/WARN/DEFER/BLOCK) and `arg_provenance` *lift* a model on ServiceNow ITSM tasks? | [`THEORY_LADDER.md`](enterpriseops/THEORY_LADDER.md) (how the tiers run) → [`HANDOFF_next_agent.md`](enterpriseops/HANDOFF_next_agent.md) (what's next) | [`RESULTS.md`](enterpriseops/RESULTS.md) | — |
| **`fleet_horizon/`** | Open-loop vs closed-loop fleet integrity — how many lies/overwrites does adjudication catch, at what review cost? | [`README.md`](fleet_horizon/README.md) | [`RESULTS.txt`](fleet_horizon/RESULTS.txt) | — |
| **`toolathlon/`** | Do byte-clean in-flight detectors (`dangling_intent` / `tool_stream` / `terminal_error`) fire on a third-party-scored trajectory corpus, and do they convert to a FIX? | [`EXPLAINER.md`](toolathlon/EXPLAINER.md) (first-time) → [`AB_RUN_RECIPE.md`](toolathlon/AB_RUN_RECIPE.md) (run the A/B) → [`HANDOFF.md`](toolathlon/HANDOFF.md) (state) | (in `_results/`) | [`GLOSSARY.md`](toolathlon/GLOSSARY.md) — byte-author / byte-clean / net-new / additivity / SSOT |
| **`iot_tier/`** | Across a model-size ladder (frontier→mid→small→IoT-class), where does DOS's recoverable-failure fraction PEAK and where does it COLLAPSE — is the proof point the weakest model or the middle one? | [`README.md`](iot_tier/README.md) | [`RESULTS.md`](iot_tier/RESULTS.md) | calibrated-sim / recoverable-fraction / the gemini-null self-test |

(Many more $0 programs are in `python -m benchmark._run list` — the table above is the
load-bearing subset, not the full inventory; the inventory is [`_BENCH_MAP.md`](_BENCH_MAP.md).)

## Reading order by audience

- **Kernel developer tuning intervention costs** → `enterpriseops/THEORY_LADDER.md` then `RESULTS.md`.
- **Fleet operator validating orchestrator trust** → `fleet_horizon/README.md` then `RESULTS.txt`.
- **Detector researcher measuring DETECT→FIX** → `toolathlon/EXPLAINER.md`, then `GLOSSARY.md` for the vocabulary, then `AB_RUN_RECIPE.md` to reproduce.
- **"Does DOS help small/edge (IoT-class) models?"** → `iot_tier/README.md` (the rise-then-collapse curve + the honesty contract) then `iot_tier/RESULTS.md`.

## Notes

- **Live-run outputs are scratch, not source.** The `live_results*/`, `_results/`,
  and the cloned `enterpriseops-gym/` are `.gitignore`d (large, re-derivable). The
  harness code and the *scored summaries* (the docs above + the numbered
  `docs/NN_*.md`) are tracked; the raw trajectory dumps are not. See the root
  [`.gitignore`](../.gitignore).
- **`enterpriseops/_PAIRED_RESULT.md` is superseded** — a small (n=20) rewind A/B
  whose favorable draw was refuted by a larger run. Read
  [`../docs/172_the-rewindable-fix-loop-experiment.md`](../docs/172_the-rewindable-fix-loop-experiment.md)
  §3.5 / §9 for the settled result, not that file in isolation.
- These benchmarks are **consumers** of the kernel, never part of it — the same
  one-way arrow as the MCP server and the release tooling (see
  [`../CLAUDE.md`](../CLAUDE.md)).
