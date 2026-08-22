"""Lane-lease write-back — the effectful boundary over the pure arbiter + WAL.

The lane-lease layer (`lane_lease.py`, L3) is the I/O shell that turns a pure
`arbiter.arbitrate` verdict into a durable WAL record so cross-process
orchestrator branches see each other's grants. `acquire`/`release`/`halt` were
already pinned (`test_lane_halt.py`); this module pins the writers that close the
LJ write side end-to-end:

  * `heartbeat()` — the writer that makes liveness SPINNING reachable from REAL
    journal evidence (the headline gap: nothing wrote an OP_HEARTBEAT, so the
    newest beat was always the boundary ACQUIRE, which aged out → STALLED, and
    SPINNING was unreachable in production). The load-bearing property is that a
    beat lands ONLY for a currently-live lease — a stray post-release beat must
    not let the fold read a dead run alive.
  * the REFUSE side-record — a contended `acquire` now journals WHY, so the WAL
    can answer its own raison d'être ("why was I refused at 14:03?").
  * `compact_journal()` — the crash-safe, verdict-preserving WAL bounding op.

Conventions mirror `test_lane_halt.py`: a `cfg` fixture pins BOTH the lock and
the journal at tmp paths, so every record lands in isolation and we can assert
nothing is written under the package tree (the SubstrateConfig boundary litmus).
"""

from __future__ import annotations

import datetime as dt
import os

import pytest

from dos import lane_lease, lane_journal
from dos import config as _config
from dos import journal_delta, liveness
from dos.lane_journal import (
    OP_ACQUIRE,
    OP_CHECKPOINT,
    OP_HEARTBEAT,
    OP_REFUSE,
    read_all,
    replay,
)

_MIN = 60 * 1000


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    # Pin BOTH the lock and the journal at tmp paths (the test_lane_halt idiom):
    # the records land in isolation and we can assert the package tree is
    # untouched. The generic default taxonomy ('main'/'global') is in force.
    monkeypatch.setenv("DISPATCH_LANE_LEASE_LOCK_PATH",
                       str(tmp_path / ".lane-lease.lock"))
    monkeypatch.setenv("DISPATCH_LANE_JOURNAL_PATH",
                       str(tmp_path / "lane-journal.jsonl"))
    return _config.default_config(str(tmp_path))


def _journal(cfg):
    return lane_lease._journal_path(cfg)


def _ops(cfg):
    from collections import Counter
    return Counter(str(e.get("op")) for e in read_all(_journal(cfg)))


# ---------------------------------------------------------------------------
# REFUSE — a contended acquire records WHY (the WAL answers "why was I refused?").
# ---------------------------------------------------------------------------

def test_acquire_refused_appends_one_refuse(cfg):
    """A first acquire takes the exclusive 'global' lane; a second acquire that
    contends it is REFUSED and appends exactly one OP_REFUSE. `journaled` is
    False (its contract is 'the GRANT was recorded', which a refuse is not), and
    replay still shows only the blocking lease. The journal can now answer the
    question its own docstring poses — why a request was denied."""
    first = lane_lease.acquire(cfg, lane="global", kind="global", tree=["**/*"],
                               owner="holder", loop_ts="2026-06-02T12:00Z")
    assert first.decision.outcome == "acquire"

    refused = lane_lease.acquire(cfg, lane="global", kind="global", tree=["**/*"],
                                 owner="latecomer", loop_ts="2026-06-02T12:01Z")
    assert refused.decision.outcome == "refuse"
    assert refused.journaled is False  # a refuse never sets journaled

    refuses = [e for e in read_all(_journal(cfg)) if e.get("op") == OP_REFUSE]
    assert len(refuses) == 1
    r = refuses[0]
    assert r["holder"] == "latecomer"
    assert r["reason"]  # carries the arbiter's prose reason
    # The grant is intact: replay shows exactly the one blocking lease.
    live = replay(read_all(_journal(cfg)))
    assert len(live) == 1
    assert live[0]["holder"] == "holder"


