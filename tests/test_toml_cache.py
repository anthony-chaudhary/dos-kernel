"""The dos.toml / answer-index read-once cache — parse-once + mtime invalidation.

Every dos MCP tool call rebuilds the workspace config, and the build folds ~two
dozen `dos.toml` tables. Before `dos._tomlcache`, each of those layer readbacks
(`config._load_toml_table`, `load_class_budgets_from_toml`, and the per-module
`reasons`/`stamp`/`enumerate`/`cooldown`/… `load_from_toml` seams) opened and
`tomllib.loads`-ed the SAME file independently — ~25 reads+parses of one small
file per build, and the MCP server rebuilds on EVERY tool call. That drove a
page-fault / allocation storm on repeated calls.

These tests pin the fix from both ends:

  * `test_workspace_config_parses_toml_once` — a single `load_workspace_config`
    parses `dos.toml` ONCE (not ~25×), and repeated builds of the UNCHANGED file
    add ZERO further parses (the cache serves them).
  * `test_workspace_config_reparses_after_mtime_change` — editing `dos.toml`
    (a live edit bumps its mtime) forces exactly one fresh parse on the next
    build — invalidation is correct, the cache never serves a stale config.
  * `test_missing_toml_is_not_a_parse` — a workspace with no `dos.toml` triggers
    zero parses and still degrades to the generic default (missing-file semantics
    unchanged).
  * `test_load_rows_reads_index_once` / `_reparses_after_mtime_change` — the
    dos_answer corpus index (`load_rows`) is read+parsed once per file-version
    and re-read after the file changes on disk.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from dos import _tomlcache
from dos import config as _config


def _bump_mtime(path: Path) -> None:
    """Force a distinct mtime so the cache sees a new file-version.

    A rewrite within the same clock tick can land the same st_mtime_ns on a
    coarse filesystem; sleep a hair and touch to guarantee the key changes.
    """
    time.sleep(0.02)
    os.utime(path, None)


def test_workspace_config_parses_toml_once(tmp_path, monkeypatch):
    """One build parses dos.toml once; repeated builds of the unchanged file add none."""
    toml = tmp_path / "dos.toml"
    toml.write_text("[reasons]\n[stamp]\n[lanes]\n", encoding="utf-8")

    import tomllib

    calls = {"n": 0}
    real_loads = tomllib.loads

    def counting_loads(s, *a, **k):
        calls["n"] += 1
        return real_loads(s, *a, **k)

    monkeypatch.setattr(tomllib, "loads", counting_loads)
    _tomlcache.clear_cache()

    _config.load_workspace_config(str(tmp_path), gather_env=False)
    after_first = calls["n"]
    # The single file is parsed ONCE for the whole ~25-table readback, not per table.
    assert after_first == 1, (
        f"expected dos.toml parsed once per build, got {after_first} parses "
        "(the per-layer re-read storm regressed)"
    )

    # Two more builds of the UNCHANGED file must be served from the cache: 0 parses.
    _config.load_workspace_config(str(tmp_path), gather_env=False)
    _config.load_workspace_config(str(tmp_path), gather_env=False)
    assert calls["n"] == after_first, (
        f"repeated builds of an unchanged dos.toml re-parsed it "
        f"({calls['n'] - after_first} extra parses); the cache is not serving them"
    )


def test_workspace_config_reparses_after_mtime_change(tmp_path, monkeypatch):
    """A live edit to dos.toml (new mtime) forces exactly one fresh parse."""
    toml = tmp_path / "dos.toml"
    toml.write_text("[reasons]\n", encoding="utf-8")

    import tomllib

    calls = {"n": 0}
    real_loads = tomllib.loads

    def counting_loads(s, *a, **k):
        calls["n"] += 1
        return real_loads(s, *a, **k)

    monkeypatch.setattr(tomllib, "loads", counting_loads)
    _tomlcache.clear_cache()

    _config.load_workspace_config(str(tmp_path), gather_env=False)
    assert calls["n"] == 1

    # Edit the file — a real live edit. The mtime bump makes the cache key change.
    toml.write_text("[reasons]\n[stamp]\n[lanes]\n", encoding="utf-8")
    _bump_mtime(toml)

    before = calls["n"]
    cfg = _config.load_workspace_config(str(tmp_path), gather_env=False)
    assert calls["n"] - before == 1, (
        "the changed dos.toml was not re-parsed — invalidation is broken "
        "(a live edit would be served stale)"
    )
    # And the new content actually took effect (not a stale cached config).
    assert cfg.stamp is not None


def test_missing_toml_is_not_a_parse(tmp_path, monkeypatch):
    """No dos.toml -> zero parses, and the config is the generic default."""
    import tomllib

    calls = {"n": 0}
    real_loads = tomllib.loads
    monkeypatch.setattr(
        tomllib, "loads", lambda s, *a, **k: (calls.__setitem__("n", calls["n"] + 1), real_loads(s, *a, **k))[1]
    )
    _tomlcache.clear_cache()

    # tmp_path has no dos.toml.
    cfg = _config.load_workspace_config(str(tmp_path), gather_env=False)
    assert calls["n"] == 0, "a missing dos.toml must not trigger a parse"
    # Degrades cleanly to a usable generic config (missing-file semantics intact).
    assert cfg.lanes is not None and cfg.reasons is not None


def test_read_toml_cached_missing_file_is_empty(tmp_path):
    """The helper returns {} for an absent file (the callers' degrade-to-base path)."""
    _tomlcache.clear_cache()
    assert _tomlcache.read_toml_cached(tmp_path / "nope.toml") == {}


def test_read_toml_cached_malformed_raises(tmp_path):
    """A present-but-malformed file still raises (uncached) — warn-and-fall-back stands."""
    bad = tmp_path / "dos.toml"
    bad.write_text("this is = = not toml", encoding="utf-8")
    _tomlcache.clear_cache()
    with pytest.raises(Exception):
        _tomlcache.read_toml_cached(bad)


# ---------------------------------------------------------------------------
# The dos_answer corpus index — answers.load_rows()
# ---------------------------------------------------------------------------
pytest.importorskip("mcp", reason="dos-mcp needs the optional `mcp` extra")

from dos_mcp import answers as _answers  # noqa: E402


def _seed_index(tmp_path: Path, n: int = 3) -> Path:
    idx = tmp_path / "index.jsonl"
    lines = [
        json.dumps({"slug": f"q{i}", "question": f"how do I x{i}", "queries": [], "answer": f"a{i}"})
        for i in range(n)
    ]
    idx.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return idx


def test_load_rows_reads_index_once(tmp_path, monkeypatch):
    """load_rows reads+parses the index once, then serves repeats from the cache."""
    idx = _seed_index(tmp_path)
    monkeypatch.setattr(_answers, "_INDEX_PATH", idx, raising=True)
    _answers._ROWS_CACHE.clear()

    reads = {"n": 0}
    real_read_text = Path.read_text

    def counting_read_text(self, *a, **k):
        if self == idx:
            reads["n"] += 1
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    rows1 = _answers.load_rows()
    assert len(rows1) == 3
    assert reads["n"] == 1

    # Repeated calls on the unchanged index: no further reads.
    _answers.load_rows()
    _answers.load_rows()
    assert reads["n"] == 1, (
        f"the answer index was re-read {reads['n']} times for an unchanged file; "
        "the load_rows cache is not serving repeats"
    )


def test_load_rows_reparses_after_mtime_change(tmp_path, monkeypatch):
    """A rebuilt index (new mtime) is re-read on the next load_rows call."""
    idx = _seed_index(tmp_path, n=2)
    monkeypatch.setattr(_answers, "_INDEX_PATH", idx, raising=True)
    _answers._ROWS_CACHE.clear()

    rows1 = _answers.load_rows()
    assert len(rows1) == 2

    # Rebuild the index with more rows (a real rewrite bumps mtime).
    _seed_index(tmp_path, n=5)
    _bump_mtime(idx)

    rows2 = _answers.load_rows()
    assert len(rows2) == 5, (
        "the rebuilt answer index was served stale — load_rows invalidation is broken"
    )
