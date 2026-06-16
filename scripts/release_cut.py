#!/usr/bin/env python3
"""Cut a release mechanically — the automated `/release` Steps 3–6, no model.

The companion to `release_decide.py`: that half answers "release, at version X";
this half performs the deterministic git work the `/release` skill does by hand —
bump the version markers, resync the plugin bundle, draft the release notes, run
the tag-last pre-tag witness, and commit. It performs NO irreversible outward
step: it does not push and does not tag. Pushing master, waiting for CI to go
green on the new commit, and minting the tag are the WORKFLOW's job
(`.github/workflows/release-cadence.yml`), so the one-way steps stay inside the
gated CI context where the ci-green witness and the TestPyPI rehearsal guard
them. This script's output is the release COMMIT plus a JSON manifest naming the
commit and the version to tag.

Why a script and not the skill: the skill is interactive judgment (read the
commits, write prose notes, pick the bump). The cadence needs the mechanical
spine of it to run unattended on a cron. The judgment that CAN be mechanized
(the bump level, the themes) is `release_decide.py`'s; the prose notes here are
auto-generated from the commit subjects clustered by scope — honest and
machine-derived, not hand-crafted, which a cadence release should be.

What it does, in order (`--execute`; default is a dry-run plan):
  1. `release_bump.py <version>` — the 7-marker lockstep bump (the single source
     of truth for which files change; we read its report to build the commit
     pathspec, never guessing the file set).
  2. `build_plugin.py` — resync the generated `claude-plugin/skills/` mirror of
     the swept `src/dos/skills/` samples (the same step the skill runs after a
     bump).
  3. Draft `docs/releases/v<version>.md` if absent — front-matter
     (version/date/headline/themes/highlights) matching the shape
     `release_context.prior_release_style` parses, body clustered by scope. If a
     sibling already drafted it (a racing cadence/skill run), it is LEFT as-is
     (never clobber hand-written notes) and only added to the commit.
  4. `release_dry_run.py --json <HEAD-after-bump>` — the tag-last witness. A
     non-zero verdict ABORTS the cut (fix forward, never burn a version; the
     docs/295 rule). `--skip-dry-run` bypasses it only for tests / a CI context
     that runs the suite itself.
  5. `git commit` with an explicit PATHSPEC built from the bump report + the
     notes file (hot-tree safe — never `git add -A`; a sibling loop's edits in
     the tree are not swept in). Subject: `v<version>: <headline>`.

Output: a JSON manifest `{version, tag, commit_sha, headline, paths, dry_run,
dry_run_verdict}` on stdout. Exit 0 = cut (or dry-run plan ok); non-zero = the
cut aborted (a gate failed) — the workflow then tags nothing.

This is **dev / release tooling, not kernel** — it operates ON the package, is
never imported BY it, and reuses the existing release scripts rather than
re-deriving their behavior.

Usage:
  python scripts/release_cut.py 0.27.0                # dry-run plan (no mutation)
  python scripts/release_cut.py 0.27.0 --execute      # bump + notes + commit
  python scripts/release_cut.py 0.27.0 --execute --skip-dry-run   # CI runs the suite itself
  python scripts/release_cut.py 0.27.0 --execute --themes arbiter,docs --headline "..."
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import subprocess
import sys
from pathlib import Path

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_SCRIPTS = Path(__file__).resolve().parent


def repo_root() -> Path:
    try:
        top = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        top = ""
    return Path(top) if top else Path.cwd()


def _run(cmd: list[str], *, cwd: Path, timeout: int = 600) -> tuple[int, str]:
    """Run a command; return (exit_code, combined output). Never raises."""
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        return 124, (exc.output or "") + f"\n(timed out after {timeout}s)"
    except FileNotFoundError as exc:
        return 127, str(exc)


def _paths_from_bump_report(report: dict) -> list[str]:
    """Every repo-relative path the bump touched, from its JSON report.

    The bump report is the single source of truth for the file set — we read it
    rather than hardcoding (so a future bump target is picked up automatically).
    Each lockstep target carries a `path`; the `docs` target carries a
    `files_swept` dict (rel-path → count) and the `llms_full` target a `path`.
    Only paths whose target actually `changed` are included (a no-op target need
    not be committed, but including an unchanged path is harmless — git ignores
    it; we include changed-only to keep the pathspec tight).
    """
    paths: list[str] = []
    for key, t in (report.get("targets") or {}).items():
        if not isinstance(t, dict):
            continue
        # The doc sweep reports its file set under files_swept (path -> count).
        swept = t.get("files_swept")
        if isinstance(swept, dict):
            paths.extend(swept.keys())
        p = t.get("path")
        # `docs+skills` is a label, not a path — skip it; real paths come from
        # files_swept above.
        if isinstance(p, str) and p and p != "docs+skills":
            paths.append(p)
    # Dedup, stable order.
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        pn = p.replace("\\", "/")
        if pn not in seen:
            seen.add(pn)
            out.append(pn)
    return out


def _default_headline(version: str, level: str, themes: list[str]) -> str:
    """A machine-honest headline when none is supplied.

    Cadence notes are auto-generated; the headline names the level and the top
    themes rather than pretending to a hand-written summary.
    """
    lvl = {"major": "Major", "minor": "Minor", "patch": "Patch"}.get(level, "Release")
    if themes:
        top = ", ".join(themes[:5])
        return f"{lvl} — {top}"
    return f"{lvl} release v{version}"


def draft_notes(root: Path, version: str, *, level: str, themes: list[str],
                headline: str, commits: list[dict], today: str) -> tuple[Path, bool]:
    """Write docs/releases/v<version>.md if absent. Returns (path, wrote).

    Never clobbers an existing file (a sibling skill/cadence run may have drafted
    richer prose) — if it exists, returns (path, False). The generated body
    clusters commit subjects by their conventional-commit scope so the notes are
    derived from the actual range, not invented.
    """
    rel_dir = root / "docs" / "releases"
    path = rel_dir / f"v{version}.md"
    if path.exists():
        return path, False
    rel_dir.mkdir(parents=True, exist_ok=True)

    # Cluster subjects by scope for the body. A subject with no scope falls into
    # a generic bucket so nothing is dropped.
    cc = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?!?:\s*(?P<rest>.*)$")
    buckets: dict[str, list[str]] = {}
    order: list[str] = []
    for c in commits:
        subj = str(c.get("subject") or "").strip()
        if re.match(r"^v\d+\.\d+\.\d+:", subj):
            continue  # skip prior release commits
        m = cc.match(subj)
        scope = (m.group("scope") or "").strip() if m else ""
        key = scope or "general"
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(subj)

    # Highlights: the first subject from each of the top themes (≤6).
    highlights: list[str] = []
    for scope in themes[:6]:
        msgs = buckets.get(scope)
        if msgs:
            highlights.append(msgs[0])
    if not highlights:  # no scoped commits — take the first few subjects
        flat = [s for msgs in buckets.values() for s in msgs]
        highlights = flat[:6]

    def _yaml_str(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines: list[str] = ["---", f"version: {version}", f"date: {today}",
                        f"headline: {_yaml_str(headline)}",
                        "themes: [" + ", ".join(_yaml_str(t) for t in themes[:6]) + "]",
                        "highlights:"]
    for h in highlights:
        lines.append(f"  - {_yaml_str(h)}")
    lines.append("---")
    lines.append("")
    lines.append(f"**TL;DR** — an automatically-cut **{level}** release "
                 f"({len(commits)} commit(s) since the previous tag). The notes "
                 "below are generated from the commit subjects, grouped by scope.")
    lines.append("")
    for scope in order:
        lines.append(f"## `{scope}`")
        for subj in buckets[scope]:
            lines.append(f"- {subj}")
        lines.append("")
    # newline="" preserves LF on every platform (the release_bump.py write() rule).
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    return path, True


def _commits_since_last_tag(root: Path, limit: int) -> list[dict]:
    """[{subject}] since the most recent vX.Y.Z tag (for notes drafting)."""
    tag = ""
    code, out = _run(["git", "tag", "--sort=-v:refname"], cwd=root)
    if code == 0:
        for line in out.splitlines():
            if re.match(r"^v\d+\.\d+\.\d+$", line.strip()):
                tag = line.strip()
                break
    rev = f"{tag}..HEAD" if tag else "HEAD"
    code, out = _run(["git", "log", rev, "--pretty=format:%s", f"-n{limit}"], cwd=root)
    if code != 0:
        return []
    return [{"subject": s} for s in out.splitlines() if s.strip()]


def cut(root: Path, version: str, *, execute: bool, skip_dry_run: bool,
        level: str, themes: list[str], headline: str, today: str) -> dict:
    """Perform (or plan) the cut. Returns the manifest dict."""
    manifest: dict = {
        "version": version, "tag": f"v{version}", "level": level,
        "headline": headline, "dry_run": not execute, "commit_sha": None,
        "paths": [], "notes_file": None, "notes_written": False,
        "dry_run_verdict": None, "ok": False, "aborted": None,
    }

    # 1. Bump (the report is the file-set source of truth).
    bump_cmd = [sys.executable, str(_SCRIPTS / "release_bump.py"), version]
    if not execute:
        bump_cmd.append("--dry-run")
    code, out = _run(bump_cmd, cwd=root)
    try:
        report = json.loads(out)
    except json.JSONDecodeError:
        manifest["aborted"] = f"release_bump.py produced no JSON (exit {code}): {out.strip()[:300]}"
        return manifest
    if code != 0:
        manifest["aborted"] = (f"release_bump.py failed (exit {code}) — "
                               f"drift={report.get('drift_after_bump')}; reconcile markers first")
        manifest["bump_report"] = report
        return manifest
    paths = _paths_from_bump_report(report)

    # 2. Resync the plugin bundle (skill-mirror) — only meaningful on execute.
    if execute:
        pcode, pout = _run([sys.executable, str(_SCRIPTS / "build_plugin.py")], cwd=root)
        if pcode != 0:
            manifest["aborted"] = f"build_plugin.py failed (exit {pcode}): {pout.strip()[-300:]}"
            return manifest
        # The plugin resync touches claude-plugin/skills/** — add the dir so the
        # commit carries the regenerated mirror.
        paths.append("claude-plugin/skills")

    # 3. Draft notes (clustered from the real commit range).
    commits = _commits_since_last_tag(root, 400)
    notes_path = root / "docs" / "releases" / f"v{version}.md"
    if execute:
        notes_path, wrote = draft_notes(root, version, level=level, themes=themes,
                                        headline=headline, commits=commits, today=today)
        manifest["notes_written"] = wrote
    manifest["notes_file"] = str(notes_path.relative_to(root)).replace("\\", "/")
    paths.append(manifest["notes_file"])

    # Dedup paths.
    seen: set[str] = set()
    paths = [p for p in (q.replace("\\", "/") for q in paths) if not (p in seen or seen.add(p))]
    manifest["paths"] = paths

    if not execute:
        manifest["ok"] = True
        manifest["bump_report"] = report
        return manifest

    # 4. Tag-last witness on the bumped tree (committed-bytes isolation is what
    #    release_dry_run does; here we run against the working tree's HEAD after
    #    staging, so we stage first, then adjudicate HEAD+staged via a temp commit?
    #    No — release_dry_run adjudicates a committed ref. So: commit first, THEN
    #    adjudicate the commit; abort by resetting if it fails.) We commit, then
    #    witness, then abort-by-reset on failure so a red verdict burns nothing.
    # Stage the pathspec (never -A).
    scode, sout = _run(["git", "add", "--", *paths], cwd=root)
    if scode != 0:
        manifest["aborted"] = f"git add failed: {sout.strip()[:300]}"
        return manifest
    subject = f"v{version}: {headline}"
    ccode, cout = _run(["git", "commit", "-m", subject, "--", *paths], cwd=root)
    if ccode != 0:
        manifest["aborted"] = f"git commit failed: {cout.strip()[:300]}"
        return manifest
    code, sha = _run(["git", "rev-parse", "HEAD"], cwd=root)
    commit_sha = sha.strip()
    manifest["commit_sha"] = commit_sha

    if not skip_dry_run:
        dcode, dout = _run([sys.executable, str(_SCRIPTS / "release_dry_run.py"),
                            "--json", commit_sha], cwd=root, timeout=900)
        try:
            verdict = json.loads(dout)
        except json.JSONDecodeError:
            verdict = {"ok": False, "note": dout.strip()[-300:]}
        manifest["dry_run_verdict"] = verdict
        if not verdict.get("ok"):
            # Abort: undo the release commit so no version number is half-cut.
            _run(["git", "reset", "--soft", "HEAD~1"], cwd=root)
            manifest["commit_sha"] = None
            manifest["aborted"] = ("release_dry_run did not pass on the cut commit — "
                                   "reset; fix forward and re-run (docs/295)")
            return manifest

    manifest["ok"] = True
    return manifest


def main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("version", help="The version to cut, e.g. 0.27.0 (no leading v)")
    p.add_argument("--execute", action="store_true",
                   help="Perform the bump + notes + commit (default: dry-run plan)")
    p.add_argument("--skip-dry-run", action="store_true",
                   help="Skip the release_dry_run witness (only when CI runs the suite itself)")
    p.add_argument("--level", default="minor", choices=["patch", "minor", "major"],
                   help="The semver level (from release_decide; default minor)")
    p.add_argument("--themes", default="",
                   help="Comma-separated theme scopes for the notes front-matter")
    p.add_argument("--headline", default="",
                   help="Release-notes headline (default: auto from level + themes)")
    p.add_argument("--date", default="",
                   help="ISO date for the notes (default: today, UTC)")
    args = p.parse_args(argv)

    version = args.version.lstrip("v")
    if not SEMVER_RE.match(version):
        print(f"release-cut: {args.version!r} is not a valid semver (X.Y.Z)", file=sys.stderr)
        return 2

    themes = [t.strip() for t in args.themes.split(",") if t.strip()]
    headline = args.headline or _default_headline(version, args.level, themes)
    # Date.now is fine here (a script, not a resumable workflow); UTC for a notes stamp.
    today = args.date or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")

    root = repo_root()
    manifest = cut(root, version, execute=args.execute, skip_dry_run=args.skip_dry_run,
                   level=args.level, themes=themes, headline=headline, today=today)

    json.dump(manifest, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if manifest.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
