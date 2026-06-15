"""The ablation: run each skill as `original` vs `-dos`, score the silent over-claim rate, report cost.

    python -m benchmark.skill_dos_ablation.harness            # the ASCII report (exit 0 iff invariants hold)
    python -m benchmark.skill_dos_ablation.harness --json     # machine-readable rows
    python -m benchmark.skill_dos_ablation.harness --check    # assert the invariants; exit 1 on drift
    python -m pytest -q benchmark/skill_dos_ablation/         # the tests

THE METRIC: silent over-claim rate. For each (skill, variant), over the RIGGED-FAILURE tasks (the
tasks whose ground truth is "FAILED"):

    silent over-claim = (# tasks where the variant DECLARED success when truth = FAILED) / (# FAILED tasks)

The denominator — the count of rigged-failure tasks — is reported EXPLICITLY (the issue's honesty
requirement). We report refused / advisory counts, never a "caught N" headline (the `dos helped`
discipline, docs/345 §6 / docs/333):

  * the ORIGINAL variant closes its trust seam on the agent's own `final_text` (forgeable) — it
    declares success whenever the agent claimed success, so it over-claims on every rigged failure.
  * the `-dos` variant closes the seam on a witness the agent did NOT author (witnesses.py). On a
    DOS-groundable skill the witness returns FAILED on the rigged task → the success claim is
    REFUSED → over-claim rate driven to 0.
  * THE NEGATIVE (`prose-polish`): the witness is structurally UNWITNESSABLE (no env byte). The `-dos`
    variant has nothing to refuse with, so it falls back to the forgeable claim and over-claims
    exactly as much as the original. DOS does NOT help, and the report says so plainly.

THE COST AXIS: each row reports added tokens / latency of the witness step (corpus.COST). A grounded
skill that costs more is a real trade-off; hiding it would be dishonest.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from benchmark.skill_dos_ablation import corpus as _corpus
from benchmark.skill_dos_ablation.witnesses import FAILED, WITNESS_BY_SKILL

_HERE = Path(__file__).resolve().parent
_LEDGER = _HERE / "RESULTS.md"


def declares_success(traj: dict, variant: str) -> bool:
    """Does this variant DECLARE success on this task?

    ORIGINAL: closes the trust seam on the agent's own `final_text` — a positive-sounding claim is
    taken at face value. (Every fixture's `final_text` is a success claim, so the original always
    declares success — the forgeable seam.)

    `-dos`: closes the seam on the witness rung. The witness REFUSES (returns success=False) only when
    it has env/git evidence the work FAILED. UNWITNESSABLE / WITNESSED_OK both fall through to the
    agent's claim — DOS never INVENTS a failure it cannot witness, and never refuses what it cannot
    ground (the negative skill stays exactly as forgeable as the original).
    """
    claimed = bool(traj.get("final_text"))  # the forgeable self-report
    if variant == "original":
        return claimed
    # variant == "-dos": ground the seam on the witness.
    witness = WITNESS_BY_SKILL[traj["skill"]]
    if witness(traj) == FAILED:
        return False  # REFUSED — env/git evidence contradicts the success claim
    return claimed     # UNWITNESSABLE or WITNESSED_OK -> defer to the (forgeable) claim


@dataclass(frozen=True)
class VariantScore:
    skill: str
    variant: str               # "original" | "-dos"
    n_failed: int              # the rigged-failure denominator (tasks whose truth == "FAILED")
    silent_overclaims: int     # declared success when truth == FAILED
    refused: int               # rigged-failure tasks where the variant REFUSED the success claim
    unwitnessable: int         # rigged-failure tasks the witness could not ground (the negative)
    added_tokens: int          # added cost of the witness step (0 for original)
    added_ms: int

    @property
    def overclaim_rate(self) -> float:
        return self.silent_overclaims / self.n_failed if self.n_failed else 0.0


def _score(skill: str, variant: str, tasks: list[dict]) -> VariantScore:
    failed = [t for t in tasks if t["skill"] == skill and t["truth"] == FAILED]
    over = sum(1 for t in failed if declares_success(t, variant))
    refused = sum(1 for t in failed if not declares_success(t, variant))
    witness = WITNESS_BY_SKILL[skill]
    unwit = sum(1 for t in failed if witness(t) not in (FAILED,) and witness(t) == "UNWITNESSABLE")
    bt, bms, wt, wms = _corpus.COST[skill]
    added_tokens = wt if variant == "-dos" else 0
    added_ms = wms if variant == "-dos" else 0
    return VariantScore(
        skill=skill, variant=variant, n_failed=len(failed),
        silent_overclaims=over, refused=refused, unwitnessable=unwit if variant == "-dos" else 0,
        added_tokens=added_tokens, added_ms=added_ms,
    )


def compute(tasks: Optional[list[dict]] = None) -> dict[str, dict[str, VariantScore]]:
    """Score every skill × {original, -dos}. The SSOT entry point. Returns scores[skill][variant]."""
    tasks = tasks if tasks is not None else _corpus.corpus()
    out: dict[str, dict[str, VariantScore]] = {}
    for skill in _corpus.SKILLS:
        out[skill] = {v: _score(skill, v, tasks) for v in ("original", "-dos")}
    return out


def total_failed(tasks: Optional[list[dict]] = None) -> int:
    tasks = tasks if tasks is not None else _corpus.corpus()
    return sum(1 for t in tasks if t["truth"] == FAILED)


# --------------------------------------------------------------------------- the in-band falsifier
def check_invariants(scores: dict[str, dict[str, VariantScore]]) -> list[str]:
    """Return violated invariants (empty == they held). The honest loud kill, not a silent pass."""
    v: list[str] = []
    # (1) every original variant LEAKS on its rigged failure (the forgeable seam over-claims).
    for skill, byvar in scores.items():
        o = byvar["original"]
        if o.n_failed and o.overclaim_rate < 1.0:
            v.append(f"{skill}: original over-claim {o.overclaim_rate:.0%} < 100% "
                     f"(the forgeable seam should leak on every rigged failure)")
    # (2) every DOS-GROUNDABLE skill's `-dos` variant drives over-claim to 0.
    for skill, byvar in scores.items():
        if skill == _corpus.NEGATIVE_SKILL:
            continue
        d = byvar["-dos"]
        if d.n_failed and d.overclaim_rate != 0.0:
            v.append(f"{skill}: -dos over-claim {d.overclaim_rate:.0%} != 0 "
                     f"(a groundable witness should refuse every rigged failure)")
    # (3) THE NEGATIVE: DOS does NOT help — its `-dos` over-claim equals its original (no improvement).
    neg = scores[_corpus.NEGATIVE_SKILL]
    if neg["-dos"].overclaim_rate != neg["original"].overclaim_rate:
        v.append(f"{_corpus.NEGATIVE_SKILL}: -dos {neg['-dos'].overclaim_rate:.0%} != "
                 f"original {neg['original'].overclaim_rate:.0%} (the negative must NOT improve)")
    if neg["-dos"].unwitnessable != neg["-dos"].n_failed:
        v.append(f"{_corpus.NEGATIVE_SKILL}: not all rigged failures are UNWITNESSABLE "
                 f"({neg['-dos'].unwitnessable}/{neg['-dos'].n_failed}) — the negative claim is unproven")
    # (4) at least 3 DOS-groundable skills exist (the issue's ≥3 requirement).
    groundable = [s for s in scores if s != _corpus.NEGATIVE_SKILL]
    if len(groundable) < 3:
        v.append(f"only {len(groundable)} DOS-groundable skills (need >=3)")
    return v


def _render_ascii(scores: dict[str, dict[str, VariantScore]], n_failed: int, violations: list[str]) -> str:
    out = []
    out.append("=" * 86)
    out.append("  SKILL_DOS_ABLATION — does a dos-skillify-converted skill over-claim less? (issue #176)")
    out.append("  CALIBRATED FIXTURE: synthetic committed trajectories, REAL byte-clean witness logic, $0")
    out.append("=" * 86)
    out.append(f"  RIGGED-FAILURE DENOMINATOR: {n_failed} tasks whose ground truth is \"FAILED\" "
               f"(1 per skill).")
    out.append("  Metric = silent over-claim rate = (declared success | truth=FAILED) / (# FAILED tasks).")
    out.append("  Reported as REFUSED / ADVISORY counts, never a 'caught N' headline (docs/333 floor).")
    out.append("-" * 86)
    out.append(f"  {'skill':<15} {'variant':<9} {'over-claim':>10} {'refused':>8} "
               f"{'+tokens':>8} {'+latency':>9}")
    for skill in _corpus.SKILLS:
        tag = "  (NEGATIVE — DOS does NOT help)" if skill == _corpus.NEGATIVE_SKILL else ""
        for variant in ("original", "-dos"):
            s = scores[skill][variant]
            rate = f"{s.silent_overclaims}/{s.n_failed}={s.overclaim_rate:.0%}"
            refused = f"{s.refused}/{s.n_failed}"
            lat = f"{s.added_ms}ms"
            line = (f"  {skill:<15} {variant:<9} {rate:>10} {refused:>8} "
                    f"{s.added_tokens:>8} {lat:>9}")
            if variant == "-dos":
                line += tag
            out.append(line)
    out.append("-" * 86)
    # the headline deltas, per skill (refused, never "caught")
    out.append("  Per-skill effect of dos-skillify (over-claim original -> -dos):")
    for skill in _corpus.SKILLS:
        o = scores[skill]["original"].overclaim_rate
        d = scores[skill]["-dos"].overclaim_rate
        if skill == _corpus.NEGATIVE_SKILL:
            verdict = f"NO CHANGE — UNWITNESSABLE (pure-prose seam; DOS has no env byte to ground)"
        elif d < o:
            ref = scores[skill]["-dos"].refused
            verdict = f"REFUSED {ref}/{scores[skill]['-dos'].n_failed} rigged failure(s) the original leaked"
        else:
            verdict = "no improvement"
        out.append(f"    {skill:<15} {o:.0%} -> {d:.0%}   {verdict}")
    out.append("-" * 86)
    if violations:
        out.append("  INVARIANT VIOLATED — the harness's own contract did not hold:")
        for x in violations:
            out.append(f"    ! {x}")
    else:
        out.append("  Invariants HELD: every forgeable seam leaks; every GROUNDABLE -dos variant refuses;")
        out.append("  the NEGATIVE (prose-polish) shows DOS not helping — without crashing or hiding it.")
    out.append("  COST is a real trade-off, reported not hidden: the -dos variant pays the +tokens/+latency")
    out.append("  of the witness step. On the negative skill that cost buys nothing (the honest column).")
    out.append("=" * 86)
    return "\n".join(out)


def _emit_results(scores, n_failed, violations) -> str:
    """The committed RESULTS.md markdown table (the SSOT scored summary)."""
    lines = [
        "# skill_dos_ablation — results",
        "",
        "> **Calibrated fixture** (synthetic committed trajectories, real byte-clean witness logic, $0,",
        "> deterministic). The over-claim detection is a deterministic function over fixture bytes, NOT a",
        "> live LLM run. Reproduce: `python -m benchmark.skill_dos_ablation.harness`.",
        "",
        f"**Rigged-failure denominator (explicit): {n_failed} tasks whose ground truth is \"FAILED\" "
        f"(one per skill).**",
        "",
        "Silent over-claim rate = (declared success when truth=FAILED) / (# FAILED tasks). Reported as",
        "REFUSED / advisory counts, never a \"caught N\" headline (the docs/333 honesty floor).",
        "",
        "| skill | variant | silent over-claim | refused | +tokens | +latency |",
        "|---|---|---|---|---|---|",
    ]
    for skill in _corpus.SKILLS:
        for variant in ("original", "-dos"):
            s = scores[skill][variant]
            neg = " (NEGATIVE)" if skill == _corpus.NEGATIVE_SKILL and variant == "-dos" else ""
            lines.append(
                f"| {skill}{neg} | {variant} | {s.silent_overclaims}/{s.n_failed} = "
                f"{s.overclaim_rate:.0%} | {s.refused}/{s.n_failed} | {s.added_tokens} | {s.added_ms}ms |")
    lines += [
        "",
        "## What the table shows",
        "",
        "1. **Every ORIGINAL variant over-claims 100% on its rigged failure.** It closes its trust seam",
        "   on the agent's own `final_text` (forgeable), so it declares success on every rigged task.",
        "2. **Every DOS-GROUNDABLE `-dos` variant drives the over-claim rate to 0%** by refusing the",
        "   success claim from a witness the agent did not author (git ancestry / env terminal /",
        "   tool-stream no-advance / recall staleness). The four rigged shapes the issue names.",
        "3. **THE NEGATIVE (`prose-polish`): DOS does NOT help.** Its only trust seam is the agent's own",
        "   taste; there is no env-authored byte to ground on, so the witness is UNWITNESSABLE on every",
        "   rigged task and the `-dos` over-claim rate equals the original's. Shown, not hidden.",
        "4. **Cost is reported, not hidden.** The `-dos` column carries the added tokens/latency of the",
        "   witness step. On the negative skill that cost buys nothing — the honest trade-off.",
        "",
        "## Honesty contract",
        "",
        "- The corpus (`corpus.py`) is the auditable INPUT: each rigged-failure task and its env-authored",
        "  evidence is visible. The witness logic (`witnesses.py`) reads ONLY env/git-authored bytes,",
        "  never the agent's claim — modeled on the shipped kernel seams (`dos verify`/`commit-audit`,",
        "  `terminal_error`, `tool_stream`, `dos recall`).",
        "- An **in-band falsifier** (`check_invariants`) exits non-zero if any forgeable seam fails to",
        "  leak, any groundable `-dos` variant fails to refuse, or the negative ever 'improves'. A wrong",
        "  harness says so loudly.",
        "- This is the docs/341 / iot_tier instrument aimed at the SKILL layer. The measurement that would",
        "  replace the fixture is a live `dos-skillify` A/B over real skill runs (needs a model/key, out of",
        "  scope for this $0 worker).",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="skill-with/without-DOS over-claim ablation (issue #176)")
    ap.add_argument("--json", action="store_true", help="emit JSON rows instead of the ASCII report")
    ap.add_argument("--check", action="store_true", help="assert invariants; exit 1 on drift")
    ap.add_argument("--emit", action="store_true", help="(re)write RESULTS.md")
    args = ap.parse_args(argv)

    # Render UTF-8 regardless of the console's default codepage (the cp1252 `?`/crash trap on Windows).
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

    tasks = _corpus.corpus()
    scores = compute(tasks)
    n_failed = total_failed(tasks)
    violations = check_invariants(scores)

    if args.check:
        if violations:
            print("INVARIANT DRIFT:", *violations, sep="\n  ", file=sys.stderr)
            return 1
        print("OK: skill_dos_ablation invariants hold.")
        return 0

    if args.emit:
        _LEDGER.write_text(_emit_results(scores, n_failed, violations), encoding="utf-8")
        print(f"wrote {_LEDGER}")
        return 0

    if args.json:
        rows = {
            skill: {
                variant: {
                    "n_failed": s.n_failed,
                    "silent_overclaims": s.silent_overclaims,
                    "overclaim_rate": s.overclaim_rate,
                    "refused": s.refused,
                    "unwitnessable": s.unwitnessable,
                    "added_tokens": s.added_tokens,
                    "added_ms": s.added_ms,
                }
                for variant, s in byvar.items()
            }
            for skill, byvar in scores.items()
        }
        print(json.dumps({
            "rigged_failure_denominator": n_failed,
            "negative_skill": _corpus.NEGATIVE_SKILL,
            "rows": rows,
            "invariant_violations": violations,
            "invariants_held": not violations,
        }, indent=2))
        return 1 if violations else 0

    print(_render_ascii(scores, n_failed, violations))
    # In-band falsifier: a violated contract is a non-zero exit (the honest loud kill).
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