def test_refuse_record_failure_does_not_change_decision(cfg, monkeypatch):
    """If journaling the refuse RAISES (a disk error), acquire still returns the
    refuse decision — a failed RECORD must never convert a refuse into anything
    else (the best-effort, swallow-OSError discipline mirroring halt())."""
    lane_lease.acquire(cfg, lane="global", kind="global", tree=["**/*"],
                       owner="holder", loop_ts="2026-06-02T12:00Z")

    real_append = lane_journal.append

    def _selective(entry, *a, **k):
        if str(entry.get("op")) == OP_REFUSE:
            raise OSError("disk full on the refuse record")
        return real_append(entry, *a, **k)

    monkeypatch.setattr(lane_journal, "append", _selective)
    refused = lane_lease.acquire(cfg, lane="global", kind="global", tree=["**/*"],
                                 owner="latecomer", loop_ts="2026-06-02T12:01Z")
    assert refused.decision.outcome == "refuse"  # the decision is unchanged


@pytest.mark.parametrize(
    ("first_kind", "second_kind", "first_owner", "second_owner"),
    [
        ("cluster", "keyword", "root-issue-8195", "root-tui-settings-8213"),
        ("keyword", "cluster", "root-tui-settings-8213", "root-issue-8195"),
    ],
)
def test_acquire_refuses_later_exclusive_tree_overlap_across_lane_kinds(
        cfg, first_kind, second_kind, first_owner, second_owner):
    """The WAL mutex makes exact-tree ownership authoritative across lane kinds.

    This is the #8239 production shape in both orders. The first holder owns two
    shared paths plus one private path. The later holder asks for those same two
    paths plus a different private path. Only the first grant may enter the WAL;
    the refusal must identify who holds each intersecting path.
    """
    earlier_tree = [
        "cmd/fak/tui.go",
        "cmd/fak/tui_registry.go",
        "cmd/fak/tui_issue_8195.go",
    ]
    later_tree = [
        "cmd/fak/tui.go",
        "cmd/fak/tui_registry.go",
        "cmd/fak/tui_settings_8213.go",
    ]
    first = lane_lease.acquire(
        cfg, lane="cmd", kind=first_kind, tree=earlier_tree,
        owner=first_owner, mode="exclusive", loop_ts="20260820T173028Z",
    )
    assert first.decision.outcome == "acquire"

    refused = lane_lease.acquire(
        cfg, lane="cmd", kind=second_kind, tree=later_tree,
        owner=second_owner, mode="exclusive", loop_ts="20260820T173652Z",
    )
    assert refused.decision.outcome == "refuse"
    assert first_owner in refused.decision.reason
    assert second_owner in refused.decision.reason
    assert "cmd/fak/tui.go" in refused.decision.reason
    assert "cmd/fak/tui_registry.go" in refused.decision.reason

    entries = read_all(_journal(cfg))
    assert [entry["op"] for entry in entries] == [OP_ACQUIRE, OP_REFUSE]
    assert entries[1]["holder"] == second_owner
    [held] = replay(entries)
    assert held["holder"] == first_owner
    assert held["tree"] == earlier_tree


def test_acquire_allows_disjoint_exclusive_trees_in_same_named_lane(cfg):
    """A lane name is metadata; disjoint exact trees remain independently leaseable."""
    first = lane_lease.acquire(
        cfg, lane="cmd", kind="cluster", tree=["cmd/fak/tui.go"],
        owner="tui-owner", mode="exclusive", loop_ts="20260820T180000Z",
    )
    second = lane_lease.acquire(
        cfg, lane="cmd", kind="keyword", tree=["cmd/fak/settings.go"],
        owner="settings-owner", mode="exclusive", loop_ts="20260820T180001Z",
    )

    assert first.decision.outcome == "acquire"
    assert second.decision.outcome == "acquire"
    assert [lease["holder"] for lease in lane_lease.live_leases(cfg)] == [
        "tui-owner", "settings-owner",
    ]


# ---------------------------------------------------------------------------
# HEARTBEAT — beats only a live lease; the load-bearing false-revive guard.
# ---------------------------------------------------------------------------

