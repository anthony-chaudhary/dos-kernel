"""git-delta — the "commits since a start SHA" evidence, one shared reader.

`git log <start-sha>..HEAD` is the authoritative *forward-progress delta* for a
run: how many commits landed on the served workspace since the run began. Two
callers need exactly this fold and must not drift from each other:

  * `dos.timeline` — Stage 6 of the dispatch handoff view ("N commits since
    start").
  * `dos.liveness` (via the `dos liveness` CLI's evidence-gather) — the git rung
    of the temporal verdict: ≥1 commit since start is the `ADVANCING` floor
    (docs/82, LVN Phase 1b).

This module is the single home for that read so LVN does not re-implement
`timeline`'s git rung (the LVN-1b directive). It is **boundary I/O**, not a pure
verdict: like `pick_oracle`'s gather and `verify`'s git reads, the subprocess
happens HERE, at the caller boundary, and the already-counted delta is handed to
the pure classifier. `dos.liveness.classify` itself never calls this — it takes
`commits_since_start: int` as already-gathered evidence (the arbiter discipline).

The repo root is passed in EXPLICITLY (never read from the process-global active
config), so a long-lived caller fielding several workspaces — the MCP server, a
fleet daemon — gets the right tree without mutating global state. Every failure
mode (no SHA, non-git dir, git missing, timeout, non-zero exit) degrades to an
empty list: a liveness verdict in a repo with no git history is `0 commits`, the
honest floor, never a crash (the no-plan / fail-safe discipline).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# Cap the git call so a pathological repo can't hang an evidence-gather. Matches
# the 10s bound `timeline._git_log` has always used.
_GIT_TIMEOUT_S = 10


def commits_since(start_sha: str, *, root: Path | str) -> list[dict[str, str]]:
    """`git log <start_sha>..HEAD` over ``root``, as ``[{sha, subject}, …]``.

    Newest-first (git's default order). Returns ``[]`` for any of: an empty
    ``start_sha`` (a run with no recorded start commit), a non-git ``root``, a
    missing ``git`` binary, a timeout, or a non-zero git exit (e.g. an unknown
    SHA). The empty list is the safe degrade — a caller reads it as "no forward
    delta observed," never as an error to propagate.
    """
    if not start_sha:
        return []
    try:
        raw = subprocess.run(
            ["git", "log", f"{start_sha}..HEAD", "--pretty=format:%h%x09%s"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_S,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if raw.returncode != 0:
        return []
    out: list[dict[str, str]] = []
    for line in raw.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        out.append({"sha": parts[0], "subject": parts[1]})
    return out


def count_commits_since(start_sha: str, *, root: Path | str) -> int:
    """Just the count — the single number `dos.liveness`'s git rung needs.

    A thin fold over `commits_since` so the LVN evidence-gather reads one int
    without materialising the subject list it does not use.
    """
    return len(commits_since(start_sha, root=root))


def recent_commits(n: int = 10, *, root: Path | str) -> list[dict[str, str]]:
    """The last ``n`` commits on ``root``, newest-first, as ``[{sha, subject}, …]``.

    The *unanchored* sibling of `commits_since`: where that answers "what landed
    since a run's start SHA," this answers "what has landed lately, period" — the
    one git read `dos top` needs to show real movement in a repo with **no
    leases and no plan at all** (a freshly-`dos init`'d checkout). Kept here so
    the kernel's git-evidence reads stay in one home rather than `dispatch_top`
    spawning its own subprocess.

    Same fail-safe contract as `commits_since`: returns ``[]`` for a non-positive
    ``n``, a non-git ``root``, a missing ``git`` binary, a timeout, or a non-zero
    git exit (e.g. a repo with zero commits — `git log` exits non-zero on an
    unborn HEAD). The empty list is the honest floor — "no history observed,"
    never an error to propagate. ``root`` is explicit (never the process-global
    active config), the long-lived-caller discipline `commits_since` set.
    """
    if n <= 0:
        return []
    try:
        raw = subprocess.run(
            ["git", "log", f"-{int(n)}", "--pretty=format:%h%x09%s"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_S,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if raw.returncode != 0:
        return []
    out: list[dict[str, str]] = []
    for line in raw.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        out.append({"sha": parts[0], "subject": parts[1]})
    return out
