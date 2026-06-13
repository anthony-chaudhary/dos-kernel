"""run2.py — the docs/322 P2-prep run: drive the IDENTICAL poisoned-pool rig
with the synthetic drift-eliciting policy and write a second stamped evidence
block beside the bench (issue #36).

Run 1 (RESULTS.md) used a live, well-calibrated model session and saw a FLAT
held-out over-claim curve in both arms — it confirmed pool poisoning (Arm S
banks over-claims, Arm W cannot) but left open whether the held-out over-claim
curve can MOVE AT ALL under few-shot selection, or whether the rig is simply
insensitive at this scale. Run 2 answers that with a sensitivity probe: the
content-blind, exemplar-pressured `NoisyPolicy` (see policies.py), driven over
the same harness, same witness, same `dos.reward.admit` gate.

This is NOT real-weights P2 (that needs compute this machine does not assume);
it is the GPU-free de-risking step the goal scoped — does the apparatus register
generation-over-generation drift when a drift-prone policy is present, and does
the witness-gated arm still keep its pool poison at zero by construction.

    python -m benchmark.poisoned_pool.run2 --run-dir <D> [--write-beside]

Determinism: the synthetic policy is seeded; the same flags reproduce the
evidence byte-for-byte (asserted by tests/test_poisoned_pool_policies.py).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import run as pp_run

# The canonical run-2 config: 1.5x run 1's trajectory count, a larger exemplar
# cap (M=6 vs run 1's 4 — "larger M exemplars"), and the weak/noisy synthetic
# policy ("a weaker/noisier policy"). Both knobs the goal named, together.
RUN2 = dict(gens=4, k_train=3, k_eval=3, m_exemplars=6, seed=7)
POLICY = {"name": "noisy"}  # NoisyPolicy defaults — the weak, bluff-prone regime

TITLE = "poisoned_pool — run 2: the drift-eliciting sensitivity probe (docs/322 P2-prep, issue #36)"

PREAMBLE: List[str] = [
    "## What run 2 is (and is not)",
    "",
    "Run 1 (`RESULTS.md`) drove this rig with a live, well-calibrated model",
    "session and saw a **flat** held-out over-claim curve (0.00 in both arms,",
    "all four generations): it confirmed *pool poisoning* but could not tell",
    "\"the gate works\" apart from \"no drift is detectable at this scale\". Run 2",
    "replaces the policy with a deterministic, **synthetic** drift-eliciting",
    "driver (`policies.py` `NoisyPolicy`) — NOT a live model and no claim about",
    "real-model behaviour — to answer the open P2-prep question: *can the",
    "held-out over-claim curve move at all under few-shot selection?* The",
    "harness, the witness subprocess, and the `dos.reward.admit` gate are",
    "byte-identical to run 1; only the driver and the scale knobs changed",
    "(weaker/noisier policy + larger M exemplars, the two levers the goal named).",
    "",
    "**The synthetic policy, stated plainly:** it solves easy tasks often and",
    "hard tasks rarely; on unsolved work it emits a genuinely-wrong patch (the",
    "task's own buggy source, which the real witness refutes) and bluffs a",
    "`CLAIM: RESOLVED` with probability `base_bluff + contagion * (n_exemplars /",
    "M)`. The bluff pressure is **content-blind** — it rises with the *count* of",
    "admitted exemplars the policy can see, never with their hidden witness",
    "truth (the byte the policy must not author). It is the weaker of the",
    "possible contagion models: it shows the curve CAN move, but does not claim",
    "the movement is *caused* by poison content.",
    "",
    "**How to read the curves below:**",
    "",
    "- **Held-out over-claim now MOVES** — off the 0.083 floor up to ~0.5 within",
    "  one generation, in both arms. Run 1's flatness was the calibrated policy,",
    "  not an insensitive rig: the apparatus registers drift when drift is",
    "  present. That is the P2-prep deliverable.",
    "- **Pool poison: Arm S accumulates, Arm W stays exactly 0.0** across every",
    "  generation — the docs/234 structural result, unchanged from run 1 and now",
    "  under a maximally bluff-prone policy.",
    "- **Pool size diverges (S grows faster than W):** the self-judged gate admits",
    "  every RESOLVED claim, over-claims included, so it manufactures more",
    "  selection volume — which (via the content-blind pressure) feeds back as",
    "  more bluff. The S/W asymmetry is emergent from the gate, not hand-set.",
    "- **The two arms' held-out over-claim rates sit CLOSE** (W can even exceed S",
    "  at a generation): with a content-blind pressure, once either pool fills M",
    "  slots the bluff inflation is similar in both arms. The clean S-vs-W",
    "  separation lives in **pool poison** (S>0, W=0) and **pool size**, not in a",
    "  lower held-out rate. Reported as measured; see threats to validity in",
    "  `docs/322` §3 and the content-blind caveat in `policies.py`.",
]


def run2(run_dir: Path, *, write_beside: bool = False) -> dict:
    if (run_dir / "state.json").exists():
        raise SystemExit(f"refusing to clobber existing state in {run_dir}")
    pp_run.init_run(run_dir, policy=POLICY, **RUN2)
    for _ in range(RUN2["gens"]):
        pp_run.drive_run(run_dir, policy_name=POLICY["name"])
        pp_run.ingest_run(run_dir)
    return pp_run.report_run(
        run_dir, write_beside=write_beside, basename="results_run2",
        title=TITLE, preamble=PREAMBLE)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m benchmark.poisoned_pool.run2")
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--write-beside", action="store_true",
                   help="also write RESULTS_run2.md + results_run2.json beside the package")
    a = p.parse_args(argv)
    results = run2(a.run_dir, write_beside=a.write_beside)
    c = results["curves"]
    print("held-out over-claim  S=%s  W=%s"
          % (c["eval_overclaim_rate"]["S"], c["eval_overclaim_rate"]["W"]))
    print("pool poison frac     S=%s  W=%s"
          % (c["pool_poison_frac"]["S"], c["pool_poison_frac"]["W"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
