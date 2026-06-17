"""stop_failure_sensor — boundary I/O for the API-failure circuit breaker.

Same shape as `marker_sensor`: the one impure piece that the pure `breaker`
kernel can't touch. `breaker.record_failure` / `breaker.record_success` are
PURE — they compute the next `BreakerCounts` and verdict but never touch disk.
This module is the disk side: one `.dos/stop-failures/<sid>.json` per session,
atomically written, read back by `cmd_hook_stop_failure` on every StopFailure
hook fire so the breaker can accumulate state across the many short-lived hook
processes of one session.

Public API (narrow — the hook command is the only caller):
    load_counts(session_id, cfg) -> BreakerCounts
    save_counts(session_id, counts, cfg) -> None
    stop_failures_dir(cfg) -> Path         # for inspection / tests
"""
from __future__ import annotations

import json
import os
import pathlib
from typing import Optional

from dos import breaker as _breaker

_SUBDIR = "stop-failures"


def _safe_sid(session_id: str) -> Optional[str]:
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in session_id)
    safe = safe[:80]
    return safe or None


def stop_failures_dir(cfg) -> pathlib.Path:
    """The .dos/stop-failures/ directory under the active workspace. PURE path."""
    return pathlib.Path(cfg.paths.dot_dos) / _SUBDIR


def _counts_path(session_id: str, cfg) -> Optional[pathlib.Path]:
    safe = _safe_sid(session_id)
    if not safe:
        return None
    return stop_failures_dir(cfg) / f"{safe}.json"


def load_counts(session_id: str, cfg=None) -> _breaker.BreakerCounts:
    """Read the persisted BreakerCounts for this session. Returns zeros if absent."""
    from dos import config as _config
    cfg = cfg or _config.active()
    path = _counts_path(session_id, cfg)
    if path is None or not path.exists():
        return _breaker.BreakerCounts()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _breaker.BreakerCounts(
            consecutive=int(data.get("consecutive", 0)),
            total=int(data.get("total", 0)),
        )
    except Exception:
        return _breaker.BreakerCounts()


def save_counts(
    session_id: str,
    counts: _breaker.BreakerCounts,
    cfg=None,
) -> None:
    """Atomically persist BreakerCounts for this session (write-then-rename)."""
    from dos import config as _config
    cfg = cfg or _config.active()
    path = _counts_path(session_id, cfg)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"consecutive": counts.consecutive, "total": counts.total}),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except Exception:
        pass
