#!/usr/bin/env python3
"""DOS git-cleanup — adjudicate detached worktrees + merged branches for safe removal.

The reusable form of the one-time "clean up stray fleet worktrees and landed
branches" sweep. Several headless loops share this hot tree; each leaves behind a
detached `fleet-runs/wt-issue-NN` worktree once its phase lands. Hand-pruning them
is error-prone — remove the wrong one and you discard unsaved work or an unlanded
commit that lives ONLY at that worktree's HEAD (a detached HEAD's commits GC away
once the worktree is gone unless a branch holds them). This tool applies the DOS
distrust posture to that sweep: it never removes a worktree or branch on a name or
a self-reported "done" — only on **git evidence** that the work is both saved
(no uncommitted files) and landed (HEAD is an ancestor of the trunk).

> **The classifier is pure; the I/O is a thin shell.** `classify_worktree` and
> `classify_branch` decide KEEP / PRUNE / REFUSE from a plain record
> `(head_sha, landed, uncommitted_count, is_main, is_named_branch, ...)` — no git,
> no filesystem. The shell (`gather_worktrees`, `gather_branches`) runs git to build
> those records, then the classifier rules. This is what makes the safety rules
> unit-testable without a real repo: drive the pure functions with synthetic records
> and assert the verdict.

The three refusal rules (each a documented foot-gun the sweep must never fire on):

1. **Uncommitted work → REFUSE.** A worktree with any dirty/untracked file may hold
   unsaved edits (the #117 `cli.py` rescue was exactly this). Removing it discards
   them. Save to a branch first, then re-run.
2. **Unlanded HEAD → REFUSE.** If the worktree's HEAD is NOT an ancestor of the
   trunk (`git merge-base --is-ancestor <head> <trunk>`, exit 0 = landed), its
   commits exist nowhere else. Pruning a detached one loses them. Branch it first.
3. **Never force-delete a branch.** Branch removal uses `git branch -d` (merged-only)
   — never `-D`. If git refuses `-d` the branch is not actually merged; that is the
   built-in safety check, surfaced not bypassed.

The main tree, the gh-pages publish tree, and any worktree on a NAMED branch are
always KEEP (a named branch is a durable handle a human chose; the tool won't
second-guess it even when landed). Only a LANDED + clean + detached worktree, or a
LANDED local branch, is ever a PRUNE candidate.

**Dry-run is the default.** Without `--apply` the tool only prints the verdict table
(or `--json`) — it runs no destructive git. `--apply` performs the `git worktree
remove` / `git worktree prune` / `git branch -d` for the PRUNE rows only; REFUSE and
KEEP rows are never touched. This is dev / workflow tooling: it operates ON the repo
but is never imported BY the `dos` package.

Usage::

    python scripts/git_cleanup.py --workspace .            # dry-run table, exit 0
    python scripts/git_cleanup.py --workspace . --json      # machine-readable plan
    python scripts/git_cleanup.py --workspace . --apply      # actually prune the PRUNE rows
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The trunk every "is this landed?" check resolves against. The remote-tracking ref
# is the source of truth for "merged into the published line" — a local `master` can
# be ahead of what actually shipped. Falls back to local `master` if the remote ref
# is absent (a fresh clone with no fetch).
_TRUNK_CANDIDATES = ("origin/master", "master", "origin/main", "main")

# Verdict vocabulary — a closed set, mirrored in the tests. KEEP: leave it be.
# PRUNE: a removal candidate (acted on only under --apply). REFUSE: a removal was
# considered but git evidence forbids it (dirty or unlanded) — surfaced, never hidden.
KEEP = "KEEP"
PRUNE = "PRUNE"
REFUSE = "REFUSE"


@dataclass(frozen=True)
class WorktreeRecord:
    """One row of `git worktree list --porcelain`, plus the derived facts the
    classifier needs. Built by the I/O shell; the pure classifier reads only this."""

    path: str
    head_sha: str
    branch: str | None          # the branch name, or None for a detached HEAD
    is_main: bool               # the primary worktree (never a prune candidate)
    landed: bool                # HEAD is an ancestor of the trunk
    uncommitted_count: int      # dirty + untracked files in this worktree


@dataclass(frozen=True)
class BranchRecord:
    """One local branch, plus the derived facts the classifier needs."""

    name: str
    head_sha: str
    is_current: bool            # the checked-out branch (never delete the current one)
    landed: bool                # HEAD is an ancestor of the trunk
    is_protected: bool          # a branch the tool refuses to delete on principle


@dataclass
class Verdict:
    """A classifier result: the verdict, a one-line reason, and the action to run
    under --apply (empty for KEEP / REFUSE)."""

    target: str                 # the worktree path or branch name
    kind: str                   # "worktree" | "branch"
    verdict: str                # KEEP | PRUNE | REFUSE
    reason: str
    action: list[str] = field(default_factory=list)  # the git argv for --apply


# Branches the tool will never delete even if they look landed — the trunk handles
# and the published-site branch. A human-named handle is durable by intent.
_PROTECTED_BRANCHES = frozenset({"master", "main", "gh-pages", "HEAD"})


# --------------------------------------------------------------------------- #
# The pure classifiers — no git, no filesystem. Unit-testable on synthetic data.
# --------------------------------------------------------------------------- #

def classify_worktree(rec: WorktreeRecord) -> Verdict:
    """Decide KEEP / PRUNE / REFUSE for one worktree from its record alone.

    Order matters: the main tree and named-branch trees are KEEP before any
    removal is considered; among removal candidates, a dirty tree REFUSEs before
    an unlanded one (both refuse, but the dirty reason is the more urgent fix).
    """
    if rec.is_main:
        return Verdict(rec.path, "worktree", KEEP, "primary worktree — never pruned")

    # A worktree checked out on a NAMED branch is a durable handle; leave it to the
    # branch sweep / a human. Only detached (fleet-run) worktrees are prune fodder.
    if rec.branch is not None:
        return Verdict(
            rec.path, "worktree", KEEP,
            f"on named branch '{rec.branch}' — durable handle, left for the branch sweep",
        )

    # Rule 1 — uncommitted work always refuses (highest-priority fix).
    if rec.uncommitted_count > 0:
        return Verdict(
            rec.path, "worktree", REFUSE,
            f"{rec.uncommitted_count} uncommitted file(s) — save to a branch first, then re-run",
        )

    # Rule 2 — an unlanded detached HEAD's commits live nowhere else.
    if not rec.landed:
        return Verdict(
            rec.path, "worktree", REFUSE,
            "HEAD not an ancestor of the trunk — branch it before removing (commits would be lost)",
        )

    # Landed + clean + detached → the only PRUNE shape.
    return Verdict(
        rec.path, "worktree", PRUNE,
        f"detached HEAD {rec.head_sha[:9]} landed in the trunk and clean — safe to remove",
        action=["worktree", "remove", rec.path],
    )


def classify_branch(rec: BranchRecord) -> Verdict:
    """Decide KEEP / PRUNE / REFUSE for one local branch from its record alone."""
    if rec.is_protected:
        return Verdict(rec.name, "branch", KEEP, "protected branch — never deleted")

    if rec.is_current:
        return Verdict(rec.name, "branch", KEEP, "the checked-out branch — never deleted")

    # An unlanded branch is a named handle on un-shipped work — keep it (the operator
    # rule: only delete proven-landed). This is a KEEP, not a REFUSE: nothing was
    # asked to be removed, the branch is simply not a candidate.
    if not rec.landed:
        return Verdict(rec.name, "branch", KEEP, "not merged into the trunk — kept (only proven-landed branches prune)")

    # Landed → a PRUNE candidate. The action uses -d (merged-only); git itself is the
    # final gate — if it refuses, the branch was not really merged and we surface that.
    return Verdict(
        rec.name, "branch", PRUNE,
        f"merged into the trunk ({rec.head_sha[:9]}) — safe to delete (git -d gates)",
        action=["branch", "-d", rec.name],
    )


# --------------------------------------------------------------------------- #
# The I/O shell — runs git to build records, then applies the pure classifier.
# --------------------------------------------------------------------------- #

def _git(args: list[str], *, root: Path) -> tuple[int, str]:
    """Run a git subcommand under `root`; return (returncode, stdout). Never raises."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
        return proc.returncode, proc.stdout
    except (FileNotFoundError, OSError):
        return 127, ""


