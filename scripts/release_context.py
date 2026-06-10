#!/usr/bin/env python3
"""Pre-digest git + version state for the DOS /release skill.

Collapses `git log -N`, `git log tag..HEAD`, `git diff --stat tag..HEAD`,
`git status`, the version-drift check, per-file diff previews, and the prior
release-notes style cue into one JSON payload. Lets the skill skip re-reading
N command outputs / per-file diffs into the model context.

This is **dev / release tooling, not kernel** — it operates ON the package but
is never imported BY it (the `dos.*` modules import nothing under `scripts/`).
It is the DOS analogue of the reference userland app's release-context script,
stripped of every host-ism: DOS single-sources its version from `pyproject.toml`
(one marker, not four — no `VERSION` file, no Go `version.txt`, no per-package
`__init__.py` version), has no apply-loop / dispatch-loop / fanout-run machinery,
and ships no Go binary or zip. The `active_leases` auto-defer and the fanout-run
/ apply-scratch buckets are therefore gone; the generic git + drift + preview
spine remains.

Workspace anchor: `git rev-parse --show-toplevel` (the repo this script is run
*inside*), NOT `__file__/../..`. The script ships with the repo it releases, so
the git top-level is the correct root even when invoked from a subdir. This also
keeps it honest if the `scripts/` dir is ever vendored elsewhere.

Usage:
  python scripts/release_context.py
  python scripts/release_context.py --limit-commits 40
  python scripts/release_context.py --no-diff-stat   # omit files_touched
  python scripts/release_context.py --no-previews    # omit per-file previews

Output JSON shape (top-level keys):
  head_sha:                short HEAD sha
  current_branch:          branch name (or empty if detached)
  default_branch:          the trunk this repo pushes to ("master" for DOS)
  last_tag:                most recent vX.Y.Z tag on HEAD's ancestry, or null
  commits_since_tag:       [{sha, subject, files: [{path, additions, deletions}]}]
  files_touched_since_tag: [path, ...] (from --name-only)
  clean_tree:              true iff `git status --porcelain` is empty
  modified:                tracked modified/staged paths
  untracked:               {scratch, release_drafts, tracked_docs, other}
  version_files:           {pyproject, init_fallback, drift: bool}
                            -- pyproject.toml is the single source of truth;
                            src/dos/__init__.py carries a literal fallback that
                            should equal it. `drift` is true iff they disagree.
  drafted_release_past_tag: "vX.Y.Z" if an untracked-or-committed release file
                            is past last_tag (racing-agent signal), else null
  modified_diff_previews:  {path: "first ~30 lines of `git diff HEAD -- path`"}
  untracked_doc_previews:  {path: "first docstring or first 10 non-blank lines"}
  prior_release_style:     {version, headline, themes, highlights, section_headings}
  phantom_diffs:           [{path, diff_lines, reason}] -- YAML/JSON files that
                            parse-equal HEAD but show a large textual diff (a
                            writer round-tripped + reformatted them).
  docs_only:               {docs_only: bool, paths: [...], note: str} -- ADVISORY
                            in DOS (unlike the reference userland app, which
                            refuses). Docs are
                            first-class deliverables of the substrate repo, so a
                            docs-only release is legitimate; this key just flags
                            it so the skill can pick `patch` + a docs theme.
  active_leases:           [{lane, lane_kind, tree, stale, holder, age_s,
                            heartbeat_age_s, ttl_s}] -- the lane-journal WAL
                            folded to the LIVE-lease set (the same fold `dos top`
                            renders), so the /release skill can auto-defer dirty
                            paths a live `/dispatch-loop` is still writing (Step
                            1.6). This is the DOS-native form of the userland
                            app's `active_leases`: DOS reads its OWN kernel
                            correlation spine (`dos.lane_journal`), not a bespoke
                            host field. A lease past its TTL heartbeat is reported
                            with `stale: true` and is NOT deferred (a dead loop's
                            region is fair game). Empty `[]` when there is no
                            journal, the journal is unreadable, or `dos` is not
                            importable -- this key NEVER crashes the release-context
                            read (it is advisory auto-defer, not a gate).
  workflows_parse_ok:      {ok: bool, files: {path: error|null}, note} -- a YAML
                            parse of every .github/workflows/*.yml at the working
                            tree (docs/295 P1). The v0.23.0 class: an unparseable
                            workflow fails CI in 0 seconds on every push, so a
                            release cut on top of it can never green. ok=true with
                            a note when there is no workflows dir or no PyYAML
                            (fail-to-abstain, never a crash).
  ci_on_head:              {status, runs_on_head, latest_trunk_ci, note} -- a
                            `gh`-derived digest of CI state (docs/295 P1):
                            `runs_on_head` lists runs on the exact HEAD sha (often
                            empty pre-push -- normal), `latest_trunk_ci` is the
                            most recent COMPLETED ci.yml run on the trunk (the
                            base this release builds on; its red was visible 3
                            minutes before the v0.23.0 tag). `status` folds
                            latest_trunk_ci to green|red|none|unknown. Advisory,
                            fail-to-abstain: a missing `gh` / offline / timeout
                            degrades to status=unknown, never blocks.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
RELEASE_FILE_RE = re.compile(r"^docs/releases/v(\d+\.\d+\.\d+)\.md$")

# Scratch patterns that should never survive a release (repo-root-relative).
# Mirrors the DOS `.gitignore` scratch conventions — `_scratch/`, `*.err`,
# downloaded `*.html` research dumps, and `scripts/_*.py` short-lived probes —
# plus a generic root-level dotfile-probe shape. No apply-/snapshot-/fanout-
# scratch (those are host concepts).
SCRATCH_PATTERNS = [
    re.compile(r"^_scratch/"),
    re.compile(r"^.*\.err$"),
    re.compile(r"^.*\.html$"),               # research scrapes (see .gitignore)
    re.compile(r"^scripts/_[^/]+\.py$"),     # leading-underscore = scratch probe
    re.compile(r"^\.dos-workspace/"),
    re.compile(r"^[^/]+\.png$"),             # root-level PNGs are scratch
    re.compile(r"^\.[a-z][\w]*_[\w.]+\.(py|json|err|yml|html|md|txt)$"),
]

# Tracked-doc subtree — `docs/**` is durable in the substrate repo (plan docs,
# HACKING.md, postmortems). Excludes
# `docs/releases/v*.md` which has its own bucket.
TRACKED_DOC_RE = re.compile(r"^docs/")

# DOS pushes to `master` (no `main` branch). The skill reads this so it never
# hardcodes the trunk name.
DEFAULT_BRANCH = "master"


def run(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(
            # errors="replace": `git diff` on a deleted BINARY file (e.g. a
            # removed PDF) emits raw non-UTF-8 bytes that would
            # otherwise crash the whole release-context read. A release tree can
            # legitimately carry binary deletions — degrade the undecodable byte
            # to U+FFFD rather than abort the release.
            cmd, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as exc:
        # Non-zero exit is sometimes expected (e.g. no tags). Return stdout anyway.
        return exc.output or ""
    except FileNotFoundError:
        return ""


def repo_root() -> Path:
    """The git top-level of the repo this script is run inside.

    NOT `__file__/../..` — the substrate kernel's prime directive is that it
    never assumes it lives in the repo it serves. The release script ships with
    the repo it releases, so `git rev-parse --show-toplevel` is the honest
    anchor (and survives invocation from any subdir).
    """
    top = run(["git", "rev-parse", "--show-toplevel"]).strip()
    if top:
        return Path(top)
    return Path.cwd()


def semver_tuple(s: str) -> tuple[int, int, int] | None:
    m = SEMVER_RE.match(s)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def latest_tag() -> str | None:
    raw = run(["git", "tag", "--sort=-v:refname"]).strip().splitlines()
    for line in raw:
        if SEMVER_RE.match(line):
            return line
    return None


def commits_since(tag: str | None, limit: int) -> list[dict]:
    """List commits since tag, with --numstat per-commit file deltas inlined."""
    fmt = "COMMIT\x1f%h\x1f%s"
    if tag:
        raw = run(["git", "log", f"{tag}..HEAD", f"--pretty=format:{fmt}",
                   "--numstat", f"-n{limit}"])
    else:
        raw = run(["git", "log", f"--pretty=format:{fmt}", "--numstat", f"-n{limit}"])
    out: list[dict] = []
    cur: dict | None = None
    for raw_line in raw.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            continue
        if line.startswith("COMMIT\x1f"):
            parts = line.split("\x1f", 2)
            if len(parts) >= 3:
                if cur is not None:
                    out.append(cur)
                cur = {"sha": parts[1], "subject": parts[2], "files": []}
            continue
        # numstat row: "<adds>\t<dels>\t<path>"  (adds/dels may be "-" for binary)
        if cur is None:
            continue
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        adds_s, dels_s, path = cols[0], cols[1], "\t".join(cols[2:])

        def _to_int(x: str) -> int | None:
            try:
                return int(x)
            except ValueError:
                return None

        cur["files"].append({
            "path": path,
            "additions": _to_int(adds_s),
            "deletions": _to_int(dels_s),
        })
    if cur is not None:
        out.append(cur)
    return out


_DIFF_PREVIEW_LINE_CAP = 30
_DIFF_PREVIEW_BYTE_CAP = 2048
_DOC_PREVIEW_BYTE_CAP = 500
_DOC_PREVIEW_LINE_CAP = 10


def diff_preview(path: str) -> str | None:
    """Short `git diff HEAD -- <path>` preview, capped to ~2 KB / 30 lines."""
    raw = run(["git", "diff", "HEAD", "--", path])
    if not raw or raw.startswith("Binary files"):
        return None
    lines = raw.splitlines()[:_DIFF_PREVIEW_LINE_CAP]
    text = "\n".join(lines)
    if len(text) > _DIFF_PREVIEW_BYTE_CAP:
        text = text[:_DIFF_PREVIEW_BYTE_CAP] + "\n... (truncated)"
    return text


_PHANTOM_DIFF_LINE_THRESHOLD = 50


def phantom_diff_check(root: Path, path: str) -> dict | None:
    """Detect a phantom diff: YAML/JSON files that parse-equal HEAD but show a
    large textual change (a writer round-tripped the file and stripped comments
    / reformatted quotes / re-indented). Returns a dict with the path, diff
    line-count, and a one-line explanation when phantom; ``None`` otherwise.

    Kept from the reference userland app because DOS's state plane is git-native
    YAML (dos.toml and host state files) — exactly the file class that round-trips
    badly through a naive PyYAML load+dump.
    """
    lower = path.lower()
    if not (lower.endswith(".yaml") or lower.endswith(".yml") or lower.endswith(".json")):
        return None

    head_text = run(["git", "show", f"HEAD:{path}"])
    if not head_text:
        return None
    try:
        wt_text = (root / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    diff_raw = run(["git", "diff", "HEAD", "--", path])
    if not diff_raw:
        return None
    diff_lines = [
        ln for ln in diff_raw.splitlines()
        if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))
    ]
    if len(diff_lines) < _PHANTOM_DIFF_LINE_THRESHOLD:
        return None

    try:
        if lower.endswith(".json"):
            import json as _json
            head_obj = _json.loads(head_text)
            wt_obj = _json.loads(wt_text)
        else:
            import yaml as _yaml
            head_obj = _yaml.safe_load(head_text)
            wt_obj = _yaml.safe_load(wt_text)
    except Exception:
        return None

    if head_obj == wt_obj:
        return {
            "path": path,
            "diff_lines": len(diff_lines),
            "reason": (
                "File parses identically to HEAD but textually diverges by "
                f"{len(diff_lines)} +/- lines. A writer likely loaded and "
                "re-serialised this file (stripping comments / reformatting "
                "quotes). Fix the writer or restore from HEAD before committing."
            ),
        }
    return None


def doc_preview(root: Path, rel_path: str) -> str | None:
    """First docstring or first ~10 non-blank lines, capped to ~500 bytes."""
    p = root / rel_path
    try:
        if not p.exists() or p.is_dir():
            return None
        text = p.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.strip():
        return None
    if rel_path.endswith(".py"):
        m = re.search(r'^\s*[ru]?"""(.+?)"""', text, re.DOTALL)
        if m:
            doc = m.group(1).strip()
            if len(doc) > _DOC_PREVIEW_BYTE_CAP:
                doc = doc[:_DOC_PREVIEW_BYTE_CAP] + "..."
            return doc
    non_blank: list[str] = []
    for line in text.splitlines():
        if line.strip():
            non_blank.append(line.rstrip())
        if len(non_blank) >= _DOC_PREVIEW_LINE_CAP:
            break
    out = "\n".join(non_blank)
    if len(out) > _DOC_PREVIEW_BYTE_CAP:
        out = out[:_DOC_PREVIEW_BYTE_CAP] + "..."
    return out or None


def prior_release_style(root: Path) -> dict | None:
    """Parse front-matter + section headings from the most recent release notes."""
    rel_dir = root / "docs" / "releases"
    if not rel_dir.exists():
        return None
    latest: tuple[tuple[int, int, int], Path] | None = None
    for p in rel_dir.glob("v*.md"):
        t = semver_tuple(p.stem)
        if t is None:
            continue
        if latest is None or t > latest[0]:
            latest = (t, p)
    if latest is None:
        return None
    try:
        text = latest[1].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    out: dict = {"file": latest[1].name}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[3:end].strip()
            for raw_line in fm.splitlines():
                line = raw_line.strip()
                if line.startswith("version:"):
                    out["version"] = line.split(":", 1)[1].strip()
                elif line.startswith("date:"):
                    out["date"] = line.split(":", 1)[1].strip()
                elif line.startswith("headline:"):
                    out["headline"] = line.split(":", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("themes:"):
                    rest = line.split(":", 1)[1].strip()
                    if rest.startswith("[") and rest.endswith("]"):
                        out["themes"] = [
                            t.strip().strip('"').strip("'")
                            for t in rest[1:-1].split(",") if t.strip()
                        ]
    hl: list[str] = []
    in_hl = False
    for raw_line in text.splitlines():
        if raw_line.strip().startswith("highlights:"):
            in_hl = True
            continue
        if in_hl:
            stripped = raw_line.strip()
            if stripped.startswith("- "):
                hl.append(stripped[2:].strip().strip('"').strip("'"))
                if len(hl) >= 6:
                    break
            elif stripped.startswith("---") or (stripped and not raw_line.startswith(" ")):
                break
    if hl:
        out["highlights"] = hl
    out["section_headings"] = [
        line[3:].strip() for line in text.splitlines() if line.startswith("## ")
    ][:8]
    return out


def files_touched(tag: str | None) -> list[str]:
    if tag:
        raw = run(["git", "diff", "--name-only", f"{tag}..HEAD"])
    else:
        raw = run(["git", "ls-files"])
    return [line for line in raw.splitlines() if line.strip()]


def porcelain_status() -> list[tuple[str, str]]:
    """Return (status_code, path) pairs from `git status --porcelain=v1 -z`."""
    raw = run(["git", "status", "--porcelain=v1", "-z"])
    if not raw:
        return []
    items = raw.split("\0")
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(items):
        entry = items[i]
        if not entry:
            i += 1
            continue
        code = entry[:2]
        path = entry[3:]
        if code[0] == "R" or code[0] == "C":
            i += 2
        else:
            i += 1
        out.append((code, path))
    return out


# ---- docs-only advisory (NOT a refusal in DOS) -----------------------------
#
# the reference userland app's release skill REFUSES a docs-only snapshot
# ("docs-only / observation-only phases do NOT release"). DOS is the opposite:
# it is a substrate whose docs (the plan series, HACKING.md) are
# first-class deliverables, and a docs-only release is a legitimate, common
# event. So DOS keeps the classifier but downgrades it to an ADVISORY flag the
# skill uses to pick `patch` + a `docs` theme — it never blocks the release.

_CONFIG_SUFFIXES = (".yaml", ".yml", ".toml", ".cfg", ".ini")
_CONFIG_FILENAMES = frozenset({".gitignore", "Makefile", "MANIFEST.in", "LICENSE"})
_VERSION_MARKER_PATHS = frozenset({"pyproject.toml", "src/dos/__init__.py"})


def classify_path(path: str) -> str:
    """Classify a repo-relative path into one of {code, docs, config, scratch}.

    First match wins: scratch -> docs -> config -> code (inclusive fallback).
    Pure function. Used only to compute the docs-only advisory.
    """
    pn = path.replace("\\", "/")
    if RELEASE_FILE_RE.match(pn):
        return "scratch"
    if any(pat.match(pn) for pat in SCRATCH_PATTERNS):
        return "scratch"
    if TRACKED_DOC_RE.match(pn):
        return "docs"
    if pn in _VERSION_MARKER_PATHS:
        return "config"
    if pn in _CONFIG_FILENAMES:
        return "config"
    if pn.endswith(_CONFIG_SUFFIXES):
        return "config"
    return "code"


_DOCS_ONLY_NOTE = (
    "Snapshot is docs/scratch only. In DOS this is a legitimate release (docs "
    "are first-class substrate deliverables) — pick `patch` and a docs theme. "
    "This is advisory, not a refusal."
)
_NOT_DOCS_ONLY_NOTE = "Snapshot includes code/config — a normal release."
_EMPTY_NOTE = "Empty snapshot — nothing to release."


def compute_docs_only(modified: list[str], untracked: dict) -> dict:
    """Advisory: is the snapshot docs/scratch only? (Never refuses in DOS.)"""
    union: list[str] = []
    union.extend(modified or [])
    union.extend((untracked or {}).get("tracked_docs", []) or [])
    union.extend((untracked or {}).get("other", []) or [])
    seen: set[str] = set()
    dedup: list[str] = []
    for p in union:
        if p not in seen:
            seen.add(p)
            dedup.append(p)
    if not dedup:
        return {"docs_only": False, "paths": [], "note": _EMPTY_NOTE}
    classes = {p: classify_path(p) for p in dedup}
    has_code_or_config = any(c in {"code", "config"} for c in classes.values())
    if has_code_or_config:
        return {"docs_only": False, "paths": [], "note": _NOT_DOCS_ONLY_NOTE}
    offending = sorted(p for p, c in classes.items() if c in {"docs", "scratch"})
    return {"docs_only": True, "paths": offending, "note": _DOCS_ONLY_NOTE}


# ---- active-lease auto-defer (DOS-native) ----------------------------------
#
# The userland app's /release Step 1.6 reads an `active_leases` field its
# dispatch loop writes, and defers any dirty path a live lane lease still owns
# (shipping a mid-flight edit would be the bug). DOS has the SAME hazard — the
# working tree here is multi-session-hot — but a BETTER source for the answer:
# its own kernel WAL. `lane_journal.read_all → replay` is exactly the fold
# `dos top` renders to show which lanes are held right now. So the release flow
# becomes a CLIENT of the arbiter's own evidence (the "DOS on DOS" dogfood) —
# it reads the same live-lease set `dos arbitrate` admits against, and defers a
# leased region the way the arbiter would refuse a contended lane.
#
# Degradation is total: any failure (no journal, torn journal, `dos` not
# importable from this checkout) yields [] — the auto-defer is advisory, never a
# gate, so it must not crash the release-context read.

# Mirror of dos.lane_lease.DEFAULT_TTL_SECONDS — the heartbeat staleness window.
# Duplicated as a literal (not imported) so the staleness read still works when
# the lease records omit an explicit ttl; the import path stays optional.
_DEFAULT_LEASE_TTL_SECONDS = 300


def _lease_age_seconds(ts: str | None, now: "object") -> float | None:
    """Seconds between an ISO stamp and `now` (a datetime), tolerant of junk."""
    if not ts:
        return None
    import datetime as _dt
    raw = str(ts).strip().replace("Z", "+00:00")
    try:
        parsed = _dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    delta = now - parsed
    return delta.total_seconds()


def compute_active_leases(root: Path) -> list[dict]:
    """Fold the lane-journal WAL → the live-lease set, with a staleness flag.

    Returns one dict per live lease: ``{lane, lane_kind, tree, stale, holder,
    age_s, heartbeat_age_s, ttl_s}``. ``tree`` is the lease's glob-region list
    (what the skill matches dirty paths against). ``stale`` is true when the
    freshest heartbeat (or the acquire stamp) is older than the lease TTL — a
    stale lease is reported but its region is NOT deferred (the holder died
    without releasing). Pure-ish: the only side effect is reading the journal
    file; never raises.
    """
    try:
        import datetime as _dt
        from dos import config as _cfg
        from dos import lane_journal as _lj
    except Exception:
        return []
    try:
        # `load_workspace_config` is the SAME builder the `dos` CLI + the MCP
        # server use (so the journal path the skill reads can't drift from the
        # one the kernel writes); it honours this workspace's `dos.toml` lane +
        # path layout. We anchor it on the git top-level `root`, not cwd.
        cfg = _cfg.load_workspace_config(root)
        entries = _lj.read_all(cfg.paths.lane_journal)
        leases = _lj.replay(entries)
    except Exception:
        return []

    now = _dt.datetime.now(_dt.timezone.utc)
    out: list[dict] = []
    for lease in leases:
        if not isinstance(lease, dict):
            continue
        # ttl_minutes is the lease's own field; fall back to the kernel default.
        ttl_min = lease.get("ttl_minutes")
        try:
            ttl_s = float(ttl_min) * 60.0 if ttl_min is not None else float(_DEFAULT_LEASE_TTL_SECONDS)
        except (TypeError, ValueError):
            ttl_s = float(_DEFAULT_LEASE_TTL_SECONDS)
        acquired_age = _lease_age_seconds(lease.get("acquired_at"), now)
        hb_age = _lease_age_seconds(
            lease.get("heartbeat_at") or lease.get("acquired_at"), now
        )
        # Stale iff we have a credible freshest stamp AND it is past the TTL. A
        # lease with NO parseable stamp is treated as live (the conservative,
        # defer-MORE direction — better to skip a path than ship a mid-flight one).
        stale = hb_age is not None and hb_age > ttl_s
        tree = lease.get("tree")
        out.append({
            "lane": str(lease.get("lane") or ""),
            "lane_kind": str(lease.get("lane_kind") or ""),
            "tree": [str(t) for t in tree] if isinstance(tree, list) else [],
            "stale": bool(stale),
            "holder": lease.get("holder") or lease.get("loop_ts") or None,
            "age_s": round(acquired_age, 1) if acquired_age is not None else None,
            "heartbeat_age_s": round(hb_age, 1) if hb_age is not None else None,
            "ttl_s": ttl_s,
        })
    return out


# ---- P1 preflight facts (docs/295) ------------------------------------------
#
# Two facts the v0.23.x night proved are release-blocking yet were never read
# pre-tag. Both are advisory and fail-to-abstain: their job is to put the fact
# in the Step-1 payload the skill already consumes, never to crash or block it.

def compute_workflows_parse(root: Path) -> dict:
    """YAML-parse every workflow file — the v0.23.0 class, caught offline in ms.

    An unparseable workflow fails CI in 0 seconds on every push, so a release
    cut on top of it can never satisfy the publish ci-green witness. Returns
    ``{ok, files: {rel_path: error|None}, note}``; degrades to ``ok=True`` with
    a note (no workflows dir / no PyYAML) rather than guessing red.
    """
    out: dict = {"ok": True, "files": {}, "note": None}
    wf_dir = root / ".github" / "workflows"
    if not wf_dir.is_dir():
        out["note"] = "no .github/workflows directory"
        return out
    try:
        import yaml as _yaml
    except Exception:
        out["note"] = "PyYAML unavailable - parse check skipped"
        return out
    paths = sorted(list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml")))
    for p in paths:
        rel = p.relative_to(root).as_posix()
        try:
            _yaml.safe_load(p.read_text(encoding="utf-8"))
            out["files"][rel] = None
        except Exception as exc:
            # One line, whitespace-folded: yaml errors are multiline with
            # position info; keep the position, drop the layout.
            out["files"][rel] = " ".join(str(exc).split())[:300] or exc.__class__.__name__
            out["ok"] = False
    return out


_GH_TIMEOUT_SECONDS = 15


def _run_gh_json(args: list[str]) -> object | None:
    """Run a `gh ... --json ...` command, parse stdout as JSON; None on any
    failure (gh missing, offline, timeout, auth, junk output)."""
    try:
        raw = subprocess.check_output(
            ["gh", *args], stderr=subprocess.DEVNULL, text=True,
            encoding="utf-8", errors="replace", timeout=_GH_TIMEOUT_SECONDS,
        )
        return json.loads(raw)
    except Exception:
        return None


def compute_ci_on_head(default_branch: str) -> dict:
    """Digest CI state around the release base — the fact nobody read pre-tag.

    ``runs_on_head`` lists runs on the exact HEAD sha (usually empty before the
    release pushes — that is normal, not red). ``latest_trunk_ci`` is the most
    recent COMPLETED ci.yml run on the trunk: the base this release builds on.
    The v0.23.0 parse break was visible there as a 0-second failure three
    minutes before the tag. ``status`` folds latest_trunk_ci to
    green|red|none|unknown; unknown means `gh` could not answer (advisory,
    fail-to-abstain — the skill warns, never blocks, on unknown).
    """
    head = run(["git", "rev-parse", "HEAD"]).strip()
    runs_on_head: list[dict] = []
    if head:
        got = _run_gh_json([
            "run", "list", "--commit", head, "--limit", "10",
            "--json", "workflowName,status,conclusion",
        ])
        if isinstance(got, list):
            runs_on_head = [
                {
                    "workflow": r.get("workflowName"),
                    "status": r.get("status"),
                    "conclusion": r.get("conclusion") or None,
                }
                for r in got if isinstance(r, dict)
            ]

    # The newest DECISIVE completed run: a cancelled/skipped run (superseded by
    # a newer push under concurrency rules) carries no verdict on the trunk —
    # folding it to red would cry wolf, folding it to green would lie. Walk the
    # recent completed runs and report the first one whose conclusion actually
    # adjudicates the bytes.
    _DECISIVE = {"success", "failure", "timed_out", "startup_failure"}
    latest_trunk = _run_gh_json([
        "run", "list", "--workflow", "ci.yml", "--branch", default_branch,
        "--status", "completed", "--limit", "5",
        "--json", "conclusion,headSha,updatedAt",
    ])
    trunk_ci: dict | None = None
    indecisive_skipped = 0
    if isinstance(latest_trunk, list):
        for r in latest_trunk:
            if not isinstance(r, dict):
                continue
            if (r.get("conclusion") or "") in _DECISIVE:
                trunk_ci = {
                    "conclusion": r.get("conclusion"),
                    "head_sha": (r.get("headSha") or "")[:7] or None,
                    "updated_at": r.get("updatedAt"),
                    "indecisive_runs_since": indecisive_skipped,
                }
                break
            indecisive_skipped += 1

    if latest_trunk is None:
        status, note = "unknown", "gh unavailable/offline - CI state not read"
    elif trunk_ci is None:
        status, note = "none", "no decisive completed ci.yml run on the trunk in the last 5"
    elif trunk_ci["conclusion"] == "success":
        status, note = "green", None
    else:
        status, note = "red", (
            "the latest decisive trunk CI run is not green - a release cut on "
            "this base inherits it; fix forward before tagging (docs/295 P1)"
        )
    return {
        "status": status,
        "runs_on_head": runs_on_head,
        "latest_trunk_ci": trunk_ci,
        "note": note,
    }


def classify_untracked(paths: list[str]) -> dict:
    scratch: list[str] = []
    release_drafts: list[str] = []
    tracked_docs: list[str] = []
    other: list[str] = []
    for p in paths:
        pn = p.replace("\\", "/")
        if RELEASE_FILE_RE.match(pn):
            release_drafts.append(pn)
            continue
        if any(pat.match(pn) for pat in SCRATCH_PATTERNS):
            scratch.append(pn)
            continue
        if TRACKED_DOC_RE.match(pn):
            tracked_docs.append(pn)
            continue
        other.append(pn)
    return {
        "scratch": scratch,
        "release_drafts": release_drafts,
        "tracked_docs": tracked_docs,
        "other": other,
    }


def read_version_files(root: Path) -> dict:
    """Current version values + whether the two DOS markers agree.

    DOS single-sources the version from `pyproject.toml`. `src/dos/__init__.py`
    reads it back via `importlib.metadata` at runtime but carries a literal
    fallback string for the uninstalled-source-tree case; that literal must
    equal pyproject's value or the CLI misreports its version when run from a
    bare checkout (this drifted once — see the comment in __init__.py).
    """
    def safe_read(rel: str) -> str | None:
        p = root / rel
        if not p.exists():
            return None
        return p.read_text(encoding="utf-8")

    pyp_text = safe_read("pyproject.toml") or ""
    m_pyp = re.search(r'^version\s*=\s*"([^"]+)"', pyp_text, re.MULTILINE)
    pyp_ver = m_pyp.group(1) if m_pyp else None

    init_text = safe_read("src/dos/__init__.py") or ""
    # The fallback literal: `__version__ = "X.Y.Z"` inside the except branch.
    m_init = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
    init_fallback = m_init.group(1) if m_init else None

    markers = [v for v in (pyp_ver, init_fallback) if v is not None]
    drift = len(set(markers)) > 1

    return {
        "pyproject": pyp_ver,
        "init_fallback": init_fallback,
        "drift": drift,
    }


def drafted_release_past(root: Path, last_tag: str | None) -> str | None:
    """Find a docs/releases/vX.Y.Z.md whose version > last_tag (racing-agent signal)."""
    rel_dir = root / "docs" / "releases"
    if not rel_dir.exists():
        return None
    last_t = semver_tuple(last_tag) if last_tag else (0, 0, 0)
    latest: tuple[tuple[int, int, int], str] | None = None
    for p in rel_dir.glob("v*.md"):
        t = semver_tuple(p.stem)
        if t and (last_t is None or t > last_t):
            if latest is None or t > latest[0]:
                latest = (t, p.stem)
    return latest[1] if latest else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pre-digest git+version state for the DOS /release skill."
    )
    parser.add_argument("--limit-commits", type=int, default=50,
                        help="Max commits to include (default 50)")
    parser.add_argument("--no-diff-stat", action="store_true",
                        help="Omit files_touched_since_tag")
    parser.add_argument("--no-previews", action="store_true",
                        help="Omit diff/doc previews + prior_release_style")
    args = parser.parse_args()

    root = repo_root()

    head_sha = run(["git", "rev-parse", "--short", "HEAD"]).strip() or None
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    if branch == "HEAD":
        branch = ""

    tag = latest_tag()
    commits = commits_since(tag, limit=args.limit_commits)
    touched = [] if args.no_diff_stat else files_touched(tag)

    status = porcelain_status()
    modified = [p for code, p in status if code != "??"]
    untracked_paths = [p for code, p in status if code == "??"]
    untracked = classify_untracked(untracked_paths)

    payload: dict = {
        "head_sha": head_sha,
        "current_branch": branch,
        "default_branch": DEFAULT_BRANCH,
        "last_tag": tag,
        "commits_since_tag": commits,
        "files_touched_since_tag": touched,
        "clean_tree": len(status) == 0,
        "modified": modified,
        "untracked": untracked,
        "version_files": read_version_files(root),
        "drafted_release_past_tag": drafted_release_past(root, tag),
        "docs_only": compute_docs_only(modified, untracked),
        "active_leases": compute_active_leases(root),
        "workflows_parse_ok": compute_workflows_parse(root),
        "ci_on_head": compute_ci_on_head(DEFAULT_BRANCH),
    }

    phantom_diffs: list[dict] = []
    for p in modified:
        phantom = phantom_diff_check(root, p)
        if phantom is not None:
            phantom_diffs.append(phantom)
    payload["phantom_diffs"] = phantom_diffs

    if not args.no_previews:
        previews: dict[str, str] = {}
        for p in modified:
            preview = diff_preview(p)
            if preview:
                previews[p] = preview
        payload["modified_diff_previews"] = previews

        doc_paths = list(untracked["tracked_docs"]) + [
            p for p in untracked["other"] if p.endswith(".py")
        ]
        doc_previews: dict[str, str] = {}
        for p in doc_paths:
            preview = doc_preview(root, p)
            if preview:
                doc_previews[p] = preview
        payload["untracked_doc_previews"] = doc_previews

        payload["prior_release_style"] = prior_release_style(root)

    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
