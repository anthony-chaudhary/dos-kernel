"""live_rotation — the PURE decision: *this LIVE seat is at its wall; rotate it, now.*

docs/391 — the proactive half of the rotate-on-wall arc (docs/386 §4). The landed
machinery rotates a seat REACTIVELY: a session dies on an API wall (``StopFailure``),
the breaker backs off, and the ``rotation_handoff`` WRITE/APPLY pair relaunches the
*dead* session under a serving seat at the next ``SessionStart``. That heals a session
that already fell over. It does nothing for a session that is still RUNNING and merely
*approaching* its cap — the one the operator actually wants caught BEFORE it walls.

This module is the kernel verdict for that live case. It is ``liveness`` /
``productivity`` / ``breaker``'s shape — a pure fold over already-gathered state — but
for a different question. Where those ask "is the run moving / productive / failing too
often?", live_rotation asks:

    given the seat a LIVE session is on (folded to SERVING / NEAR_CAP / WALLED) and the
    other seats available to it, should that session HOLD where it is, ROTATE to a named
    serving seat, or WAIT (every alternate is walled too)?

The kernel computes the verdict from folded facts; the host ACTS on it (write a
rotation handoff so the next boundary relaunches under the named seat; surface a
``--resume`` continuation an operator can run same-window; or, headless, relaunch the
child itself). live_rotation never touches disk, a clock, or a process — exactly the
advisory line ``liveness`` / ``breaker`` hold.

**The hard constraint it is honest about (docs/380).** A live process's environment
cannot be mutated from outside it, and a 429 does not make the CLI re-read its creds
file — so a genuinely same-process account swap of a *running* session is impossible
without a relaunch. ``ROTATE`` therefore names the seat to relaunch under at the next
boundary; it does not pretend to hot-swap the current process. The win is that the
decision is COMPUTED the instant the live session crosses near-cap (this module), so the
relaunch is ready before the wall, instead of after the session has already died.

**The thundering-herd defence (the DoS-relevant pure piece).** When MANY live sessions
cross near-cap at once — a whole fleet pinned to accounts that all roll their 5-hour
window together — the naive move is for every one of them to ``decide`` independently and
all pick the SAME lowest-roster serving alternate, instantly walling it (a stampede that
turns one wall into two). ``plan_fleet`` is the population answer: it SPREADS the rotating
sessions across the distinct serving alternates by remaining headroom (the
``allocate_seats`` discipline), so N rotating sessions fan out across the pool instead of
piling onto seat[0]. Pure and deterministic — no clock, no RNG — so the spread is
replay-testable on frozen fixtures and a stress harness can pin "no alternate is handed
more than its headroom share."

PURE — no I/O, no clock (``now_epoch`` injected for the wait math), no host vocabulary.
py.typed.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional, Sequence

# The folded per-seat kind strings. These MIRROR
# ``dos.drivers.account_switcher.ACCT_*`` deliberately — this module is the kernel
# verdict and must NOT import the driver (the kernel→driver layering floor, the same
# reason ``provider_limit`` mirrors its upstream taxonomies by VALUE rather than
# importing them). ``test_live_rotation`` pins these equal to the driver's, so the
# two can never silently drift.
KIND_SERVING = "serving"      # probe allowed, utilization < near_cap — route freely
KIND_NEAR_CAP = "near_cap"    # serving but utilization >= near_cap — rotate if we can
KIND_WALLED = "walled"        # probe rejected — this seat cannot serve right now

# When a session is WALLED and carries no parseable reset (and the host injects no
# wait floor), this is the wait the verdict reports — a typical Max 5-hour window.
# The kernel owns no backoff policy beyond this floor (the ``account_switcher``
# ``_NO_HINT_WAIT_SECONDS`` value, kept equal by a test).
_NO_HINT_WAIT_SECONDS = 1800

# Minimum headroom credited to a serving alternate when spreading a herd, so the
# fan-out never hands a probed-at-cap seat literally zero of the rotating sessions
# while it is the only thing serving — but far fewer than a near-empty one. Mirrors
# ``account_switcher._MIN_HEADROOM``.
_MIN_HEADROOM = 0.05


@dataclass(frozen=True)
class Seat:
    """A live seat folded to the few facts the verdict needs — duck-typed, driver-free.

    The host folds its own ``account_switcher.AccountState`` into this via
    :func:`from_account_state` (the one place that touches the driver shape), so this
    module reasons over a flat, frozen, replay-testable record and imports no driver.

      ``name``           — the roster label (the thing ``ROTATE`` names / a handoff keys on).
      ``kind``           — one of ``KIND_SERVING`` / ``KIND_NEAR_CAP`` / ``KIND_WALLED``.
      ``utilization``    — the binding window's consumed fraction ∈ [0,1], or None (fail-open:
                           an un-probed seat reads as full headroom for the spread).
      ``reset_at_epoch`` — when the window next rolls (for the WALL_WAIT math), or None.
    """

    name: str
    kind: str
    utilization: Optional[float] = None
    reset_at_epoch: Optional[int] = None

    @property
    def headroom(self) -> float:
        """Remaining capacity ∈ [_MIN_HEADROOM, 1.0] — the spread weight.

        ``1 - utilization`` floored at ``_MIN_HEADROOM``; a None utilization (no live
        probe — fail-open) is credited FULL headroom 1.0, exactly as the switcher
        treats an un-probed account as cleanly serving."""
        if self.utilization is None:
            return 1.0
        return max(_MIN_HEADROOM, 1.0 - float(self.utilization))


def from_account_state(state: object) -> Seat:
    """Adapt a ``account_switcher.AccountState`` (or any duck-compatible object) → :class:`Seat`.

    The ONLY function aware of the driver's state shape (``.account.name`` / ``.kind`` /
    ``.utilization`` / ``.reset_at_epoch``), kept tiny and getattr-based so the kernel
    module stays import-free of the driver. Tolerant: a missing name folds to ``""`` (the
    caller's decision logic treats an unnamed seat as un-routable), a non-kind string is
    carried verbatim (the verdict's ``else`` arm handles an unexpected kind)."""
    account = getattr(state, "account", None)
    name = str(getattr(account, "name", "") or getattr(state, "name", "") or "")
    util = getattr(state, "utilization", None)
    try:
        util_f = float(util) if util is not None else None
    except (TypeError, ValueError):
        util_f = None
    reset = getattr(state, "reset_at_epoch", None)
    try:
        reset_i = int(reset) if reset is not None else None
    except (TypeError, ValueError):
        reset_i = None
    return Seat(
        name=name,
        kind=str(getattr(state, "kind", "") or ""),
        utilization=util_f,
        reset_at_epoch=reset_i,
    )


class LiveVerdict(str, enum.Enum):
    """What a live session should do about the seat it is on.

    ``str``-valued so it round-trips as a CLI token / exit-map without a lookup table
    (the ``breaker.BreakerState`` / ``provider_limit.ProviderLimit`` idiom).

      HOLD          — the seat is serving (or it is near-cap but the ONLY thing serving,
                      so there is nowhere better to go) — keep running here.
      ROTATE        — the seat is near-cap or walled AND a better serving seat exists —
                      relaunch under ``to_account`` at the next boundary.
      WALL_WAIT     — the seat is walled and EVERY alternate is walled too — there is no
                      seat to rotate to; wait ``wait_seconds`` for the soonest reset.
      NO_ALTERNATE  — the seat is walled and there are NO other enrolled seats at all —
                      a single-account roster has nothing to rotate to (distinct from
                      WALL_WAIT, where alternates exist but are all walled).
    """

    HOLD = "HOLD"
    ROTATE = "ROTATE"
    WALL_WAIT = "WALL_WAIT"
    NO_ALTERNATE = "NO_ALTERNATE"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class LiveRotationDecision:
    """The verdict for one live seat: what to do + where + why.

    ``to_account`` is set only for ``ROTATE`` (the serving seat to relaunch under).
    ``wait_seconds`` / ``reset_at_epoch`` are set only for ``WALL_WAIT``. ``trigger`` echoes
    the current seat's kind (``near_cap`` / ``walled``) so forensics can tell a proactive
    rotation (caught BEFORE the wall) from a reactive one (caught AT it) — the
    ``breaker.tripped_on`` echo-the-evidence discipline.
    """

    verdict: LiveVerdict
    reason: str
    to_account: Optional[str] = None
    from_account: str = ""
    trigger: str = ""
    wait_seconds: Optional[int] = None
    reset_at_epoch: Optional[int] = None

    @property
    def should_rotate(self) -> bool:
        return self.verdict is LiveVerdict.ROTATE

    def to_dict(self) -> dict:
        d: dict = {"verdict": self.verdict.value, "reason": self.reason}
        if self.to_account:
            d["to_account"] = self.to_account
        if self.from_account:
            d["from_account"] = self.from_account
        if self.trigger:
            d["trigger"] = self.trigger
        if self.wait_seconds is not None:
            d["wait_seconds"] = self.wait_seconds
        if self.reset_at_epoch is not None:
            d["reset_at_epoch"] = self.reset_at_epoch
        return d


def _rank_alternatives(alternatives: Sequence[Seat]) -> list[Seat]:
    """Serving alternates first (roster order), then near-cap (lowest-util first).

    The ``account_switcher.serving_pool`` order applied to the OTHER seats: a clean
    serving window is always preferred to a near-cap one, and among near-cap windows the
    least-loaded goes first. Walled alternates are dropped (they cannot serve). PURE."""
    serving = [a for a in alternatives if a.kind == KIND_SERVING]
    near = sorted(
        (a for a in alternatives if a.kind == KIND_NEAR_CAP),
        key=lambda a: (a.utilization if a.utilization is not None else 1.0),
    )
    return serving + near


def decide(
    current: Seat,
    alternatives: Sequence[Seat],
    *,
    now_epoch: float,
    wall_wait_floor: int = 0,
) -> LiveRotationDecision:
    """Decide what the live session on ``current`` should do. PURE — no I/O, no clock.

    ``alternatives`` are the OTHER seats available to this session (the current seat must
    NOT be in the list — the caller excludes it by name). ``now_epoch`` is injected for the
    WALL_WAIT reset math; ``wall_wait_floor`` clamps the reported wait up to a host floor.

    The order of preference mirrors ``pick_account``:

      * ``current`` SERVING                  → HOLD (nothing to do — plenty of headroom).
      * ``current`` NEAR_CAP / WALLED, and a serving (or near-cap) alternate exists
                                             → ROTATE to the best-ranked alternate.
      * ``current`` NEAR_CAP, NO alternate   → HOLD (it is the only thing serving — the
                                               ``pick_account`` near-cap fallback: a near-cap
                                               seat is still usable when it is all there is).
      * ``current`` WALLED, alternates exist but all walled
                                             → WALL_WAIT (wait the soonest reset).
      * ``current`` WALLED, NO alternates    → NO_ALTERNATE (single-account roster).
    """
    frm = current.name
    if current.kind == KIND_SERVING:
        return LiveRotationDecision(
            verdict=LiveVerdict.HOLD,
            reason=f"seat {frm!r} is serving — hold",
            from_account=frm,
            trigger=KIND_SERVING,
        )

    ranked = _rank_alternatives(alternatives)
    if ranked:
        best = ranked[0]
        util_note = (
            f" (util {current.utilization:.0%})"
            if current.utilization is not None
            else ""
        )
        kind_word = "near its cap" if current.kind == KIND_NEAR_CAP else "walled"
        return LiveRotationDecision(
            verdict=LiveVerdict.ROTATE,
            reason=(
                f"seat {frm!r} is {kind_word}{util_note} and {best.name!r} is serving — "
                f"rotate to {best.name!r}"
            ),
            to_account=best.name,
            from_account=frm,
            trigger=current.kind or KIND_NEAR_CAP,
        )

    # No alternate can serve. Distinguish "near-cap but it's all we have" (HOLD — still
    # usable) from "walled with nowhere to go" (WALL_WAIT / NO_ALTERNATE).
    if current.kind == KIND_NEAR_CAP:
        return LiveRotationDecision(
            verdict=LiveVerdict.HOLD,
            reason=(
                f"seat {frm!r} is near its cap but is the only serving seat — hold "
                "(no better alternate)"
            ),
            from_account=frm,
            trigger=KIND_NEAR_CAP,
        )

    # current is WALLED with no serving alternate.
    has_other_seats = len(alternatives) > 0
    if not has_other_seats:
        return LiveRotationDecision(
            verdict=LiveVerdict.NO_ALTERNATE,
            reason=f"seat {frm!r} is walled and the roster has no other seat — wait it out",
            from_account=frm,
            trigger=KIND_WALLED,
        )
    # Other seats exist but are all walled too — wait for the soonest reset across them.
    resets = [a.reset_at_epoch for a in alternatives if a.reset_at_epoch]
    if current.reset_at_epoch:
        resets.append(current.reset_at_epoch)
    soonest = min(resets) if resets else None
    if soonest is not None:
        wait = max(wall_wait_floor, int(soonest - now_epoch))
        if wait < 0:
            wait = 0
    else:
        wait = max(wall_wait_floor, _NO_HINT_WAIT_SECONDS)
    return LiveRotationDecision(
        verdict=LiveVerdict.WALL_WAIT,
        reason=(
            f"seat {frm!r} is walled and every alternate is walled too — "
            f"wait {wait}s for the soonest reset"
        ),
        from_account=frm,
        trigger=KIND_WALLED,
        wait_seconds=wait,
        reset_at_epoch=soonest,
    )


def spread_targets(n_sessions: int, alternatives: Sequence[Seat]) -> list[str]:
    """Fan ``n_sessions`` rotating sessions across the serving alternates by headroom. PURE.

    The thundering-herd defence. ``n_sessions`` live seats have all crossed near-cap and
    each, deciding alone, would pick the SAME best alternate (``_rank_alternatives[0]``) and
    instantly wall it. This spreads them in PROPORTION to each alternate's remaining
    headroom (``allocate_seats``' largest-remainder apportionment, ties broken by rank
    order), so a near-empty window absorbs more of the herd than a near-cap one and the
    fan-out maximises total headroom before any alternate walls.

    Returns a list of length ``n_sessions`` of account NAMES — element ``i`` is the seat the
    i-th rotating session should target. Empty when there is no serving alternate (the
    caller then keeps each session where it is / lets WALL_WAIT handle it) or
    ``n_sessions <= 0``. Deterministic: no clock, no RNG."""
    if n_sessions <= 0:
        return []
    ranked = _rank_alternatives(alternatives)
    if not ranked:
        return []
    weights = [a.headroom for a in ranked]
    total = sum(weights)
    if total <= 0:  # pragma: no cover - _MIN_HEADROOM keeps this positive
        weights = [1.0] * len(ranked)
        total = float(len(ranked))
    # Largest-remainder (Hamilton) apportionment of n_sessions across the weights.
    exact = [n_sessions * w / total for w in weights]
    floors = [int(x) for x in exact]
    remainder = n_sessions - sum(floors)
    order = sorted(
        range(len(ranked)),
        key=lambda i: (exact[i] - floors[i], weights[i]),
        reverse=True,
    )
    seats = list(floors)
    for k in range(remainder):
        seats[order[k % len(order)]] += 1
    # Interleave by descending headroom so consecutive sessions land on DISTINCT
    # alternates first (a herd smaller than the pool still fans across windows).
    by_headroom = sorted(range(len(ranked)), key=lambda i: weights[i], reverse=True)
    remaining = {i: seats[i] for i in range(len(ranked))}
    out: list[str] = []
    while len(out) < n_sessions and any(remaining.values()):
        for i in by_headroom:
            if remaining[i] > 0:
                out.append(ranked[i].name)
                remaining[i] -= 1
                if len(out) >= n_sessions:
                    break
    return out


def plan_fleet(
    sessions: Sequence[tuple],
    alternatives: Sequence[Seat],
    *,
    now_epoch: float,
    wall_wait_floor: int = 0,
) -> dict:
    """Decide a whole fleet at once, SPREADING the rotations (anti-stampede). PURE.

    ``sessions`` is a sequence of ``(session_id, current_seat)`` pairs — each a live session
    and the seat it is on. ``alternatives`` is the shared pool of OTHER seats (none of the
    sessions' current seats). Sessions whose current seat is serving get HOLD; the ones that
    need to rotate (NEAR_CAP / WALLED with a serving alternate) are fanned across the pool
    by :func:`spread_targets` so they do NOT all pick the same alternate and stampede it.

    Returns ``{session_id: LiveRotationDecision}``. A session whose current seat is walled
    with no serving alternate still gets its per-session WALL_WAIT / NO_ALTERNATE verdict
    (the spread only governs WHICH serving alternate a rotating session targets, never
    whether it rotates). Deterministic and order-stable."""
    decisions: dict = {}
    # First pass: per-session verdict (independent), collecting the rotating ones in order.
    rotating: list = []  # (session_id, current_seat)
    for sid, current in sessions:
        d = decide(
            current, alternatives, now_epoch=now_epoch, wall_wait_floor=wall_wait_floor
        )
        decisions[sid] = d
        if d.verdict is LiveVerdict.ROTATE:
            rotating.append((sid, current))
    if not rotating:
        return decisions
    # Second pass: re-target the rotating sessions across the pool by headroom, so the
    # herd fans out instead of all landing on _rank_alternatives[0].
    targets = spread_targets(len(rotating), alternatives)
    for (sid, current), target in zip(rotating, targets):
        prior = decisions[sid]
        decisions[sid] = LiveRotationDecision(
            verdict=LiveVerdict.ROTATE,
            reason=(
                f"seat {current.name!r} is {('near its cap' if current.kind == KIND_NEAR_CAP else 'walled')} "
                f"— rotate to {target!r} (fleet-spread across {len(set(targets))} alternate"
                f"{'s' if len(set(targets)) != 1 else ''})"
            ),
            to_account=target,
            from_account=current.name,
            trigger=prior.trigger,
        )
    return decisions


__all__ = [
    "Seat",
    "from_account_state",
    "LiveVerdict",
    "LiveRotationDecision",
    "decide",
    "spread_targets",
    "plan_fleet",
    "KIND_SERVING",
    "KIND_NEAR_CAP",
    "KIND_WALLED",
]
