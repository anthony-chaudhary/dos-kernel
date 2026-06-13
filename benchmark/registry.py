"""The benchmark registry — the single typed inventory of all DOS benchmark
programs, their arms, prereqs, and runnable entrypoints.

This is the declarative core the standardized runner (`benchmark/_run.py`) reads.
It lives under `benchmark/` (the CONSUMER side), never under `src/dos/` — so it
may import benchmark internals freely (e.g. the shared arm vocabulary in
`_arms`), and the kernel one-way arrow (nothing in `src/dos/*.py` imports
`benchmark`) is untouched. Pinned by `tests/test_bench_layering.py`.

Each `BenchSpec` answers, for one benchmark:
  - what question it measures (one line),
  - which named ARMS it supports (each resolving to DOS_* env via `_arms`),
  - what PREREQS a run needs (docker / api key / dataset / gym / none),
  - the runnable ENTRYPOINTS (free $0 vs paid), as argv lists for subprocess.

The runner turns a `(bench, arm)` request into: preflight the prereqs → set the
arm's env (popping all DOS_* knobs first) → subprocess the entrypoint → stamp a
run record. The operator never sets a DOS_* variable by hand.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from _arms import arm_env  # benchmark/ is on sys.path when run as `python -m benchmark._run`

# ---------------------------------------------------------------------------
# Prereq kinds — a closed vocabulary the preflight checker understands.
# ---------------------------------------------------------------------------
DOCKER = "docker"          # a Docker daemon must be up
API_KEY = "api_key"        # an API key must be resolvable (from .env or environ)
DATASET = "dataset"        # a sibling-clone corpus must exist (resolved via *_ROOT)
GYM = "gym"                # the enterpriseops-gym must be cloned
NONE = "none"              # no external prereq — pure sim / cached replay

PREREQ_KINDS = {DOCKER, API_KEY, DATASET, GYM, NONE}


@dataclass(frozen=True)
class Prereq:
    kind: str                       # one of PREREQ_KINDS
    detail: str = ""                # e.g. the env var name for API_KEY, the *_ROOT for DATASET
    fix: str = ""                   # the exact command to satisfy it, shown on failure


@dataclass(frozen=True)
class Entrypoint:
    name: str                       # the arm/tier name a user asks for, e.g. "replay", "theories"
    argv: List[str]                 # the subprocess argv (module form), {tokens} filled at run time
    cost: str = "free"              # "free" ($0) or "paid"
    does: str = ""                  # one-line description
    prereqs: Tuple[Prereq, ...] = ()
    arm: str = ""                   # if set, the _arms arm whose DOS_* env this entrypoint runs under
    # default {token} substitutions for the argv (overridable on the CLI via --set k=v)
    defaults: Dict[str, str] = field(default_factory=dict)
    # SCRIPT-FORM LAUNCH (the gym-collision escape hatch). Default launch is module
    # form: `python -m <argv[0]> <argv[1:]>`. But the EnterpriseOps gym ships its OWN
    # top-level `benchmark/` package (enterpriseops-gym/benchmark/executor.py), so
    # `python -m benchmark.enterpriseops.live_ab` makes our `benchmark` shadow the
    # gym's and `from benchmark.executor import ...` fails. For such an entrypoint,
    # set `script` (a path relative to repo root) + `cwd` (a dir relative to repo
    # root): the runner then launches `python <abs script> <argv[1:]>` from <cwd>,
    # exactly the script-form invocation the gym arm was designed for (THEORY_LADDER.md).
    script: str = ""                # e.g. "benchmark/enterpriseops/live_ab.py" — launch in script form
    cwd: str = ""                   # e.g. "benchmark/enterpriseops" — run the subprocess from here


@dataclass(frozen=True)
class BenchSpec:
    name: str
    question: str                   # the research question, one sentence
    results_summary: str            # the committed scored-summary path (relative to repo root), or ""
    entrypoints: Tuple[Entrypoint, ...]

    def entry(self, name: str) -> Entrypoint:
        for e in self.entrypoints:
            if e.name == name:
                return e
        raise KeyError(f"benchmark {self.name!r} has no entrypoint {name!r}; "
                       f"have {[e.name for e in self.entrypoints]}")

    def free_default(self) -> Entrypoint:
        """The cheapest free entrypoint — what `run <bench>` does with no --arm."""
        for e in self.entrypoints:
            if e.cost == "free":
                return e
        return self.entrypoints[0]


# A Gemini key satisfiable from the repo-root .env (loaded by the preflight).
_GEMINI = Prereq(API_KEY, "GEMINI_API_KEY",
                 "put GEMINI_API_KEY in repo-root .env (preflight loads it)")
_DOCKER = Prereq(DOCKER, "", "start Docker Desktop / dockerd")
_GYM = Prereq(GYM, "benchmark/enterpriseops/enterpriseops-gym",
              "clone ServiceNow/enterpriseops-gym into benchmark/enterpriseops/ (see THEORY_LADDER.md)")
_AGENTDIFF = Prereq(DATASET, "AGENT_DIFF_ROOT",
                    "clone agent-diff as a sibling (default ../agent-diff) or set AGENT_DIFF_ROOT; "
                    "the frozen dry-run reads its dataset + the real assertion engine ($0, no backend)")


BENCHMARKS: Dict[str, BenchSpec] = {
    # ------------------------------------------------------------------ toolathlon
    "toolathlon": BenchSpec(
        name="toolathlon",
        question="Do byte-clean detectors fire on third-party-scored trajectories, and convert fail->pass live?",
        results_summary="",  # numbers live in EXPLAINER.md + docs/157-158
        entrypoints=(
            Entrypoint("replay", ["benchmark.toolathlon.run_replay", "--all", "--no-download"],
                       cost="free", does="$0 cached-corpus replay over all detectors",
                       prereqs=()),
            Entrypoint("replay_smoke", ["benchmark.toolathlon.run_replay", "--all", "--no-download",
                                        "--limit", "{limit}"],
                       cost="free", does="$0 smoke (capped records/file)",
                       defaults={"limit": "20"}),
            Entrypoint("live_warn", ["benchmark.toolathlon.live_adapter"],
                       cost="paid", arm="warn_stream",
                       does="live WARN A/B scorer (after _ab_wiring/run_ab.sh produces run dirs)",
                       prereqs=(_DOCKER, _GEMINI)),
        ),
    ),
    # ------------------------------------------------------------- enterpriseops
    "enterpriseops": BenchSpec(
        name="enterpriseops",
        question="Does the OBSERVE/WARN/DEFER/BLOCK ladder + arg_provenance LIFT a model on ServiceNow ITSM?",
        results_summary="benchmark/enterpriseops/RESULTS.md",
        entrypoints=(
            Entrypoint("theories", ["benchmark.enterpriseops.intervention_theories",
                                    "--tasks", "{tasks}", "--seeds", "{seeds}"],
                       cost="free", does="Tier-0 $0 theory sweep (recovery-dynamics boundary)",
                       defaults={"tasks": "600", "seeds": "3"}),
            Entrypoint("sim_ab", ["benchmark.enterpriseops.intervention_ab"],
                       cost="free", does="Tier-0b $0 fixed-point simulator A/B"),
            Entrypoint("cause_locality", ["benchmark.enterpriseops.cause_locality"],
                       cost="free", does="$0 ablation: are natural thrashes a rewind issue?"),
            Entrypoint("feasibility_split", ["benchmark.enterpriseops.feasibility_split"],
                       cost="free",
                       does="$0 docs/198: WALLED/CURABLE split + witness-gated early-halt + curable-conversion read"),
            Entrypoint("curable_oversample", ["benchmark.enterpriseops.curable_oversample"],
                       cost="free",
                       does="$0 recipe: the targeted run plan to power the curable-slice conversion A/B"),
            Entrypoint("live", ["benchmark.enterpriseops.live_ab",
                                "--tasks", "{tasks}", "--arms", "{arms}", "--domains", "{domains}",
                                "--mint-rate", "{mint_rate}", "--mint-seed", "{mint_seed}"],
                       cost="paid",
                       does="Tier 2-4 LIVE A/B (the arms are the shared _arms vocabulary)",
                       prereqs=(_DOCKER, _GEMINI, _GYM),
                       # script form (NOT `-m`): the gym ships its own top-level `benchmark`
                       # package, so module-form launch shadows it and breaks the gym's imports.
                       script="benchmark/enterpriseops/live_ab.py",
                       cwd="benchmark/enterpriseops",
                       defaults={"tasks": "3", "arms": "none warn block", "domains": "itsm",
                                 "mint_rate": "0.30", "mint_seed": "42"}),
        ),
    ),
    # -------------------------------------------------------------- fleet_horizon
    "fleet_horizon": BenchSpec(
        name="fleet_horizon",
        question="Open-loop vs closed-loop fleet integrity: lies/overwrites caught, at what review cost?",
        results_summary="benchmark/fleet_horizon/RESULTS.txt",
        entrypoints=(
            Entrypoint("cell", ["benchmark.fleet_horizon.harness",
                                "--efforts", "{efforts}", "--phases", "{phases}",
                                "--seed", "{seed}", "--json"],
                       cost="free", does="$0 single integrity cell (drives the REAL kernel)",
                       defaults={"efforts": "6", "phases": "20", "seed": "1729"}),
            Entrypoint("sweep", ["benchmark.fleet_horizon.harness", "--sweep", "--json"],
                       cost="free", does="$0 horizon x fleet integrity sweep"),
            Entrypoint("velocity", ["benchmark.fleet_horizon.harness", "--velocity-sweep", "--json"],
                       cost="free", does="$0 velocity-economics sweep"),
        ),
    ),
    # ----------------------------------------------------------------- fleetforge
    "fleetforge": BenchSpec(
        name="fleetforge",
        question="Do real LLM fleets collide on shared state, and does the skill arm capture it attributably?",
        results_summary="",  # newest; no committed RESULTS file yet
        entrypoints=(
            Entrypoint("smoke", ["benchmark.fleetforge.run_smoke",
                                 "--efforts", "{efforts}", "--phases", "{phases}",
                                 "--seed", "{seed}", "--json"],
                       cost="free", does="$0 Tier-2 smoke (the load-bearing datum)",
                       defaults={"efforts": "4", "phases": "4", "seed": "1"}),
            Entrypoint("adherence", ["benchmark.fleetforge.skill_adherence"],
                       cost="free", does="$0 attribution-instrument keystone"),
        ),
    ),
    # ------------------------------------------------------------------ agenthallu
    "agenthallu": BenchSpec(
        name="agenthallu",
        question="Does the byte-clean structural+recovery detector localize the gold hallucination step?",
        results_summary="",  # SSOT scorer is self-checking; numbers in docs/173-174
        entrypoints=(
            Entrypoint("score", ["benchmark.agenthallu.scoring", "--check"],
                       cost="free", does="$0 offline SSOT scorer (asserts committed claims hold)",
                       prereqs=(Prereq(DATASET, "AGENTHALLU_ROOT",
                                       "clone github.com/liuxuannan/AgentHallu as a sibling, "
                                       "or set AGENTHALLU_ROOT"),)),
            Entrypoint("emit", ["benchmark.agenthallu.scoring", "--emit"],
                       cost="free", does="$0 write the claims ledger",
                       prereqs=(Prereq(DATASET, "AGENTHALLU_ROOT", "see score"),)),
        ),
    ),
    # ------------------------------------------------------------ agentprocessbench
    "agentprocessbench": BenchSpec(
        name="agentprocessbench",
        question="Where does the deterministic ORACLE rung end and the JUDGE rung take over (the ~11-27% boundary)?",
        results_summary="",  # SSOT scorer is self-checking; numbers in docs/174
        entrypoints=(
            Entrypoint("score", ["benchmark.agentprocessbench.scoring", "--check"],
                       cost="free", does="$0 offline boundary/floor SSOT scorer",
                       prereqs=(Prereq(DATASET, "AGENTPROCESSBENCH_ROOT",
                                       "clone huggingface LulaCola/AgentProcessBench as a sibling, "
                                       "or set AGENTPROCESSBENCH_ROOT"),)),
            Entrypoint("emit", ["benchmark.agentprocessbench.scoring", "--emit"],
                       cost="free", does="$0 write the claims ledger",
                       prereqs=(Prereq(DATASET, "AGENTPROCESSBENCH_ROOT", "see score"),)),
        ),
    ),
    # -------------------------------------------------------------- witness_ladder
    "witness_ladder": BenchSpec(
        name="witness_ladder",
        question="How does the write-admission verdict's value change as the WITNESS "
                 "rung weakens — where does the value end and the abstain band (the "
                 "docs/204 §3 wall) begin?",
        results_summary="",  # the curve + roadmap are printed by the entrypoint (docs/261)
        entrypoints=(
            Entrypoint("sweep", ["benchmark.witness_ladder.harness", "--json"],
                       cost="free",
                       does="$0 sweep dos.reward.admit over the Accountability rung axis on a "
                            "declared distribution (drives the REAL kernel verdict); emits the "
                            "monotone J-vs-witness-strength curve + the growth-frontier roadmap",
                       prereqs=()),
        ),
    ),
    # ----------------------------------------------------------------- forge_arena
    "forge_arena": BenchSpec(
        name="forge_arena",
        question="Can TEXT ALONE flip the write-admission bit — can an attacker who "
                 "controls every text channel (narration, a pasted [SYSTEM: accept=True], "
                 "a forged floor-rung witness, judge flattery) get a false-effect claim "
                 "ACCEPTed without doing the work?",
        results_summary="benchmark/forge_arena/RESULTS.md",  # docs/321, issue #114
        entrypoints=(
            Entrypoint("ladder", ["benchmark.forge_arena.harness", "--json"],
                       cost="free",
                       does="$0 drive a declared attack corpus x task grid through TWO gates "
                            "(the REAL dos.reward.admit witness floor vs a text-believing "
                            "narration-grader); emits the forgery-rate gap (floor 0% vs text "
                            "100%) + the append-only attempts rows. Exits non-zero if the floor "
                            "EVER admits a forgery (a #35-class witness-tamper hole)",
                       prereqs=()),
        ),
    ),
    # --------------------------------------------------------------- constraintviol
    "constraintviol": BenchSpec(
        name="constraintviol",
        question="On an ODCV-Bench-style SAFETY scenario, where does a deterministic "
                 "world-state read-back DISAGREE with the gameable post-hoc LLM judge, "
                 "and what fraction of irreversible violations does the pre-action gate "
                 "PREVENT that the agent's narration waved through?",
        results_summary="benchmark/constraintviol/RESULTS.md",
        entrypoints=(
            Entrypoint("measure", ["benchmark.constraintviol.harness"],
                       cost="free",
                       does="$0 deterministic fold over the faithful-minimal ODCV-Bench "
                            "scenario set: judge-vs-oracle disagreement rate + gate "
                            "prevention rate (drives the REAL effect_witness join + the "
                            "pretool deny dialect); no model, no network",
                       prereqs=()),
            Entrypoint("measure_json", ["benchmark.constraintviol.harness", "--json"],
                       cost="free",
                       does="$0 full per-scenario fold + headline rates as JSON",
                       prereqs=()),
        ),
    ),
    # ------------------------------------------------------------------- agentdiff
    "agentdiff": BenchSpec(
        name="agentdiff",
        question="Does the docs/228 out-of-loop write-admission gate hold on a RICHER witness "
                 "(Agent-Diff's structured state-diff, not tau2's single db-hash bool)?",
        results_summary="",  # frozen J arithmetic printed by the entrypoint; live ΔB is the paid arm
        entrypoints=(
            Entrypoint("frozen", ["benchmark.agentdiff.live_loop"],
                       cost="free",
                       does="$0 believe-vs-adjudicate A/B over the 224 bench tasks via the REAL "
                            "assertion engine (synthetic over-claim diff); prints the J arithmetic",
                       prereqs=(_AGENTDIFF,)),
            Entrypoint("live", ["benchmark.agentdiff.live_loop", "--live"],
                       cost="paid",
                       does="LIVE believe-vs-adjudicate A/B: drive A via the SDK, witness = "
                            "evaluate_run().passed, gate, seed peer B, count causal ΔB",
                       prereqs=(_AGENTDIFF, _DOCKER, _GEMINI)),
        ),
    ),
    # -------------------------------------------------------------------- finmodel
    "finmodel": BenchSpec(
        name="finmodel",
        question="Over a FrontierFinance-style (arXiv 2604.05912) financial-model corpus, does a "
                 "`formula_recompute` derived-witness rung REFUTE the exact static-value / "
                 "fabricated-balance / plug-balance forgeries the paper documents in its own "
                 "failure analysis, at 0% false-refute on a clean auditable model?",
        results_summary="benchmark/finmodel/RESULTS.md",
        entrypoints=(
            Entrypoint("measure", ["benchmark.finmodel.replay"],
                       cost="free",
                       does="$0 replay over the labeled corpus (clean + injected static-value/"
                            "fabricated-balance/plug-balance forgeries): DETECT recall per class "
                            "+ FALSE-REFUTE on clean (drives the REAL effect_witness join over a "
                            "deterministic recompute); no model, no network",
                       prereqs=()),
            Entrypoint("measure_json", ["benchmark.finmodel.replay", "--json"],
                       cost="free",
                       does="$0 full per-class fold + headline recall/false-refute as JSON",
                       prereqs=()),
        ),
    ),
    # ------------------------------------------------------------- memory_integrity
    "memory_integrity": BenchSpec(
        name="memory_integrity",
        question="How much bad memory does the write gate refuse and the recall gate "
                 "catch (docs/316 taxonomy), at what false-refusal cost — vs the "
                 "admit-all industry default?",
        results_summary="benchmark/memory_integrity/RESULTS.md",
        entrypoints=(
            Entrypoint("measure", ["benchmark.memory_integrity.run"],
                       cost="free",
                       does="$0 two-time-point protocol over the constructed-repo corpus "
                            "(drives the REAL admit/recall driver); writes RESULTS.md + "
                            "results.json; no model, no network",
                       prereqs=()),
            Entrypoint("measure_json", ["benchmark.memory_integrity.run", "--json"],
                       cost="free",
                       does="$0 same protocol, full per-candidate fold as JSON",
                       prereqs=()),
        ),
    ),
    # ------------------------------------------------------------------- legalcite
    "legalcite": BenchSpec(
        name="legalcite",
        question="Does DOS catch fabricated/mis-quoted legal citations (the Mata v. Avianca "
                 "class) against a third-party reporter — DETECT recall at ~0% false-fire "
                 "(docs/277 §6 #1, docs/279)?",
        results_summary="benchmark/legalcite/RESULTS.md",
        entrypoints=(
            Entrypoint("replay", ["benchmark.legalcite.harness", "--json"],
                       cost="free",
                       does="$0 frozen-corpus replay: run the labeled cite set (real + "
                            "documented Mata fabrications + synth perturbations) through the "
                            "REAL citation_resolve.classify(); report DETECT recall + FALSE-FIRE "
                            "over a stated denominator; the false-fire floor is the exit gate",
                       prereqs=()),
        ),
    ),
}


def arm_env_for(arm: str) -> Dict[str, str]:
    """The DOS_* env for a named arm (delegates to the shared _arms vocabulary)."""
    return arm_env(arm)