def test_heartbeat_beats_only_a_live_lease(cfg):
    """acquire then heartbeat → True and appends one OP_HEARTBEAT carrying the
    lease's true (loop_ts, lane); a heartbeat on an unheld lane → False and
    appends NOTHING (the live-lease match guard)."""
    res = lane_lease.acquire(cfg, lane="", kind="concurrent", tree=["a/**"],
                             owner="w1", loop_ts="2026-06-02T12:00Z")
    held_lane = res.decision.lane  # 'main' under the generic default

    ok = lane_lease.heartbeat(cfg, lane=held_lane, owner="w1",
                              loop_ts="2026-06-02T12:00Z")
    assert ok is True
    beats = [e for e in read_all(_journal(cfg)) if e.get("op") == OP_HEARTBEAT]
    assert len(beats) == 1
    assert beats[0]["lane"] == held_lane
    assert beats[0]["loop_ts"] == "2026-06-02T12:00Z"

    # An unheld lane is not beaten and writes nothing.
    before = _ops(cfg)
    miss = lane_lease.heartbeat(cfg, lane="nonesuch", owner="w1",
                                loop_ts="2026-06-02T99:99Z")
    assert miss is False
    assert _ops(cfg) == before  # not a single new entry


def test_post_release_stray_beat_cannot_revive(cfg):
    """ADVERSARIAL REGRESSION: acquire, RELEASE, then heartbeat → False and writes
    nothing. `journal_delta.fold_since` credits a beat by identity+ts with NO
    held-lease check (journal_delta.py: the HEARTBEAT-freshness rung), so a stray
    beat appended after a RELEASE for the same (loop_ts, lane) would let the fold
    read a DEAD run alive — a false ADVANCING/SPINNING. The writer-side defense is
    that heartbeat() only beats a CURRENTLY-live lease."""
    res = lane_lease.acquire(cfg, lane="", kind="concurrent", tree=["a/**"],
                             owner="w1", loop_ts="2026-06-02T12:00Z")
    held_lane = res.decision.lane
    assert lane_lease.release(cfg, lane=held_lane, owner="w1",
                              loop_ts="2026-06-02T12:00Z") is True

    before = _ops(cfg)
    stray = lane_lease.heartbeat(cfg, lane=held_lane, owner="w1",
                                 loop_ts="2026-06-02T12:00Z")
    assert stray is False
    assert _ops(cfg) == before  # no stray HEARTBEAT written — the hole stays shut


def test_non_holder_cannot_beat_a_live_lease(cfg):
    """ADVERSARIAL REGRESSION (false-SPINNING): a HEARTBEAT must be authenticated by
    the lease's holder, because it is the one fail-DANGEROUS lease op — it REVIVES a
    lease's beat, and `journal_delta.fold_since` credits that beat with no held-lease
    check, so a beat from anyone-but-the-holder flips a crashed orphan's verdict from
    STALLED (the orphan-sweep's input) to SPINNING (alive). Two holes once let a
    non-holder beat a LIVE lease:

      * owner="" — the empty-owner wildcard bypassed the holder guard entirely;
      * a lease with holder=None (a foreign / RECONCILE / inline-fields ACQUIRE the
        replay fold supports) matched ANY non-empty owner.

    Both are now refused: a beat requires owner to non-emptily, EXACTLY equal the
    lease's recorded holder. The legitimate holder beating its own lease still works
    (test_heartbeat_beats_only_a_live_lease)."""
    res = lane_lease.acquire(cfg, lane="global", kind="global", tree=["**/*"],
                             owner="A", loop_ts="2026-06-02T12:00Z")
    assert res.decision.outcome == "acquire"

    # Hole 1: a DIFFERENT owner B (non-empty) cannot beat A's properly-held lease.
    before = _ops(cfg)
    assert lane_lease.heartbeat(cfg, lane="global", owner="B",
                                loop_ts="2026-06-02T12:00Z") is False
    # Hole 2: owner="" cannot authenticate a beat on A's lease.
    assert lane_lease.heartbeat(cfg, lane="global", owner="",
                                loop_ts="2026-06-02T12:00Z") is False
    assert _ops(cfg) == before  # neither wrote a single HEARTBEAT


