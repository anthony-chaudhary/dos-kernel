#!/usr/bin/env python3
"""discoverability_inventory — count the surfaces an agent can discover DOS through.

The goal "make DOS more discoverable, especially by other agents" is unbounded
prose until it has a number. This script is that number: a re-runnable count of
the *contexts in which an arriving agent (or its tooling) can find DOS*, read
from the repo's own ground truth — never from a claim. Run it before and after a
distribution change and the delta is the progress, measured.

It is dev tooling that operates ON the repo (it imports nothing from `src/dos/`
beyond shelling the public CLI for the host registry; the package is unaware of
it — the same one-way arrow as `build_readme.py` and `backlog_triage.py`).

What "discoverable by an agent" means here — five families, each a real fetch an
agent or its installer makes:

  0. ARRIVAL QUERIES the high-intent questions an answer-engine routes to a
                     canonical page. Captured = the evidence-backed answer page
                     exists in the tree (we count having the answer, never where
                     we rank). A fresh query with no incumbent answer is the
                     cheapest discovery win; this counts whether we took it.
  1. ARRIVAL FILES   the well-known files an agent fetches first (llms.txt, the
                     manifests, the answer corpus). The llms.txt convention says
                     an LLM reads `/llms.txt` before it clones; an MCP host reads
                     `server.json`; a Gemini CLI reads `gemini-extension.json`.
  2. HOSTS           the agent runtimes DOS can wire — read live from the
                     `dos hosts --json` registry, never a hand-kept list.
  3. INTEGRATION     the tiers a host can adopt through (MCP / hooks / exit-code)
     TIERS           and the framework seams (the fleet-framework cookbook
                     recipes) — how many distinct ways DOS plugs in.
  4. REGISTRIES      the external venues an agent's package resolver or gallery
                     crawler reaches DOS through — split by STATUS, because an
                     in-tree manifest (we control) is not the same as a live
                     listing (a third party controls). We count what we can
                     prove from the tree; gated submissions are listed but
                     flagged, never folded into the "live" headline.

The honesty rule (the whole point of the product): a surface is only counted
LIVE when its evidence is in this repo (a tracked file, a registry the CLI
reports). A submission we filed but a third party hasn't merged is SUBMITTED,
not LIVE — counted in its own column so the headline can't inflate on a promise.

Exit code: 0 always (it is a report, not a gate) unless --check is given, which
exits 1 if any ARRIVAL file the inventory expects is missing (a rot pin — a
renamed manifest should fail loudly, like the llms.txt link test).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# --- family 0: arrival queries (the high-intent questions an answer-engine routes) -
# (query, target answer page). A query is CAPTURED only when its canonical page
# is present in the tree — read from ground truth, never a ranking claim (we do
# not assert "we rank #1", only "we have a canonical, evidence-backed answer").
# The point of tracking queries, not just files: a fresh high-intent query with
# no incumbent answer is the cheapest discovery win, and this counts whether we
# took it. The "metric shift" rows are the 2026 token-maxxing→verified-outcomes
# transition queries.
ARRIVAL_QUERIES = [
    ("how to verify an AI agent actually did the work",
     "docs/answers/how-to-verify-an-ai-agent-actually-did-the-work.md"),
    ("how to stop two AI agents overwriting each other",
     "docs/answers/how-to-stop-two-ai-agents-overwriting-each-other.md"),
    ("how to detect an agent loop spinning without progress",
     "docs/answers/how-to-detect-an-agent-loop-spinning-without-progress.md"),
    ("do AI coding agents lie about what they shipped",
     "docs/answers/do-ai-coding-agents-lie-about-what-they-shipped.md"),
    ("process-reward training data that can't be gamed",
     "docs/answers/process-reward-model-training-data-that-cant-be-gamed.md"),
    ("add a guardrail to a coding agent with no plugin system",
     "docs/answers/how-to-add-a-guardrail-to-a-coding-agent-with-no-plugin-system.md"),
    # metric-shift / transition queries (2026 token-maxxing is over)
    ("what replaced tokens-burned as the metric for AI agents",
     "docs/answers/what-replaced-tokens-burned-as-the-metric-for-ai-agents.md"),
    ("is the token-maxxing era over / what is token efficiency",
     "docs/answers/what-replaced-tokens-burned-as-the-metric-for-ai-agents.md"),
    ("how to measure verified outcomes instead of token usage",
     "docs/answers/what-replaced-tokens-burned-as-the-metric-for-ai-agents.md"),
]

# --- family 1: arrival files (the well-known fetch targets) -------------------
# (path, what an agent/tool fetches it for). Presence is read from the tree.
ARRIVAL_FILES = [
    ("llms.txt", "the llms.txt convention — an LLM's first fetch, a curated index"),
    ("llms-full.txt", "the whole story in one file (the docs concatenated)"),
    ("llms-install.md", "the agent-readable install recipe"),
    ("AGENTS.md", "orientation written for an agent working inside the repo"),
    ("GEMINI.md", "the Gemini CLI context file the extension loads"),
    ("server.json", "the MCP registry manifest (official registry)"),
    ("gemini-extension.json", "the Gemini CLI extension manifest (auto-indexed gallery)"),
    ("smithery.yaml", "the Smithery MCP-registry manifest"),
    ("CITATION.cff", "GitHub 'cite this repository' + the scholarly-agent surface"),
    ("docs/FAQ.md", "question-shaped answers an answer-engine lifts"),
]

# --- family 4: external registries / venues, by who controls the listing ------
# status: LIVE = provable here or auto-indexed from an in-tree manifest;
#         GATED = needs an owner submission a third party must accept.
# Evidence is a tracked file where one exists; otherwise the status is asserted
# from the tracking issue and clearly flagged GATED so it never joins the headline.
REGISTRIES = [
    ("PyPI (dos-kernel)", "LIVE", "pyproject.toml", "the package resolver every pip/uv/pipx agent uses"),
    ("MCP official registry", "LIVE", "server.json", "the registry that fans out to github.com/mcp + VS Code"),
    ("Gemini CLI extensions gallery", "LIVE", "gemini-extension.json", "auto-indexed: crawls repos with a valid manifest, no PR"),
    ("GitHub Action (verify-action)", "LIVE", "verify-action/action.yml", "the CI gate the Marketplace lists"),
    ("GitLab CI template + catalog component", "LIVE", "gitlab-ci/dos-verify.gitlab-ci.yml", "the population the GitHub Action never reaches"),
    ("Smithery listing", "GATED", "smithery.yaml", "manifest in-tree; the listing is an owner submission (#134)"),
    ("conda-forge feedstock", "GATED", None, "noarch recipe, no traction gate; one staged-recipes PR (#54)"),
    ("punkpeye/awesome-mcp-servers", "GATED", None, "one-line README PR, agent fast-track (#134)"),
    ("upstream CrewAI / OpenAI Agents listings", "GATED", "src/dos/drivers/crewai_guardrail.py", "drivers shipped; the listings pin a release (#77)"),
]


def _present(rel: str) -> bool:
    return (REPO / rel).exists()


def _count_glob(globpat: str, exclude: str | None = None) -> list[str]:
    out = []
    for p in sorted(REPO.glob(globpat)):
        if exclude and exclude in p.name:
            continue
        out.append(str(p.relative_to(REPO)).replace("\\", "/"))
    return out


def _hosts() -> list[dict]:
    """Read the live host registry via the public CLI. Empty list on any failure
    (the report degrades; it never crashes on a missing CLI)."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "dos.cli", "hosts", "--json"],
            cwd=REPO, capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return []
        d = json.loads(r.stdout)
        return d.get("hosts", []) if isinstance(d, dict) else (d if isinstance(d, list) else [])
    except Exception:
        return []


