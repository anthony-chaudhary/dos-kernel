#!/usr/bin/env python3
"""sota_scan — sweep the field for DOS-relevant work, on a cycle.

DOS lives in a fast-moving space: agent verification, multi-agent trust,
process-reward training, self-improving loops. New papers, repos, and threads
that bear on DOS appear every week. Until now we found them by hand — docs/344's
2026 SOTA audit named arXiv ids one at a time, and that knowledge was gone the
moment the audit ended. Nothing recorded what the field was doing relative to
DOS, or tracked it over time.

This is that scanner. Each cycle it fetches the newest items across a few
sources, throws away the ones it has already seen, writes a dated digest, and
prints a GitHub-issue body listing only the NEW items with a one-line suggested
DOS action each. Run it weekly and you get a tracked trail of the
state-of-the-art around DOS instead of a fresh hunt every time.

The honesty wall (the script/agent split) — read this before you trust a count
================================================================================
A standalone Python script cannot run the `WebSearch` tool; that is a capability
of the agent, not the shell. So the work is split, and the split is honest:

  * THE SCRIPT owns the four sources it can actually fetch by itself — GitHub
    (`gh search repos`), arXiv (the Atom API), Reddit (the `.rss` search feed),
    and Hacker News (the Algolia API). These run unattended under cron. The
    script NEVER claims to have done a web search it cannot do.

  * THE WEB-SEARCH FAMILY is a manual rung. When the agent runs a cycle it does
    its own `WebSearch` queries and feeds the hits back into the SAME ledger and
    digest via `--add-manual --source web ...`. That is the only way a web item
    enters; the script will not invent one.

This is the same LIVE-vs-claimed wall `aeo_tracker.py` draws: a tracked item is
one a fetcher actually pulled, or one the agent explicitly added — never a
promise the script can't keep.

Design — mirrors aeo_tracker.py
===============================
  * Append-only JSONL ledger (`docs/sota/seen.jsonl`); one line = one seen item.
    The ledger is the dedup memory: NEW = an item whose id is not yet in it.
  * Deterministic + offline by construction: the one non-deterministic input
    (the date) is injectable via `--stamp`, and EVERY network fetch is wrapped
    so it degrades to `[]` when the source is unreachable — a dead source skips,
    it never crashes the cycle. Two scans with the same `--stamp` over an
    unchanged ledger are byte-identical.
  * Dev tooling that operates ON the repo: it shells the public `gh`/CLI and
    `urllib`; it imports nothing from `src/dos/`. The package is unaware it
    exists — the same one-way arrow `build_readme.py`, `backlog_triage.py`, and
    `aeo_tracker.py` all follow.

Subcommands
===========
  --scan        (default) fetch every source, drop already-seen items, append
                the NEW ones to the ledger, write `docs/sota/digest-<stamp>.md`,
                and print the issue body. Re-running with no new items writes a
                "0 new" digest and appends nothing.
  --add-manual  feed one item the agent gathered (a WebSearch hit) into the
                ledger + the day's digest: `--add-manual --source web
                --id <url> --title "<...>" [--url <...>] [--topic <...>]`.
  --open-issue  with --scan, also `gh issue create` the body — but ONLY after it
                passes `scripts/leak_scan.py --stdin`. A leak verdict is a
                refusal (exit 1), per CLAUDE.md's issue-leak gate.
  --status      per-source counts over the whole ledger (the default-less view).

Exit code: 0, except a leak-scan refusal on --open-issue (exit 1).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "docs" / "sota" / "seen.jsonl"
LEAK_SCAN = REPO / "scripts" / "leak_scan.py"

LEDGER_SCHEMA = "dos-sota-ledger/v1"

# A neutral UA — some feeds (Reddit) reject the default urllib agent. Names the
# tool, not a person or a host (the tracked-file privacy rule).
_UA = "dos-sota-scan/1 (+https://github.com/; research scan)"

# ---------------------------------------------------------------------------
# Topics — the ONE source of truth for "what is DOS-relevant". Each fetcher maps
# these to its own query syntax. Edit here to widen/narrow the sweep.
# ---------------------------------------------------------------------------
TOPICS = [
    "AI agent verification",
    "multi-agent orchestration trust",
    "process reward model",
    "self-improving agent",
    "agent provenance attestation",
    "LLM agent reliability",
]

# arXiv categories worth sweeping — the agent/SE/ML bands.
_ARXIV_CATS = ["cs.AI", "cs.SE", "cs.LG", "cs.MA"]
# Reddit subs where agent-reliability talk lives.
_REDDIT_SUBS = ["LocalLLaMA", "MachineLearning"]


# ---------------------------------------------------------------------------
# Item — the unit. A plain dict so a ledger row IS an item (no schema drift).
# `id` is the stable dedup key; everything else is for the digest.
# ---------------------------------------------------------------------------
def _item(source: str, id_: str, title: str, url: str,
          score: int = 0, date: str = "", topic: str = "") -> dict:
    return {
        "source": source,
        "id": id_,
        "title": (title or "").strip(),
        "url": url,
        "score": int(score),
        "date": (date or "")[:10],
        "topic": topic,
    }


def _http_json(url: str, timeout: int = 30) -> dict | list | None:
    """GET → parsed JSON, or None on any failure (offline degradation)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (https only, built below)
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _http_text(url: str, timeout: int = 30) -> str | None:
    """GET → text, or None on any failure (offline degradation)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Fetchers — each returns list[Item]; each degrades to [] when its source is
# unreachable. None of them raise. `since` is a YYYY-MM-DD floor (best-effort:
# some sources can't filter by date, so the ledger dedup is the real floor).
# ---------------------------------------------------------------------------
def _fetch_github(topics: list[str], since: str, *, per_topic: int = 5,
                  timeout: int = 60) -> list[dict]:
    """`gh search repos` per topic, star-ranked. [] on any gh failure.

    Repo search (not commit search) because — per the scoreboard lesson — repo
    search ranks by stars and commit search does not.
    """
    out: list[dict] = []
    for topic in topics:
        try:
            r = subprocess.run(
                ["gh", "search", "repos", topic,
                 "--sort", "stars", "--limit", str(per_topic),
                 "--json", "fullName,stargazersCount,pushedAt,description,url"],
                cwd=REPO, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
            rows = json.loads(r.stdout) if r.stdout.strip() else []
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            continue
        for row in rows:
            full = row.get("fullName", "")
            if not full:
                continue
            pushed = (row.get("pushedAt") or "")[:10]
            # best-effort recency filter; ledger dedup is the real gate.
            if since and pushed and pushed < since:
                continue
            out.append(_item(
                "github", f"gh:{full}", row.get("description") or full,
                row.get("url") or f"https://github.com/{full}",
                score=int(row.get("stargazersCount", 0)),
                date=pushed, topic=topic))
    return out


def _fetch_arxiv(topics: list[str], since: str, *, per_query: int = 5,
                 timeout: int = 45) -> list[dict]:
    """arXiv Atom API per topic, newest first. [] on any failure."""
    import xml.etree.ElementTree as ET

    ns = {"a": "http://www.w3.org/2005/Atom"}
    cat_clause = " OR ".join(f"cat:{c}" for c in _ARXIV_CATS)
    out: list[dict] = []
    for topic in topics:
        q = f"({cat_clause}) AND all:{topic}"
        url = ("https://export.arxiv.org/api/query?"
               + urllib.parse.urlencode({
                   "search_query": q, "sortBy": "submittedDate",
                   "sortOrder": "descending", "max_results": per_query}))
        text = _http_text(url, timeout=timeout)
        if not text:
            continue
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            continue
        for entry in root.findall("a:entry", ns):
            aid = (entry.findtext("a:id", "", ns) or "").strip()
            published = (entry.findtext("a:published", "", ns) or "")[:10]
            if not aid:
                continue
            if since and published and published < since:
                continue
            out.append(_item(
                "arxiv", f"arxiv:{aid}",
                (entry.findtext("a:title", "", ns) or "").strip(),
                aid, date=published, topic=topic))
    return out


def _fetch_reddit(topics: list[str], since: str, *, per_query: int = 5,
                  timeout: int = 30) -> list[dict]:
    """Reddit `.rss` search feed per sub, newest first. [] on any failure."""
    import xml.etree.ElementTree as ET

    ns = {"a": "http://www.w3.org/2005/Atom"}
    # one combined query keeps it to one request per sub.
    q = " OR ".join(f'"{t}"' for t in topics)
    out: list[dict] = []
    for sub in _REDDIT_SUBS:
        url = (f"https://www.reddit.com/r/{sub}/search.rss?"
               + urllib.parse.urlencode({
                   "q": q, "sort": "new", "restrict_sr": "1",
                   "limit": per_query}))
        text = _http_text(url, timeout=timeout)
        if not text:
            continue
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            continue
        for entry in root.findall("a:entry", ns):
            link_el = entry.find("a:link", ns)
            link = link_el.get("href") if link_el is not None else ""
            updated = (entry.findtext("a:updated", "", ns) or "")[:10]
            if not link:
                continue
            if since and updated and updated < since:
                continue
            out.append(_item(
                "reddit", f"reddit:{link}",
                (entry.findtext("a:title", "", ns) or "").strip(),
                link, date=updated, topic=f"r/{sub}"))
    return out


def _fetch_hn(topics: list[str], since: str, *, per_query: int = 5,
              timeout: int = 30) -> list[dict]:
    """Hacker News Algolia search per topic, newest first. [] on any failure."""
    out: list[dict] = []
    for topic in topics:
        url = ("https://hn.algolia.com/api/v1/search_by_date?"
               + urllib.parse.urlencode({
                   "query": topic, "tags": "story",
                   "hitsPerPage": per_query}))
        data = _http_json(url, timeout=timeout)
        if not isinstance(data, dict):
            continue
        for hit in data.get("hits", []):
            oid = hit.get("objectID")
            if not oid:
                continue
            date = (hit.get("created_at") or "")[:10]
            if since and date and date < since:
                continue
            out.append(_item(
                "hn", f"hn:{oid}",
                hit.get("title") or hit.get("story_title") or "",
                hit.get("url") or f"https://news.ycombinator.com/item?id={oid}",
                score=int(hit.get("points", 0)), date=date, topic=topic))
    return out


# The fetcher registry — the roster IS the dict (no hand-kept list to rot).
FETCHERS = {
    "github": _fetch_github,
    "arxiv": _fetch_arxiv,
    "reddit": _fetch_reddit,
    "hn": _fetch_hn,
}


def gather(topics: list[str], since: str) -> list[dict]:
    """Every source, folded. A dead source contributes [] — never crashes."""
    out: list[dict] = []
    for fn in FETCHERS.values():
        out.extend(fn(topics, since))
    return out


# ---------------------------------------------------------------------------
# Ledger I/O — append-only JSONL, one item per line.
# ---------------------------------------------------------------------------
def read_ledger(path: Path = LEDGER) -> list[dict]:
    """Every seen item, oldest first. Missing ledger → empty (first run)."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def seen_ids(rows: list[dict]) -> set[str]:
    return {r.get("id", "") for r in rows}