def repo_root(workspace: str | None) -> Path:
    """Resolve the workspace root: explicit dir › `dos` config root › git top-level › cwd."""
    if workspace:
        p = Path(workspace).expanduser()
        if p.is_dir():
            # Prefer the dos-config root when the package is importable — keeps the
            # tool's notion of "the workspace" identical to every other dos verb.
            try:
                from dos import config as _cfg  # type: ignore
                return _cfg.load_workspace_config(p, gather_env=False).paths.root.resolve()
            except Exception:
                return p.resolve()
    code, out = _git(["rev-parse", "--show-toplevel"], root=Path.cwd())
    top = out.strip()
    return Path(top).resolve() if code == 0 and top else Path.cwd()


def resolve_trunk(root: Path) -> str:
    """The first existing trunk ref among the candidates (origin/master first)."""
    for ref in _TRUNK_CANDIDATES:
        code, _ = _git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], root=root)
        if code == 0:
            return ref
    return "HEAD"  # degenerate fallback; everything will read as "landed" vs itself


def _is_ancestor(sha: str, trunk: str, *, root: Path) -> bool:
    """True iff `sha` is an ancestor of `trunk` (git merge-base --is-ancestor, exit 0)."""
    if not sha:
        return False
    code, _ = _git(["merge-base", "--is-ancestor", sha, trunk], root=root)
    return code == 0


