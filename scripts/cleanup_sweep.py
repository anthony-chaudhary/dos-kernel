#!/usr/bin/env python3
"""DOS cleanup-sweep — run every safe housekeeping chore in one pass, for a cron.

The single entry point a scheduled job (a session cron, a GitHub Action, a `dos
pulse` sidecar) runs on a cadence to keep a hot DOS workspace tidy without a human
typing each chore. It does NOT invent new cleanup logic — it FOLDS the chores that
already exist as standalone verbs/scripts, each of which already carries its own
safety rules, into one fail-soft sweep:

    1. `dos reap --journal [--apply]`  — bound `.dos/` scratch (audits/verdicts/runs)
       to the workspace `[retention]` caps AND compact the lane-journal WAL. The WAL
       compaction is provably safe: `replay(compact(E)) == replay(E)`, live leases
       always survive. The scratch reap keeps the newest-N per class.
    2. `dos reindex --prune`           — rebuild the central project index and drop
       stale project mirrors (durable; a dropped root never resurrects).
    3. `scripts/git_cleanup.py [--apply]` — remove LANDED + clean + detached fleet
       worktrees and MERGED local branches. Its pure classifier REFUSEs any dirty or
       unlanded tree and never force-deletes a branch (`git branch -d`, not `-D`), so
       a concurrent loop's in-flight worktree is structurally safe from this sweep.
    4. memory-index size check          — MEMORY.md (the agent's long-term index) has
       a soft size cap; when it is over, the sweep SURFACES that as a finding. It does
       NOT auto-rewrite freeform prose memory — that is not a provably-safe mechanical
       act the way a keep-last-N reap is, so compression stays a surfaced decision for
       a human / a memory skill, never an automatic edit.
    5. `dos pulse`                      — the standing self-watch heartbeat. CONDITIONAL:
       if the verb is not present on this checkout (it ships on a later line), the step
       no-ops with a clear note rather than failing the sweep.
    6. `scripts/proc_reaper.py [--apply]` — reap RUNAWAY processes a fleet leaks: an
       orphaned whole-disk scanner (`find /` re-parented after its parent died, still
       burning CPU) and a memory-leak outlier (a worker over a hard RSS ceiling). Its
       pure classifier KEEPs every protected fleet member (supervisors, apply-loops,
       MCP servers) and REAPs only proven-waste classes, so a live worker is never
       killed. CONDITIONAL on psutil: absent -> a clean recorded skip, not a failure.

> **Fail-soft is the contract.** One chore erroring (a torn journal, `dos` not
> importable, git unhappy) NEVER aborts the rest — its step is recorded as `ok:
> false` with the error, and the sweep moves on. The sweep's exit code is non-zero
> only when a step that was asked to ACT (`--apply`) failed, so a cron notices a
> real problem while a clean dry-run is always exit 0.

> **Dry-run is the default.** Without `--apply` every chore runs in its read-only /
> `--json` form: the sweep reports what WOULD be reaped/pruned and never deletes a
> byte. `--apply` flips each chore to its acting form. This mirrors `dos reap` and
> `git_cleanup.py`, which are dry-run-by-default for the same reason.

This is dev / workflow tooling: it operates ON the package (shells its CLI, reads
its config) but is never imported BY it — nothing under `src/dos/` imports anything
in `scripts/`. It names no host, no lane, no commit convention; every chore reads
the workspace's own `dos.toml` through the verb it shells.

Usage::

    python scripts/cleanup_sweep.py --workspace .            # dry-run report, exit 0
    python scripts/cleanup_sweep.py --workspace . --json      # machine-readable plan
    python scripts/cleanup_sweep.py --workspace . --apply      # actually run the chores
    python scripts/cleanup_sweep.py --workspace . --apply --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The agent-memory index and its soft size cap. The cap mirrors the limit the
# memory harness itself warns at; over-cap is SURFACED, never auto-edited (see the
# module docstring, step 4). The path is resolved relative to the user home so the
# sweep names no dev-machine absolute path; an absent file is simply "not found"
# (a workspace with no agent memory is a valid, clean state).
_MEMORY_INDEX_REL = Path(".claude") / "projects"
_MEMORY_INDEX_NAME = "MEMORY.md"
_MEMORY_SOFT_CAP_BYTES = 24_400  # ~24.4 KiB, the harness's index soft limit


@dataclass
class StepResult:
    """One chore's outcome. `acted` is True only when the chore was asked to apply
    AND a destructive form ran; `ok` is False on any error (fail-soft: recorded, not
    raised). `detail` is plain data — counts, the would-reap list, an error string."""

    name: str
    ran: bool                       # the chore was attempted (vs skipped/absent)
    ok: bool                        # it completed without error
    acted: bool                     # a destructive action actually ran (--apply only)
    summary: str                    # one human line
    detail: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# The I/O shell — each chore shells an existing verb/script and folds its result.
# A subprocess that fails (non-zero, missing binary, timeout) becomes an ok:false
# StepResult, never an exception — that is the fail-soft contract.
# --------------------------------------------------------------------------- #

def _run(argv: list[str], *, cwd: Path) -> tuple[int, str, str]:
    """Run a subprocess; return (returncode, stdout, stderr). A missing executable
    or any OS error is folded to returncode 127 with the error on stderr — never
    raised, so a chore whose tool is absent degrades to a recorded skip."""
    try:
        proc = subprocess.run(
            argv, cwd=str(cwd), capture_output=True, text=True, timeout=300,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except (OSError, subprocess.SubprocessError) as exc:  # missing binary, timeout, …
        return 127, "", f"{type(exc).__name__}: {exc}"


def _load_json(out: str) -> dict | list | None:
    """Parse a verb's --json stdout; None if it isn't JSON (so the fold degrades)."""
    try:
        return json.loads(out)
    except (ValueError, TypeError):
        return None


def step_reap(root: Path, *, apply: bool) -> StepResult:
    """`dos reap --journal [--apply] --json` — scratch caps + WAL compaction."""
    # `--workspace` is a GLOBAL flag — it must precede the verb, not follow it.
    argv = ["dos", "--workspace", str(root), "reap", "--journal", "--json"]
    if apply:
        argv.append("--apply")
    code, out, err = _run(argv, cwd=root)
    if code != 0:
        return StepResult("reap", ran=True, ok=False, acted=False,
                          summary=f"dos reap failed (exit {code})",
                          detail={"stderr": err.strip()[:500]})
    data = _load_json(out) or {}
    # Count what was (or would be) reaped across the scratch classes + the journal.
    dropped = 0
    for key in ("audits", "verdicts", "runs"):
        cls = data.get(key) if isinstance(data, dict) else None
        if isinstance(cls, dict):
            dropped += len(cls.get("dropped", []))
    journal = data.get("journal", {}) if isinstance(data, dict) else {}
    j_note = ""
    if journal.get("compacted"):
        j_note = (f"; journal {journal.get('entries_before')}->"
                  f"{journal.get('entries_after')} lines")
    elif journal.get("would_compact"):
        j_note = f"; journal would compact ({journal.get('entries')} lines)"
    verb = "reaped" if apply else "would reap"
    return StepResult("reap", ran=True, ok=True, acted=apply,
                      summary=f"{verb} {dropped} scratch item(s){j_note}",
                      detail=data if isinstance(data, dict) else {})


def step_reindex(root: Path, *, apply: bool) -> StepResult:
    """`dos reindex [--prune] --json` — rebuild the project index, drop stale mirrors.

    Reindex always rebuilds the live index (non-destructive); `--prune` is the durable
    drop-stale-projects act, gated on `apply`."""
    argv = ["dos", "reindex", "--json"]  # reindex reads the central store, not --workspace
    if apply:
        argv.append("--prune")
    code, out, err = _run(argv, cwd=root)
    if code != 0:
        return StepResult("reindex", ran=True, ok=False, acted=False,
                          summary=f"dos reindex failed (exit {code})",
                          detail={"stderr": err.strip()[:500]})
    data = _load_json(out) or {}
    stale = data.get("stale", 0) if isinstance(data, dict) else 0
    dropped = data.get("throwaway", 0) if isinstance(data, dict) else 0
    note = f", {dropped} dropped" if (apply and dropped) else ""
    return StepResult("reindex", ran=True, ok=True, acted=apply,
                      summary=f"{data.get('projects', 0)} project(s), {stale} stale{note}",
                      detail=data if isinstance(data, dict) else {})


def step_git_cleanup(root: Path, *, apply: bool) -> StepResult:
    """`scripts/git_cleanup.py [--apply] --json` — prune landed worktrees/branches.

    Shelled (not imported) so the sweep stays a thin orchestrator and the cleanup
    tool keeps its own process/exit semantics. The classifier REFUSEs anything dirty
    or unlanded, so a sibling loop's in-flight worktree is safe."""
    script = Path(__file__).with_name("git_cleanup.py")
    argv = [sys.executable, str(script), "--workspace", str(root), "--json"]
    if apply:
        argv.append("--apply")
    code, out, err = _run(argv, cwd=root)
    data = _load_json(out) or {}
    counts = data.get("counts", {}) if isinstance(data, dict) else {}
    pruned = counts.get("PRUNE", 0)
    refused = counts.get("REFUSE", 0)
    applied = data.get("applied") if isinstance(data, dict) else None
    fails = [r for r in (applied or []) if not r.get("ok")]
    # git_cleanup exits 1 when an --apply step failed (e.g. branch -d refused). That
    # is a real, surfaced problem — record ok:false but never raise.
    ok = code == 0 or (not apply)
    verb = "pruned" if apply else "would prune"
    summary = f"{verb} {pruned} worktree/branch(es), {refused} refused"
    if fails:
        summary += f"; {len(fails)} apply step(s) failed"
    return StepResult("git_cleanup", ran=True, ok=ok and not fails, acted=apply,
                      summary=summary,
                      detail={"counts": counts, "apply_failures": fails})


