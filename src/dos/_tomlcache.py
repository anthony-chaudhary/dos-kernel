"""One shared, mtime-keyed reader+parser for ``dos.toml`` — the anti-storm cache.

Every config-layer readback (`config._load_toml_table`,
`config.load_class_budgets_from_toml`, and the ~two-dozen per-module
`load_from_toml` seams in `reasons`/`stamp`/`enumerate`/`cooldown`/… ) used to
`tomllib.loads(p.read_text(encoding="utf-8-sig"))` the SAME file independently.
One `load_workspace_config()` call therefore re-read and re-parsed a single
`dos.toml` ~25 times with zero caching; the MCP server rebuilds the config on
EVERY tool call, so a burst of tool calls turned into a page-fault / allocation
storm on one small file.

This module holds the ONE cached parse. `read_toml_cached(path)` returns the
fully-parsed top-level table (a plain ``dict``), keyed on the file's
``(resolved_path, st_mtime_ns, st_size)`` so a live edit to ``dos.toml`` is
picked up on the next read (invalidation is correct by construction — a changed
mtime is a cache miss). It is a pure, stdlib-only helper (no `dos` imports), so
any kernel/seam module can route through it without an import cycle.

Semantics preserved exactly, so every caller behaves byte-identically:

  * **Missing file → ``{}``.** Callers today branch on ``p.exists()`` and return
    their ``base`` when it is False; an empty dict makes ``data.get(key)`` return
    ``None`` down the SAME "degrade to base" path. The missing-file semantics of
    no caller change.
  * **`tomllib` unavailable (py<3.11 with no `tomli`) → ``{}``.** Same "degrade
    to base" outcome the callers' own `except ModuleNotFoundError` branch took.
  * **Present but MALFORMED → the parse error propagates** (`tomllib` raises
    `TOMLDecodeError`, a `ValueError` subclass) and is NOT cached — exactly as
    today, where `load_workspace_config`'s `_layer` catches the `ValueError`,
    warns, and keeps the base. A malformed file never poisons the cache.
  * **BOM preserved.** Read via ``encoding="utf-8-sig"`` so a UTF-8 BOM (what
    PowerShell's default `utf8` writes) is transparently stripped, the same as
    every call site did before.

The cache is bounded (a small LRU-ish cap, evicting the oldest entry) so it can
never grow without limit even if a long-lived process reads many distinct
workspaces.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["read_toml_cached", "clear_cache"]

# (resolved_path_str, st_mtime_ns, st_size) -> parsed top-level table.
# Insertion-ordered; oldest entry evicted first once _MAX_ENTRIES is exceeded.
_CACHE: dict[tuple[str, int, int], dict] = {}

# Cap the number of distinct (file, version) entries. Small on purpose: a process
# typically touches ONE dos.toml (its workspace), and re-reads dominate; extra
# room covers a handful of workspaces or a few live edits without unbounded growth.
_MAX_ENTRIES = 64


def read_toml_cached(path: Path | str) -> dict:
    """Parsed top-level table of the ``dos.toml`` at ``path`` — cached per version.

    Returns ``{}`` when the file is absent or `tomllib`/`tomli` is unavailable
    (both the "degrade to base" outcomes the callers already handle via
    ``data.get(key) is None``). A present-but-malformed file lets the parse error
    propagate (uncached), so the caller's existing warn-and-fall-back still fires.
    The returned dict is the shared cached object — callers only READ it (they
    pull a sub-table and validate a copy), so it is not defensively copied.
    """
    p = Path(path)
    try:
        st = os.stat(p)
    except OSError:
        # Absent (or unstatable) → the missing-file branch every caller took.
        return {}

    key = (str(p.resolve()), st.st_mtime_ns, st.st_size)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    try:
        import tomllib  # py3.11+
    except ModuleNotFoundError:  # pragma: no cover - py<3.11 fallback
        try:
            import tomli as tomllib  # type: ignore
        except ModuleNotFoundError:
            return {}

    # `utf-8-sig` transparently strips a UTF-8 BOM (a no-op when absent). A parse
    # error propagates here (NOT stored) so the caller's existing handling stands.
    data = tomllib.loads(p.read_text(encoding="utf-8-sig"))

    if len(_CACHE) >= _MAX_ENTRIES:
        # Evict the oldest inserted entry (dicts preserve insertion order).
        oldest = next(iter(_CACHE))
        del _CACHE[oldest]
    _CACHE[key] = data
    return data


def clear_cache() -> None:
    """Drop every cached parse. For tests / a host that wants a cold read."""
    _CACHE.clear()