def test_holder_none_lease_is_unbeatable(cfg):
    """ADVERSARIAL REGRESSION (false-SPINNING): a LIVE lease with no recorded holder
    (an inline-fields ACQUIRE a foreign / forward-compat / RECONCILE writer may emit,
    which `replay` reconstructs with holder=None) is UNATTRIBUTABLE — no owner can
    prove it holds it, so NO owner may beat it. It can only age out to STALLED and be
    scavenged, the correct fate of a lease whose holder can't be proven. Before the
    fix, the guard's `holder not in (owner, None)` clause let ANY non-empty owner beat
    such a lease, reviving a crashed orphan to SPINNING."""
    jp = _journal(cfg)
    # An inline-fields ACQUIRE with NO holder and NO nested 'lease' — exactly what
    # replay reconstructs to a holder=None live lease.
    lane_journal.append({
        "op": OP_ACQUIRE, "lane": "global", "lane_kind": "global", "tree": ["**/*"],
        "loop_ts": "2026-06-02T12:00Z", "host_id": "hostA", "pid": 4242,
        "acquired_at": "2026-06-02T12:00:00Z",
    }, jp)
    live = replay(read_all(jp))
    assert len(live) == 1 and live[0].get("holder") is None  # the unattributable lease

    before = _ops(cfg)
    assert lane_lease.heartbeat(cfg, lane="global", owner="evil",
                                loop_ts="2026-06-02T12:00Z") is False
    assert lane_lease.heartbeat(cfg, lane="global", owner="",
                                loop_ts="2026-06-02T12:00Z") is False
    assert _ops(cfg) == before  # nothing beat the holderless lease


def test_heartbeat_is_a_beat_not_an_event(cfg):
    """After N heartbeat() calls on a held lease, fold_since reports
    events_since_start == 0 — HEARTBEAT is excluded from _EVENT_OPS, so it can
    never fabricate ADVANCING. The fresh beat proves life, not progress."""
    res = lane_lease.acquire(cfg, lane="", kind="concurrent", tree=["a/**"],
                             owner="w1", loop_ts="2026-06-02T12:00Z")
    held_lane = res.decision.lane
    for _ in range(3):
        assert lane_lease.heartbeat(cfg, lane=held_lane, owner="w1",
                                    loop_ts="2026-06-02T12:00Z") is True

    entries = read_all(_journal(cfg))
    # Place "now" just after the appended beats; run started well before.
    beats = [e for e in entries if e.get("op") == OP_HEARTBEAT]
    beat_ms = _iso_to_ms(beats[-1]["ts"])
    jd = journal_delta.fold_since(
        entries, run_started_ms=beat_ms - 40 * _MIN, now_ms=beat_ms + 1 * _MIN,
        lease_key=("2026-06-02T12:00Z", held_lane))
    assert jd.events_since_start == 0  # the ACQUIRE birth + the beats — none count
    assert jd.newest_heartbeat_age_ms is not None  # but the beat proves life


def test_heartbeat_makes_spinning_reachable_end_to_end(cfg):
    """THE HERO: acquire a lane and heartbeat it; on a realistic production
    timeline (run started 40 min ago — past grace; the beat refreshed 2 min ago —
    fresh) the journal fold yields a FRESH beat age AND zero events, and
    liveness.classify reaches SPINNING. WITHOUT the heartbeat, the boundary
    ACQUIRE is 40 min old → STALLED. This proves the writer feeds the real fold
    that test_journal_delta only fakes with frozen entries — the headline gap
    (SPINNING unreachable from real evidence) is closed."""
    res = lane_lease.acquire(cfg, lane="", kind="concurrent", tree=["a/**"],
                             owner="w1", loop_ts="2026-06-02T12:00Z")
    held_lane = res.decision.lane
    assert lane_lease.heartbeat(cfg, lane=held_lane, owner="w1",
                                loop_ts="2026-06-02T12:00Z") is True

    entries = read_all(_journal(cfg))
    lease_key = ("2026-06-02T12:00Z", held_lane)
    beat = [e for e in entries if e.get("op") == OP_HEARTBEAT][-1]
    beat_ms = _iso_to_ms(beat["ts"])
    now_ms = beat_ms + 2 * _MIN          # beat 2 min old: fresh (≤ 15-min spin)
    run_started_ms = beat_ms - 40 * _MIN  # run 40 min old: past 30-min grace

    pol = liveness.LivenessPolicy()  # defaults: 30-min grace, 15-min spin

    jd = journal_delta.fold_since(entries, run_started_ms=run_started_ms,
                                  now_ms=now_ms, lease_key=lease_key)
    assert jd.events_since_start == 0
    assert jd.newest_heartbeat_age_ms == 2 * _MIN
    ev = liveness.ProgressEvidence(
        commits_since_start=0, journal_events_since=jd.events_since_start,
        run_started_ms=run_started_ms, now_ms=now_ms,
        last_heartbeat_age_ms=jd.newest_heartbeat_age_ms)
    assert liveness.classify(ev, pol).verdict is liveness.Liveness.SPINNING

    # The OLD WORLD (no HEARTBEAT writer): on a REAL production timeline the
    # boundary ACQUIRE is stamped at run-START, so by `now` it is 40 min old and
    # ages past the 15-min spin window → STALLED, and SPINNING is unreachable.
    # (We must construct this with the ACQUIRE at run-start: the real writer
    # stamps the ACQUIRE and the HEARTBEAT in the same test instant, so simply
    # dropping the beat would leave an equally-fresh ACQUIRE — the age-out is a
    # property of elapsed wall-clock, which only a constructed timeline exhibits.)
    old_world = [{
        "op": OP_ACQUIRE, "lane": held_lane, "loop_ts": "2026-06-02T12:00Z",
        "ts": _ms_to_iso(run_started_ms),  # the boundary beat, at run-start
        "lease": {"lane": held_lane, "loop_ts": "2026-06-02T12:00Z"},
    }]
    jd_old = journal_delta.fold_since(old_world, run_started_ms=run_started_ms,
                                      now_ms=now_ms, lease_key=lease_key)
    # The boundary beat is at run-start, so its age from `now` is the full run-age
    # (42 min here) — well past the 15-min spin window → it has aged out.
    assert jd_old.newest_heartbeat_age_ms == now_ms - run_started_ms
    ev_old = liveness.ProgressEvidence(
        commits_since_start=0, journal_events_since=jd_old.events_since_start,
        run_started_ms=run_started_ms, now_ms=now_ms,
        last_heartbeat_age_ms=jd_old.newest_heartbeat_age_ms)
    assert liveness.classify(ev_old, pol).verdict is liveness.Liveness.STALLED