def last_stamp(rows: list[dict]) -> str:
    """The most recent scan date recorded, or "" on an empty ledger."""
    stamps = [r.get("scanned", "") for r in rows if r.get("scanned")]
    return max(stamps) if stamps else ""


def append_items(items: list[dict], *, stamp: str, path: Path = LEDGER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" — tracked file; keep it LF on Windows too so a row written
    # here and one written in CI are byte-comparable (same rule as aeo_tracker).
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for it in items:
            row = dict(it, scanned=stamp)
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def new_items(fetched: list[dict], already: set[str]) -> list[dict]:
    """Items not yet in the ledger, de-duped within the batch too."""
    out, batch = [], set()
    for it in fetched:
        i = it.get("id", "")
        if not i or i in already or i in batch:
            continue
        batch.add(i)
        out.append(it)
    return out


# ---------------------------------------------------------------------------
# Suggested action — a tiny heuristic so a digest row is actionable, not inert.
# It is a HINT for the operator/agent, never an assertion of fact.
# ---------------------------------------------------------------------------
def suggest_action(item: dict) -> str:
    src = item["source"]
    if src == "arxiv":
        return "read; candidate for an answer page / a docs/NN note if it names our thesis"
    if src == "github":
        if item["score"] >= 500:
            return "high-star — candidate scoreboard/integration target"
        return "watch — possible integration or competitor"
    if src in ("reddit", "hn"):
        return "skim the thread; gauge demand / objections to verification tooling"
    if src == "web":
        return "agent-gathered; triage into an issue or an answer page"
    return "triage"


# ---------------------------------------------------------------------------
# Rendering — the digest file + the issue body.
# ---------------------------------------------------------------------------
_SOURCE_ORDER = ["arxiv", "github", "reddit", "hn", "web"]
_SOURCE_LABEL = {
    "arxiv": "Research (arXiv)", "github": "GitHub repos",
    "reddit": "Reddit", "hn": "Hacker News", "web": "Web search (agent-gathered)",
}


def _group(items: list[dict]) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = {}
    for it in items:
        g.setdefault(it["source"], []).append(it)
    return g


# ---------------------------------------------------------------------------
# Importance — the roll-up's ranker. A small, defensible score over data we
# ALREADY have (no extra fetch, fully testable). It decides WHICH items are
# "the most important" so the roll-up can show those and collapse the tail.
#
# It is a relevance HEURISTIC, never a claim of fact — the same honesty rule as
# suggest_action(). The weights say only "surface this one first", not "this is
# true / good".
# ---------------------------------------------------------------------------
# Per-source base weight: what the source itself signals. Research (arXiv) and an
# item the AGENT explicitly surfaced (web) lead — a human/agent already judged it
# relevant. Repos and HN earn their rank mostly from their own score (stars/pts);
# Reddit is discussion context.
_SOURCE_WEIGHT = {"arxiv": 60, "web": 55, "github": 30, "hn": 25, "reddit": 15}


def importance(item: dict, *, today: str = "") -> float:
    """A rank score for one item. Higher = surface sooner. Pure over the item.

    base(source) + a log-damped popularity term (so a 5000★ repo outranks a 50★
    one without a single mega-repo swamping everything) + a small boost for an
    item from the most recent scan date. Deterministic; no clock, no network.
    """
    import math

    score = float(_SOURCE_WEIGHT.get(item.get("source", ""), 10))
    pop = int(item.get("score", 0) or 0)
    if pop > 0:
        score += 12.0 * math.log10(pop + 1)  # 100★→24, 1k★→36, 10k★→48
    # freshness: an item dated on/after the latest scan day gets a nudge.
    if today and (item.get("date") or "") >= today:
        score += 8.0
    return round(score, 3)


def rank_items(items: list[dict], *, today: str = "") -> list[dict]:
    """Items sorted most-important first. Ties break by date then id so the
    order is stable (a re-run yields the same roll-up)."""
    return sorted(
        items,
        key=lambda it: (-importance(it, today=today),
                        it.get("date", ""), it.get("id", "")),
    )


def render_rollup(items: list[dict], *, stamp: str, top: int = 8) -> str:
    """The clean user roll-up: the `top` most important items overall, then the
    rest collapsed to a per-source tail count. The single-key `r` view."""
    L = [f"# DOS SOTA roll-up — {stamp}", ""]
    if not items:
        L.append("Nothing tracked yet. Run `python scripts/sota_scan.py --scan` first.")
        L.append("")
        return "\n".join(L)
    ranked = rank_items(items, today=stamp)
    head, tail = ranked[:top], ranked[top:]
    L.append(f"{len(items)} item(s) tracked — the {len(head)} most important first:")
    L.append("")
    for i, it in enumerate(head, 1):
        src = _SOURCE_LABEL.get(it["source"], it["source"])
        score = f" · {it['score']}★/pts" if it["score"] else ""
        meta = " · ".join(x for x in (it.get("date", ""), it.get("topic", "")) if x)
        title = it["title"] or it["url"]
        L.append(f"{i:>2}. [{src}{score}] {title}")
        L.append(f"    {it['url']}")
        L.append(f"    → {suggest_action(it)}" + (f"   ({meta})" if meta else ""))
        L.append("")
    if tail:
        by_src: dict[str, int] = {}
        for it in tail:
            by_src[it["source"]] = by_src.get(it["source"], 0) + 1
        parts = [f"{by_src[s]} {_SOURCE_LABEL.get(s, s)}"
                 for s in _SOURCE_ORDER if s in by_src]
        L.append(f"…and {len(tail)} more: " + ", ".join(parts) + ".")
        L.append("Full list: `python scripts/sota_scan.py --status` "
                 "or the digest under docs/sota/.")
        L.append("")
    return "\n".join(L)


def render_digest(items: list[dict], *, stamp: str) -> str:
    L = [f"# DOS SOTA scan — {stamp}", ""]
    if not items:
        L.append("No new items this cycle (everything fetched was already seen).")
        L.append("")
        return "\n".join(L)
    L.append(f"{len(items)} new item(s) since the last scan, by source.")
    L.append("")
    g = _group(items)
    for src in _SOURCE_ORDER:
        rows = g.get(src)
        if not rows:
            continue
        L.append(f"## {_SOURCE_LABEL.get(src, src)} ({len(rows)})")
        L.append("")
        for it in rows:
            score = f" ({it['score']}★/pts)" if it["score"] else ""
            title = it["title"] or it["url"]
            L.append(f"- **{title}**{score} — {it['url']}")
            meta = " · ".join(x for x in (it["date"], it["topic"]) if x)
            if meta:
                L.append(f"  - {meta}")
            L.append(f"  - _suggested:_ {suggest_action(it)}")
        L.append("")
    return "\n".join(L)


def render_issue_body(items: list[dict], *, stamp: str, digest_rel: str) -> str:
    L = []
    if not items:
        L.append(f"DOS SOTA scan **{stamp}** — no new items this cycle.")
        L.append("")
        L.append(f"Digest: `{digest_rel}`")
        return "\n".join(L) + "\n"
    L.append(f"DOS SOTA scan **{stamp}** found **{len(items)}** new item(s) "
             "across research / GitHub / Reddit / HN" +
             (" / web" if any(i["source"] == "web" for i in items) else "") + ".")
    L.append("")
    L.append(f"Full digest committed at `{digest_rel}`. New items, with a "
             "suggested DOS action each:")
    L.append("")
    g = _group(items)
    for src in _SOURCE_ORDER:
        rows = g.get(src)
        if not rows:
            continue
        L.append(f"### {_SOURCE_LABEL.get(src, src)}")
        for it in rows:
            score = f" ({it['score']}★/pts)" if it["score"] else ""
            title = it["title"] or it["url"]
            L.append(f"- {title}{score} — {it['url']} — _{suggest_action(it)}_")
        L.append("")
    L.append("---")
    L.append("_Generated by `scripts/sota_scan.py`. Items are leads to triage, "
             "not verified facts; verify before acting._")
    return "\n".join(L) + "\n"


def render_status(rows: list[dict]) -> str:
    L = ["# DOS SOTA scanner — status", ""]
    if not rows:
        L.append("ledger empty — run `python scripts/sota_scan.py --scan`.")
        return "\n".join(L) + "\n"
    by_src: dict[str, int] = {}
    for r in rows:
        by_src[r.get("source", "?")] = by_src.get(r.get("source", "?"), 0) + 1
    scans = sorted({r.get("scanned", "") for r in rows if r.get("scanned")})
    L.append(f"items tracked: {len(rows)}   "
             f"(scans recorded: {len(scans)}"
             + (f", {scans[0]} → {scans[-1]}" if scans else "") + ")")
    L.append("")
    L.append("by source:")
    for src in _SOURCE_ORDER + sorted(set(by_src) - set(_SOURCE_ORDER)):
        if src in by_src:
            L.append(f"  {by_src[src]:>4}   {_SOURCE_LABEL.get(src, src)}")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# The leak gate — issue text is public and skips no scrub (CLAUDE.md). Pipe the
# drafted body through leak_scan.py --stdin; a non-zero exit is a refusal.
# ---------------------------------------------------------------------------
def leak_clean(body: str) -> bool:
    """True iff the body passes the leak scanner. Missing scanner → refuse
    (fail-closed): we never post unscanned public text."""
    if not LEAK_SCAN.exists():
        print(f"REFUSE: leak scanner not found at {LEAK_SCAN} — "
              "refusing to post unscanned text.", file=sys.stderr)
        return False
    try:
        r = subprocess.run(
            [sys.executable, str(LEAK_SCAN), "--stdin"],
            input=body, cwd=REPO, capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        print(f"REFUSE: leak scan failed to run ({e}).", file=sys.stderr)
        return False
    if r.returncode != 0:
        print("REFUSE: leak scan flagged the issue body:", file=sys.stderr)
        print(r.stdout or r.stderr, file=sys.stderr)
        return False
    return True


def open_issue(title: str, body: str, timeout: int = 60) -> bool:
    """gh issue create. Returns True on success. Caller leak-gates the body."""
    try:
        r = subprocess.run(
            ["gh", "issue", "create", "--title", title, "--body", body],
            cwd=REPO, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"gh issue create failed: {e}", file=sys.stderr)
        return False
    if r.returncode != 0:
        print(f"gh issue create failed: {r.stderr}", file=sys.stderr)
        return False
    print(r.stdout.strip())
    return True


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------
def _utc_today() -> str:
    # One home for the clock so tests can stamp it deterministically.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).date().isoformat()


def _digest_path(stamp: str) -> Path:
    return REPO / "docs" / "sota" / f"digest-{stamp}.md"


def _write_digest(items: list[dict], *, stamp: str) -> Path:
    path = _digest_path(stamp)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_digest(items, stamp=stamp), encoding="utf-8", newline="\n")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--scan", action="store_true",
                   help="fetch every source, dedup, write the digest, print the issue body (default)")
    g.add_argument("--status", action="store_true",
                   help="per-source counts over the whole ledger")
    g.add_argument("--rollup", action="store_true",
                   help="the clean roll-up: the most important items, ranked (the single-key `r` view)")
    g.add_argument("--add-manual", action="store_true",
                   help="feed one agent-gathered item (e.g. a WebSearch hit) into the ledger + digest")
    ap.add_argument("--open-issue", action="store_true",
                    help="with --scan, also gh-issue-create the body (leak-gated)")
    ap.add_argument("--top", type=int, default=8,
                    help="(--rollup) how many top items to show before collapsing the tail (default 8)")
    ap.add_argument("--all", dest="roll_all", action="store_true",
                    help="(--rollup) rank the WHOLE ledger, not just the latest scan's items")
    ap.add_argument("--stamp", help="scan date (default: today UTC) — injectable for determinism")
    ap.add_argument("--since", help="recency floor YYYY-MM-DD (default: last recorded scan date)")
    ap.add_argument("--ledger", help="ledger path (default: docs/sota/seen.jsonl)")
    ap.add_argument("--json", action="store_true", help="emit machine JSON instead of the report")
    # --add-manual fields
    ap.add_argument("--source", default="web", help="(--add-manual) item source label")
    ap.add_argument("--id", dest="item_id", help="(--add-manual) stable dedup id (e.g. the url)")
    ap.add_argument("--title", help="(--add-manual) item title")
    ap.add_argument("--url", help="(--add-manual) item url (default: --id)")
    ap.add_argument("--topic", default="", help="(--add-manual) topic label")
    args = ap.parse_args(argv)

    # UTF-8 the streams — the digest carries stars/middots; a cp1252 Windows
    # console must not crash the render (same defensive move as aeo_tracker).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    ledger_path = Path(args.ledger) if args.ledger else LEDGER
    rows = read_ledger(ledger_path)
    stamp = args.stamp or _utc_today()

    if args.add_manual:
        if not (args.item_id and args.title):
            ap.error("--add-manual requires --id and --title")
        item = _item(args.source, args.item_id, args.title,
                     args.url or args.item_id, topic=args.topic)
        fresh = new_items([item], seen_ids(rows))
        if not fresh:
            print(f"already seen: {args.item_id}")
            return 0
        append_items(fresh, stamp=stamp, path=ledger_path)
        # fold into today's digest (rebuild from all items scanned today).
        today = [r for r in read_ledger(ledger_path) if r.get("scanned") == stamp]
        _write_digest(today, stamp=stamp)
        print(f"added: {args.item_id}")
        return 0

    if args.rollup:
        # default: roll up the latest scan's items (freshest, most actionable);
        # --all ranks the whole ledger. The stamp shown is the latest scan date.
        latest = last_stamp(rows)
        scope = rows if args.roll_all else [r for r in rows if r.get("scanned") == latest]
        roll_stamp = stamp if args.stamp else (latest or stamp)
        if args.json:
            ranked = rank_items(scope, today=roll_stamp)
            top = ranked[: args.top]
            print(json.dumps({
                "stamp": roll_stamp, "tracked": len(scope),
                "top": [dict(it, importance=importance(it, today=roll_stamp)) for it in top],
                "tail": len(ranked) - len(top),
            }, indent=2, sort_keys=True))
        else:
            print(render_rollup(scope, stamp=roll_stamp, top=args.top))
        return 0

    if args.status:
        if args.json:
            print(json.dumps({"items": rows}, indent=2, sort_keys=True))
        else:
            print(render_status(rows))
        return 0

    # default: --scan
    since = args.since or last_stamp(rows)
    fetched = gather(TOPICS, since)
    fresh = new_items(fetched, seen_ids(rows))
    append_items(fresh, stamp=stamp, path=ledger_path)
    # the digest for today is every item scanned today (lets --add-manual extend it).
    today = [r for r in read_ledger(ledger_path) if r.get("scanned") == stamp]
    digest = _write_digest(today, stamp=stamp)
    digest_rel = digest.relative_to(REPO).as_posix()
    title = f"SOTA scan {stamp}: {len(fresh)} new item(s)"
    body = render_issue_body(today, stamp=stamp, digest_rel=digest_rel)

    if args.open_issue:
        if not leak_clean(body):
            return 1
        if not open_issue(title, body):
            return 1

    if args.json:
        print(json.dumps({
            "stamp": stamp, "since": since, "new": len(fresh),
            "fetched": len(fetched), "digest": digest_rel,
            "issue_title": title,
        }, indent=2, sort_keys=True))
    else:
        print(f"scan {stamp}: {len(fresh)} new of {len(fetched)} fetched "
              f"(since {since or 'beginning'}) → {digest_rel}")
        print()
        print("--- issue body ---")
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
