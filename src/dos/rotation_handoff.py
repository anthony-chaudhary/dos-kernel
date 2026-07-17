"""rotation_handoff — DOS's durable StopFailure-to-SessionStart handoff (docs/386 §4).

The "10x" account-switcher step is: on a rate-limit wall, **record the serving seat
the next real SessionStart should use**, not just back off the same one. The kernel
side is cheap (``serving_pool`` already answers "a different serving seat") and the
per-seat failure attribution is landed (``account_ledger``). The blocker is the host
boundary: a StopFailure hook cannot relaunch the session or pass a different seat's
auth env (``CLAUDE_CONFIG_DIR`` / token) to the next attempt. Its output and exit code
are ignored for that purpose, so StopFailure ``asyncRewake`` is not a resumption
mechanism. Actual resumption is Claude Code's retry loop (``CLAUDE_CODE_MAX_RETRIES``,
with ``CLAUDE_CODE_RETRY_WATCHDOG=1`` for sustained 429/529 capacity errors). The only
env channel this module can target is ``CLAUDE_ENV_FILE`` on a real SessionStart-class
event.

So the rotation is a **two-phase handoff**, and this module is the durable record between
the phases — the contract the two hooks share:

  * **WRITE** (the StopFailure hook, on a wall): attribute the wall to the current seat,
    pick a serving seat to rotate to, build that seat's env-override via the resolved
    ``AccountAuthSpec`` (``spec.launch_env``), and persist it here keyed by session. This
    is the COMPUTE half — all the StopFailure boundary can reach.
  * **CONSUME** (the SessionStart applier, at the next real SessionStart): read the pending
    handoff, append its env to ``CLAUDE_ENV_FILE`` (the one env channel that boundary
    exposes), and clear the record so it applies exactly once. This is the APPLY half.

The env is a caller-supplied DATA dict (the spec built it) — this module names **no
vendor**, it just files a record under a sanitized session stem and reads it back. It is
fail-soft I/O at the boundary (the ``stop_failure_sensor`` / ``account_ledger`` discipline):
a write never breaks the hook, a read never breaks the session, and a torn/garbage record
reads back as "no handoff". The store is a single record per session (atomic replace — the
latest wall wins), not an append-only log: only the most recent rotation matters, and a
successful apply clears it.

Honesty note (docs/386 §4): there is no StopFailure rewake boundary for this module to
rely on. If Claude Code retry/watchdog (or any later host start) produces a real
SessionStart exposing ``CLAUDE_ENV_FILE``, the handoff applies then; otherwise it
persists until the next genuine env-capable start. Either way the rotation is COMPUTED +
RECORDED the moment the wall is seen, and APPLIED at the first env-capable boundary —
never silently dropped. The residual the harness still owns (retry/start needs an env
channel before SessionStart) stays named in docs/386 §4, not papered over.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Files live under the account ledger's own root (this IS per-seat rotation state),
# in a `rotation/` sub-dir keyed by session: .dos/accounts/rotation/<safe_sid>.json
_SUBDIR = "accounts"
_ROTATION = "rotation"


@dataclass(frozen=True)
class RotationHandoff:
    """A pending seat rotation: the seat/env the next env-capable start should apply.

      ``to_account``   — the serving seat the next attempt should run under (a roster label).
      ``env``          — the env-override that selects that seat (a flat str→str map the
                         ``AccountAuthSpec`` built, e.g. ``{CLAUDE_CONFIG_DIR, ...}``); the
                         APPLY phase writes these to ``CLAUDE_ENV_FILE``.
      ``from_account`` — the walled seat being rotated away from (for the surfaced note).
      ``reason``       — a short human reason (e.g. ``rotate-on-wall: transient_overload``).
      ``ts_ms``        — epoch-ms the handoff was written (staleness / ordering).
    """

    to_account: str
    env: dict = field(default_factory=dict)
    from_account: str = ""
    reason: str = ""
    ts_ms: int = 0

    def to_dict(self) -> dict:
        d: dict = {"to_account": self.to_account, "env": dict(self.env)}
        if self.from_account:
            d["from_account"] = self.from_account
        if self.reason:
            d["reason"] = self.reason
        if self.ts_ms:
            d["ts_ms"] = self.ts_ms
        return d

    @classmethod
    def from_dict(cls, d: object) -> "Optional[RotationHandoff]":
        """Rebuild a handoff from a parsed record, or None when it is unusable.

        Tolerant: a non-dict, an empty ``to_account``, or a non-dict ``env`` reads back
        as None (no handoff). The env is coerced to a flat str→str map defensively — a
        session-start env is always plain strings; anything else is dropped, never trusted."""
        if not isinstance(d, dict):
            return None
        to = str(d.get("to_account") or "").strip()
        env = d.get("env")
        if not to or not isinstance(env, dict):
            return None
        clean = {
            str(k): str(v)
            for k, v in env.items()
            if isinstance(k, str) and isinstance(v, (str, int, float))
        }
        try:
            ts = int(d.get("ts_ms") or 0)
        except (TypeError, ValueError):
            ts = 0
        return cls(
            to_account=to,
            env=clean,
            from_account=str(d.get("from_account") or ""),
            reason=str(d.get("reason") or ""),
            ts_ms=ts,
        )


def _safe_sid(session_id: str) -> Optional[str]:
    """A filesystem-safe stem for a session id, or None when unusable — the
    ``stop_failure_sensor._safe_sid`` / ``account_ledger._safe_name`` discipline so a
    distrusted host-supplied id never escapes its directory."""
    if not isinstance(session_id, str):
        return None
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in session_id)
    safe = safe[:120]
    return safe or None


def rotation_dir(cfg) -> Path:
    """The ``.dos/accounts/rotation/`` root under the active workspace. PURE path."""
    return Path(cfg.paths.dot_dos) / _SUBDIR / _ROTATION


def handoff_path(cfg, session_id: str) -> Optional[Path]:
    """The per-session handoff file path, or None when the session id is unusable."""
    safe = _safe_sid(session_id)
    if safe is None:
        return None
    return rotation_dir(cfg) / f"{safe}.json"


def write_handoff(
    cfg, session_id: str, handoff: RotationHandoff, *, now_ms: Optional[int] = None
) -> bool:
    """Persist a pending rotation for ``session_id``. Returns True on a write.

    Single-record-per-session: an atomic ``os.replace`` overwrites any prior handoff, so
    the latest wall wins and there is no torn-append window. Stamps ``ts_ms`` when the
    handoff carries none. Fail-soft: an unusable id, an empty ``to_account``, or any OS
    error returns False — a handoff-write fault must NEVER break the StopFailure hook."""
    path = handoff_path(cfg, session_id)
    if path is None or not isinstance(handoff, RotationHandoff) or not handoff.to_account:
        return False
    rec = handoff.to_dict()
    if not rec.get("ts_ms"):
        rec["ts_ms"] = int(time.time() * 1000) if now_ms is None else int(now_ms)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(rec, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def read_handoff(cfg, session_id: str) -> Optional[RotationHandoff]:
    """The pending handoff for ``session_id``, or None — never raises.

    A missing file, an unreadable one, or a torn/garbage record all read back as None
    (no pending rotation) — the WAL torn-tail discipline applied to a single record."""
    path = handoff_path(cfg, session_id)
    if path is None or not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return RotationHandoff.from_dict(d)


def clear_handoff(cfg, session_id: str) -> bool:
    """Delete the pending handoff for ``session_id``. True if a file was removed."""
    path = handoff_path(cfg, session_id)
    if path is None:
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def consume_handoff(cfg, session_id: str) -> Optional[RotationHandoff]:
    """Read the pending handoff AND clear it — the apply-once read for the SessionStart
    applier. Returns the handoff (or None); the clear is best-effort (a stale record
    that fails to unlink is harmless — the next successful apply overwrites it)."""
    h = read_handoff(cfg, session_id)
    clear_handoff(cfg, session_id)
    return h


def apply_to_env_file(handoff: RotationHandoff, env_file_path: str) -> bool:
    """Append the handoff's env-override to ``CLAUDE_ENV_FILE`` (``KEY=value`` lines).

    The APPLY half: ``CLAUDE_ENV_FILE`` is the path the harness persists env vars to at a
    SessionStart-class boundary. One ``KEY=value`` line per override is appended so
    commands after that start run under the rotated seat. Returns True on a write.
    Fail-soft: an empty env, no path, or any OS error returns False — the applier must
    never break the session start over an env-file fault. (Format note: ``KEY=value`` is
    the dotenv convention the harness reads; values are written verbatim — a config-dir
    path is a single token to end-of-line.)"""
    if not isinstance(handoff, RotationHandoff) or not handoff.env or not env_file_path:
        return False
    try:
        payload = "".join(f"{k}={v}\n" for k, v in handoff.env.items())
        with open(env_file_path, "a", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
        return True
    except OSError:
        return False


__all__ = [
    "RotationHandoff",
    "rotation_dir",
    "handoff_path",
    "write_handoff",
    "read_handoff",
    "clear_handoff",
    "consume_handoff",
    "apply_to_env_file",
]
