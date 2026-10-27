"""Compaction preserves genuine evidence without replaying old lease effects."""

import datetime as dt
import json

import pytest

from dos import config, dispatch_top, journal_delta, lane_journal, lane_lease
from dos.drivers import supervisor


NOW = 1_800_000_000_000
START = NOW - 600_000
KEY = ("owned-loop", "main")


def _iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(op, seconds, *, key=KEY, **fields):
    return {"op": op, "loop_ts": key[0], "lane": key[1],
            "ts": _iso(NOW + seconds * 1000), **fields}


def _acquire(seconds=-500, *, key=KEY):
    return _row("ACQUIRE", seconds, key=key, lease={
        "loop_ts": key[0], "lane": key[1], "holder": "owned",
        "tree": ["owned/**"], "acquired_at": _iso(START),
        "heartbeat_at": _iso(START),
    })


def _sequence(name):
    rows = [_acquire()]
    if name == "heartbeat":
        rows += [_row("HEARTBEAT", -5, heartbeat_at=_iso(START))]
    elif name == "reconcile":
        rows += [_row("RECONCILE", -4, lease=rows[0]["lease"])]
    elif name == "reacquire":
        rows += [_row("RELEASE", -400), _acquire(-100), _row("HEARTBEAT", -1)]
    elif name == "adopt":
        rows += [_row("ADOPT", -4, holder="adopted", pid=42, heartbeat_at=_iso(NOW - 4000))]
    elif name == "foreign":
        rows += [_row("RECONCILE", -2, key=("other", "main")), _row("HEARTBEAT", -1)]
    elif name == "future":
        rows += [_row("HEARTBEAT", -5), _row("HEARTBEAT", 100), _row("RECONCILE", 200)]
    elif name == "missing-ts":
        row = _row("HEARTBEAT", -3, heartbeat_at=_iso(NOW - 3000))
        row.pop("ts")
        rows += [row]
    elif name == "spawn":
        rows += [_row("SPAWN", -1, key=("launch", "other"), holder="launcher")]
    elif name == "corrupt":
        rows += [{"op": "_CORRUPT", "_raw": "original corrupt record", "_line": 1},
                 _row("HEARTBEAT", -1)]
    elif name == "unnamed":
        rows = [_acquire(key=("owned-unnamed", "")),
                _row("HEARTBEAT", -1, key=("owned-unnamed", ""))]
    elif name == "halt":
        rows += [_row("HALT", -1, reason="original proposal")]
    return [{**row, "seq": index + 1} for index, row in enumerate(rows)]


@pytest.mark.parametrize("name", [
    "birth", "heartbeat", "reconcile", "reacquire", "adopt", "foreign", "future",
    "missing-ts", "spawn", "corrupt", "unnamed", "halt",
])
def test_compaction_preserves_evidence_windows_and_tail_cursors(name):
    rows = _sequence(name)
    keys = {KEY, ("other", "main"), ("owned-unnamed", ""), ("", ""), None}
    for cursor in range(1, len(rows) + 1):
        compacted = lane_journal.compact(rows[:cursor]) + rows[cursor:]
        assert lane_journal.replay(compacted) == lane_journal.replay(rows)
        assert lane_journal.lease_generations(compacted) == lane_journal.lease_generations(rows)
        for key in keys:
            for started in (START, NOW - 4000, NOW):
                for now in (NOW, NOW + 300_000):
                    args = {"run_started_ms": started, "now_ms": now, "lease_key": key}
                    assert journal_delta.fold_since(compacted, **args) == journal_delta.fold_since(
                        rows, **args)
        normalized = lane_journal.compact(compacted)
        assert lane_journal.compact(normalized) == normalized