# ---------------------------------------------------------------------------
# BEAT COALESCING (docs/106 §3.1a) — the WAL-drain brake. Default writes every
# beat (unchanged); an opt-in floor elides a redundant beat without changing the
# liveness verdict.
# ---------------------------------------------------------------------------

def _at(monkeypatch, when: dt.datetime):
    """Pin BOTH clocks at `when`: lane_lease._now (drives the coalescing age check +
    the beat's heartbeat_at stamp) and lane_journal.journal_now_iso (the entry `ts`
    that fold_since trusts first). Patching both keeps the elision decision and the
    folded verdict on ONE deterministic timeline — otherwise append stamps `ts` from
    the real wall clock and the fold reads real-time ages, not the test's."""
    iso = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    monkeypatch.setattr(lane_lease, "_now", lambda: when)
    monkeypatch.setattr(lane_journal, "journal_now_iso", lambda: iso)


def test_coalesce_default_writes_every_beat(cfg):
    """The DEFAULT (coalesce_within_s=0.0) is byte-identical to the pre-coalescing
    writer: every beat appends one HEARTBEAT. The brake is strictly opt-in."""
    res = lane_lease.acquire(cfg, lane="", kind="concurrent", tree=["a/**"],
                             owner="w1", loop_ts="2026-06-02T12:00Z")
    held = res.decision.lane
    for _ in range(4):
        assert lane_lease.heartbeat(cfg, lane=held, owner="w1",
                                    loop_ts="2026-06-02T12:00Z") is True
    beats = [e for e in read_all(_journal(cfg)) if e.get("op") == OP_HEARTBEAT]
    assert len(beats) == 4  # nothing elided when the floor is 0


