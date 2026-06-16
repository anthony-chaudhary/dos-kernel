"""git-delta — the "commits since a start SHA" evidence, one shared reader.

`<start-sha>..HEAD` is the authoritative *forward-progress delta* for a run: how
many commits landed on the served workspace since the run began. Two callers need
exactly this fold and must not drift from each other:

  * `dos.timeline` — Stage 6 of the dispatch handoff view ("N commits since
    start").
  * `dos.liveness` (via the `dos liveness` CLI's evidence-gather) — the VCS rung
    of the temporal verdict: ≥1 commit since start is the `ADVANCING` floor
    (docs/82, LVN Phase 1b).

This module is the single home for that read so LVN does not re-implement
`timeline`'s VCS rung (the LVN-1b directive). It is **boundary I/O**, not a pure
verdict: like `pick_oracle`'s gather and `verify`'s reads, the read happens HERE,
at the caller boundary, and the already-counted delta is handed to the pure
classifier. `dos.liveness.classify` itself never calls this — it takes
`commits_since_start: int` as already-gathered evidence (the arbiter discipline).

The actual VCS read now routes through the **`dos.vcs` backend seam**: this module
asks `active_vcs(root=…)` for the configured backend (default `GitBackend`, the
existing `git log`; `NullVcs` for a no-VCS workspace) and folds its `Commit` rows
back into the `{sha, subject}` dict shape every caller has always read. The public
signatures and the empty-list contract are UNCHANGED — the seam is invisible to
`timeline`/`liveness`/`dispatch_top`/`memory_recall`. (docs/360, the VCS-seam port.)

The repo root is passed in EXPLICITLY (never read from the process-global active
config), so a long-lived caller fielding several workspaces — the MCP server, a
fleet daemon — gets the right tree without mutating global state. Every failure
mode (no SHA, non-VCS dir, git missing, timeout, non-zero exit) degrades to an
empty list: a liveness verdict in a repo with no history is `0 commits`, the
honest floor, never a crash (the no-plan / fail-safe discipline).
"""

from __future__ import annotations

from pathlib import Path

from dos.vcs import active_vcs


def commits_since(start_sha: str, *, root: Path | str) -> list[dict[str, str]]:
    """Commits since ``start_sha`` over ``root``, as ``[{sha, subject}, …]``.

    Newest-first. Returns ``[]`` for any of: an empty ``start_sha`` (a run with no
    recorded start commit), a non-VCS ``root``, a missing VCS binary, a timeout, or
    an unknown SHA. The empty list is the safe degrade — a caller reads it as "no
    forward delta observed," never as an error to propagate. The read routes through
    the active VCS backend (default git); the `{sha, subject}` shape is unchanged.
    """
    return [c.to_dict() for c in active_vcs(root=root).commits_since(start_sha)]


def count_commits_since(start_sha: str, *, root: Path | str) -> int:
    """Just the count — the single number `dos.liveness`'s VCS rung needs.

    A thin fold over `commits_since` so the LVN evidence-gather reads one int
    without materialising the subject list it does not use.
    """
    return len(commits_since(start_sha, root=root))


def recent_commits(n: int = 10, *, root: Path | str) -> list[dict[str, str]]:
    """The last ``n`` commits on ``root``, newest-first, as ``[{sha, subject}, …]``.

    The *unanchored* sibling of `commits_since`: where that answers "what landed
    since a run's start SHA," this answers "what has landed lately, period" — the
    one read `dos top` needs to show real movement in a repo with **no leases and
    no plan at all** (a freshly-`dos init`'d checkout). Kept here so the kernel's
    VCS-evidence reads stay in one home rather than `dispatch_top` reading directly.

    Same fail-safe contract as `commits_since`: returns ``[]`` for a non-positive
    ``n``, a non-VCS ``root``, a missing VCS binary, a timeout, or a repo with zero
    commits (an unborn HEAD). The empty list is the honest floor — "no history
    observed," never an error to propagate. ``root`` is explicit (never the
    process-global active config), the long-lived-caller discipline `commits_since`
    set. The read routes through the active VCS backend (default git).
    """
    return [c.to_dict() for c in active_vcs(root=root).recent_commits(n)]
