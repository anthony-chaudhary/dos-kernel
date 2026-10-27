"""Append/compact serialization, including writers without the lease mutex."""

from concurrent.futures import ThreadPoolExecutor
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import stat
from threading import Event

import pytest

from dos import _filelock, cli, config, lane_journal, lane_lease
from dos._journal_mutex import locked


@pytest.mark.parametrize("exists", [False, True])
def test_missing_or_empty_journal_compaction_is_noop(tmp_path, monkeypatch, exists):
    journal = tmp_path / "lane-journal.jsonl"
    monkeypatch.setenv("DISPATCH_LANE_JOURNAL_PATH", str(journal))
    monkeypatch.setenv("DISPATCH_LANE_LEASE_LOCK_PATH", str(tmp_path / "lease.lock"))
    if exists:
        journal.touch()
    result = lane_lease.compact_journal(config.default_config(str(tmp_path)))
    assert result.entries_before == result.entries_after == result.bytes_reclaimed == 0
    assert result.archive_path is None
    assert journal.exists() == exists
    if exists:
        assert journal.read_bytes() == b""
    assert not list(tmp_path.glob("lane-journal.jsonl.archive.*"))


@pytest.mark.parametrize("op", ["ENFORCE", "SCAVENGE"])
def test_compaction_retains_append_started_during_fold(tmp_path, monkeypatch, op):
    journal = tmp_path / "lane-journal.jsonl"
    monkeypatch.setenv("DISPATCH_LANE_JOURNAL_PATH", str(journal))
    monkeypatch.setenv("DISPATCH_LANE_LEASE_LOCK_PATH", str(tmp_path / "lease.lock"))
    cfg = config.default_config(str(tmp_path))
    for lane in ("stale", ""):
        lane_journal.append({"op": "ACQUIRE", "lane": lane, "loop_ts": "old",
                             "tree": [lane + "/**"], "holder": "keep",
                             "acquired_at": "2020-01-01T00:00:00Z"}, journal)
    lane_journal.append({"op": "_CORRUPT", "_raw": "retained corruption"}, journal)
    before = lane_journal.read_all(journal)
    original_bytes = journal.read_bytes()
    before_live = lane_journal.replay(before)
    before_generations = lane_journal.lease_generations(before)
    seq = lane_journal.next_seq(journal)
    folding, resume, writer_started = Event(), Event(), Event()
    original = lane_journal.compact

    def paused_fold(entries):
        folding.set()
        assert resume.wait(5)
        return original(entries)

    def append():
        writer_started.set()
        # The supervisor and native hook do not hold the lease mutex.
        return lane_journal.append({"op": op, "lane": "absent", "loop_ts": "old"},
                                   journal)

    monkeypatch.setattr(lane_journal, "compact", paused_fold)
    with ThreadPoolExecutor(max_workers=2) as pool:
        compacting = pool.submit(lane_lease.compact_journal, cfg)
        try:
            assert folding.wait(5)
            writing = pool.submit(append)
            assert writer_started.wait(5)
            assert not writing.done()
        finally:
            resume.set()
        compacted = compacting.result(timeout=5)
        entry = writing.result(timeout=5)
    assert compacted.archive_path is not None
    assert Path(compacted.archive_path).read_bytes() == original_bytes
    after = lane_journal.read_all(journal)
    assert after[-1] == entry
    assert entry["seq"] >= seq
    assert lane_journal.replay(after) == before_live
    assert lane_journal.lease_generations(after) == before_generations
    assert any(e.get("op") == "_CORRUPT" for e in after)


def test_failed_replace_releases_append_mutex(tmp_path, monkeypatch):
    journal = tmp_path / "lane-journal.jsonl"
    monkeypatch.setenv("DISPATCH_LANE_JOURNAL_PATH", str(journal))
    monkeypatch.setenv("DISPATCH_LANE_LEASE_LOCK_PATH", str(tmp_path / "lease.lock"))
    lane_journal.append({"op": "ENFORCE"}, journal)
    original = journal.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("injected replacement failure")

    monkeypatch.setattr(_filelock, "atomic_replace", fail)
    with pytest.raises(OSError):
        lane_lease.compact_journal(config.default_config(str(tmp_path)))
    assert journal.read_bytes() == original
    with locked(journal, timeout=0):
        pass
    lane_journal.append({"op": "ENFORCE"}, journal)
    assert len(lane_journal.read_all(journal)) == 2


def test_failed_archive_preserves_wal_and_releases_mutex(tmp_path, monkeypatch):
    import shutil

    journal = tmp_path / "lane-journal.jsonl"
    monkeypatch.setenv("DISPATCH_LANE_JOURNAL_PATH", str(journal))
    monkeypatch.setenv("DISPATCH_LANE_LEASE_LOCK_PATH", str(tmp_path / "lease.lock"))
    lane_journal.append({"op": "ENFORCE"}, journal)
    original = journal.read_bytes()

    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "injected archive capacity failure")

    monkeypatch.setattr(shutil, "copyfileobj", full)
    with pytest.raises(OSError) as error:
        lane_lease.compact_journal(config.default_config(str(tmp_path)))
    assert error.value.errno == errno.ENOSPC
    assert journal.read_bytes() == original
    with locked(journal, timeout=0):
        pass


