"""harness.py — the recoverable-fraction-vs-capability sweep (docs/341).

For each model tier, fold the THREE real kernel detectors over every trajectory and
report the deduped DOS-recoverable failure fraction — the share of FAILED runs that at
least one ENRICHED detector would advisory-flag:

  DANGLE  — `dos.dangling_intent.classify_stop`  (narrating premature stop)
  MINT    — `dos.arg_provenance.classify_call`   (a minted-from-nowhere id on a write)
  LOOP    — `dos.tool_stream.classify_stream`    (a no-progress repeated triple)

The enrichment guard (the `weak_model_gate.py` honesty): a detector counts toward the
recoverable fraction ONLY if its fail-run fire-rate exceeds its pass-run fire-rate — i.e.
it is enriched on the failures it claims to recover. A detector that fires equally on
passes is NOISE (a false positive) and is excluded. The recoverable fraction is then the
deduped count of failed runs fired on by >=1 enriched detector.

THE HEADLINE (the thesis's directional prediction, docs/341 §3): the recoverable fraction
FALLS monotonically from `<=1B` to `frontier`. A smartphone-tier model fails in DOS-shaped
ways DOS can flag; a frontier model's failures are almost all SILENT (the unreachable
remainder, docs/149). The harness asserts that monotone fall + the frontier null as
soundness checks and exits non-zero if either breaks.

This module CALLS the kernel detectors; it never re-encodes a detector rule (pinned by
`tests/test_smartphone_tier_bench.py::test_kernel_verdict_not_reimplemented`). The
synthetic tier corpus is a DECLARED pre-registration (docs/145 honesty) — point
`--recordings <dir>` at real on-device model trajectory dumps to fold the SAME detectors
over real data; nothing else changes.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Relative imports so this resolves under BOTH `python -m benchmark.smartphone_tier.harness`
# (the runner's module form, from repo root) and a file-path launch (the forge_arena idiom).
from .tiers import ModelTier, Trajectory, default_tiers, trajectories_for

from dos.dangling_intent import StopEvidence, classify_stop
from dos.tool_stream import StreamState, StreamStep, ToolStream, classify_stream
from dos.arg_provenance import (
    CorpusSource, EnvBlob, PriorResults, ToolArg, ToolCall, classify_call,
)


# ---------------------------------------------------------------------------
# The three detector folds. Each takes a Trajectory and returns True iff the REAL
# kernel detector fires on it. No re-encoding — the kernel decides.
# ---------------------------------------------------------------------------
def fires_dangle(t: Trajectory) -> bool:
    """True iff `dangling_intent` flags a narrating premature stop on the terminal turn."""
    ev = StopEvidence(final_turn_text=t.final_turn, results_after_turn=t.results_after)
    return classify_stop(ev).is_dangling


def fires_loop(t: Trajectory) -> bool:
    """True iff `tool_stream` flags REPEATING/STALLED at any prefix of the stream."""
    steps = [StreamStep(tool_name=tn, args_digest=ad, result_digest=rd)
             for (tn, ad, rd) in t.steps]
    for i in range(1, len(steps) + 1):
        v = classify_stream(ToolStream(steps=tuple(steps[:i])))
        if v.state is not StreamState.ADVANCING:
            return True
    return False


def fires_mint(t: Trajectory) -> bool:
    """True iff `arg_provenance` flags a minted id on the mutating call (believe=False)."""
    if t.mutating_call is None:
        return False
    tool_name, args = t.mutating_call
    call = ToolCall(
        tool_name=tool_name,
        args=tuple(ToolArg(name=k, value=v) for k, v in args.items()),
        is_mutating=True,
    )
    prior = PriorResults(
        blobs=tuple(EnvBlob(text=b, source=CorpusSource.TOOL_RESULT) for b in t.env_blobs)
    )
    return not classify_call(call, prior).believe


_DETECTORS = (("dangle", fires_dangle), ("mint", fires_mint), ("loop", fires_loop))


# ---------------------------------------------------------------------------
# Per-tier fold — the enriched, deduped recoverable fraction.
# ---------------------------------------------------------------------------
@dataclass
class TierResult:
    name: str
    exemplars: Tuple[str, ...]
    n_failed: int = 0
    n_passed: int = 0
    fail_fire: Dict[str, int] = field(default_factory=lambda: {"dangle": 0, "mint": 0, "loop": 0})
    pass_fire: Dict[str, int] = field(default_factory=lambda: {"dangle": 0, "mint": 0, "loop": 0})
    recoverable: int = 0          # deduped failed runs fired on by >=1 ENRICHED detector
    enriched: Dict[str, bool] = field(default_factory=dict)

    @property
    def recoverable_fraction(self) -> float:
        return (self.recoverable / self.n_failed) if self.n_failed else 0.0

    @property
    def unreachable(self) -> int:
        return self.n_failed - self.recoverable

    def fail_rate(self, k: str) -> float:
        return (self.fail_fire[k] / self.n_failed) if self.n_failed else 0.0

    def pass_rate(self, k: str) -> float:
        return (self.pass_fire[k] / self.n_passed) if self.n_passed else 0.0

    def to_dict(self) -> dict:
        return {
            "tier": self.name,
            "exemplars": list(self.exemplars),
            "n_failed": self.n_failed,
            "n_passed": self.n_passed,
            "fail_fire": dict(self.fail_fire),
            "pass_fire": dict(self.pass_fire),
            "fail_rate": {k: round(self.fail_rate(k), 4) for k in self.fail_fire},
            "pass_rate": {k: round(self.pass_rate(k), 4) for k in self.pass_fire},
            "enriched": dict(self.enriched),
            "recoverable": self.recoverable,
            "unreachable": self.unreachable,
            "recoverable_fraction": round(self.recoverable_fraction, 4),
        }


def fold_tier(name: str, exemplars: Tuple[str, ...], trajs: List[Trajectory]) -> TierResult:
    """Fold the three real detectors over one tier's trajectories. PURE over the corpus."""
    res = TierResult(name=name, exemplars=exemplars)
    rows: List[Tuple[bool, Dict[str, bool]]] = []   # (failed?, {detector: fired})
    for t in trajs:
        fired = {k: fn(t) for k, fn in _DETECTORS}
        if t.failed:
            res.n_failed += 1
            for k, v in fired.items():
                res.fail_fire[k] += 1 if v else 0
        else:
            res.n_passed += 1
            for k, v in fired.items():
                res.pass_fire[k] += 1 if v else 0
        rows.append((t.failed, fired))

    # a detector is SIGNAL iff enriched on failures (fail-rate > pass-rate); else NOISE.
    res.enriched = {k: res.fail_rate(k) > res.pass_rate(k) for k in res.fail_fire}
    # deduped recoverable = failed runs fired on by AT LEAST ONE enriched detector.
    res.recoverable = sum(
        1 for failed, fired in rows
        if failed and any(fired[k] and res.enriched[k] for k in fired)
    )
    return res


