#!/usr/bin/env python3
"""seed_scoreboard_index — the corpus-scale seeded scoreboard index (docs/311 P6, #98).

The discovery fan-out. One run sweeps the agent-active-repo corpus and renders a
NAMED, indexed, machine-fetchable trust page for every repo whose claim-vs-diff
verdict is CLEAN — each page an SEO/AEO landing surface carrying DOS's name, the
verdict's receipts, and the registration funnel. ~N qualifying repos → ~N
discovery contexts from one command, where the prior surfaces (answer pages,
host adapters) were each hand-authored one at a time. This is the multiplicative
shape the additive surfaces could not reach.

This is dev tooling that operates ON the repo: it `import dos` and lives under
`scripts/`, the one-way arrow `drift_scoreboard.py`/`backlog_triage.py` follow.
It COMPOSES the existing, tested pipeline rather than reimplementing it —

  enumerate   `drift_scoreboard.py --enumerate`        (corpus candidates)
  filter      the docs/311 §4 mechanical floor          (this file)
  sweep       `drift_scoreboard.py --corpus --out`      (per-repo verdicts + agg)
  render      `scoreboard_page.render_page(...)`         (the SAME §2 gate code)
  index       the index root, published-pages-only       (this file)

— so the load-bearing honesty rules are enforced by code that is already pinned,
not a copy that could drift from it.

The two structural rules, both inherited from the renderer/sweep and re-asserted
by this file's tests:

  * **§2 — a non-CLEAN verdict is never a named page.** `render_page` raises
    `Refusal` for a seeded-tier non-CLEAN verdict (tier 2a publishes CLEAN
    only). The orchestrator catches it, counts the repo as *withheld
    (aggregate-only, unnamed)*, and writes NO page and NO name for it anywhere.
    The index root lists published pages only; coverage is an aggregate count
    ("N audited, M published, K withheld"), never a name list.
  * **§4 — the corpus floor is mechanical and published.** ≥500 stars, pushed
    within the active window, not a fork, not archived, no in-flight DOS
    outreach relationship (the operator-supplied exclusion list). The ≥20
    attributed-commits floor is enforced downstream by the sweep (too few
    checkable commits → never CLEAN-with-substance → not paged).

The owner-gated boundary this file does NOT cross: **publishing to gh-pages is
an owner action** (#98 gate 4). Everything lands in a staging dir; only
`--write-index` touches the tracked index root, and even then only the
self/page-1 tier is committed — foreign named pages stage for the operator's
review (and right-of-reply where it applies) before any push.

CORPUS ACQUISITION — the real corpus-scale route is `--candidates FILE`, NOT
live `--enumerate`. Measured 2026-06-13: GitHub's public commit-search API
(`gh api search/commits`) (a) does not rank commits by repo stars, so its
default window is dominated by tiny new repos, and (b) secondary-rate-limits
aggressive pagination within a few requests — so it cannot enumerate a
star-ranked agent-active corpus at scale from one session. The methodology's
own §4 says selection stays a human step for exactly this reason. The
corpus-scale path the operator runs: build a curated `candidates.txt` of known
high-star agent-active repos (from the GH Archive / BigQuery the methodology
references, or a hand-curated seed), then `--candidates that-file` — the §4
floor re-verifies stars/recency/fork live, so a stale seed self-corrects. The
live `--enumerate` (default) is a convenience for a quick small sweep, not the
scale route.

Advisory framing (the Wall-3 line, verbatim): drift is a claim-vs-diff
mismatch, never a correctness, honesty, or intent grade.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_DRIFT = REPO / "scripts" / "drift_scoreboard.py"

# Import the renderer's PURE functions so the §2 gate is the same code, not a
# copy. (Loaded by path — scripts/ is not a package.)
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "scoreboard_page", REPO / "scripts" / "scoreboard_page.py")
_sp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sp)
render_page = _sp.render_page
load_sweep = _sp.load_sweep
Refusal = _sp.Refusal

# The §4 mechanical floor's closed exclusion vocabulary. A candidate that fails
# the floor folds to exactly one of these — the same closed-set discipline the
# kernel applies to refusals.
EXCL_BELOW_STARS = "BELOW_STARS"
EXCL_STALE = "STALE"
EXCL_FORK = "FORK"
EXCL_ARCHIVED = "ARCHIVED"
EXCL_OUTREACH = "OUTREACH_CONFLICT"
EXCL_META_FAIL = "META_FAIL"  # gh metadata unreadable — excluded conservatively
EXCL_TOO_LARGE = "TOO_LARGE"  # disk size over the budget — the full clone the
                              # ancestry audit needs would blow the time/disk
                              # budget (measured: a 1GB+ repo can't clone in a
                              # session). A shallow clone is NOT a fix — the
                              # audit reads ancestry (the GIT_DEPTH lesson).
EXCL_REASONS = (
    EXCL_BELOW_STARS, EXCL_STALE, EXCL_FORK, EXCL_ARCHIVED,
    EXCL_OUTREACH, EXCL_META_FAIL, EXCL_TOO_LARGE,
)


# ---------------------------------------------------------------------------
# enumerate + parse — candidates from drift_scoreboard.py --enumerate.
# ---------------------------------------------------------------------------


def parse_candidates(text: str) -> list[tuple[str, str]]:
    """Parse `owner/repo\\tlabel` lines (the --enumerate stdout). Comment lines
    (`# …`) and blanks are skipped. Returns (full_name, marker_label) pairs."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        full = parts[0].strip()
        label = parts[1].strip() if len(parts) > 1 else ""
        if "/" in full:
            out.append((full, label))
    return out


