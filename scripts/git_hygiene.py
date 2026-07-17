#!/usr/bin/env python3
"""DOS git-hygiene reporter — advisory "is this tree clean?" nudge for a Stop hook.

Wired as a **second, advisory `Stop` hook** in `.claude/settings.json` (alongside
the trajectory audit). When a Claude Code session finishes a turn, this prints a
one-line reminder if the working tree carries uncommitted work, local stash
entries, or commits ahead of its upstream — so a session never quietly ends
leaving work hidden or unshipped. It is the lightweight, *always-on* complement to
the deliberate `/release` skill: `/release` cuts a version when you ask; this just
keeps the tree's state **visible** at every stopping point so "commit / `/release`
the work" is the default reflex, not an afterthought.

> **Advisory, never a gate — by construction (the docs/274 rule).** A `Stop`-hook
> that *blocks* (`{"decision":"block"}`) FORCES the agent to keep working, and a
> bare Stop fires on EVERY finished turn — so a blocking hygiene check would
> force-loop ordinary interactive sessions (the exact scar docs/274 records: a
> marker budget on a bare Stop held 44 sessions open). This reporter therefore
> **only ever reports** — it prints to stderr and **always exits 0** in its default
> mode. It never blocks a turn, never commits, never pushes, never `rm`s anything.
> The act stays the operator's; the reporter only makes the work visible. The one
> non-zero exit is the explicit, opt-in `--strict` mode (or `DOS_GIT_HYGIENE=strict`
> env), intended for a *headless loop* that WANTS to act on dirty or hidden work —
> never the interactive default.

> **Lease-aware (the DOS-on-DOS dogfood).** This tree is multi-session-hot — several
> agents write it at once, and a live `/dispatch-loop` legitimately holds dirty paths
> mid-flight under a lane lease. Nagging about those would be noise. So the reporter
> folds DOS's OWN kernel WAL (`dos.lane_journal.read_all → replay`, the same fold
> `dos top` and `/release` Step 1.6 read) and **splits** the dirty set: paths a live
> (non-stale) lease owns are reported as *lease-held (in-flight)*, the rest as
> *stranded (commit me)*. A lease past its TTL heartbeat is treated as dead — its
> region counts as stranded (the same stale-steal rule the arbiter applies). The
> normal nudge fires on the **stranded** count, not the raw dirty count. The
> exception is the hot-tree loss class: durable untracked files are warned when any
> live lease exists, even if a lease owns their path, because a sibling tree move can
> delete never-staged files before git has an index, stash, commit, or blob for them.

> **Stash-aware (issue #208).** A hot-tree `git stash pop` is not a safe probe:
> a kept-entry partial apply can leave contended files at HEAD while the stash
> still holds the only copy, and a later `git stash drop` deletes those bytes.
> The reporter cannot prove WHY a stash entry exists from local refs alone, so it
> reports any stash entries and points the operator to a throwaway worktree or
> copy-aside instead of pop/drop in the shared tree.

> **Stage-aware (issue #207).** A narrow pathspec is still file-scoped: `git add
> path` stages every hunk in that file, including a sibling's pre-existing
> worktree edits. For shared tracked files, capture a session-start snapshot and
> check the staged hunks against it before committing:
> `python scripts/git_hygiene.py --write-stage-snapshot .git/dos-stage-snapshot
> path` before editing, then `python scripts/git_hygiene.py
> --check-stage-snapshot .git/dos-stage-snapshot path` after staging. The check
> compares `HEAD -> index` hunks with `snapshot -> worktree` hunks and exits 1 if
> the index contains hunks that were not authored after the snapshot.

This is dev / workflow tooling — it operates ON the package (reads its journal via
the public `dos` API) but is never imported BY it (the `dos.*` modules import
nothing under `scripts/`). Degradation is total: any failure folding the journal
(no journal, torn journal, `dos` not importable from this checkout) yields an empty
live-lease set, so the reporter still runs — it just can't subtract in-flight leases
and treats every dirty path as stranded (the conservative, nag-MORE direction). It
never raises out of `main`; a hard failure still exits 0 so it can never wedge the
Stop hook.

Usage::

    python scripts/git_hygiene.py --workspace .            # one-line stderr nudge, exit 0
    python scripts/git_hygiene.py --workspace . --json      # machine-readable report
    python scripts/git_hygiene.py --workspace . --strict     # exit 1 if stranded work (loops only)
    python scripts/git_hygiene.py --workspace . --write-stage-snapshot .git/dos-stage-snapshot path...
    python scripts/git_hygiene.py --workspace . --check-stage-snapshot .git/dos-stage-snapshot path...
"""

