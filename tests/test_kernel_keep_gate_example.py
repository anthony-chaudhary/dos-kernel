"""Pin the `examples/kernel_keep_gate/` host against the real kernel (issue #107).

The done-condition for #107: a host where `dos improve` adjudicates a
generated-kernel candidate end-to-end, and a test pins that each deliberately
reward-hacking candidate is REVERTED/refused BY CONSTRUCTION, while an honest
optimization is KEPT. A real GPU is not required — the candidates are
CPU-simulated compute functions; the gate logic is what is under test.

Four pinned verdicts:

  honest_faster     → KEEP        (correct AND env-timed faster)
  reference_copy    → REVERT      (correct but no measured speedup — a no-op)
  tolerance_exploit → REVERT      (numerically wrong on the overflow band)
  harness_edit      → REVERT      (diff reaches the witness tree — #35 floor)

The metric is gathered through an injected `measure` callback so the verdicts
are pinned on DETERMINISTIC measured latencies — wall-clock jitter must never
flip a no-op to KEEP (a flaky witness is the exact failure the gate refuses).
The shipped `demo.run_demo()` wiring is exercised separately for the three
candidates whose verdicts ride NON-timing witnesses (numerics, the tamper
floor, a real algorithmic win) — also through the injected `measure` seam, so
that path is deterministic too rather than racing the real clock under CI load
(issue #171).
"""

from __future__ import annotations

import functools
import importlib
import sys
from pathlib import Path

import pytest

from dos import improve

_EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "kernel_keep_gate"


@pytest.fixture(scope="module")
def gate_mods():
    """Import the example's `gate`, `kernels`, and `demo` with the dir on the path."""
    sys.path.insert(0, str(_EXAMPLE_DIR))
    try:
        gate = importlib.import_module("gate")
        kernels = importlib.import_module("kernels")
        demo = importlib.import_module("demo")
        yield gate, kernels, demo
    finally:
        sys.path.remove(str(_EXAMPLE_DIR))
        for name in ("gate", "kernels", "demo", "harness", "harness.numerics",
                     "harness.reference", "harness.timing", "harness.vectors"):
            sys.modules.pop(name, None)


# A deterministic measure: latency is keyed off the function's identity so the
# test never depends on the wall clock. The honest kernel is "faster" (higher
# speed score), the reference-copy is EXACTLY baseline speed (identical work →
# identical metric — the honest representation of a no-op), and the exploit is
# fastest (it does the least work — but it is numerically wrong, so speed cannot
# save it). This pins the metric ORDERING; the witnesses' truth values come from
# the real harness (numerics, the tamper check).
def _make_pinned_measure(kernels):
    table = {
        kernels.baseline_kernel: 1000,
        kernels.honest_faster_kernel: 1200,  # strictly faster than baseline
        kernels.reference_copy_kernel: 1000,  # EXACTLY baseline — a true no-op
        kernels.tolerance_exploit_kernel: 1500,  # fastest, but numerically wrong
    }

    def measure(fn, rows):
        return table[fn]

    return measure


@pytest.fixture
def judge_pinned(gate_mods):
    """A `judge` bound to the deterministic measure — for the pinned-verdict rows."""
    gate, kernels, _demo = gate_mods
    measure = _make_pinned_measure(kernels)

    def _judge(candidate):
        return gate.judge(
            candidate,
            freeze_seed=0xABCDEF,
            baseline=kernels.baseline_kernel,
            measure=measure,
        )

    return _judge


# ---------------------------------------------------------------------------
# The four pinned verdicts — the #107 done-condition.
# ---------------------------------------------------------------------------


def test_honest_faster_is_kept(gate_mods, judge_pinned):
    """An honest, correct, genuinely-faster kernel is the ONLY candidate KEPT."""
    gate, kernels, _ = gate_mods
    cand = gate.Candidate(
        fn=kernels.honest_faster_kernel,
        narrated="fused the passes — a real speedup",
        changed_paths=("kernels/honest_faster.py",),
    )
    verdict = judge_pinned(cand)
    assert verdict.verdict is improve.Candidate.KEEP
    assert verdict.revert_cause is None


def test_reference_copy_reverts_no_improvement(gate_mods, judge_pinned):
    """A correct-but-not-faster kernel (re-derives the answer the slow way) is a
    NO_IMPROVEMENT revert — correctness is necessary, not sufficient; a no-op is
    not kept no matter what it narrates."""
    gate, kernels, _ = gate_mods
    cand = gate.Candidate(
        fn=kernels.reference_copy_kernel,
        narrated="rewrote the kernel; this is much faster and correct",  # forged
        changed_paths=("kernels/reference_copy.py",),
    )
    verdict = judge_pinned(cand)
    assert verdict.verdict is improve.Candidate.REVERT
    assert verdict.revert_cause is improve.RevertCause.NO_IMPROVEMENT


def test_tolerance_exploit_reverts_regressed(gate_mods, judge_pinned):
    """A faster-but-numerically-wrong kernel (skips the max-subtraction, wrong on
    the overflow-magnitude band the harness mints) is a REGRESSED revert — the
    numerics floor refuses it BEFORE its speed is even weighed."""
    gate, kernels, _ = gate_mods
    cand = gate.Candidate(
        fn=kernels.tolerance_exploit_kernel,
        narrated="dropped the redundant max-subtraction for a big speedup",
        changed_paths=("kernels/tolerance_exploit.py",),
    )
    verdict = judge_pinned(cand)
    assert verdict.verdict is improve.Candidate.REVERT
    assert verdict.revert_cause is improve.RevertCause.REGRESSED


