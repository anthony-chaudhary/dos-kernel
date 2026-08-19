"""The `[retention]` seam (docs/106 §3.3/§3.4) — scratch caps as per-workspace DATA.

DOS has the garbage-collection *problem* (an unbounded WAL + per-project `.dos/`
scratch nobody auto-reaps); docs/106 argues the collector already exists
(`replay`+`compact`) and is missing a *trigger*, a *generational split*, and a
*safe-point*. This module pins the policy surface that the trigger reads — the
`RetentionPolicy` seam-data + the two pure functions (`should_compact` threshold,
`plan_reap` keep-last-N) + the `dos.toml [retention]` loader + the `home.reap_scratch`
I/O reaper + the `dos reap` CLI verb.

The whole point (docs/106 §5): these are size/recency *tuning* knobs; the
load-bearing floor — **never reap a live lease** — is the collector's, enforced
independently of these numbers. So the tests assert the safe direction structurally:
`should_compact` is monotone in size and a smaller cap can only ask to collect MORE
of the already-collectable; `plan_reap` always keeps the newest N; the reaper's
dry-run deletes nothing.

Litmus for the layering: `retention.py` is a pure stdlib leaf (layer 2b seam-data,
like `reasons.py`/`stamp.py`) — the DECISION (`plan_reap`/`should_compact`) is here,
the I/O (`reap_scratch`'s scandir/unlink) is in the home tier. A test importing
`dos.retention` pulls in no driver and no host name.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

from dos import config as _config
from dos import retention as _retention
from dos.retention import (
    GENERIC_RETENTION,
    UNBOUNDED_RETENTION,
    RetentionPolicy,
    plan_reap,
    policy_from_table,
    should_compact,
)

_DAY_MS = 86_400_000


def _write_toml(repo: Path, body: str) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "dos.toml").write_text(body, encoding="utf-8")


def _cli(repo: Path, *argv: str) -> subprocess.CompletedProcess:
    # `--workspace` is a GLOBAL flag (before the subcommand).
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", "--workspace", str(repo), *argv],
        capture_output=True, text=True,
    )


# ===========================================================================
# The pure threshold — should_compact (docs/106 §3.2)
# ===========================================================================


def test_should_compact_empty_journal_is_false():
    assert should_compact([], GENERIC_RETENTION, now_ms=0) is False


def test_should_compact_fires_over_entry_cap():
    pol = RetentionPolicy(journal_max_entries=10, journal_max_age_days=None)
    under = [{"op": "ACQUIRE", "ts": 1}] * 10  # == cap, not over
    over = [{"op": "ACQUIRE", "ts": 1}] * 11
    assert should_compact(under, pol, now_ms=10) is False
    assert should_compact(over, pol, now_ms=10) is True


def test_should_compact_fires_over_age_cap():
    pol = RetentionPolicy(journal_max_entries=None, journal_max_age_days=30.0)
    now = 100 * _DAY_MS
    fresh = [{"op": "ACQUIRE", "ts": now - 10 * _DAY_MS}]
    stale = [{"op": "ACQUIRE", "ts": now - 40 * _DAY_MS}]
    assert should_compact(fresh, pol, now_ms=now) is False
    assert should_compact(stale, pol, now_ms=now) is True


def test_should_compact_ignores_checkpoint_age():
    """A CHECKPOINT line is a prior snapshot, NOT original history — its age must
    not re-trigger compaction (the compaction-loop hazard, docs/106 helper note)."""
    pol = RetentionPolicy(journal_max_entries=None, journal_max_age_days=30.0)
    now = 100 * _DAY_MS
    # The only old line is a checkpoint; the real entry is fresh → no compaction.
    entries = [
        {"op": "CHECKPOINT", "ts": now - 99 * _DAY_MS},
        {"op": "ACQUIRE", "ts": now - 1 * _DAY_MS},
    ]
    assert should_compact(entries, pol, now_ms=now) is False


def test_should_compact_unbounded_never_fires():
    big = [{"op": "ACQUIRE", "ts": 1}] * 100_000
    assert should_compact(big, UNBOUNDED_RETENTION, now_ms=10**15) is False


def test_should_compact_is_monotone_in_size():
    """The safe direction: a bigger journal can only ever ask to collect MORE."""
    pol = RetentionPolicy(journal_max_entries=50, journal_max_age_days=None)
    seq = [{"op": "ACQUIRE", "ts": 1}]
    fired = [should_compact(seq * n, pol, now_ms=1) for n in (1, 49, 50, 51, 200)]
    # once True, stays True as size grows (monotone non-decreasing)
    assert fired == [False, False, False, True, True]


def test_should_compact_skips_unparseable_ts():
    """A line with no integer ts is skipped, not crashed (forgiving like journal_delta)."""
    pol = RetentionPolicy(journal_max_entries=None, journal_max_age_days=1.0)
    now = 10 * _DAY_MS
    entries = [{"op": "ACQUIRE", "ts": "not-an-int"}, {"op": "HEARTBEAT"}]
    # no usable ts anywhere → age rung can't fire → False
    assert should_compact(entries, pol, now_ms=now) is False


# ===========================================================================
# The pure reaper plan — plan_reap (docs/106 §3.4 recency half)
# ===========================================================================


def test_plan_reap_keeps_newest_n():
    entries = [(f"f{i}", float(i)) for i in range(10)]  # mtime == i, f9 newest
    drop = plan_reap(entries, keep_last=3)
    assert set(drop) == {f"f{i}" for i in range(7)}  # f0..f6 dropped, f7,f8,f9 kept
    # the kept are the 3 newest
    assert "f9" not in drop and "f8" not in drop and "f7" not in drop


def test_plan_reap_none_cap_drops_nothing():
    entries = [(f"f{i}", float(i)) for i in range(10)]
    assert plan_reap(entries, keep_last=None) == []


def test_plan_reap_zero_cap_drops_everything():
    entries = [(f"f{i}", float(i)) for i in range(5)]
    assert set(plan_reap(entries, keep_last=0)) == {f"f{i}" for i in range(5)}


def test_plan_reap_fewer_than_cap_drops_nothing():
    entries = [("a", 1.0), ("b", 2.0)]
    assert plan_reap(entries, keep_last=5) == []


def test_plan_reap_ties_broken_deterministically():
    """Equal mtimes → a total order by identifier, so the plan is reproducible."""
    entries = [("b", 1.0), ("a", 1.0), ("c", 1.0)]
    # keep 1: newest by (mtime, name desc) → "c" kept; a,b dropped
    drop = plan_reap(entries, keep_last=1)
    assert set(drop) == {"a", "b"}


# ===========================================================================
# The toml loader — policy_from_table / load_from_toml
# ===========================================================================


def test_policy_from_table_overrides_named_keys_only():
    base = GENERIC_RETENTION
    pol = policy_from_table({"audits_keep_last": 7}, base=base)
    assert pol.audits_keep_last == 7
    # untouched fields inherit base
    assert pol.journal_max_entries == base.journal_max_entries
    assert pol.verdicts_keep_last == base.verdicts_keep_last


def test_policy_from_table_none_sentinels():
    """TOML has no null; -1 and "none" both mean unbounded."""
    a = policy_from_table({"runs_keep_last": -1}, base=GENERIC_RETENTION)
    b = policy_from_table({"runs_keep_last": "none"}, base=GENERIC_RETENTION)
    c = policy_from_table({"runs_keep_last": "NONE"}, base=GENERIC_RETENTION)
    assert a.runs_keep_last is None
    assert b.runs_keep_last is None
    assert c.runs_keep_last is None


def test_policy_from_table_unknown_key_raises():
    try:
        policy_from_table({"runs_keep_lsat": 3}, base=GENERIC_RETENTION)
    except ValueError as e:
        assert "unknown [retention] key" in str(e)
        assert "runs_keep_lsat" in str(e)
    else:
        raise AssertionError("expected ValueError on typo'd key")


def test_policy_from_table_negative_raises():
    try:
        policy_from_table({"audits_keep_last": -5}, base=GENERIC_RETENTION)
    except ValueError as e:
        assert "audits_keep_last" in str(e)
    else:
        raise AssertionError("expected ValueError on negative cap")


def test_policy_from_table_bool_rejected_as_cap():
    """A bool is an int subclass; it must not silently become a cap of 0/1."""
    try:
        policy_from_table({"audits_keep_last": True}, base=GENERIC_RETENTION)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError on bool cap")


def test_load_from_toml_absent_returns_base(tmp_path: Path):
    assert _retention.load_from_toml(tmp_path / "dos.toml", base=GENERIC_RETENTION) is GENERIC_RETENTION
    _write_toml(tmp_path, "[reasons.FOO]\ncategory='OPERATOR_GATE'\n")
    assert _retention.load_from_toml(tmp_path / "dos.toml", base=GENERIC_RETENTION) is GENERIC_RETENTION


def test_load_from_toml_present_overrides(tmp_path: Path):
    _write_toml(tmp_path, "[retention]\naudits_keep_last = 12\njournal_max_entries = 999\n")
    pol = _retention.load_from_toml(tmp_path / "dos.toml", base=GENERIC_RETENTION)
    assert pol.audits_keep_last == 12
    assert pol.journal_max_entries == 999
    assert pol.verdicts_keep_last == GENERIC_RETENTION.verdicts_keep_last  # inherited


# ===========================================================================
# Config wiring — the seam reaches SubstrateConfig
# ===========================================================================


def test_config_default_is_generic_retention(tmp_path: Path):
    cfg = _config.load_workspace_config(tmp_path)
    assert cfg.retention == GENERIC_RETENTION


def test_config_reads_retention_table(tmp_path: Path):
    _write_toml(tmp_path, "[retention]\naudits_keep_last = 3\n")
    cfg = _config.load_workspace_config(tmp_path)
    assert cfg.retention.audits_keep_last == 3


def test_config_malformed_retention_warns_keeps_base(tmp_path: Path):
    """A malformed [retention] warns and keeps the base — never crashes config load
    (the shared warn-and-fall-back posture)."""
    _write_toml(tmp_path, "[retention]\naudits_keep_last = -9\n")
    warnings = []
    cfg = _config.load_workspace_config(
        tmp_path, warn=lambda label, msg: warnings.append((label, msg)))
    assert cfg.retention == GENERIC_RETENTION  # base kept
    assert any(label == "retention" for label, _ in warnings)


# ===========================================================================
# The I/O reaper — home.reap_scratch (dry-run is safe; --apply deletes)
# ===========================================================================


def _seed_audits(root: Path, n: int) -> Path:
    """Create n trajectory-audit-*.md files with strictly increasing mtimes."""
    import os
    d = root / ".dos" / "audits"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        f = d / f"trajectory-audit-{i:03d}.md"  # 000..NNN, lexically == mtime order
        f.write_text(f"report {i}\n", encoding="utf-8")
        # force a deterministic mtime ordering (i seconds apart)
        os.utime(f, (1_780_000_000 + i, 1_780_000_000 + i))
    return d


def test_reap_scratch_dry_run_deletes_nothing(tmp_path: Path):
    d = _seed_audits(tmp_path, 10)
    cfg = dataclasses.replace(
        _config.default_config(tmp_path),
        retention=GENERIC_RETENTION.with_overrides(audits_keep_last=3))
    rep = _retention_reap(cfg, apply=False)
    assert rep["_applied"] is False
    assert rep["audits"]["kept"] == 3
    assert len(rep["audits"]["dropped"]) == 7
    # NOTHING actually deleted
    assert len(list(d.glob("trajectory-audit-*.md"))) == 10


def test_reap_scratch_apply_keeps_newest(tmp_path: Path):
    d = _seed_audits(tmp_path, 10)
    cfg = dataclasses.replace(
        _config.default_config(tmp_path),
        retention=GENERIC_RETENTION.with_overrides(audits_keep_last=3))
    rep = _retention_reap(cfg, apply=True)
    assert rep["_applied"] is True
    survivors = sorted(p.name for p in d.glob("trajectory-audit-*.md"))
    assert len(survivors) == 3
    # the 3 newest by mtime were 007,008,009
    assert survivors == [
        "trajectory-audit-007.md",
        "trajectory-audit-008.md",
        "trajectory-audit-009.md",
    ]


def test_reap_scratch_unbounded_cap_keeps_all(tmp_path: Path):
    d = _seed_audits(tmp_path, 5)
    cfg = dataclasses.replace(
        _config.default_config(tmp_path),
        retention=GENERIC_RETENTION.with_overrides(audits_keep_last=None))
    rep = _retention_reap(cfg, apply=True)
    assert rep["audits"].get("unbounded") is True
    assert len(list(d.glob("trajectory-audit-*.md"))) == 5  # untouched


def test_reap_scratch_runs_flagged_liveness_unwired(tmp_path: Path):
    """The run-dir class announces that its liveness gate isn't wired (§3.4)."""
    cfg = _config.default_config(tmp_path)
    rep = _retention_reap(cfg, apply=False)
    assert rep["runs"].get("liveness_unwired") is True


