"""Lane-lease write-back — the durable cross-process surface over the pure arbiter.

`arbiter.arbitrate` is the **pure** admission kernel: state in (`request`,
`live_leases`), decision out, no I/O (`arbiter.py`). That purity is load-bearing
— a verdict you can replay a year later from the journal and get byte-identical —
so the arbiter deliberately does **not** persist the lease it grants. In a single
process that is fine: the caller holds `live_leases` as an in-memory list and
threads it into the next `arbitrate` call (`benchmark/fleet_horizon/closed_loop.py`
does exactly this).

But an **ephemeral, multi-process** orchestrator — a harness `Workflow` whose
`parallel()` branches are separate `dos` invocations — has no shared in-memory
list. Each branch's `dos arbitrate` would see an empty `--leases`, both would
ADMIT a colliding tree, and the collision would be **detected after the fact by a
later `verify`, not PREVENTED at contention**. That is strictly weaker than the
in-process loop, and it is the one real gap between "harness orchestrates, DOS
adjudicates" and DOS owning its own dispatch (see `docs/98`).

This module closes that gap **without touching the arbiter's purity**. It is the
thin, I/O-bearing shell the lane journal's docstring already anticipates ("the
writer is the caller, under the lock that serializes it"): it runs the pure
`arbitrate`, and — on `acquire` — appends an `ACQUIRE` record to the lane-journal
WAL (`lane_journal.acquire_entry`), all inside an `O_EXCL` mutex so two
cross-process acquirers cannot both win a contended lane. A sibling branch then
reconstructs `live_leases` by folding the WAL (`live()` → `lane_journal.replay`)
*before* its own `arbitrate`, so the second branch sees the first's grant and is
correctly refused.

The split mirrors `liveness` vs its CLI boundary, and `arbitrate` vs
`cmd_arbitrate`: **the verdict stays pure; the durability lives at the edge.**

Layer: this is a Layer-3 helper (`CLAUDE.md`) — a thin shell over the kernel
(`arbiter` + `lane_journal`) carrying **no policy of its own**. It names no host,
reads its lock/journal paths from the injected `SubstrateConfig`, and adds no new
admission rule. The CLI verb is `dos lease-lane`.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Optional

from dos import _filelock
from dos import arbiter, lane_journal
from dos import admission as _admission
from dos.config import SubstrateConfig


# The lane-lease mutex is a SECOND, distinct lock from the archive lock
# (`archive_lock.py`, `dos lease`): the archive lock serializes the Step-9.5
# archive ceremony; this one serializes a lane-lease grant's read-arbitrate-append
# critical section. Keeping them separate is deliberate — conflating two locks
# under one owner-namespace invites a deadlock where the archive holder blocks a
# lane acquire. The lock lives beside the journal it guards.
DEFAULT_TTL_SECONDS = 300
DEFAULT_RETRIES = 5
DEFAULT_RETRY_INTERVAL = 0.2


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _now_iso() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _journal_path(config: SubstrateConfig) -> Path:
    """The lane-journal path this lease writes to / folds from.

    MUST resolve the SAME way `lane_journal` itself does, or this module writes a
    different journal than `dos journal replay` / `dispatch_top` read — the env
    override (`DISPATCH_LANE_JOURNAL_PATH` / the `JOB_` alias) takes precedence over
    the injected config's path, mirroring `lane_journal._journal_path` exactly so
    there is ONE source of truth for where the WAL lives. (Falls back to the
    explicitly-passed config, not `config.active()`, since callers inject it.)
    """
    env = (os.environ.get("DISPATCH_LANE_JOURNAL_PATH")
           or os.environ.get("JOB_LANE_JOURNAL_PATH"))
    if env:
        return Path(env)
    return config.paths.lane_journal


def _lock_path(config: SubstrateConfig) -> Path:
    """The lane-lease mutex path: a sibling of the lane journal.

    Resolved from the injected config (never `__file__`), the same seam every
    other path uses. An env override (`DISPATCH_LANE_LEASE_LOCK_PATH`) exists for
    tests, mirroring `archive_lock`/`lane_journal`.
    """
    env = os.environ.get("DISPATCH_LANE_LEASE_LOCK_PATH")
    if env:
        return Path(env)
    j = _journal_path(config)
    return j.parent / ".lane-lease.lock"


def _read_lock(config: SubstrateConfig) -> dict | None:
    """Parse the lane-lease lock body → dict (None if absent). Shared `_filelock` parser."""
    return _filelock.read_lock(_lock_path(config))


def _write_lock(config: SubstrateConfig, owner: str) -> None:
    """Atomic O_CREAT|O_EXCL create. Raises FileExistsError if held. Shared `_filelock`."""
    _filelock.write_lock(_lock_path(config), owner)


def _release_lock(config: SubstrateConfig, owner: str) -> None:
    info = _read_lock(config)
    if info is None:
        return
    if info.get("owner") not in (owner, None):
        # someone stole/holds it; do not yank another holder's mutex
        return
    try:
        _lock_path(config).unlink()
    except FileNotFoundError:
        pass


def _age_seconds(info: dict) -> float | None:
    raw = info.get("acquired_at", "")
    return _stamp_age_seconds(raw)


def _stamp_age_seconds(raw) -> float | None:
    """`now − ts` in seconds for a second-resolution UTC stamp; None if unparseable.

    The shared parser behind `_age_seconds` (lock `acquired_at`) and beat coalescing
    (a lease's `heartbeat_at`/`acquired_at`). None means "no credible stamp" — the
    callers treat that as the safe direction (don't steal / don't elide).
    """
    try:
        ts = dt.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc)
    except (ValueError, TypeError):
        return None
    return (_now() - ts).total_seconds()


class _Mutex:
    """A scoped O_EXCL hold with stale-steal, matching `archive_lock` semantics.

    Used as a context manager around the read-arbitrate-append critical section so
    the journal append happens UNDER the lock — honoring the lane-journal rule that
    nothing journals a decision outside the lock that serializes it. Raises
    `TimeoutError` if the lock cannot be taken within the retry budget (the caller
    maps that to a non-acquire exit).
    """

    def __init__(self, config: SubstrateConfig, owner: str, *,
                 retries: int = DEFAULT_RETRIES,
                 retry_interval: float = DEFAULT_RETRY_INTERVAL,
                 ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.config = config
        self.owner = owner
        self.retries = retries
        self.retry_interval = retry_interval
        self.ttl_seconds = ttl_seconds

    def __enter__(self) -> "_Mutex":
        for attempt in range(self.retries + 1):
            try:
                _write_lock(self.config, self.owner)
                return self
            except FileExistsError:
                pass
            info = _read_lock(self.config)
            if info is None:
                continue  # unlinked between EEXIST and read; retry
            if info.get("owner") == self.owner:
                return self  # re-entrant
            age = _age_seconds(info)
            if age is not None and age >= self.ttl_seconds:
                # Atomic value-keyed CAS steal (shared `_filelock.steal_stale`) — the
                # SAME primitive archive_lock uses. The old `unlink()` + retry-create
                # was the non-value-keyed TOCTOU where two cross-process stealers of
                # one stale lock could each displace the other's fresh lock and both
                # win — here that means both fold the same pre-other's-ACQUIRE
                # live-lease set, both ADMIT one colliding tree, and both append an
                # ACQUIRE = the kernel admits two colliding lanes (the worst-class
                # false-admit this module exists to prevent). The CAS displaces only
                # the EXACT stale lock `info` we observed, so exactly one stealer wins.
                if _filelock.steal_stale(_lock_path(self.config), self.owner, info):
                    return self  # we won the steal — hold the mutex
                continue  # lost the steal (a racer won) — retry the normal path
            if attempt < self.retries:
                time.sleep(self.retry_interval)
                continue
        raise TimeoutError(
            f"lane-lease lock busy (owner={(_read_lock(self.config) or {}).get('owner')})")

    def __exit__(self, *exc) -> None:
        _release_lock(self.config, self.owner)


@dataclasses.dataclass(frozen=True)
class LaneLeaseResult:
    """The outcome of an `acquire`: the pure decision PLUS whether it was durably
    journaled. `journaled` is True only when the arbiter ACQUIRED and the WAL
    append succeeded — so a caller can tell "admitted and recorded" from "admitted
    but the record failed" (which it should treat as not-held)."""
    decision: arbiter.LaneDecision
    journaled: bool
    owner: str


# The heartbeat-freshness grace added on top of a lease's own `ttl_minutes` before
# the live-set fold treats it as expired. A lease is dropped only when its age
# exceeds `ttl_minutes + grace`, so a lease that is merely a beat-or-two late (the
# eventual-consistency window of a healthy-but-busy worker) is NEVER elided — only a
# lease that has gone quiet well past its own declared TTL. The default backstop TTL
# (`_DEFAULT_LIVE_TTL_MINUTES`) is the hard ceiling for a lease that declared none, so
# a malformed/legacy ACQUIRE with no `ttl_minutes` still cannot be immortal.
_DEFAULT_LIVE_TTL_MINUTES = 50.0   # matches lease_health.LeaseHealthPolicy.ttl_minutes
_LIVE_TTL_GRACE_MINUTES = 5.0


def _lease_is_dead(lease: dict, *, now: dt.datetime, this_host: str) -> bool:
    """Is this folded lease PROVABLY dead — safe to drop from the live set? PURE-ish.

    The structural WAL fold (`lane_journal.replay`) returns every un-RELEASEd
    ACQUIRE as "live", with NO regard for the clock: a loop that ACQUIREs and then
    crashes/exits without RELEASE leaves an *immortal* lease that the PRE-admission
    hook (which reads `live_leases`) then enforces against on every tool call —
    docs/281 Defect 1. This predicate is the self-heal: it returns True ONLY when
    the lease is **confidently** dead, so the live-set reader can elide it without
    waiting for an external SCAVENGE. Two independent confident signals, OR'd:

      (a) TTL/heartbeat staleness — the lease's newest stamp (`heartbeat_at`, else
          `acquired_at`) is older than its own `ttl_minutes` (or the default
          backstop) plus a grace. A fresh/heartbeating lease is never stale; a
          crashed one stops beating and ages out. Uses `lease_health.parse_iso`
          (the minute-OR-second stamp parser both the host and a `replay()` emit).

      (b) Dead PID on THIS host — `proc_delta.probe` confidently reports the
          holder process is gone — BUT only as a FASTER reclaim of a lease that is
          ALSO already heartbeat-stale (past the grace window), NEVER as an outright
          eviction of a still-FRESH lease. This is the load-bearing correction
          (docs/283 acquire regression): a held lease is meant to OUTLIVE its
          acquiring process. The primary acquire model is an EPHEMERAL `dos
          lease-lane acquire` subprocess that journals its ACQUIRE and EXITS
          immediately — so the recorded `pid` is dead by the time the very next
          acquirer reads `live_leases`, while the reservation it took is perfectly
          valid and must hold for its TTL. The original "dead PID wins outright"
          rule dropped that fresh reservation and let a racing acquirer DOUBLE-BOOK
          the region (a false-ADMIT collision — the exact failure the arbiter
          exists to prevent, `test_coord_demo_k4_serializes_writes`). So a dead PID
          can only SHORTEN the reclaim of a lease whose heartbeat has ALREADY gone
          quiet (it is dead AND silent → reclaim now, don't wait the full TTL); a
          fresh-beat lease is kept regardless of PID. Three-valued and foreign-host-
          blind: a foreign host, a `pid<=0` sentinel, or any probe uncertainty
          returns `None`, which is NOT death. (A cross-host orphan is left to signal
          (a); the kernel never reads its own process table as another box's —
          docs/95.)

    FAIL-SAFE DIRECTION: a lease is dropped only when its TTL/heartbeat is stale
    (signal a) — optionally reclaimed SOONER when a dead PID corroborates a lease
    already past the grace window. A FRESH lease (beat within the grace) is NEVER
    dropped, even if its (ephemeral-acquirer) PID is gone. An unparseable stamp with
    no probeable-dead PID → kept (we cannot prove it dead, so it keeps its claim —
    the genuine-collision-protection direction). This predicate can only ever make
    the live set SMALLER by removing the provably/long-stale, never admit a
    colliding live worker.
    """
    from dos import lease_health, proc_delta

    # (a) TTL/heartbeat age — the PRIMARY signal (the goal's `ttl_minutes/heartbeat
    # expiry`). A lease with no credible stamp cannot be proven stale by time.
    stamp = lease.get("heartbeat_at", "") or lease.get("acquired_at", "")
    hb = lease_health.parse_iso(stamp) if stamp else None
    age_min = None if hb is None else (now - hb).total_seconds() / 60.0
    ttl = lease.get("ttl_minutes")
    if not isinstance(ttl, (int, float)) or ttl <= 0:
        ttl = _DEFAULT_LIVE_TTL_MINUTES

    # A lease beaten within the grace window is FRESH — kept regardless of PID. This
    # is what preserves the ephemeral-acquirer reservation: agent-1's `dos lease-lane`
    # has exited (dead PID) but its just-journaled ACQUIRE is fresh, so a racing
    # agent-2 reading live_leases still SEES it and is correctly refused.
    if age_min is not None and age_min <= _LIVE_TTL_GRACE_MINUTES:
        return False

    # (b) Dead PID on THIS host — only a CORROBORATING faster-reclaim, gated on the
    # lease being ALSO heartbeat-stale (past the grace above). A dead-and-silent
    # holder is genuinely gone → reclaim now rather than wait the full TTL.
    pid = lease.get("pid")
    host_id = lease.get("host_id", "") or ""
    probe = proc_delta.probe(
        pid if isinstance(pid, int) else None,
        host_id=host_id,
        this_host=this_host,
    )
    if probe.alive is False and age_min is not None:
        # Past the grace (checked above) AND the holder process is confirmed gone →
        # the lease is both silent and dead; reclaim it without waiting the full TTL.
        return True

    # (a) continued — the hard TTL backstop. No credible stamp → cannot prove stale
    # by time, and the PID was not a (gated) confident-dead → keep (claim-preserving).
    if age_min is None:
        return False
    return age_min > (ttl + _LIVE_TTL_GRACE_MINUTES)


def _expire_dead(leases: list[dict], *, now: dt.datetime, this_host: str) -> list[dict]:
    """Drop the provably-dead leases from a structurally-folded live set. PURE-ish.

    The filter `live_leases` applies on top of `lane_journal.replay` so the live
    set the admission hook + arbiter see self-heals past a crashed worker's orphan,
    WITHOUT mutating the WAL (a real SCAVENGE is still appended by the
    reconcile/supervisor writers; this is a read-time fold, replay-pure). Best
    effort per-lease: a predicate error on one malformed lease must not blank the
    whole set, so a raising `_lease_is_dead` keeps that lease (fail-safe)."""
    kept: list[dict] = []
    for l in leases:
        try:
            dead = _lease_is_dead(l, now=now, this_host=this_host)
        except Exception:
            dead = False  # never let a probe/parse fault drop a real lease
        if not dead:
            kept.append(l)
    return kept


def live_leases(config: SubstrateConfig, *, expire_dead: bool = False) -> list[dict]:
    """The current live-lease set, reconstructed from the WAL (pure fold over I/O).

    This is the cross-process channel: a sibling orchestrator branch calls this to
    learn what lanes are already held before it arbitrates — the durable analogue
    of the in-process `live_leases` list `closed_loop.py` threads by hand. Reads
    the journal, folds it with the PURE `lane_journal.replay`. No lock needed: a
    read of an append-only, torn-tail-tolerant log is always consistent-enough
    (a half-written final ACQUIRE folds as "didn't happen", the safe WAL reading).

    `expire_dead` (default **False** — the structural fold is the base contract):
    when True, the provably-dead leases (`_expire_dead`) are dropped from the
    returned set — a crashed worker's un-RELEASEd ACQUIRE whose TTL/heartbeat aged
    out or whose holder PID is confidently gone on this host. This is the
    **admission/contention** view: "which LIVE workers would I collide with",
    which must self-heal past a phantom orphan (docs/281 Defect 1) instead of
    enforcing it on every tool call until an external SCAVENGE lands. It is OFF by
    default because the OTHER consumers — `adopt()` and the orphan-reclaim sweep —
    need the dead orphan to remain VISIBLE precisely so they can transfer/scavenge
    it; hiding it there would make a dead lane un-reclaimable. So: contention reads
    pass `expire_dead=True`, reclaim reads keep the default. Either way `replay`
    stays a pure structural fold (`dos journal replay` / the audit trail are
    byte-identical); expiry is a read-time view, never a WAL mutation.
    """
    entries = lane_journal.read_all(_journal_path(config))
    folded = lane_journal.replay(entries)
    if expire_dead:
        return _expire_dead(folded, now=_now(), this_host=_hostname())
    return folded


def live_generations(config: SubstrateConfig) -> dict[tuple[str, str], int]:
    """The fencing GENERATION of each live lease, folded from the WAL (docs/342 M2).

    The boundary I/O shell over the pure `lane_journal.lease_generations` — the same
    `live_leases` is over `lane_journal.replay`. Reads the journal and folds it by
    append-order into a `{(loop_ts, lane): generation}` map, the monotonic fencing
    token (docs/114 §A2) the apply-gate checks: a run presents the generation it
    holds, and the gate refuses a write a later grant on an overlapping region
    superseded. Kept SEPARATE from `live_leases` (not stamped onto the lease dict)
    so `replay`'s live set stays byte-identical and the `replay(compact(E)) ==
    replay(E)` invariant is untouched — the generation is a read-time projection,
    not a WAL field. No lock: the same consistent-enough append-only read.

    A caller joins this to `live_leases` by `(loop_ts, lane)` identity to learn the
    generation of its OWN lease (the one it presents at the gate) and of each OTHER
    live lease (the supersede operands).
    """
    entries = lane_journal.read_all(_journal_path(config))
    return lane_journal.lease_generations(entries)


def acquire(
    config: SubstrateConfig,
    *,
    lane: str,
    kind: str,
    tree: list[str],
    owner: str,
    loop_ts: str = "",
    extra_leases: list[dict] | None = None,
    retries: int = DEFAULT_RETRIES,
    retry_interval: float = DEFAULT_RETRY_INTERVAL,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    run_id: str = "",
) -> LaneLeaseResult:
    """Arbitrate a lane request and, on ACQUIRE, durably journal the grant.

    The whole read-arbitrate-append runs under the lane-lease mutex so two
    cross-process acquirers serialize: the second sees the first's freshly
    journaled ACQUIRE in `live_leases` and is refused. `arbitrate` itself stays
    pure — this function is the I/O shell around it.

    `extra_leases` are caller-supplied live leases to union with the journal's
    (e.g. a test injecting state, or a host that tracks some leases out-of-band).
    `loop_ts` is the lease's `(loop_ts, lane)` identity key — defaults to a
    second-resolution stamp so each acquire is uniquely keyed.

    `run_id` (OPTIONAL, docs/118 S / docs/137) is the CID spine id the lease (and,
    on a genuine-collision, the REFUSE) carries — the field that closes the
    WAL↔spine join, so a *held* lane is traceable back to its run exactly as a
    *refused* one already is. Resolved at the CLI boundary (the flag / `CID_RUN_ID`
    env), never inside the pure arbiter. Empty ⇒ the ACQUIRE replays unchanged.
    """
    loop_ts = loop_ts or _now_iso()
    extra = list(extra_leases or [])
    preds = _admission.active_predicates(config=config)

    with _Mutex(config, owner, retries=retries, retry_interval=retry_interval,
                ttl_seconds=ttl_seconds):
        # Read the durable lease set INSIDE the lock so a racing acquirer that
        # already journaled its grant is visible to us — the serialization point.
        #
        # This is the STRUCTURAL fold (`expire_dead=False`), NOT the dead-elision
        # the admission hook uses. The distinction is load-bearing (docs/283): a
        # lease is held by a process that exits between ACQUIRE and RELEASE — its
        # EFFECT (the booked region) outlives the short-lived process that took it.
        # `expire_dead=True` runs the dead-PID rung (`_lease_is_dead` signal b),
        # which probes the holder PID; for a fresh lease whose journaling subprocess
        # has already exited (the `dos lease-lane acquire` shape — a child that
        # journals then returns) that probe reports `alive=False`, so the
        # still-held region is wrongly elided and a racing acquirer DOUBLE-BOOKS it
        # (a lost update — the exact TOCTOU the lease exists to prevent). Inside
        # this mutex we are already serialized against concurrent acquirers, so we
        # do NOT need (and must not use) dead-elision here: the genuine live set is
        # the right contention view. The phantom-orphan self-heal docs/281 wants is
        # a property of the LONG-LIVED admission read (`pretool_sensor`, where a
        # dead PID + no fresh tool activity really is abandonment), not of this
        # short, lock-held acquire read. Coupling the two through one `expire_dead`
        # flag is what regressed `test_coord_demo_k4_serializes_writes`.
        live = live_leases(config) + extra
        decision = arbiter.arbitrate(
            requested_lane=lane,
            requested_kind=kind,
            requested_tree=tree,
            live_leases=live,
            config=config,
            predicates=preds,
        )
        journaled = False
        if decision.outcome == "acquire":
            lease = {
                "lane": decision.lane or lane,
                "lane_kind": kind,
                "tree": list(decision.tree or tree),
                "loop_ts": loop_ts,
                "host_id": os.environ.get("DISPATCH_HOST_ID") or _hostname(),
                "pid": os.getpid(),
                "holder": owner,
                "acquired_at": _now_iso(),
            }
            if run_id:
                lease["run_id"] = run_id  # the WAL↔spine join key (docs/137)
            lane_journal.append(
                lane_journal.acquire_entry(lease, reason=f"lane-lease:{owner}",
                                           run_id=run_id or None),
                _journal_path(config),
            )
            journaled = True
        else:
            # A genuine-collision refuse — record WHY, inside the already-held
            # mutex. Without this the journal cannot answer its own raison d'être,
            # "why was I refused at 14:03?": a denied arbitrate left no trace, yet
            # the decisions queue / central-index / trajectory-audit all CONSUME
            # OP_REFUSE. `journaled` STAYS False — its contract is "the GRANT was
            # durably recorded", which a refuse never is, so we do NOT overload it
            # with "the refuse-record landed". A failed RECORD must never convert a
            # refuse into anything else, so the append is best-effort (swallow
            # OSError, mirroring `halt`). Only the genuine-collision path reaches
            # here; the _Mutex lock-busy TimeoutError raises out of __enter__ above
            # and records nothing (a contended LOCK is not a refused LANE).
            try:
                lane_journal.append(
                    lane_journal.refuse_entry(
                        decision,
                        owner=owner,
                        lane=lane,
                        loop_ts=loop_ts,
                        host_id=os.environ.get("DISPATCH_HOST_ID") or _hostname(),
                        run_id=run_id or None,
                    ),
                    _journal_path(config),
                )
            except OSError:
                pass
        return LaneLeaseResult(decision=decision, journaled=journaled, owner=owner)


def release(
    config: SubstrateConfig,
    *,
    lane: str,
    owner: str,
    loop_ts: str = "",
) -> bool:
    """Release a held lane lease by appending a RELEASE to the WAL.

    Returns True if a matching live lease was found and released. If `loop_ts` is
    omitted, releases the newest live lease on `lane` held by `owner` (the common
    case: a branch that acquired without tracking its own loop_ts). Runs under the
    mutex so the read-which-lease + append is atomic w.r.t. a concurrent acquire.
    """
    with _Mutex(config, owner):
        live = live_leases(config)
        match = None
        for l in live:
            if l.get("lane") != lane:
                continue
            if loop_ts and str(l.get("loop_ts") or "") != loop_ts:
                continue
            if l.get("holder") not in (owner, None) and owner != "":
                # only release our own lease unless owner is unset on the record
                if str(l.get("holder") or "") != owner:
                    continue
            match = l  # keep last → newest on the lane
        if match is None:
            return False
        lane_journal.append(
            lane_journal.release_entry(match, reason=f"lane-lease:{owner}"),
            _journal_path(config),
        )
        return True


def adopt(
    config: SubstrateConfig,
    *,
    lane: str,
    new_owner: str,
    loop_ts: str = "",
    new_pid: int | None = None,
) -> bool:
    """Take over a live lease at `(loop_ts, lane)` for `new_owner` (C5 ownership transfer).

    Returns True if a matching live lease was found and the ADOPT was journaled. The
    CALLER (a host supervisor) has already decided this lease is an adoptable orphan —
    its holder is gone but its recorded children are still live (it measured that at
    the boundary, keyed on the children `acquire_entry` recorded, via the proc-liveness
    rung). This just performs the durable ownership rewrite under the mutex: the lease
    keeps its identity, tree, ttl, and children; only holder/pid/host_id move to the
    adopter. NEVER kills anything — the grandchildren keep running.

    `loop_ts` disambiguates a same-minute sibling; omitted, it adopts the newest live
    lease on `lane`. Returns False (no-op) if no live lease matches — you cannot adopt
    a lease no one holds. Runs under the mutex so the read-which-lease + append is
    atomic w.r.t. a concurrent acquire/release (two adopters serialize; the second sees
    the first's ADOPT in the fold)."""
    with _Mutex(config, new_owner):
        live = live_leases(config)
        match = None
        for l in live:
            if l.get("lane") != lane:
                continue
            if loop_ts and str(l.get("loop_ts") or "") != loop_ts:
                continue
            match = l  # keep last → newest on the lane
        if match is None:
            return False
        lane_journal.append(
            lane_journal.adopt_entry(
                match, new_holder=new_owner, new_pid=new_pid,
                new_host_id=os.environ.get("DISPATCH_HOST_ID") or _hostname(),
                reason=f"adopt:{new_owner}"),
            _journal_path(config),
        )
        return True


def heartbeat(
    config: SubstrateConfig,
    *,
    lane: str,
    owner: str,
    loop_ts: str = "",
    coalesce_within_s: float = 0.0,
) -> bool:
    """Refresh a HELD lane lease by appending a HEARTBEAT to the WAL.

    This is the writer the liveness oracle was waiting for. The HEARTBEAT op, its
    `replay` fold, and the `journal_delta._HEARTBEAT_OPS` fold all already existed
    — but NOTHING in the package ever wrote one, so the newest journal beat for a
    held lane was always its boundary ACQUIRE, which ages past `spin_ms` and the
    liveness verdict could only ever reach STALLED from real evidence. SPINNING
    (alive-but-not-progressing) was unreachable in production. A held worker
    calling `dos lease-lane heartbeat` on a cadence is what makes it reachable:
    the fresh beat proves the lease is alive NOW while the absence of state-mutating
    events keeps it short of ADVANCING — the exact SPINNING ladder rung.

    Returns True if a matching LIVE lease was found and beaten; False (writing
    NOTHING) if no live lease on `lane` is held by `owner`. The live-lease match
    is **load-bearing for fold correctness, not ergonomics**: `journal_delta.
    fold_since` credits a beat by `(loop_ts, lane)` identity + `ts` with NO
    held-lease check, so a stray HEARTBEAT appended after a RELEASE/SCAVENGE for
    the same identity would let the fold read a DEAD run alive (a false ADVANCING/
    SPINNING). Beating only a currently-live lease — and carrying that lease's true
    `(loop_ts, lane, host_id)` so the fold's identity match is exact — is the
    writer-side defense for that hole. Runs under the same `_Mutex` as
    acquire/release so the read-which-lease + append is atomic w.r.t. a concurrent
    eviction (a lease scavenged out from under us is gone from `live` before we
    decide to beat it).

    If `loop_ts` is omitted, beats the NEWEST live lease on `lane` held by `owner`
    (mirrors `release`). Pass the SAME `loop_ts` the acquire used to beat a
    specific lease — re-defaulting a fresh stamp would mint a DIFFERENT identity
    and the beat would fold as a no-op against the real lease.

    **Beat coalescing (docs/106 §3.1a — the WAL-drain brake).** A worker that beats
    every few seconds while `liveness`'s `spin_ms` window is minutes writes one WAL
    line per beat, of which all but the freshest are pure noise: `journal_delta.
    fold_since` keeps only the NEWEST beat per identity, so an older beat changes no
    verdict. `coalesce_within_s` makes that redundancy free to skip: when the matched
    live lease's CURRENT `heartbeat_at` is younger than this many seconds, the beat
    is ELIDED — nothing is appended — and the call still returns True (the lease is
    live and was beaten recently enough that re-stamping it would not move the
    liveness verdict). This is verdict-preserving *by construction* and only in the
    safe direction: eliding can only let an existing beat AGE, never fabricate a
    fresher one, so it can never cause a false ADVANCING/SPINNING — the same
    one-way-safety `compact` relies on. The append path stays append-only and
    O(1)-atomic (no journal rewrite); coalescing simply doesn't write the line.
    Default `0.0` elides nothing — byte-identical to the pre-coalescing writer — so
    this is a pure opt-in: a caller that beats fast passes e.g. `coalesce_within_s`
    a small fraction of `spin_ms` (a 5 s beat under a 900 s window coalesced at 60 s
    cuts the beat lines ~12x while the lease never reads older than 60 s). Choose it
    well under `spin_ms`: an elision floor at or above `spin_ms` could let the only
    beat age past the alive bound between writes and flip a live lease to STALLED —
    so the floor is the caller's concern, bounded by the policy it runs under, never
    a kernel constant silently coupled to `spin_ms`.
    """
    with _Mutex(config, owner):
        live = live_leases(config)
        match = None
        for l in live:
            if l.get("lane") != lane:
                continue
            if loop_ts and str(l.get("loop_ts") or "") != loop_ts:
                continue
            # HOLDER AUTHENTICATION — STRICTER than release's owner filter, and
            # deliberately so. A HEARTBEAT is the one fail-DANGEROUS lease op: it
            # REFRESHES a lease's beat, and `journal_delta.fold_since` credits that
            # beat by (loop_ts, lane)+ts with NO held-lease check, so a beat written
            # by anyone-but-the-holder flips a dead/crashed orphan's liveness verdict
            # from STALLED (the orphan-sweep's input) to SPINNING (alive) — a
            # false-revival, the catastrophic false-SPINNING this writer must never
            # cause. release()'s loose `owner in (holder, None) or owner==''` filter
            # is safe THERE because over-release only FORGETS a lease (→ STALLED, the
            # safe direction); the SAME looseness here is unsafe because over-beat
            # REVIVES one. So a beat requires the caller's non-empty `owner` to EXACTLY
            # equal the lease's recorded `holder`:
            #   * owner=="" cannot authenticate a beat (an empty requester is not a
            #     holder — the wildcard hole that let any caller beat any live lease);
            #   * a lease with holder=None (a foreign / RECONCILE / inline-fields
            #     ACQUIRE the replay fold supports) is UNATTRIBUTABLE, so NO owner may
            #     beat it — it can only age out to STALLED and be scavenged, the
            #     correct fate of a lease whose holder can't be proven (the None hole
            #     that let any non-empty owner beat such a lease).
            # Both holes produced a confirmed false-SPINNING on a crashed orphan.
            if not owner or str(l.get("holder") or "") != owner:
                continue
            match = l  # keep last → newest on the lane
        if match is None:
            return False
        # Beat coalescing (docs/106 §3.1a): if this lease's current beat is younger
        # than the caller's floor, the new beat would only re-stamp an already-fresh
        # lease — `fold_since` keeps the newest beat, so the verdict is unchanged.
        # Skip the append (the WAL stays append-only; we just don't write the line).
        # The lease's freshest beat is its `heartbeat_at` (set by replay from the
        # last HEARTBEAT) falling back to its `acquired_at` (the boundary ACQUIRE IS
        # the first beat — `journal_delta._HEARTBEAT_OPS` counts ACQUIRE too). A
        # missing/unparseable stamp yields None age → never elide (write the beat,
        # the safe direction). Default floor 0.0 makes `< 0.0` always False → every
        # beat writes, exactly as before.
        if coalesce_within_s > 0.0:
            beat_ts = match.get("heartbeat_at") or match.get("acquired_at")
            age = _stamp_age_seconds(beat_ts)
            if age is not None and age < coalesce_within_s:
                return True  # live and recently beaten — coalesced, nothing written
        lane_journal.append(
            lane_journal.heartbeat_entry(match, heartbeat_at=_now_iso()),
            _journal_path(config),
        )
        return True


@dataclasses.dataclass(frozen=True)
class HaltResult:
    """The outcome of a `halt`: a recorded stop DECISION, never a delivered kill.

    `handle` is the opaque host-supplied identifier echoed back. `recorded` is
    True iff the `OP_HALT` WAL append succeeded (the only thing the kernel did).
    `command` is the host-supplied stop command echoed for a driver/operator to
    run — the kernel proposes it and exits; it NEVER executes it (docs/99 §5).
    `lane`/`loop_ts` are filled from the matched live lease when the handle
    correlated to one, else echoed from the args (forensic correlation only)."""
    handle: str
    recorded: bool
    command: Optional[str] = None
    lane: str = ""
    loop_ts: str = ""


def halt(
    config: SubstrateConfig,
    *,
    handle: str,
    lane: str = "",
    owner: str = "",
    loop_ts: str = "",
    reason: str = "",
    run_id: str = "",
    command: Optional[str] = None,
) -> HaltResult:
    """Record a STOP DECISION for an in-flight run on the WAL — and NOTHING else.

    docs/99 §5: the kernel's one effectful concession past `spawn`/`reap` is to
    *record* a stop decision and *propose* a command; it deliberately stops short
    of delivering any signal, because delivering it requires knowing WHAT the
    `handle` is (a pid? a container? a remote task?), and that domain knowledge is
    a driver's, never a domain-free kernel's. So this:

      1. appends an `OP_HALT` entry to the lane journal (under the mutex, so the
         optional live-lease correlation read + the append are atomic w.r.t. a
         concurrent acquire/release), recording the opaque `handle`, the `reason`,
         the (forensically-correlated) lane/loop_ts, and the proposed `command`;
      2. returns a `HaltResult` carrying the proposed `command` for a driver or
         operator to run.

    It NEVER calls `os.kill`, `subprocess`, `TaskStop`, or any process API. A host
    that wants the stop *enacted* writes a driver that consumes the `OP_HALT`
    record and signals — exactly as `drivers/supervisor.py` consumes a REAP plan
    and journals the SCAVENGE. The HALT records the *intent*; the lease only ends
    when that driver appends the confirming RELEASE/SCAVENGE.

    `handle` is REQUIRED and opaque — the kernel records it verbatim and branches
    on nothing about it (the domain-free contract). `lane`/`loop_ts`/`owner` are
    optional: when given (or when a single live lease matches the handle's pid),
    they are stamped on the entry so an operator can correlate the HALT to the
    lease it targeted; when absent, the HALT still records against the bare handle.
    """
    with _Mutex(config, owner or handle):
        # Best-effort forensic correlation: if the caller named a lane/loop_ts, or
        # the handle matches a live lease's pid, carry that lease's identity onto
        # the entry. This is purely so the journal reader can join HALT→lease; it
        # is NEVER required, and a no-match handle records just fine.
        corr_lane, corr_loop_ts, corr_host = lane, loop_ts, None
        try:
            for l in live_leases(config):
                if lane and str(l.get("lane") or "") != lane:
                    continue
                if loop_ts and str(l.get("loop_ts") or "") != loop_ts:
                    continue
                if not lane and not loop_ts:
                    # Correlate by opaque handle == the recorded pid, if it parses
                    # as one. We do NOT interpret the handle as a pid for any
                    # ACTION — only to fill forensic fields — so domain-freedom
                    # holds (the kernel still kills nothing, reads no process).
                    if str(l.get("pid") or "") != handle:
                        continue
                corr_lane = str(l.get("lane") or "") or corr_lane
                corr_loop_ts = str(l.get("loop_ts") or "") or corr_loop_ts
                corr_host = l.get("host_id")
                break
        except Exception:
            # Correlation is best-effort; a fold failure must never block the
            # decision record (the WAL read stance: degrade, don't raise).
            pass

        entry_reason = reason or (f"halt:{owner}" if owner else "halt")
        entry = lane_journal.halt_entry(
            handle,
            reason=entry_reason,
            lane=corr_lane,
            loop_ts=corr_loop_ts,
            host_id=corr_host,
            run_id=run_id or None,
            command=command,
        )
        recorded = True
        try:
            lane_journal.append(entry, _journal_path(config))
        except OSError:
            recorded = False
        return HaltResult(
            handle=handle,
            recorded=recorded,
            command=command,
            lane=corr_lane,
            loop_ts=corr_loop_ts,
        )


@dataclasses.dataclass(frozen=True)
class SpawnResult:
    """The outcome of a `spawn`: a recorded INTENT to take a lane, never a hold.

    `lane` is the region the launcher committed to. `recorded` is True iff the
    `OP_SPAWN` WAL append succeeded — that is the only thing `spawn` does. It grants
    NO lease (the eventual `acquire` does), so there is no `journaled`/lease field:
    a SPAWN is a forensic intent the dos-top SPAWNING chip folds, not a grant the
    arbiter admits against. `loop_ts`/`holder` are echoed for the SPAWN→ACQUIRE join.
    """
    lane: str
    recorded: bool
    loop_ts: str = ""
    holder: str = ""


def spawn(
    config: SubstrateConfig,
    *,
    lane: str,
    owner: str = "",
    loop_ts: str = "",
    run_id: str = "",
    reason: str = "",
) -> SpawnResult:
    """Record an INTENT TO TAKE A LANE on the WAL — and NOTHING else (the dos-top gap).

    The acquire-side sibling of `halt`. Where `acquire` durably GRANTS a lane and
    `halt` records a STOP intent, `spawn` records a START intent: "a run is *coming*
    to this lane," appended the instant a launcher commits to a lane — BEFORE the
    heavy preflight (`dos doctor`, pick selection) and before the durable ACQUIRE.

    It exists to close the SPAWN→ACQUIRE blind window the 2026-06-09 dos-top
    operator audit (private archive) names:
    `dos top` is a read-only projection over the WAL, and a *successful* `arbitrate`
    PERSISTS nothing (the purity boundary), so between launch and the first ACQUIRE
    a loop leaves zero trace on the only surface the watchdog reads. This append is
    that trace.

    Crucially it grants NO lease: `lane_journal.OP_SPAWN` is NOT in
    `_STATE_MUTATING_OPS`, so `replay` ignores it and the arbiter never admits
    against it. An intent that never acquires therefore strands no phantom hold (the
    docs/281 failure mode) and a not-yet-real run can never double-book a region. The
    durable SPAWN is the cross-process home for the supervisor's in-memory `pending`
    field; `dispatch_top` folds the recent SPAWNs (TTL-bounded, no-live-lease-only)
    into the `SPAWNING` chip — a separate fold, never the admission live set.

    The append runs under the lane-lease `_Mutex` so it serializes against
    concurrent acquire/release/halt appends (journal order = decision order, the WAL
    invariant). The record is best-effort: an `OSError` on the append yields
    `recorded=False` rather than raising, mirroring `halt` — a failed forensic record
    must never block a launch.
    """
    loop_ts = loop_ts or _now_iso()
    holder = owner or f"{_hostname()}:{os.getpid()}"
    with _Mutex(config, owner or f"spawn:{lane}"):
        entry = lane_journal.spawn_entry(
            lane=lane,
            loop_ts=loop_ts,
            holder=holder,
            host_id=os.environ.get("DISPATCH_HOST_ID") or _hostname(),
            pid=os.getpid(),
            run_id=run_id or None,
            reason=reason or (f"spawn:{owner}" if owner else "spawn"),
        )
        recorded = True
        try:
            lane_journal.append(entry, _journal_path(config))
        except OSError:
            recorded = False
        return SpawnResult(
            lane=lane, recorded=recorded, loop_ts=loop_ts, holder=holder
        )


@dataclasses.dataclass(frozen=True)
class CompactResult:
    """The outcome of a `compact_journal`: the before/after size of the WAL.

    `entries_before`/`entries_after` are line counts (the after count is the
    single CHECKPOINT plus any preserved `_CORRUPT` sentinels); `bytes_reclaimed`
    is the file shrink. A compaction is purely a size operation for the ARBITER:
    `replay` over the compacted journal reconstructs a byte-identical live-lease
    set (the differential invariant), so admission decisions are unchanged.

    It is NOT, however, liveness-fold-preserving: a CHECKPOINT carries no `ts` and
    is in neither `journal_delta._EVENT_OPS` nor `_HEARTBEAT_OPS`, so a mid-flight
    compaction drops the beat anchor of a still-live run — that run reads STALLED
    to the liveness oracle until its next ACQUIRE/HEARTBEAT lands. The direction is
    always toward less-alive (compaction can never FABRICATE an event or beat), so
    no false-ADVANCING/SPINNING can result; but for that reason `dos journal
    compact` is meant for a quiet window, like the supervisor-lock caveat below.
    """
    entries_before: int
    entries_after: int
    bytes_reclaimed: int


def compact_journal(
    config: SubstrateConfig,
    *,
    owner: str = "journal-compact",
) -> CompactResult:
    """Compact the lane-journal WAL in place, crash-safely, under the lease mutex.

    The WAL is append-only with no auto-rotation, so on a long-lived fleet it
    grows unbounded and every lease op pays O(file) to `read_all`/`replay`/
    `next_seq`. This operator-invoked verb bounds it: fold the whole journal to a
    single OP_CHECKPOINT snapshot of the authoritative live set (the pure
    `lane_journal.compact`) and rewrite the file to that snapshot.

    The correctness rail is `lane_journal.compact`'s differential invariant —
    `replay(compact(E)) == replay(E)` — so a still-live lease older than any
    cutoff SURVIVES in the snapshot and the kernel can never false-ADMIT a
    colliding tree after a compaction (the catastrophic lost-live-lease bug a
    naive truncate-old-lines would cause). A `_CORRUPT` sentinel is preserved, and
    `next_seq` stays monotonic via the checkpoint's `seq_watermark`.

    Crash-safety: the new content is written to a tmp sibling, `flush()`+`fsync`'d,
    then `_filelock.atomic_replace`'d over the journal (the same win32-hardened
    primitive `home` uses) — a crash leaves either the full old WAL or the full new
    one, NEVER a torn rewrite. The whole read-fold-rewrite runs under the same
    `_Mutex` that acquire/release/heartbeat take, so no concurrent lease append
    races the rewrite. (NOTE: `drivers/supervisor` serializes its SCAVENGE appends
    under its OWN `.supervisor.lock`, not this mutex — so compaction does not
    serialize against a concurrent supervisor reap; run compaction in a quiet
    window. Unifying the two write-locks is a noted hardening follow-on.)
    """
    jp = _journal_path(config)
    with _Mutex(config, owner):
        entries = lane_journal.read_all(jp)
        before = len(entries)
        try:
            size_before = jp.stat().st_size
        except OSError:
            size_before = 0
        if before == 0:
            # Nothing to compact — short-circuit so an empty journal stays empty
            # (a bare `compact` would otherwise materialize a spurious 1-line
            # CHECKPOINT of an empty live set, "growing" a 0-byte file).
            return CompactResult(entries_before=0, entries_after=0,
                                 bytes_reclaimed=0)
        compacted = lane_journal.compact(entries)
        body = "".join(
            json.dumps(e, sort_keys=True, default=str, ensure_ascii=False) + "\n"
            for e in compacted
        )
        jp.parent.mkdir(parents=True, exist_ok=True)
        tmp = jp.with_suffix(jp.suffix + ".compact.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, body.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        _filelock.atomic_replace(tmp, jp)
        try:
            size_after = jp.stat().st_size
        except OSError:
            size_after = 0
        return CompactResult(
            entries_before=before,
            entries_after=len(compacted),
            bytes_reclaimed=max(0, size_before - size_after),
        )


def _hostname() -> str:
    try:
        import socket
        return socket.gethostname()
    except Exception:
        return "unknown"
