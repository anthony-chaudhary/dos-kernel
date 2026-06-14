"""docs/341 — pin the smartphone_tier (recoverable-fraction-vs-capability) benchmark.

The whole public claim, asserted from the suite so it cannot silently rot:

  * The recoverable fraction FALLS monotonically from the smallest param tier
    (`<=1B`, the most phone-like) to `frontier` — the thesis's directional
    prediction (docs/341 §3): a trust substrate flags more of a WEAK model's
    failures than a strong model's.
  * The frontier tier is the gemini NULL self-test: its recoverable fraction is
    clearly below the smallest tier's, dominated by the unreachable (silent-stop)
    remainder (docs/149).
  * Each declared failure kind's detector ACTUALLY fires (the corpus is not rigged
    with labels the kernel disagrees with) — the instrument is live.
  * The harness does NOT re-implement a detector rule — it calls the three real
    `dos.*` detectors (the kernel-not-reimplemented discipline, forge_arena).

The benchmark package uses relative imports, so it is loaded as a package with
`benchmark/` on sys.path (the forge_arena / memory_integrity convention; `benchmark/`
is not importable from the suite's default sys.path).
"""
from __future__ import annotations

import sys
from pathlib import Path

_BENCH = str(Path(__file__).resolve().parents[1] / "benchmark")
if _BENCH not in sys.path:
    sys.path.insert(0, _BENCH)

from smartphone_tier.tiers import default_tiers, trajectories_for  # noqa: E402
from smartphone_tier.harness import (  # noqa: E402
    fires_dangle, fires_loop, fires_mint, fold_tier, run_sweep,
)


def test_recoverable_fraction_is_monotone_falling():
    """The headline: recoverable fraction is non-increasing <=1B -> frontier."""
    res = run_sweep()
    fr = [t.recoverable_fraction for t in res.tiers]
    assert len(fr) >= 3
    for i in range(len(fr) - 1):
        assert fr[i] >= fr[i + 1] - 1e-9, (
            f"recoverable fraction rose from {res.tiers[i].name} ({fr[i]:.2%}) to "
            f"{res.tiers[i + 1].name} ({fr[i + 1]:.2%}) — the directional prediction broke")


def test_frontier_is_the_null_self_test():
    """The strongest tier's recoverable fraction is clearly below the weakest's —
    a frontier model's failures are mostly unreachable (the gemini null, docs/149)."""
    res = run_sweep()
    assert res.tiers[-1].recoverable_fraction < res.tiers[0].recoverable_fraction
    # and the frontier is dominated by the unreachable remainder.
    assert res.tiers[-1].unreachable > res.tiers[-1].recoverable


def test_smallest_tier_is_dos_shaped():
    """The most phone-like tier's failures are majority DOS-recoverable — the value
    proposition. (A weak model fails in ways the trust substrate can flag.)"""
    res = run_sweep()
    assert res.tiers[0].recoverable_fraction > 0.5


def test_each_declared_kind_actually_fires_its_detector():
    """The corpus is not rigged: every dangle/mint/loop trajectory really fires its
    kernel detector, and every silent/passed trajectory fires NONE. The harness
    re-derives the fire from the kernel; this proves the labels are honest."""
    tier = default_tiers()[0]  # the <=1B tier has every kind
    by_kind = {}
    for t in trajectories_for(tier):
        by_kind.setdefault(t.gold_kind, []).append(t)

    assert all(fires_dangle(t) for t in by_kind["dangle"])
    assert all(fires_mint(t) for t in by_kind["mint"])
    assert all(fires_loop(t) for t in by_kind["loop"])
    # silent failures and clean passes fire NONE of the three.
    for t in by_kind["silent"] + by_kind[""]:
        assert not (fires_dangle(t) or fires_mint(t) or fires_loop(t)), (
            f"a {t.gold_kind or 'passed'} trajectory unexpectedly fired a detector — "
            "the corpus or a detector drifted")


def test_enrichment_guard_excludes_noise():
    """A detector counts toward the recoverable fraction only if it is enriched on
    failures (fail-rate > pass-rate). The frontier MINT detector never fires, so it
    must be excluded as noise (enriched=False)."""
    res = run_sweep()
    frontier = res.tiers[-1]
    assert frontier.fail_fire["mint"] == 0
    assert frontier.enriched["mint"] is False


def test_soundness_checks_pass_and_exit_zero():
    res = run_sweep()
    ck = res.checks()
    assert ck["monotone"] and ck["frontier_low"] and ck["detectors_fire"]
    assert res.sound()


def test_json_shape():
    """The machine surface carries the curve + the per-tier enrichment + the checks."""
    d = run_sweep().to_dict()
    assert d["benchmark"] == "smartphone_tier"
    assert d["source"] == "synthetic"
    assert set(d["curve"]) == {t.name for t in default_tiers()}
    assert set(d["checks"]) == {"monotone", "frontier_low", "detectors_fire"}
    for t in d["tiers"]:
        assert set(t["enriched"]) == {"dangle", "mint", "loop"}
        assert 0.0 <= t["recoverable_fraction"] <= 1.0


def test_kernel_verdict_not_reimplemented():
    """The folds must route through the real `dos.*` detectors, not a local copy.

    Pinned structurally: the harness module binds the kernel classifiers, and a
    fold's result for a known trajectory equals the kernel's own verdict on the
    same input. A drifted re-implementation breaks this.
    """
    import smartphone_tier.harness as H
    assert getattr(H, "classify_stop").__module__ == "dos.dangling_intent"
    assert getattr(H, "classify_stream").__module__ == "dos.tool_stream"
    assert getattr(H, "classify_call").__module__ == "dos.arg_provenance"

    # a known dangling trajectory: the fold agrees with the kernel on the same input.
    from dos.dangling_intent import StopEvidence, classify_stop
    dangles = [t for t in trajectories_for(default_tiers()[0]) if t.gold_kind == "dangle"]
    t = dangles[0]
    direct = classify_stop(StopEvidence(final_turn_text=t.final_turn,
                                        results_after_turn=t.results_after)).is_dangling
    assert fires_dangle(t) == direct is True