def test_archive_preserves_journal_mode(tmp_path, monkeypatch):
    journal = tmp_path / "lane-journal.jsonl"
    monkeypatch.setenv("DISPATCH_LANE_JOURNAL_PATH", str(journal))
    monkeypatch.setenv("DISPATCH_LANE_LEASE_LOCK_PATH", str(tmp_path / "lease.lock"))
    lane_journal.append({"op": "ENFORCE"}, journal)
    journal.chmod(0o600)
    result = lane_lease.compact_journal(config.default_config(str(tmp_path)))
    assert result.archive_path is not None
    assert stat.S_IMODE(Path(result.archive_path).stat().st_mode) == stat.S_IMODE(
        journal.stat().st_mode)


def test_append_mutex_contends_and_releases_after_exception(tmp_path):
    journal = tmp_path / "lane-journal.jsonl"
    with locked(journal):
        with pytest.raises(TimeoutError):
            with locked(journal, timeout=0):
                pytest.fail("overlapping lock admitted")
    with pytest.raises(ValueError):
        with locked(journal):
            raise ValueError("injected holder failure")
    with locked(journal, timeout=0):
        pass


def test_process_death_releases_append_mutex(tmp_path):
    journal = tmp_path / "lane-journal.jsonl"
    code = """import sys
from dos._journal_mutex import locked
from pathlib import Path
with locked(Path(sys.argv[1])):
    print('LOCKED', flush=True)
    sys.stdin.read()
"""
    with subprocess.Popen(
        [sys.executable, "-c", code, str(journal)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ) as worker:
        try:
            assert worker.stdout.readline().strip() == "LOCKED"
            with pytest.raises(TimeoutError):
                with locked(journal, timeout=0):
                    pytest.fail("child's live lock admitted another writer")
        finally:
            worker.kill()
            worker.communicate(timeout=5)
    with locked(journal, timeout=0):
        pass


@pytest.mark.parametrize("limit", [3, 1000])
def test_threshold_cli_noop_never_replays_or_materializes_home(
    tmp_path, monkeypatch, capsys, limit,
):
    journal = tmp_path / "lane-journal.jsonl"
    journal.write_bytes(b"{}\n")
    cfg = config.default_config(str(tmp_path))
    monkeypatch.setenv("DISPATCH_LANE_JOURNAL_PATH", str(journal))
    monkeypatch.setattr(cli, "_apply_workspace", lambda args: None)
    monkeypatch.setattr(cli._config, "active", lambda: cfg)

    def forbidden(*args, **kwargs):
        pytest.fail("below-threshold maintenance replayed or initialized a store")

    monkeypatch.setattr(lane_journal, "read_all", forbidden)
    monkeypatch.setattr(cli, "_ensure_home_if_persisting", forbidden)
    rc = cli.main(["journal", "compact", "--max-bytes", str(limit)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {
        "compacted": False, "reason": "BELOW_SIZE_THRESHOLD", "bytes": 3, "max_bytes": limit,
    }
    assert journal.read_bytes() == b"{}\n"
    assert not list(tmp_path.glob("*.append.lock"))


def test_threshold_cli_compacts_only_journal_and_reports_archive(tmp_path, monkeypatch, capsys):
    journal = tmp_path / "lane-journal.jsonl"
    monkeypatch.setenv("DISPATCH_LANE_JOURNAL_PATH", str(journal))
    monkeypatch.setenv("DISPATCH_LANE_LEASE_LOCK_PATH", str(tmp_path / "lease.lock"))
    cfg = config.default_config(str(tmp_path))
    monkeypatch.setattr(cli, "_apply_workspace", lambda args: None)
    monkeypatch.setattr(cli._config, "active", lambda: cfg)
    monkeypatch.setattr(cli, "_ensure_home_if_persisting", lambda: None)
    metrics = tmp_path / "metrics.jsonl"
    metrics.write_bytes(b"retained metrics\n")
    lane_journal.append({"op": "ACQUIRE", "lane": "keep", "loop_ts": "old"}, journal)
    original = journal.read_bytes()
    assert cli.main(["journal", "compact", "--max-bytes", "0"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["entries_before"] == receipt["entries_after"] == 1
    assert Path(receipt["archive_path"]).read_bytes() == original
    assert lane_journal.read_all(journal)[0]["op"] == "CHECKPOINT"
    assert metrics.read_bytes() == b"retained metrics\n"


def test_threshold_cli_rejects_negative_limit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_apply_workspace", lambda args: None)
    assert cli.main(["journal", "compact", "--max-bytes", "-1"]) == 2
    assert not list(tmp_path.iterdir())
