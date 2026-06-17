"""stop_failure_sensor — the disk side of the StopFailure asyncRewake breaker.

The pure `breaker` kernel already ships and is green (`test_breaker.py`,
`test_prop_breaker.py` exercise the count arithmetic). This exercises the BOUNDARY
adapter that persists `BreakerCounts` across the many short-lived hook processes of one
session — the same shape as `marker_sensor`'s accumulator section.

Regression (the import guard below): `src/dos/stop_failure_sensor.py` was imported by
`cli.cmd_hook_stop_failure` (the wired `dos hook stop-failure` StopFailure hook) but was
UNTRACKED on master — a clean checkout's hook crashed with `ImportError`, and NO test
covered the path, so CI stayed green. `test_import_chain_is_shippable` would have caught
it at suite time instead of silently in a host's Stop lifecycle.
"""

from __future__ import annotations

from pathlib import Path

from dos import breaker as _breaker
from dos import config as _config
from dos import stop_failure_sensor as sfs


def test_import_chain_is_shippable():
    """The regression guard: the module `cli.cmd_hook_stop_failure` imports must exist
    and be importable, and the hook command itself must be a callable on the CLI surface.
    A clean checkout missing `stop_failure_sensor.py` fails THIS test, loudly at suite
    time, instead of at hook-fire time inside a host's Stop lifecycle."""
    from dos.cli import cmd_hook_stop_failure

    assert callable(cmd_hook_stop_failure)
    assert callable(sfs.load_counts) and callable(sfs.save_counts)


def test_load_absent_file_is_zeros(tmp_path: Path, monkeypatch):
    """No persisted file yet → a fresh zeroed `BreakerCounts` (a session's first fire)."""
    monkeypatch.setenv("DISPATCH_WORKSPACE", str(tmp_path))
    cfg = _config.active()
    counts = sfs.load_counts("sess-absent", cfg)
    assert (counts.consecutive, counts.total) == (0, 0)


def test_save_then_load_round_trips(tmp_path: Path, monkeypatch):
    """The whole point of the boundary: counts survive across hook processes. The file
    lands under the workspace's own `.dos/stop-failures/`, never the real repo's."""
    monkeypatch.setenv("DISPATCH_WORKSPACE", str(tmp_path))
    cfg = _config.active()
    sfs.save_counts("sess-1", _breaker.BreakerCounts(consecutive=3, total=7), cfg)
    back = sfs.load_counts("sess-1", cfg)
    assert (back.consecutive, back.total) == (3, 7)
    assert (sfs.stop_failures_dir(cfg) / "sess-1.json").exists()


def test_corrupt_file_loads_as_zeros(tmp_path: Path, monkeypatch):
    """A torn / unreadable counts file degrades to zeros — the StopFailure hook must
    never crash on its OWN persisted state (fail-safe, mirroring the sensor siblings)."""
    monkeypatch.setenv("DISPATCH_WORKSPACE", str(tmp_path))
    cfg = _config.active()
    d = sfs.stop_failures_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    (d / "sess-corrupt.json").write_text("{not valid json", encoding="utf-8")
    counts = sfs.load_counts("sess-corrupt", cfg)
    assert (counts.consecutive, counts.total) == (0, 0)


def test_hostile_session_id_stays_under_dir(tmp_path: Path, monkeypatch):
    """A path-traversal session id sanitizes to safe chars — save/load can never write
    outside the stop-failures dir (the distrusted-host-token discipline)."""
    monkeypatch.setenv("DISPATCH_WORKSPACE", str(tmp_path))
    cfg = _config.active()
    sfs.save_counts("../../etc/passwd", _breaker.BreakerCounts(consecutive=1, total=2), cfg)
    back = sfs.load_counts("../../etc/passwd", cfg)
    assert (back.consecutive, back.total) == (1, 2)
    d = sfs.stop_failures_dir(cfg)
    files = list(d.glob("*.json"))
    assert files and all(d in f.parents for f in files)