def _memory_index_path() -> Path | None:
    """Resolve the agent-memory index path under the user home, or None if absent.
    Walks `~/.claude/projects/*/memory/MEMORY.md` and returns the first that exists
    — there is normally exactly one per project. Names no absolute dev path."""
    base = Path.home() / _MEMORY_INDEX_REL
    if not base.is_dir():
        return None
    for proj in sorted(base.iterdir()):
        candidate = proj / "memory" / _MEMORY_INDEX_NAME
        if candidate.is_file():
            return candidate
    return None


def step_memory_index() -> StepResult:
    """Measure MEMORY.md against its soft cap and SURFACE an over-cap finding.

    Deliberately read-only: freeform prose memory is not auto-rewritten by a cron —
    compression is a surfaced decision for a human or a memory skill (see the module
    docstring). A workspace with no agent memory is a clean, valid state (ran:false)."""
    path = _memory_index_path()
    if path is None:
        return StepResult("memory_index", ran=False, ok=True, acted=False,
                          summary="no agent-memory index found (nothing to check)")
    try:
        size = path.stat().st_size
    except OSError as exc:
        return StepResult("memory_index", ran=True, ok=False, acted=False,
                          summary=f"could not stat memory index: {exc}")
    over = size > _MEMORY_SOFT_CAP_BYTES
    kib = size / 1024
    cap_kib = _MEMORY_SOFT_CAP_BYTES / 1024
    if over:
        summary = (f"MEMORY.md is {kib:.1f} KiB — OVER the {cap_kib:.1f} KiB cap; "
                   f"compress the index (archive landed threads, shorten hooks)")
    else:
        summary = f"MEMORY.md is {kib:.1f} KiB — under the {cap_kib:.1f} KiB cap"
    return StepResult("memory_index", ran=True, ok=True, acted=False,
                      summary=summary,
                      detail={"bytes": size, "cap_bytes": _MEMORY_SOFT_CAP_BYTES,
                              "over_cap": over})


