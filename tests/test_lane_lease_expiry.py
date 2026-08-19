"""Phantom-lease self-heal — `live_leases(expire_dead=True)` drops the provably
dead (docs/281 Defect 1).

The PRE-admission hook reads the live-lease set; a crashed worker's un-RELEASEd
ACQUIRE folds as structurally "live" forever (no TTL/heartbeat/PID expiry), so the
hook enforced a PHANTOM lane on every tool call until an external SCAVENGE landed.
These tests pin the fix: the CONTENTION view (`expire_dead=True`, what `acquire`'s
arbitration read + the hook's `live_leases_for` use) drops a lease that is
PROVABLY dead — past its own `ttl_minutes + grace`, OR whose holder PID is
confidently gone on this host — while the base structural view (`expire_dead=False`,
the default, what `adopt()` / orphan-reclaim use) keeps it so the orphan stays
reclaimable. Fail-safe direction: a lease we CANNOT prove dead is always kept.

Conventions mirror `test_lane_lease.py`: a `cfg` fixture pins lock + journal at
tmp paths so every record lands in isolation.
"""
from __future__ import annotations

import datetime as dt
import os

import pytest

from dos import lane_lease, lane_journal
from dos import config as _config


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("DISPATCH_LANE_LEASE_LOCK_PATH", str(tmp_path / ".lane-lease.lock"))
    monkeypatch.setenv("DISPATCH_LANE_JOURNAL_PATH", str(tmp_path / "lane-journal.jsonl"))
    return _config.default_config(str(tmp_path))


