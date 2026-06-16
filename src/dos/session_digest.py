"""session_digest — the one-line ground-truth orientation an agent gets at session start.

> **A trust substrate should not let an agent start blind.** A fresh Claude-Code
> session knows nothing about the work already in flight in this workspace: which
> lane leases a sibling holds, whether a breaker has tripped, what DOS has already
> refused. So the agent can collide with in-flight edits or repeat a move the
> kernel already blocked. This module folds that already-known ground truth into
> one short paragraph the `dos hook session-start` verb injects as
> `additionalContext` — the SessionStart analogue of the `dos hook stop` digest.

The kernel/driver split — this is KERNEL, and PURE
==================================================

`build_digest` is a PURE transform: folded leases + a `HelpSummary` + a
`BreakerVerdict` in, one string (or `None`) out. The I/O that gathers those facts
(`lane_lease.live_leases`, `lane_journal.read_all`, `breaker.classify`) lives at
the CLI boundary (`cli.py:cmd_hook_session_start`) — the same "I/O at the boundary,
data to the pure core" rule `liveness.classify` / `help_summary.summarize` rest on.
It names no vendor: the SessionStart envelope is the dialect's job (`HookMoment.SESSION`).

The silence rule
================

A digest is injected ONLY when there is something worth saying. A fresh repo with
no leases held, a CLOSED breaker, and nothing caught this session gets `None` — the
verb emits nothing, exactly like the advisory hooks pass silently. Orientation is a
signal; a digest that fires on every empty session would be noise the agent learns
to ignore.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence


def _lane_names(leases: Sequence[dict], limit: int = 4) -> list[str]:
    """The distinct lane names from a folded live-lease set, in first-seen order.

    De-duplicated (one lane can carry several WAL rows) and capped so the digest
    stays one line; an over-cap set is reported as a count by the caller.
    """
    names: list[str] = []
    for lease in leases:
        if not isinstance(lease, dict):
            continue
        lane = lease.get("lane")
        if isinstance(lane, str) and lane and lane not in names:
            names.append(lane)
    return names[:limit]


def _lease_phrase(leases: Sequence[dict]) -> Optional[str]:
    """`2 lane leases held (src, docs)` — or `None` when nothing is held."""
    names = _lane_names(leases)
    # Count distinct lanes, not raw rows, so the number matches the named set.
    distinct = []
    for lease in leases:
        if isinstance(lease, dict):
            lane = lease.get("lane")
            if isinstance(lane, str) and lane and lane not in distinct:
                distinct.append(lane)
    n = len(distinct)
    if n == 0:
        return None
    noun = "lane lease" if n == 1 else "lane leases"
    if names:
        shown = ", ".join(names)
        more = "" if n <= len(names) else f", +{n - len(names)} more"
        return f"{n} {noun} held ({shown}{more})"
    return f"{n} {noun} held"


def _breaker_phrase(breaker_verdict: Any) -> Optional[str]:
    """`breaker OPEN — escalate to a human` — or `None` when CLOSED/absent.

    Only an OPEN breaker is worth a session-start line; a CLOSED breaker is the
    normal state and stays silent (the silence rule).
    """
    if breaker_verdict is None:
        return None
    is_open = getattr(breaker_verdict, "is_open", None)
    if is_open is None:
        state = getattr(breaker_verdict, "state", None)
        is_open = str(getattr(state, "value", state) or "").upper() == "OPEN"
    if not is_open:
        return None
    esc = getattr(breaker_verdict, "escalation", None)
    esc_name = str(getattr(esc, "value", esc) or "").upper()
    if esc_name == "HUMAN":
        return "breaker OPEN — a decision is queued for a human"
    if esc_name == "JUDGE":
        return "breaker OPEN — escalated to a judge"
    return "breaker OPEN"


def _caught_phrase(summary: Any) -> Optional[str]:
    """`DOS refused 3 calls (+2 advisory) earlier this session` — or `None`.

    Folds a `help_summary.HelpSummary`. Reports only when something was caught;
    a clean session adds no line.
    """
    if summary is None:
        return None
    total = int(getattr(summary, "total", 0) or 0)
    if total <= 0:
        return None
    withheld = int(getattr(summary, "withheld", 0) or 0)
    advisory = int(getattr(summary, "advisory", max(0, total - withheld)) or 0)
    if withheld > 0:
        noun = "call" if withheld == 1 else "calls"
        tail = f" (+{advisory} advisory)" if advisory > 0 else ""
        return f"DOS refused {withheld} {noun}{tail} earlier this session"
    noun = "caution" if advisory == 1 else "cautions"
    return f"DOS surfaced {advisory} advisory {noun} earlier this session"


def _prior_refusals_phrase(recent_refusals: Any) -> Optional[str]:
    """`a prior run refused 2 calls here recently` — or `None` when 0/absent.

    The CROSS-SESSION leg: `caught_phrase` reports what THIS session caught, but a
    fresh agent waking COLD (the cron/unattended case) inherits none of that. This
    folds the durable refusal trail prior runs wrote to the lane WAL (the OP_REFUSE
    records `lane_journal.read_all` carries), so a scheduled worker starting blind
    sees what its predecessors already refused — instead of re-attempting the exact
    move the last 3am run was denied, with nobody watching (docs/121 §4.1). An int
    count in; 0/None ⇒ no line (the silence rule).
    """
    try:
        n = int(recent_refusals or 0)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    noun = "call" if n == 1 else "calls"
    return f"a prior run was refused {n} {noun} here recently — don't re-attempt blindly"


_HEAD = "DOS session orientation"
_RESTORED_HEAD = f"{_HEAD} (restored after compaction)"


def mark_restored(digest: str) -> str:
    """Stamp an already-built digest as restored-after-compaction. PURE, idempotent.

    The persisted digest was built BEFORE the compaction (so its head is the plain
    `DOS session orientation: …`); re-injecting it after a compact should note that
    it survived. Rewrites only the head clause; a digest that already carries the
    note (or one with an unexpected shape) is returned unchanged.
    """
    if not isinstance(digest, str) or not digest:
        return digest
    if digest.startswith(_RESTORED_HEAD):
        return digest
    if digest.startswith(_HEAD):
        return _RESTORED_HEAD + digest[len(_HEAD):]
    return digest


def build_digest(
    *,
    leases: Sequence[dict] = (),
    summary: Any = None,
    breaker_verdict: Any = None,
    is_kernel_repo: bool = False,
    restored: bool = False,
    recent_refusals: Any = None,
) -> Optional[str]:
    """Fold the read-only ground-truth facts into one session-start line. PURE.

    Returns the `additionalContext` string, or `None` when there is nothing worth
    saying (the silence rule: no leases held, a CLOSED breaker, nothing caught, no
    prior-run refusal trail).

    `leases`          — a folded live-lease set (`lane_lease.live_leases`).
    `summary`         — a `help_summary.HelpSummary` for this session (or None).
    `breaker_verdict` — a `breaker.BreakerVerdict` (or None); only OPEN speaks.
    `is_kernel_repo`  — whether this workspace IS the kernel (a SELF_MODIFY caveat
                        worth one clause, but only when leases are already held —
                        on its own it is not enough to break the silence rule).
    `restored`        — set on a SessionStart fired by a compaction, so the line
                        notes the orientation survived the compact (docs: Part C).
    `recent_refusals` — the CROSS-SESSION count of durable refusals prior runs wrote
                        to the lane WAL (OP_REFUSE), so a COLD cron worker wakes
                        knowing what its predecessors were already refused (docs/121
                        §4.1). 0/None ⇒ no line; a positive count breaks the silence
                        rule on its own (the unattended case has no other channel).
    """
    parts: list[str] = []
    lease_phrase = _lease_phrase(leases)
    if lease_phrase is not None:
        if is_kernel_repo:
            lease_phrase += " — this IS the kernel repo (edits self-modify)"
        parts.append(lease_phrase)
    breaker_phrase = _breaker_phrase(breaker_verdict)
    if breaker_phrase is not None:
        parts.append(breaker_phrase)
    caught_phrase = _caught_phrase(summary)
    if caught_phrase is not None:
        parts.append(caught_phrase)
    prior_phrase = _prior_refusals_phrase(recent_refusals)
    if prior_phrase is not None:
        parts.append(prior_phrase)

    if not parts:
        return None

    head = _RESTORED_HEAD if restored else _HEAD
    body = "; ".join(parts)
    return f"{head}: {body}."
