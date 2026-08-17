"""`cooldown` — the anti-churn / per-pick cooldown verdict (docs/207 §3).

The single highest-leverage anti-churn mechanism, and the hardest lesson the
`job` repo learned: a bare dispatch loop **re-picked the same drained unit every
iteration** once its claim TTL lapsed — measured at ~1/21 runs actually shipping
(~5%), the rest burning money re-confirming a known drain. The cure is a
*cross-run memory*: "have I already tried this unit recently, and it didn't
move?" `liveness` asks "is THIS run moving?"; `cooldown` asks "have I *already
tried* this unit and it didn't move?" — the anti-churn sibling.

Why it needs a durable record (not the lease)
==============================================

The soft-claim a pick gets at selection time IS an attempt record — but it is
DELETED when it lapses: a fanout that DRAINs leaves a soft-claim that simply
expires and is swept out. By the next loop iteration the footprint is gone, so
the unit looks fresh and gets re-picked. So the attempt must be stamped to a
durable record that OUTLIVES the claim — the `lane_journal` `OP_ATTEMPT` event
(docs/207 §3a), folded HERE.

Outcome-awareness without a per-pick verdict
=============================================

The operator wants: a long cooldown after DRAINED/BLOCKED (the waste), NONE
after a genuine SHIPPED, a fast retry after a partial-progress ERROR. We get all
three from the attempt's recorded ``outcome`` alone (relocated from the `job`
`pick_cooldown` reference):

  * ``SHIPPED``           → the unit is on the non-forgeable rung; it leaves the
                            residual entirely (the picker's ship-detection already
                            excludes it), so the cooldown is moot — pre-screened
                            out, never `RECENTLY_ATTEMPTED`.
  * ``DRAINED`` / ``BLOCKED`` → not shipped, recently tried → cooldown HOLDS for
                            the window. THE storm case.
  * ``ERROR``             → a partial-progress failure; a SHORTER backoff window
                            (a fast retry — the failure may clear on its own),
                            declarable per-outcome in `[cooldown]`.

⚓ Pure; host gathers state. `cooldown_verdict(unit_id, attempt_history, *,
now_ms, policy)` makes no file/git/clock call — the host reads the ATTEMPT
events (`lane_journal.read_all` filtered to `op == "ATTEMPT"`) and the clock at
the boundary and hands them in, exactly like `liveness.classify`. So the
re-pick-storm backtest replays on a frozen attempt list with no disk.

⚓ Observability-grade, fail-open. A missing/garbled attempt row only means a
unit that should cool down doesn't (it gets re-picked — the pre-fix behavior);
it NEVER blocks a legitimate dispatch and never corrupts state. So the fold
treats an unreadable row as "no attempt" and a too-new schema as "ignore this
row," never a refusal that could wedge a clean unit. (Contrast the durable-READ
refuse-don't-guess floor, which protects a *correctness* record; a cooldown is a
hint, so the safe direction is the opposite — degrade to re-pickable.)

⚓ The verdict feeds `pickable` through the SAME key it already reads. A
`RECENTLY_ATTEMPTED` verdict carries an ``until_ms`` wall; the host writes it as
the unit's ``cooldown_until_ms``, which `pickable.classify` already turns into a
`HoldReason.COOLDOWN`. So the producer (here) and the consumer (`pickable`,
shipped) meet at one field — no new consumer, no drift.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from dos import durable_schema as _schema

# The lane-journal schema family/version this fold reads ATTEMPT records under.
# Inlined (NOT imported from `dos.lane_journal`) on purpose: `lane_journal` imports
# `dos.config`, and `config` carries the `[cooldown]` seam — importing the constants
# from `lane_journal` here would close a config→cooldown→lane_journal→config import
# cycle. These mirror `lane_journal.SCHEMA_FAMILY` / `LANE_JOURNAL_SCHEMA`; a test
# pins them equal so they can never silently drift (`tests/test_cooldown.py`).
_LJ_FAMILY = "lane-journal"
_LJ_SCHEMA = 1


# ---------------------------------------------------------------------------
# AttemptOutcome — the closed set of pick-attempt outcomes the fold reads.
# ---------------------------------------------------------------------------


class AttemptOutcome(str, enum.Enum):
    """The recorded outcome of a pick attempt — what the `OP_ATTEMPT` event carries.

    `str`-valued so it round-trips through the journal's JSON token. The fold keys
    its backoff on this: SHIPPED is pre-screened out (moot); DRAINED/BLOCKED earn
    the full window (the storm case); ERROR earns a shorter fast-retry window.
    """

    SHIPPED = "shipped"     # the attempt shipped — leaves the residual, cooldown moot
    DRAINED = "drained"     # the attempt drained (no pickable phase) — full window
    BLOCKED = "blocked"     # the attempt was blocked (a gate/claim) — full window
    ERROR = "error"         # a partial-progress failure — short fast-retry window
    UNKNOWN = "unknown"     # outcome not recorded — treated as the full window (conservative)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value

    @classmethod
    def coerce(cls, value: object) -> "AttemptOutcome":
        """Map a recorded token to an outcome; an unrecognized token → UNKNOWN."""
        s = str(value or "").strip().lower()
        for m in cls:
            if m.value == s:
                return m
        return cls.UNKNOWN


# Outcomes that PRE-SCREEN a unit out of the cooldown set entirely — it is not a
# "recently tried and didn't move" case, it MOVED (shipped), so the cooldown is moot.
_PRESCREENED: frozenset[AttemptOutcome] = frozenset({AttemptOutcome.SHIPPED})


# ---------------------------------------------------------------------------
# CooldownPolicy — the `[cooldown]` data (window + per-outcome backoff).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CooldownPolicy:
    """The cooldown windows — policy, not mechanism (the `[stamp]` data split).

      * ``window_ms`` — the default cooldown window after a DRAINED/BLOCKED/UNKNOWN
        attempt. Default 6h (the `job` value the operator tuned: long enough to
        break the per-iteration re-pick storm — loops re-pick every ~10–50 min —
        short enough that a genuinely-unblocked unit returns the same session).
      * ``error_window_ms`` — the shorter window after an ERROR (partial-progress)
        attempt: a fast retry, since the failure may clear on its own. Default 30m.

    Declared per-workspace in `dos.toml [cooldown]`. The defaults are GENERIC.
    """

    window_ms: int = 6 * 60 * 60 * 1000          # 6h
    error_window_ms: int = 30 * 60 * 1000        # 30m

    def window_for(self, outcome: AttemptOutcome) -> int:
        """The backoff window (ms) this outcome earns."""
        if outcome is AttemptOutcome.ERROR:
            return self.error_window_ms
        return self.window_ms

    def to_dict(self) -> dict:
        return {"window_ms": self.window_ms, "error_window_ms": self.error_window_ms}


DEFAULT_COOLDOWN_POLICY = CooldownPolicy()


# ---------------------------------------------------------------------------
# Cooldown — the typed verdict.
# ---------------------------------------------------------------------------


class CooldownState(str, enum.Enum):
    """Whether a unit is in a cooldown window right now (docs/207 §3b)."""

    CLEAR = "CLEAR"                          # no recent attempt holds it — offerable
    RECENTLY_ATTEMPTED = "RECENTLY_ATTEMPTED"  # tried recently, didn't move — skip until the wall

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class Cooldown:
    """The typed cooldown verdict + the derivation.

    ``state`` is the typed `CooldownState`. ``last_ts`` is the newest attempt's
    stamp (or ``""`` when none). ``count`` is how many recent attempts folded.
    ``until_ms`` is the cooldown wall (0 when CLEAR) — the host writes it as the
    unit's ``cooldown_until_ms``, which `pickable.classify` already reads.
    ``reason`` is the operator-facing one-liner.
    """

    state: CooldownState
    unit_id: str
    last_ts: str = ""
    count: int = 0
    until_ms: int = 0
    reason: str = ""

    @property
    def held(self) -> bool:
        """True iff the unit is RECENTLY_ATTEMPTED (skip it this iteration)."""
        return self.state is CooldownState.RECENTLY_ATTEMPTED

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "unit_id": self.unit_id,
            "last_ts": self.last_ts,
            "count": self.count,
            "until_ms": self.until_ms,
            "reason": self.reason,
        }

    @classmethod
    def CLEAR(cls, unit_id: str, *, count: int = 0, last_ts: str = "", reason: str = "") -> "Cooldown":
        return cls(state=CooldownState.CLEAR, unit_id=unit_id, count=count,
                   last_ts=last_ts, until_ms=0,
                   reason=reason or "no recent attempt holds this unit — offerable")


# ---------------------------------------------------------------------------
# cooldown_verdict — the pure fold over a unit's attempt history.
# ---------------------------------------------------------------------------


def _attempt_ms(record: Mapping, key: str) -> Optional[int]:
    """Read a ms-epoch timestamp from an attempt record's `<key>` (or `*_ms`),
    tolerating a missing/garbled value (fail-open → None)."""
    v = record.get(key)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def cooldown_verdict(
    unit_id: str,
    attempt_history: Iterable[Mapping],
    *,
    now_ms: int,
    policy: CooldownPolicy = DEFAULT_COOLDOWN_POLICY,
) -> Cooldown:
    """Decide whether `unit_id` is in a cooldown window now. PURE — no I/O.

    ``attempt_history`` is the bag of `OP_ATTEMPT` records the host gathered
    (`lane_journal.read_all` filtered to `op == "ATTEMPT"`); each carries
    ``unit_id``, ``outcome``, and a ms-epoch ``attempted_at_ms`` (the host stamps
    it from the journal `ts`, or passes one explicitly). ``now_ms`` is the caller's
    clock (an input, never read here — the `liveness.classify` discipline).

    The fold (docs/207 §3b), outcome-aware:

      1. Filter to records for THIS unit (fail-open: a record missing/garbling its
         schema beyond this kernel's ceiling is SKIPPED, never a refusal — a
         cooldown is a hint, the safe direction is re-pickable).
      2. If the unit's MOST RECENT attempt is a pre-screened outcome (SHIPPED), it
         MOVED — return CLEAR (the cooldown is moot; the picker's ship-detection
         excludes it anyway).
      3. Otherwise take the most recent attempt's wall = ``attempted_at_ms +
         policy.window_for(outcome)``. If ``now_ms`` is strictly before that wall →
         `RECENTLY_ATTEMPTED(until_ms=wall)`; else `CLEAR` (the window elapsed).

    Returns `Cooldown.CLEAR(...)` when no attempt holds the unit. Never raises.
    """
    uid = str(unit_id)
    mine: list[tuple[int, AttemptOutcome, str]] = []
    for rec in attempt_history or ():
        if not isinstance(rec, Mapping):
            continue
        if str(rec.get("op") or "") not in ("", "ATTEMPT"):
            # Not an attempt record (the host should pre-filter, but be defensive).
            continue
        if str(rec.get("unit_id") or "") != uid:
            continue
        # Fail-open schema gate: a too-new / wrong-family record is SKIPPED, never a
        # refusal — the cooldown is observability, so an unreadable row degrades to
        # "no attempt" (re-pickable), the opposite of the correctness-read floor.
        v = _schema.classify(rec, family=_LJ_FAMILY, understands=_LJ_SCHEMA)
        if v.readability not in (_schema.Readability.READABLE, _schema.Readability.UNTAGGED):
            continue
        at = _attempt_ms(rec, "attempted_at_ms")
        if at is None:
            continue
        outcome = AttemptOutcome.coerce(rec.get("outcome"))
        ts = str(rec.get("ts") or rec.get("attempted_at") or "")
        mine.append((at, outcome, ts))

    if not mine:
        return Cooldown.CLEAR(uid)

    # Pick the most recent attempt — but break a timestamp TIE deterministically,
    # never by input order. Two attempts can share an `attempted_at_ms` (same-ms
    # journal stamps); a plain time sort is stable, so `mine[-1]` would then be
    # whichever tied row arrived last in the bag, making the verdict depend on the
    # order the host happened to gather the records (a real non-determinism — see
    # `tests/test_prop_cooldown.py::test_verdict_is_invariant_under_history_order`).
    # The order-free, intent-preserving tie-break is the LONGEST wall: among the
    # tied-latest attempts, take the one whose window holds longest (DRAINED/BLOCKED's
    # 6h beats ERROR's 30m). That is the module's documented "fail toward cooldown"
    # bias (hold rather than re-pick on ambiguity), and it is invariant under order.
    count = len(mine)
    last_at = max(at for at, _, _ in mine)
    last_at, last_outcome, last_ts = max(
        (t for t in mine if t[0] == last_at),
        key=lambda t: (policy.window_for(t[1]), t[2]),
    )

    # Pre-screen: a SHIPPED most-recent attempt means the unit moved — cooldown moot.
    if last_outcome in _PRESCREENED:
        return Cooldown.CLEAR(
            uid, count=count, last_ts=last_ts,
            reason=(f"most recent attempt SHIPPED ({last_ts}) — the unit moved, "
                    f"cooldown is moot (it leaves the residual)"),
        )

    wall = last_at + policy.window_for(last_outcome)
    if now_ms < wall:
        return Cooldown(
            state=CooldownState.RECENTLY_ATTEMPTED,
            unit_id=uid,
            last_ts=last_ts,
            count=count,
            until_ms=wall,
            reason=(
                f"attempted {count}× (last {last_outcome.value} at {last_ts}); the "
                f"{policy.window_for(last_outcome) // 60000}m cooldown window has not "
                f"elapsed (until {wall}ms, now {now_ms}ms) — skip, do not re-dispatch"
            ),
        )
    return Cooldown.CLEAR(
        uid, count=count, last_ts=last_ts,
        reason=(f"attempted {count}× (last {last_outcome.value} at {last_ts}); the "
                f"cooldown window has elapsed — offerable again"),
    )


# ---------------------------------------------------------------------------
# The `[cooldown]` config seam — modelled on `dos.stamp` / `dos.enumerate`.
# ---------------------------------------------------------------------------


def policy_from_table(
    table: dict, *, base: CooldownPolicy = DEFAULT_COOLDOWN_POLICY
) -> CooldownPolicy:
    """Build a `CooldownPolicy` from a parsed `[cooldown]` TOML table. PURE.

    Each field the table names overrides ``base``; omitted fields inherit. An
    unknown key raises (the `stamp.convention_from_table` posture). Windows may be
    declared in ms directly (``window_ms``) or in hours (``window_hours``, ergonomic).
    """
    if not isinstance(table, dict):
        raise ValueError(f"[cooldown] must be a table, got {type(table).__name__}")
    known = {"window_ms", "window_hours", "error_window_ms", "error_window_minutes"}
    unknown = set(table) - known
    if unknown:
        raise ValueError(
            f"[cooldown] has unknown key(s) {sorted(unknown)}; known keys are {sorted(known)}"
        )

    def _int(key: str) -> Optional[int]:
        if key not in table:
            return None
        v = table[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"[cooldown].{key} must be a number, got {type(v).__name__}")
        return int(v)

    window_ms = base.window_ms
    if "window_ms" in table:
        window_ms = _int("window_ms")  # type: ignore[assignment]
    elif "window_hours" in table:
        window_ms = int(_int("window_hours") * 60 * 60 * 1000)  # type: ignore[operator]
    error_window_ms = base.error_window_ms
    if "error_window_ms" in table:
        error_window_ms = _int("error_window_ms")  # type: ignore[assignment]
    elif "error_window_minutes" in table:
        error_window_ms = int(_int("error_window_minutes") * 60 * 1000)  # type: ignore[operator]
    return CooldownPolicy(window_ms=window_ms, error_window_ms=error_window_ms)


def load_from_toml(
    path, *, base: CooldownPolicy = DEFAULT_COOLDOWN_POLICY
) -> CooldownPolicy:
    """Build a `CooldownPolicy` from a `dos.toml`'s `[cooldown]` table.

    Returns ``base`` unchanged when the file is absent, has no `[cooldown]` table,
    or `tomllib` is unavailable. A present-but-malformed table raises. Mirrors
    `stamp.load_from_toml` (incl. the `utf-8-sig` BOM strip)."""
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return base
    # ONE shared, mtime-keyed parse (`_tomlcache`) - collapses the per-config-layer
    # re-read/re-parse storm on `dos.toml`. A malformed file still raises here
    # (uncached), so the caller's existing handling is unchanged; the missing-file
    # guard above is untouched. The utf-8-sig BOM strip lives inside the helper.
    from dos._tomlcache import read_toml_cached
    data = read_toml_cached(p)
    table = data.get("cooldown")
    if not isinstance(table, dict) or not table:
        return base
    return policy_from_table(table, base=base)