def test_coalesce_elides_a_fresh_redundant_beat(cfg, monkeypatch):
    """With a 60 s floor, a second beat 10 s after the first is ELIDED — nothing is
    appended — yet the call still returns True (the lease is live and recently
    beaten). A beat past the floor writes again. This is the WAL-drain reduction:
    one line per floor-window, not one per beat."""
    t0 = dt.datetime(2026, 6, 2, 12, 0, 0, tzinfo=dt.timezone.utc)
    _at(monkeypatch, t0)
    res = lane_lease.acquire(cfg, lane="", kind="concurrent", tree=["a/**"],
                             owner="w1", loop_ts="2026-06-02T12:00Z")
    held = res.decision.lane

    # First beat at t0+5s lands (acquire's boundary beat is at t0, age 5 < 60 →
    # actually elided too; so assert from the count, not an assumption).
    _at(monkeypatch, t0 + dt.timedelta(seconds=5))
    assert lane_lease.heartbeat(cfg, lane=held, owner="w1",
                                loop_ts="2026-06-02T12:00Z",
                                coalesce_within_s=60.0) is True
    # The acquire stamped acquired_at=t0 (the first beat); +5s is < 60s → elided.
    assert [e for e in read_all(_journal(cfg)) if e.get("op") == OP_HEARTBEAT] == []

    # +90s from the boundary beat: now past the 60 s floor → a real beat is written.
    _at(monkeypatch, t0 + dt.timedelta(seconds=90))
    assert lane_lease.heartbeat(cfg, lane=held, owner="w1",
                                loop_ts="2026-06-02T12:00Z",
                                coalesce_within_s=60.0) is True
    beats = [e for e in read_all(_journal(cfg)) if e.get("op") == OP_HEARTBEAT]
    assert len(beats) == 1  # exactly the one past-floor beat

    # +95s: only 5 s since the beat we just wrote → elided again.
    _at(monkeypatch, t0 + dt.timedelta(seconds=95))
    assert lane_lease.heartbeat(cfg, lane=held, owner="w1",
                                loop_ts="2026-06-02T12:00Z",
                                coalesce_within_s=60.0) is True
    beats = [e for e in read_all(_journal(cfg)) if e.get("op") == OP_HEARTBEAT]
    assert len(beats) == 1  # still one — the fresh beat suppressed the next


def test_coalesce_unparseable_stamp_never_elides(cfg, monkeypatch):
    """A lease whose freshest stamp can't be parsed yields age None → the beat is
    ALWAYS written (the safe direction: never suppress a beat we can't prove is
    fresh). Constructed via an inline ACQUIRE with a malformed acquired_at and no
    heartbeat_at, beaten by its holder."""
    jp = _journal(cfg)
    lane_journal.append({
        "op": OP_ACQUIRE, "lane": "global", "lane_kind": "global", "tree": ["**/*"],
        "loop_ts": "2026-06-02T12:00Z", "host_id": "hostA", "pid": 7,
        "acquired_at": "not-a-timestamp",
        "lease": {"lane": "global", "loop_ts": "2026-06-02T12:00Z",
                  "holder": "w1", "acquired_at": "not-a-timestamp"},
    }, jp)
    assert any(l.get("holder") == "w1" for l in replay(read_all(jp)))
    _at(monkeypatch, dt.datetime(2026, 6, 2, 12, 0, 1, tzinfo=dt.timezone.utc))
    assert lane_lease.heartbeat(cfg, lane="global", owner="w1",
                                loop_ts="2026-06-02T12:00Z",
                                coalesce_within_s=600.0) is True
    beats = [e for e in read_all(jp) if e.get("op") == OP_HEARTBEAT]
    assert len(beats) == 1  # written despite the huge floor — None age never elides