def test_metadata_records_remain_original_and_do_not_become_lease_mutations():
    rows = _sequence("reacquire") + [_row("ENFORCE", -1, dispatch_call=True,
                                                    withheld=False, intervention="WARN",
                                                    proposal={"decision": "warn"})]
    compacted = lane_journal.compact(rows)
    metadata = compacted[0]["evidence_records"]
    assert metadata == rows[:-1]
    assert all("evidence_records" not in row for row in metadata)
    assert lane_journal.replay(compacted) == lane_journal.replay(rows)
    assert lane_journal.lease_generations(compacted) == lane_journal.lease_generations(rows)
    assert compacted[0]["seq_watermark"] == 4


def test_bare_or_malformed_metadata_never_invents_a_beat():
    for value in (None, {}, [None, 7, {"op": ["HEARTBEAT"]}, {"op": "ENFORCE"}]):
        entries = [{"op": "CHECKPOINT", "leases": [], "ts": _iso(NOW),
                    "evidence_records": value}]
        result = journal_delta.fold_since(entries, run_started_ms=START, now_ms=NOW, lease_key=KEY)
        assert result.events_since_start == 0
        assert result.newest_heartbeat_age_ms is None


def test_progress_and_pending_spawn_counters_survive_compaction():
    rows = _sequence("reacquire") + [
        _row("SPAWN", -100, key=("launch", "other"), holder="old"),
        _row("SPAWN", -1, key=("launch", "other"), holder="current"),
    ]
    compacted = lane_journal.compact(rows)
    live = {row["lane"]: row for row in lane_journal.replay(rows)}
    now = dt.datetime.fromtimestamp(NOW / 1000, dt.timezone.utc)
    assert dispatch_top._events_by_lane(compacted, live) == dispatch_top._events_by_lane(rows, live)
    assert dispatch_top._spawning_lanes(compacted, live, now=now) == dispatch_top._spawning_lanes(
        rows, live, now=now)


def test_native_compaction_does_not_reap_or_replace_reconciled_worker(tmp_path, monkeypatch):
    cfg = config.default_config(str(tmp_path))
    wal = cfg.paths.lane_journal
    monkeypatch.setenv("DISPATCH_LANE_JOURNAL_PATH", str(wal))
    monkeypatch.setenv("DISPATCH_LANE_LEASE_LOCK_PATH", str(tmp_path / "lease.lock"))
    lease = _acquire()["lease"]
    lane_journal.append(_row("RECONCILE", -1, lease=lease), wal)
    before = supervisor.plan_tick(cfg, target=1, now_ms=NOW, launched={}, cooldown_ms=0)
    source_rows = lane_journal.read_all(wal)
    result = lane_lease.compact_journal(cfg)
    assert result.archive_path is not None
    after = supervisor.plan_tick(cfg, target=1, now_ms=NOW, launched={}, cooldown_ms=0)
    assert before.to_dict() == after.to_dict()
    spawned = []
    _, actions = supervisor.tick(cfg, target=1, now_ms=NOW, launched={}, root_run=None,
                                 cooldown_ms=0, popen=lambda *a, **kw: spawned.append(a))
    assert actions.reaped == actions.spawned == spawned == []
    assert lane_journal.replay(lane_journal.read_all(wal)) == lane_journal.replay(source_rows)


def test_sequence_watermark_survives_evidence_metadata(tmp_path):
    wal = tmp_path / "journal.jsonl"
    rows = _sequence("reacquire")
    wal.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    before = lane_journal.next_seq(wal)
    compacted = lane_journal.compact(rows)
    wal.write_text("".join(json.dumps(row) + "\n" for row in compacted), encoding="utf-8")
    assert lane_journal.next_seq(wal) == before


@pytest.mark.parametrize("change", [
    {"dispatch_call": False}, {"dispatch_call": 1}, {"withheld": True},
    {"withheld": None}, {"intervention": "DEFER"}, {"intervention": "warn"},
    {"proposal": {"decision": "deny"}}, {"proposal": {"decision": "override-admit"}},
    {"proposal": {"decision": "unknown"}}, {"proposal": []},
])
def test_only_closed_passing_warn_shape_can_be_retired(change):
    row = _row("ENFORCE", -1, dispatch_call=True, withheld=False,
               intervention="WARN", proposal={"decision": "warn"})
    assert lane_journal._retired_observation(row)
    protected = {**row, **change}
    assert not lane_journal._retired_observation(protected)
    assert list(lane_journal.evidence_entries(lane_journal.compact([protected]))) == [protected]


