"""account_ledger — per-account run / failure / token attribution (append-only).

The independent-session-tracking surface. DOS already keys state two ways:
``.dos/stop-failures/<sid>.json`` by SESSION (the API-failure breaker) and
``.dos/runs/<run_id>/`` by RUN (lineage + intent). Neither answers the SEAT
question a multi-account fleet has: *which runs landed on account A, how often did
account B wall, what did account C spend?* This module adds that third axis — an
append-only ledger keyed by the seat a run used — so the question is one read, not
a scan of the whole run tree.

Layout, under the workspace's ``.dos/``::

    .dos/accounts/<safe_name>/runs.jsonl       # one line per run that used the seat
    .dos/accounts/<safe_name>/failures.jsonl   # rate-limit / wall / breaker events
    .dos/accounts/<safe_name>/tokens.jsonl     # per-run token-spend samples

The account NAME is **caller-supplied** — a roster label the host hands in. The
kernel names no vendor account mechanism (the litmus); this module only files
records under whatever string it is given, sanitized to a filesystem-safe stem.

Every write is append-only JSONL — the ``lane_journal`` / ``intent_ledger``
discipline: make the dir, append one line, flush+fsync, never rewrite a prior
line. A torn or non-JSON line on read is skipped, never raised. The whole module
is **fail-soft**: a ledger write must never break a launch, and a read must never
break a report. It is I/O at the boundary (a genuine filesystem interconnect),
carries no verdict, and names no host — the seam tier, not a decider.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

_SUBDIR = "accounts"

# The three ledger streams an account accumulates. Closed set as data so a caller
# can't typo a stream into existence (the append refuses an unknown ledger).
LEDGER_RUNS = "runs"
LEDGER_FAILURES = "failures"
LEDGER_TOKENS = "tokens"
_LEDGERS = (LEDGER_RUNS, LEDGER_FAILURES, LEDGER_TOKENS)


def _safe_name(account_name: str) -> Optional[str]:
    """A filesystem-safe stem for an account name, or None when it is unusable.

    Same discipline as ``stop_failure_sensor._safe_sid``: keep alnum / ``-`` / ``_``,
    map everything else to ``_``, cap the length, and reject an empty result — a
    distrusted host-supplied label never escapes its directory.
    """
    if not isinstance(account_name, str):
        return None
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in account_name)
    safe = safe[:80]
    return safe or None


def accounts_dir(cfg) -> Path:
    """The ``.dos/accounts/`` root under the active workspace. PURE path."""
    return Path(cfg.paths.dot_dos) / _SUBDIR


def account_dir(cfg, account_name: str) -> Optional[Path]:
    """The per-account ledger directory, or None when the name is unusable."""
    safe = _safe_name(account_name)
    if safe is None:
        return None
    return accounts_dir(cfg) / safe


def _ledger_path(cfg, account_name: str, ledger: str) -> Optional[Path]:
    if ledger not in _LEDGERS:
        return None
    d = account_dir(cfg, account_name)
    if d is None:
        return None
    return d / f"{ledger}.jsonl"


def append(
    cfg,
    account_name: str,
    ledger: str,
    record: dict,
    *,
    now_ms: Optional[int] = None,
) -> bool:
    """Append one record to an account's ledger stream. Returns True on a write.

    Stamps ``ts_ms`` (epoch-ms) into the record unless it already carries one — so a
    test can pin time by pre-setting it, and a caller gets ordering for free. The
    write is append-only (open ``"a"``), flushed and fsync'd so a crash mid-fleet
    leaves a complete prefix. Fail-soft: an unusable name, an unknown ledger, or any
    OS error returns False (the caller never has to guard a ledger write)."""
    path = _ledger_path(cfg, account_name, ledger)
    if path is None or not isinstance(record, dict):
        return False
    rec = dict(record)
    rec.setdefault("ts_ms", int(time.time() * 1000) if now_ms is None else int(now_ms))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return True
    except OSError:
        return False


def read(cfg, account_name: str, ledger: str) -> list[dict]:
    """Read an account's ledger stream as a list of dicts (oldest first).

    Tolerant: a missing file is ``[]``; a torn or non-JSON line (a crash mid-write,
    a non-object line) is skipped, never raised. Never returns a partial record."""
    path = _ledger_path(cfg, account_name, ledger)
    if path is None or not path.exists():
        return []
    out: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue  # torn / garbage line — skip, the WAL torn-tail discipline
        if isinstance(obj, dict):
            out.append(obj)
    return out


# --------------------------------------------------------------------------- #
# Convenience recorders — the three streams a fleet actually writes.
# --------------------------------------------------------------------------- #
def record_run(
    cfg,
    account_name: str,
    run_id: str,
    *,
    process_id: str = "",
    extra: Optional[dict] = None,
    now_ms: Optional[int] = None,
) -> bool:
    """Note that ``run_id`` launched on this seat (the runs.jsonl producer)."""
    rec = {"run_id": run_id}
    if process_id:
        rec["process_id"] = process_id
    if extra:
        rec.update(extra)
    return append(cfg, account_name, LEDGER_RUNS, rec, now_ms=now_ms)


def record_failure(
    cfg,
    account_name: str,
    *,
    reason: str,
    category: str = "",
    run_id: str = "",
    session_id: str = "",
    extra: Optional[dict] = None,
    now_ms: Optional[int] = None,
) -> bool:
    """Note a wall / rate-limit / breaker event on this seat (failures.jsonl).

    ``category`` is a free string the caller fills from its own taxonomy (e.g. a
    ``provider_limit.ProviderLimit`` value) — this module stores it, never derives
    it. Lets a rotation policy later ask "which seats wall most" per account."""
    rec = {"reason": reason}
    if category:
        rec["category"] = category
    if run_id:
        rec["run_id"] = run_id
    if session_id:
        rec["session_id"] = session_id
    if extra:
        rec.update(extra)
    return append(cfg, account_name, LEDGER_FAILURES, rec, now_ms=now_ms)


def record_tokens(
    cfg,
    account_name: str,
    *,
    tokens: int,
    run_id: str = "",
    extra: Optional[dict] = None,
    now_ms: Optional[int] = None,
) -> bool:
    """Note a token-spend sample on this seat (tokens.jsonl) for cost attribution."""
    rec = {"tokens": int(tokens)}
    if run_id:
        rec["run_id"] = run_id
    if extra:
        rec.update(extra)
    return append(cfg, account_name, LEDGER_TOKENS, rec, now_ms=now_ms)


def summary(cfg, account_name: str) -> dict:
    """A folded at-a-glance tally for one account, for a `dos accounts` enrichment.

    ``runs`` / ``failures`` = line counts; ``tokens`` = the summed ``tokens`` field
    across the token stream. Read-only; never raises (each stream folds via the
    tolerant :func:`read`). An account with no ledger folds to all-zeros."""
    runs = read(cfg, account_name, LEDGER_RUNS)
    failures = read(cfg, account_name, LEDGER_FAILURES)
    tokens = read(cfg, account_name, LEDGER_TOKENS)
    total_tokens = 0
    for r in tokens:
        try:
            total_tokens += int(r.get("tokens", 0))
        except (TypeError, ValueError):
            continue
    return {
        "account": account_name,
        "runs": len(runs),
        "failures": len(failures),
        "tokens": total_tokens,
    }


def known_accounts(cfg) -> list[str]:
    """The account stems that have a ledger dir, sorted. ``[]`` when none / unreadable."""
    root = accounts_dir(cfg)
    try:
        return sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        return []


__all__ = [
    "LEDGER_RUNS",
    "LEDGER_FAILURES",
    "LEDGER_TOKENS",
    "accounts_dir",
    "account_dir",
    "append",
    "read",
    "record_run",
    "record_failure",
    "record_tokens",
    "summary",
    "known_accounts",
]