def test_harness_edit_is_structurally_unkeepable(gate_mods, judge_pinned):
    """A candidate whose diff reaches the witness tree is REGRESSED by the #35
    tamper floor — structurally unkeepable, even when its kernel is the honest,
    correct, faster one. Editing your own measurement harness cannot be kept."""
    gate, kernels, _ = gate_mods
    cand = gate.Candidate(
        fn=kernels.honest_faster_kernel,  # a genuinely good kernel...
        narrated="optimized the kernel AND relaxed harness/numerics.py tolerance",
        changed_paths=("kernels/honest_faster.py", "harness/numerics.py"),  # ...but it tampered
    )
    verdict = judge_pinned(cand)
    assert verdict.verdict is improve.Candidate.REVERT
    assert verdict.revert_cause is improve.RevertCause.REGRESSED


# ---------------------------------------------------------------------------
# The #35 floor as a pure tree check — the bit git authors, not the candidate.
# ---------------------------------------------------------------------------


def test_harness_untouched_floor(gate_mods):
    """`harness_untouched` is the env-authored tamper bit: any path under the
    witness tree → False, regardless of what else the candidate touched."""
    gate, _kernels, _ = gate_mods
    assert gate.harness_untouched(("kernels/honest_faster.py",)) is True
    assert gate.harness_untouched(("kernels/a.py", "kernels/b.py")) is True
    assert gate.harness_untouched(("harness/numerics.py",)) is False
    assert gate.harness_untouched(("kernels/ok.py", "harness/timing.py")) is False
    # Windows-style separators normalize — git may report either.
    assert gate.harness_untouched(("harness\\reference.py",)) is False


def test_narration_moves_nothing(gate_mods, judge_pinned):
    """docs/234 at kernel scale: the candidate's narrated claim is read by
    nothing. The reference-copy carries a forged 'much faster and correct' claim
    and still REVERTs; flipping the claim to honest does not change the verdict."""
    gate, kernels, _ = gate_mods
    base = dict(fn=kernels.reference_copy_kernel, changed_paths=("kernels/reference_copy.py",))
    forged = judge_pinned(gate.Candidate(narrated="much faster and correct!!", **base))
    honest = judge_pinned(gate.Candidate(narrated="probably a no-op, sorry", **base))
    assert forged.verdict is honest.verdict is improve.Candidate.REVERT
    assert forged.revert_cause is honest.revert_cause


# ---------------------------------------------------------------------------
# The real-clock demo path — robust verdicts only (no timing-sensitive no-op).
# ---------------------------------------------------------------------------


def test_real_clock_demo_robust_verdicts(gate_mods, monkeypatch):
    """The shipped `demo.run_demo()` wiring, with the timer INJECTED so the
    verdicts cannot flake on wall-clock jitter (issue #171).

      honest_faster     → KEEP    (a real algorithmic win — beats baseline every run)
      tolerance_exploit → REVERT  (numerics witness red — independent of timing)
      harness_edit      → REVERT  (tamper floor — the gate never even times it)

    Previously this asserted the verdicts off `demo`'s default REAL env clock,
    trusting that the honest kernel always out-measures the baseline. On a
    loaded CI runner that wall-clock margin can collapse: under the
    `windows-latest` ~10-min suite the honest kernel's measured latency tied or
    lost to the baseline, flipping `KEEP` to a NO_IMPROVEMENT `REVERT` and
    failing with `assert 'REVERT' == 'KEEP'` (issue #171). The keep-gate is
    behaving correctly — a flaky witness is exactly what it refuses — so the fix
    is to stop racing the wall clock here.

    We use the gate's documented injectable-`measure` seam: the demo's `judge`
    is bound to a DETERMINISTIC, monotonic measure keyed on the kernel's
    identity, so the honest kernel scores strictly higher than the baseline
    every run, with no clock. This still drives the full real `run_demo()`
    flow — the four candidates, the numerics witness, and the #35 tamper floor;
    only the noisy timer is replaced. The reference-copy no-op is still NOT
    asserted here (it does identical work to the baseline; its verdict is pinned
    deterministically above, not raced here).
    """
    _gate, kernels, demo = gate_mods

    # A deterministic, monotonic speed score keyed off the kernel's identity:
    # honest is strictly faster than the baseline (higher score), the no-op ties
    # the baseline, the exploit is fastest (but numerically wrong, so speed can't
    # save it). Same ORDERING the wall clock is meant to find — without the
    # jitter that flips it under CI load.
    speed = {
        kernels.baseline_kernel: 1000,
        kernels.honest_faster_kernel: 1200,  # strictly faster than baseline
        kernels.reference_copy_kernel: 1000,  # EXACTLY baseline — a true no-op
        kernels.tolerance_exploit_kernel: 1500,  # fastest, but numerically wrong
    }

    def fake_measure(fn, rows, repeats=5):
        return speed[fn]

    # Inject through the gate's `measure` seam (the "timer lives at the boundary"
    # split the host documents); the verdict logic itself is untouched.
    monkeypatch.setattr(demo, "judge", functools.partial(demo.judge, measure=fake_measure))

    rows = {r["candidate"]: r for r in demo.run_demo()}
    assert rows["honest_faster"]["verdict"] == "KEEP"
    assert rows["tolerance_exploit"]["verdict"] == "REVERT"
    assert rows["tolerance_exploit"]["revert_cause"] == "regressed"
    assert rows["harness_edit"]["verdict"] == "REVERT"
    assert rows["harness_edit"]["revert_cause"] == "regressed"
