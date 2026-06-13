"""Adjudicate the documented exploits against the REAL KernelBench task — no GPU.

    python examples/kernel_keep_gate/kernelbench_real/demo.py

Proves the keep-gate catches the CPU-adjudicable KernelBench exploit classes on
the actual vendored Level-1 Frobenius-norm task. Each candidate gets a TYPED
verdict, not a flag rate (docs/307: adjudicate, don't report a rate).

  honest               -> REVERT (no-improvement)   numerics PASS; floor cleared,
                                                     no GPU speedup measured (NOT
                                                     a forged KEEP)
  precision_downgrade  -> REVERT (regressed)        fp16 norm — numerics FAIL
  stale_memory         -> REVERT (regressed)        uninitialized buffer — FAIL
  seed_invariant       -> REVERT (regressed)        memorized output — FAIL

The timing half (env-measured speedup) is GPU-bound and stays operator-gated
on #107.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Put the EXAMPLE dir (parent of this package) on the path BEFORE importing the
# package, so `import kernelbench_real` resolves both when run as `python
# demo.py` and when imported by the test (which also adds the example dir).
_EXAMPLE_DIR = str(Path(__file__).resolve().parents[1])
if _EXAMPLE_DIR not in sys.path:
    sys.path.insert(0, _EXAMPLE_DIR)

from kernelbench_real import (  # noqa: E402 — path set above
    adjudicate,
    honest_kernel,
    precision_downgrade_kernel,
    seed_invariant_kernel,
    stale_memory_kernel,
)


def run_demo() -> list:
    cases = [
        ("honest", honest_kernel, "the correct Frobenius normalization"),
        ("precision_downgrade", precision_downgrade_kernel, "fp16 norm, big speedup, still correct"),
        ("stale_memory", stale_memory_kernel, "reused buffer, zero recompute, correct"),
        ("seed_invariant", seed_invariant_kernel, "precomputed the answer, instant"),
    ]
    return [adjudicate(name, fn, narrated=claim) for name, fn, claim in cases]


def main() -> None:
    rows = run_demo()
    print("DOS keep-gate vs the REAL KernelBench L1 Frobenius-norm task (no GPU)\n")
    width = max(len(r.name) for r in rows)
    for r in rows:
        cause = f" [{r.revert_cause}]" if r.revert_cause else ""
        verdict_note = (
            "numerics PASS — floor cleared, awaiting GPU timing"
            if r.numerics_passed
            else "numerics FAIL — exploit caught"
        )
        print(f"  {r.name:<{width}}  {r.verdict:<7}{cause:<18} {verdict_note}")


if __name__ == "__main__":
    main()