# ---------------------------------------------------------------------------
# The whole sweep + the soundness checks.
# ---------------------------------------------------------------------------
@dataclass
class SweepResult:
    tiers: List[TierResult]
    source: str = "synthetic"     # "synthetic" (declared corpus) or "recordings:<dir>"

    def checks(self) -> dict:
        """The soundness falsifiers (asserted in the test + the exit code):
          * monotone     — the recoverable fraction is NON-INCREASING from the smallest
            tier to the largest (the thesis's directional prediction). The headline.
          * frontier_low — the largest tier's recoverable fraction is below the smallest
            tier's by a clear margin (the gemini-null self-test: a strong model's
            failures are mostly unreachable).
          * detectors_fire — at least one detector was enriched on the smallest tier
            (the instrument is live, not dead-abstaining on everything).
        """
        fr = [t.recoverable_fraction for t in self.tiers]
        monotone = all(fr[i] >= fr[i + 1] - 1e-9 for i in range(len(fr) - 1))
        frontier_low = (len(fr) >= 2 and fr[-1] <= fr[0] - 1e-9)
        detectors_fire = bool(self.tiers and any(self.tiers[0].enriched.values()))
        return {
            "monotone": monotone,
            "frontier_low": frontier_low,
            "detectors_fire": detectors_fire,
        }

    def sound(self) -> bool:
        c = self.checks()
        return c["monotone"] and c["frontier_low"] and c["detectors_fire"]

    def to_dict(self) -> dict:
        return {
            "benchmark": "smartphone_tier",
            "doc": "docs/341",
            "source": self.source,
            "n_tiers": len(self.tiers),
            "tiers": [t.to_dict() for t in self.tiers],
            "curve": {t.name: round(t.recoverable_fraction, 4) for t in self.tiers},
            "checks": self.checks(),
        }


def run_sweep(tiers: Optional[List[ModelTier]] = None) -> SweepResult:
    """Drive every tier's synthetic corpus through the three real detectors."""
    tiers = tiers if tiers is not None else default_tiers()
    return SweepResult(
        tiers=[fold_tier(t.name, t.exemplars, trajectories_for(t)) for t in tiers],
        source="synthetic",
    )


# ---------------------------------------------------------------------------
# The drop-in path: fold the SAME detectors over REAL on-device recordings.
# A recordings dir holds one JSON per run; the loader maps each to a Trajectory.
# This is the seam that turns the pre-registration into a measurement (docs/341 §4).
# ---------------------------------------------------------------------------
def _traj_from_record(rec: dict, task_id: str) -> Trajectory:
    """Map a recorded run JSON to the reduced Trajectory datum. Tolerant of absent
    fields (a recording missing a stream just yields no LOOP signal). Mirrors the
    weak_model_gate.py boundary-reader shape; never trusts a self-reported label."""
    steps = tuple(
        (str(s.get("tool", "")), str(s.get("args_digest", s.get("args", ""))),
         (None if s.get("result_digest") is None and s.get("result") is None
          else str(s.get("result_digest", s.get("result", "")))))
        for s in (rec.get("steps") or [])
    )
    mc = rec.get("mutating_call")
    mutating_call = (str(mc[0]), dict(mc[1])) if mc else None
    return Trajectory(
        task_id=task_id,
        failed=bool(rec.get("failed", rec.get("overall_success") is False)),
        gold_kind=str(rec.get("gold_kind", "")),
        final_turn=str(rec.get("final_turn", rec.get("final_turn_text", "")) or ""),
        results_after=int(rec.get("results_after", rec.get("results_after_turn", 0)) or 0),
        steps=steps,
        mutating_call=mutating_call,
        env_blobs=tuple(str(b) for b in (rec.get("env_blobs") or [])),
    )


