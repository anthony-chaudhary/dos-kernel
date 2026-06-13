"""Real-KernelBench adjudication rung — the no-GPU correctness half of #107 ITEM 2.

Vendors the actual KernelBench L1 Frobenius-norm task (MIT) and adjudicates
faithful reconstructions of the documented correctness-side exploit classes
(precision downgrade, stale memory, seed-invariant output) against it, proving
the keep-gate catches them on REAL material with no GPU. The timing-side half
(env-measured speedup, and the timing-channel exploits) is GPU-bound and stays
an operator-gated decision on #107.
"""

from .adjudicate import Adjudication, adjudicate, numerics_pass
from .candidates import (
    honest_kernel,
    precision_downgrade_kernel,
    seed_invariant_kernel,
    stale_memory_kernel,
)

__all__ = [
    "Adjudication",
    "adjudicate",
    "numerics_pass",
    "honest_kernel",
    "precision_downgrade_kernel",
    "seed_invariant_kernel",
    "stale_memory_kernel",
]
