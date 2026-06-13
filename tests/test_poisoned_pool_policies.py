"""docs/322 P2-prep — pin the synthetic drift-eliciting policy + run 2 (issue #36).

Run 2 (`benchmark/poisoned_pool/run2.py`) drives the IDENTICAL poisoned-pool rig
with a deterministic, provider-free `NoisyPolicy` to answer the open P2-prep
question: can the held-out over-claim curve move at all under few-shot
selection? These pins keep that probe trustworthy:

1. The policy is WITNESS-HONEST: a solved trajectory emits a patch the real
   witness ATTESTS; a bluffed trajectory emits one the real witness REFUTES.
   The over-claims it manufactures are genuine wrong bets, not narration.
2. The bluff pressure is CONTENT-BLIND: it rises with the COUNT of admitted
   exemplars the prompt shows, never with their hidden witness truth.
3. The whole run is DETERMINISTIC: the same seed reproduces identical curves.
4. Arm W's pool poison stays ZERO by construction across the synthetic run —
   the docs/234 structural floor, now under a maximally bluff-prone policy.

benchmark/ is on sys.path the witness_ladder way (testpaths = tests only).
"""
from __future__ import annotations

import sys
from pathlib import Path

_BENCH = str(Path(__file__).resolve().parents[1] / "benchmark")
if _BENCH not in sys.path:
    sys.path.insert(0, _BENCH)

from poisoned_pool import harness, policies, run as pp_run, run2  # noqa: E402
from poisoned_pool.tasks import task_by_id, heldout_tasks         # noqa: E402


# ---------------------------------------------------------------------------
# 1. The policy is witness-honest — its patches map to real witness outcomes.
# ---------------------------------------------------------------------------

def _parse(body: str):
    return harness.parse_completion(body)


def test_solved_patch_attests_and_bluffed_patch_refutes():
    pol = policies.NoisyPolicy(seed=1)
    task = task_by_id("clamp")
    # A solved answer emits the reference fix + RESOLVED -> witness ATTESTS.
    solved_body = policies._completion(task.reference_src, harness.CLAIM_RESOLVED)
    patch, claim = _parse(solved_body)
    assert claim == "RESOLVED"
    assert harness.run_witness(task, patch).stance.value == "ATTESTED"
    # A bluffed answer emits the buggy source + RESOLVED -> witness REFUTES.
    # That pairing (RESOLVED over a refuted patch) is exactly an over-claim.
    bluff_body = policies._completion(task.buggy_src, harness.CLAIM_RESOLVED)
    patch, claim = _parse(bluff_body)
    assert claim == "RESOLVED"
    assert harness.run_witness(task, patch).stance.value == "REFUTED"


# ---------------------------------------------------------------------------
# 2. Bluff pressure is content-blind: it tracks exemplar COUNT, not truth.
# ---------------------------------------------------------------------------

def test_exemplar_count_reads_admitted_blocks_only():
    # No exemplar block -> 0; the rules-line "CLAIM: RESOLVED" must not count.
    task = task_by_id("median")
    bare = harness.render_prompt(task, [])
    assert policies.NoisyPolicy.exemplar_count(bare) == 0
    # Two admitted exemplars -> count 2, regardless of their claim text.
    pool = [harness.pool_entry("g0.W.train.sum_range.0", task_by_id("sum_range"),
                               task_by_id("sum_range").reference_src, "RESOLVED", True),
            harness.pool_entry("g0.W.train.clamp.0", task_by_id("clamp"),
                               task_by_id("clamp").reference_src, "RESOLVED", True)]
    conditioned = harness.render_prompt(task, pool)
    assert policies.NoisyPolicy.exemplar_count(conditioned) == 2


def test_bluff_probability_rises_with_exemplar_count_not_content():
    # Two pools of equal SIZE but opposite witness truth (one all-confirmed
    # wins, one all-poison) must drive the SAME bluff propensity — the pressure
    # is blind to the hidden `witness_confirmed` bit.
    pol = policies.NoisyPolicy(seed=3, p_solve_hard=0.0, base_bluff=0.0, contagion=1.0)
    task = task_by_id("median")  # hard, p_solve forced to 0 -> always unsolved
    sr = task_by_id("sum_range")
    clean_pool = [harness.pool_entry(f"e{i}", sr, sr.reference_src, "RESOLVED", True)
                  for i in range(6)]
    poison_pool = [harness.pool_entry(f"e{i}", sr, sr.buggy_src, "RESOLVED", False)
                   for i in range(6)]
    clean_prompt = harness.render_prompt(task, clean_pool)
    poison_prompt = harness.render_prompt(task, poison_pool)
    # cap=6, count=6 -> pressure 1.0 -> p_bluff=1.0 in BOTH; identical claim.
    a = _parse(pol.answer("g1.W.eval.median.0", task, clean_prompt, cap=6))[1]
    b = _parse(pol.answer("g1.S.eval.median.0", task, poison_prompt, cap=6))[1]
    # Same trajectory-shaped id seed differs by arm token, so compare propensity
    # via the rendered claim under forced p_bluff=1.0: both must bluff RESOLVED.
    assert a == "RESOLVED" and b == "RESOLVED"
    # And with NO exemplars (pressure 0, base_bluff 0) the same task stays honest.
    bare = harness.render_prompt(task, [])
    assert _parse(pol.answer("g0.B.eval.median.0", task, bare, cap=6))[1] == "NOT_RESOLVED"


# ---------------------------------------------------------------------------
# 3. The run is deterministic, and 4. Arm W poison is zero by construction.
# ---------------------------------------------------------------------------

def test_run2_is_deterministic_and_armW_poison_is_zero(tmp_path: Path):
    a = run2.run2(tmp_path / "a")
    b = run2.run2(tmp_path / "b")
    assert a["curves"] == b["curves"]
    assert a["rows"] == b["rows"]
    # Arm W never admits a refuted claim — poison zero at every generation.
    assert all(v == 0.0 for v in a["curves"]["pool_poison_frac"]["W"])
    # The probe earns its name: the held-out over-claim curve actually MOVED
    # off its generation-0 value (run 1's stayed flat at 0.00).
    s_curve = a["curves"]["eval_overclaim_rate"]["S"]
    assert max(s_curve) > s_curve[0]
    # Arm S accumulates poison monotonically (the self-judged gate banks it).
    s_pool = a["curves"]["pool_poison_frac"]["S"]
    assert s_pool[-1] > s_pool[0] > 0.0


def test_drive_then_ingest_matches_run2_orchestrator(tmp_path: Path):
    # The `drive` CLI path and the run2 orchestrator must agree — same config,
    # same seed, same curves (the orchestrator is just a thin loop over them).
    run_dir = tmp_path / "manual"
    pp_run.init_run(run_dir, policy={"name": "noisy"}, **run2.RUN2)
    for _ in range(run2.RUN2["gens"]):
        pp_run.drive_run(run_dir, policy_name="noisy")
        pp_run.ingest_run(run_dir)
    manual = pp_run.report_run(run_dir, basename="results_run2")
    orchestrated = run2.run2(tmp_path / "orch")
    assert manual["curves"] == orchestrated["curves"]