from __future__ import annotations

import argparse
import collections
import difflib
import json
import os
import subprocess
import sys
from pathlib import Path

# Mirror of dos.lane_lease.DEFAULT_TTL_SECONDS — the heartbeat staleness window.
# Duplicated as a literal (not imported) so the staleness read still works when a
# lease record omits an explicit ttl and `dos` is unimportable; the import path
# stays optional (this whole script must degrade to "no leases", never crash).
_DEFAULT_LEASE_TTL_SECONDS = 300

# Scratch conventions — a mirror of `.gitignore` + the `/release` skill's scratch
# bucket (`release-runbook.md` "Untracked-file classifier"). An untracked path
# matching one of these is short-lived probe output the release flow would `rm -f`,
# so the hygiene nudge labels it "scratch (deletable)" rather than "commit me" —
# it is noise to nag about as if it were durable work.
_SCRATCH_SUFFIXES = (".err", ".html", ".zip", ".exe", ".log", ".tmp")
_SCRATCH_DIR_HINTS = ("_scratch/", ".dos-workspace/", ".pytest_cache/", ".ruff_cache/")


def repo_root(workspace: str | None) -> Path:
    """Resolve the workspace root: explicit arg › git top-level › cwd.

    Anchors on the git top-level (like `release_context.py`) so the journal path
    the reporter reads can't drift from cwd when the hook fires from a subdir.
    """
    if workspace:
        p = Path(workspace).expanduser()
        if p.is_dir():
            return p.resolve()
    try:
        top = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        top = ""
    return Path(top).resolve() if top else Path.cwd()


def _git(args: list[str], *, root: Path) -> tuple[int, str]:
    """Run a git subcommand under `root`; return (returncode, stdout). Never raises."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True,
            encoding="utf-8", check=False,
        )
        return proc.returncode, proc.stdout
    except (FileNotFoundError, OSError):
        return 127, ""


def _repo_rel(root: Path, path: str) -> str:
    """Normalize `path` to a POSIX repo-relative path under `root`."""
    raw = Path(path).expanduser()
    candidate = raw if raw.is_absolute() else root / raw
    try:
        rel = candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"path is outside workspace: {path}") from exc
    return rel.as_posix()


def _snapshot_root(root: Path, snapshot_dir: str) -> Path:
    """Resolve the stage-snapshot directory without requiring it to exist yet."""
    raw = Path(snapshot_dir).expanduser()
    return (raw if raw.is_absolute() else root / raw).resolve()


def _read_lines_lossy(path: Path) -> list[str]:
    """Read a text file into newline-preserving lines; missing files are empty."""
    if not path.exists():
        return []
    data = path.read_bytes()
    return data.decode("utf-8", errors="surrogateescape").splitlines(keepends=True)


def _git_blob_lines(root: Path, spec: str) -> list[str]:
    """Read a git blob (`HEAD:path` or `:path`) as newline-preserving lines."""
    code, out = _git(["show", spec], root=root)
    if code != 0:
        return []
    return out.splitlines(keepends=True)


def _hunk_signatures(
    old_lines: list[str], new_lines: list[str]
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Return content signatures for hunks between two line lists.

    The signature intentionally ignores line numbers: a sibling's pre-existing
    hunk can shift later line numbers, but the current session's changed content
    should still match between `snapshot -> worktree` and `HEAD -> index`.
    """
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    signatures: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for group in matcher.get_grouped_opcodes(0):
        removed: list[str] = []
        added: list[str] = []
        for tag, i1, i2, j1, j2 in group:
            if tag in {"replace", "delete"}:
                removed.extend(line.rstrip("\r\n") for line in old_lines[i1:i2])
            if tag in {"replace", "insert"}:
                added.extend(line.rstrip("\r\n") for line in new_lines[j1:j2])
        if removed or added:
            signatures.append((tuple(removed), tuple(added)))
    return signatures


