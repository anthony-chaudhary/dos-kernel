"""real_corpus.py — the MEASUREMENT (docs/341 §4), not the pre-registration.

`tiers.py` declares a synthetic failure-mode shape; `harness.py` folds the real
kernel detectors over it. This module replaces the synthetic corpus with REAL data:
the committed Toolathlon replay (`benchmark/toolathlon/_results/replay_all_rows.csv`),
7,116 recorded runs across 22 real models, each row carrying the third-party oracle
verdict (`passed`) and the three detectors' pre-computed fires (`dangling_fired`,
`tool_stream_fired`, `terminal_error_fired`) — the exact rows behind the paper's
detector table.

Because the corpus has no parameter counts but DOES have each model's task pass-rate,
we use **pass-rate as the capability axis** (a weak model passes few tasks; a strong
one passes many) and bin models into capability tiers. The recoverable fraction is
then, per tier, `failed runs with >=1 detector fired / all failed runs` — the same
unit `harness.py` reports, measured instead of declared.

THE HONEST FINDING (the reason this module exists): the real recoverable fraction is
much SMALLER than the synthetic pre-registration. The thesis DIRECTION holds — the
fraction falls as capability rises (Pearson r ~= -0.58 across 22 models) — but the
weak-end LEVEL is ~14%, not the synthetic 80%. The synthetic magnitudes were
optimistic; the direction was right. That gap is the point of measuring (docs/145):
a declared shape is a hypothesis, the corpus is the verdict.

This module is pure data + arithmetic. It reuses `harness.TierResult` so the real and
synthetic results render through the SAME table.
"""
from __future__ import annotations

import csv
import os
from typing import Dict, List, Optional, Tuple

from .harness import SweepResult, TierResult


# the committed replay rows (the paper's detector-table source). Resolved relative to
# this file so it works from any cwd (the SubstrateConfig.root discipline, applied to
# a benchmark consumer — never __file__-relative for kernel code, but fine here).
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
DEFAULT_CSV = os.path.join(_REPO, "benchmark", "toolathlon", "_results", "replay_all_rows.csv")


# Capability tiers by task pass-rate. The boundaries are declared (not fit to the
# data) so the binning is auditable; they split the 22 models into four populated
# bands from "very weak" (the most phone-like behavior) to "strong".
_TIER_BANDS: Tuple[Tuple[str, float, float], ...] = (
    ("very-weak (<12% pass)", 0.00, 0.12),
    ("weak (12-20%)",         0.12, 0.20),
    ("mid (20-32%)",          0.20, 0.32),
    ("strong (>=32%)",        0.32, 1.01),
)


def _tier_of(pass_rate: float) -> str:
    for name, lo, hi in _TIER_BANDS:
        if lo <= pass_rate < hi:
            return name
    return _TIER_BANDS[-1][0]


def _tb(x: str) -> bool:
    return str(x).strip().lower() == "true"


def run_real_corpus(csv_path: Optional[str] = None) -> SweepResult:
    """Fold the committed replay rows into the recoverable-fraction-vs-capability curve.

    Reads the pre-computed detector fires + the oracle `passed` verdict from the CSV,
    bins each model by its measured pass-rate, and tallies the recoverable fraction
    per capability tier. Returns a `SweepResult` (same shape as the synthetic sweep),
    so it renders through the same table.
    """
    path = csv_path or DEFAULT_CSV
    rows = list(csv.DictReader(open(path, encoding="utf-8")))

    # pass 1: per-model pass-rate (the capability axis).
    n: Dict[str, int] = {}
    npass: Dict[str, int] = {}
    for r in rows:
        m = r["model"]
        n[m] = n.get(m, 0) + 1
        if _tb(r["passed"]):
            npass[m] = npass.get(m, 0) + 1
    cap = {m: (npass.get(m, 0) / n[m]) for m in n}

    # pass 2: tally per capability tier.
    tiers: Dict[str, TierResult] = {}
    members: Dict[str, set] = {}
    for r in rows:
        m = r["model"]
        tname = _tier_of(cap[m])
        tr = tiers.setdefault(tname, TierResult(name=tname, exemplars=()))
        members.setdefault(tname, set()).add(m)
        fired = {
            "dangle": _tb(r["dangling_fired"]),
            "mint": False,   # the replay corpus has no arg_provenance column; absent here
            "loop": _tb(r["tool_stream_fired"]),
        }
        # terminal_error is a real Toolathlon detector but maps to no smartphone_tier
        # column; fold it into the recoverable union so the measurement is not understated.
        term = _tb(r["terminal_error_fired"])
        if _tb(r["passed"]):
            tr.n_passed += 1
            for k, v in fired.items():
                tr.pass_fire[k] += 1 if v else 0
        else:
            tr.n_failed += 1
            for k, v in fired.items():
                tr.fail_fire[k] += 1 if v else 0
            # recoverable = any byte-clean detector fired (dangle/loop/terminal_error).
            if fired["dangle"] or fired["loop"] or term:
                tr.recoverable += 1

    # enrichment flags (kept for the table; on real data dangle/loop are enriched).
    for tname, tr in tiers.items():
        tr.exemplars = tuple(sorted(members[tname]))
        tr.enriched = {k: tr.fail_rate(k) > tr.pass_rate(k) for k in tr.fail_fire}

    # order weak -> strong (the same axis direction as the synthetic sweep).
    band_order = [b[0] for b in _TIER_BANDS]
    ordered = [tiers[b] for b in band_order if b in tiers]
    return SweepResult(tiers=ordered, source=f"real:{os.path.relpath(path, _REPO)}")
