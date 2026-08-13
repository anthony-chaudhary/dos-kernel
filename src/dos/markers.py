"""Wait-marker management for .dos/markers/.

Handles pruning of old markers to prevent unbounded growth.
"""

from __future__ import annotations

import time
from pathlib import Path

MARKERS_DIR = Path(".dos/markers")
MARKER_PATTERN = "*.jsonl"


def prune_markers(
    dir: Path | None = None,
    max_age_days: int = 3,
) -> int:
    """Remove wait-marker files older than *max_age_days*.

    Intended to be invoked periodically by the dispatch loop or ``dos reindex``
    to prevent unbounded growth of the ``.dos/markers/`` directory.

    :param dir: Directory containing markers (defaults to ``.dos/markers``).
    :param max_age_days: Maximum age in days before a marker is eligible for removal.
    :returns: Number of markers removed.
    """
    target = dir or MARKERS_DIR
    if not target.is_dir():
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for f in target.iterdir():
        if f.suffix == ".jsonl" and f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
            removed += 1
    return removed
