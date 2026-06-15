#!/usr/bin/env python3
"""scoreboard_rollup — fold the PUBLISHED per-repo sweep.json files into one
aggregate roll-up report, reproducible offline by anyone from the repo.

The aggregate report `docs/scoreboard/report-2026-06.md` was generated from an
operator-run corpus sweep (12 repos, network). This script is the OFFLINE,
zero-network twin: it globs the committed per-repo data under
`docs/scoreboard/<org>/<repo>/sweep.json` — the set actually named on the
published site — and folds THOSE files into one living roll-up. Every number in
the generated `docs/scoreboard/rollup.md` is derived here from the JSON; nothing
is hand-typed, and `--check` proves the committed report has not drifted from the
data (the honesty gate).

It COMPOSES the established wording so the roll-up speaks with the same voice as
the index and the per-repo pages:

  * the denominators stay DISTINCT, exactly like `index_aggregate_headline` —
    the agent SHARE is `attributed / commits_scanned`; the backed RATE is
    `witnessed / checkable`. Those two never share a denominator.
  * the load-bearing advisory line is `scoreboard_copy.ETHICS_LINE`, reused
    verbatim — a message-vs-diff mismatch is never a correctness/honesty grade.
  * the AI-built share renders through `scoreboard_copy.format_ai_share` (a
    sub-1% nonzero share floors to "<1%", never "0%").

Dev tooling that operates ON the repo (it reads `docs/scoreboard/`); stdlib +
`scoreboard_copy` only, pure and deterministic. Given the same JSON inputs and
the same `--stamp`, the output is byte-reproducible — there is no clock read in
the committed output beyond the as-of date you pass (default: today's hardcoded
stamp). Nothing under `src/dos/` imports it.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCOREBOARD = REPO / "docs" / "scoreboard"
ROLLUP_PATH = SCOREBOARD / "rollup.md"

# The as-of date baked into the committed report when no --stamp is passed.
# Hardcoded (not a clock read) so the output is byte-reproducible under --check.
DEFAULT_STAMP = "2026-06-15"

# Import the reader-facing copy from the same module the index/per-repo
# renderers use, so the roll-up's wording and denominators match theirs.
import importlib.util

_copy_spec = importlib.util.spec_from_file_location(
    "scoreboard_copy", REPO / "scripts" / "scoreboard_copy.py")
copy = importlib.util.module_from_spec(_copy_spec)
assert _copy_spec and _copy_spec.loader
_copy_spec.loader.exec_module(copy)


# ---------------------------------------------------------------------------
# load + fold — the pure aggregation over the committed per-repo JSON.
# ---------------------------------------------------------------------------

# The two sweep.json shapes the published set carries (both real on disk):
#   * the corpus shape — {repo, commits_scanned, attributed_commits, markers,
#     summary:{checkable, witnessed, …, by_kind, …}} (the 15 named clean repos);
#   * the bare-summary shape — the summary fields at TOP level, no scan/marker
#     wrapper (the self page, anthony-chaudhary/dos-kernel). `summary_of` and the
#     `.get(..., 0)` reads below fold both without special-casing either.


def find_sweeps(root: Path = SCOREBOARD) -> list[Path]:
    """Every published per-repo sweep.json, sorted for a deterministic fold."""
    return sorted(Path(p) for p in glob.glob(str(root / "*" / "*" / "sweep.json")))


def summary_of(doc: dict) -> dict:
    """The summary block — nested under `summary` (corpus shape) or the doc
    itself (bare-summary self page)."""
    return doc.get("summary", doc)


def repo_name_of(doc: dict, path: Path) -> str:
    """`<org>/<repo>` for a per-repo doc. Prefer the explicit `repo` field; fall
    back to the on-disk path (the bare-summary self page carries no `repo`)."""
    name = doc.get("repo")
    if name:
        return str(name)
    # …/docs/scoreboard/<org>/<repo>/sweep.json → "<org>/<repo>"
    return f"{path.parent.parent.name}/{path.parent.name}"


def fold(paths: list[Path]) -> dict:
    """Fold the per-repo files into the aggregate counts the report renders.

    Denominators stay distinct (see module docstring): SHARE rides
    scanned/attributed; the backed RATE rides checkable/witnessed.
    """
    repos = 0
    scanned = attributed = 0
    checkable = witnessed = unwitnessed = abstained = 0
    clean = 0
    markers: dict[str, int] = {}
    by_kind: dict[str, dict[str, int]] = {}
    per_repo_rates: list[tuple[float, str]] = []  # (backed_rate, repo) over checkable>0

    for p in paths:
        doc = json.loads(p.read_text(encoding="utf-8"))
        s = summary_of(doc)
        repos += 1
        scanned += int(doc.get("commits_scanned", 0) or 0)
        attributed += int(doc.get("attributed_commits", 0) or 0)
        c = int(s.get("checkable", 0) or 0)
        w = int(s.get("witnessed", 0) or 0)
        u = int(s.get("unwitnessed", 0) or 0)
        a = int(s.get("abstained", 0) or 0)
        checkable += c
        witnessed += w
        unwitnessed += u
        abstained += a
        if u == 0:
            clean += 1
        for label, count in (doc.get("markers") or {}).items():
            markers[str(label)] = markers.get(str(label), 0) + int(count or 0)
        for kind, kk in (s.get("by_kind") or {}).items():
            bucket = by_kind.setdefault(
                kind, {"witnessed": 0, "unwitnessed": 0, "abstain": 0})
            bucket["witnessed"] += int(kk.get("witnessed", 0) or 0)
            bucket["unwitnessed"] += int(kk.get("unwitnessed", 0) or 0)
            bucket["abstain"] += int(kk.get("abstain", 0) or 0)
        if c > 0:
            per_repo_rates.append((w / c, repo_name_of(doc, p)))

    # Deterministic orderings: markers biggest-first then name; rates ascending.
    mix = sorted(markers.items(), key=lambda kv: (-kv[1], kv[0]))
    per_repo_rates.sort()
    return {
        "repos": repos,
        "scanned": scanned,
        "attributed": attributed,
        "checkable": checkable,
        "witnessed": witnessed,
        "unwitnessed": unwitnessed,
        "abstained": abstained,
        "clean_repos": clean,
        "mix": mix,
        "by_kind": by_kind,
        "rate_spread": per_repo_rates,  # ascending (min first, max last)
    }


# ---------------------------------------------------------------------------
# render — the markdown report, mirroring report-2026-06.md's structure.
# ---------------------------------------------------------------------------

# Canonical claim-kind order for the by-kind line (skips `none` — it abstains).
_KIND_ORDER = ["code_effect", "test", "doc"]
_KIND_PLAIN = {"code_effect": "code-effect", "test": "test", "doc": "doc"}


def _by_kind_line(by_kind: dict[str, dict[str, int]]) -> str:
    """`code-effect W / U · test W / U · doc W / U` — witnessed / unwitnessed
    per kind, in the report's order; trailing kinds present in the data but not
    in the canonical list are appended after."""
    parts: list[str] = []
    seen = set()
    for kind in _KIND_ORDER:
        if kind in by_kind:
            seen.add(kind)
            b = by_kind[kind]
            label = _KIND_PLAIN.get(kind, kind)
            parts.append(f"{label} {b['witnessed']:,} / {b['unwitnessed']:,}")
    for kind in sorted(by_kind):
        if kind in seen or kind == "none":
            continue
        b = by_kind[kind]
        parts.append(f"{kind} {b['witnessed']:,} / {b['unwitnessed']:,}")
    return " · ".join(parts)


def render(agg: dict, *, stamp: str) -> str:
    """Render the roll-up markdown from the folded counts. Pure: same agg +
    stamp ⇒ byte-identical output."""
    repos = agg["repos"]
    scanned = agg["scanned"]
    attributed = agg["attributed"]
    checkable = agg["checkable"]
    witnessed = agg["witnessed"]
    unwitnessed = agg["unwitnessed"]
    abstained = agg["abstained"]
    clean = agg["clean_repos"]
    mix = agg["mix"]
    spread = agg["rate_spread"]

    share = copy.format_ai_share(attributed, scanned)
    backed_rate = (witnessed / checkable) if checkable else 0.0
    unwit_rate = (unwitnessed / checkable) if checkable else 0.0
    repo_noun = "repo" if repos == 1 else "repos"

    # The headline blockquote — the one-line ecosystem fact, denominators folded.
    if unwitnessed == 0:
        headline_tail = (
            "**every one** of the concrete claims those commits made is backed "
            "by the commit's own diff."
        )
    else:
        headline_tail = (
            f"**{backed_rate:.1%}** of the concrete claims those commits made "
            f"are backed by the commit's own diff (the **{unwitnessed}** "
            f"exception{'s' if unwitnessed != 1 else ''}, {unwit_rate:.1%}, "
            "named on their pages)."
        )
    headline = (
        f"> Across the **{repos} published {repo_noun}** on the scoreboard, AI "
        f"agents wrote about **{share}** of recent commits — and {headline_tail}"
    )

    # The agent mix, biggest first.
    mix_cell = " · ".join(f"{label} {count:,}" for label, count in mix) or "—"

    # The per-repo backed-rate spread (min..max), with the zero-count.
    if spread:
        lo_rate, lo_repo = spread[0]
        hi_rate, hi_repo = spread[-1]
        full_clean = clean
        spread_cell = (
            f"{lo_rate:.1%} – {hi_rate:.1%} "
            f"({full_clean} of {repos} {repo_noun}: 100% backed)"
        )
    else:
        spread_cell = "—"

    by_kind_line = _by_kind_line(agg["by_kind"])

    lines = [
        f"# Drift-rate scoreboard — published-set roll-up (as of {stamp})",
        "",
        headline,
        "",
        f"Generated {stamp} by `python scripts/scoreboard_rollup.py` over the "
        f"committed per-repo data under `docs/scoreboard/<org>/<repo>/sweep.json` "
        "— **zero network, reproducible by anyone from the repo**. This is the "
        "living roll-up of the *named, published* set (the leaderboard on the "
        "[index](README.md)); the larger operator-run corpus snapshot is "
        "[report-2026-06.md](report-2026-06.md). Read the "
        "[methodology](methodology.md) first.",
        "",
        "## The numbers",
        "",
        "| | |",
        "|---|---|",
        f"| published repos folded | {repos} (every `<org>/<repo>/sweep.json` "
        "under `docs/scoreboard/`) |",
        f"| default-branch commits scanned | {scanned:,} |",
        f"| machine-attributed agent commits | {attributed:,} |",
        f"| AI-built share (attributed / scanned) | **{share}** |",
        f"| made a concrete, checkable claim (the denominator) | **{checkable:,}** |",
        f"| claim backed by the commit's own diff | {witnessed:,} |",
        f"| claim not backed (unwitnessed) | **{unwitnessed:,}** |",
        f"| abstained (no checkable claim — excluded from the rate) | {abstained:,} |",
        f"| **pooled backed rate** (witnessed / checkable) | **{backed_rate:.1%}** |",
        f"| pooled unwitnessed rate | {unwit_rate:.1%} |",
        f"| per-repo backed-rate spread | {spread_cell} |",
        "",
        f"By claim kind (backed / not backed): {by_kind_line}. The audited "
        f"commits carry attribution markers from {len(mix)} "
        f"toolchain{'s' if len(mix) != 1 else ''}: {mix_cell}.",
        "",
        "## The honest reading",
        "",
        "This is not \"agents lie X% of the time.\" It is: across the named, "
        "published repositories on the scoreboard — visible-attribution, active, "
        "popular projects — agent-authored commit subjects are overwhelmingly "
        "backed by their own diffs. The pooled backed rate is "
        f"**{backed_rate:.1%}**; "
        + (
            "every published repo came back fully clean."
            if unwitnessed == 0 else
            f"the **{unwitnessed}** unwitnessed claim"
            f"{'s' if unwitnessed != 1 else ''} "
            f"({clean} of {repos} {repo_noun} are 100% backed) "
            "are all on this auditor's own page, where each is explained as a "
            "deliberate empty re-stamp — a house convention, not an over-claim "
            "(see the self page and methodology §2)."
        )
        + " Selection caveats — visible attribution only, active/popular "
        "repos, English-grammar claims — are in methodology §4 and §7.",
        "",
        copy.ETHICS_LINE,
        "",
        "## Reproduce",
        "",
        "```bash",
        "python scripts/scoreboard_rollup.py            # regenerate this file",
        "python scripts/scoreboard_rollup.py --check     # assert it matches the data",
        "```",
        "",
        "`--check` regenerates the report in memory and compares it to the "
        "committed `docs/scoreboard/rollup.md`, exiting non-zero on any drift — "
        "so the report can never silently disagree with the JSON it folds. To "
        "score your own repo in one command: "
        "`dos commit-audit --sweep --workspace . BASE..HEAD`.",
        "",
        copy.INDEX_TAGLINE,
        "",
    ]
    return "\n".join(lines)


def build(stamp: str, root: Path = SCOREBOARD) -> str:
    """The end-to-end fold + render — the single source the CLI and tests use."""
    return render(fold(find_sweeps(root)), stamp=stamp)


# ---------------------------------------------------------------------------
# CLI — write or --check.
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stamp", default=DEFAULT_STAMP,
                    help=f"as-of date YYYY-MM-DD baked into the report "
                    f"(default: {DEFAULT_STAMP}). Pure: same inputs + stamp ⇒ "
                    "byte-identical output.")
    ap.add_argument("--check", action="store_true",
                    help="regenerate in memory, compare to the committed "
                    "docs/scoreboard/rollup.md, exit non-zero on drift (the "
                    "honesty gate — the report can't diverge from the data)")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    rendered = build(args.stamp)

    if args.check:
        if not ROLLUP_PATH.exists():
            print(f"DRIFT: {ROLLUP_PATH} does not exist — run without --check "
                  "to generate it", file=sys.stderr)
            return 1
        on_disk = ROLLUP_PATH.read_text(encoding="utf-8")
        if on_disk != rendered:
            print(f"DRIFT: {ROLLUP_PATH} does not match the per-repo sweep.json "
                  "data. Re-run `python scripts/scoreboard_rollup.py` to "
                  "regenerate.", file=sys.stderr)
            return 1
        print(f"OK: {ROLLUP_PATH.name} matches the {len(find_sweeps())} "
              "per-repo sweep.json files.")
        return 0

    # write_bytes with an explicit \n join keeps the file LF on Windows so the
    # committed bytes are stable (the prior workers' CRLF lesson).
    ROLLUP_PATH.write_bytes(rendered.encode("utf-8"))
    print(f"wrote {ROLLUP_PATH} ({len(find_sweeps())} repos folded, "
          f"stamp {args.stamp})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
