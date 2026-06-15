"""_ladder.py — fold the detectors over a LADDER of on-device model recording dirs and
print the recoverable-fraction-vs-param-size curve (docs/341 §3, the inverted-U test).

`harness.py --recordings <dir>` folds one model's runs. This stitches several together
into the capability curve, ordered by declared parameter size, so the inverted-U
hypothesis is visible in one table: recoverability LOW at the sub-1B floor (incoherent),
PEAKS in the 1.5-4B tool-tuned band (coherent-but-wrong), falls again at frontier.

Usage (after running _drive_cpu_model.py for each model into its own --out dir):

    python -m benchmark.smartphone_tier._ladder \
        0.13:SmolLM2-135M:/tmp/smol \
        0.5:Qwen2.5-0.5B:/tmp/q05 \
        1.5:Qwen2.5-1.5B:/tmp/q15 \
        3.0:Qwen2.5-3B:/tmp/q3

Each arg is `params_b:label:dir`. params_b is the size in billions (the x-axis); the
rungs are sorted by it. Opt-in tool (needs the dirs produced by a CPU run); never part
of the $0 sweep.
"""
from __future__ import annotations

import sys
from typing import List, Tuple

from .harness import render_ascii, run_recordings, SweepResult, TierResult


def _parse_rung(arg: str) -> Tuple[float, str, str]:
    """`params_b:label:dir` -> (params_b, label, dir). Dir may contain ':' (a drive)."""
    parts = arg.split(":", 2)
    if len(parts) != 3:
        raise SystemExit(f"bad rung {arg!r}; expected params_b:label:dir")
    return float(parts[0]), parts[1], parts[2]


def run_ladder(rungs: List[Tuple[float, str, str]]) -> SweepResult:
    """Fold each model's recordings, label its tier with the param size, sort weak->strong."""
    rungs = sorted(rungs, key=lambda r: r[0])   # smallest param first (the weak end)
    tiers: List[TierResult] = []
    for params_b, label, d in rungs:
        sub = run_recordings(d, tier_name=f"{label} ({params_b:g}B)")
        # run_recordings returns a 1-tier SweepResult; lift its tier in.
        if sub.tiers:
            tiers.append(sub.tiers[0])
    return SweepResult(tiers=tiers, source="recordings-ladder")


def main(argv: List[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    rungs = [_parse_rung(a) for a in argv]
    res = run_ladder(rungs)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(render_ascii(res))
    # a measurement reports the curve; it never fails the directional check (the
    # inverted-U is the finding, not a falsifier — a non-monotone curve is allowed).
    return 0


if __name__ == "__main__":
    sys.exit(main())
