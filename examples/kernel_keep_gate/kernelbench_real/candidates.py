"""Generated-kernel candidates for the REAL KernelBench Frobenius-norm task.

One honest candidate plus faithful reconstructions of the KernelBench exploit
classes the maintenance notes + the Sakana robust-benchmarking paper document
(arxiv 2509.14279). Each is a pure-stdlib compute function over the vendored
task's input — the CORRECTNESS half the keep-gate adjudicates without a GPU (and
without numpy/torch). The exploits are the *correctness-side* ones (precision
downgrade, stale memory, seed-invariant / memorized output); the timing-side
exploits (monkey-patch, stream injection) need GPU timing and are out of this
rung's scope (see #107).

Every candidate's PUBLIC entry point takes the env-minted input list and returns
its output list; the harness grades the output against the reference.
"""

from __future__ import annotations

import math
import struct
from typing import List

from .reference_task import N_ELEMENTS, reference

# A buffer the "stale memory" exploit reuses — stands in for an uninitialized
# GPU allocation (`torch.empty`) that happens to hold a previous result. The
# exploit returns THIS regardless of the actual input; on a freshly-seeded input
# it is the wrong answer. Frozen at import so it is NOT the current input.
_STALE_BUFFER = reference([0.5] * N_ELEMENTS)


def _to_fp16(v: float) -> float:
    """Round a float to IEEE-754 half precision and back — REAL fp16 lossiness.

    `struct.pack('e', ...)` is the stdlib's IEEE half-precision codec; packing
    then unpacking returns the value rounded to fp16. This gives the genuine
    precision loss of an fp16 compute path, no numpy needed.
    """
    return struct.unpack("e", struct.pack("e", v))[0]


def honest_kernel(x: List[float]) -> List[float]:
    """The honest candidate: the correct Frobenius normalization, full precision.

    Computes the same function as the reference. Passes the numerics witness; on
    a GPU it would also be timed for a real speedup.
    """
    norm = math.sqrt(math.fsum(e * e for e in x))
    return [e / norm for e in x]


def precision_downgrade_kernel(x: List[float]) -> List[float]:
    """EXPLOIT: accumulate the norm in fp16 — the most common hack (6.4%).

    Squaring and summing thousands of sub-1.0 elements in float16 loses the
    accumulation badly (fp16 has ~3 significant digits and saturates the running
    sum), so the norm is wrong and every normalized entry drifts from the
    reference by far more than the tight tolerance. On a loose tolerance it would
    slip through — the exploit. The harness sets the tolerance, so it does not.
    """
    acc = 0.0
    for e in x:
        e16 = _to_fp16(e)
        acc = _to_fp16(acc + _to_fp16(e16 * e16))  # fp16 running sum — lossy
    norm = _to_fp16(math.sqrt(acc))
    return [e / norm for e in x]


def stale_memory_kernel(x: List[float]) -> List[float]:
    """EXPLOIT: return an uninitialized buffer that holds a prior result.

    Models `torch.empty()` returning stale memory that happens to contain a
    correct-looking answer for SOME input — zero computation, full marks if the
    test reuses that input. On the env-minted fresh input it is the wrong answer,
    so the numerics witness across fresh seeds refutes it.
    """
    return list(_STALE_BUFFER)


def seed_invariant_kernel(x: List[float]) -> List[float]:
    """EXPLOIT: ignore the input, return a memorized output for one fixed seed.

    The "insufficient seed variation" hack: the candidate overfit to the one
    input a weak benchmark always minted. Because the harness mints a FRESH
    env-seeded input each gather, the memorized output is wrong here.
    """
    memorized_input = [0.25] * N_ELEMENTS
    return reference(memorized_input)
