"""Suite-wide test isolation so no test writes a lease to the REAL workspace WAL.

THE LEAK THIS CLOSES (FQ-532, docs/281). The lane journal is a single
append-only WAL whose default path resolves against the ACTIVE WORKSPACE — and
the workspace is discovered from the CURRENT WORKING DIRECTORY (`config.active()`
→ `default_config(cwd)` → `PathLayout.for_root(cwd)` →
`<cwd>/docs/_plans/lane-journal.jsonl`). When `pytest` is launched from inside a
real DOS workspace, ANY test that takes a real lease through that un-redirected
default — `config.active()`, `lane_journal.JOURNAL_PATH`, or a `dos lease-lane`
subprocess that inherits the cwd but no explicit `--workspace` — appends its
ACQUIRE/HEARTBEAT to that workspace's *live* journal.

Because the structural WAL fold (`lane_journal.replay`) has no TTL/heartbeat
expiry, a fixture's ACQUIRE that the test process never RELEASEs (a crash, a
kill, a tight re-acquire loop the harness interrupts) becomes an IMMORTAL phantom
lease — and the PRE-admission hook reading that same WAL then refuses live agent
tool calls against a lane no process actually holds. That is exactly the
`alpha`/`DTE`/`orchestration` phantom that DENIED Read/Edit in the field.

THE FIX is to move the *whole suite's* working directory into a throwaway tmp
workspace, so a bare `config.active()` resolves the journal under tmp, never
under the real repo. This is the LEAST-SURPRISE lever: it does NOT shadow a
test's own `cfg.paths.lane_journal` (tests that build `default_config(tmp_path)`
keep an env-free, self-consistent path where their direct WAL writes and the
resolver-based reads still agree — the watchdog/decisions idiom), and it does NOT
require a `DISPATCH_LANE_JOURNAL_PATH` override that would override that path.
Subprocess tests that pass an explicit `--workspace`/`-C <tmp>` are already
isolated; those that inherit cwd now inherit the tmp cwd too.

`autouse=True` + session scope: the chdir happens ONCE before the first test and
is restored after the last, so the real workspace is never the cwd while any test
runs. A test that needs a specific cwd uses `monkeypatch.chdir(...)` in its body,
which is undone per-test and so cannot escape this session-level tmp.

THE SECOND LEAK THIS CLOSES (the machine-global twin, 2026-06-10 audit). The
central project index (`home.ensure_project_home` → `<DOS_HOME>/projects/
index.jsonl`) resolves against the REAL `%APPDATA%/dos` / `~/.dos` whenever the
`DISPATCH_HOME` env override is unset — and it is append-only with no
retention. Isolating only the cwd left every test that crosses a persisting
CLI path registering its throwaway tmp workspace into the operator's real
index FOREVER: the live index was 87% dead pytest tmp dirs (3796 of 4368
rows). Same disease docs/139 fixed on the lane journal, one shared file later.
So this fixture also pins `DISPATCH_HOME` (imported as `config.ENV_DOS_HOME`,
never a re-typed literal) to a session tmp dir — env vars are inherited by
every `dos` subprocess the tests shell, so the redirect covers both in-process
and subprocess writers. Tests that redirect the home themselves
(`monkeypatch.setenv(\"DISPATCH_HOME\", …)`, the hermetic-home idiom) override
per-test and restore back to THIS tmp value, never to the real home.
`home._is_temp_root` is the in-kernel backstop for consumers that lack this
fixture.

This conftest also carries two small pieces of the agent-surface litmus tier
(docs/290 Phase 1, `tests/test_agent_surface.py`): the AV6 session guard
(`_suite_is_effect_free_on_tracked_files` — running the suite must modify no
tracked file) and the collection-count record `pytest_collection_finish` writes
for the AV5 suite-size litmus. Both live here because they are session-level
facts a single test module cannot observe alone.
"""
from __future__ import annotations

import builtins
import io
import os
import subprocess
from collections.abc import Callable
from functools import wraps
from pathlib import Path

import pytest

from dos.config import ENV_DOS_HOME

# The repo this suite tests, anchored on THIS file — tests are tooling that
# ships with the repo it serves (unlike the kernel, which must never assume
# its tree), and the suite's cwd is deliberately a tmp dir (see below), so
# cwd-relative git calls would miss the real repo entirely.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_WRITE_MODE_MARKERS = frozenset({"w", "a", "x", "+"})


@pytest.fixture(autouse=True, scope="session")
def _isolate_workspace_cwd(tmp_path_factory):
    """Run the whole suite from a throwaway tmp workspace, not the real repo.

    A bare `config.active()` discovers its workspace from cwd; pinning cwd to a
    tmp dir means the default-resolved lane journal (and every other
    workspace-relative path) lands under tmp, so no test can append a lease to the
    real workspace's live WAL. The home override (`DISPATCH_HOME`) is pinned to a
    sibling tmp dir for the same reason one tier up: no test (nor any `dos`
    subprocess a test shells) can register a throwaway workspace into the
    operator's real machine-global index. Both restored after the session.
    """
    ws = tmp_path_factory.mktemp("dos-suite-workspace")
    home = tmp_path_factory.mktemp("dos-suite-home")
    prev = os.getcwd()
    prev_home = os.environ.get(ENV_DOS_HOME)
    os.chdir(ws)
    os.environ[ENV_DOS_HOME] = str(home)
    try:
        yield ws
    finally:
        os.chdir(prev)
        if prev_home is None:
            os.environ.pop(ENV_DOS_HOME, None)
        else:
            os.environ[ENV_DOS_HOME] = prev_home


