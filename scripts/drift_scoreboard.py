"""drift_scoreboard — the public drift-rate scoreboard sweep (docs/307, issue #66).

Run `dos commit-audit`'s claim-vs-diff witness over a CORPUS of repositories
with machine-attributable agent commits, and fold the verdicts into one
publishable aggregate: the **drift rate** — how often a concrete claim in an
agent-authored commit subject is unwitnessed by the commit's own diff.

This is DOS dev tooling, not a kernel module: it `import dos` and lives under
`scripts/`, the same one-way arrow as `trajectory_audit.py`. The per-commit
verdict is the unchanged `dos.commit_audit.audit_commit`; the per-repo fold is
the unchanged `sweep_summary`. This tool adds only corpus plumbing:

  * **corpus in** — a text file, one repo per line (clone URL or local path);
  * **attribution gate** — a CLOSED marker set deciding "did an agent
    toolchain author this commit?" from committer/author identity and
    commit trailers. Under-matching on purpose: an uncertain marker stays
    OUT (a missed agent commit shrinks the sample; a human commit wrongly
    counted would poison the headline claim). A bare human name like
    "Claude" or "Claudette" never matches — identity rules require the
    bot-account form or the toolchain's own email/trailer bytes.
  * **per-repo verdicts out** — full JSON per repo (markers, rates, offending
    SHAs) under `<out>/per-repo/`. Operator-only: NOT for publication.
  * **aggregate out** — `aggregate.json` + `aggregate.md`, identity-stripped
    BY CONSTRUCTION: the fold never copies repo names, URLs, or commit SHAs
    (a SHA is globally searchable, so it would deanonymize), and the render
    step only ever sees the stripped fold. v1 of the public report names no
    repository without opt-in; the tool makes that structural.

Advisory framing (the Wall-3 line, verbatim from the witness's contract):
drift is a claim-vs-diff mismatch, never a correctness or malice grade.
`CLAIM_UNWITNESSED` fires only where a concrete code/test claim and a
contradicting diff coexist; everything else ABSTAINs and is excluded from
the denominator.

Usage:
    python scripts/drift_scoreboard.py --corpus corpus.txt --out out/
    python scripts/drift_scoreboard.py --enumerate   # print corpus candidates
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# The one-way arrow: this tool imports dos; nothing under src/dos/ imports it.
from dos.commit_audit import audit_commit, sweep_summary

_GIT_TIMEOUT_S = 180
_CLONE_TIMEOUT_S = 1800

# ---------------------------------------------------------------------------
# The attribution marker set — CLOSED, mechanical, published.
# ---------------------------------------------------------------------------
# A `<slug>[bot]@users.noreply.github.com` email (with or without the numeric
# id prefix GitHub adds) names a GitHub App installation — no human commits
# with a `[bot]` address — so slug→label is structural, not a substring guess.
_BOT_SLUGS = {
    "devin-ai-integration": "devin",
    "copilot-swe-agent": "copilot",
    "copilot": "copilot",
    "claude": "claude",
    "sweep-ai": "sweep",
    "google-labs-jules": "jules",
    "openhands": "openhands",
    "openhands-agent": "openhands",
    "cursoragent": "cursor",
    "openclaw": "openclaw",
    "clawsweeper": "openclaw",           # the OpenClaw repo's own agent fleet
    "roomote": "roo",                    # Roo Code's cloud agent
    "opencode-agent": "opencode",
}
# Exact toolchain emails (the agent's own address, not a human's). Each byte
# form was verified against real commit history before entering the set.
_EMAIL_EXACT = {
    "noreply@anthropic.com": "claude",   # Claude Code's Co-Authored-By identity
    "openhands@all-hands.dev": "openhands",
    "qwen-coder@alibabacloud.com": "qwen",
    "crush@charm.land": "crush",
    "roomote@roocode.com": "roo",
    "cursoragent@cursor.com": "cursor",
}
_EMAIL_DOMAINS = {
    "aider.chat": "aider",               # aider's attribution identity
    "aider.dev": "aider",
}
# Codex's Co-Authored-By identity is a generic-looking address, so the rule is
# CONJUNCTIVE (name AND email) — neither half alone matches.
_CODEX_NAME, _CODEX_EMAIL = "codex", "noreply@openai.com"
# `NNNNN+Copilot@users.noreply.github.com` — the Copilot coding agent's
# author identity on commits it pushes.
_EMAIL_SUFFIXES = {
    "+copilot@users.noreply.github.com": "copilot",
}
# Name forms only an agent toolchain writes. aider's default attribution
# appends " (aider)" to the configured author name; "Cursor Agent" is the
# Cursor background-agent author. A bare "claude"/"copilot" NAME is not here:
# humans carry those names, and the conservative direction is to stay out.
_NAME_AIDER_RE = re.compile(r"\(aider\)|^aider\s*\(|^aider$")
_NAME_EXACT = {
    "cursor agent": "cursor",
    "devin ai": "devin",
}
# Generator footers an agent toolchain writes into the commit BODY.
_BODY_FOOTERS = {
    "generated with [claude code]": "claude",
}
_BOT_EMAIL_RE = re.compile(
    r"(?:^|\+)([a-z0-9][a-z0-9._-]*)\[bot\]@users\.noreply\.github\.com$")
_BOT_NAME_RE = re.compile(r"^([a-z0-9][a-z0-9._-]*)\[bot\]$")
_TRAILER_RE = re.compile(r"^\s*co-authored-by:\s*(.*?)\s*<([^>]*)>\s*$",
                         re.IGNORECASE)


def match_identity(name: str, email: str) -> str | None:
    """Match ONE (name, email) identity against the closed marker set. Pure.

    Returns the marker label, or None. Negative space is deliberate: a human
    named "Claude"/"Claudette" with a personal email does not match — only
    the bot-account form (`...[bot]@users.noreply.github.com`), the
    toolchain's own email, or the explicit `(aider)` name suffix do.
    """
    name_l = (name or "").strip().lower()
    email_l = (email or "").strip().lower()
    m = _BOT_EMAIL_RE.search(email_l)
    if m and m.group(1) in _BOT_SLUGS:
        return _BOT_SLUGS[m.group(1)]
    m = _BOT_NAME_RE.match(name_l)
    if m and m.group(1) in _BOT_SLUGS:
        return _BOT_SLUGS[m.group(1)]
    if email_l in _EMAIL_EXACT:
        return _EMAIL_EXACT[email_l]
    domain = email_l.rsplit("@", 1)[-1] if "@" in email_l else ""
    if domain in _EMAIL_DOMAINS:
        return _EMAIL_DOMAINS[domain]
    for suffix, label in _EMAIL_SUFFIXES.items():
        if email_l.endswith(suffix):
            return label
    if name_l == _CODEX_NAME and email_l == _CODEX_EMAIL:
        return "codex"
    if _NAME_AIDER_RE.search(name_l):
        return "aider"
    if name_l in _NAME_EXACT:
        return _NAME_EXACT[name_l]
    return None


def classify_attribution(author_name: str, author_email: str,
                         committer_name: str, committer_email: str,
                         body: str) -> str | None:
    """Is this commit machine-attributable to an agent toolchain? Pure.

    Checks author, committer, every `Co-authored-by:` trailer, then the
    generator footers. Returns the FIRST marker label that matches, else None.
    """
    hit = match_identity(author_name, author_email)
    if hit:
        return hit
    hit = match_identity(committer_name, committer_email)
    if hit:
        return hit
    for line in (body or "").splitlines():
        m = _TRAILER_RE.match(line)
        if m:
            hit = match_identity(m.group(1), m.group(2))
            if hit:
                return hit
    body_l = (body or "").lower()
    for footer, label in _BODY_FOOTERS.items():
        if footer in body_l:
            return label
    return None


# ---------------------------------------------------------------------------
# Corpus parsing + the aggregate fold + the render. All pure.
# ---------------------------------------------------------------------------


def parse_corpus(text: str) -> list[str]:
    """One repo per line (clone URL or local path); `#` comments; blanks skipped."""
    entries: list[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            entries.append(line)
    return entries


def fold_aggregate(per_repo: list[dict]) -> dict:
    """Fold per-repo sweeps into the publishable aggregate. Pure.

    IDENTITY-STRIPPED BY CONSTRUCTION: the result carries no repo name, no
    URL, and no commit SHA (`unwitnessed_shas` is deliberately not copied —
    a SHA is globally searchable, so it would name the repo). The render
    step below only ever sees this fold, so the publishable artifacts cannot
    leak what the fold never kept. Pinned by test.
    """
    totals = {"commits_scanned": 0, "attributed_commits": 0,
              "audited_commits": 0, "checkable": 0, "abstained": 0,
              "witnessed": 0, "unwitnessed": 0}
    by_kind: dict[str, dict[str, int]] = {}
    by_marker: dict[str, int] = {}
    rates: list[float] = []
    for repo in per_repo:
        s = repo["summary"]
        totals["commits_scanned"] += repo.get("commits_scanned", 0)
        totals["attributed_commits"] += repo.get("attributed_commits", 0)
        totals["audited_commits"] += s.get("commits", 0)
        for key in ("checkable", "abstained", "witnessed", "unwitnessed"):
            totals[key] += s.get(key, 0)
        if s.get("checkable", 0) > 0:
            rates.append(s["unwitnessed"] / s["checkable"])
        for kind, row in s.get("by_kind", {}).items():
            agg_row = by_kind.setdefault(
                kind, {"unwitnessed": 0, "witnessed": 0, "abstain": 0})
            for cell in agg_row:
                agg_row[cell] += row.get(cell, 0)
        for marker, n in repo.get("markers", {}).items():
            by_marker[marker] = by_marker.get(marker, 0) + n
    checkable = totals["checkable"]
    return {
        "report": "drift-rate scoreboard aggregate (v1: aggregate-only)",
        "repos": len(per_repo),
        "repos_with_checkable_claims": len(rates),
        **totals,
        "drift_rate_pooled": (totals["unwitnessed"] / checkable) if checkable else 0.0,
        "drift_rate_median_per_repo": statistics.median(rates) if rates else 0.0,
        "per_repo_drift_rates": sorted(round(r, 4) for r in rates),
        "by_kind": by_kind,
        "by_marker": by_marker,
    }


_METHODOLOGY_URL = ("https://anthony-chaudhary.github.io/dos-kernel/"
                    "drift-scoreboard.html")


def render_markdown(agg: dict, generated_on: str) -> str:
    """The publishable aggregate report. Pure; sees only the stripped fold."""
    pct = f"{agg['drift_rate_pooled'] * 100:.1f}%"
    med = f"{agg['drift_rate_median_per_repo'] * 100:.1f}%"
    rates = agg["per_repo_drift_rates"]
    spread = (f"{rates[0] * 100:.1f}%–{rates[-1] * 100:.1f}%" if rates else "n/a")
    lines = [
        "# Drift-rate scoreboard — aggregate report",
        "",
        f"Generated {generated_on} · methodology: {_METHODOLOGY_URL}",
        "",
        f"Across **{agg['repos']} public repositories** with "
        "machine-attributable agent commits:",
        "",
        f"- agent-attributed commits audited: **{agg['audited_commits']}**",
        f"- made a concrete, checkable claim (the denominator): "
        f"**{agg['checkable']}**",
        f"- claim witnessed by the commit's own diff: **{agg['witnessed']}**",
        f"- claim unwitnessed by the commit's own diff: "
        f"**{agg['unwitnessed']}**",
        f"- abstained (no checkable claim — excluded from the rate): "
        f"{agg['abstained']}",
        "",
        f"**Pooled drift rate: {pct}** (median per-repo {med}, "
        f"per-repo spread {spread}).",
        "",
        "## By claim kind",
        "",
        "| claim kind | witnessed | unwitnessed | abstain |",
        "|---|---|---|---|",
    ]
    for kind in sorted(agg["by_kind"]):
        row = agg["by_kind"][kind]
        lines.append(f"| {kind} | {row['witnessed']} | {row['unwitnessed']} "
                     f"| {row['abstain']} |")
    lines += [
        "",
        "## By attribution marker (commits audited)",
        "",
        "| toolchain marker | commits |",
        "|---|---|",
    ]
    for marker in sorted(agg["by_marker"]):
        lines.append(f"| {marker} | {agg['by_marker'][marker]} |")
    lines += [
        "",
        "## What this is — and is not",
        "",
        "Drift is a **claim-vs-diff mismatch**, never a correctness or malice "
        "grade. A commit counts as unwitnessed only where its subject makes a "
        "concrete code/test claim and its own diff contradicts the claim's "
        "kind; everything else abstains and is excluded from the rate. This "
        "v1 report is aggregate-only: no repository is named without opt-in. "
        "Methodology, false-positive characterization, and the reproduction "
        f"recipe: {_METHODOLOGY_URL}",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Boundary I/O — clone, log, audit. NOT pure; every failure skips, never crashes.
# ---------------------------------------------------------------------------


def _git(root: Path | str, *args: str,
         timeout: int = _GIT_TIMEOUT_S) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, text=True, check=False,
                           encoding="utf-8", errors="replace",
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return r.returncode, r.stdout


def _slug(entry: str) -> str:
    tail = entry.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    tail = tail[:-4] if tail.endswith(".git") else tail
    return re.sub(r"[^A-Za-z0-9._-]", "_", tail) or "repo"


def ensure_repo(entry: str, cache: Path) -> Path | None:
    """A local path is used in place; a URL is cloned `--no-checkout` into the
    cache (an existing cached clone is reused as-is — delete it to refresh)."""
    p = Path(entry)
    if p.exists() and (p / ".git").exists():
        return p
    if p.exists():
        return p  # a bare repo path
    dest = cache / _slug(entry)
    if dest.exists():
        return dest
    cache.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            ["git", "clone", "--quiet", "--no-checkout", entry, str(dest)],
            capture_output=True, text=True, check=False,
            encoding="utf-8", errors="replace",
            timeout=_CLONE_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return dest if r.returncode == 0 else None


def iter_commits(repo: Path, scan_limit: int):
    """Yield (sha, author_name, author_email, committer_name, committer_email,
    body) newest-first from the default branch."""
    fmt = "%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B%x1e"
    rc, out = _git(repo, "log", f"-{int(scan_limit)}", f"--format={fmt}")
    if rc != 0:
        return
    for record in out.split("\x1e"):
        parts = record.lstrip("\r\n").split("\x1f")
        if len(parts) == 6 and parts[0].strip():
            yield tuple(p.strip() if i == 0 else p for i, p in enumerate(parts))


def sweep_repo(entry: str, repo: Path, *, scan_limit: int,
               audit_limit: int) -> dict:
    """One repo's sweep: attribution gate, per-commit audit, per-repo fold.

    The returned dict is the OPERATOR-ONLY artifact — it carries the repo's
    identity and offending SHAs. Only `fold_aggregate` output is publishable.
    """
    attributed: list[tuple[str, str]] = []
    scanned = 0
    for sha, an, ae, cn, ce, body in iter_commits(repo, scan_limit):
        scanned += 1
        marker = classify_attribution(an, ae, cn, ce, body)
        if marker:
            attributed.append((sha, marker))
    verdicts = []
    markers: dict[str, int] = {}
    for sha, marker in attributed[:audit_limit]:
        v = audit_commit(sha, root=repo)
        if v is not None:
            verdicts.append(v)
            markers[marker] = markers.get(marker, 0) + 1
    return {
        "repo": entry,
        "commits_scanned": scanned,
        "attributed_commits": len(attributed),
        "markers": markers,
        "summary": sweep_summary(verdicts),
    }


# ---------------------------------------------------------------------------
# --enumerate — print corpus CANDIDATES from a `gh` commit search. Candidates
# only: selection against the published criteria stays a human step.
# ---------------------------------------------------------------------------

_ENUM_QUERIES = (
    ("devin", "author-email:devin-ai-integration[bot]@users.noreply.github.com"),
    ("copilot", "author-email:198982749+Copilot@users.noreply.github.com"),
    ("aider", "author-email:aider@aider.chat"),
    ("claude", '"Generated with Claude Code"'),
)


def enumerate_candidates() -> int:
    seen: dict[str, str] = {}
    for label, query in _ENUM_QUERIES:
        try:
            r = subprocess.run(
                ["gh", "api", "-X", "GET", "search/commits",
                 "-f", f"q={query}", "-f", "per_page=50",
                 "--jq", "[.items[].repository.full_name] | unique | .[]"],
                capture_output=True, text=True, check=False,
                encoding="utf-8", errors="replace", timeout=120,
                stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            print("gh unavailable — install the GitHub CLI to enumerate",
                  file=sys.stderr)
            return 1
        if r.returncode != 0:
            print(f"[enumerate] {label}: search failed", file=sys.stderr)
            continue
        for full in r.stdout.split():
            seen.setdefault(full.strip(), label)
    for full, label in sorted(seen.items()):
        print(f"{full}\t{label}")
    print(f"# {len(seen)} candidates — curate against the published criteria "
          "(stars, recency, no in-flight relationship) before sweeping",
          file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", help="text file: one repo (URL or path) per line")
    ap.add_argument("--out", default="scoreboard-out", help="output directory")
    ap.add_argument("--cache", help="clone cache dir (default <out>/clones)")
    ap.add_argument("--scan-limit", type=int, default=10000,
                    help="newest-first commits scanned per repo")
    ap.add_argument("--audit-limit", type=int, default=500,
                    help="attributed commits audited per repo")
    ap.add_argument("--stamp", help="report date (default: today UTC)")
    ap.add_argument("--enumerate", action="store_true",
                    help="print corpus candidates via gh commit search")
    args = ap.parse_args(argv)

    if args.enumerate:
        return enumerate_candidates()
    if not args.corpus:
        ap.error("--corpus is required (or use --enumerate)")

    out = Path(args.out)
    per_repo_dir = out / "per-repo"
    per_repo_dir.mkdir(parents=True, exist_ok=True)
    cache = Path(args.cache) if args.cache else out / "clones"

    entries = parse_corpus(Path(args.corpus).read_text(encoding="utf-8"))
    results: list[dict] = []
    for entry in entries:
        print(f"[sweep] {entry} ...", file=sys.stderr)
        repo = ensure_repo(entry, cache)
        if repo is None:
            print(f"[sweep] {entry}: clone failed — skipped", file=sys.stderr)
            continue
        result = sweep_repo(entry, repo, scan_limit=args.scan_limit,
                            audit_limit=args.audit_limit)
        results.append(result)
        (per_repo_dir / f"{_slug(entry)}.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8")
        s = result["summary"]
        print(f"[sweep] {entry}: {result['attributed_commits']} attributed, "
              f"{s['checkable']} checkable, {s['unwitnessed']} unwitnessed",
              file=sys.stderr)

    agg = fold_aggregate(results)
    stamp = args.stamp or datetime.now(timezone.utc).date().isoformat()
    (out / "aggregate.json").write_text(
        json.dumps({"generated": stamp, **agg}, indent=2), encoding="utf-8")
    (out / "aggregate.md").write_text(
        render_markdown(agg, stamp), encoding="utf-8", newline="\n")
    print(f"aggregate: {out / 'aggregate.md'}")
    print(f"pooled drift rate: {agg['drift_rate_pooled'] * 100:.1f}% "
          f"over {agg['checkable']} checkable claims in {agg['repos']} repos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