def test_reap_scratch_annotates_data_class(tmp_path: Path):
    """Each class report carries a `data_class` tag (the trajectory-vs-product
    annotation) — the generic policy classifies `.dos/audits/` as AUDIT and the
    run-dir scratch as TRAJECTORY. Annotation only: it must not change WHAT is
    reaped (kept/dropped are still driven by the retention caps)."""
    _seed_audits(tmp_path, 5)
    cfg = dataclasses.replace(
        _config.default_config(tmp_path),
        retention=GENERIC_RETENTION.with_overrides(audits_keep_last=2))
    rep = _retention_reap(cfg, apply=False)
    assert rep["audits"]["data_class"] == "AUDIT"
    assert rep["runs"]["data_class"] == "TRAJECTORY"
    # reap behavior unchanged by the annotation
    assert rep["audits"]["kept"] == 2
    assert len(rep["audits"]["dropped"]) == 3


def test_cli_reap_human_output_shows_data_class(tmp_path: Path):
    _seed_audits(tmp_path, 3)
    _write_toml(tmp_path, "[retention]\naudits_keep_last = 1\n")
    r = _cli(tmp_path, "reap")
    assert r.returncode == 0, r.stderr
    assert "[AUDIT]" in r.stdout       # audits class tagged
    assert "[TRAJECTORY]" in r.stdout  # runs class tagged


