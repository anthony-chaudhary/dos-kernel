#!/usr/bin/env python3
"""scoreboard_index — rebuild the scoreboard FRONT DOOR from the committed data.

The front door (`docs/scoreboard/README.md`) is the most-read page of the whole
scoreboard, and until now it had no standing rebuild path: it was whatever the
last `seed_scoreboard_index.py` corpus run happened to stage and an operator
happened to copy in. Two problems with that. First, it could silently drift
from the per-repo `sweep.json` data underneath it — there was no `--check` the
way `scoreboard_rollup.py` has one. Second, it was a flat wall of "100% clean"
links: every number the audit collects (how much of each repo AI built, which
agent did it, what kind of work it was) was flattened into one green word.

This tool fixes both, and is the visual half of the observatory reframe
(docs/356). It folds the SAME committed per-repo `sweep.json` files
`scoreboard_rollup.py` folds — one source of truth, reused, never a second copy
of the fold — and from them it:

  * generates three self-contained SVG charts (``scoreboard_charts``) under
    ``docs/scoreboard/assets/`` — the AI-built share per repo, the agent mix,
    and the claim-kind composition — embedded via Markdown image syntax so they
    render on BOTH GitHub and the Pages site;
  * renders ``docs/scoreboard/README.md`` with the observatory framing
    (``scoreboard_copy``) — the charts first, then the cross-set insight, then
    the per-repo comparison table, with the claim-vs-diff audit as the trust
    column;
  * keeps the ethics floor intact — the self page is named (it grades itself
    first), foreign pages are the already-published opt-in/seeded set, the
    withheld count stays a NUMBER (docs/311 §2).

It is **pure + reproducible**: same committed inputs + same ``--stamp`` ⇒
byte-identical README and SVGs. ``--check`` regenerates everything in memory and
compares it to the tracked files, exiting non-zero on any drift — so the front
door can never silently disagree with the data it summarizes, exactly the
honesty gate the roll-up runs on its numbers.

Dev tooling that operates ON the repo: stdlib + the three sibling scoreboard
modules, loaded by path (``scripts/`` is not a package). Nothing under
``src/dos/`` imports it — the one-way arrow the other generators follow.

Usage:
    python scripts/scoreboard_index.py            # rebuild README + the SVGs
    python scripts/scoreboard_index.py --check     # assert they match the data
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCOREBOARD = REPO / "docs" / "scoreboard"
README_PATH = SCOREBOARD / "README.md"
ASSETS_DIR = SCOREBOARD / "assets"
DEFAULT_STAMP = "2026-06-16"

# This repo's own page — the one always-named self entry (docs/311 P1).
SELF_REPO = "anthony-chaudhary/dos-kernel"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


copy = _load("scoreboard_copy")
charts = _load("scoreboard_charts")
rollup = _load("scoreboard_rollup")  # reuse its fold + find_sweeps (one source)


# ---------------------------------------------------------------------------
# fold the committed per-repo data into leaderboard rows. The aggregate fold is
# `rollup.fold`; here we also need a per-repo ROW (for the table + the charts),
# which the rollup does not emit. Built from the same files, path-keyed for the
# bare-summary self page (it carries no `repo` field).
# ---------------------------------------------------------------------------


def leaderboard_rows(paths: list[Path]) -> list[dict]:
    """One row per committed sweep, sorted by AI-built share (desc), then name.

    Each row: {full, scanned, attributed, mix:[(label,count)], checkable,
    witnessed}. A bare-summary page (no scan/attributed/markers — the self page)
    gets scanned=attributed=0 and an empty mix, so it sorts last and is dropped
    from the share/mix charts (no honest share to draw) but still appears in the
    comparison table with its backed rate.
    """
    import json

    rows: list[dict] = []
    for p in paths:
        doc = json.loads(p.read_text(encoding="utf-8"))
        full = rollup.repo_name_of(doc, p)
        summary = doc.get("summary", doc)
        markers = doc.get("markers") or {}
        mix = sorted(((str(k), int(v)) for k, v in markers.items()),
                     key=lambda kv: (-kv[1], kv[0]))
        rows.append({
            "full": full,
            "scanned": int(doc.get("commits_scanned", 0) or 0),
            "attributed": int(doc.get("attributed_commits", 0) or 0),
            "mix": mix,
            "checkable": int(summary.get("checkable", 0) or 0),
            "witnessed": int(summary.get("witnessed", 0) or 0),
        })
    rows.sort(key=_row_sort_key)
    return rows


def _row_sort_key(r: dict):
    scanned = int(r.get("scanned", 0) or 0)
    share = (int(r.get("attributed", 0) or 0) / scanned) if scanned else -1.0
    return (-share, r["full"])


# ---------------------------------------------------------------------------
# the cross-set insight inputs — which agent dominates, how lopsided the work.
# ---------------------------------------------------------------------------

# A small map from a repo's org to the agent its OWN vendor ships, so the
# "vendor dogfoods its own tool" call is data-checked, not asserted: a repo is
# only called dogfooding if its top agent IS its vendor's agent. Conservative —
# an org not in this map is never claimed to be dogfooding.
_VENDOR_AGENT = {
    "openai": "codex",
    "openinterpreter": "codex",      # Open Interpreter ships on the codex line
    "charmbracelet": "crush",
    "microsoft": "copilot",          # GitHub Copilot is Microsoft's
    "github": "copilot",
}


def who_builds_facts(rows: list[dict]) -> dict:
    """Derive the 'who builds whose repo' inputs from the leaderboard rows.

    Pure over `rows`. Returns {top_agent, top_count, total, dogfood, rivals}:
      * top_agent — the agent that is the single biggest committer in the most
        repos (ties broken by name for determinism);
      * top_count / total — in how many of the `total` repos-with-a-mix it leads;
      * dogfood — [(repo, agent)] where the repo's top agent is its own vendor's;
      * rivals — [(repo, repo_top_agent, top_agent_count)] for repos NOT led by
        top_agent but where top_agent still appears, sorted by that count desc.
    """
    mixed = [(r["full"], dict(r["mix"])) for r in rows if r.get("mix")]
    total = len(mixed)
    repo_top = {}
    for full, m in mixed:
        repo_top[full] = sorted(m.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    lead_counts: dict[str, int] = {}
    for agent in repo_top.values():
        lead_counts[agent] = lead_counts.get(agent, 0) + 1
    top_agent = (sorted(lead_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                 if lead_counts else None)
    top_count = lead_counts.get(top_agent, 0)
    dogfood = []
    for full, _m in mixed:
        org = full.split("/", 1)[0].lower()
        vendor_agent = _VENDOR_AGENT.get(org)
        if vendor_agent and repo_top[full] == vendor_agent:
            dogfood.append((full, vendor_agent))
    dogfood.sort()
    rivals = []
    for full, m in mixed:
        if repo_top[full] != top_agent and m.get(top_agent, 0) > 0:
            rivals.append((full, repo_top[full], int(m[top_agent])))
    rivals.sort(key=lambda t: (-t[2], t[0]))
    return {"top_agent": top_agent, "top_count": top_count, "total": total,
            "dogfood": dogfood, "rivals": rivals}


def insight_facts(agg: dict) -> dict:
    """Distil the rollup aggregate into the observatory insight inputs. Pure."""
    mix = agg.get("mix") or []  # [(label, count)] biggest first
    total_attr = sum(c for _, c in mix)
    top_agent = mix[0][0] if mix else None
    top_share = (mix[0][1] / total_attr) if (mix and total_attr) else 0.0
    by_kind = agg.get("by_kind") or {}
    checkable_kinds = {k: v.get("witnessed", 0) for k, v in by_kind.items()
                       if k != "none"}
    kind_total = sum(checkable_kinds.values())
    code = checkable_kinds.get("code_effect", 0)
    code_pct = (code / kind_total) if kind_total else 0.0
    return {
        "top_agent": top_agent,
        "top_agent_share": top_share,
        "n_agents": len(mix),
        "code_pct": code_pct,
        "repos": agg.get("repos", 0),
    }


# ---------------------------------------------------------------------------
# the SVG assets — generated from the rows + aggregate, written to assets/.
# ---------------------------------------------------------------------------

ASSET_STAT_BAND = "assets/stat-band.svg"
ASSET_AI_SHARE = "assets/ai-share.svg"
ASSET_AGENT_MIX = "assets/agent-mix.svg"
ASSET_CLAIM_KINDS = "assets/claim-kinds.svg"


def _hero_stats(agg: dict) -> list[tuple[str, str]]:
    """The headline figures for the stat band — all from the fold, formatted.
    Pure. Distinct denominators kept distinct (share over scanned; backed over
    checkable)."""
    repos = agg.get("repos", 0)
    scanned = agg.get("scanned", 0)
    attributed = agg.get("attributed", 0)
    checkable = agg.get("checkable", 0)
    witnessed = agg.get("witnessed", 0)
    stats = [(f"{repos:,}", "popular repos")]
    if scanned:
        stats.append((f"{scanned:,}", "commits read"))
        stats.append((copy.format_ai_share(attributed, scanned), "AI-built"))
    if checkable:
        rate = witnessed / checkable
        stats.append((f"{rate * 100:.1f}%", "claims backed by the diff"))
    return stats


def build_assets(rows: list[dict], agg: dict) -> dict[str, str]:
    """The SVG strings, keyed by their repo-relative asset path. Pure."""
    by_kind_witnessed = {k: v.get("witnessed", 0)
                         for k, v in (agg.get("by_kind") or {}).items()}
    return {
        ASSET_STAT_BAND: charts.stat_band_chart(_hero_stats(agg)),
        ASSET_AI_SHARE: charts.ai_share_chart(rows),
        ASSET_AGENT_MIX: charts.agent_mix_chart(rows),
        ASSET_CLAIM_KINDS: charts.claim_kind_chart(
            by_kind_witnessed,
            backed=agg.get("witnessed", 0),
            checkable=agg.get("checkable", 0)),
    }


# ---------------------------------------------------------------------------
# render the README — the observatory front door. Pure (rows/agg/stamp in,
# markdown out). The order: hook → charts → insight → score-your-repo → self
# page → comparison table → fine print → tagline.
# ---------------------------------------------------------------------------


def render_readme(rows: list[dict], agg: dict, *, stamp: str,
                  assets: dict[str, str]) -> str:
    L = [copy.observatory_hook(), ""]

    # The hero stat band — the headline numbers, right under the hook, so a
    # skimmer leaves with the payload before scrolling into the charts.
    if assets.get(ASSET_STAT_BAND):
        L += [f"![The board at a glance]({ASSET_STAT_BAND})", ""]

    # The charts, only the ones that have data (an empty chart returns "" and is
    # skipped so the front door never shows an empty frame).
    chart_blocks = []
    if assets.get(ASSET_AI_SHARE):
        chart_blocks.append(f"![AI-built share of each repo]({ASSET_AI_SHARE})")
    if assets.get(ASSET_AGENT_MIX):
        chart_blocks.append(f"![Which agent built which repo]({ASSET_AGENT_MIX})")
    if assets.get(ASSET_CLAIM_KINDS):
        chart_blocks.append(
            f"![What kind of work AI commits claimed]({ASSET_CLAIM_KINDS})")
    if chart_blocks:
        L += [copy.observatory_charts_intro(), ""]
        for block in chart_blocks:
            L += [block, ""]

    facts = insight_facts(agg)
    insight = copy.observatory_insight(**facts)
    if insight:
        L += [insight, ""]

    # The standout cross-repo story — the 'who builds whose repo' payload.
    wb = who_builds_facts(rows)
    who = copy.observatory_who_builds_whose_repo(**wb)
    if who:
        L += [who, ""]

    L += [copy.observatory_score_your_repo(), ""]

    # The self page — the auditor grades itself first, the one always-named entry.
    if any(r["full"] == SELF_REPO for r in rows):
        L += [copy.index_self_section(SELF_REPO), ""]

    # The comparison table — the per-repo detail, reusing the tested leaderboard
    # renderer so the table shape stays one source.
    L += [copy.observatory_leaderboard_intro(), ""]
    table = copy.index_leaderboard(rows)
    if table:
        # `index_leaderboard` prepends its own intro sentence; drop that line so
        # the heading above isn't said twice. The table starts at the first
        # pipe-row.
        lines = table.splitlines()
        first_pipe = next((i for i, ln in enumerate(lines)
                           if ln.startswith("|")), 0)
        L += ["\n".join(lines[first_pipe:]), ""]

    # The fine print — the ethics line, the deeper links. The withheld count is
    # NOT knowable from the committed per-repo data alone (it lived in the
    # corpus run's manifest, not in tree), so pass None: the "Another N repos…"
    # sentence is omitted rather than asserting a false zero.
    L += [copy.index_fine_print(audited=len(rows), withheld=None), ""]
    L += [copy.INDEX_TAGLINE, ""]
    return "\n".join(L)


# ---------------------------------------------------------------------------
# build / check — the I/O boundary.
# ---------------------------------------------------------------------------


def build(stamp: str) -> tuple[str, dict[str, str]]:
    """Fold the committed data and return (readme_markdown, {asset_path: svg}).
    Pure over the committed files + stamp — no clock read here."""
    paths = rollup.find_sweeps(SCOREBOARD)
    agg = rollup.fold(paths)
    rows = leaderboard_rows(paths)
    assets = build_assets(rows, agg)
    readme = render_readme(rows, agg, stamp=stamp, assets=assets)
    return readme, assets


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="regenerate in memory and assert the committed README "
                         "+ SVG assets match the data; non-zero on any drift")
    ap.add_argument("--stamp", default=DEFAULT_STAMP,
                    help=f"as-of date baked into the README (default {DEFAULT_STAMP}; "
                         "hardcoded, not a clock read, so output is reproducible)")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    readme, assets = build(args.stamp)

    if args.check:
        drift = []
        if not README_PATH.exists() or \
                README_PATH.read_text(encoding="utf-8") != readme:
            drift.append(str(README_PATH.relative_to(REPO)))
        for rel, svg in assets.items():
            ap_path = SCOREBOARD / rel
            if not svg:
                continue  # an empty chart writes nothing
            if not ap_path.exists() or \
                    ap_path.read_text(encoding="utf-8") != svg:
                drift.append(str(ap_path.relative_to(REPO)))
        if drift:
            print("DRIFT: the front door does not match the per-repo data:",
                  file=sys.stderr)
            for d in drift:
                print(f"  {d}", file=sys.stderr)
            print("Run `python scripts/scoreboard_index.py` to rebuild.",
                  file=sys.stderr)
            return 1
        print(f"OK: README.md + {sum(1 for v in assets.values() if v)} SVG "
              "assets match the per-repo sweep data.")
        return 0

    _write(README_PATH, readme)
    for rel, svg in assets.items():
        if svg:
            _write(SCOREBOARD / rel, svg)
    n = sum(1 for v in assets.values() if v)
    print(f"wrote {README_PATH.relative_to(REPO)} + {n} SVG asset(s) "
          f"under {ASSETS_DIR.relative_to(REPO)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
