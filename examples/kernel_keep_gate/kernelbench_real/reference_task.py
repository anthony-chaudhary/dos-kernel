"""A REAL KernelBench Level-1 reference task, vendored verbatim (MIT) — the spec.

This is KernelBench problem `level1/37_FrobeniusNorm_.py`, the actual benchmark
task, vendored so the keep-gate adjudicates REAL material rather than a
stand-in. The reference math is device-agnostic (a Frobenius-norm
normalization: divide a tensor by sqrt(sum of squares)), so its CORRECTNESS is
CPU-adjudicable with no GPU and no CUDA toolchain — which is the half of the
gate this rung proves. The TIMING half (env-measured speedup) is GPU-bound and
stays an operator-gated decision on #107.

Frobenius-norm normalization is a textbook **precision-dominated** task: the
output is the input scaled by one global constant, so a candidate that computes
the norm in low precision (fp16) and upcasts, or returns an uninitialized buffer
that happens to be near the answer, can slip under a loose tolerance — exactly
the KernelBench precision-downgrade (the most common documented exploit, 6.4% of
samples) and stale-memory exploit classes.

------------------------------------------------------------------------------
Vendored from KernelBench (https://github.com/ScalingIntelligence/KernelBench),
file `KernelBench/level1/37_FrobeniusNorm_.py`, under the MIT License:

    MIT License
    Copyright (c) 2023 Anne Ouyang, Simon Guo, Azalia Mirhoseini
    (Scaling Intelligence Lab, Stanford University)

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in
    all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

The original task is a PyTorch `nn.Module`; the reference COMPUTATION is
reproduced here in **pure stdlib Python** (mathematically identical:
`torch.norm(x, p='fro')` == `sqrt(sum(e*e for e in x))`) so the example runs
against the shipped `dos-kernel` package with NO torch, NO numpy, NO CUDA —
nothing beyond the standard library (the kernel is deliberately near-stdlib).
The task identity — the operation and the input distribution — is byte-faithful
to the vendored spec below; only the tensor representation (a flat list of
floats) is an implementation choice.
------------------------------------------------------------------------------

The vendored PyTorch spec, for provenance (NOT executed here — the stdlib
reference below computes the identical function):

    class Model(nn.Module):
        def forward(self, x):
            norm = torch.norm(x, p='fro')
            return x / norm

    batch_size = 112; features = 64; dim1 = 512; dim2 = 512
    def get_inputs():
        x = torch.rand(batch_size, features, dim1, dim2)
        return [x]
    def get_init_inputs():
        return []
"""

from __future__ import annotations

import math
import random
from typing import List

# The vendored input size. The real task is 112x64x512x512 = ~1.9e9 elements;
# we use a flat vector of N elements (the Frobenius norm is over ALL elements
# regardless of shape, so a flat vector computes the identical function). The
# precision-dominated property — one global norm over many positive elements —
# is preserved at any size; N is kept modest for a fast example.
N_ELEMENTS = 4096


def get_inputs(seed: int) -> List[float]:
    """Mint the task's input, env-seeded — U[0,1), the vendored `rand`.

    `seed` is authored by the environment (the harness), never the candidate —
    the docs/138 invariant. A fresh seed each gather is the KernelBench
    "insufficient seed variation" defense: a candidate that memorized one input
    fails on the next. Returned as a flat list of floats (the Frobenius norm is
    shape-agnostic, so this computes the identical function as the 4-D tensor).
    """
    rng = random.Random(seed)
    return [rng.random() for _ in range(N_ELEMENTS)]


def reference(x: List[float]) -> List[float]:
    """The authoritative Frobenius-norm normalization, in stable float64.

    Identical to `x / torch.norm(x, p='fro')`: divide by the sqrt of the sum of
    squares. Python floats ARE IEEE-754 double (float64), and `math.fsum` gives
    an exactly-rounded sum — the stable reference the candidate cannot weaken,
    computed in the harness, never a copy the candidate can read.
    """
    norm = math.sqrt(math.fsum(e * e for e in x))
    return [e / norm for e in x]
