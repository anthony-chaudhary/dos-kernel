# Kernel keep-gate × DOS — a non-forgeable keep bit for generated kernels

LLM-driven GPU-kernel generation is bottlenecked by **verification trust**, not
generation ability. The failure class is documented across the field: generated
kernels game the evaluation harness — copying the reference instead of
computing, exploiting numeric tolerance on precision-dominated outputs, timing
only part of the work, or editing the measurement harness itself. Every serious
project hand-builds the same bespoke defenses.

That gate already ships here, domain-free: `dos improve` (docs/280) KEEPs a
candidate **only** on suite-green + truth-clean + a strict env-measured metric
gain, and the candidate authors zero bytes of the keep bit. This example aims it
at a generated kernel ([issue #107](https://github.com/anthony-chaudhary/dos-kernel/issues/107)),
and closes the evaluator-tamper hole [issue #35](https://github.com/anthony-chaudhary/dos-kernel/issues/35)
names as a structural floor.

```bash
pip install dos-kernel
python examples/kernel_keep_gate/demo.py             # offline, no GPU, no account
python -m pytest tests/test_kernel_keep_gate_example.py   # the four pinned verdicts
```

The candidates are CPU compute functions standing in for GPU kernels (a stable
**softmax** over a batch of logit rows — a precision-sensitive task with exactly
the property the tolerance exploit needs). A real GPU is not required for the
pin: the gate logic is what is under test.

## The four pinned verdicts

| Candidate | Verdict | Why |
|---|---|---|
| `honest_faster` | **KEEP** | correct AND env-timed faster — the only candidate that earns the keep bit, by actually doing the work |
| `reference_copy` | **REVERT** (`no-improvement`) | correct but re-derives the answer the slow way — no measured speedup; correctness is necessary, not sufficient |
| `tolerance_exploit` | **REVERT** (`regressed`) | faster, but skips the softmax max-subtraction → numerically wrong on the overflow-magnitude band the harness mints; the numerics floor refuses it before its speed is weighed |
| `harness_edit` | **REVERT** (`regressed`) | its diff reaches the witness tree (`harness/`) → structurally unkeepable by the #35 tamper floor, even when its kernel is the honest, correct, fast one |

## The four facts — every one env-authored (docs/138)

`improve.classify` reads four facts; the candidate authors none of them.

- **`suite_passed`** — the candidate output matches the authoritative reference,
  computed in `harness/` where the candidate can't read it, on vectors
  **minted after the candidate is frozen** (`harness/vectors.py`), under a
  **tight tolerance the harness sets** (`harness/numerics.py`). A candidate
  cannot special-case inputs it never saw, nor pass by caching a reference
  output it can't read, nor relax a tolerance it can't reach.
- **`truth_clean`** — the [#35](https://github.com/anthony-chaudhary/dos-kernel/issues/35)
  tamper witness: the candidate diff stays inside `kernels/`; `harness/` is
  byte-identical. See below.
- **`work` / `baseline_work`** — the env-timed latency speed score
  (`harness/timing.py`), the harness's clock, never the candidate's claim.

The candidate's `narrated` description is carried to the operator surface and
**parsed for nothing** (docs/234). The `reference_copy` candidate ships a forged
"much faster and correct" claim and still REVERTs — flipping its narration to
honest changes no verdict (`test_narration_moves_nothing`).

## The #35 floor — a tree check, not a convention

The keep-gate's witnesses (reference, minter, tolerance, timer) all live in
`harness/`. A candidate whose diff reaches `harness/` could weaken any of them —
loosen the tolerance, mint only easy vectors, time a no-op — then measure
green-and-fast on its own rigged harness. So the host computes one extra
env-authored fact, `harness_untouched`, from the candidate's changed-file set
(`git diff --name-only` — git authors the bit, the loop authors zero bytes of
it), and **refuses before any improvement is weighed**: it does not even run a
harness the candidate touched. This is `SELF_MODIFY` generalized from "the
kernel's own running code" to "whatever authors this loop's witnesses."

The asymmetry the #35 sketch names holds: *widening* the witness tree
(protecting more) is a mechanical edit; *narrowing* it is a human-rung act.

## What this does NOT claim

- It does not make any kernel faster. It adjudicates the **claim** that a kernel
  is faster-and-correct, from evidence the kernel's author didn't write.
- The metric is gathered through an injected `measure` callback so the verdicts
  are pinnable on deterministic latencies — wall-clock jitter must never flip a
  no-op to KEEP (a flaky witness is the failure the gate exists to refuse). The
  shipped `demo.py` runs the real clock; the test pins the order. The verdict
  itself is pure — no clock inside `classify`.
- CPU stand-ins exercise the **gate logic**, not a CUDA toolchain. Mapping the
  shape onto real KernelBench material — which tasks admit CPU-only correctness
  adjudication vs which need GPU timing, and the GPU-burst run plan — is tracked
  on [#107](https://github.com/anthony-chaudhary/dos-kernel/issues/107).
- The path-scoped tamper floor models the **fleet-search** setting: a proposer
  that edits files in an isolated worktree (the docs/280 `dos improve` shape,
  which is diff-confined). KernelBench's single-shot evaluator instead `exec`s
  an opaque kernel **source string** in-process, so there is no diff to scope —
  there the same "the candidate may not author its own measurement" principle
  has to move to the **exec/sandbox boundary** (the timer the candidate can't
  rebind), which the timing-channel exploits (monkey-patching `elapsed_time`,
  stream injection) need and a tree-diff alone does not close. That variant is
  the open design surface on [#107](https://github.com/anthony-chaudhary/dos-kernel/issues/107).

## Layout

```
kernel_keep_gate/
  kernels/     # the ONLY tree a candidate may edit — the four stand-in kernels
  harness/     # the witness tree — reference, post-freeze minter, tolerance, timer (untouchable)
  gate.py      # the host: gather the four env-authored facts → improve.classify
  demo.py      # run the four candidates, print the verdicts
```