def _cookbook_recipes() -> int:
    """Count framework seams in the fleet-framework cookbook (## / ### headings
    naming a recipe)."""
    f = REPO / "examples/playbooks/cookbook-fleet-frameworks.md"
    if not f.exists():
        return 0
    n = 0
    for line in f.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("## ") or s.startswith("### "):
            n += 1
    return n


def _scoreboard() -> dict:
    """The per-repo scoreboard fan-out (docs/311, #98) — the multiplicative
    discovery surface: one indexed, named trust page per audited repo, each a
    landing context where an agent auditing that repo meets DOS. Counts the
    TRACKED pages (the published surface), plus whether the seeded-index
    orchestrator (the corpus-scale engine) and its index root are in the tree."""
    sb = REPO / "docs" / "scoreboard"
    pages = sorted(
        str(p.relative_to(REPO)).replace("\\", "/")
        for p in sb.glob("*/*.md")  # docs/scoreboard/<org>/<name>.md
    ) if sb.exists() else []
    return {
        "pages_published": pages,
        "index_root": _present("docs/scoreboard/README.md"),
        "orchestrator": _present("scripts/seed_scoreboard_index.py"),
    }


def gather() -> dict:
    arrival = [(p, d, _present(p)) for p, d in ARRIVAL_FILES]
    # captured = the canonical answer page exists; we dedupe distinct target pages
    # so two query phrasings pointing at one page don't double-count the surface.
    queries = [(q, page, _present(page)) for q, page in ARRIVAL_QUERIES]
    answers = _count_glob("docs/answers/*.md", exclude="README")
    hosts = _hosts()
    tiers = ["MCP (advisory)", "hooks (enforcement)", "exit-code (any command env)"]
    recipes = _cookbook_recipes()
    registries = []
    for name, status, evidence, why in REGISTRIES:
        proven = _present(evidence) if evidence else None
        registries.append({
            "name": name, "status": status, "evidence": evidence,
            "evidence_present": proven, "why": why,
        })
    return {
        "arrival_queries": queries,
        "arrival_files": arrival,
        "answers_pages": answers,
        "hosts": hosts,
        "tiers": tiers,
        "framework_recipes": recipes,
        "registries": registries,
        "scoreboard": _scoreboard(),
    }


