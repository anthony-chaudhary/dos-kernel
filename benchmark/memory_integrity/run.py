"""The memory-integrity runner (docs/316 §2).

Build the scratch repo (T0) → ADMIT every candidate through both arms →
mutate the repo (T1) → RECALL-sweep each arm's store → fold the metrics →
write `results.json` + `RESULTS.md` beside this file.

Arms:
  admit-all — the industry default: every candidate is stored, untyped, with
              fact presentation. The baseline IS the point, not a strawman.
  dos-gate  — `admit_text` types every candidate; REJECT_POISON never enters
              the store; everything else enters wearing its admission type.

Run (free, no model, no network — git only):
  python -m benchmark.memory_integrity.run [--json] [--no-write] [--keep]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

if __package__ in (None, ""):  # script-form launch: put the repo root on sys.path
    sys.path.insert(0, str(HERE.parent.parent))

from benchmark.memory_integrity import corpus  # noqa: E402

FACT_TIER = "ADMIT_WITNESSED"


def _kernel_version() -> str:
    try:
        import dos
        return str(getattr(dos, "__version__", "0"))
    except Exception:
        return "0"


def _kernel_sha() -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(HERE.parent.parent), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL)
        return proc.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def _rate(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def run_bench(workdir: Path) -> dict:
    """The whole T0→T1 protocol. Returns the metrics dict."""
    from dos.config import default_config
    from dos.drivers.memory_recall import admit_text, sweep

    repo = workdir / "repo"
    facts = corpus.build_t0(repo)
    cands = corpus.candidates(facts)
    store_gate = workdir / "store_gate"
    store_all = workdir / "store_all"
    store_gate.mkdir(parents=True, exist_ok=True)
    store_all.mkdir(parents=True, exist_ok=True)

    # ---- T0: the write moment --------------------------------------------
    cfg = default_config(repo)
    rows = []
    for c in cands:
        v = admit_text(c.text, name=c.id, cfg=cfg)
        got = v.admission.value
        rows.append({
            "id": c.id, "class": c.klass, "truth_at_t0": c.truth_at_t0,
            "expected_admit": c.expected_admit, "got_admit": got,
            "admit_as_expected": got == c.expected_admit,
            "culprit": (v.culprit.claim.raw if v.culprit else None),
        })
        (store_all / f"{c.id}.md").write_text(c.text, encoding="utf-8")
        if got != "REJECT_POISON":
            (store_gate / f"{c.id}.md").write_text(c.text, encoding="utf-8")

    # ---- T1: the world moves; recall both stores --------------------------
    corpus.mutate_to_t1(facts)
    cfg = default_config(repo)
    by_name_gate = {v.evidence.mem_name: v.verdict.value
                    for v in sweep(cfg=cfg, store=str(store_gate))}
    by_name_all = {v.evidence.mem_name: v.verdict.value
                   for v in sweep(cfg=cfg, store=str(store_all))}
    for r, c in zip(rows, cands):
        r["recall_t1_gate"] = by_name_gate.get(c.id)  # None == refused at write
        r["recall_t1_all"] = by_name_all.get(c.id)
        r["expected_recall_t1"] = c.expected_recall_t1
        r["recall_as_expected"] = (
            r["recall_t1_gate"] == c.expected_recall_t1
            if r["recall_t1_gate"] is not None else None)

    # ---- the fold ----------------------------------------------------------
    true_rows = [r for r in rows if r["truth_at_t0"] == "true"]
    false_rows = [r for r in rows if r["truth_at_t0"] == "false"]
    in_grammar = [r for r in false_rows if r["class"] == "poison_in_grammar"]
    out_grammar = [r for r in false_rows if r["class"] != "poison_in_grammar"]
    witnessed = [r for r in rows if r["got_admit"] == FACT_TIER]
    aging = [r for r in rows if r["class"] == "true_then_stale"]
    evergreen = [r for r in rows
                 if r["expected_recall_t1"] == "RECALL_FRESH"]

    metrics = {
        "write_gate": {
            "poison_refusal_in_grammar": _rate(
                sum(r["got_admit"] == "REJECT_POISON" for r in in_grammar), len(in_grammar)),
            "poison_refusal_out_of_grammar": _rate(
                sum(r["got_admit"] == "REJECT_POISON" for r in out_grammar), len(out_grammar)),
            "fact_authority_precision": _rate(
                sum(r["truth_at_t0"] == "true" for r in witnessed), len(witnessed)),
            "fact_authority_leak": sum(r["truth_at_t0"] == "false" for r in witnessed),
            "witnessed_reach_on_true": _rate(
                sum(r["got_admit"] == FACT_TIER for r in true_rows), len(true_rows)),
            "false_refusals_on_true": sum(
                r["got_admit"] == "REJECT_POISON" for r in true_rows),
            "false_contained_without_fact_authority": _rate(
                sum(r["got_admit"] not in ("REJECT_POISON", FACT_TIER) for r in false_rows)
                + sum(r["got_admit"] == "REJECT_POISON" for r in false_rows),
                len(false_rows)),
        },
        "recall_gate": {
            "stale_catch_on_aged": _rate(
                sum(r["recall_t1_gate"] == "RECALL_STALE" for r in aging), len(aging)),
            "fresh_survival": _rate(
                sum(r["recall_t1_gate"] == "RECALL_FRESH" for r in evergreen), len(evergreen)),
        },
        "admit_all_baseline": {
            "poison_admitted": _rate(len(false_rows), len(false_rows)),
            "late_recall_catch_on_false": _rate(
                sum(r["recall_t1_all"] == "RECALL_STALE" for r in false_rows), len(false_rows)),
        },
        "expectation_drift": [
            {k: r[k] for k in ("id", "class", "expected_admit", "got_admit",
                               "expected_recall_t1", "recall_t1_gate")}
            for r in rows
            if not r["admit_as_expected"] or r["recall_as_expected"] is False
        ],
    }
    return {
        "benchmark": "memory_integrity",
        "docs": "docs/316_bad-memory-taxonomy-and-integrity-benchmark-plan.md",
        "kernel_sha": _kernel_sha(),
        "utc_date": time.strftime("%Y-%m-%d", time.gmtime()),
        "n_candidates": len(rows),
        "metrics": metrics,
        "rows": rows,
    }


def render_md(res: dict) -> str:
    m = res["metrics"]
    w, rg, base = m["write_gate"], m["recall_gate"], m["admit_all_baseline"]
    by_class: dict[str, list[dict]] = {}
    for r in res["rows"]:
        by_class.setdefault(r["class"], []).append(r)
    lines = [
        "# memory_integrity — results",
        "",
        f"<!-- dos-bench-stamp: kernel={_kernel_version()} sha={res['kernel_sha']} "
        f"date={res['utc_date']} -->",
        "",
        f"> Generated by `python -m benchmark.memory_integrity.run` — kernel "
        f"`{res['kernel_sha']}`, {res['utc_date']}, n={res['n_candidates']} candidates. "
        f"Labels are env-authored (a constructed git history), design in "
        f"[docs/316](../../docs/316_bad-memory-taxonomy-and-integrity-benchmark-plan.md).",
        "",
        "## Headline",
        "",
        "| Metric | Value | Reading |",
        "|---|---|---|",
        f"| Poison refusal, in-grammar | {w['poison_refusal_in_grammar']:.0%} | "
        f"a false claim the extractor can bind is refused at birth |",
        f"| Poison refusal, out-of-grammar | {w['poison_refusal_out_of_grammar']:.0%} | "
        f"**the published ceiling** — prose-shaped lies are not refused (P2 work-list) |",
        f"| False content stopped from wearing FACT authority | "
        f"{w['false_contained_without_fact_authority']:.0%} | refusal catches the bindable; "
        f"TYPING contains the rest (admitted only as dated claim / opinion) |",
        f"| Fact-tier precision | {w['fact_authority_precision']:.0%} | "
        f"every memory admitted WITNESSED was actually true (leak={w['fact_authority_leak']}) |",
        f"| False refusals on true candidates | {w['false_refusals_on_true']} | "
        f"the cost side of distrust — must stay 0 |",
        f"| Stale catch on aged-true memories | {rg['stale_catch_on_aged']:.0%} | "
        f"admitted-true-then-falsified is caught at recall (the T0→T1 handoff) |",
        f"| Fresh survival at recall | {rg['fresh_survival']:.0%} | "
        f"still-true memories are not harassed |",
        f"| Baseline (admit-all): poison admitted | {base['poison_admitted']:.0%} | "
        f"the industry default — auto-extraction with no admission bar |",
        f"| Baseline: late recall-only catch on false | "
        f"{base['late_recall_catch_on_false']:.0%} | recall alone catches only the bindable, "
        f"and only AFTER sessions may have inherited the lie |",
        "",
        "## Per-class",
        "",
        "| Class | n | Expected admit | Got (histogram) | As expected |",
        "|---|---|---|---|---|",
    ]
    for klass, rs in by_class.items():
        hist: dict[str, int] = {}
        for r in rs:
            hist[r["got_admit"]] = hist.get(r["got_admit"], 0) + 1
        hist_s = ", ".join(f"{k}×{v}" for k, v in sorted(hist.items()))
        ok = sum(r["admit_as_expected"] for r in rs)
        lines.append(f"| {klass} | {len(rs)} | {rs[0]['expected_admit']} | "
                     f"{hist_s} | {ok}/{len(rs)} |")
    lines += [
        "",
        "## Honest notes",
        "",
        "- The corpus is repo-grounded but TEMPLATE-PHRASED; a live-trajectory "
        "corpus is future work (docs/316 §8).",
        "- `poison_evasive` and `poison_contained` rows are EXPECTED misses — "
        "the extraction-grammar ceiling, published on purpose. The two-tier "
        "finding: what the gate cannot refuse it still strips of fact authority.",
        "- `directive_injection` admits as OPINION today — the docs/316 §4 gap; "
        "this row flips when directive typing ships.",
        "- Expectation drift (gate or corpus moved): "
        f"{len(res['metrics']['expectation_drift'])} row(s).",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="print full JSON to stdout")
    ap.add_argument("--no-write", action="store_true",
                    help="do not write RESULTS.md/results.json beside the runner")
    ap.add_argument("--keep", action="store_true", help="keep the scratch workdir")
    ap.add_argument("--workdir", default="", help="scratch dir (default: a temp dir)")
    args = ap.parse_args(argv)

    workdir = Path(args.workdir) if args.workdir else Path(
        tempfile.mkdtemp(prefix="dos-membench-"))
    try:
        res = run_bench(workdir)
    finally:
        if not args.keep and not args.workdir:
            shutil.rmtree(workdir, ignore_errors=True)

    if not args.no_write:
        (HERE / "results.json").write_text(
            json.dumps(res, indent=2) + "\n", encoding="utf-8")
        (HERE / "RESULTS.md").write_text(render_md(res), encoding="utf-8")

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        m = res["metrics"]
        print(f"memory_integrity  n={res['n_candidates']}  kernel={res['kernel_sha']}")
        print(f"  write : in-grammar poison refused {m['write_gate']['poison_refusal_in_grammar']:.0%}"
              f" | fact-tier precision {m['write_gate']['fact_authority_precision']:.0%}"
              f" | false refusals {m['write_gate']['false_refusals_on_true']}")
        print(f"  recall: stale catch {m['recall_gate']['stale_catch_on_aged']:.0%}"
              f" | fresh survival {m['recall_gate']['fresh_survival']:.0%}")
        print(f"  base  : poison admitted {m['admit_all_baseline']['poison_admitted']:.0%}"
              f" | late recall-only catch {m['admit_all_baseline']['late_recall_catch_on_false']:.0%}")
        drift = m["expectation_drift"]
        print(f"  drift : {len(drift)} row(s)" + (f" — {[d['id'] for d in drift]}" if drift else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
