"""The state-home — DOS's per-project `.dos/` scaffolding and the machine-local
projection store (docs/75_state-home-plan.md).

DOS was lifted out of the reference userland app and inherited its body-plan:
the generic default scattered DOS's own emissions across the served repo's
`docs/` tree. This module
is the generic default's *own* body — a per-project **`.dos/`** home (auto-created
on the first write, gitignored-by-default) plus a machine-local **DOS_HOME**
(`~/.dos`) holding a rebuildable projection over every workspace DOS has served.

Two hard properties this module exists to guarantee (both pinned by tests):

  * **Read-only syscalls write nothing.** Nothing here runs on a `verify` / `man`
    / `doctor` / `decisions` / `judge` / `journal-read` path. `ensure_project_home`
    is invoked ONLY by the CLI's persisting handlers (`dos lease`,
    `dos arbitrate --force`-on-capture). So `dos verify` in a stranger's repo
    creates no `.dos/`, no `~/.dos` row.
  * **The central store is a projection, never a source of truth.** Per-project
    `.dos/project.json` is authoritative; `~/.dos/{projects/index.jsonl,
    decisions.jsonl}` are rebuildable digests that `dos reindex` regenerates by
    walking the `.dos/` dirs. A corrupt or deleted central index is never a
    data-loss event.

Layering (CLAUDE.md): this is layer-1 kernel — it imports only `dos.config` and
`dos.archive_lock` (a *downward* edge; `archive_lock` itself imports only
`dos.config`, so the graph stays a DAG) plus stdlib. No kernel module imports
`home`; only the CLI (layer 3) wires it in. It names no host.

Determinism (Law 5): `project_id` is a pure function of the resolved path (no
clock, no randomness). The two genuinely time-sourced fields (`created_at`,
`ts_ms`) are *event* stamps and take an injectable `clock=` for reproducible
tests. Central-store writes reuse `lane_journal`'s fsync/torn-tail discipline AND
take a real `O_CREAT|O_EXCL` cross-process lock — `O_APPEND` alone is not atomic
on win32 (the platform), and unlike `lane_journal` the central store has no
surrounding `_StateFileLock`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from dos import _filelock
from dos import config as _config

# Re-derivable identity-card / index schema version. Bumped only on a
# breaking shape change; readers tolerate older rows (best-effort projection).
SCHEMA = 1

# The shipped `.dos/.gitignore` — a self-ignoring directory, so a host repo needs
# zero `.gitignore` edits of its own. `*` ignores everything under `.dos/` from
# the host repo's view; `!.gitignore` keeps this marker visible.
_DOT_DOS_GITIGNORE = """\
# DOS per-project state — re-derivable emissions (runs, leases, verdicts,
# lane journal, soak index). DOS auto-created this directory and ignores its own
# contents so they never enter your repo's history. Safe to delete; DOS rebuilds
# with `dos reindex`. What this directory is and why it isn't committed:
# https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/DOT_DOS.md
*
!.gitignore
"""

_COURTESY = (
    "dos: created .dos/ for this workspace ({dot_dos}) — gitignored DOS state; "
    "`dos reindex` rebuilds central indices"
)


# ---------------------------------------------------------------------------
# Time — event stamps only (injectable for deterministic tests).
# ---------------------------------------------------------------------------


def _now_iso(clock: Callable[[], int] | None = None) -> str:
    """Second-resolution UTC stamp for an event field (created_at/last_seen)."""
    ms = clock() if clock is not None else int(time.time() * 1000)
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _now_ms(clock: Callable[[], int] | None = None) -> int:
    return clock() if clock is not None else int(time.time() * 1000)


# ---------------------------------------------------------------------------
# project_id — deterministic, path-derived (no clock, no random). The id is
# minted ONCE into `.dos/project.json` and read back thereafter; the card is
# authoritative (so a re-mint under the SAME path view always agrees, and a
# cross-OS-path-view divergence is a known, out-of-scope limitation — §5.6).
# ---------------------------------------------------------------------------


def project_id_for(workspace_root: Path | str) -> str:
    """16 hex chars (64 bits) of SHA-256 over the resolved POSIX path.

    Deterministic: the same realpath always yields the same id. Used to MINT on
    first ensure (when no card exists); thereafter the stored card id is read
    back. Cross-OS-path-view stability (a Windows drive path vs its
    ``/mnt/...`` WSL view) is explicitly out of scope — each view gets its
    own card/id.
    """
    real = Path(workspace_root).resolve().as_posix()
    return hashlib.sha256(real.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# The cross-process lock for the central JSONL writes. A tiny re-implementation
# of archive_lock's O_CREAT|O_EXCL primitive (we do NOT import archive_lock's CLI
# surface; we only need the bare mutex). Serializes every central-store write —
# both the hot `ensure_project_home` append and reindex's whole-file rewrite —
# so an append can't land between reindex's read and its os.replace.
# ---------------------------------------------------------------------------

_HOME_LOCK_TTL_S = 60.0   # a central write is sub-second; older = a dead holder.
_HOME_LOCK_RETRIES = 50
_HOME_LOCK_INTERVAL_S = 0.05


@contextmanager
def _home_lock(home_lock: Path) -> Iterator[None]:
    """Hold the DOS_HOME write mutex for the duration of the block.

    Atomic O_CREAT|O_EXCL acquire with bounded retry; steals a lock older than the
    TTL (a crashed holder must not wedge the store forever) through the shared
    value-keyed CAS (`_filelock.steal_stale`) — the SAME primitive archive_lock and
    lane_lease use, so the naive unlink-then-create steal (two stealers both win, a
    lost/duplicated central row) cannot be re-introduced here. Best-effort: if the
    lock can't be acquired within the retry budget we proceed anyway rather than
    fail a telemetry write (the central store is rebuildable; a lost row is
    recoverable by `dos reindex`, a hung CLI is not).
    """
    owner = f"home-{os.getpid()}"
    acquired = False
    for _ in range(_HOME_LOCK_RETRIES):
        try:
            _filelock.write_lock(home_lock, owner)
            acquired = True
            break
        except FileExistsError:
            info = _filelock.read_lock(home_lock)
            if info is None:
                continue  # unlinked between EEXIST and read — retry the create
            age = _filelock_age_seconds(info)
            if age is not None and age >= _HOME_LOCK_TTL_S:
                # Value-keyed steal of the EXACT stale lock observed (not a bare
                # unlink) so two concurrent stealers can't both win and clobber the
                # store's read-modify-append.
                if _filelock.steal_stale(home_lock, owner, info):
                    acquired = True
                    break
                continue  # lost the steal — retry
            time.sleep(_HOME_LOCK_INTERVAL_S)
    try:
        yield
    finally:
        if acquired:
            # Release only OUR lock — a stealer past the TTL may now hold it.
            info = _filelock.read_lock(home_lock)
            if info is None or info.get("owner") in (owner, None):
                try:
                    home_lock.unlink()
                except FileNotFoundError:
                    pass


def _filelock_age_seconds(info: dict) -> float | None:
    """Seconds since the lock's `acquired_at` stamp; None if unparseable. Local to
    the home lock's TTL check (the shared `_filelock` body stamps `acquired_at`)."""
    raw = str((info or {}).get("acquired_at", ""))
    try:
        ts = dt.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except (ValueError, TypeError):
        return None
    return (dt.datetime.now(dt.timezone.utc) - ts).total_seconds()