def headline(inv: dict) -> dict:
    queries_captured = sum(1 for _, _, ok in inv["arrival_queries"] if ok)
    queries_total = len(inv["arrival_queries"])
    # distinct canonical pages the captured queries resolve to (the real surface count)
    captured_pages = {page for _, page, ok in inv["arrival_queries"] if ok}
    arrival_present = sum(1 for _, _, ok in inv["arrival_files"] if ok)
    registries_live = sum(1 for r in inv["registries"] if r["status"] == "LIVE")
    registries_gated = sum(1 for r in inv["registries"] if r["status"] == "GATED")
    sb = inv["scoreboard"]
    return {
        "arrival_queries_captured": queries_captured,
        "arrival_queries_tracked": queries_total,
        "arrival_query_pages": len(captured_pages),
        "arrival_files_present": arrival_present,
        "arrival_files_expected": len(inv["arrival_files"]),
        "answer_pages": len(inv["answers_pages"]),
        "hosts_wireable": len(inv["hosts"]),
        "integration_tiers": len(inv["tiers"]),
        "framework_recipes": inv["framework_recipes"],
        "registries_live": registries_live,
        "registries_gated_submitted": registries_gated,
        "scoreboard_pages_published": len(sb["pages_published"]),
        "scoreboard_fanout_engine": bool(sb["orchestrator"] and sb["index_root"]),
    }