def test_coalesce_is_verdict_preserving(cfg, monkeypatch):
    """THE LOAD-BEARING PROPERTY (docs/106 §3.1a): a coalesced journal and a
    full-beat journal yield the SAME liveness verdict. We beat every 30 s for 12
    min on two identical leases — one with a 300 s coalescing floor, one without —
    then classify both at a production `now`. Coalescing writes far fewer lines but
    the verdict is identical (SPINNING here: alive, past grace, zero events). Proof
    the brake only ages an existing beat, never crosses the spin_ms alive bound."""
    pol = liveness.LivenessPolicy()  # 30-min grace, 15-min spin
    t0 = dt.datetime(2026, 6, 2, 12, 0, 0, tzinfo=dt.timezone.utc)
    jp = _journal(cfg)
    held = "main"  # the generic concurrent default

    def _run(owner, loop_ts, floor):
        # Seed the lease directly (a distinct loop_ts → a distinct live lease on the
        # SAME lane name; replay keys by (loop_ts, lane), so two beating leases can
        # coexist without arbitration redirecting the second). The boundary ACQUIRE
        # is the first beat, stamped at t0.
        t0_iso = t0.strftime("%Y-%m-%dT%H:%M:%SZ")
        lane_journal.append({
            "op": OP_ACQUIRE, "lane": held, "lane_kind": "concurrent",
            "tree": ["a/**"], "loop_ts": loop_ts, "host_id": "h", "pid": 1,
            "ts": t0_iso, "acquired_at": t0_iso,
            "lease": {"lane": held, "loop_ts": loop_ts, "holder": owner,
                      "acquired_at": t0_iso},
        }, jp)
        # 24 beats at 30 s spacing → 12 min of beating.
        for i in range(1, 25):
            _at(monkeypatch, t0 + dt.timedelta(seconds=30 * i))
            assert lane_lease.heartbeat(cfg, lane=held, owner=owner, loop_ts=loop_ts,
                                        coalesce_within_s=floor) is True

    _run("wfull", "2026-06-02T12:00Z", 0.0)
    _run("wcoal", "2026-06-02T13:00Z", 300.0)
    full_lane = coal_lane = held

    entries = read_all(jp)
    full_beats = [e for e in entries if e.get("op") == OP_HEARTBEAT
                  and e.get("loop_ts") == "2026-06-02T12:00Z"]
    coal_beats = [e for e in entries if e.get("op") == OP_HEARTBEAT
                  and e.get("loop_ts") == "2026-06-02T13:00Z"]
    assert len(full_beats) == 24                 # every beat written
    assert 0 < len(coal_beats) < len(full_beats)  # far fewer under the 300 s floor

    # Classify both at the SAME production-shaped `now`: run started 40 min before
    # its last beat (past grace), last beat 2 min before now (fresh ≤ 15-min spin).
    def _verdict(lane, loop_ts, beats):
        last_ms = _iso_to_ms(beats[-1]["ts"])
        now_ms = last_ms + 2 * _MIN
        started_ms = last_ms - 38 * _MIN  # 40 min before `now`
        jd = journal_delta.fold_since(entries, run_started_ms=started_ms,
                                      now_ms=now_ms, lease_key=(loop_ts, lane))
        ev = liveness.ProgressEvidence(
            commits_since_start=0, journal_events_since=jd.events_since_start,
            run_started_ms=started_ms, now_ms=now_ms,
            last_heartbeat_age_ms=jd.newest_heartbeat_age_ms)
        return liveness.classify(ev, pol).verdict

    v_full = _verdict(full_lane, "2026-06-02T12:00Z", full_beats)
    v_coal = _verdict(coal_lane, "2026-06-02T13:00Z", coal_beats)
    assert v_full is liveness.Liveness.SPINNING
    assert v_coal == v_full  # IDENTICAL verdict — coalescing changed the line count, not the truth


def test_heartbeat_resolves_via_config_not_file(cfg, tmp_path):
    """The HEARTBEAT lands at the configured journal path; nothing is written
    under the package source tree (the boundary litmus, a clone of
    test_halt_resolves_via_config_not_file)."""
    res = lane_lease.acquire(cfg, lane="", kind="concurrent", tree=["a/**"],
                             owner="w1", loop_ts="2026-06-02T12:00Z")
    lane_lease.heartbeat(cfg, lane=res.decision.lane, owner="w1",
                         loop_ts="2026-06-02T12:00Z")
    assert (tmp_path / "lane-journal.jsonl").exists()
    pkg_dir = os.path.dirname(lane_lease.__file__)
    assert not os.path.exists(os.path.join(pkg_dir, "lane-journal.jsonl"))


# ---------------------------------------------------------------------------
# compact_journal — crash-safe, verdict-preserving WAL bounding.
# ---------------------------------------------------------------------------

def test_compact_journal_preserves_replay_and_shrinks(cfg):
    """A journal with a live lease + much dead history compacts to a single
    CHECKPOINT (+ no corruption) whose replay is byte-identical to before — the
    live lease survives via the snapshot, the dead history is gone, and the file
    shrinks. (The verdict-preserving property, end-to-end through the I/O shell.)"""
    # One lease that stays live, plus churn that all releases (dead history).
    lane_lease.acquire(cfg, lane="", kind="concurrent", tree=["a/**"],
                       owner="keep", loop_ts="2026-06-02T12:00Z")
    for i in range(5):
        lane_lease.acquire(cfg, lane="global", kind="global", tree=["**/*"],
                           owner=f"churn{i}", loop_ts=f"2026-06-02T13:{i:02d}Z")
        lane_lease.release(cfg, lane="global", owner=f"churn{i}",
                           loop_ts=f"2026-06-02T13:{i:02d}Z")

    before_live = replay(read_all(_journal(cfg)))
    before_entries = len(read_all(_journal(cfg)))

    result = lane_lease.compact_journal(cfg)

    after_entries = read_all(_journal(cfg))
    after_live = replay(after_entries)
    assert before_live == after_live  # DIFFERENTIAL EQUIVALENCE (the WAL-safety hero)
    assert [l["holder"] for l in after_live] == ["keep"]  # the live lease survived
    assert result.entries_before == before_entries
    assert result.entries_after == len(after_entries)
    assert result.entries_after < result.entries_before  # it actually shrank
    # The compacted file is a single CHECKPOINT (no _CORRUPT in this run).
    assert [e["op"] for e in after_entries] == [OP_CHECKPOINT]