def step_pulse(root: Path) -> StepResult:
    """`dos pulse` — the standing self-watch heartbeat (conditional).

    The pulse verb ships on a later line; if it is absent on this checkout the step
    no-ops with a note rather than failing the sweep. When present, the pulse's own
    silence rule applies — it pushes a notification only when something is wrong."""
    # Probe for the verb without acting: `dos pulse --help` exits 0 iff it exists.
    code, _out, _err = _run(["dos", "pulse", "--help"], cwd=root)
    if code != 0:
        return StepResult("pulse", ran=False, ok=True, acted=False,
                          summary="dos pulse not on this checkout — skipped (ships on a later line)")
    # `--workspace` is a global flag — before the verb.
    code, out, err = _run(["dos", "--workspace", str(root), "pulse"], cwd=root)
    if code != 0:
        return StepResult("pulse", ran=True, ok=False, acted=False,
                          summary=f"dos pulse failed (exit {code})",
                          detail={"stderr": err.strip()[:500]})
    # An all-clear pulse is silent (no output); a pushed pulse names what was wrong.
    line = (out.strip().splitlines() or ["all clear (silent)"])[0]
    return StepResult("pulse", ran=True, ok=True, acted=False,
                      summary=f"heartbeat: {line}")


def step_proc_reaper(root: Path, *, apply: bool) -> StepResult:
    """`scripts/proc_reaper.py [--apply] --json` — reap runaway leaked processes.

    Shelled (not imported) so the sweep stays a thin orchestrator and the reaper keeps
    its own process/exit semantics. Its classifier REAPs only an orphaned whole-disk
    scanner past a CPU floor or a process over a hard RSS cap, and KEEPs every
    protected fleet member — so a live worker is structurally safe. CONDITIONAL on
    psutil: if absent the reaper reports an error note and this step records a clean
    skip rather than failing the sweep."""
    script = Path(__file__).with_name("proc_reaper.py")
    argv = [sys.executable, str(script), "--json"]
    if apply:
        argv.append("--apply")
    code, out, err = _run(argv, cwd=root)
    data = _load_json(out) or {}
    # psutil absent -> the reaper emits {"error": ...}; that is a skip, not a failure.
    if isinstance(data, dict) and data.get("error"):
        return StepResult("proc_reaper", ran=False, ok=True, acted=False,
                          summary=data["error"])
    counts = data.get("counts", {}) if isinstance(data, dict) else {}
    reaped = data.get("reaped", []) if isinstance(data, dict) else []
    surfaced = data.get("surfaced", []) if isinstance(data, dict) else []
    fails = [r for r in reaped if r.get("ok") is False]
    n = len([r for r in reaped if r.get("ok") is not False])
    verb = "reaped" if apply else "would reap"
    summary = f"{verb} {n} runaway process(es), {len(surfaced)} surfaced"
    if fails:
        summary += f"; {len(fails)} kill(s) failed"
    # proc_reaper exits 1 only when an --apply kill failed (e.g. access denied).
    ok = (code == 0 or not apply) and not fails
    return StepResult("proc_reaper", ran=True, ok=ok, acted=apply and n > 0,
                      summary=summary,
                      detail={"counts": counts, "reaped": reaped,
                              "surfaced": surfaced})


