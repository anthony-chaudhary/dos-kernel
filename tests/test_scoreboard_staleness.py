"""The public self-scoreboard cannot silently drift arbitrarily behind HEAD."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import dos

_REPO = Path(dos.__file__).resolve().parents[2]
_BADGE = _REPO / "docs/scoreboard/anthony-chaudhary/dos-kernel/badge.json"
_MAX_COMMITS_BEHIND = 300


def test_self_scoreboard_badge_has_a_bounded_git_head_witness():
    badge = json.loads(_BADGE.read_text(encoding="utf-8"))
    head_sha = badge.get("head_sha")
    assert isinstance(head_sha, str) and head_sha, (
        "self-scoreboard badge has no machine-readable head_sha; regenerate it with "
        "scripts/scoreboard_surfaces.py before the staleness floor can judge it"
    )
    proc = subprocess.run(
        ["git", "rev-list", "--count", f"{head_sha}..HEAD"],
        cwd=_REPO, text=True, capture_output=True,
    )
    assert proc.returncode == 0, f"badge head_sha is not an ancestor of HEAD: {proc.stderr}"
    behind = int(proc.stdout.strip())
    assert behind <= _MAX_COMMITS_BEHIND, (
        f"self-scoreboard is {behind} commits behind HEAD (limit {_MAX_COMMITS_BEHIND}); "
        "run the scoreboard refresh or adjudicate the new raw fires"
    )
