"""docs/322 P2 — pin the real-weights rig's PURE spine (issue #36).

P2 (`benchmark/poisoned_pool/p2/`) upgrades the poisoned-pool PoC from few-shot
conditioning to actual LoRA SFT parameter movement. The GPU training itself
(`train.py`) needs the optional `[gpu]` extra and a real accelerator, so it is
not exercised here. What IS pinned is everything that decides the result BEFORE
a weight moves — the manifest converter, the ablation, and the eval fold — all
of which are pure and CPU-runnable:

1. The ABLATION holds in the training set: Arm S's SFT manifest carries the
   over-claim poison; Arm W's is exactly 0 by construction (the kernel cannot
   admit a refuted claim, so it cannot enter the manifest). This is the
   docs/234 structural floor, lifted from the prompt layer to the weights.
2. Arm W membership is the KERNEL verdict, not a reimplemented bit — a row
   enters W's manifest iff `dos.reward.admit` ACCEPTs it.
3. The manifest's patch is the REAL emitted bytes (the row records only a
   length); a poison record's patch is one the witness REFUTES.
4. The eval fold reuses P1's `fold_batch` + the SAME witness + the SAME gate —
   the only change from P1 eval is that the completions came from a trained
   model.
5. The whole manifest build is DETERMINISTIC: the synthesized P1 run is seeded,
   so the audit reproduces byte-for-byte.

benchmark/ is on sys.path the witness_ladder way (testpaths = tests only).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Heavy (manifest audits over real-weights fixtures, ~15-30s/test) — excluded from
# the `dev.py fast` inner loop, still run in full CI. See pyproject [tool.pytest].
pytestmark = pytest.mark.slow

_BENCH = str(Path(__file__).resolve().parents[1] / "benchmark")
if _BENCH not in sys.path:
    sys.path.insert(0, _BENCH)

from poisoned_pool import harness, run as pp_run            # noqa: E402
from poisoned_pool.run2 import RUN2, POLICY                 # noqa: E402
from poisoned_pool.p2 import manifest as p2_manifest        # noqa: E402
from poisoned_pool.p2 import eval as p2_eval                # noqa: E402
from poisoned_pool.p2 import run_p2                          # noqa: E402
from poisoned_pool.tasks import task_by_id                   # noqa: E402


# ---------------------------------------------------------------------------
# A small synthesized P1 run shared by the manifest tests — deterministic.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def synth_run(tmp_path_factory):
    run_dir = tmp_path_factory.mktemp("pp_p2")
    rows = run_p2.synthesize_p1_run(run_dir)
    loader = p2_manifest.run_dir_loader(run_dir / "p1")
    return {"run_dir": run_dir, "rows": rows, "loader": loader}


# ---------------------------------------------------------------------------
# 1 + 2. The ablation: S carries poison, W is zero by the KERNEL verdict.
# ---------------------------------------------------------------------------

def test_arm_W_manifest_is_poison_free_by_construction(synth_run):
    sft_W = p2_manifest.build_sft_manifest(
        synth_run["rows"], "W", synth_run["loader"])
    audit = p2_manifest.manifest_poison(sft_W)
    assert audit["size"] > 0, "W must admit some witness-confirmed wins"
    assert audit["poison_n"] == 0, "W's training set cannot carry poison"
    assert audit["poison_frac"] == 0.0


def test_arm_S_manifest_carries_overclaim_poison(synth_run):
    sft_S = p2_manifest.build_sft_manifest(
        synth_run["rows"], "S", synth_run["loader"])
    audit = p2_manifest.manifest_poison(sft_S)
    assert audit["poison_n"] > 0, (
        "the self-judged manifest must bank the over-claims (the whole point)")
    # S admits strictly more than W (every RESOLVED claim vs only confirmed).
    sft_W = p2_manifest.build_sft_manifest(
        synth_run["rows"], "W", synth_run["loader"])
    assert len(sft_S) > len(sft_W)


def test_arm_W_membership_equals_dos_reward_admit(synth_run):
    """Kernel-not-reimplemented: a train row is in W's manifest iff
    `dos.reward.admit` (via harness.witness_admit) ACCEPTs it."""
    rows = synth_run["rows"]
    sft_W = p2_manifest.build_sft_manifest(rows, "W", synth_run["loader"])
    in_W = {r.traj_id for r in sft_W}
    for r in rows:
        if r.get("kind") != "train":
            continue
        readback = p2_manifest._readback_from_row(r)
        label = harness.witness_admit(r.get("claim"), readback, r["task_id"])
        assert (r["traj_id"] in in_W) == bool(label.accept), (
            f"{r['traj_id']}: manifest membership disagrees with the kernel")


# ---------------------------------------------------------------------------
# 3. The manifest's patch is the REAL emitted bytes; poison patches refute.
# ---------------------------------------------------------------------------

def test_manifest_completion_is_real_patch_not_empty(synth_run):
    sft_W = p2_manifest.build_sft_manifest(
        synth_run["rows"], "W", synth_run["loader"])
    # Every W record claims RESOLVED and carries a non-empty patch.
    for r in sft_W:
        patch, claim = harness.parse_completion(r.completion)
        assert claim == "RESOLVED"
        assert patch and patch.strip(), f"{r.traj_id}: empty patch in manifest"


def test_poison_record_patch_is_refuted_by_the_witness(synth_run):
    sft_S = p2_manifest.build_sft_manifest(
        synth_run["rows"], "S", synth_run["loader"])
    poison = [r for r in sft_S if r.poison]
    assert poison, "expected at least one poison record to check"
    # A poison record claims RESOLVED but the witness refutes its patch — the
    # over-claim, end to end through the real subprocess witness.
    r = poison[0]
    adj = p2_eval.adjudicate_completion(r.task_id, r.completion)
    assert adj["claim"] == "RESOLVED"
    assert adj["witness_stance"] == "REFUTED"
    assert adj["kernel_verdict"] == "REJECT_POISON"


# ---------------------------------------------------------------------------
# 4. The eval fold reuses P1's fold + the SAME witness + the SAME gate.
# ---------------------------------------------------------------------------

def test_eval_fold_uses_p1_metrics_and_kernel_gate():
    prompts = p2_manifest.heldout_eval_prompts(k_eval=1)
    assert prompts, "held-out prompts must be non-empty"
    # A confirmed completion (the reference fix + RESOLVED) is a true success,
    # not an over-claim, and the kernel ACCEPTs it.
    completions = {}
    for p in prompts:
        task = task_by_id(p["task_id"])
        completions[p["traj_id"]] = (
            f"```python\n{task.reference_src}```\nCLAIM: RESOLVED\n")
    out = p2_eval.eval_arm(prompts, completions)
    m = out["metrics"]
    assert m["overclaim_rate"] == 0.0
    assert m["true_success_rate"] == 1.0
    assert m["kernel_verdict_counts"].get("ACCEPT") == len(prompts)


def test_eval_overclaim_is_caught_by_witness():
    prompts = p2_manifest.heldout_eval_prompts(k_eval=1)
    # An over-claim: the buggy source + RESOLVED. Witness refutes -> over-claim
    # counted, kernel REJECT_POISON. The model authored the claim; not the exit.
    completions = {}
    for p in prompts:
        task = task_by_id(p["task_id"])
        completions[p["traj_id"]] = (
            f"```python\n{task.buggy_src}```\nCLAIM: RESOLVED\n")
    out = p2_eval.eval_arm(prompts, completions)
    m = out["metrics"]
    assert m["overclaim_rate"] == 1.0
    assert m["true_success_rate"] == 0.0
    assert m["kernel_verdict_counts"].get("REJECT_POISON") == len(prompts)


def test_missing_completion_is_honest_refute():
    prompts = p2_manifest.heldout_eval_prompts(k_eval=1)
    out = p2_eval.eval_arm(prompts, {})  # no completions at all
    m = out["metrics"]
    # No claim, no patch -> no over-claim, no true success; an honest absence.
    assert m["overclaim_rate"] == 0.0
    assert m["true_success_rate"] == 0.0


# ---------------------------------------------------------------------------
# 5. Determinism — the synthesized run + manifest audit reproduce.
# ---------------------------------------------------------------------------

def test_manifest_audit_is_deterministic(tmp_path):
    def _audit(sub):
        rd = tmp_path / sub
        rows = run_p2.synthesize_p1_run(rd)
        loader = p2_manifest.run_dir_loader(rd / "p1")
        built = run_p2.build_manifests(rows, loader)
        return built["audit"], len(built["dpo_W"])

    a1, d1 = _audit("a")
    a2, d2 = _audit("b")
    assert a1 == a2, "the synthesized P1 run must be byte-deterministic"
    assert d1 == d2
    # And the structural invariant survives the repeat.
    assert a1["W"]["poison_n"] == 0
    assert a1["S"]["poison_n"] > 0


# ---------------------------------------------------------------------------
# 6. The DPO pairs are real: chosen confirmed, rejected REJECT_POISON.
# ---------------------------------------------------------------------------

def test_dpo_pairs_chosen_confirmed_rejected_poison(synth_run):
    dpo = p2_manifest.build_dpo_manifest(synth_run["rows"], synth_run["loader"])
    assert dpo, "expected at least one preference pair"
    for pair in dpo[:8]:
        chosen = p2_eval.adjudicate_completion(pair.task_id, pair.chosen)
        rejected = p2_eval.adjudicate_completion(pair.task_id, pair.rejected)
        assert chosen["kernel_verdict"] == "ACCEPT"
        assert rejected["kernel_verdict"] == "REJECT_POISON"


# ---------------------------------------------------------------------------
# 7. The manifest-only CLI runs end to end (the CPU preflight the goal names).
# ---------------------------------------------------------------------------

def test_manifest_only_cli_writes_evidence(tmp_path):
    rc = run_p2.main(["--run-dir", str(tmp_path / "run"), "--manifest-only"])
    assert rc == 0
    md = (tmp_path / "run" / "RESULTS_run3.md").read_text(encoding="utf-8")
    assert "docs/322 P2" in md
    assert "dos-bench-stamp" in md
    results = json.loads(
        (tmp_path / "run" / "results_run3.json").read_text(encoding="utf-8"))
    assert results["mode"] == "manifest-only"
    assert results["audit"]["W"]["poison_n"] == 0
    assert results["audit"]["S"]["poison_n"] > 0