def enumerate_live(timeout: int = 180) -> list[tuple[str, str]]:
    """Run the existing enumerator and parse its output. [] on any failure."""
    try:
        r = subprocess.run(
            [sys.executable, str(_DRIFT), "--enumerate"],
            cwd=REPO, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_candidates(r.stdout)


# ---------------------------------------------------------------------------
# the §4 mechanical floor — pure decision over gh metadata + the exclusion set.
# ---------------------------------------------------------------------------


def classify_candidate(full: str, meta: dict | None, *, min_stars: int,
                       active_days: int, excluded: set[str],
                       now_iso: str, max_mb: int = 0) -> str | None:
    """Return None to KEEP, or an EXCL_* reason to drop. Pure over its inputs.

    `meta` is the `gh repo view --json …` dict (or None if unreadable);
    `excluded` is the lower-cased outreach-conflict set; `now_iso` is the
    YYYY-MM-DD wall date the staleness window is measured against (injected so
    the decision is testable); `max_mb` (0 = off) drops a repo whose on-disk
    size would blow the clone budget — the full ancestry clone, not a shallow
    one (the audit reads ancestry).
    """
    if full.lower() in excluded:
        return EXCL_OUTREACH
    if meta is None:
        return EXCL_META_FAIL
    if meta.get("isFork"):
        return EXCL_FORK
    if meta.get("isArchived"):
        return EXCL_ARCHIVED
    if int(meta.get("stargazerCount", 0)) < min_stars:
        return EXCL_BELOW_STARS
    # `pushedAt` is a nullable GitTimestamp — gh returns `null` (→ Python None)
    # for a repo with no push events, so `.get(key, "")` is NOT enough (it only
    # fills the default on a MISSING key). `None or ""` folds both to "", which
    # the truthy guard then skips: can't-prove-stale ⇒ treat as fresh.
    pushed = (meta.get("pushedAt") or "")[:10]  # YYYY-MM-DD
    if pushed and _days_between(pushed, now_iso) > active_days:
        return EXCL_STALE
    # diskUsage is in KB (gh's field unit); convert to MB for the budget.
    if max_mb and int(meta.get("diskUsage", 0)) / 1024 > max_mb:
        return EXCL_TOO_LARGE
    return None


def _days_between(earlier_iso: str, later_iso: str) -> int:
    """Whole days between two YYYY-MM-DD strings (no clock import — pure on the
    two strings, so the staleness gate is deterministic in tests)."""
    from datetime import date

    def _d(s: str) -> date:
        y, m, d = (int(x) for x in s[:10].split("-"))
        return date(y, m, d)
    return (_d(later_iso) - _d(earlier_iso)).days


def fetch_meta(full: str, timeout: int = 30) -> dict | None:
    """Read a repo's gating metadata via gh. None on any failure (→ META_FAIL,
    excluded conservatively — we never page a repo whose floor we can't read)."""
    try:
        r = subprocess.run(
            ["gh", "repo", "view", full, "--json",
             "stargazerCount,pushedAt,isFork,isArchived,diskUsage"],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# range SHAs — a seeded page is a pinned claim about a pinned range (§3). The
# per-repo sweep summary carries no range, so we read it from the clone.
# ---------------------------------------------------------------------------


def repo_range(clone: Path, scan_limit: int) -> tuple[str, str] | None:
    """(base_sha, head_sha) for the audited window: HEAD and the PARENT of the
    oldest commit within the newest-`scan_limit` window. None if git can't answer.

    We return the oldest commit's parent, not the oldest commit itself, because
    the page renders the range as the git two-dot reproducer `base..head`, which
    EXCLUDES base. Parent..head then covers exactly the audited window —
    inclusive of the oldest commit. When the oldest IS a root commit (no parent),
    the window genuinely begins at the repo's first commit and base..head can't
    include it; we fall back to the oldest commit's own SHA (the prior behavior),
    losing only that one root commit from the reproducer."""
    try:
        head = subprocess.run(
            ["git", "-C", str(clone), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30).stdout.strip()
        oldest = subprocess.run(
            ["git", "-C", str(clone), "rev-list", "--max-count",
             str(scan_limit), "HEAD"],
            capture_output=True, text=True, timeout=60).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    if len(head) != 40 or not oldest:
        return None
    oldest_sha = oldest[-1].strip()
    if len(oldest_sha) != 40:
        return None
    # Resolve the parent so base..head is inclusive of the oldest audited commit.
    try:
        parent = subprocess.run(
            ["git", "-C", str(clone), "rev-parse", "--verify", "--quiet",
             f"{oldest_sha}^"],
            capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        parent = ""
    base = parent if len(parent) == 40 else oldest_sha  # root commit ⇒ no parent
    return base, head


# ---------------------------------------------------------------------------
# the seeded-tier meta + the per-repo render (the §2 gate, via render_page).
# ---------------------------------------------------------------------------


def seeded_meta(full: str, *, base_sha: str, head_sha: str, rendered: str,
                auditor: str, commits: int) -> dict:
    """The seeded-tier adjudications/meta dict. records=[] — a CLEAN repo has no
    flags, which is exactly why tier 2a publishes CLEAN-only: there is nothing to
    adjudicate. A repo WITH flags hits the renderer's §2 Refusal and is withheld.
    """
    return {
        "schema": "dos-scoreboard-page/v1",
        "repo": full,
        "tier": "seeded",
        "rendered": rendered,
        "attribution": ("agent-attributed commits only (the closed marker set, "
                        "docs/scoreboard/methodology.md §3); a human commit is "
                        "never audited here"),
        "auditor": auditor,
        # NOT "newest N commits" — `commits` is the count of agent-ATTRIBUTED
        # commits audited (capped at --audit-limit), scattered within the
        # base..head window, not a contiguous newest-N prefix. The base..head row
        # pins the actual span; this number is the audited subset.
        "range": {"base_sha": base_sha, "head_sha": head_sha,
                  "commits_note": f"{commits} attributed commits audited"},
        "records": [],
    }


def render_one(per_repo: dict, *, base_sha: str, head_sha: str, rendered: str,
               auditor: str, min_checkable: int = 0) -> tuple[str, str] | None:
    """Render one repo's seeded page, or return None if a gate withholds it.

    `per_repo` is a drift_scoreboard per-repo JSON ({repo, summary, …}); the
    `repo` field there is the corpus ENTRY (a URL/path). The caller passes the
    `<org>/<name>` form via `per_repo['full_name']`.

    `min_checkable` (the small-denominator floor): a CLEAN verdict over only a
    handful of checkable claims is not a meaningful trust signal — "CLEAN over 2
    claims" says almost nothing. Below the floor the page is withheld (the §4
    ≥20-attributed-commits intent, applied to the checkable DENOMINATOR the page
    actually grades). 0 = off.
    """
    full = per_repo["full_name"]
    # The per-repo JSON's `repo` field is the corpus ENTRY (a clone URL); the
    # renderer's load_sweep pairs it against the <org>/<name> meta repo, so
    # normalize it to the canonical form before handing it over.
    paired = {**per_repo, "repo": full}
    sweep = load_sweep(paired, repo=full)
    if min_checkable and int(sweep.get("checkable", 0)) < min_checkable:
        return None  # too thin a denominator to publish a CLEAN claim honestly
    meta = seeded_meta(full, base_sha=base_sha, head_sha=head_sha,
                       rendered=rendered, auditor=auditor,
                       commits=int(sweep.get("commits", 0)))
    try:
        markdown, state = render_page(sweep, meta)
    except Refusal:
        # §2: a non-CLEAN seeded verdict does not publish — withheld, unnamed.
        return None
    if state != "CLEAN":  # belt-and-suspenders; render_page already gates this
        return None
    return markdown, state


# ---------------------------------------------------------------------------
# the index root — published (named, CLEAN) pages only; coverage is a count.
# ---------------------------------------------------------------------------


def render_index(published: list[str], *, audited: int, withheld: int,
                 rendered: str, self_page: str | None = None) -> str:
    """The index landing page. `published` is the sorted `<org>/<name>` list of
    seeded CLEAN pages; `self_page` is page #1 — the auditor's OWN repo, the one
    page that publishes its own verdict whatever it is (the docs/311 P1
    self-grades-first rule). Everything not published is a NUMBER (§2 — a
    withheld repo is never named)."""
    L = []
    L.append("# DOS drift scoreboard — the per-repo index")
    L.append("")
    L.append("> The seeded pages below each graded **CLEAN** — zero claim-vs-diff")
    L.append("> over-claims surviving adjudication over the page's pinned commit")
    L.append("> range. The page is the receipt. Drift is a claim-vs-diff mismatch,")
    L.append("> **never** a correctness, honesty, or intent grade.")
    L.append("")
    repo_noun = "repository" if audited == 1 else "repositories"
    page_noun = "page" if len(published) == 1 else "pages"
    L.append(f"**Coverage (as of {rendered}):** {audited} {repo_noun} audited, "
             f"{len(published)} {page_noun} published clean, {withheld} withheld "
             "(aggregate-only — a non-clean or unadjudicated verdict is never a "
             "named page, [docs/311](../311_scoreboard-per-repo-index-plan.md) "
             "§2).")
    L.append("")
    if self_page:
        org, name = self_page.split("/", 1)
        L.append("## Page #1 — the auditor's own repo")
        L.append("")
        L.append(f"- [{self_page}]({org}/{name}.md) — we grade ourselves first, "
                 "and publish our own verdict with its receipts whatever it says "
                 "(the self tier, not held to the seeded CLEAN-only bar).")
        L.append("")
    L.append("## Published pages (seeded — clean verdicts)")
    L.append("")
    if published:
        for full in published:
            org, name = full.split("/", 1)
            L.append(f"- [{full}]({org}/{name}.md)")
    else:
        L.append("_(none yet — the seed run publishes here once the corpus sweep "
                 "runs and the operator publishes to Pages, #98)_")
    L.append("")
    L.append("## How to read this")
    L.append("")
    L.append("- **[Methodology](methodology.md)** — what the witness reads, what "
             "it abstains on, the corpus floor, and where the auditor has been "
             "wrong.")
    L.append("- **[Aggregate report](report-2026-06.md)** — the population "
             "drift rate, denominators everywhere, identity-stripped.")
    L.append("- **Your repo graded clean and you want the page claimed (badge + "
             "machine `verdict.json`)?** See the methodology's registration "
             "section. A contested flag → the §3 correction path.")
    L.append("")
    L.append("> The kernel is the part that doesn't believe the agents.")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# the run — compose the stages, fail-closed, stage everything.
# ---------------------------------------------------------------------------


def _slug(entry: str) -> str:
    """Keep a remote repo's org so same-named repos from different orgs don't
    collide onto one per-repo JSON / clone dir. MUST match drift_scoreboard._slug
    byte-for-byte: this orchestrator keys lookups by the slug the sweep wrote
    (the bare `owner/repo` form here vs the clone URL there must agree)."""
    import re
    e = entry.replace("\\", "/").rstrip("/")
    parts = [p for p in e.split("/") if p]
    if "://" in entry or e.lower().startswith("github.com/"):
        keep = parts[-2:]
    elif "/" in entry and not entry.startswith((".", "/", "~")) and len(parts) == 2:
        keep = parts
    else:
        keep = parts[-1:]
    tail = "/".join(keep) if keep else ""
    tail = tail[:-4] if tail.endswith(".git") else tail
    return re.sub(r"[^A-Za-z0-9._-]", "_", tail) or "repo"


def run(*, candidates: list[tuple[str, str]], excluded: set[str],
        out: Path, min_stars: int, active_days: int, audit_limit: int,
        scan_limit: int, rendered: str, auditor: str, now_iso: str,
        limit: int | None, max_mb: int = 0, min_checkable: int = 0) -> dict:
    """Execute the pipeline. Returns a manifest of COUNTS + the published
    (CLEAN, named) set only — never an un-published foreign name."""
    out.mkdir(parents=True, exist_ok=True)
    if limit is not None:
        candidates = candidates[:limit]

    # -- stage: the §4 mechanical floor -------------------------------------
    kept: list[tuple[str, str]] = []
    exclusions: dict[str, str] = {}
    for full, label in candidates:
        meta = fetch_meta(full)
        reason = classify_candidate(full, meta, min_stars=min_stars,
                                    active_days=active_days, excluded=excluded,
                                    now_iso=now_iso, max_mb=max_mb)
        if reason is None:
            kept.append((full, label))
        else:
            exclusions[full] = reason
    (out / "exclusions.json").write_text(
        json.dumps(exclusions, indent=2), encoding="utf-8")

    manifest = {
        "enumerated": len(candidates),
        "kept_after_floor": len(kept),
        "excluded": len(exclusions),
        "excluded_by_reason": {
            r: sum(1 for v in exclusions.values() if v == r) for r in EXCL_REASONS
        },
    }
    if not kept:
        manifest.update({"audited": 0, "published": [], "withheld": 0})
        return manifest

    # -- stage: sweep (the heavy step — clones, audits, folds) ---------------
    corpus_file = out / "corpus.txt"
    corpus_file.write_text(
        "\n".join(f"https://github.com/{full}" for full, _ in kept) + "\n",
        encoding="utf-8")
    sweep_out = out / "sweep"
    subprocess.run(
        [sys.executable, str(_DRIFT), "--corpus", str(corpus_file),
         "--out", str(sweep_out), "--audit-limit", str(audit_limit),
         "--scan-limit", str(scan_limit), "--stamp", rendered],
        cwd=REPO, check=False)

    # -- stage: render per CLEAN repo (the §2 gate via render_page) ----------
    pages_dir = out / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    cache = sweep_out / "clones"
    published: list[str] = []
    audited = 0
    for full, _label in kept:
        per_path = sweep_out / "per-repo" / f"{_slug(full)}.json"
        if not per_path.exists():
            continue  # the sweep skipped it (clone failed) — not audited
        audited += 1
        per_repo = json.loads(per_path.read_text(encoding="utf-8"))
        per_repo["full_name"] = full
        clone = cache / _slug(f"https://github.com/{full}")
        rng = repo_range(clone, scan_limit) if clone.exists() else None
        if rng is None:
            continue  # no pinned range → can't make an honest as-of page
        base_sha, head_sha = rng
        result = render_one(per_repo, base_sha=base_sha, head_sha=head_sha,
                            rendered=rendered, auditor=auditor,
                            min_checkable=min_checkable)
        if result is None:
            continue  # §2 withheld (non-CLEAN) OR too-thin a denominator — unnamed
        markdown, _state = result
        org, name = full.split("/", 1)
        page_path = pages_dir / org / f"{name}.md"
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(markdown, encoding="utf-8")
        published.append(full)

    published.sort()
    withheld = audited - len(published)
    manifest.update({"audited": audited, "published": published,
                     "withheld": withheld})

    # -- stage: the index root (published-only) -----------------------------
    index_md = render_index(published, audited=audited, withheld=withheld,
                            rendered=rendered)
    (out / "index.md").write_text(index_md, encoding="utf-8")
    return manifest


def _auditor_string() -> str:
    """The auditor version line. Best-effort from `dos doctor`; a plain default
    if unavailable (the page still pins its range, which is the load-bearing part)."""
    try:
        r = subprocess.run([sys.executable, "-m", "dos.cli", "doctor", "--json"],
                           cwd=REPO, capture_output=True, text=True, timeout=30)
        d = json.loads(r.stdout)
        ver = d.get("version") or d.get("dos_version") or ""
        if ver:
            return f"dos-kernel {ver}"
    except Exception:
        pass
    return "dos-kernel (commit-audit witness)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--candidates", help="file of `owner/repo[\\tlabel]` lines "
                    "— THE corpus-scale route (a curated high-star agent-active "
                    "seed; the §4 floor re-verifies it live). Default: a small "
                    "live `drift_scoreboard.py --enumerate` (rate-limited, "
                    "not star-ranked — a quick sweep, not the scale route).")
    ap.add_argument("--exclude", help="outreach-conflict file: one owner/repo "
                    "per line (the §4 'no grading whom we court' rule)")
    ap.add_argument("--out", default="scoreboard-seed-out",
                    help="staging dir (gitignored; gh-pages push is owner-gated)")
    ap.add_argument("--min-stars", type=int, default=500)
    ap.add_argument("--active-days", type=int, default=90)
    ap.add_argument("--audit-limit", type=int, default=500)
    ap.add_argument("--scan-limit", type=int, default=10000)
    ap.add_argument("--limit", type=int,
                    help="bound the candidate count (the bounded-proof knob)")
    ap.add_argument("--max-clone-mb", type=int, default=0,
                    help="skip a repo whose on-disk size exceeds this many MB "
                    "(0 = off). The audit needs the FULL ancestry clone, so a "
                    "huge repo blows the time/disk budget — measured: a 1GB+ "
                    "repo can't clone in a session. A shallow clone is not a "
                    "fix (ancestry). Excluded as TOO_LARGE.")
    ap.add_argument("--min-checkable", type=int, default=20,
                    help="withhold a CLEAN page with fewer than this many "
                    "checkable claims — a CLEAN verdict over a handful of claims "
                    "is not a real trust signal (the §4 small-denominator floor, "
                    "applied to the checkable denominator). Default 20; 0 = off.")
    ap.add_argument("--rendered", help="render date YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--write-index", action="store_true",
                    help="write the tracked docs/scoreboard/README.md index "
                    "(owner-gated: only the self-tier is committed)")
    ap.add_argument("--json", action="store_true", help="emit the run manifest")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    rendered = args.rendered
    if not rendered:
        # The one clock read, at the boundary (the report's as-of date).
        from datetime import datetime, timezone
        rendered = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = rendered

    if args.candidates:
        candidates = parse_candidates(Path(args.candidates).read_text(encoding="utf-8"))
    else:
        candidates = enumerate_live()
    if not candidates:
        print("no candidates (enumerate failed or empty file)", file=sys.stderr)
        return 2

    excluded = set()
    if args.exclude:
        for line in Path(args.exclude).read_text(encoding="utf-8").splitlines():
            s = line.strip().lower()
            if s and not s.startswith("#"):
                excluded.add(s)
    else:
        print("note: no --exclude list — the §4 outreach-conflict rule (do not "
              "grade whom we court) is the operator's to supply", file=sys.stderr)

    manifest = run(
        candidates=candidates, excluded=excluded, out=Path(args.out),
        min_stars=args.min_stars, active_days=args.active_days,
        audit_limit=args.audit_limit, scan_limit=args.scan_limit,
        rendered=rendered, auditor=_auditor_string(), now_iso=now_iso,
        limit=args.limit, max_mb=args.max_clone_mb,
        min_checkable=args.min_checkable)

    if args.write_index:
        # The self-tier-only contract (docstring §"owner-gated boundary"): the
        # TRACKED index commits page #1 (the auditor's own repo) ONLY. Foreign
        # CLEAN pages stage under --out for the operator's review + right-of-reply
        # before any gh-pages push — they are NOT named in the committed file, and
        # their links would dangle (the pages live in the gitignored staging dir,
        # not under docs/scoreboard/). So pass published=[] and count just the
        # self-page; the foreign counts live in the staged manifest, not here.
        index_md = render_index(
            [], audited=1, withheld=0, rendered=rendered,
            self_page=_sp._AUDITOR_REPO)
        (REPO / "docs" / "scoreboard" / "README.md").write_bytes(
            index_md.encode("utf-8"))

    if args.json:
        print(json.dumps(manifest, indent=2))
    else:
        m = manifest
        print(f"enumerated {m['enumerated']} · kept {m['kept_after_floor']} "
              f"after the §4 floor · audited {m.get('audited', 0)} · "
              f"published {len(m.get('published', []))} clean · "
              f"withheld {m.get('withheld', 0)} (aggregate-only)")
        print(f"staged in {args.out}/ (gh-pages push is the owner action, #98)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
