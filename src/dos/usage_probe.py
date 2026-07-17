"""usage_probe — the live near-cap SIGNAL for the seat a running session is on (docs/391).

``live_rotation`` is the pure verdict ("this seat is near its cap → rotate"); it needs a
fact it cannot compute: *is the current seat actually near its cap right now?* That fact is
a live meter read, and the kernel is meter-blind by design (the "no host knowledge" floor —
``account_switcher`` takes its rate-limit state from an injected ``probe_fn``, never reads it
itself). This module is the boundary that supplies the meter for the ONE seat a hook knows
intimately: the seat the firing session is running on.

The honest problem (docs/380, confirmed on a real transcript): the Claude Code harness does
NOT write a rate-limit window / utilization / reset-epoch anywhere a hook can read. What IS
on disk is two weaker but real signals, both keyed by the live SESSION (not by account — so
this is NOT the switcher's per-creds ``probe_fn``; it is the current-seat assessor a hook
calls because it alone knows the session id):

  1. **Breaker pressure** — ``.dos/stop-failures/<sid>.json`` (``stop_failure_sensor``).
     A session that has already taken API walls this run (rewoken by the StopFailure
     backoff and recovered) carries a non-zero consecutive/total failure count. That is a
     real "this seat is struggling" signal even while the session is currently UP — the
     proactive cue to rotate before the next wall sticks. Always available, no config.

  2. **Token budget** — the session transcript's per-turn ``usage`` (``input_tokens`` +
     ``output_tokens`` + cache tokens, which the assistant records DO carry). Summed over
     the session and divided by a host-supplied window cap, it estimates utilization. This
     leg is OPT-IN: without a configured cap there is no ceiling to divide by, so it is
     skipped rather than fabricating a fraction (the meter-blind floor — never invent a
     wall the host did not declare).

The output is a :class:`ProbeSnapshot` that satisfies ``account_switcher.ProbeLike``
(``status`` / ``utilization`` / ``reset_at_epoch`` / ``allowed``), so the hook folds it
through the SAME ``account_state`` path the fleet picker uses — the live signal and the
batch picker classify a seat identically.

**Fail-soft / fail-open, always.** Every read is wrapped: a missing file, a torn transcript,
a bad count — any of them yields ``None`` (no signal), and ``account_state`` treats a None
probe as SERVING. The contract is the one every other near-the-edge DOS piece holds: when
in doubt, do NOT manufacture a wall (a false near-cap would rotate a perfectly healthy
session and churn the fleet). Rotation is only ever proposed on a signal we actually saw.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

# Consecutive StopFailures that read as a FULL "this seat is walling" pressure signal
# (utilization 1.0). One transient blip (consecutive == 1) is ~0.33 — real but not yet
# rotation-worthy on its own; by the third the seat is treated as walled. Mirrors the
# spirit of the breaker's default 3-consecutive trip without importing its policy.
_PRESSURE_FULL = 3

# Probe status tokens — the ``account_switcher`` vocabulary (``FakeProbe`` uses the same):
# ``allowed`` (serving), ``allowed_warning`` (serving but near cap), ``rejected`` (walled).
STATUS_ALLOWED = "allowed"
STATUS_WARNING = "allowed_warning"
STATUS_REJECTED = "rejected"


@dataclass(frozen=True)
class ProbeSnapshot:
    """A live read of one seat's usage state — the ``account_switcher.ProbeLike`` shape.

    ``status`` is the harness-vocabulary token; ``utilization`` ∈ [0,1] is the binding
    window's estimated consumed fraction (the MAX across the signals we saw);
    ``reset_at_epoch`` is when the window rolls (``None`` — not on disk; a host that knows
    it injects it). ``allowed`` is the binary "would a request be served now" — False only
    when the signal is an actual rejection (a recorded wall), never merely near-cap."""

    status: str
    utilization: float
    reset_at_epoch: Optional[int] = None

    @property
    def allowed(self) -> bool:
        return self.status != STATUS_REJECTED


def _read_jsonl_lines(path: str) -> list[str]:
    """Read a JSONL file into lines, fail-soft (missing / unreadable → ``[]``)."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [ln for ln in text.splitlines() if ln.strip()]