# ---------------------------------------------------------------------------
# JSONL append/read — lane_journal's fsync + torn-tail discipline, minus the
# `seq` (the central store has no replay-order invariant that needs a monotonic
# seq, and computing max+1 would reintroduce a read-modify-write race). The
# CALLER holds `_home_lock` around the append; O_APPEND is the belt to that
# suspenders, the way lane_journal frames it.
# ---------------------------------------------------------------------------


def _append_jsonl(path: Path, row: dict) -> dict:
    """Append one canonical-JSON row to a JSONL file and fsync it. Caller locks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, sort_keys=True, default=str, ensure_ascii=False) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return row


def read_jsonl(path: Path) -> list[dict]:
    """Every row of a JSONL store, torn-tail tolerant (lane_journal's rule).

    Skips an unparseable TRAILING line (a crash mid-append); a non-trailing
    corrupt line is surfaced as a `_CORRUPT` sentinel so a reindex/audit notices
    rather than silently dropping a row from the middle.
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = raw.splitlines()
    out: list[dict] = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                break  # torn final line — "didn't happen"
            out.append({"_CORRUPT": True, "_raw": s, "_line": i})
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _atomic_write_jsonl(path: Path, rows: list[dict]) -> None:
    """Rewrite a JSONL store wholesale via tmp+os.replace (reindex compaction).
    Caller holds `_home_lock`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    body = "".join(
        json.dumps(r, sort_keys=True, default=str, ensure_ascii=False) + "\n"
        for r in rows
    )
    tmp.write_text(body, encoding="utf-8")
    _filelock.atomic_replace(tmp, path)


# ---------------------------------------------------------------------------
# DOS_HOME creation (the only writer to ~/.dos that creates the tree).
# ---------------------------------------------------------------------------


def ensure_dos_home(home: Path | str | None = None) -> _config.HomeLayout:
    """Resolve and CREATE the machine-local DOS_HOME tree; return its layout.

    Idempotent. Unlike `resolve_dos_home` (pure path math, never creates), this
    is a deliberate write — called only from a persisting path.
    """
    layout = _config.HomeLayout.for_home(home)
    layout.home.mkdir(parents=True, exist_ok=True)
    layout.projects_index.parent.mkdir(parents=True, exist_ok=True)
    return layout


# ---------------------------------------------------------------------------
# The per-project identity card.
# ---------------------------------------------------------------------------


def _read_card(card_path: Path) -> dict | None:
    if not card_path.exists():
        return None
    try:
        data = json.loads(card_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_card(card_path: Path, card: dict) -> None:
    """Atomic tmp+os.replace write of the identity card."""
    card_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = card_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(card, indent=2, sort_keys=True), encoding="utf-8")
    _filelock.atomic_replace(tmp, card_path)


def _projects_row(cfg, card: dict, *, clock=None) -> dict:
    """Build the central projects/index.jsonl row from the card + on-disk counts.

    Counts are derived by counting artifacts, never a clock read. A missing
    emissions dir simply counts zero (a fresh project).
    """
    root = cfg.paths.root
    runs_dir = cfg.paths.fanout_runs  # == chained_runs == dispatch_loops under .dos/
    verdicts = cfg.paths.next_packets
    journal = cfg.paths.lane_journal

    run_count = 0
    if runs_dir.exists():
        run_count = sum(1 for c in runs_dir.iterdir() if c.is_dir())
    wedge_count = 0
    if verdicts.exists():
        wedge_count = sum(1 for _ in verdicts.glob(".verdict-*.json"))
    refusal_count = 0
    if journal.exists():
        for e in read_jsonl(journal):
            if e.get("op") == "REFUSE":
                refusal_count += 1

    return {
        "schema": SCHEMA,
        "project_id": card["project_id"],
        "root": str(root),
        "dos_dir": str(cfg.paths.dot_dos),
        "label": root.name,
        "status": "active",
        "first_seen": card.get("created_at"),
        "last_indexed": _now_iso(clock),
        "run_count": run_count,
        "wedge_count": wedge_count,
        "refusal_count": refusal_count,
    }


# ---------------------------------------------------------------------------
# ensure_project_home — the auto-create-on-first-write entry point.
# ---------------------------------------------------------------------------


def ensure_project_home(
    cfg,
    *,
    home: Path | str | None = None,
    clock: Callable[[], int] | None = None,
    _stderr=None,
) -> Path:
    """Lazily scaffold `<root>/.dos/` and register the project centrally.

    Idempotent and safe to call on every persisting syscall. Invoked ONLY from
    the CLI's persisting handlers — never from a read-only path (so the
    read-only-writes-nothing property holds). Steps:

      1. atomic `os.mkdir(.dos)` — the process that wins is `first_time` (this is
         the exactly-once signal across concurrent first-persists, NOT a
         check-then-act `.exists()` which races);
      2. write `.dos/.gitignore` if absent (never overwrite a host's edit);
      3. write/update `.dos/project.json` (preserve project_id + created_at);
      4. under the DOS_HOME write-lock, fold the project's row into
         `~/.dos/projects/index.jsonl` (best-effort — a central-store failure is
         logged, never raised: the card is truth, the index is rebuildable);
      5. if `first_time`, emit exactly one stderr courtesy line.

    Returns the `.dos/` path.
    """
    stderr = _stderr if _stderr is not None else sys.stderr
    dot_dos = cfg.paths.dot_dos

    # (1) Atomic first-time detection: only the winner of the create is first.
    first_time = False
    try:
        os.mkdir(dot_dos)
        first_time = True
    except FileExistsError:
        pass
    except OSError:
        # Parent missing (shouldn't happen — root exists) — fall back to makedirs.
        pass
    dot_dos.mkdir(parents=True, exist_ok=True)

    # (2) Self-ignoring marker (write-if-absent).
    gitignore = dot_dos / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(_DOT_DOS_GITIGNORE, encoding="utf-8")

    # (3) The identity card — mint id+created_at once, refresh last_seen.
    card_path = cfg.paths.project_card or (dot_dos / "project.json")
    card = _read_card(card_path)
    if card is None:
        card = {
            "schema": SCHEMA,
            "project_id": project_id_for(cfg.paths.root),
            "root": str(cfg.paths.root),
            "created_at": _now_iso(clock),
        }
    card["last_seen"] = _now_iso(clock)
    card["dos_version"] = _dos_version()
    _write_card(card_path, card)

    # (4) Central registration — best-effort, never fatal. SKIPPED for a
    # throwaway workspace that would pollute the REAL machine-global index: a
    # root under the OS temp dir with no explicit home override is a test/tmp
    # workspace whose row would outlive the workspace forever (the index is
    # append-only; the 2026-06-10 audit found 87% of the live index was dead
    # pytest tmp dirs — the same disease docs/139 fixed on the lane journal).
    # An explicit `home=` arg or a `DISPATCH_HOME` env override means the caller
    # ALREADY redirected the central store (the hermetic-test idiom), so a
    # temp-rooted project still registers there. The per-project `.dos/` above
    # is always scaffolded either way — only the machine-global projection skips.
    skip_central = (
        home is None
        and not os.environ.get(_config.ENV_DOS_HOME)
        and _is_temp_root(cfg.paths.root)
    )
    if not skip_central:
        try:
            h = ensure_dos_home(home)
            row = _projects_row(cfg, card, clock=clock)
            with _home_lock(h.home_lock):
                _register_root(h.roots_log, str(cfg.paths.root))
                _fold_projects_row(h.projects_index, row)
        except Exception as exc:  # noqa: BLE001 — telemetry must never break a persist
            print(f"dos: warning: could not update central index: {exc}",
                  file=stderr)

    # (5) One-time courtesy line.
    if first_time:
        print(_COURTESY.format(dot_dos=dot_dos), file=stderr)

    return dot_dos


def _is_temp_root(root: Path | str, tempdir: Path | str | None = None) -> bool:
    """True iff ``root`` lives under the OS temp dir (``tempdir`` overrides for tests).

    Pure path containment — no I/O beyond `resolve()`. Used by
    `ensure_project_home` to keep throwaway workspaces out of the REAL
    machine-global index; a cross-drive pair (ValueError) or an unresolvable
    path (OSError) is conservatively NOT temp, so a weird root still registers
    rather than silently vanishing from the operator's registry.
    """
    try:
        base = Path(tempdir if tempdir is not None else tempfile.gettempdir()).resolve()
        return Path(root).resolve().is_relative_to(base)
    except (OSError, ValueError):
        return False


def _register_root(roots_log: Path, root: str) -> None:
    """Append ``root`` to the durable path registry if not already present.

    `roots.log` is a plain newline-delimited list of project roots — the one
    central file a PLAIN `reindex` does NOT rewrite, so it survives an index
    deletion and lets reindex rebuild the rich index purely from the live
    `.dos/` cards. (Only `reindex --prune` compacts it, dropping exactly the
    pruned projects' roots — see `_rewrite_roots`.) It is still a projection
    (every root in it also has a `.dos/project.json`), just the durable spine
    of the path list. Caller holds `_home_lock`."""
    roots_log.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if roots_log.exists():
        try:
            existing = {ln.strip() for ln in
                        roots_log.read_text(encoding="utf-8").splitlines() if ln.strip()}
        except OSError:
            existing = set()
    if root not in existing:
        fd = os.open(str(roots_log), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (root + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)


def _read_roots(roots_log: Path) -> list[str]:
    if not roots_log.exists():
        return []
    try:
        return [ln.strip() for ln in
                roots_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return []


def _rewrite_roots(roots_log: Path, roots: list[str]) -> None:
    """Rewrite the durable path registry wholesale — the `--prune` path ONLY.

    Outside a prune, reindex never touches `roots.log` (it is the spine that
    survives an index deletion). Tmp+`atomic_replace`, the same discipline as
    the index rewrite. Caller holds `_home_lock`."""
    roots_log.parent.mkdir(parents=True, exist_ok=True)
    tmp = roots_log.with_suffix(roots_log.suffix + ".tmp")
    tmp.write_text("".join(r + "\n" for r in roots), encoding="utf-8")
    _filelock.atomic_replace(tmp, roots_log)


def _fold_projects_row(index_path: Path, row: dict) -> None:
    """Append the row, keeping last-write-wins-by-project_id semantics.

    The index is an append-only log folded on read (so the hot path is a cheap
    append, no rewrite); reindex compacts it. We append unconditionally — the
    reader/`reindex` keeps only the last row per `project_id` — but preserve the
    original `first_seen` by carrying forward the earliest seen for this id.
    """
    existing = [r for r in read_jsonl(index_path)
                if r.get("project_id") == row["project_id"] and not r.get("_CORRUPT")]
    if existing:
        earliest = min((r.get("first_seen") or row["first_seen"]) for r in existing)
        if earliest:
            row = {**row, "first_seen": earliest}
    _append_jsonl(index_path, row)


def _dos_version() -> str:
    try:
        import dos
        return getattr(dos, "__version__", "0")
    except Exception:  # pragma: no cover
        return "0"


# ---------------------------------------------------------------------------
# Resolved-decision capture (docs/75 §5.7). A decision is "resolved" only on a
# genuinely-persisting operator act — today `dos arbitrate --force` producing an
# acquire a non-forced call would have refused. `dos judge` is read-only and is
# NOT a capture point (running it N times must not multiply rows). The digest is
# written by this function, called from the CLI ACTION layer — never by
# `decisions.py` (which stays a pure read-only projection, Law 3).
#
# To honor projection-not-sync exactly, the resolution is mirrored to the
# project's OWN `.dos/decisions/resolved.jsonl` (local truth) AND projected to
# `~/.dos/decisions.jsonl`; `dos reindex` rebuilds the central log from the local
# mirrors. The append is deduped by (project_id, lane, run_ts, action) so a
# repeated identical force is one logical resolution.
# ---------------------------------------------------------------------------


def _decision_identity(row: dict) -> tuple:
    res = row.get("resolution") or {}
    return (
        row.get("project_id", ""),
        row.get("lane", ""),
        row.get("run_ts", ""),
        res.get("action", "") if isinstance(res, dict) else "",
    )


def append_decision(
    cfg,
    row: dict,
    *,
    home: Path | str | None = None,
    clock: Callable[[], int] | None = None,
) -> dict | None:
    """Record a resolved-decision digest (local mirror + central projection).

    `row` carries the digest fields (kind, resolver_kind, lane, reason_token,
    reason_category, run_ts, resolution). This fills `project_id`/`label`/`ts_ms`
    from `cfg`, mirrors it to the project's `.dos/decisions/resolved.jsonl`, and
    projects it to `~/.dos/decisions.jsonl` under the home write-lock. Deduped by
    `_decision_identity` against the local mirror (so re-running the same force is
    idempotent). Returns the stamped row, or None if it was a duplicate.

    Best-effort on the central projection (a failure is logged, never raised) —
    the local mirror is the rebuildable truth.
    """
    # Read the project_id from the card (authoritative); fall back to deriving it.
    card = _read_card(cfg.paths.project_card or (cfg.paths.dot_dos / "project.json"))
    pid = (card or {}).get("project_id") or project_id_for(cfg.paths.root)
    stamped = {
        "schema": SCHEMA,
        "project_id": pid,
        "label": cfg.paths.root.name,
        **row,
        "ts_ms": _now_ms(clock),
    }

    local = cfg.paths.dot_dos / "decisions" / "resolved.jsonl"
    identity = _decision_identity(stamped)
    if any(_decision_identity(r) == identity for r in read_jsonl(local) if not r.get("_CORRUPT")):
        return None  # already recorded — idempotent

    _append_jsonl(local, stamped)  # local truth first
    try:
        h = ensure_dos_home(home)
        with _home_lock(h.home_lock):
            _append_jsonl(h.decisions_log, stamped)
    except Exception as exc:  # noqa: BLE001 — central projection is best-effort
        import sys as _sys
        print(f"dos: warning: could not project decision centrally: {exc}",
              file=_sys.stderr)
    return stamped


# ---------------------------------------------------------------------------
# reindex — rebuild the central store from the per-project `.dos/` dirs. This is
# the projection-not-sync authority: the central index is DERIVED, never the
# source of truth. It reads the existing index for the known-project PATH LIST
# (the registry), re-stats each `.dos/`, marks active/stale/moved, and rewrites
# the index atomically under the home write-lock. Rebuilds decisions.jsonl from
# each project's local `.dos/decisions/resolved.jsonl` mirror.
# ---------------------------------------------------------------------------


def _fold_latest(rows: list[dict]) -> dict[str, dict]:
    """Last-row-wins per project_id (the index is an append log folded on read)."""
    latest: dict[str, dict] = {}
    earliest_seen: dict[str, str] = {}
    for r in rows:
        if r.get("_CORRUPT"):
            continue
        pid = r.get("project_id")
        if not pid:
            continue
        fs = r.get("first_seen")
        if fs and (pid not in earliest_seen or fs < earliest_seen[pid]):
            earliest_seen[pid] = fs
        latest[pid] = r
    for pid, r in latest.items():
        if earliest_seen.get(pid):
            r["first_seen"] = earliest_seen[pid]
    return latest


def reindex(
    home: Path | str | None = None,
    *,
    prune: bool = False,
    clock: Callable[[], int] | None = None,
) -> dict:
    """Rebuild the central store from the live `.dos/` dirs. Returns a summary.

    Algorithm (docs/75 §7, Phase 4):
      1. read the existing `projects/index.jsonl` (torn-tail tolerant), fold to
         the latest row per project_id — this is the known-project registry;
      2. for each, follow the recorded `root`; if its `.dos/` is gone mark
         `stale`; if the card's id differs mark `moved`; else re-stat counts via
         the generic `.dos/` layout (NEVER env vars — reindex reads cfg.paths.*);
      3. rebuild `decisions.jsonl` by concatenating each live project's local
         `.dos/decisions/resolved.jsonl` mirror (so the central log is a pure
         projection of local truth);
      4. atomically rewrite both files under the home write-lock.

    Never crashes on a missing/moved project (marks it, continues). ``prune``
    compacts the registry to real, live projects, in three coordinated drops:
    stale rows leave the rewritten index; THROWAWAY rows — a root under the OS
    temp dir (`_is_temp_root`), registry pollution even while its tmp dir still
    exists, since pytest retains the last few run dirs for days — leave it too;
    and `roots.log` is rewritten down to the kept roots, because a pruned root
    left in the union below would resurrect its row as `stale` on the very next
    plain reindex. The throwaway drop honors the same override exemption as
    `ensure_project_home`'s registration guard: an explicit ``home=`` arg or a
    `DISPATCH_HOME` env override means a deliberately-redirected store, where
    temp-rooted projects are legitimate (the hermetic-test idiom), so only a
    prune aimed at the machine-default home applies it.
    """
    from dos.config import PathLayout

    h = ensure_dos_home(home)
    existing = read_jsonl(h.projects_index)
    folded = _fold_latest(existing)

    # The retroactive twin of ensure_project_home's skip_central guard: armed
    # only when this reindex targets the machine-default home (no home= arg, no
    # env override) — exactly the store the registration guard protects.
    prune_throwaway = (
        prune
        and home is None
        and not os.environ.get(_config.ENV_DOS_HOME)
    )

    # The path list = the durable roots.log UNION the roots recorded in the index.
    # Either alone can rebuild the other (both are projections of the live `.dos/`
    # cards); the union means a deletion of EITHER central file still reindexes.
    roots: dict[str, dict] = {}  # root-string -> the folded index row, if any
    for r in folded.values():
        if r.get("root"):
            roots[r["root"]] = r
    for root_str in _read_roots(h.roots_log):
        roots.setdefault(root_str, {"root": root_str})

    rebuilt_rows: list[dict] = []
    kept_roots: list[str] = []  # survives into roots.log when pruning
    decisions: list[dict] = []
    summary = {"active": 0, "stale": 0, "moved": 0, "throwaway": 0,
               "id_collisions": []}
    seen_ids: dict[str, str] = {}  # project_id -> root, to surface collisions

    for root_str, row in sorted(roots.items()):
        root = Path(root_str)
        if prune_throwaway and _is_temp_root(root):
            summary["throwaway"] += 1
            continue
        layout = PathLayout.for_dos_dir(root)
        card = _read_card(layout.project_card)
        # The card is the authoritative id; fall back to the index row's id, then
        # to deriving it from the path (so a row with no live card still has an id
        # to key the summary on).
        pid = ((card or {}).get("project_id")
               or row.get("project_id")
               or project_id_for(root))

        status = "active"
        if not layout.dot_dos.exists() or card is None:
            status = "stale"
        elif card.get("root") and Path(card["root"]).resolve() != root.resolve():
            # The card records a different home than where we found it → moved.
            status = "moved"

        # Surface a 64-bit truncation collision (two distinct roots → one id) —
        # never silently merge (docs/75 §5.6).
        if status == "active":
            prior = seen_ids.get(pid)
            if prior is not None and prior != str(root):
                summary["id_collisions"].append({"project_id": pid,
                                                  "roots": [prior, str(root)]})
            seen_ids[pid] = str(root)

        summary[status] = summary.get(status, 0) + 1
        if status == "stale" and prune:
            continue
        kept_roots.append(root_str)

        if status == "active":
            new_row = _projects_row(_FakeCfg(layout), card, clock=clock)
            new_row["status"] = "active"
            rebuilt_rows.append(new_row)
            # Collect this project's local resolved-decision mirror.
            decisions.extend(
                r for r in read_jsonl(layout.dot_dos / "decisions" / "resolved.jsonl")
                if not r.get("_CORRUPT")
            )
        else:
            row = {**row, "project_id": pid, "status": status,
                   "last_indexed": _now_iso(clock)}
            rebuilt_rows.append(row)

    rebuilt_rows.sort(key=lambda r: r.get("project_id", ""))
    decisions.sort(key=lambda r: (r.get("ts_ms", 0), r.get("project_id", "")))

    with _home_lock(h.home_lock):
        _atomic_write_jsonl(h.projects_index, rebuilt_rows)
        _atomic_write_jsonl(h.decisions_log, decisions)
        if prune:
            # A prune must be DURABLE: a pruned root left in roots.log re-enters
            # the union above on the next plain reindex and resurrects its row
            # as `stale`. The dropped roots have no surviving index row and no
            # live `.dos/` card a future rebuild could use, so compacting the
            # spine loses nothing rebuildable.
            _rewrite_roots(h.roots_log, kept_roots)

    summary["projects"] = len(rebuilt_rows)
    summary["decisions"] = len(decisions)
    return summary


class _FakeCfg:
    """A minimal cfg shim exposing `.paths` so `_projects_row` can re-stat a
    project during reindex without constructing a full SubstrateConfig (reindex
    only needs the path layout, never lanes/reasons/stamp)."""

    __slots__ = ("paths",)

    def __init__(self, layout):
        self.paths = layout


# ---------------------------------------------------------------------------
# Cross-project read-only queries (docs/75 §7, Phase 4) — pure group-bys over
# the central store. These WRITE NOTHING; they are the home-tier read syscalls.
# ---------------------------------------------------------------------------


def list_projects(home: Path | str | None = None) -> list[dict]:
    """The known-project registry rows, folded latest-per-id, sorted by label."""
    h = _config.HomeLayout.for_home(home)
    rows = list(_fold_latest(read_jsonl(h.projects_index)).values())
    return sorted(rows, key=lambda r: (r.get("label") or "", r.get("project_id") or ""))


def learn(axis: str, home: Path | str | None = None) -> list[dict]:
    """Aggregate the resolved-decision log along one of three closed axes:

      * ``wedge-hotspots``   — which projects accrue the most decisions (by label);
      * ``lane-refusals``    — which lanes get force-overridden most (by lane);
      * ``oracle-calibration`` — resolved decisions grouped by reason_category,
        the signal for whether a deterministic oracle owns the right categories.

    Pure read-only group-by; returns sorted (descending count) tally rows.
    """
    h = _config.HomeLayout.for_home(home)
    rows = [r for r in read_jsonl(h.decisions_log) if not r.get("_CORRUPT")]
    key = {
        "wedge-hotspots": lambda r: r.get("label") or r.get("project_id") or "?",
        "lane-refusals": lambda r: r.get("lane") or "(none)",
        "oracle-calibration": lambda r: r.get("reason_category") or "(uncategorized)",
    }.get(axis)
    if key is None:
        raise ValueError(
            f"unknown learn axis {axis!r}; known: "
            f"wedge-hotspots, lane-refusals, oracle-calibration"
        )
    tally: dict[str, int] = {}
    for r in rows:
        tally[key(r)] = tally.get(key(r), 0) + 1
    return [{"group": g, "count": c}
            for g, c in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))]


# ---------------------------------------------------------------------------
# Scratch reaping (docs/106 §3.4) — keep-last-N over the per-project `.dos/`
# scratch classes the kernel never auto-reaped: verdict sidecars and audit
# reports (recency-floored, no liveness — "a point-in-time artifact"), plus
# run-dirs (recency fallback until the lease-liveness join lands — §3.4).
#
# The DECISION of what to drop is the pure `retention.plan_reap` (kernel leaf);
# this is the I/O half — the scandir + unlink — so it lives here in the home tier,
# never in the pure leaf. Every drop is RETURNED in the report (the CLI prints it):
# the docs/106 §3.4 "no silent caps — log() what you dropped and why" discipline,
# because a reaper that quietly eats a report an operator needed is the disease,
# not the cure. Dry-run (`apply=False`) is the default-safe mode: it computes the
# exact same plan and reports it, deleting nothing.
# ---------------------------------------------------------------------------

# The scratch classes this reaper knows, each: (report-key, the glob over the dir,
# the cap field on RetentionPolicy, whether liveness-gating is REQUIRED-but-unwired).
# A run-dir genuinely has a liveness (its lease may still be live); §3.4 says fall
# back to keep-last-N by mtime until the (loop_ts, lane)->run_id join exists, and
# announce that the gate is not yet applied. Verdicts/audits have no liveness, so
# recency is the honest-and-complete rule for them.
def _scratch_classes(cfg):
    p = cfg.paths
    return [
        # (key, dir, child-predicate, cap-attr, liveness_unwired)
        ("audits", p.dot_dos / "audits",
         lambda e: e.is_file() and e.name.startswith("trajectory-audit-"),
         "audits_keep_last", False),
        ("verdicts", p.verdicts_dir,
         lambda e: e.is_file() and ".verdict-" in e.name,
         "verdicts_keep_last", False),
        ("runs", p.fanout_runs,
         lambda e: e.is_dir(),
         "runs_keep_last", True),
    ]


def reap_scratch(cfg, *, apply: bool = False) -> dict:
    """Reap per-project `.dos/` scratch to the workspace's `[retention]` caps.

    For each scratch class (audits, verdicts, runs) gather `(name, mtime)` at the
    filesystem boundary, ask the pure `retention.plan_reap` which to drop by
    recency (keep the newest ``keep_last``), and — when ``apply`` — unlink them.
    Returns a per-class report: how many were kept, the identifiers dropped, and a
    ``liveness_unwired`` note for the run-dir class (whose lease-liveness gate is
    not yet built — docs/106 §3.4, the correlation-join gap; recency is the
    documented fallback). The report lists EVERY dropped identifier — no silent
    truncation.

    ``apply=False`` (the default) is a dry run: identical plan, deletes nothing —
    so an operator sees exactly what a sweep WOULD remove before authorizing it,
    the same posture as `dos reindex --prune`'s preview. The reaper never touches
    a host's working tree, only DOS's own `.dos/` scratch (docs/106 §5 non-goal).

    The caps live on ``cfg.retention``; a ``None`` cap means "keep everything on
    this axis" and the class is reported as ``unbounded`` (nothing scanned-to-drop).
    The "never reap a live lease" floor is honored structurally here for the only
    class that HAS a lease (runs) by *not yet* reaping on liveness at all — the
    recency fallback can only ever keep MORE than a liveness gate would, never less,
    so it cannot drop a live run that a future gate would spare. (A future
    liveness-gated reaper tightens this; it can only become safer.)

    Each class report also carries a ``data_class`` token — the
    `cfg.data_class.classify` verdict for that scratch dir (TRAJECTORY / AUDIT /
    BASELINE / PRODUCT) — purely as an annotation (the trajectory-vs-product tag).
    It does NOT change WHAT is reaped (the retention caps decide that); it only
    labels the report so a clutter audit / operator can roll the sweep up by kind.
    """
    from dos import retention as _retention

    def _class_of(d: Path) -> str:
        """The data-class of a scratch dir, as a repo-relative-path classify().
        Annotation only — never gates reaping."""
        try:
            rel = d.relative_to(cfg.paths.root).as_posix()
        except (ValueError, OSError):
            rel = d.as_posix()
        return cfg.data_class.classify(rel)

    report: dict[str, dict] = {}
    for key, d, pred, cap_attr, liveness_unwired in _scratch_classes(cfg):
        cap = getattr(cfg.retention, cap_attr)
        cls: dict = {"dir": str(d), "data_class": _class_of(d),
                     "cap": cap, "kept": 0, "dropped": []}
        if liveness_unwired:
            cls["liveness_unwired"] = True
        if cap is None:
            cls["unbounded"] = True
            report[key] = cls
            continue
        if not d.is_dir():
            report[key] = cls  # nothing to reap (dir not created yet)
            continue
        # Gather (identifier, mtime) at the I/O boundary; the identifier is the
        # entry NAME (unique within the dir), mtime drives recency.
        entries: list[tuple[str, float]] = []
        by_name: dict[str, os.DirEntry] = {}
        with os.scandir(d) as it:
            for e in it:
                if not pred(e):
                    continue
                try:
                    mtime = e.stat().st_mtime
                except OSError:
                    continue  # vanished mid-scan; skip
                entries.append((e.name, mtime))
                by_name[e.name] = e
        drop = _retention.plan_reap(entries, cap)
        cls["kept"] = len(entries) - len(drop)
        for name in sorted(drop):
            cls["dropped"].append(name)
            if apply:
                _reap_one(Path(by_name[name].path), is_dir=by_name[name].is_dir())
        report[key] = cls
    report["_applied"] = apply
    return report


def _reap_one(path: Path, *, is_dir: bool) -> None:
    """Delete one scratch entry (a file, or a run-dir tree). Best-effort: a
    permission/race error on one entry never aborts the sweep — it is logged by its
    ABSENCE from a later report, and the next sweep retries it."""
    import shutil
    try:
        if is_dir:
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass
