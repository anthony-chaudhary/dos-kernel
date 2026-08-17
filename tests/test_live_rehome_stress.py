"""DoS / stress proof for live-session rehoming (docs/391).

"Prove it out with DOS" — the adversarial pass on the rehome machinery. The threat model
is a FLEET under load: many sessions crossing their caps at once, repeatedly, concurrently.
The four ways that could go wrong, each pinned here:

  1. **Concurrent handoff store** — a real fleet has many independent processes writing
     rotation handoffs at the same instant. The store is an atomic write-tmp-then-replace
     per session; this hammers it from many threads (distinct sessions = the real shape, and
     a pathological same-session pile-up) and proves a concurrent reader NEVER crashes and
     NEVER reads a torn/partial record — the os.replace atomicity holds under contention.

  2. **Thundering herd / stampede** — when N sessions all wall together, the danger is they
     all rotate onto the SAME alternate and instantly wall IT (one wall becomes two). This
     pins ``plan_fleet`` at scale: the herd FANS OUT across the serving alternates roughly by
     headroom, and no single alternate absorbs the whole herd.

  3. **Breaker flood as a loop-stopper** — a non-retriable wall hit forever would loop the
     asyncRewake. This floods the breaker and proves it OPENS and STAYS bounded (counts don't
     overflow, verdict is stably OPEN→HUMAN) so the rewake loop is capped.

  4. **Per-session store bound** — repeated rotation of ONE session must not fill the disk
     with handoff files. This proves the store keeps exactly ONE record per session
     (latest-wins), regardless of how many times it rotates.

Run:  python -m pytest tests/test_live_rehome_stress.py -q
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dos import breaker as _brk
from dos import config as _config
from dos import live_rotation as lr
from dos import rotation_handoff as _rh


def _cfg(tmp_path: Path):
    return _config.default_config(tmp_path)


def _seat(name, kind, util=None):
    return lr.Seat(name=name, kind=kind, utilization=util)


# --------------------------------------------------------------------------- #
# 1. Concurrent handoff store — atomic replace under real contention
# --------------------------------------------------------------------------- #
def test_concurrent_distinct_session_writes_all_land_intact(tmp_path):
    """The real fleet shape: N independent sessions each write their own handoff at once.
    Every one must land as a complete, readable record (independent files, no collision)."""
    cfg = _cfg(tmp_path)
    N = 600

    def _write(i):
        return _rh.write_handoff(
            cfg, f"sess-{i:04d}",
            _rh.RotationHandoff(to_account=f"acct{i % 5}",
                                env={"CLAUDE_CONFIG_DIR": f"/seats/acct{i % 5}"}),
            now_ms=i,
        )

    with ThreadPoolExecutor(max_workers=32) as ex:
        results = list(ex.map(_write, range(N)))
    assert all(results)  # every independent write succeeded
    # Every record reads back complete and correct — no tears, no losses.
    for i in range(N):
        h = _rh.read_handoff(cfg, f"sess-{i:04d}")
        assert h is not None, f"sess-{i:04d} lost"
        assert h.to_account == f"acct{i % 5}"
        assert h.ts_ms == i


def test_concurrent_same_session_pileup_never_tears_a_read(tmp_path):
    """Pathological: many writers AND readers hammer the SAME session's handoff at once.
    Writers may race (some lose and return False), but a reader must NEVER raise and NEVER
    see a torn/partial record — os.replace guarantees the live file is always a complete
    prior write. The final record is always one of the written values."""
    cfg = _cfg(tmp_path)
    sid = "hot-session"
    valid_targets = {f"acct{i}" for i in range(8)}
    torn_or_crash = []

    def _writer(i):
        try:
            _rh.write_handoff(
                cfg, sid,
                _rh.RotationHandoff(to_account=f"acct{i % 8}",
                                    env={"CLAUDE_CONFIG_DIR": f"/seats/{i % 8}"}),
                now_ms=i,
            )
        except Exception as e:  # noqa: BLE001 — a write must never raise
            torn_or_crash.append(("write", repr(e)))

    def _reader(_):
        try:
            h = _rh.read_handoff(cfg, sid)
            # h may be None only before the first write commits; once present it is whole.
            if h is not None and h.to_account not in valid_targets:
                torn_or_crash.append(("torn", h.to_account))
        except Exception as e:  # noqa: BLE001 — a read must never raise
            torn_or_crash.append(("read", repr(e)))

    work = []
    with ThreadPoolExecutor(max_workers=24) as ex:
        for i in range(400):
            work.append(ex.submit(_writer, i))
            work.append(ex.submit(_reader, i))
        for f in work:
            f.result()
    assert torn_or_crash == [], f"durability violation under contention: {torn_or_crash[:5]}"
    # The store still holds exactly one valid record for the session afterward.
    final = _rh.read_handoff(cfg, sid)
    assert final is not None and final.to_account in valid_targets


# --------------------------------------------------------------------------- #
# 2. Thundering herd — the spread must not stampede one alternate
# --------------------------------------------------------------------------- #
def test_thundering_herd_fans_out_not_stampedes():
    """2000 sessions all near-cap, sharing 5 equal serving alternates. The herd must spread
    ~evenly — no alternate gets more than its fair share + slack — never all 2000 onto one."""
    N = 2000
    K = 5
    sessions = [(f"s{i}", _seat(f"seat{i}", lr.KIND_NEAR_CAP, 0.97)) for i in range(N)]
    alts = [_seat(f"alt{j}", lr.KIND_SERVING, 0.0) for j in range(K)]
    decisions = lr.plan_fleet(sessions, alts, now_epoch=1.0)
    targets = [d.to_account for d in decisions.values()]
    assert all(d.verdict is lr.LiveVerdict.ROTATE for d in decisions.values())
    counts = {a.name: targets.count(a.name) for a in alts}
    fair = N / K
    # Every alternate is used, and none absorbs more than ~fair share (+1 for remainder).
    assert all(c > 0 for c in counts.values()), counts
    assert max(counts.values()) <= fair + 1, counts
    assert min(counts.values()) >= fair - 1, counts


def test_thundering_herd_respects_headroom_under_skew():
    """A near-empty alternate must absorb more of the herd than a near-cap one (so the
    fan-out maximises total headroom before any alternate walls)."""
    N = 1000
    sessions = [(f"s{i}", _seat(f"seat{i}", lr.KIND_NEAR_CAP, 0.95)) for i in range(N)]
    alts = [_seat("roomy", lr.KIND_SERVING, 0.0), _seat("tight", lr.KIND_NEAR_CAP, 0.85)]
    decisions = lr.plan_fleet(sessions, alts, now_epoch=1.0)
    targets = [d.to_account for d in decisions.values()]
    assert targets.count("roomy") > targets.count("tight")
    assert targets.count("tight") > 0  # the tight window still takes a sliver


def test_herd_with_no_serving_alternate_does_not_invent_one():
    """If every alternate is walled too, the herd must NOT rotate (no phantom target) —
    each session falls to its WALL_WAIT/NO_ALTERNATE verdict."""
    sessions = [(f"s{i}", _seat(f"seat{i}", lr.KIND_WALLED, 1.0, )) for i in range(50)]
    alts = [_seat("alt0", lr.KIND_WALLED, 1.0)]
    decisions = lr.plan_fleet(sessions, alts, now_epoch=1.0)
    assert all(d.verdict is not lr.LiveVerdict.ROTATE for d in decisions.values())


# --------------------------------------------------------------------------- #
# 3. Breaker flood — the rewake loop is bounded
# --------------------------------------------------------------------------- #
def test_breaker_flood_opens_and_stays_bounded():
    """A non-retriable wall hit forever must stop looping. Flood the breaker: it OPENS at
    the threshold and the verdict stays stably OPEN (counts grow but the decision is fixed),
    so the asyncRewake side reads OPEN and stops re-launching."""
    policy = _brk.BreakerPolicy(max_consecutive=5, max_total=50, on_trip=_brk.Escalation.HUMAN)
    counts = _brk.BreakerCounts()
    opened_at = None
    for i in range(5000):
        t = _brk.record_failure(counts, policy)
        counts = t.counts
        if t.verdict.is_open and opened_at is None:
            opened_at = i + 1
    assert opened_at == 5  # opened the moment consecutive hit the threshold
    final = _brk.classify(counts, policy)
    assert final.is_open and final.escalation is _brk.Escalation.HUMAN
    # counts are plain ints — no overflow, no unbounded structure
    assert counts.consecutive == 5000 and counts.total == 5000


def test_breaker_heals_then_re_trips_under_flapping():
    """A flapping seat (fail, succeed, fail, …) never trips the consecutive rung but MUST
    trip the total rung — the flap can't dodge the breaker forever."""
    policy = _brk.BreakerPolicy(max_consecutive=5, max_total=20, on_trip=_brk.Escalation.HUMAN)
    counts = _brk.BreakerCounts()
    tripped = False
    for _ in range(40):
        counts = _brk.record_failure(counts, policy).counts
        v = _brk.record_success(counts, policy)  # immediately recover (resets consecutive)
        counts = v.counts
        if v.verdict.is_open:
            tripped = True
    assert tripped  # the total rung caught the flap


# --------------------------------------------------------------------------- #
# 4. Per-session store bound — repeated rotation can't fill the disk
# --------------------------------------------------------------------------- #
def test_repeated_rotation_keeps_exactly_one_record_per_session(tmp_path):
    cfg = _cfg(tmp_path)
    sid = "churner"
    for i in range(3000):
        _rh.write_handoff(
            cfg, sid,
            _rh.RotationHandoff(to_account=f"acct{i % 4}", env={"K": str(i)}),
            now_ms=i,
        )
    # Exactly ONE file for the session — latest-wins overwrote, never accumulated.
    rotation_dir = _rh.rotation_dir(cfg)
    files = list(rotation_dir.glob("*.json"))
    assert len(files) == 1, [f.name for f in files]
    h = _rh.read_handoff(cfg, sid)
    assert h is not None and h.env["K"] == "2999"  # the last write won


def test_distinct_sessions_each_keep_one_record(tmp_path):
    cfg = _cfg(tmp_path)
    for i in range(300):
        _rh.write_handoff(cfg, f"s{i}", _rh.RotationHandoff(to_account="a", env={"K": "v"}))
    files = list(_rh.rotation_dir(cfg).glob("*.json"))
    assert len(files) == 300  # one per distinct session, no fan-out explosion
    # all parse cleanly
    for f in files:
        json.loads(f.read_text(encoding="utf-8"))