def _worktree_dirty_count(wt_path: str) -> int:
    """Count dirty + untracked files in a worktree via its own `git status`.

    Runs `git -C <wt_path> status --porcelain` so the count reflects THAT tree, not
    the primary one. A missing directory (a stale admin entry) reads as 0 dirty — it
    has no files to lose; `git worktree remove` / `prune` handles the stale entry.
    """
    if not Path(wt_path).is_dir():
        return 0
    code, out = _git(["status", "--porcelain", "-uall"], root=Path(wt_path))
    if code != 0:
        return 0
    return sum(1 for line in out.splitlines() if line.strip())


def gather_worktrees(root: Path, trunk: str) -> list[WorktreeRecord]:
    """Parse `git worktree list --porcelain` into classifier records (runs git)."""
    code, out = _git(["worktree", "list", "--porcelain"], root=root)
    if code != 0:
        return []
    records: list[WorktreeRecord] = []
    # Porcelain blocks are separated by blank lines; first block is the main tree.
    blocks = out.split("\n\n")
    main_path = None
    for idx, block in enumerate(blocks):
        path = head = branch = None
        detached = False
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = line[len("worktree "):].strip()
            elif line.startswith("HEAD "):
                head = line[len("HEAD "):].strip()
            elif line.startswith("branch "):
                ref = line[len("branch "):].strip()
                branch = ref.rsplit("/", 1)[-1] if ref.startswith("refs/heads/") else ref
            elif line.strip() == "detached":
                detached = True
        if not path:
            continue
        if main_path is None:
            main_path = path
        is_main = path == main_path
        rec = WorktreeRecord(
            path=path,
            head_sha=head or "",
            branch=None if detached else branch,
            is_main=is_main,
            landed=_is_ancestor(head or "", trunk, root=root),
            uncommitted_count=0 if is_main else _worktree_dirty_count(path),
        )
        records.append(rec)
    return records


def gather_branches(root: Path, trunk: str) -> list[BranchRecord]:
    """Parse `git for-each-ref refs/heads` into classifier records (runs git)."""
    code, out = _git(
        ["for-each-ref", "--format=%(refname:short)%09%(objectname)%09%(HEAD)", "refs/heads"],
        root=root,
    )
    if code != 0:
        return []
    records: list[BranchRecord] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, sha, head_marker = parts[0], parts[1], parts[2]
        records.append(BranchRecord(
            name=name,
            head_sha=sha,
            is_current=(head_marker.strip() == "*"),
            landed=_is_ancestor(sha, trunk, root=root),
            is_protected=name in _PROTECTED_BRANCHES,
        ))
    return records


