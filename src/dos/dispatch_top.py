"""`dos top` — `top(1)` for a DOS fleet: who holds which lane, what just shipped, what's stuck.

The **live-ops** sibling of `dos decisions`. Where the decisions queue answers
"what is waiting on *me* right now," `dos top` answers "what is *running* right
now": one near-real-time screen of the lanes, the leases holding them, the recent
verdicts with a ship-oracle trust cross-check, and any lane that has stopped
moving. It is the screen an operator leaves open in a side terminal during a
fleet run — the fleet watchdog the closed-loop-control thesis wants a host to be
able to build, here as kernel-generic mechanism.

It is a **read-only projection** (the `decisions.py` discipline, restated for the
live axis): it stores nothing, mutates nothing, acquires no lease, launches no
agent. Every panel is a pure function over an in-memory payload; the only I/O is
`snapshot()` at the boundary, which reads four already-persisted sources and
freezes them. Delete this module and you lose the screen, not any data.

**Why it is kernel-generic (works in a random new repo).** job's `dispatch_top`
read its lease world from `fanout_state.py` + `execution-state.yaml` — host
workflow the kernel is fenced from. This reads the kernel's *own* lease world
instead:

    lanes      <- config.lanes              (the generic `main`/`global` default;
                                             a workspace's `dos.toml [lanes]` wins)
    leases     <- lane_journal.replay(...)  (the WAL folded to the live-lease set —
                                             the same rows execution-state.yaml held)
    liveness   <- liveness.classify(...)    (per-lane ADVANCING/SPINNING/STALLED,
                                             the kernel verdict, not a host health)
    verdicts   <- .verdict-*.json           (recent no-pick/ship envelopes)
    activity   <- git_delta.recent_commits  (so a zero-lease repo still has content)

Nothing here imports a host. In a freshly-`dos init`'d checkout there are no
leases and no verdicts yet — every reader returns empty and the screen shows the
lane roster (all FREE) plus the git-activity strip. That is the headline
contract, pinned in `tests/test_dispatch_top.py`: `snapshot()` against a plain
git repo with no `dos.toml`, no journal, no plan returns a renderable frame.

The rich live-redraw skin + the poll loop live in `dispatch_top_tui` (behind the
`[tui]` extra); this module is import-light and dependency-free so the plain-text
renderers are always available — the floor that works everywhere, exactly the
`decisions` / `decisions_tui` split.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import sys
from dataclasses import dataclass
from typing import Iterable

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # pragma: no cover
        pass
elif not isinstance(sys.stdout, io.TextIOWrapper):  # pragma: no cover
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from dos import config as _config
from dos import git_delta
from dos import lane_journal
from dos import liveness as _liveness


# ---------------------------------------------------------------------------
# Status chips — the per-lane verdict, collapsed to one glyph the operator reads
# at a glance. A held lane takes its chip from the kernel liveness verdict; a
# lane with no lease is FREE. This is the kernel-honest upgrade over job's
# dispatch-top, which read STALLED/ORPHANED_WORKING/DEAD from fanout_state's
# audit: here the chip IS `liveness.classify`, the 4th distrust syscall.
# ---------------------------------------------------------------------------

CHIP_ADVANCING = "🟢 ADVANCING"   # held + liveness says ground-truth state moved
CHIP_SPINNING = "🟡 SPINNING"     # held + alive but not moving (burning tokens)
CHIP_STALLED = "🔴 STALLED"       # held + no fresh heartbeat / no commits — dead/hung
CHIP_SPAWNING = "🔵 SPAWNING"     # a run is COMING — a recent OP_SPAWN, no lease yet
CHIP_FREE = "⚪ FREE"             # no lease on this lane

# liveness verdict -> chip. One home for the mapping so a new Liveness value
# surfaces here as a KeyError in tests rather than silently rendering blank.
_CHIP_BY_LIVENESS = {
    _liveness.Liveness.ADVANCING: CHIP_ADVANCING,
    _liveness.Liveness.SPINNING: CHIP_SPINNING,
    _liveness.Liveness.STALLED: CHIP_STALLED,
}

# Spend chips — the per-lane LATEST efficiency verdict, read from the verdict
# journal (docs/263 §6 + docs/300, issue #38). Orthogonal to the liveness chip: a
# lane can be ADVANCING (moving) yet WASTEFUL (the tokens bought little). Absent a
# recorded efficiency verdict the column renders blank, never an error — the
# row-3 read-only projection discipline (no lease, no mutation). The verdict TOKEN
# is the journal's own EfficiencyVerdict string, so a new value surfaces as a
# missing-chip blank rather than a crash, the same fail-soft posture as the rest
# of this module.
_SPEND_CHIP = {
    "EFFICIENT": "💚 EFFICIENT",
    "COSTLY": "🟠 COSTLY",
    "WASTEFUL": "🟤 WASTEFUL",
}

# How long a journaled OP_SPAWN keeps a lane reading SPAWNING before it ages out.
# A loop normally goes SPAWN→preflight→ACQUIRE in seconds; once the ACQUIRE lands a
# held lease WINS the chip (the spawning fold is no-live-lease-only). The TTL is the
# self-heal for the OTHER case — a launch that DIES in preflight, which never
# acquires: its SPAWN ages out on its own rather than wedging a phantom SPAWNING
# forever (the same self-heal `lane_lease._expire_dead` gives a crashed *holder*,
# here for a never-born one). 120s is generous for any real preflight while still
# clearing a dead launch within a couple of `dos top` polls.
SPAWN_TTL_MS = 120_000


# ---------------------------------------------------------------------------
# Time helpers (mirrors decisions.py — same tolerant ISO parse + compact age).
# ---------------------------------------------------------------------------


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_iso(ts: str | None) -> dt.datetime | None:
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _age_ms(ts: str | None, *, now: dt.datetime) -> int | None:
    """Age of an ISO stamp in milliseconds as of ``now`` (None if unparseable)."""
    t = _parse_iso(ts)
    if t is None:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return max(0, int((now - t).total_seconds() * 1000))


def _fmt_age(age_ms: int | None) -> str:
    """Compact age from milliseconds: 45s / 18m / 2h / 3d / '—' when unknown."""
    if age_ms is None:
        return "—"
    s = age_ms // 1000
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


# ---------------------------------------------------------------------------
# Lane roster — derived from config.lanes, never hardcoded (the drift the job
# DTOP6 guard existed to catch). A held lane outside the roster is surfaced last
# so a live lease can never be invisible (job DTOP1's "unknown-held-never-
# invisible" rule, restated against the generic taxonomy).
# ---------------------------------------------------------------------------


def lane_roster(config: _config.SubstrateConfig) -> list[str]:
    """The always-shown lane order: concurrent (cluster) lanes, then exclusive.

    Deduped, declaration-order-preserving. For the generic default this is
    ``["main", "global"]``; a workspace's `dos.toml [lanes]` replaces it. Never
    raises — an empty taxonomy yields ``[]`` and the screen renders "(no lanes)".
    """
    seen: set[str] = set()
    out: list[str] = []
    for lane in tuple(config.lanes.concurrent) + tuple(config.lanes.exclusive):
        if lane and lane not in seen:
            seen.add(lane)
            out.append(lane)
    return out


# ---------------------------------------------------------------------------
# Lane state model + the pure adapter from a live-lease payload.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LaneState:
    """One rendered lane row — pure data, no rich objects."""

    lane: str
    chip: str                         # one of the CHIP_* constants
    loop_ts: str = ""                 # holding loop/run ts, "" when FREE
    holder: str = ""                  # host:pid of the holder, "" when FREE
    heartbeat_age_ms: int | None = None
    liveness_reason: str = ""         # the liveness verdict's one-line reason
    is_exclusive: bool = False        # an exclusive lane (renders a marker)
    # The spend column (docs/263 §6, issue #38) — the lane's LATEST efficiency
    # verdict, read from the verdict journal. "" when none recorded (blank column).
    spend_chip: str = ""              # a _SPEND_CHIP value, or "" when none
    work: int | None = None           # the journal's evidence.work, when present
    tokens: int | None = None         # the journal's evidence.tokens, when present

    def to_dict(self) -> dict:
        return {
            "lane": self.lane,
            "chip": self.chip,
            "loop_ts": self.loop_ts,
            "holder": self.holder,
            "heartbeat_age_ms": self.heartbeat_age_ms,
            "liveness_reason": self.liveness_reason,
            "is_exclusive": self.is_exclusive,
            "spend_chip": self.spend_chip,
            "work": self.work,
            "tokens": self.tokens,
        }


def _lease_liveness(
    lease: dict, *, events_since: int, now: dt.datetime, policy
) -> _liveness.LivenessVerdict:
    """Classify one held lease with the kernel liveness verdict.

    The boundary builds `ProgressEvidence` from the lease row — the run-start is
    its `acquired_at`, the heartbeat age is `now - heartbeat_at`, and
    ``events_since`` is the count of state-mutating lane-journal events recorded
    *strictly after* this lease was acquired (its own ACQUIRE is the anchor, NOT
    progress — a lease that has only sat there since acquire has 0 events-since)
    — and hands it to the PURE `liveness.classify`. `commits_since_start` is 0
    here: a lease records no start SHA, so `dos top`'s Phase-1 liveness rung is
    the heartbeat/event signal (the honest floor LVN Phase 1 set; a SHA-anchored
    commit rung is a later enrichment, not needed for the watchdog screen). The
    clock is injected (`now_ms`), never read inside the verdict — the arbiter
    discipline.

    Consequence (the bug the first smoke-test caught): an idle just-acquired lease
    with a stale/absent heartbeat now correctly reads STALLED, not ADVANCING — its
    lone ACQUIRE no longer counts as forward motion.
    """
    now_ms = int(now.timestamp() * 1000)
    started_ms = _age_ms(lease.get("acquired_at"), now=now)
    run_started_ms = (now_ms - started_ms) if started_ms is not None else now_ms
    hb_age = _age_ms(lease.get("heartbeat_at") or lease.get("acquired_at"), now=now)
    ev = _liveness.ProgressEvidence(
        run_started_ms=run_started_ms,
        now_ms=now_ms,
        commits_since_start=0,
        journal_events_since=max(0, events_since),
        last_heartbeat_age_ms=hb_age,
    )
    return _liveness.classify(ev, policy)


def build_lane_states(
    payload: dict,
    *,
    roster: list[str],
    exclusive: tuple[str, ...] = (),
    now: dt.datetime | None = None,
    policy=None,
) -> list[LaneState]:
    """Pure adapter: (live-lease payload, roster) → ordered LaneState rows.

    ``payload`` is ``{"leases": [...], "events_by_lane": {lane: count},
    "spawning_by_lane": {lane: SpawnIntent}}`` — the shape `snapshot()` builds from
    `lane_journal.replay` + the journal folds. Every lane in ``roster`` appears
    exactly once; any *held* lane not in ``roster`` is appended last so a live lease
    is never invisible. A lane's chip is: the kernel liveness verdict when held; else
    SPAWNING when a recent OP_SPAWN says a run is coming (the SPAWN→ACQUIRE window);
    else FREE. The clock is passed in (pure given ``now``).
    """
    now = now or _now()
    policy = policy if policy is not None else _liveness.DEFAULT_POLICY
    leases_by_lane = {str(l.get("lane") or ""): l for l in payload.get("leases", [])}
    events_by_lane = payload.get("events_by_lane", {}) or {}
    spawning_by_lane = payload.get("spawning_by_lane", {}) or {}
    # The spend column (issue #38): {lane: LaneSpend} from the verdict journal. A
    # lane with no recorded efficiency verdict simply gets no chip (blank column).
    spend_by_lane = payload.get("spend_by_lane", {}) or {}

    def _state(lane: str, lease: dict | None) -> LaneState:
        excl = lane in exclusive
        if lease is None:
            # No live lease — but a recent OP_SPAWN means a run is COMING to this
            # lane (the blind SPAWN→ACQUIRE window). Surface it as SPAWNING so the
            # loop is visible the instant it commits to a lane, not only once it has
            # durably acquired. A held lease (above) always wins; a stale SPAWN has
            # already aged out of `spawning_by_lane`.
            intent = spawning_by_lane.get(lane)
            if intent is not None:
                return LaneState(
                    lane=lane,
                    chip=CHIP_SPAWNING,
                    holder=str(getattr(intent, "holder", "") or ""),
                    heartbeat_age_ms=getattr(intent, "age_ms", None),
                    liveness_reason="a run is spawning — no lease yet",
                    is_exclusive=excl,
                )
            return LaneState(lane=lane, chip=CHIP_FREE, is_exclusive=excl)
        verdict = _lease_liveness(
            lease,
            events_since=int(events_by_lane.get(lane, 0) or 0),
            now=now,
            policy=policy,
        )
        spend = spend_by_lane.get(lane)
        return LaneState(
            lane=lane,
            chip=_CHIP_BY_LIVENESS[verdict.verdict],
            loop_ts=str(lease.get("loop_ts") or ""),
            holder=str(lease.get("holder") or ""),
            heartbeat_age_ms=verdict.evidence.last_heartbeat_age_ms,
            liveness_reason=verdict.reason,
            is_exclusive=excl,
            # The spend column rides on the held lane; an unrecognized verdict token
            # maps to a blank chip (fail-soft), the work/tokens ride from the fossil.
            spend_chip=_SPEND_CHIP.get(spend.verdict, "") if spend else "",
            work=spend.work if spend else None,
            tokens=spend.tokens if spend else None,
        )

    states: list[LaneState] = []
    seen: set[str] = set()
    for lane in roster:
        seen.add(lane)
        states.append(_state(lane, leases_by_lane.get(lane)))
    for lane, lease in leases_by_lane.items():
        if lane and lane not in seen:
            seen.add(lane)
            states.append(_state(lane, lease))
    # A SPAWNING lane outside the roster (a launcher committed to a lane the
    # workspace taxonomy doesn't name) must also never be invisible — the same
    # rule that surfaces an unknown HELD lane, applied to an unknown COMING one.
    for lane in spawning_by_lane:
        if lane and lane not in seen:
            seen.add(lane)
            states.append(_state(lane, None))
    return states


# ---------------------------------------------------------------------------
# Recent verdicts — the .verdict-*.json envelopes, newest-first. dos top shows
# ALL recent verdicts (a ship/accept as well as a wedge), unlike the decisions
# queue which keeps only the refusal-shaped ones; the trust column cross-checks a
# claimed ship against the oracle (evidence-over-narrative as a UI affordance).
# ---------------------------------------------------------------------------

TRUST_OK = "✓oracle"        # the oracle confirms the claimed pick shipped
TRUST_PENDING = "·pending"  # an accept/launchable verdict the oracle hasn't seen ship
TRUST_NA = "—"              # nothing to verify (a no-pick wedge/drain)


@dataclass(frozen=True)
class VerdictRow:
    """One recent verdict envelope, normalized to a display row."""

    tag: str
    lane: str
    verdict: str                 # ACCEPT | WEDGE | DRAIN | … (envelope's own token)
    reason_token: str = ""       # closed reason_class when present
    pick: str = ""               # "PLAN PHASE" of the lead pick, when present
    trust: str = TRUST_NA
    age_ms: int | None = None

    def to_dict(self) -> dict:
        return {
            "tag": self.tag, "lane": self.lane, "verdict": self.verdict,
            "reason_token": self.reason_token, "pick": self.pick,
            "trust": self.trust, "age_ms": self.age_ms,
        }


def _envelope_lane(env: dict) -> str:
    # Normalize to the bare dynamic lane handle (dos/119) so a curated-cluster
    # relic scope ("apply cluster (AFR, …)") renders as its handle, identically to
    # the operator-decision queue (`decisions._dynamic_lane_handle`) — one
    # normalizer, two readers can't drift.
    from dos.decisions import _dynamic_lane_handle
    scope = env.get("scope")
    if isinstance(scope, dict):
        return _dynamic_lane_handle(str(scope.get("lane") or scope.get("label") or ""))
    if isinstance(scope, str):
        return _dynamic_lane_handle(scope)
    return _dynamic_lane_handle(str(env.get("lane") or ""))


def _envelope_lead_pick(env: dict) -> str:
    for key in ("picks", "intended_picks"):
        for p in env.get(key, []) or []:
            if isinstance(p, dict):
                plan = str(p.get("plan_id") or "").strip()
                phase = str(p.get("phase_id") or "").strip()
                if plan:
                    return f"{plan} {phase}".strip()
    return ""


def parse_verdict_envelope(env: dict, tag: str, *, now: dt.datetime) -> VerdictRow:
    """Pure: one parsed `.verdict-<tag>.json` dict → a VerdictRow (no trust yet).

    Handles both shapes the spine writes: the clean ACCEPT envelope
    (``all_clear``/``picks``, no explicit ``verdict``) and the WEDGE envelope
    (``verdict``/``reason_class``/``intended_picks``). Trust is attached
    separately (`attach_trust`) so this stays pure and the oracle call is an
    injected boundary, exactly as job's DTOP2 kept `attach_trust` over an injected
    `verify`.
    """
    verdict = str(env.get("verdict") or "").strip().upper()
    if not verdict:
        if env.get("all_clear") and not env.get("blocked"):
            verdict = "ACCEPT"
        elif env.get("blocked"):
            verdict = "WEDGE"
        else:
            verdict = "UNKNOWN"
    return VerdictRow(
        tag=tag,
        lane=_envelope_lane(env),
        verdict=verdict,
        reason_token=str(env.get("reason_class") or "").strip().upper(),
        pick=_envelope_lead_pick(env),
        age_ms=_age_ms(env.get("generated_at") or env.get("ts"), now=now),
    )


def attach_trust(row: VerdictRow, verify) -> VerdictRow:
    """Attach the ship-oracle trust chip to a verdict row over an injected ``verify``.

    ``verify`` is a ``(plan, phase) -> bool`` shipped-check (the live path wires
    `oracle.is_shipped`; tests inject a fake). A verdict with no pick has nothing
    to verify (TRUST_NA). A launchable/accept verdict whose pick the oracle has
    not yet seen ship reads ·pending (informational, NOT a false-ship warn — the
    correction job's DTOP2 made: an ACCEPT is a go-ahead, not a ship claim). A
    pick the oracle confirms reads ✓oracle. Never raises — a verify that throws
    degrades the row to its current trust (fail-safe).
    """
    if not row.pick or verify is None:
        return row
    parts = row.pick.split()
    plan, phase = parts[0], (parts[1] if len(parts) > 1 else "")
    try:
        shipped = bool(verify(plan, phase))
    except Exception:
        return row
    chip = TRUST_OK if shipped else TRUST_PENDING
    return VerdictRow(
        tag=row.tag, lane=row.lane, verdict=row.verdict,
        reason_token=row.reason_token, pick=row.pick, trust=chip, age_ms=row.age_ms,
    )


# ---------------------------------------------------------------------------
# The frame — everything one screen shows, as pure data. `snapshot()` builds it
# from disk; the renderers + the TUI consume it.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Frame:
    """A single rendered moment of `dos top` — pure, serializable, testable."""

    workspace: str
    now_iso: str
    lanes: tuple[LaneState, ...] = ()
    verdicts: tuple[VerdictRow, ...] = ()
    activity: tuple[dict, ...] = ()       # recent commits [{sha, subject}, …]
    initialized: bool = True              # did a dos.toml exist (vs. bare repo)?

    def to_dict(self) -> dict:
        return {
            "workspace": self.workspace,
            "now": self.now_iso,
            "initialized": self.initialized,
            "lanes": [s.to_dict() for s in self.lanes],
            "verdicts": [v.to_dict() for v in self.verdicts],
            "activity": [dict(c) for c in self.activity],
        }


_WORK_OPS = frozenset({
    lane_journal.OP_ACQUIRE, lane_journal.OP_RELEASE,
    lane_journal.OP_SCAVENGE, lane_journal.OP_RECONCILE,
})


def _entry_ts(e: dict) -> str:
    return str(e.get("ts") or e.get("heartbeat_at") or e.get("acquired_at") or "")


def _events_by_lane(entries: list[dict], live_by_lane: dict[str, dict]) -> dict[str, int]:
    """Count state-mutating lane-journal events recorded AFTER each lane's acquire.

    The liveness rung wants *forward* lease-layer work, not the ACQUIRE that created
    the currently-held lease (counting that would make every idle just-acquired lease
    read ADVANCING forever — the first-smoke-test bug). The establishing ACQUIRE is
    excluded by **identity** (the first ACQUIRE we see for this lane in append order
    is its birth), NOT by a `ts > acquired_at` timestamp compare — the exact fix
    `journal_delta.fold_since` adopted (docs/82): the timestamp rule only excluded
    the birth ACQUIRE when its `ts` was not strictly past `acquired_at`, but in a real
    grant those are TWO separate clock reads (`lane_lease.acquire` stamps
    `acquired_at`, then `lane_journal.append` stamps a strictly-later `ts`), so the
    birth ACQUIRE's `ts > acquired_at` is true and it was counted → a held-but-idle
    lane read ADVANCING forever. Identity-keyed birth-skip holds regardless of the
    clock relationship; a later RELEASE/SCAVENGE/RECONCILE or a genuine re-ACQUIRE is
    real work and still counts (a keepalive HEARTBEAT is a beat, not progress, and is
    not in `_WORK_OPS`). A lane with no live lease gets no entry (renders FREE). Pure
    over already-read entries + the replayed live-lease map.
    """
    out: dict[str, int] = {}
    seen_birth: set[str] = set()  # lanes whose establishing ACQUIRE we've skipped
    for e in entries:
        op = str(e.get("op") or "")
        if op not in _WORK_OPS:
            continue
        lane = str(e.get("lane") or "")
        live = live_by_lane.get(lane)
        if not lane or live is None:
            continue
        # Skip exactly the FIRST ACQUIRE for this lane (its birth); count everything
        # after — including a later re-ACQUIRE, which is real lease work.
        if op == lane_journal.OP_ACQUIRE and lane not in seen_birth:
            seen_birth.add(lane)
            continue
        out[lane] = out.get(lane, 0) + 1
    return out


@dataclass(frozen=True)
class LaneSpend:
    """One lane's latest recorded efficiency verdict (pure data; issue #38)."""

    verdict: str = ""            # EFFICIENT | COSTLY | WASTEFUL (the journal token)
    work: int | None = None      # evidence.work, when the fossil carries it
    tokens: int | None = None    # evidence.tokens, when the fossil carries it


def latest_efficiency_by_lane(events: Iterable) -> dict[str, LaneSpend]:
    """Fold verdict-journal events → the NEWEST efficiency verdict per lane. PURE.

    Reads ONLY `syscall == "efficiency"` events (docs/263), keyed by `lane`; the
    last one wins (events are appended oldest-first, so a later record overwrites an
    earlier — the "latest verdict" the operator wants). `evidence.work` /
    `evidence.tokens` ride from `detail` when present (the docs/300 dotted-key
    flatten), `None` for a fossil that predates them. A lane-less efficiency event
    (run with no lane) is skipped — it has no column to land in. Pure over
    already-read events; the journal read is the caller's (snapshot's) boundary I/O.
    """
    out: dict[str, LaneSpend] = {}
    for ev in events:
        if getattr(ev, "syscall", "") != "efficiency":
            continue
        lane = str(getattr(ev, "lane", "") or "")
        if not lane:
            continue
        detail = getattr(ev, "detail", None) or {}
        w = detail.get("evidence.work")
        t = detail.get("evidence.tokens")
        out[lane] = LaneSpend(
            verdict=str(getattr(ev, "verdict", "") or ""),
            work=int(w) if isinstance(w, (int, float)) else None,
            tokens=int(t) if isinstance(t, (int, float)) else None,
        )
    return out


@dataclass(frozen=True)
class SpawnIntent:
    """A lane reading SPAWNING — a recent OP_SPAWN with no live lease yet (pure data)."""

    holder: str = ""
    age_ms: int | None = None


def _spawning_lanes(
    entries: list[dict],
    live_by_lane: dict[str, dict],
    *,
    now: dt.datetime,
    ttl_ms: int = SPAWN_TTL_MS,
) -> dict[str, SpawnIntent]:
    """Fold recent OP_SPAWN intents into the set of lanes that are SPAWNING.

    A lane is SPAWNING iff it has a journaled OP_SPAWN within ``ttl_ms`` AND holds no
    live lease. Both gates are load-bearing:

      * **no live lease** — once the eventual ACQUIRE lands, the held lease WINS the
        chip (the liveness verdict is the truth then); SPAWNING is only the
        SPAWN→ACQUIRE window. A later RELEASE that returns the lane to FREE re-exposes
        any *still-fresh* SPAWN, but a launch normally acquires long before that.
      * **within TTL** — a launch that DIES in preflight never acquires and never
        releases; its SPAWN would otherwise wedge a phantom SPAWNING forever. The TTL
        ages it out on its own — the self-heal `_expire_dead` gives a crashed holder,
        here for a never-born one. This is the safety property that lets the SPAWN be
        a pure forensic record (never a lease): a stale intent simply disappears.

    Carries the MOST RECENT spawn's holder + age for rendering (a re-launch on the
    same lane refreshes the intent). Pure over already-read entries + the replayed
    live-lease map; the clock is injected. A lane that is already held gets no entry.
    """
    # Most-recent SPAWN per lane (append order ⇒ last wins), with its age.
    latest: dict[str, dict] = {}
    for e in entries:
        if str(e.get("op") or "") != lane_journal.OP_SPAWN:
            continue
        lane = str(e.get("lane") or "")
        if not lane or lane in live_by_lane:
            continue  # held lanes take the liveness chip, not SPAWNING
        latest[lane] = e  # append order ⇒ this overwrites with the newer SPAWN
    out: dict[str, SpawnIntent] = {}
    for lane, e in latest.items():
        age = _age_ms(_entry_ts(e), now=now)
        if age is not None and age > ttl_ms:
            continue  # a dead-in-preflight launch ages out — no phantom SPAWNING
        # An unreadable/absent `ts` (age is None) keeps the SPAWN visible rather than
        # aging it out — but `append` ALWAYS stamps `ts`, so a real journaled SPAWN
        # has a parseable age and the TTL gate is live; this only protects a
        # hand-built/torn entry from vanishing silently (the row renders age `—`).
        out[lane] = SpawnIntent(holder=str(e.get("holder") or ""), age_ms=age)
    return out


def snapshot(
    config=None, *, verify=None, verdict_limit: int = 12, activity_limit: int = 10,
    now: dt.datetime | None = None,
) -> Frame:
    """Read the four sources and freeze one `Frame`. The only I/O in this module.

    Every reader degrades to empty on a missing/torn source, so this returns a
    renderable frame in a **brand-new repo with no DOS state at all** (the
    headline contract): no journal → no leases (all lanes FREE), no verdict dir →
    no verdicts, and the git-activity strip from `git_delta.recent_commits` gives
    the screen real content. ``verify`` defaults to the live `oracle.is_shipped`
    bound to this workspace; pass a fake in tests.
    """
    cfg = _config.ensure(config)
    now = now or _now()

    # --- leases (lane_journal WAL → live-lease set) + per-lane event counts ----
    entries: list[dict] = []
    try:
        entries = lane_journal.read_all(cfg.paths.lane_journal)
    except Exception:
        entries = []
    try:
        leases = lane_journal.replay(entries)
    except Exception:
        leases = []
    live_by_lane = {str(l.get("lane") or ""): l for l in leases}
    # --- spend (verdict journal → latest efficiency verdict per lane; issue #38) -
    # The fifth read, fail-soft like the rest: a missing/torn journal yields no
    # spend chips, never an error. Read-only — no lease, no mutation (row-3).
    spend_by_lane: dict = {}
    try:
        from dos import verdict_journal
        spend_by_lane = latest_efficiency_by_lane(
            verdict_journal.read_events(cfg.paths.verdict_journal)
        )
    except Exception:
        spend_by_lane = {}
    payload = {
        "leases": leases,
        "events_by_lane": _events_by_lane(entries, live_by_lane),
        "spawning_by_lane": _spawning_lanes(entries, live_by_lane, now=now),
        "spend_by_lane": spend_by_lane,
    }
    roster = lane_roster(cfg)
    states = build_lane_states(
        payload, roster=roster, exclusive=tuple(cfg.lanes.exclusive), now=now
    )

    # --- recent verdicts (.verdict-*.json) + trust cross-check ----------------
    if verify is None:
        verify = _make_oracle_verify(cfg)
    verdicts = _read_verdicts(cfg, limit=verdict_limit, verify=verify, now=now)

    # --- git-activity strip (the fresh-repo content) --------------------------
    try:
        activity = git_delta.recent_commits(activity_limit, root=cfg.root)
    except Exception:
        activity = []

    return Frame(
        workspace=str(cfg.root),
        now_iso=now.replace(microsecond=0).isoformat(),
        lanes=tuple(states),
        verdicts=tuple(verdicts),
        activity=tuple(activity),
        initialized=(cfg.root / "dos.toml").exists(),
    )


def _make_oracle_verify(cfg):
    """Build the live ``(plan, phase) -> bool`` over `oracle.is_shipped`, bound to cfg.

    Imported lazily (oracle pulls a heavier chain) and wrapped so a missing
    oracle degrades the trust column to NA rather than crashing the screen.
    """
    try:
        from dos import oracle
    except Exception:
        return None

    def _verify(plan: str, phase: str) -> bool:
        try:
            return bool(oracle.is_shipped(plan, phase, cfg=cfg).shipped)
        except Exception:
            return False

    return _verify


def _read_verdicts(cfg, *, limit: int, verify, now: dt.datetime) -> list[VerdictRow]:
    """Walk `<next_packets>/.verdict-*.json`, newest-first, → trust-attached rows."""
    ndir = cfg.paths.next_packets
    try:
        if not ndir.exists():
            return []
        files = sorted(ndir.glob(".verdict-*.json"), reverse=True)
    except OSError:
        return []
    rows: list[VerdictRow] = []
    for p in files:
        if len(rows) >= limit:
            break
        try:
            env = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(env, dict):
            continue
        tag = p.name[len(".verdict-"):-len(".json")]
        rows.append(attach_trust(parse_verdict_envelope(env, tag, now=now), verify))
    return rows


# ---------------------------------------------------------------------------
# Rendering — the plain-text floor (always available; the rich skin is in
# dispatch_top_tui). Each renderer is pure over its data + a `now` string, so the
# tests assert byte-identical output (the job DTOP renderer discipline).
# ---------------------------------------------------------------------------

_WIDTH = 78


def render_lanes_text(states: tuple[LaneState, ...]) -> str:
    out = ["LANES"]
    if not states:
        out.append("  (no lanes — declare [lanes] in dos.toml, or `dos init`)")
    for s in states:
        bits: list[str] = []
        if s.loop_ts:
            bits.append(f"loop={s.loop_ts}")
        if s.heartbeat_age_ms is not None:
            # The age field is generic; the LABEL is chip-honest — a SPAWNING lane's
            # age is "how long since the spawn intent", not a heartbeat (it has no
            # lease to beat yet), so it reads `spawn <age>`, a held lane `hb <age>`.
            label = "spawn" if s.chip == CHIP_SPAWNING else "hb"
            bits.append(f"{label} {_fmt_age(s.heartbeat_age_ms)}")
        # The spend column (issue #38): the latest efficiency chip + its counts, when
        # recorded. Appended AFTER heartbeat so a lane with no efficiency fossil keeps
        # its byte-identical pre-#38 line (the steady-state render is undisturbed).
        if s.spend_chip:
            counts = ""
            if s.work is not None and s.tokens is not None:
                counts = f" ({s.work}w/{s.tokens}t)"
            bits.append(f"{s.spend_chip}{counts}")
        if s.holder:
            bits.append(s.holder)
        marker = "*" if s.is_exclusive else " "
        detail = "  ".join(bits)
        out.append(f"  {marker}{s.lane:<13} {s.chip:<13}  {detail}".rstrip())
    live = sum(1 for s in states if s.chip == CHIP_ADVANCING)
    spin = sum(1 for s in states if s.chip == CHIP_SPINNING)
    stalled = sum(1 for s in states if s.chip == CHIP_STALLED)
    spawning = sum(1 for s in states if s.chip == CHIP_SPAWNING)
    free = sum(1 for s in states if s.chip == CHIP_FREE)
    tally = (
        f"  {len(states)} lanes · {live} advancing · {spin} spinning · "
        f"{stalled} stalled · "
    )
    # Only surface the spawning count when there IS one — keep the steady-state
    # summary (the byte-pinned no-spawn line) unchanged so existing renders/tests
    # are undisturbed; a coming run adds a segment, it doesn't reshape the line.
    if spawning:
        tally += f"{spawning} spawning · "
    tally += f"{free} free"
    out.append(tally)
    return "\n".join(out)


def render_verdicts_text(rows: tuple[VerdictRow, ...], *, limit: int = 12) -> str:
    out = ["RECENT VERDICTS        [trust = ship-oracle cross-check]"]
    if not rows:
        out.append("  (no verdicts yet)")
    for r in rows[:limit]:
        reason = f"  {r.reason_token}" if r.reason_token else ""
        trust = r.trust if r.trust != TRUST_NA else ""
        out.append(
            f"  {_fmt_age(r.age_ms):>4}  {(r.lane or '-'):<12} {r.verdict:<8} "
            f"{(r.pick or '-'):<14} {trust:<10}{reason}".rstrip()
        )
    return "\n".join(out)


def render_activity_text(commits: tuple[dict, ...], *, limit: int = 10) -> str:
    out = ["RECENT COMMITS        [ground truth — git history]"]
    if not commits:
        out.append("  (no commits — empty or non-git workspace)")
    for c in commits[:limit]:
        sha = str(c.get("sha") or "")[:9]
        subject = str(c.get("subject") or "")
        out.append(f"  {sha:<9}  {subject}"[: _WIDTH + 2])
    return "\n".join(out)


def render_frame_text(frame: Frame) -> str:
    """The whole `dos top --once` screen as plain text — the always-available floor."""
    # A long workspace path can exceed the rule width; print the header in full
    # (never truncate the path the operator needs to read) and pad with `─` only
    # when there is room. Truncating here mangled long temp-dir paths in testing.
    head = f"┌─ dos top · {frame.workspace} · {frame.now_iso} "
    out = [head + "─" * max(0, _WIDTH - len(head))]
    if not frame.initialized:
        out.append("  (no dos.toml — showing generic main/global; `dos init` to declare lanes)")
    out.append("")
    out.append(render_lanes_text(frame.lanes))
    out.append("")
    out.append(render_verdicts_text(frame.verdicts))
    out.append("")
    out.append(render_activity_text(frame.activity))
    out.append("─" * _WIDTH)
    out.append("read-only · q quit · this screen mutates nothing")
    return "\n".join(out)