def _retention_reap(cfg, *, apply):
    from dos import home
    return home.reap_scratch(cfg, apply=apply)


# ===========================================================================
# The CLI verb — dos reap (dry-run default, --apply, --json)
# ===========================================================================


def test_cli_reap_dry_run_default(tmp_path: Path):
    _seed_audits(tmp_path, 8)
    _write_toml(tmp_path, "[retention]\naudits_keep_last = 2\n")
    r = _cli(tmp_path, "reap", "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["_applied"] is False
    assert out["audits"]["kept"] == 2
    assert len(out["audits"]["dropped"]) == 6
    # dry-run deleted nothing
    assert len(list((tmp_path / ".dos" / "audits").glob("*.md"))) == 8


def test_cli_reap_apply_deletes(tmp_path: Path):
    _seed_audits(tmp_path, 8)
    _write_toml(tmp_path, "[retention]\naudits_keep_last = 2\n")
    r = _cli(tmp_path, "reap", "--apply", "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["_applied"] is True
    assert len(list((tmp_path / ".dos" / "audits").glob("*.md"))) == 2


def test_cli_reap_human_output_lists_drops(tmp_path: Path):
    _seed_audits(tmp_path, 5)
    _write_toml(tmp_path, "[retention]\naudits_keep_last = 1\n")
    r = _cli(tmp_path, "reap")
    assert r.returncode == 0, r.stderr
    assert "DRY-RUN" in r.stdout
    assert "would reap=4" in r.stdout
    # liveness note shows for runs
    assert "liveness-gate not yet wired" in r.stdout



def test_reap_covers_hook_written_stream_and_digest_directories(tmp_path: Path):
    cfg = dataclasses.replace(
        _config.load_workspace_config(tmp_path, gather_env=False),
        retention=GENERIC_RETENTION.with_overrides(
            streams_keep_last=2,
            session_digests_keep_last=1,
            help_digests_keep_last=1,
        ),
    )
    classes = {
        "streams": (cfg.paths.dot_dos / "streams", ".jsonl", 4),
        "session_digests": (cfg.paths.dot_dos / "session-digest", ".txt", 3),
        "help_digests": (cfg.paths.dot_dos / "help-digest", ".txt", 2),
    }
    for _name, (directory, suffix, count) in classes.items():
        directory.mkdir(parents=True)
        for i in range(count):
            path = directory / f"{i}{suffix}"
            path.write_text("x" * (i + 1), encoding="utf-8")
            os.utime(path, (100 + i, 100 + i))

    report = _retention_reap(cfg, apply=False)

    assert report["streams"]["kept"] == 2
    assert len(report["streams"]["dropped"]) == 2
    assert report["session_digests"]["kept"] == 1
    assert len(report["session_digests"]["dropped"]) == 2
    assert report["help_digests"]["kept"] == 1
    assert len(report["help_digests"]["dropped"]) == 1
    for name in classes:
        assert report[name]["domain_files"] == classes[name][2]
        assert report[name]["domain_bytes"] > 0


def test_hot_scratch_caps_load_from_retention_toml(tmp_path: Path):
    _write_toml(
        tmp_path,
        "[retention]\n"
        "streams_keep_last = 12\n"
        "session_digests_keep_last = 13\n"
        "help_digests_keep_last = 14\n",
    )
    pol = _retention.load_from_toml(tmp_path / "dos.toml")
    assert pol.streams_keep_last == 12
    assert pol.session_digests_keep_last == 13
    assert pol.help_digests_keep_last == 14



def test_metrics_log_dry_run_and_apply_preserve_complete_newest_lines(tmp_path: Path):
    cfg = dataclasses.replace(
        _config.load_workspace_config(tmp_path, gather_env=False),
        retention=GENERIC_RETENTION.with_overrides(metrics_max_bytes=18),
    )
    log = cfg.paths.dot_dos / "metrics" / "observations.jsonl"
    log.parent.mkdir(parents=True)
    original = b'{"n":1}\n{"n":2}\n{"n":3}\n'
    log.write_bytes(original)

    dry = _retention_reap(cfg, apply=False)["metrics"]
    assert dry["would_compact"] is True
    assert dry["compacted"] is False
    assert log.read_bytes() == original

    applied = _retention_reap(cfg, apply=True)["metrics"]
    assert applied["compacted"] is True
    assert applied["bytes_reclaimed"] > 0
    assert log.read_bytes().endswith(b'{"n":3}\n')
    assert not log.read_bytes().startswith(b'n":')


def test_metrics_byte_cap_loads_from_toml(tmp_path: Path):
    _write_toml(tmp_path, "[retention]\nmetrics_max_bytes = 1234\n")
    assert _retention.load_from_toml(
        tmp_path / "dos.toml"
    ).metrics_max_bytes == 1234



def test_reap_reports_zero_domain_for_absent_hot_directories(tmp_path: Path):
    cfg = _config.load_workspace_config(tmp_path, gather_env=False)

    report = _retention_reap(cfg, apply=False)

    for name in ("streams", "session_digests", "help_digests"):
        assert report[name]["domain_files"] == 0
        assert report[name]["domain_bytes"] == 0


def test_reap_apply_removes_old_hook_written_artifacts(tmp_path: Path):
    cfg = dataclasses.replace(
        _config.load_workspace_config(tmp_path, gather_env=False),
        retention=GENERIC_RETENTION.with_overrides(
            streams_keep_last=1,
            session_digests_keep_last=1,
            help_digests_keep_last=1,
        ),
    )
    directories = (
        (cfg.paths.dot_dos / "streams", ".jsonl"),
        (cfg.paths.dot_dos / "session-digest", ".txt"),
        (cfg.paths.dot_dos / "help-digest", ".txt"),
    )
    for directory, suffix in directories:
        directory.mkdir(parents=True)
        for i in range(3):
            path = directory / f"{i}{suffix}"
            path.write_text(str(i), encoding="utf-8")
            os.utime(path, (100 + i, 100 + i))

    report = _retention_reap(cfg, apply=True)

    for name, (directory, suffix) in zip(
        ("streams", "session_digests", "help_digests"), directories,
    ):
        assert len(report[name]["dropped"]) == 2
        assert sorted(path.name for path in directory.iterdir()) == [f"2{suffix}"]


def test_metrics_compaction_discards_torn_final_line_and_preserves_mode(tmp_path: Path):
    cfg = dataclasses.replace(
        _config.load_workspace_config(tmp_path, gather_env=False),
        retention=GENERIC_RETENTION.with_overrides(metrics_max_bytes=20),
    )
    log = cfg.paths.dot_dos / "metrics" / "observations.jsonl"
    log.parent.mkdir(parents=True)
    log.write_bytes(b'{"n":1}\n{"n":2}\n{"n":3}\n{"torn":')
    os.chmod(log, 0o640)
    original_mode = log.stat().st_mode

    report = _retention_reap(cfg, apply=True)["metrics"]

    assert report["apply_precondition"] == "quiet-window"
    assert log.read_bytes() == b'{"n":2}\n{"n":3}\n'
    assert log.stat().st_mode == original_mode


def test_reap_reports_recursive_run_domain_bytes(tmp_path: Path):
    cfg = _config.load_workspace_config(tmp_path, gather_env=False)
    payload = cfg.paths.fanout_runs / "run-1" / "nested" / "payload.txt"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"payload")

    report = _retention_reap(cfg, apply=False)

    assert report["runs"]["domain_files"] == 1
    assert report["runs"]["domain_bytes"] == len(b"payload")