def test_compact_journal_is_crash_safe_atomic(cfg, monkeypatch, tmp_path):
    """If the atomic_replace mid-rewrite RAISES, the ORIGINAL journal is intact
    (full-old, never torn). Crash-safety: tmp+fsync then atomic_replace — a crash
    leaves either the full old WAL or the full new one."""
    lane_lease.acquire(cfg, lane="", kind="concurrent", tree=["a/**"],
                       owner="keep", loop_ts="2026-06-02T12:00Z")
    original = (tmp_path / "lane-journal.jsonl").read_bytes()
    original_live = replay(read_all(_journal(cfg)))

    from dos import _filelock

    def _boom(src, dst, *a, **k):
        raise OSError("simulated crash during the atomic rename")

    monkeypatch.setattr(_filelock, "atomic_replace", _boom)
    with pytest.raises(OSError):
        lane_lease.compact_journal(cfg)

    # The journal is byte-for-byte the original — no torn rewrite.
    assert (tmp_path / "lane-journal.jsonl").read_bytes() == original
    assert replay(read_all(_journal(cfg))) == original_live


def test_compact_journal_resolves_via_config_not_file(cfg, tmp_path):
    """Compaction rewrites the configured journal; nothing under the package
    tree (the boundary litmus, applied to the rewrite path)."""
    lane_lease.acquire(cfg, lane="", kind="concurrent", tree=["a/**"],
                       owner="w1", loop_ts="2026-06-02T12:00Z")
    lane_lease.compact_journal(cfg)
    assert (tmp_path / "lane-journal.jsonl").exists()
    pkg_dir = os.path.dirname(lane_lease.__file__)
    assert not os.path.exists(os.path.join(pkg_dir, "lane-journal.jsonl"))


# ---------------------------------------------------------------------------
# spawn — record an INTENT to take a lane (the dos-top SPAWN→ACQUIRE visibility
# gap). It journals one OP_SPAWN under the mutex and grants NO lease.
# ---------------------------------------------------------------------------


def test_spawn_appends_one_spawn_and_grants_no_lease(cfg):
    """`spawn` writes exactly one OP_SPAWN and produces NO live lease — an intent is
    not a hold, so the arbiter never admits against it (no phantom-hold risk)."""
    res = lane_lease.spawn(cfg, lane="src", owner="launcher-1", reason="launch")
    assert res.recorded is True and res.lane == "src"
    assert _ops(cfg) == {"SPAWN": 1}
    # The SPAWN must not reconstruct as a live lease.
    assert lane_lease.live_leases(cfg) == []


def test_spawn_then_acquire_yields_exactly_one_lease(cfg):
    """SPAWN then ACQUIRE on the same lane: the journal carries both ops but the
    live set has exactly one lease (the ACQUIRE's) — the SPAWN added nothing."""
    lane_lease.spawn(cfg, lane="src", owner="launcher-1")
    lane_lease.acquire(cfg, lane="src", kind="cluster",
                       tree=cfg.lanes.tree_for("src"), owner="launcher-1")
    ops = _ops(cfg)
    assert ops["SPAWN"] == 1 and ops["ACQUIRE"] == 1
    live = lane_lease.live_leases(cfg)
    assert len(live) == 1 and live[0]["lane"] == "src"


def test_spawn_defaults_holder_to_host_pid(cfg):
    """With no --owner the holder defaults to host:pid (so the SPAWNING chip still
    has a holder to show)."""
    res = lane_lease.spawn(cfg, lane="src")
    assert ":" in res.holder  # host:pid shape
    [entry] = read_all(_journal(cfg))
    assert entry["op"] == "SPAWN" and entry["holder"] == res.holder


def _iso_to_ms(s: str) -> int:
    return int(dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc).timestamp() * 1000)


def _ms_to_iso(ms: int) -> str:
    """The exact second-resolution stamp lane_journal.journal_now_iso writes."""
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
