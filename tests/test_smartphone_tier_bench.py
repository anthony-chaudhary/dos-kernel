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
from smartphone_tier.real_corpus import DEFAULT_CSV, run_real_corpus  # noqa: E402


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


# ---------------------------------------------------------------------------
# The MEASUREMENT (docs/341 §4) — the real Toolathlon replay corpus, not the
# synthetic pre-registration. The honest counterweight to the synthetic curve.
# ---------------------------------------------------------------------------
import os  # noqa: E402

import pytest  # noqa: E402

_HAS_CSV = os.path.isfile(DEFAULT_CSV)
_needs_csv = pytest.mark.skipif(not _HAS_CSV, reason="committed replay CSV not present")


@_needs_csv
def test_real_corpus_direction_holds():
    """On REAL data the thesis direction holds: recoverable fraction is non-increasing
    from the weakest capability tier to the strongest."""
    res = run_real_corpus()
    fr = [t.recoverable_fraction for t in res.tiers]
    assert len(fr) >= 3
    for i in range(len(fr) - 1):
        assert fr[i] >= fr[i + 1] - 1e-9
    assert res.source.startswith("real:")


@_needs_csv
def test_real_corpus_level_is_modest_not_eighty_percent():
    """The honesty pin: the REAL weak-end recoverable fraction is FAR below the
    synthetic 80% — single digits to low double digits. If this ever reads ~80%, the
    real reader has silently drifted toward the synthetic shape (the failure we are
    guarding against)."""
    real = run_real_corpus()
    syn = run_sweep()
    real_weak = real.tiers[0].recoverable_fraction
    syn_weak = syn.tiers[0].recoverable_fraction
    assert real_weak < 0.30, f"real weak-end {real_weak:.0%} is implausibly high"
    assert real_weak < syn_weak - 0.30, (
        f"the real measurement ({real_weak:.0%}) is supposed to be much lower than the "
        f"synthetic pre-registration ({syn_weak:.0%}) — the whole point of measuring")


@_needs_csv
def test_real_corpus_recall_matches_paper_ceiling():
    """The overall real recall (recovered / all failures) is the paper's low-single-digit
    ceiling, not a large number — a cross-check against the published detector table."""
    res = run_real_corpus()
    tot_fail = sum(t.n_failed for t in res.tiers)
    tot_rec = sum(t.recoverable for t in res.tiers)
    overall = tot_rec / tot_fail
    assert 0.02 < overall < 0.12, f"overall real recall {overall:.1%} off the paper ceiling"


# ---------------------------------------------------------------------------
# The on-device tool-caller ladder (docs/341 §3a) — REAL Qwen2.5 runs, committed as
# fixtures so the rising edge of the inverted-U reproduces at $0 (no model needed).
# ---------------------------------------------------------------------------
_REC = os.path.join(_BENCH, "smartphone_tier", "_recordings")
_HAS_LADDER = os.path.isdir(os.path.join(_REC, "q05")) and os.path.isdir(os.path.join(_REC, "q15"))
_needs_ladder = pytest.mark.skipif(not _HAS_LADDER, reason="committed on-device fixtures not present")


@_needs_ladder
def test_ondevice_ladder_recoverability_RISES_with_competence():
    """The corrected finding (docs/341 §3a): among REAL small tool-calling models, the
    DOS-recoverable fraction RISES with size/competence — the OPPOSITE of the naive
    'weaker = more recoverable' guess. A bigger tool-tuned model reads-before-it-writes,
    so when it mints a fake id the detector has a corpus to refute it against; the 0.5B
    model skips the reads and mints into an empty corpus (uncatchable)."""
    from smartphone_tier.harness import run_recordings
    q05 = run_recordings(os.path.join(_REC, "q05")).tiers[0]
    q15 = run_recordings(os.path.join(_REC, "q15")).tiers[0]
    # 0.5B is in the detector blind spot; 1.5B is catchable — recoverability RISES.
    assert q05.recoverable_fraction < q15.recoverable_fraction
    assert q05.recoverable_fraction == 0.0   # skips reads -> empty corpus -> MINT abstains
    assert q15.recoverable_fraction >= 0.5   # reads first -> minted id refuted -> MINT fires
    # the catch is the MINT detector (a hallucinated id on a real mutating call).
    assert q15.fail_fire["mint"] > 0


@_needs_ladder
def test_ondevice_mint_is_the_real_failure_shape():
    """The 1.5B catch is a genuine minted-FK: a user_id arg that appears in NO env blob,
    on a real mutating call, after the model did the reads. This is the arg_provenance
    target produced by a real on-device tool-caller — folded by the live kernel."""
    import json
    from dos.arg_provenance import (
        CorpusSource, EnvBlob, PriorResults, ToolArg, ToolCall, classify_call,
    )
    rec = json.load(open(os.path.join(_REC, "q15", "assign-incident_0.json"), encoding="utf-8"))
    assert rec["mutating_call"] is not None and rec["env_blobs"], "fixture lost its mint surface"
    tool, args = rec["mutating_call"]
    call = ToolCall(tool_name=tool,
                    args=tuple(ToolArg(name=k, value=v) for k, v in args.items()),
                    is_mutating=True)
    prior = PriorResults(blobs=tuple(EnvBlob(text=b, source=CorpusSource.TOOL_RESULT)
                                     for b in rec["env_blobs"]))
    # the kernel itself says: do not believe this call (an id was minted from nowhere).
    assert classify_call(call, prior).believe is False