def turn_token_total(lines: Iterable[str]) -> int:
    """Sum the per-turn token usage across transcript JSONL lines. PURE.

    Reads ``message.usage`` from each record and sums ``input_tokens`` +
    ``output_tokens`` + ``cache_read_input_tokens`` + ``cache_creation_input_tokens``
    (the fields a real assistant record carries — user records carry an empty ``usage`` and
    contribute nothing). A torn / non-JSON line is skipped, never raised — the WAL
    torn-tail discipline. Returns the cumulative token count (0 when nothing parses)."""
    total = 0
    for ln in lines:
        try:
            rec = json.loads(ln)
        except (ValueError, TypeError):
            continue
        if not isinstance(rec, dict):
            continue
        msg = rec.get("message")
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if not isinstance(usage, dict):
            continue
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            v = usage.get(key)
            if isinstance(v, (int, float)):
                total += int(v)
    return total


def transcript_token_total(transcript_path: str) -> int:
    """Cumulative tokens spent in a session transcript. Fail-soft I/O wrapper of
    :func:`turn_token_total` (missing / torn file → 0)."""
    if not transcript_path:
        return 0
    return turn_token_total(_read_jsonl_lines(transcript_path))


def _breaker_pressure(cfg, session_id: str) -> Optional[float]:
    """The current seat's failure-pressure utilization from the breaker store, or None.

    Reads the session's persisted ``BreakerCounts`` (``stop_failure_sensor``). ``consecutive``
    API failures this run map to a pressure fraction ``consecutive / _PRESSURE_FULL`` clamped
    to 1.0. Returns None (no signal) when there is no breaker state or the count is zero — a
    clean session contributes nothing, so it can never be spuriously rotated. Never raises."""
    if not session_id:
        return None
    try:
        from dos import stop_failure_sensor as _sfs

        counts = _sfs.load_counts(session_id, cfg)
    except Exception:  # noqa: BLE001 — a sensor fault is "no signal", not a crash
        return None
    consecutive = int(getattr(counts, "consecutive", 0) or 0)
    if consecutive <= 0:
        return None
    return min(1.0, consecutive / float(_PRESSURE_FULL))


def assess_current_seat(
    cfg,
    session_id: str,
    *,
    transcript_path: str = "",
    near_cap_util: float = 0.9,
    window_token_cap: int = 0,
    reset_at_epoch: Optional[int] = None,
) -> Optional[ProbeSnapshot]:
    """Read the live near-cap signal for the seat ``session_id`` is running on, or None.

    Combines the two available signals (breaker pressure, opt-in token budget) into one
    :class:`ProbeSnapshot`, taking the MAX utilization across whichever signals were seen.
    The status is derived from the combined utilization against ``near_cap_util``:

      * a recorded breaker WALL (pressure at full, ``consecutive >= _PRESSURE_FULL``)
        → ``rejected`` (``allowed == False`` — the seat is walling, treat as WALLED);
      * utilization ``>= near_cap_util`` → ``allowed_warning`` (NEAR_CAP);
      * else → ``allowed`` (SERVING).

    ``window_token_cap`` (host-supplied, e.g. the plan's rolling-window token ceiling) opts
    the token-budget leg in; ``0`` skips it (no fabricated fraction). ``reset_at_epoch`` is
    passed through verbatim — a host that knows the window reset injects it; otherwise None.

    Returns **None** when NO signal is available (clean breaker AND no configured cap, or
    every read failed) — the fail-open contract: ``account_state`` reads a None probe as
    SERVING, so a healthy session is never rotated on a phantom wall."""
    utils: list[float] = []
    walled = False

    pressure = _breaker_pressure(cfg, session_id)
    if pressure is not None:
        utils.append(pressure)
        if pressure >= 1.0:
            walled = True

    if window_token_cap and window_token_cap > 0 and transcript_path:
        try:
            spent = transcript_token_total(transcript_path)
        except Exception:  # noqa: BLE001 — a read fault is "no token signal"
            spent = 0
        if spent > 0:
            utils.append(min(1.0, spent / float(window_token_cap)))

    if not utils:
        return None  # no signal at all → fail-open (account_state treats None as serving)

    util = max(utils)
    if walled or util >= 1.0:
        status = STATUS_REJECTED
    elif util >= near_cap_util:
        status = STATUS_WARNING
    else:
        status = STATUS_ALLOWED
    return ProbeSnapshot(status=status, utilization=util, reset_at_epoch=reset_at_epoch)


__all__ = [
    "ProbeSnapshot",
    "turn_token_total",
    "transcript_token_total",
    "assess_current_seat",
    "STATUS_ALLOWED",
    "STATUS_WARNING",
    "STATUS_REJECTED",
]