def run_recordings(recordings_dir: str, tier_name: str = "recorded") -> SweepResult:
    """Fold the three real detectors over a directory of recorded runs (one tier)."""
    files = sorted(glob.glob(os.path.join(recordings_dir, "**", "*.json"), recursive=True))
    trajs: List[Trajectory] = []
    for f in files:
        try:
            # utf-8-sig tolerates a leading BOM (Windows dumps often carry one);
            # it decodes plain UTF-8 unchanged.
            rec = json.load(open(f, encoding="utf-8-sig"))
        except Exception:
            continue
        # a record may be one run or a list of runs
        runs = rec if isinstance(rec, list) else [rec]
        for i, r in enumerate(runs):
            if isinstance(r, dict):
                trajs.append(_traj_from_record(r, f"{os.path.basename(f)}:{i}"))
    return SweepResult(
        tiers=[fold_tier(tier_name, (recordings_dir,), trajs)],
        source=f"recordings:{recordings_dir}",
    )


# ---------------------------------------------------------------------------
# Rendering — always-on ASCII (the forge_arena idiom); --json for machines.
# ---------------------------------------------------------------------------
def render_ascii(res: SweepResult) -> str:
    lines: List[str] = []
    lines.append("smartphone_tier (docs/341) — does the DOS-recoverable failure fraction")
    lines.append("RISE as the model shrinks toward on-device / smartphone size?")
    lines.append(f"  source: {res.source}   (synthetic = a DECLARED pre-registration, "
                 "not a measurement)")
    lines.append("")
    lines.append("  tier        failed  recoverable  unreachable  recoverable-fraction")
    lines.append("  ----        ------  -----------  -----------  --------------------")
    for t in res.tiers:
        bar = "#" * int(round(t.recoverable_fraction * 20))
        lines.append(f"  {t.name:<10}  {t.n_failed:>6}  {t.recoverable:>11}  "
                     f"{t.unreachable:>11}  {t.recoverable_fraction:>6.1%}  {bar}")
    lines.append("")
    lines.append("  per-detector fire-rate on FAILED runs (enriched? = signal vs noise):")
    for t in res.tiers:
        cells = []
        for k in ("dangle", "mint", "loop"):
            tag = "" if t.enriched.get(k) else "*"   # * = excluded as noise
            cells.append(f"{k}={t.fail_rate(k):.0%}{tag}")
        lines.append(f"    {t.name:<10}  " + "  ".join(cells))
    lines.append("    (* = fired no more on failures than on passes -> excluded as noise)")
    lines.append("")
    ck = res.checks()
    ok = "PASS" if res.sound() else "FAIL"
    lines.append(f"  soundness checks [{ok}]: monotone {ck['monotone']} | "
                 f"frontier-low {ck['frontier_low']} | detectors-fire {ck['detectors_fire']}")
    if not ck["monotone"]:
        lines.append("  !! the recoverable fraction is NOT non-increasing toward frontier —")
        lines.append("     the directional prediction (docs/341 §3) does not hold on this corpus.")
    lines.append("")
    lines.append("  the honest caption: the magnitudes above are a SYNTHETIC pre-registration")
    lines.append("  of the failure-mode shape (docs/145). Point --recordings at on-device model")
    lines.append("  dumps (Llama-3.2-1B / Qwen2.5-1.5B / Phi-3-mini) to fold the SAME real kernel")
    lines.append("  detectors over real data — that is the measurement.")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="smartphone_tier (docs/341): the DOS-recoverable failure fraction "
                    "vs model param tier, folded by the REAL kernel detectors.")
    p.add_argument("--json", action="store_true", help="emit the result as JSON")
    p.add_argument("--recordings", default="",
                   help="a directory of recorded on-device model runs (one JSON per run); "
                        "fold the SAME detectors over real data instead of the synthetic corpus")
    p.add_argument("--tier-name", default="recorded",
                   help="label for the recordings tier (default: recorded)")
    args = p.parse_args(argv)

    if args.recordings:
        res = run_recordings(args.recordings, tier_name=args.tier_name)
    else:
        res = run_sweep()

    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8")   # Windows console is cp1252
        except Exception:
            pass
        print(render_ascii(res))

    # Exit non-zero if the synthetic sweep is not sound (a real-recordings run reports
    # the curve but never fails the directional check — a measurement may legitimately
    # come out any shape; the falsifier is only meaningful on the declared corpus).
    if args.recordings:
        return 0
    return 0 if res.sound() else 1


if __name__ == "__main__":
    sys.exit(main())