# --------------------------------------------------------------------------- #
# The sweep — run every chore, fold the results, decide the exit code.
# --------------------------------------------------------------------------- #

def run_sweep(root: Path, *, apply: bool) -> list[StepResult]:
    """Run every chore in order, fail-soft. Returns one StepResult per chore."""
    return [
        step_reap(root, apply=apply),
        step_reindex(root, apply=apply),
        step_git_cleanup(root, apply=apply),
        step_memory_index(),
        step_pulse(root),
        step_proc_reaper(root, apply=apply),
    ]


def render_report(results: list[StepResult], *, apply: bool) -> str:
    mode = "APPLY" if apply else "DRY-RUN (nothing changed — pass --apply to act)"
    lines = [f"# DOS cleanup-sweep — {mode}", ""]
    for r in results:
        if not r.ran:
            mark = "skip"
        elif not r.ok:
            mark = "FAIL"
        elif r.acted:
            mark = "did "
        else:
            mark = "ok  "
        lines.append(f"  [{mark}] {r.name:<13} {r.summary}")
    return "\n".join(lines)


def _repo_root(workspace: str) -> Path:
    """Resolve the workspace to a git top-level if possible, else the given path."""
    try:
        out = subprocess.run(
            ["git", "-C", workspace, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return Path(workspace).resolve()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--workspace", default=".", help="workspace root (default: cwd / git top-level)")
    p.add_argument("--json", action="store_true", help="emit the sweep result as JSON")
    p.add_argument("--apply", action="store_true",
                   help="run the chores for real (reap/prune/reindex); default is dry-run")
    args = p.parse_args(argv)
    root = _repo_root(args.workspace)

    results = run_sweep(root, apply=args.apply)

    if args.json:
        out = {
            "workspace": str(root),
            "apply": args.apply,
            "steps": [
                {"name": r.name, "ran": r.ran, "ok": r.ok, "acted": r.acted,
                 "summary": r.summary, "detail": r.detail}
                for r in results
            ],
        }
        sys.stdout.write(json.dumps(out, indent=2, default=str) + "\n")
    else:
        sys.stdout.write(render_report(results, apply=args.apply) + "\n")

    # Exit non-zero only when a chore that was asked to ACT failed — a cron then
    # notices a real problem, while a clean dry-run (or an all-skip) is always 0.
    acting_failure = any(r.ran and not r.ok for r in results)
    return 1 if (args.apply and acting_failure) else 0


if __name__ == "__main__":
    sys.exit(main())
