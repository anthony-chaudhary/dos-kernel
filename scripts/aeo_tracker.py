#!/usr/bin/env python3
"""aeo_tracker — track the AEO/SEO discovery surface over TIME, not just now.

`discoverability_inventory.py` answers "how many surfaces does an arriving agent
find DOS through, right now?" — a point-in-time count read from the repo's own
ground truth. Its own docstring promises the payoff: *"Run it before and after a
distribution change and the delta is the progress, measured."* But that delta
was ephemeral — two terminal runs, eyeballed, then gone. Nothing recorded it.

This is that recorder. It is the tracking tool over the inventory: it snapshots
the inventory's headline into an APPEND-ONLY ledger (`docs/aeo/aeo_ledger.jsonl`,
one row per snapshot), and reports the delta against the prior row. The progress
on the AEO/SEO goal stops being a manual before/after and becomes a tracked
series an operator (or a CI gate) can read.

It carries the kernel's one rule into the AEO surface: **track the witnessed
effect, never the self-report.** The tracked counts are the LIVE / in-tree
surfaces — a page that exists, a manifest that ships, a registry whose evidence
is a tracked file. A *claim* — "we rank #1", "they merged our PR" — is never a
tracked number; GATED/submitted counts are recorded in their own field and are
explicitly EXCLUDED from the regression gate, so the tracked progress can never
inflate on a promise a third party hasn't honored. That is the same honesty wall
`discoverability_inventory` draws between LIVE and GATED, now drawn across time.

It is dev tooling that operates ON the repo: it composes
`discoverability_inventory` by import (the same way that script's own test loads
it) and imports nothing from `src/dos/` — the one-way arrow `build_readme.py`,
`backlog_triage.py`, and the inventory itself all follow. The package is unaware
it exists.

Deterministic and offline by construction: the only non-determinism (the
timestamp) is injectable via `--stamp`, and the inventory's lone network read
(the host registry) already degrades to an empty list offline. Two snapshots
taken with the same `--stamp` against an unchanged tree are byte-identical.

Subcommands:

  --snapshot   run the inventory, append one row to the ledger, print the delta
               vs the prior row. Idempotent on an unchanged tree + same stamp:
               a row whose witnessed counts match the prior row's is NOT appended
               (the ledger records change, not heartbeat) unless --force.
  --status     show the latest snapshot and the trend (first → latest) per
               tracked surface. The default when no subcommand is given.
  --check      exit 1 if the latest snapshot REGRESSED against the prior one —
               any witnessed/LIVE surface count dropped. A rot pin: a surface
               silently disappearing fails loudly, the same discipline as the
               inventory's --check and the llms.txt link test.

Exit code: 0, except --check returns 1 on a measured regression.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "docs" / "aeo" / "aeo_ledger.jsonl"

# Load the inventory the way its own test does — it is a script, not an importable
# module, so go through importlib rather than `import`. This COMPOSES it (one
# source of truth for the counts); the tracker never re-implements a count.
_INV_PATH = REPO / "scripts" / "discoverability_inventory.py"
_spec = importlib.util.spec_from_file_location("discoverability_inventory", _INV_PATH)
_inv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_inv)

# The headline keys that count a WITNESSED / LIVE surface — the ones whose drop
# is a real regression. Each is an in-tree fact (a present file, a live
# registry, a published page), never a claim. These are the gated keys for
# --check. The honesty wall: `registries_gated_submitted` is deliberately NOT
# here — a filed-but-unmerged submission is a promise, and a promise withdrawn
# is not a regression of a surface we ever controlled.
WITNESSED_KEYS = (
    "arrival_queries_captured",
    "arrival_query_pages",
    "arrival_files_present",
    "answer_pages",
    "hosts_wireable",
    "integration_tiers",
    "framework_recipes",
    "registries_live",
    "scoreboard_pages_published",
)

# Advisory keys — recorded for context, but a drop in one of these is not a
# surface regression (a denominator shrinking, a promise expiring). Tracked so
# the row is complete, never gated.
ADVISORY_KEYS = (
    "arrival_queries_tracked",
    "arrival_files_expected",
    "registries_gated_submitted",
)

LEDGER_SCHEMA = "dos-aeo-ledger/v1"


# ---------------------------------------------------------------------------
# Ledger I/O — append-only JSONL, one snapshot per line.
# ---------------------------------------------------------------------------


def read_ledger(path: Path = LEDGER) -> list[dict]:
    """Every snapshot row, oldest first. Missing ledger → empty (first run)."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _append_row(row: dict, path: Path = LEDGER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" — the ledger is a tracked file; keep it LF on Windows too, so
    # a snapshot taken here and one taken in CI are byte-comparable.
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Snapshot + delta — pure functions over the inventory headline.
# ---------------------------------------------------------------------------


def current_headline() -> dict:
    """The inventory's headline, computed from the live tree. Composes the
    inventory — this tracker holds no count of its own."""
    return _inv.headline(_inv.gather())


def make_row(headline: dict, *, stamp: str) -> dict:
    """A ledger row from a headline + an injected stamp. Pure (no clock)."""
    tracked = {k: int(headline[k]) for k in WITNESSED_KEYS}
    advisory = {k: int(headline[k]) for k in ADVISORY_KEYS if k in headline}
    return {
        "schema": LEDGER_SCHEMA,
        "stamp": stamp,
        "witnessed": tracked,
        "advisory": advisory,
    }


def delta(prev: dict | None, cur: dict) -> dict:
    """Per-witnessed-key change cur - prev. First snapshot (prev=None) reports
    every key as a +N gain from a zero baseline. Pure."""
    out = {}
    prev_w = (prev or {}).get("witnessed", {})
    for k in WITNESSED_KEYS:
        before = int(prev_w.get(k, 0))
        after = int(cur["witnessed"][k])
        out[k] = after - before
    return out