def _stamp(delta_minutes: float) -> str:
    """A UTC second-resolution stamp `delta_minutes` in the PAST (positive = older)."""
    t = lane_lease._now() - dt.timedelta(minutes=delta_minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_acquire(cfg, *, lane, holder, pid, acquired_min_ago=0.0,
                  ttl_minutes=50, host_id=None, heartbeat_min_ago=None):
    """Append a bare ACQUIRE to the WAL (an orphan: no matching RELEASE)."""
    host = host_id if host_id is not None else lane_lease._hostname()
    lease = {
        "lane": lane, "lane_kind": "keyword", "tree": [f"{lane}/**"],
        "loop_ts": _stamp(acquired_min_ago), "host_id": host, "pid": pid,
        "holder": holder, "acquired_at": _stamp(acquired_min_ago),
        "ttl_minutes": ttl_minutes,
    }
    if heartbeat_min_ago is not None:
        lease["heartbeat_at"] = _stamp(heartbeat_min_ago)
    lane_journal.append(
        lane_journal.acquire_entry(lease, reason=f"lane-lease:{holder}"),
        lane_lease._journal_path(cfg),
    )


# ── the predicate ───────────────────────────────────────────────────────────

def test_fresh_lease_is_not_dead():
    """A lease beating within its TTL, our own live PID → kept (not dead)."""
    host = lane_lease._hostname()
    now = lane_lease._now()
    lease = {"lane": "x", "acquired_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "ttl_minutes": 50, "pid": os.getpid(), "host_id": host}
    assert lane_lease._lease_is_dead(lease, now=now, this_host=host) is False


def test_ttl_expired_lease_is_dead():
    """Acquired 2h ago, ttl 50 → age 120 > 55 → dead via TTL (pid 0 = no probe)."""
    host = lane_lease._hostname()
    now = lane_lease._now()
    lease = {"lane": "y", "acquired_at": (now - dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "ttl_minutes": 50, "pid": 0, "host_id": host}
    assert lane_lease._lease_is_dead(lease, now=now, this_host=host) is True


def test_dead_pid_with_fresh_stamp_is_KEPT_ephemeral_acquirer():
    """A FRESH stamp + a confidently-dead PID on this host → KEPT, not dead
    (docs/283). The primary acquire model is an EPHEMERAL `dos lease-lane acquire`
    subprocess that journals its ACQUIRE then EXITS — so its recorded PID is dead by
    the time the next acquirer reads the live set, while the reservation it took is
    perfectly valid and must hold for its TTL. The old "dead PID wins outright" rule
    dropped that fresh reservation and let a racing acquirer DOUBLE-BOOK the region
    (`test_coord_demo_k4_serializes_writes`). A dead PID may only SHORTEN the reclaim
    of an ALREADY-stale lease, never evict a fresh one."""
    host = lane_lease._hostname()
    now = lane_lease._now()
    lease = {"lane": "crash", "acquired_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "ttl_minutes": 50, "pid": 999_999, "host_id": host}  # 999999 ~ not running
    assert lane_lease._lease_is_dead(lease, now=now, this_host=host) is False


def test_dead_pid_with_stale_stamp_is_reclaimed_faster():
    """A STALE stamp (past the grace) + a confidently-dead PID on this host → dead.
    Past the grace window the lease is no longer a fresh ephemeral reservation, so a
    dead-and-silent holder is genuinely gone and the dead-PID signal reclaims it
    SOONER than the full TTL backstop would (the fast-reclaim corroboration). Here
    the stamp is `grace + 1` minutes old — past the fresh window but well under the
    50-min TTL — so ONLY the dead-PID rung can expire it."""
    host = lane_lease._hostname()
    now = lane_lease._now()
    stale = now - dt.timedelta(minutes=lane_lease._LIVE_TTL_GRACE_MINUTES + 1)
    lease = {"lane": "crash", "acquired_at": stale.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "ttl_minutes": 50, "pid": 999_999, "host_id": host}
    assert lane_lease._lease_is_dead(lease, now=now, this_host=host) is True


def test_no_credible_stamp_and_no_dead_pid_is_kept():
    """No parseable stamp AND pid<=0 (unprobeable) → cannot PROVE dead → kept."""
    host = lane_lease._hostname()
    now = lane_lease._now()
    lease = {"lane": "z", "pid": 0, "host_id": host}
    assert lane_lease._lease_is_dead(lease, now=now, this_host=host) is False


def test_foreign_host_fresh_lease_is_kept():
    """A foreign-host pid can't be probed; a FRESH foreign lease must not be
    false-dropped (the cross-host false-positive docs/95 forbids)."""
    host = lane_lease._hostname()
    now = lane_lease._now()
    lease = {"lane": "f", "acquired_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "ttl_minutes": 50, "pid": 999_999, "host_id": "OTHERBOX"}
    assert lane_lease._lease_is_dead(lease, now=now, this_host=host) is False


def test_foreign_host_stale_lease_dies_by_ttl():
    """A foreign-host lease can't be PID-probed, but TTL staleness still expires it
    (the only signal available cross-host)."""
    host = lane_lease._hostname()
    now = lane_lease._now()
    lease = {"lane": "f2", "acquired_at": (now - dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "ttl_minutes": 50, "pid": 999_999, "host_id": "OTHERBOX"}
    assert lane_lease._lease_is_dead(lease, now=now, this_host=host) is True


def test_heartbeat_within_ttl_keeps_an_old_acquire():
    """A lease ACQUIRED long ago but HEARTBEATING recently is alive — heartbeat_at
    wins over acquired_at (the SPINNING-but-alive worker)."""
    host = lane_lease._hostname()
    now = lane_lease._now()
    lease = {"lane": "hb",
             "acquired_at": (now - dt.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "heartbeat_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "ttl_minutes": 50, "pid": os.getpid(), "host_id": host}
    assert lane_lease._lease_is_dead(lease, now=now, this_host=host) is False


def test_predicate_fault_does_not_drop_a_lease():
    """A malformed lease that raises inside the predicate is KEPT by `_expire_dead`
    (never let a probe/parse fault blank a real lease)."""
    host = lane_lease._hostname()
    now = lane_lease._now()
    bad = {"lane": "bad", "pid": "not-an-int", "acquired_at": object()}  # forces a path quirk
    kept = lane_lease._expire_dead([bad], now=now, this_host=host)
    assert bad in kept


# ── the wired live_leases seam (the contention vs reclaim split) ──────────────

def test_live_leases_default_keeps_dead_orphan_for_reclaim(cfg):
    """`expire_dead=False` (DEFAULT) keeps a dead orphan VISIBLE so `adopt()` /
    orphan-reclaim can transfer it — the reclaim view."""
    _seed_acquire(cfg, lane="apply", holder="orphan:1", pid=1, acquired_min_ago=180)
    live = lane_lease.live_leases(cfg)  # default = structural fold
    assert any(l["lane"] == "apply" for l in live), "reclaim view must keep the orphan"


def test_live_leases_contention_drops_dead_orphan(cfg):
    """`expire_dead=True` (the admission/contention view) DROPS the same dead
    orphan, so the hook self-heals and a live acquirer is not refused by a phantom."""
    _seed_acquire(cfg, lane="apply", holder="orphan:1", pid=1, acquired_min_ago=180)
    live = lane_lease.live_leases(cfg, expire_dead=True)
    assert not any(l["lane"] == "apply" for l in live), "contention view must drop the phantom"


def test_live_leases_contention_keeps_a_fresh_live_lease(cfg):
    """The contention view must NOT drop a genuinely-live lease — only the dead.
    A fresh ACQUIRE by our own live PID still gates a colliding acquirer."""
    _seed_acquire(cfg, lane="apply", holder="me:1", pid=os.getpid(),
                  acquired_min_ago=0, heartbeat_min_ago=0)
    live = lane_lease.live_leases(cfg, expire_dead=True)
    assert any(l["lane"] == "apply" for l in live), "a live lane must still gate"


def test_acquire_expires_stale_orphan_but_preserves_fresh_ephemeral_holder(cfg):
    """The acquisition gate converges stale WAL claims without double-booking fresh ones."""
    _seed_acquire(cfg, lane="apply", holder="orphan:1", pid=1, acquired_min_ago=180)

    rescued = lane_lease.acquire(cfg, lane="apply", kind="keyword",
                                 tree=["apply/**"], owner="rescuer:2")
    assert rescued.journaled is True, "a proven stale orphan must not wedge acquire"

    _seed_acquire(cfg, lane="fresh", holder="ephemeral:1", pid=1, acquired_min_ago=0)
    blocked = lane_lease.acquire(cfg, lane="fresh", kind="keyword",
                                 tree=["fresh/**"], owner="racer:2")
    assert blocked.journaled is False,         "a fresh reservation survives its ephemeral acquire subprocess"


def test_hook_live_leases_for_uses_contention_view(cfg, monkeypatch):
    """The PRE-admission hook seam (`pretool_sensor.live_leases_for`) reads the
    CONTENTION view, so a phantom orphan does not revoke the session's tools."""
    from dos import pretool_sensor
    _seed_acquire(cfg, lane="apply", holder="orphan:1", pid=1, acquired_min_ago=180)
    leases = pretool_sensor.live_leases_for(cfg)
    assert not any(l["lane"] == "apply" for l in leases), \
        "the hook must not see the dead phantom lane"