def build_plan(root: Path) -> dict:
    """Run git, classify every worktree + branch, return the full plan as plain data."""
    trunk = resolve_trunk(root)
    wt_records = gather_worktrees(root, trunk)
    br_records = gather_branches(root, trunk)
    verdicts = [classify_worktree(r) for r in wt_records] + [classify_branch(r) for r in br_records]
    return {
        "workspace": str(root),
        "trunk": trunk,
        "verdicts": [
            {"target": v.target, "kind": v.kind, "verdict": v.verdict,
             "reason": v.reason, "action": v.action}
            for v in verdicts
        ],
        "counts": {
            KEEP: sum(1 for v in verdicts if v.verdict == KEEP),
            PRUNE: sum(1 for v in verdicts if v.verdict == PRUNE),
            REFUSE: sum(1 for v in verdicts if v.verdict == REFUSE),
        },
    }


def render_table(plan: dict) -> str:
    """A human-readable verdict table for the dry-run default."""
    lines = [
        f"DOS git-cleanup — workspace {plan['workspace']} (trunk: {plan['trunk']})",
        "",
    ]
    order = {REFUSE: 0, PRUNE: 1, KEEP: 2}
    rows = sorted(plan["verdicts"], key=lambda v: (order.get(v["verdict"], 9), v["kind"], v["target"]))
    for v in rows:
        tag = {KEEP: "KEEP  ", PRUNE: "PRUNE ", REFUSE: "REFUSE"}[v["verdict"]]
        lines.append(f"  {tag} [{v['kind']:8}] {v['target']}")
        lines.append(f"         └─ {v['reason']}")
    c = plan["counts"]
    lines.append("")
    lines.append(f"  {c[PRUNE]} PRUNE · {c[REFUSE]} REFUSE · {c[KEEP]} KEEP")
    if c[PRUNE]:
        lines.append("  (dry-run — re-run with --apply to remove the PRUNE rows)")
    return "\n".join(lines)


def apply_plan(plan: dict, root: Path) -> list[dict]:
    """Run the PRUNE actions (worktree remove / branch -d), then `git worktree prune`.

    Only PRUNE rows act; KEEP and REFUSE are never touched. Each action's git
    returncode is captured — a non-zero (e.g. `git branch -d` refusing an un-merged
    branch) is reported as a failed step, not raised. Returns one result row per
    attempted action."""
    results: list[dict] = []
    for v in plan["verdicts"]:
        if v["verdict"] != PRUNE or not v["action"]:
            continue
        code, out = _git(v["action"], root=root)
        results.append({
            "target": v["target"], "kind": v["kind"],
            "argv": v["action"], "returncode": code, "ok": code == 0,
            "output": out.strip(),
        })
    # Clear any stale admin entries left by a removed/vanished worktree directory.
    prune_code, prune_out = _git(["worktree", "prune", "-v"], root=root)
    results.append({
        "target": "(worktree prune)", "kind": "worktree",
        "argv": ["worktree", "prune", "-v"], "returncode": prune_code,
        "ok": prune_code == 0, "output": prune_out.strip(),
    })
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--workspace", default=".", help="workspace root (default: cwd / git top-level)")
    p.add_argument("--json", action="store_true", help="emit the plan (and apply results) as JSON")
    p.add_argument(
        "--apply", action="store_true",
        help="run the PRUNE actions (git worktree remove / branch -d / worktree prune); "
             "default is a read-only dry-run",
    )
    args = p.parse_args(argv)
    root = repo_root(args.workspace)
    plan = build_plan(root)

    applied: list[dict] | None = None
    if args.apply:
        applied = apply_plan(plan, root)

    if args.json:
        out = dict(plan)
        if applied is not None:
            out["applied"] = applied
        sys.stdout.write(json.dumps(out, indent=2) + "\n")
    else:
        sys.stdout.write(render_table(plan) + "\n")
        if applied is not None:
            sys.stdout.write("\n  applied:\n")
            for r in applied:
                mark = "ok " if r["ok"] else "FAIL"
                sys.stdout.write(f"    [{mark}] {' '.join(r['argv'])}")
                if r["output"]:
                    sys.stdout.write(f"  — {r['output'].splitlines()[0]}")
                sys.stdout.write("\n")

    # A failed --apply step (e.g. git -d refusing an un-merged branch) is a non-zero
    # exit so a calling loop notices; the dry-run default is always exit 0.
    if applied is not None and any(not r["ok"] for r in applied):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