def test_latest_checkpoint_replaces_prior_anchors_without_duplicate_evidence():
    rows = _sequence("reacquire")
    cp = lane_journal.compact(rows)
    assert list(lane_journal.evidence_entries(cp + cp)) == rows
    assert lane_journal.compact(cp + cp) == cp


def test_released_last_generation_still_fences_future_grants():
    rows = [_acquire(), _row("RELEASE", -400)]
    cp = lane_journal.compact(rows)
    assert cp[0]["generation_watermark"] == 1
    assert "generations" not in cp[0]
    tail = [_acquire(-1)]
    assert lane_journal.lease_generations(cp + tail) == lane_journal.lease_generations(rows + tail)
    assert lane_journal.lease_generations(lane_journal.compact(cp) + tail) == {KEY: 2}


def test_native_compaction_holds_before_archive_for_oversize_or_no_benefit(tmp_path, monkeypatch):
    cfg = config.default_config(str(tmp_path))
    wal = cfg.paths.lane_journal
    monkeypatch.setenv("DISPATCH_LANE_JOURNAL_PATH", str(wal))
    monkeypatch.setenv("DISPATCH_LANE_LEASE_LOCK_PATH", str(tmp_path / "lease.lock"))
    lane_journal.append(_acquire(), wal)
    original = wal.read_bytes()
    oversize = lane_lease.compact_journal(cfg, max_checkpoint_bytes=64)
    assert oversize.reason is lane_lease.CompactReason.CHECKPOINT_TOO_LARGE
    nobenefit = lane_lease.compact_journal(cfg, reclaim_only=True)
    assert nobenefit.reason is lane_lease.CompactReason.NO_BENEFIT
    assert wal.read_bytes() == original
    assert list(wal.parent.glob(wal.name + ".archive.*")) == []
    first = lane_lease.compact_journal(cfg)
    assert first.reason is lane_lease.CompactReason.COMPACTED
    repeat = lane_lease.compact_journal(cfg)
    assert repeat.reason is lane_lease.CompactReason.UNCHANGED
    assert repeat.archive_path is None
    assert len(list(wal.parent.glob(wal.name + ".archive.*"))) == 1


def test_forensic_and_operational_history_stays_ordered():
    from dos import child_refusals, enforce_outcomes
    rows = _sequence("reacquire") + [
        _row("REFUSE", -3, root_id="root", run_id="child", reason_class="LANE_BUSY"),
        _row("ATTEMPT", -2, unit_id="owned-unit", outcome="drained"),
        _row("ENFORCE", -1, holder="owned", intervention="BLOCK",
             root_id="root", run_id="child", proposal={"decision": "deny"}),
        _row("ENFORCE", 0, holder="owned", proposal={"decision": "override-admit"}),
        _row("FUTURE_OP", 0, opaque="preserve"),
    ]
    cp = lane_journal.compact(rows)
    assert list(lane_journal.evidence_entries(cp)) == rows
    assert child_refusals.fold_child_refusals(cp, root_id="root") == child_refusals.fold_child_refusals(rows, root_id="root")
    assert enforce_outcomes.fold_enforce_outcomes(cp) == enforce_outcomes.fold_enforce_outcomes(rows)