def pytest_collection_finish(session) -> None:
    """Record this session's collected-test count for the AV5 suite-size litmus.

    `test_agent_surface.py::test_av5_*` (docs/290 Phase 1) compares the suite
    size the docs CLAIM (~3,900) against the count pytest actually COLLECTS. In
    a full run the true count is free — it is `len(session.items)` — so it is
    recorded here; a partial run (a path / `-k` / `-m` selection) cannot use its
    own collection as the suite size and falls back to one `--collect-only`
    subprocess inside the test's session fixture.
    """
    session.config._dos_collected_count = len(session.items)


def _modified_tracked_paths(porcelain_text: str) -> frozenset[str]:
    """PURE: `git status --porcelain` text -> the set of changed TRACKED paths.

    Untracked (`??`) and ignored (`!!`) entries are excluded: AV6 (docs/290)
    pins "running the suite modifies no *tracked* file" — scratch under an
    ignored dir is out of scope, and the hot tree's legitimately-dirty entries
    are handled by the caller comparing before/after SETS, never by demanding
    absolute cleanliness.
    """
    paths: set[str] = set()
    for line in porcelain_text.splitlines():
        if len(line) < 4:
            continue
        status = line[:2]
        if status in ("??", "!!"):
            continue
        paths.add(line[3:].replace("\\", "/"))
    return frozenset(paths)


def _repo_path_opened_for_write(file: object, mode: object) -> str | None:
    """Return the repo-relative path if this open can write a file in this repo."""
    mode_text = str(mode)
    if not any(marker in mode_text for marker in _WRITE_MODE_MARKERS):
        return None
    try:
        path = Path(file).resolve()
    except (OSError, TypeError, ValueError):
        return None
    try:
        rel = path.relative_to(_REPO_ROOT)
    except ValueError:
        return None
    return rel.as_posix()


def _new_tracked_paths_written_by_session(
    before: frozenset[str],
    after: frozenset[str],
    written_by_session: set[str] | frozenset[str],
) -> list[str]:
    """PURE: dirty-at-end paths that were clean at start and this session wrote."""
    return sorted((after - before).intersection(written_by_session))


def _install_repo_write_tracker(written_by_session: set[str]) -> Callable[[], None]:
    """Track write-mode opens under the repo while the pytest session runs."""
    original_builtins_open = builtins.open
    original_io_open = io.open

    def record(file: object, mode: object) -> None:
        rel = _repo_path_opened_for_write(file, mode)
        if rel is not None:
            written_by_session.add(rel)

    @wraps(original_builtins_open)
    def tracked_builtins_open(file, mode="r", *args, **kwargs):
        record(file, mode)
        return original_builtins_open(file, mode, *args, **kwargs)

    @wraps(original_io_open)
    def tracked_io_open(file, mode="r", *args, **kwargs):
        record(file, mode)
        return original_io_open(file, mode, *args, **kwargs)

    builtins.open = tracked_builtins_open
    io.open = tracked_io_open

    def restore() -> None:
        builtins.open = original_builtins_open
        io.open = original_io_open

    return restore


def _tracked_status_snapshot() -> frozenset[str] | None:
    """Boundary I/O: the repo's changed-tracked set right now; None = no witness.

    `None` (git missing, a timeout, not a work tree — e.g. an unpacked sdist)
    makes the AV6 guard ABSTAIN rather than fail the session: absent evidence is
    not evidence of a violation (the fail-to-abstain discipline).
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return _modified_tracked_paths(proc.stdout)


@pytest.fixture(autouse=True, scope="session")
def _suite_is_effect_free_on_tracked_files():
    """AV6 (docs/290 Phase 1, pins D8): the suite must not modify tracked files.

    The D8 defect: a full-suite run on Windows rewrote two committed Go parity
    corpora as CRLF (`write_text` without `newline="\\n"`), dirtying every cold
    clone that ran the tests. The guard is the kernel shape — evidence snapshot
    at the boundary (the modified-tracked set before the first test), a pure
    set comparison after the last, intersected with write-mode opens made by
    this pytest process. Delta-of-sets, NOT absolute cleanliness: the hot tree
    is legitimately dirty with concurrent work; the suite must only add nothing
    itself.
    """
    written_by_session: set[str] = set()
    restore_write_tracker = _install_repo_write_tracker(written_by_session)
    before = _tracked_status_snapshot()
    try:
        yield
        if before is None:
            return
        after = _tracked_status_snapshot()
        if after is None:
            return
        new = _new_tracked_paths_written_by_session(
            before, after, written_by_session
        )
        assert not new, (
            "AV6 (docs/290): this pytest session left tracked files modified "
            f"that were clean when it started and were opened for write by "
            f"this session: {new}. The suite must be effect-free on tracked "
            "paths (the D8 corpus-CRLF defect class)."
        )
    finally:
        restore_write_tracker()
