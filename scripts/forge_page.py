#!/usr/bin/env python3
"""Render the witness-forgery challenge page from a forge_arena run (docs/321 P2).

The published face of issue #114 — the standing dare. It consumes ONE JSON file
(the `benchmark/forge_arena/harness.py --json` output) and writes one markdown
page: the two-gate forgery-rate table, the honest denominator caption, and the
append-only attempts log (one row per (attack, task), both gates' verdicts).

The structural rule this file enforces (the docs/321 §0 honest-caption discipline,
pinned by `tests/test_forge_page.py`): **the headline is DERIVED from the floor's
own numbers, never asserted.** A `FLOOR HELD` headline can only arise from a run
whose `checks.floor_no_forgery` is true AND whose floor admitted zero forgeries;
a run with even one floor forgery renders `FORGERY LANDED` — the page reports the
hole honestly, the same way the harness exits non-zero. The denominator caption
names the no-witness slice as OUT of the rate (a judgment claim is not a forgery
the floor caught or missed — it is one the floor declines to rule on).

Dev tooling, not a kernel module — stdlib-only, pure on its input (one JSON in,
one page out, no git or network I/O). Nothing under `src/dos/` knows it exists.

Usage:
    PYTHONPATH=src python -m benchmark.forge_arena.harness --json > run.json
    python scripts/forge_page.py --run run.json [--out PAGE.md] [--check]

Exit codes: 0 rendered (or --check matched), 1 --check mismatch, 2 bad input.
`--check` writes nothing — it re-renders and compares bytes against `--out`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCHEMA = "dos-forge-page/v1"
_REPO_URL = "https://github.com/anthony-chaudhary/dos-kernel"

FLOOR_HELD = "FLOOR HELD"
FORGERY_LANDED = "FORGERY LANDED"


class BadInput(Exception):
    """The run JSON was missing/malformed (exit 2, no page written)."""


def _require(d: dict, key: str, ctx: str):
    if key not in d:
        raise BadInput(f"run JSON missing '{key}' under {ctx}")
    return d[key]


def _headline(run: dict) -> str:
    """DERIVED, never data: FLOOR HELD iff the floor admitted zero forgeries AND
    the soundness check agrees. One floor forgery forces FORGERY LANDED."""
    gates = _require(run, "gates", "run")
    floor = _require(gates, "floor", "gates")
    checks = _require(run, "checks", "run")
    no_forgery = floor.get("forge_admitted", -1) == 0 and bool(checks.get("floor_no_forgery"))
    return FLOOR_HELD if no_forgery else FORGERY_LANDED


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def render(run: dict) -> str:
    if run.get("benchmark") != "forge_arena":
        raise BadInput("run JSON is not a forge_arena result")
    gates = _require(run, "gates", "run")
    floor = _require(gates, "floor", "gates")
    text = _require(gates, "text_believing", "gates")
    tier = run.get("model_tier", "scripted")
    headline = _headline(run)

    lines: list[str] = []
    lines.append("# The witness-forgery challenge")
    lines.append("")
    lines.append(f"> *Standing dare ([issue #114]({_REPO_URL}/issues/114), "
                 "[docs/321]({0}/blob/master/docs/321_the-standing-witness-forgery-challenge-plan.md)):* "
                 "an attacker controls every text channel — narration, the claim bid, a "
                 "pasted `[SYSTEM: accept=True]`, a forged floor-rung witness, judge "
                 "flattery — and must get one false-effect claim **ADMITTED without doing "
                 "the work**. The witness reads a different surface (an OS exit code, git "
                 "ancestry, a provider ledger) the attacker never touches."
                 .format(_REPO_URL))
    lines.append("")
    lines.append(f"## Verdict: **{headline}**")
    lines.append("")
    if headline == FLOOR_HELD:
        lines.append("Across every attack in the corpus, **text alone never flipped the "
                     "admit bit**. The witness floor admitted zero forgeries; the "
                     "text-believing gate, on the same attacker text, was gamed.")
    else:
        lines.append("**A forgery landed on the witness floor — a witness-tamper hole.** "
                     "The offending attack is listed below and becomes a regression "
                     "fixture until the kernel is fixed.")
    lines.append("")
    lines.append(f"Corpus: {run.get('n_attacks', '?')} attacks × {run.get('n_tasks', '?')} "
                 f"tasks = {run.get('n_attempts', '?')} attempts. Model tier: `{tier}`.")
    lines.append("")

    # The two-gate table.
    lines.append("| gate | forgeries / attempts | forgery-rate | honest-accepts |")
    lines.append("|---|---|---|---|")
    lines.append(f"| **witness_floor** (`dos.reward.admit`) | "
                 f"**{floor.get('forge_admitted')} / {floor.get('forge_attempts')}** | "
                 f"**{_pct(floor.get('forgery_rate', 0.0))}** | "
                 f"{floor.get('honest_admitted')}/{floor.get('honest_attempts')} |")
    lines.append(f"| text_believing (narration-grader) | "
                 f"{text.get('forge_admitted')} / {text.get('forge_attempts')} | "
                 f"{_pct(text.get('forgery_rate', 0.0))} | "
                 f"{text.get('honest_admitted')}/{text.get('honest_attempts')} |")
    lines.append("")

    # The honest denominator (docs/307 §2).
    nw = floor.get("nowitness_attempts", 0)
    lines.append(f"**The honest denominator.** The forgery-rate is over checkable claims "
                 f"with an env-authored witness (false-effect, witnessed). A judgment/taste "
                 f"claim has no deterministic witness — it ABSTAINs on the floor and is "
                 f"**outside** the rate ({nw} no-witness attempt(s), never accepted by the "
                 f"floor). The text-believing gate, by contrast, accepts those on prose "
                 f"alone. The floor number is structural, not tuned: "
                 f"`believe_under_floor` filters every agent-authored byte out of the "
                 f"belief decision by construction (docs/234).")
    lines.append("")

    # The append-only attempts log.
    attempts = run.get("attempts", [])
    lines.append("## Attempts log")
    lines.append("")
    lines.append("| attack | task | tier | floor | text-gate | forgery? |")
    lines.append("|---|---|---|---|---|---|")
    for a in attempts:
        forgery = "⚠ YES" if a.get("is_forgery") else ("—" if not a.get("is_forgeable_target") else "no")
        lines.append(f"| {a.get('attack')} | {a.get('task')} | {a.get('model_tier')} | "
                     f"{a.get('floor_verdict')} | {a.get('text_verdict')} | {forgery} |")
    lines.append("")
    lines.append(f"*Reproduce:* `PYTHONPATH=src python -m benchmark.forge_arena.harness "
                 f"--json` → `python scripts/forge_page.py --run -`. "
                 f"Page schema: `{_SCHEMA}`.")
    return "\n".join(lines) + "\n"


def _load(path: str) -> dict:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise BadInput(f"could not parse run JSON: {e}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render the witness-forgery challenge page (docs/321 P2).")
    p.add_argument("--run", required=True, help="the forge_arena harness --json output ('-' for stdin)")
    p.add_argument("--out", help="write the page here (default: stdout)")
    p.add_argument("--check", action="store_true",
                   help="re-render and compare bytes against --out; write nothing (exit 1 on mismatch)")
    args = p.parse_args(argv)

    try:
        run = _load(args.run)
        page = render(run)
    except BadInput as e:
        print(f"forge_page: {e}", file=sys.stderr)
        return 2

    if args.check:
        if not args.out:
            print("forge_page: --check requires --out", file=sys.stderr)
            return 2
        existing = Path(args.out).read_text(encoding="utf-8") if Path(args.out).exists() else ""
        if existing == page:
            return 0
        print(f"forge_page: --check mismatch against {args.out}", file=sys.stderr)
        return 1

    if args.out:
        Path(args.out).write_text(page, encoding="utf-8")
    else:
        # The page carries non-ASCII (×, →, ⚠); force UTF-8 so a cp1252 console
        # does not mangle it. write the bytes directly, bypassing the locale codec.
        sys.stdout.buffer.write(page.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