@pytest.mark.parametrize("stop", [False, True])
def test_short_write_cannot_install_torn_checkpoint(tmp_path, monkeypatch, stop):
    import os
    cfg = config.default_config(str(tmp_path))
    wal = cfg.paths.lane_journal
    monkeypatch.setenv("DISPATCH_LANE_JOURNAL_PATH", str(wal))
    monkeypatch.setenv("DISPATCH_LANE_LEASE_LOCK_PATH", str(tmp_path / "lease.lock"))
    lane_journal.append(_acquire(), wal)
    original = wal.read_bytes()
    real_write = os.write
    calls = []
    def short_write(fd, value):
        calls.append(len(value))
        if stop and len(calls) > 1:
            return 0
        return real_write(fd, value[:17])
    monkeypatch.setattr(os, "write", short_write)
    if stop:
        with pytest.raises(OSError):
            lane_lease.compact_journal(cfg)
        assert wal.read_bytes() == original
    else:
        result = lane_lease.compact_journal(cfg)
        assert result.reason is lane_lease.CompactReason.COMPACTED
        assert len(calls) > 1
        assert lane_journal.replay(lane_journal.read_all(wal)) == lane_journal.replay(
            [json.loads(original)])
        assert b"\r\n" not in wal.read_bytes()


def test_corruption_signal_survives_a_later_checkpoint_reset():
    corrupt = {"op": "_CORRUPT", "_raw": "original", "_line": 1}
    cp = lane_journal.compact(_sequence("heartbeat"))
    rows = cp + [corrupt] + cp
    before = journal_delta.fold_since(rows, run_started_ms=START, now_ms=NOW, lease_key=KEY)
    after = journal_delta.fold_since(lane_journal.compact(rows), run_started_ms=START,
                                     now_ms=NOW, lease_key=KEY)
    assert before == after
    assert before.saw_corrupt


def test_actual_cooldown_breaker_refusal_and_watchdog_consumers(tmp_path, monkeypatch, capsys):
    from dos import cli, decisions, run_id
    from dos.drivers import watchdog
    cfg = config.default_config(str(tmp_path))
    wal = cfg.paths.lane_journal
    monkeypatch.setenv("DISPATCH_LANE_JOURNAL_PATH", str(wal))
    monkeypatch.setenv("DISPATCH_LANE_LEASE_LOCK_PATH", str(tmp_path / "lease.lock"))
    monkeypatch.setattr(cli, "_apply_workspace", lambda args: None)
    monkeypatch.setattr(cli._config, "active", lambda: cfg)
    rid = str(run_id.mint("owned", clock_ms=lambda: START))
    rows = _sequence("reacquire") + [
        _row("REFUSE", -5, reason="busy", conflicts=[{"lane": "main"}]),
        _row("RELEASE", -4), _acquire(-3),
        _row("ATTEMPT", -2, unit_id="owned-unit", outcome="drained"),
    ]
    rows += [_row("ENFORCE", -1, holder="owned", intervention="BLOCK",
                  proposal={"decision": "deny"}) for _ in range(4)]
    rows += [_row("ENFORCE", 0, holder="owned", proposal={"decision": "override-admit"})]
    rows = [{**row, "seq": i + 1} for i, row in enumerate(rows)]
    wal.parent.mkdir(parents=True, exist_ok=True)
    def write(records):
        wal.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    def observe():
        code = cli.main(["cooldown", "owned-unit", "--now-ms", str(NOW), "--json"])
        cooldown = (code, json.loads(capsys.readouterr().out))
        clock = dt.datetime.fromtimestamp(NOW / 1000, dt.timezone.utc)
        pending = [row.to_dict() for row in decisions._from_lane_journal(cfg)]
        breaker = [row.to_dict() for row in decisions._from_enforce_storms(
            cfg, now=clock, live_holders={"owned"})]
        tracked = watchdog.TrackedRun(run_id=rid, lane=KEY[1], loop_ts=KEY[0])
        halted = []
        verdicts, actions = watchdog.tick(cfg, [tracked], now_ms=NOW, proposed={},
                                           halt=lambda *a, **kw: halted.append(a))
        return cooldown, pending, breaker, verdicts, actions, halted
    write(rows)
    before = observe()
    write(lane_journal.compact(rows))
    assert observe() == before
    assert before[0][1]["state"] == "RECENTLY_ATTEMPTED"
    assert before[1] == []  # A genuine later release supersedes the refusal.
    assert before[2] == []  # The genuine override still resets the deny breaker.
    assert before[5] == []