def regressions(prev: dict | None, cur: dict) -> dict:
    """The witnessed keys that DROPPED (negative delta). Empty on the first
    snapshot (nothing to regress against) and on any all-up/flat change."""
    if prev is None:
        return {}
    return {k: v for k, v in delta(prev, cur).items() if v < 0}


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------


def _fmt_delta(n: int) -> str:
    return f"+{n}" if n > 0 else (str(n) if n < 0 else "·")


def render_snapshot(prev: dict | None, cur: dict, *, appended: bool) -> str:
    L = ["# AEO/SEO surface snapshot", ""]
    L.append(f"as of: {cur['stamp']}   (schema {cur['schema']})")
    if not appended:
        L.append("(unchanged since the prior snapshot — not appended; use --force to record anyway)")
    L.append("")
    L.append("witnessed surfaces (LIVE / in-tree — the tracked progress):")
    d = delta(prev, cur)
    for k in WITNESSED_KEYS:
        L.append(f"  {cur['witnessed'][k]:>4}  {_fmt_delta(d[k]):>4}   {k}")
    if cur.get("advisory"):
        L.append("")
        L.append("advisory (recorded, never gated):")
        for k, v in cur["advisory"].items():
            L.append(f"  {v:>4}        {k}")
    regs = regressions(prev, cur)
    L.append("")
    if regs:
        L.append("REGRESSION — a witnessed surface dropped:")
        for k, v in regs.items():
            L.append(f"  {k}: {v}")
    else:
        L.append("no regression (no witnessed surface dropped).")
    L.append("")
    return "\n".join(L)


def render_status(rows: list[dict]) -> str:
    L = ["# AEO/SEO surface tracker — status", ""]
    if not rows:
        L.append("no snapshots yet — run `python scripts/aeo_tracker.py --snapshot`.")
        return "\n".join(L) + "\n"
    first, latest = rows[0], rows[-1]
    L.append(f"snapshots recorded: {len(rows)}   "
             f"(first {first['stamp']} → latest {latest['stamp']})")
    L.append("")
    L.append("witnessed surfaces — latest, and the trend since the first snapshot:")
    fw, lw = first.get("witnessed", {}), latest["witnessed"]
    for k in WITNESSED_KEYS:
        trend = int(lw[k]) - int(fw.get(k, 0))
        L.append(f"  {lw[k]:>4}  {_fmt_delta(trend):>4} since first   {k}")
    if latest.get("advisory"):
        L.append("")
        L.append("advisory (latest):")
        for k, v in latest["advisory"].items():
            L.append(f"  {v:>4}   {k}")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def _utc_today() -> str:
    # Isolated so tests can monkeypatch / so the clock has exactly one home.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).date().isoformat()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--snapshot", action="store_true",
                   help="record one snapshot row + print the delta vs the prior row")
    g.add_argument("--status", action="store_true",
                   help="show the latest snapshot + the trend since the first (default)")
    g.add_argument("--check", action="store_true",
                   help="exit 1 if the latest snapshot regressed a witnessed surface")
    ap.add_argument("--stamp", help="snapshot date (default: today UTC) — injectable for determinism")
    ap.add_argument("--force", action="store_true",
                    help="with --snapshot, append even when nothing changed")
    ap.add_argument("--json", action="store_true", help="emit machine JSON instead of the report")
    ap.add_argument("--ledger", help="ledger path (default: docs/aeo/aeo_ledger.jsonl)")
    args = ap.parse_args(argv)

    # UTF-8 the streams — the report carries bullets/middots; a cp1252 Windows
    # console must not crash the render (same defensive move as the inventory).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    ledger_path = Path(args.ledger) if args.ledger else LEDGER
    rows = read_ledger(ledger_path)

    if args.snapshot:
        stamp = args.stamp or _utc_today()
        cur = make_row(current_headline(), stamp=stamp)
        prev = rows[-1] if rows else None
        # Idempotent: don't append a heartbeat. A row is recorded only when a
        # witnessed count actually changed (or --force). The ledger logs change.
        changed = prev is None or any(delta(prev, cur).values())
        appended = changed or args.force
        if appended:
            _append_row(cur, ledger_path)
        if args.json:
            print(json.dumps({
                "appended": appended,
                "row": cur,
                "delta": delta(prev, cur),
                "regressions": regressions(prev, cur),
            }, indent=2, sort_keys=True))
        else:
            print(render_snapshot(prev, cur, appended=appended))
        return 0

    if args.check:
        if len(rows) < 2:
            # Nothing to regress against — a single (or empty) ledger passes.
            if args.json:
                print(json.dumps({"regressions": {}, "rows": len(rows)}, indent=2))
            else:
                print(f"--check: {len(rows)} snapshot(s) — nothing to compare, pass.")
            return 0
        prev, cur = rows[-2], rows[-1]
        regs = regressions(prev, cur)
        if args.json:
            print(json.dumps({"regressions": regs, "rows": len(rows)}, indent=2, sort_keys=True))
        else:
            if regs:
                print("FAIL: witnessed AEO surface regressed since the prior snapshot:",
                      file=sys.stderr)
                for k, v in regs.items():
                    print(f"  {k}: {v}", file=sys.stderr)
            else:
                print("--check: no witnessed surface regressed, pass.")
        return 1 if regs else 0

    # default: --status
    if args.json:
        print(json.dumps({"snapshots": rows}, indent=2, sort_keys=True))
    else:
        print(render_status(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