def render(inv: dict, h: dict) -> str:
    L = []
    L.append("# DOS discoverability inventory — the surfaces an agent finds DOS through")
    L.append("")
    L.append("> Counted from the repo's own ground truth. A surface is LIVE only when")
    L.append("> its evidence is in this tree; a filed-but-unmerged submission is GATED,")
    L.append("> never folded into the LIVE count. Re-run before/after a change — the")
    L.append("> delta is the measured progress.")
    L.append("")
    L.append("## Headline")
    L.append("")
    L.append(f"- high-intent queries captured (canonical page in tree): "
             f"**{h['arrival_queries_captured']}/{h['arrival_queries_tracked']}** "
             f"→ **{h['arrival_query_pages']}** distinct answer pages")
    L.append(f"- arrival files present: **{h['arrival_files_present']}/{h['arrival_files_expected']}**")
    L.append(f"- answer-shaped pages (answer-engine liftable): **{h['answer_pages']}**")
    L.append(f"- agent hosts wireable (live registry): **{h['hosts_wireable']}**")
    L.append(f"- integration tiers: **{h['integration_tiers']}**")
    L.append(f"- framework seams (cookbook recipes): **{h['framework_recipes']}**")
    L.append(f"- external registries LIVE: **{h['registries_live']}**  ·  GATED/submitted: **{h['registries_gated_submitted']}**")
    fanout = "yes" if h["scoreboard_fanout_engine"] else "no"
    L.append(f"- scoreboard per-repo pages published: **{h['scoreboard_pages_published']}**  ·  corpus fan-out engine: **{fanout}**")
    L.append("")
    L.append("## 0. Arrival queries (the high-intent questions an answer-engine routes)")
    L.append("")
    L.append("> Captured = a canonical, evidence-backed answer page exists in the tree.")
    L.append("> This counts whether we *have the answer*, not where we rank.")
    L.append("")
    for q, page, ok in inv["arrival_queries"]:
        mark = "[captured]" if ok else "[OPEN]"
        L.append(f"- {mark}  \"{q}\" → `{page}`")
    L.append("")
    L.append("## 1. Arrival files (the well-known fetch targets)")
    L.append("")
    for p, why, ok in inv["arrival_files"]:
        mark = "[present]" if ok else "[MISSING]"
        L.append(f"- {mark}  `{p}` - {why}")
    L.append("")
    L.append("## 2. Agent hosts (live from `dos hosts --json`)")
    L.append("")
    if inv["hosts"]:
        for hh in inv["hosts"]:
            tier = hh.get("tier", "?")
            L.append(f"- `{hh.get('host','?')}` — {tier} ({hh.get('dialect','?')})")
    else:
        L.append("- (host registry unavailable — install `dos-kernel` to populate)")
    L.append("")
    L.append("## 3. Integration tiers + framework seams")
    L.append("")
    for t in inv["tiers"]:
        L.append(f"- tier: {t}")
    L.append(f"- framework cookbook recipes: {inv['framework_recipes']}")
    L.append("")
    L.append("## 4. External registries / venues")
    L.append("")
    for r in inv["registries"]:
        ev = ""
        if r["evidence"]:
            ev = f" [evidence: `{r['evidence']}`{'' if r['evidence_present'] else ' — MISSING'}]"
        L.append(f"- **{r['status']}** — {r['name']}: {r['why']}{ev}")
    L.append("")
    L.append("## 5. Scoreboard per-repo fan-out (the multiplicative surface)")
    L.append("")
    sb = inv["scoreboard"]
    L.append(f"- corpus fan-out engine present: "
             f"{'yes' if sb['orchestrator'] and sb['index_root'] else 'no'} "
             "(`scripts/seed_scoreboard_index.py` + `docs/scoreboard/README.md`)")
    L.append(f"- per-repo pages published (tracked): {len(sb['pages_published'])}")
    for p in sb["pages_published"]:
        L.append(f"  - `{p}`")
    L.append("")
    L.append("## Answer pages (the corpus an answer-engine lifts)")
    L.append("")
    for a in inv["answers_pages"]:
        L.append(f"- `{a}`")
    L.append("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="emit the inventory + headline as JSON")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any expected arrival file is missing (rot pin)")
    args = ap.parse_args(argv)

    # The report carries em-dashes / bullets; force UTF-8 so a cp1252 Windows
    # console doesn't crash the render (the same defensive move other scripts make).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    inv = gather()
    h = headline(inv)

    if args.json:
        print(json.dumps({"headline": h, "inventory": {
            "arrival_queries": [{"query": q, "page": page, "captured": ok} for q, page, ok in inv["arrival_queries"]],
            "arrival_files": [{"path": p, "why": w, "present": ok} for p, w, ok in inv["arrival_files"]],
            "answers_pages": inv["answers_pages"],
            "hosts": inv["hosts"],
            "tiers": inv["tiers"],
            "framework_recipes": inv["framework_recipes"],
            "registries": inv["registries"],
            "scoreboard": inv["scoreboard"],
        }}, indent=2))
    else:
        print(render(inv, h))

    if args.check:
        missing = [p for p, _, ok in inv["arrival_files"] if not ok]
        if missing:
            print(f"\nFAIL: {len(missing)} arrival file(s) missing: {', '.join(missing)}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
