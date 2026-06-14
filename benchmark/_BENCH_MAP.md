# Benchmark run-surface map — the inventory behind standardization

> Authoritative, read directly off disk on 2026-06-06 (witness_ladder added
> 2026-06-09, docs/261). Feeds the `dos bench` standardization design. The point
> this map makes: **many programs, many different run rituals, many results
> conventions, env-knob soup** — and yet one pattern
> (`live_ab.py`'s `ARMS` dict) already shows the cure. Lift that pattern to a
> shared declarative manifest.

## Prereq state on THIS machine (2026-06-06)

- Docker: **28.3.3, up** ✓
- `enterpriseops-gym`: **cloned** ✓
- `GEMINI_API_KEY`: **present in repo-root `.env`** ✓ — BUT no benchmark calls
  `load_dotenv`, so it is **not in `os.environ`** → paid arms silently fail on an
  unset key unless `.env` is sourced first. (Standardization fix: preflight loads `.env`.)
- `dos` kernel: installed, **v0.13.0**.
- Proven $0 runs this session: `toolathlon.run_replay --list` (exit 0, 30+ cached
  trajectories), `fleetforge.run_smoke --efforts 2 --phases 2 --seed 1 --json` (exit 0).

## The programs

### 1. `toolathlon/` — byte-clean detectors on a third-party-scored corpus  ($0 replay + paid live A/B)
- **Q:** do `dangling_intent`/`tool_stream`/`terminal_error` fire on frozen ICLR-2026
  Toolathlon trajectories, and does a WARN re-surface convert fail→pass live?
- **Free entrypoint:** `python -m benchmark.toolathlon.run_replay --all [--rows-out R] [--out O]`
  (cached corpus; `--no-download` for offline; knobs `--ts-min-state`, `--te-recovery`, `--raw-digest`, `--by-model`).
- **Paid entrypoint:** the WSL2/Docker live A/B — `_ab_wiring/run_ab.sh <reps>` (OBSERVE vs WARN,
  gemini-2.5-pro, ~$10-25). Arm delta = `DOS_WARN` flag only. Scorer: `live_adapter.py`.
- **Prereqs:** free = cached `_data/` corpus, no key. paid = Docker/WSL2 + Gemini key + serper.
- **Env knobs:** `DOS_WARN` (live arm on), `TOOLATHLON_CACHE`.
- **Output:** `_results/` (gitignored), scored summaries in EXPLAINER/docs/157-158.
- **Freshness:** replay is reproducible anytime; live A/B was *running* per HANDOFF (N=6×reps pilot).

### 2. `enterpriseops/` — the intervention ladder on ServiceNow ITSM  (TIERED $0→paid)
- **Q:** does the OBSERVE/WARN/DEFER/BLOCK ladder + `arg_provenance` LIFT a model?
- **THE STANDARDIZATION KEYSTONE:** `live_ab.py` already has an **`ARMS` dict** (lines 62-104)
  mapping named arms → `DOS_*` env. This is the pattern to generalize:
  | arm | env it sets |
  |---|---|
  | `none` | `DOS_CONSULT=0` |
  | `defer` | `DOS_CONSULT=1 DOS_INTERVENTION=DEFER` |
  | `warn` | `DOS_CONSULT=1 DOS_INTERVENTION=WARN` (docs/143 live winner) |
  | `block` | `DOS_CONSULT=1 DOS_INTERVENTION=BLOCK` |
  | `rewind` | `…BLOCK DOS_REWIND=1` |
  | `rewind_natural` | `DOS_CONSULT=0 DOS_REWIND_NATURAL=1` |
  | `stall` | `DOS_CONSULT=0 DOS_STALL=1` |
  | `resurface` | `DOS_CONSULT=0 DOS_DANGLING=1` |
  | `restart` / `restart_seeded` | `…BLOCK DOS_RESTART=1 [DOS_RESTART_SEED=1]` |
- **Tier ladder (THEORY_LADDER.md):** Tier0 `intervention_theories` ($0 sim) → Tier0b `intervention_ab`
  → Tier1 `replay_recall --results <dir>` ($0, needs prior artifacts) → Tier2/3/4 `live_ab.py --tasks 3|12|55` (paid).
- **Free entrypoints:** `intervention_theories`, `intervention_ab`, `replay_recall`, `replay_stall`,
  `replay_dangling`, `dangling_convert`, `cause_locality`, `failure_distribution`, `restart_counterfactual`,
  `abandon_counterfactual`, `natural_thrash_counterfactual` (all $0 sim/replay/counterfactual).
- **Paid:** `live_ab.py [--tasks N --arms … --domains … --mint-rate … --mint-seed …]` + `run_ab.py`, `sample_and_run.py`.
- **Prereqs:** paid = Docker (4 MCP servers email/itsm/csm/hr) + Gemini key + cloned gym.
- **Env knobs:** `DOS_CONSULT DOS_INTERVENTION DOS_WARN_ONLY DOS_DANGLING DOS_PRECURSOR
  DOS_PRECURSOR_GRAMMAR DOS_REWIND DOS_REWIND_NATURAL DOS_STALL DOS_TERMINAL_ERROR DOS_RESTART
  DOS_RESTART_SEED DOS_MINT_RATE DOS_MINT_SEED DOS_CONSULT`. (`live_ab.py:114` pops these between arms —
  the process-wide contamination bug docs/152 flagged.)
- **Output:** `RESULTS.md` (committed) + **8 `live_results*/` dirs** (gitignored): `live_results`,
  `_natural`, `_natural_ab`, `_natural_run`, `_natural_smoke`, `_rewind_ab`, `_rewind_paired`,
  `_rewind_pilot`, `_rewind_smoke`, `_rewind_smoke2`. **← the results-chaos exhibit.**
- **Freshness:** RESULTS.md = docs/143 strong-model run; the natural-thrash / rewind / restart /
  cause-locality work (docs/172/176, c0d681e) is newer than RESULTS.md and lives only in docs.

### 3. `fleet_horizon/` — open-loop vs closed-loop fleet integrity  ($0 sim, drives REAL kernel)
- **Q:** how many lies/overwrites does adjudication catch, at what review cost, over horizon×fanout?
- **Entrypoints:** `python -m benchmark.fleet_horizon.harness [--efforts N --phases M --seed S
  --shared-ratio --lie-rate --sweep --velocity-sweep --orchestrator-sweep --json]`;
  `measure_real_collisions.py`; `live_demo.py` (gated by `DOS_LIVE_DEMO`); `plot.py`.
- **Prereqs:** none — pure simulator over a real temp git repo + real `dos.oracle/arbiter/run_id/lane_journal`.
- **Env knobs:** `DISPATCH_LANE_JOURNAL_PATH` (set internally), `DOS_LIVE_DEMO`.
- **Output:** `RESULTS.txt` (committed), `build/` (gitignored).
- **Freshness:** reproducible anytime; RESULTS.txt is the committed numbers.

### 4. `fleetforge/` — do real LLM fleets collide on shared state, and does the skill arm capture it  ($0 sim keystone + paid)
- **Q:** measurable collision/over-claim rate that the DOS-skills arm prevents attributably?
- **Entrypoints:** `python -m benchmark.fleetforge.run_smoke [--efforts --phases --shared-ratio
  --disjoint --seed --lie-rate --flake-rate --thrash-rate --model --json]`; `skill_adherence.py` (the $0 keystone).
- **Prereqs:** free = sim; the keystone is $0. `--model` implies a paid live arm.
- **Output:** stdout JSON (no committed summary dir yet — NEW benchmark).
- **Freshness:** newest program; no committed RESULTS file.

### 5. `agenthallu/` — hallucination-step localization on AgentHallu  ($0 offline detector)
- **Q:** does the byte-clean structural+recovery detector localize the gold hallucination step?
- **Entrypoints:** `python -m benchmark.agenthallu.dataset` (corpus summary);
  `python -m benchmark.agenthallu.scoring [--check] [--emit]` (SSOT scorer).
- **Prereqs:** **dataset = sibling clone** of `github.com/liuxuannan/AgentHallu`, resolved
  `$AGENTHALLU_ROOT` › `../AgentHallu/AgentHallu`. No key, no network.
- **Env knobs:** `AGENTHALLU_ROOT`.
- **Output:** `_results/` (gitignored) + the claims ledger via `--emit`; numbers in docs/173-174.
- **Freshness:** offline + deterministic; re-runs identically.

### 6. `agentprocessbench/` — error-localization BOUNDARY on AgentProcessBench  ($0 offline detector)
- **Q:** where does the deterministic ORACLE rung end and the JUDGE rung take over (the ~11-27% boundary)?
- **Entrypoints:** `python -m benchmark.agentprocessbench.dataset`;
  `python -m benchmark.agentprocessbench.scoring [--check] [--emit]`.
- **Prereqs:** **dataset = sibling clone** of HF `LulaCola/AgentProcessBench`, resolved
  `$AGENTPROCESSBENCH_ROOT` › `../AgentProcessBench` › fallback. 4 configs bfcl/tau2/gaia_dev/hotpotqa. No key.
- **Env knobs:** `AGENTPROCESSBENCH_ROOT`.
- **Output:** `_results/` (gitignored); numbers in docs/174.
- **Freshness:** offline + deterministic.

### 7. `witness_ladder/` — value vs WITNESS STRENGTH; the §3 wall as an instrument  ($0 sim, drives REAL kernel)
- **Q:** how does the write-admission verdict's value change as the witness rung
  weakens — where does the value end and the abstain band (docs/204 §3) begin?
- **The axis no other benchmark varies.** The six above hold the witness fixed and
  vary fleet size / model / detector / dataset / arm. This one sweeps the
  `Accountability` rung (`AGENT_AUTHORED` → `OS_RECORDED` → `THIRD_PARTY`) and calls
  the REAL kernel verdict `dos.reward.admit` per rung over a fixed declared
  distribution. Emits one monotone J(rung) curve: the rising arm is the VALUE
  (poison purged where a non-forgeable witness exists), the floor abstain band is
  the GROWTH FRONTIER (the §3 wall, quantified + decomposed into the witnesses that
  would convert it). docs/261.
- **Entrypoint:** `python -m benchmark.witness_ladder.harness [--json]` (the `sweep`
  arm). $0, no prereq — pure sim over the real `dos.reward`/`dos.evidence`.
- **Output:** the curve + roadmap to stdout (ASCII always-on; `--json` for machines);
  no committed RESULTS file (newest program). Falsifier asserted in-band: J(floor)==0,
  J monotone, precision==1.0 (exit non-zero if any trips).
- **Freshness:** reproducible anytime (deterministic distribution, no `random`).

### 8. `iot_tier/` — the model-size ladder; where DOS's recoverable fraction peaks/collapses  ($0 calibrated sim, drives the REAL detectors)
- **Q:** across frontier→mid→small→IoT-class, where does the DOS-recoverable failure fraction
  PEAK and where does it COLLAPSE — is the proof point the weakest model or the *middle* one
  (docs/153 §1–§2)?
- **The axis docs/153 named but never swept.** `weak_model_gate.py` self-validated the gate on
  ONE corpus (the gemini null, 13% < 15%). This program turns that single-corpus gate into a
  **sweep across a declared model-size ladder**, folding the SAME validated enrichment logic
  (`weak_model_gate.gate_fraction`, extracted) over a synthetic, declared-and-cited per-tier
  failure mix. Emits the **non-monotone** recoverable-fraction curve (rises to a middle peak,
  collapses at IoT — the docs/153 §1 `can-do-when-nudged` decay made visible).
- **Entrypoint:** `python -m benchmark.iot_tier.harness [--runs N --seed S --json]`. $0, no
  prereq — pure replay over the real `dos.dangling_intent` / `dos.tool_stream` / `arg_provenance`
  detectors. The frontier tier self-tests against the gemini null; the in-band falsifier exits
  non-zero if the curve is flat/monotone.
- **Output:** the ASCII curve (+ `--json`); committed summary `iot_tier/RESULTS.md`.
- **Honesty:** a CALIBRATED SIM — the per-tier failure mix is a *declared input* (`tiers.py`),
  cited to docs/153, not a measured magnitude. The measurement that replaces it is the docs/153
  Stage-0 ~$50 real IoT-corpus run (tracked as a GitHub issue).
- **Freshness:** deterministic (seed 1729), reproducible anytime.

## The cross-cutting problems a standard runner must fix

1. **Env-knob soup.** ~14 `DOS_*` knobs + 2 `*_ROOT` + cache vars. `DOS_WARN` (toolathlon)
   vs `DOS_WARN_ONLY` (enterpriseops) vs `DOS_INTERVENTION=WARN` — three spellings of "warn."
   **Cure:** named *arms* in a manifest (the `live_ab.py:ARMS` pattern, lifted shared).
2. **Results chaos.** `RESULTS.md` / `RESULTS.txt` / `_results/` / **10 `live_results*/` dirs** /
   stdout-only (fleetforge). No run stamps the kernel SHA / model / arm / date.
   **Cure:** one convention — raw → a stamped run-dir; scored → one `RESULTS.md` per benchmark;
   every run records `{kernel_sha, date, arm, model, prereq_state}`.
3. **Prereq gap.** Datasets are sibling clones with 3-way fallback; the Gemini key is in `.env`
   but never loaded. **Cure:** a `preflight` that loads `.env`, checks docker/key/dataset/gym and
   fails LOUD + EARLY for paid arms, with the exact fix command.
4. **No single entry.** Six `python -m …` rituals. **Cure:** `<runner> list | preflight | run <bench> [--arm A] | status`.
5. **Freshness invisible.** Committed numbers (RESULTS.md = strong-model docs/143) are stale vs
   newer doc-only work (docs/172/176/190/191). **Cure:** `status` diffs each RESULTS' stamped
   kernel SHA against `git rev-parse HEAD`.