def write_stage_snapshot(root: Path, snapshot_dir: str, paths: list[str]) -> dict:
    """Copy the current worktree bytes for `paths` into a session snapshot dir."""
    if not paths:
        raise ValueError("--write-stage-snapshot requires at least one path")
    snap_root = _snapshot_root(root, snapshot_dir)
    snap_root.mkdir(parents=True, exist_ok=True)
    captured: list[str] = []
    missing: list[str] = []
    for raw in paths:
        rel = _repo_rel(root, raw)
        src = root / rel
        dst = snap_root / rel
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            captured.append(rel)
        else:
            missing.append(rel)
    manifest = {
        "schema": 1,
        "workspace": str(root),
        "snapshot": str(snap_root),
        "captured": sorted(captured),
        "missing": sorted(missing),
    }
    (snap_root / "_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _staged_paths(root: Path) -> list[str]:
    """Return staged paths when the caller omits an explicit path list."""
    code, out = _git(["diff", "--cached", "--name-only"], root=root)
    if code != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def check_stage_snapshot(root: Path, snapshot_dir: str, paths: list[str]) -> dict:
    """Compare staged hunks against hunks authored since a session snapshot."""
    snap_root = _snapshot_root(root, snapshot_dir)
    rels = [_repo_rel(root, p) for p in paths] if paths else _staged_paths(root)
    unsafe: list[dict] = []
    ok: list[str] = []
    missing_snapshot: list[str] = []
    for rel in rels:
        snap = snap_root / rel
        if not snap.exists():
            missing_snapshot.append(rel)
            continue
        staged = _hunk_signatures(
            _git_blob_lines(root, f"HEAD:{rel}"),
            _git_blob_lines(root, f":{rel}"),
        )
        session = _hunk_signatures(_read_lines_lossy(snap), _read_lines_lossy(root / rel))
        allowed = collections.Counter(session)
        unexpected = []
        for sig in staged:
            if allowed[sig]:
                allowed[sig] -= 1
            else:
                unexpected.append(sig)
        if unexpected:
            unsafe.append(
                {
                    "path": rel,
                    "unexpected_hunks": len(unexpected),
                    "staged_hunks": len(staged),
                    "session_hunks": len(session),
                }
            )
        else:
            ok.append(rel)
    return {
        "workspace": str(root),
        "snapshot": str(snap_root),
        "checked": sorted(rels),
        "ok": sorted(ok),
        "unsafe": unsafe,
        "missing_snapshot": sorted(missing_snapshot),
        "clean": not unsafe and not missing_snapshot,
    }


def render_stage_check(rep: dict) -> str:
    """Human line for the stage-snapshot guard."""
    if rep["clean"]:
        n = len(rep.get("checked") or [])
        return f"DOS safe-stage: staged hunks match the session snapshot for {n} file{'s' if n != 1 else ''}."
    bits: list[str] = []
    for item in rep.get("unsafe") or []:
        bits.append(
            f"{item['path']} has {item['unexpected_hunks']} staged hunk"
            f"{'s' if item['unexpected_hunks'] != 1 else ''} absent from the session snapshot"
        )
    missing = rep.get("missing_snapshot") or []
    if missing:
        bits.append(f"{len(missing)} file{'s' if len(missing) != 1 else ''} missing from the snapshot")
    return (
        "DOS safe-stage: "
        + "; ".join(bits)
        + " — abort the commit, unstage the file, and stage only your session hunks."
    )


def porcelain_status(root: Path) -> list[tuple[str, str]]:
    """Parse `git status --porcelain` → [(xy, path)]. Empty on any failure.

    `xy` is the two-char status code (` M`, `??`, `MM`, …); `path` is the worktree
    path (the post-`->` target for a rename). Read-only.

    Uses `-uall` (`--untracked-files=all`) so an all-untracked directory is listed
    file-by-file rather than collapsed to `dir/` — per-file granularity is what
    lets a scratch probe (`scripts/_probe.py`) and a lease-owned path
    (`benchmark/run.py`) be classified individually instead of as a whole dir.
    """
    code, out = _git(["status", "--porcelain", "-uall"], root=root)
    if code != 0:
        return []
    rows: list[tuple[str, str]] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        path = line[3:]
        # A rename/copy shows "old -> new"; the worktree path is the new one.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        rows.append((xy, path.strip().strip('"')))
    return rows


def current_branch(root: Path) -> str | None:
    """The current branch name, or None (detached / no repo)."""
    code, out = _git(["rev-parse", "--abbrev-ref", "HEAD"], root=root)
    name = out.strip()
    if code != 0 or not name or name == "HEAD":
        return None
    return name


def ahead_behind(root: Path) -> tuple[int, int] | None:
    """(ahead, behind) vs the branch's upstream, or None if no upstream is set.

    `ahead` = local commits not pushed; `behind` = upstream commits not pulled.
    A non-zero `ahead` on the trunk is the "work committed but not pushed" signal.
    """
    code, out = _git(
        ["rev-list", "--left-right", "--count", "@{upstream}...HEAD"], root=root
    )
    if code != 0:
        return None
    parts = out.split()
    if len(parts) != 2:
        return None
    try:
        behind, ahead = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return ahead, behind


def stash_entries(root: Path) -> list[str]:
    """Return the local stash rows, newest first. Empty on any git failure.

    Read-only and intentionally coarse: git does not record "this entry was kept
    after a failed pop" in a portable, cheap shape. A stash entry in this shared
    checkout is hidden work, so the Stop hook reports it and lets the operator
    inspect or copy it aside.
    """
    code, out = _git(["stash", "list", "--format=%gd%x09%s"], root=root)
    if code != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


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
    return (now - parsed).total_seconds()


def live_lease_trees(root: Path) -> list[list[str]]:
    """Fold the lane-journal WAL → the glob-region list of each LIVE (non-stale) lease.

    The same fold `release_context.compute_active_leases` and `dos top` use, reduced
    to just what the hygiene reporter needs: the `tree` globs of every lease whose
    heartbeat is still within TTL. A stale lease (heartbeat past TTL) is dropped — a
    dead loop's region is fair game, so its dirty paths count as stranded. Returns []
    on ANY failure (no journal / torn journal / `dos` unimportable) — the conservative
    nag-MORE direction.
    """
    try:
        import datetime as _dt
        from dos import config as _cfg
        from dos import lane_journal as _lj
    except Exception:
        return []
    try:
        cfg = _cfg.load_workspace_config(root)
        entries = _lj.read_all(cfg.paths.lane_journal)
        leases = _lj.replay(entries)
    except Exception:
        return []

    now = _dt.datetime.now(_dt.timezone.utc)
    trees: list[list[str]] = []
    for lease in leases:
        if not isinstance(lease, dict):
            continue
        ttl_min = lease.get("ttl_minutes")
        try:
            ttl_s = float(ttl_min) * 60.0 if ttl_min is not None else float(_DEFAULT_LEASE_TTL_SECONDS)
        except (TypeError, ValueError):
            ttl_s = float(_DEFAULT_LEASE_TTL_SECONDS)
        hb_age = _lease_age_seconds(
            lease.get("heartbeat_at") or lease.get("acquired_at"), now
        )
        # Live iff NO credible stamp (treat as live — defer/skip MORE) OR within TTL.
        stale = hb_age is not None and hb_age > ttl_s
        if stale:
            continue
        tree = lease.get("tree")
        if isinstance(tree, list) and tree:
            trees.append([str(t) for t in tree])
    return trees


def _matches_glob(path: str, glob: str) -> bool:
    """Does `path` fall under a lease's glob-region entry?

    The same coarse shape `/release` Step 1.6 documents and the arbiter uses: an
    exact path, a `dir/` prefix, a `dir/**` / `dir/*` subtree, or a `*_suffix.py`
    tail. Deliberately permissive (a false "lease-held" only SILENCES a nag, never
    suppresses an action — the reporter takes none).
    """
    g = glob.strip()
    if not g:
        return False
    # Normalize separators so a Windows worktree path matches a posix-style glob.
    p = path.replace("\\", "/")
    g = g.replace("\\", "/")
    if g == p:
        return True
    # `dir/**` or `dir/*` or `dir/` → prefix match on the directory.
    for suffix in ("/**", "/*", "/"):
        if g.endswith(suffix):
            base = g[: -len(suffix)]
            if base and (p == base or p.startswith(base + "/")):
                return True
    # `**/*.py` / `*_suffix.py` → match on the tail token.
    if g.startswith("*") and p.endswith(g.lstrip("*")):
        return True
    # bare `dir` (no trailing slash/glob) → treat as a prefix too.
    if "/" not in g and (p == g or p.startswith(g + "/")):
        return True
    return False


def _is_scratch(xy: str, path: str) -> bool:
    """True if an UNTRACKED path is short-lived scratch (the `/release` rm-f bucket)."""
    if xy != "??":
        return False
    p = path.replace("\\", "/")
    if any(hint in p for hint in _SCRATCH_DIR_HINTS):
        return True
    if any(p.endswith(suf) for suf in _SCRATCH_SUFFIXES):
        return True
    # scripts/_probe.py — a leading-underscore script is a scratch probe.
    base = p.rsplit("/", 1)[-1]
    if p.startswith("scripts/") and base.startswith("_"):
        return True
    return False


def build_report(root: Path) -> dict:
    """Assemble the hygiene report — pure data, no I/O side effects beyond reads."""
    rows = porcelain_status(root)
    lease_trees = live_lease_trees(root)

    def _lease_held(path: str) -> bool:
        return any(_matches_glob(path, g) for tree in lease_trees for g in tree)

    stranded: list[str] = []      # dirty + durable + NOT under a live lease → "commit me"
    lease_held: list[str] = []    # dirty but a live loop owns it → in-flight
    scratch: list[str] = []       # untracked short-lived probe output → deletable noise
    durable_untracked: list[str] = []  # non-scratch ?? paths; unrecoverable if deleted
    for xy, path in rows:
        if _is_scratch(xy, path):
            scratch.append(path)
        else:
            if xy == "??":
                durable_untracked.append(path)
            if _lease_held(path):
                lease_held.append(path)
            else:
                stranded.append(path)

    branch = current_branch(root)
    ab = ahead_behind(root)
    ahead, behind = (ab if ab is not None else (None, None))
    stashes = stash_entries(root)

    hot_untracked_at_risk = bool(lease_trees and durable_untracked)

    return {
        "workspace": str(root),
        "branch": branch,
        "dirty_total": len(rows),
        "stranded": sorted(stranded),
        "lease_held": sorted(lease_held),
        "scratch": sorted(scratch),
        "durable_untracked": sorted(durable_untracked),
        "hot_untracked_at_risk": hot_untracked_at_risk,
        "stash_entries": stashes,
        "stash_count": len(stashes),
        "ahead": ahead,            # local commits not pushed (None = no upstream)
        "behind": behind,
        "live_leases": len(lease_trees),
        # The nudge fires iff there is stranded work, unpushed commits, OR durable
        # untracked work exposed in a hot tree, OR hidden stash work. Scratch never
        # trips it.
        "clean": (
            (not stranded) and (not ahead) and (not hot_untracked_at_risk)
            and (not stashes)
        ),
    }


def render_nudge(rep: dict) -> str:
    """One human line for the Stop hook's stderr (empty string when clean)."""
    if rep["clean"]:
        return ""
    bits: list[str] = []
    n = len(rep["stranded"])
    if n:
        on = f" on {rep['branch']}" if rep["branch"] else ""
        bits.append(f"{n} uncommitted file{'s' if n != 1 else ''}{on}")
    if rep["ahead"]:
        a = rep["ahead"]
        bits.append(f"{a} commit{'s' if a != 1 else ''} not pushed")
    risk_n = len(rep.get("durable_untracked") or []) if rep.get("hot_untracked_at_risk") else 0
    if risk_n:
        leases = rep.get("live_leases") or 0
        lease_noun = "lease" if leases == 1 else "leases"
        lease_verb = "is" if leases == 1 else "are"
        bits.append(
            f"{risk_n} untracked file{'s' if risk_n != 1 else ''} at risk "
            f"while {leases} live {lease_noun} {lease_verb} present"
        )
    stash_n = int(rep.get("stash_count") or 0)
    if stash_n:
        bits.append(f"{stash_n} git stash entr{'y' if stash_n == 1 else 'ies'} present")
    held = len(rep["lease_held"])
    tail = f" ({held} more held by a live loop — left for it)" if held else ""
    actions: list[str] = []
    if risk_n:
        actions.append("commit within minutes or use a detached git worktree off origin/master")
    elif n:
        actions.append("commit the lane or run /release")
    elif rep["ahead"]:
        actions.append("git push origin master (or /release)")
    if stash_n:
        actions.append(
            "move stash bytes to a throwaway worktree or copy-aside; "
            "do not git stash pop/drop on a hot tree"
        )
    action = "; ".join(actions)
    return f"DOS git-hygiene: {', '.join(bits)}{tail} — {action}."


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--workspace", default=".", help="workspace root (default: cwd / git top-level)")
    p.add_argument("--json", action="store_true", help="emit the full report as JSON")
    p.add_argument("paths", nargs="*", help="repo paths for the stage-snapshot modes")
    p.add_argument(
        "--write-stage-snapshot",
        metavar="DIR",
        help="copy current worktree bytes for paths into DIR, before editing",
    )
    p.add_argument(
        "--check-stage-snapshot",
        metavar="DIR",
        help="fail if staged hunks are absent from DIR's session-start snapshots",
    )
    p.add_argument(
        "--strict", action="store_true",
        help="exit 1 when stranded work, stash entries, or unpushed commits exist "
             "(loops only; "
             "the interactive default is advisory exit-0). Also enabled by "
             "DOS_GIT_HYGIENE=strict.",
    )
    # Wrap the whole body so a hard failure still exits 0 in the default mode —
    # the reporter must never wedge the Stop hook it is wired into.
    try:
        args = p.parse_args(argv)
        root = repo_root(args.workspace)
        if args.write_stage_snapshot and args.check_stage_snapshot:
            p.error("--write-stage-snapshot and --check-stage-snapshot are exclusive")
        if args.paths and not (args.write_stage_snapshot or args.check_stage_snapshot):
            p.error("paths are only valid with the stage-snapshot modes")
        if args.write_stage_snapshot:
            rep = write_stage_snapshot(root, args.write_stage_snapshot, args.paths)
            if args.json:
                sys.stdout.write(json.dumps(rep, indent=2) + "\n")
            else:
                sys.stdout.write(
                    "DOS safe-stage: captured "
                    f"{len(rep['captured'])} file{'s' if len(rep['captured']) != 1 else ''} "
                    f"in {rep['snapshot']}.\n"
                )
            return 1 if rep["missing"] else 0
        if args.check_stage_snapshot:
            rep = check_stage_snapshot(root, args.check_stage_snapshot, args.paths)
            if args.json:
                sys.stdout.write(json.dumps(rep, indent=2) + "\n")
            else:
                line = render_stage_check(rep)
                stream = sys.stdout if rep["clean"] else sys.stderr
                stream.write(line + "\n")
            return 0 if rep["clean"] else 1
        rep = build_report(root)
    except SystemExit:
        raise  # argparse --help / bad-flag: let it through
    except Exception as exc:  # pragma: no cover - defensive belt
        if "args" in locals() and (
            getattr(args, "write_stage_snapshot", None)
            or getattr(args, "check_stage_snapshot", None)
        ):
            sys.stderr.write(f"git-hygiene: stage-snapshot failed ({type(exc).__name__}: {exc})\n")
            return 2
        sys.stderr.write(f"git-hygiene: skipped ({type(exc).__name__})\n")
        return 0

    if args.json:
        sys.stdout.write(json.dumps(rep, indent=2) + "\n")
    else:
        line = render_nudge(rep)
        if line:
            sys.stderr.write(line + "\n")

    strict = args.strict or os.environ.get("DOS_GIT_HYGIENE", "").strip().lower() == "strict"
    if strict and not rep["clean"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
