#!/usr/bin/env python3
"""The single source of truth for what a runtime *run-id* is (CID-series).

docs/64_correlation-id-spine-plan.md — CID1 (the throughline slice).

The reference userland app is saturated with IDs at the *plan/phase* altitude
(~90 series prefixes, ~230 baseline dirs, the FQ-NNN findings queue). The thin spot is
the **runtime**: a `/dispatch` / `/fanout` / `/dispatch-loop` iteration is
identified only by its UTC directory name (`docs/_fanout_runs/20260531T143451Z/`),
a string that

  - is NOT collision-safe across concurrent same-host loops (two loops can
    mint the same second — the recurring WinError5 / torn-write race), and
  - carries NO lineage (the dispatch → next-up → fanout → N×`claude -p` tree
    is reconstructed by timestamp-correlation + git-log grep, not a join).

A `RunId` fixes both without losing the one good property the bare timestamp
has — sortability:

    RID-<base32-ts-ms><sep><base32-entropy>
        └ Crockford base32 of epoch-ms (sortable)  └ (pid, monotonic_ns) tail

Lexicographic sort on the token == chronological order, so it drops straight
into the existing timestamp-named dirs. The entropy tail (derived from
`(pid, monotonic_ns)`, the same collision-safe idiom the reference userland app
uses for its stable event ids) makes two ids minted in the same
millisecond distinct.

DESIGN RULES (docs/64):
  - This module adds NO new series prefix. It mints ONE id *kind* (`run_id`)
    and carries lineage in three explicit fields (run_id / parent_id / root_id).
  - The clock and entropy source are **injectable** so tests are deterministic
    (the reference userland app bans non-deterministic time in reproducible
    paths for exactly this reason). Production callers use the module defaults.
  - Telemetry never blocks: callers wrap mint() failures and degrade to the
    bare timestamp. mint() itself never raises on normal input.

The minted `RunId.run_id` is shaped to drop directly into the reference userland
app's run-context id field (currently a bare uuid4), so the
event spine — whose `compute_event_id(run_id, ...)` already makes run_id its
first component — lights up the moment a run sets the context.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dos import _filelock

# ---------------------------------------------------------------------------
# Encoding — Crockford base32 (no I/L/O/U; case-insensitive; sortable).
# We keep our own tiny encoder rather than pull a dep; the alphabet is ordered
# so that lexicographic compare on the encoded string matches numeric order.
# ---------------------------------------------------------------------------
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # 32 symbols, ascending


def _b32(n: int, *, width: int) -> str:
    """Left-zero-padded Crockford base32 of a non-negative int.

    Fixed ``width`` keeps every id the same length so lexicographic sort over a
    batch of ids is total (a shorter encoding would sort before a longer one
    regardless of value).
    """
    if n < 0:
        raise ValueError("run-id components must be non-negative")
    out = []
    for _ in range(width):
        out.append(_CROCKFORD[n & 0x1F])
        n >>= 5
    return "".join(reversed(out))


# Crockford base32 carries 5 bits/symbol. Current epoch-ms (~1.78e12, May 2026)
# is ~41 bits, so 8 symbols (40 bits, max ~1.10e12 ms ≈ year 2004) WRAP — the
# high bit is lost and sortability breaks past that boundary. 9 symbols carry
# 45 bits (max ~3.52e13 ms ≈ year 3084), which covers epoch-ms with headroom.
# (Regression pinned by test_minted_token_validates_and_decodes.)
_TS_WIDTH = 9
# 30 bits of entropy → 6 symbols. Plenty to separate same-ms mints on one host.
_ENTROPY_WIDTH = 6
_ENTROPY_BITS = _ENTROPY_WIDTH * 5  # 30

PREFIX = "RID-"
PROCESS_PREFIX = "PROC-"


def _default_clock_ms() -> int:
    """Wall-clock epoch-ms. Injectable so tests pin a fixed instant."""
    return int(time.time() * 1000)


# A strictly-increasing in-process counter. monotonic_ns() ALONE is not enough:
# on Windows its resolution is coarse (~15 ms), so a rapid mint batch reads the
# SAME ns for thousands of calls — folding that to 30 bits then collapses the
# batch to a handful of distinct ids (the observed 12/5000 collision-safety
# failure). A per-process counter is monotonic regardless of clock resolution,
# so consecutive same-host mints are ALWAYS distinct. Lock-guarded so concurrent
# threads in one process can't read the same counter value.
_MINT_COUNTER = itertools.count()
_MINT_COUNTER_LOCK = threading.Lock()


def _next_mint_seq() -> int:
    with _MINT_COUNTER_LOCK:
        return next(_MINT_COUNTER)


def _default_entropy() -> int:
    """Per-mint entropy from ``(pid, in-process counter, monotonic_ns)`` — an
    extension of the collision-safe idiom `_stable_event_id` uses, hardened for
    coarse-resolution clocks. The in-process counter occupies the LOW bits so it
    survives the ``_ENTROPY_BITS`` fold (it is the part that guarantees two mints
    in the same wall-clock millisecond — even the same monotonic_ns tick —
    differ); pid + monotonic_ns fill the high bits to separate concurrent
    processes on one host and add wall-time variation. Folded to ``_ENTROPY_BITS``.
    """
    seq = _next_mint_seq()
    # Counter in the low bits (survives the fold and is the distinctness floor);
    # pid + monotonic_ns xored into the high bits for cross-process separation.
    raw = seq ^ ((os.getpid() << 13) ^ (time.monotonic_ns() << 7))
    return raw & ((1 << _ENTROPY_BITS) - 1)


@dataclass(frozen=True)
class RunId:
    """A minted run-id plus its lineage and the process it belongs to.

    ``run_id``   — this invocation's own sortable, collision-safe token.
    ``process_id`` — the repeatable-process slug (PROC-…), declared not minted;
                     lets "the same process across invocations" be a query.
    ``parent_id`` — the run_id that launched this one (None for a root).
    ``root_id``   — top of the tree (== run_id for a root; inherited otherwise).
    ``ts_ms``     — the epoch-ms encoded in run_id, kept for cheap reads.
    """

    run_id: str
    process_id: str
    parent_id: str | None
    root_id: str
    ts_ms: int

    def to_dict(self) -> dict:
        """The exact shape written to a run-dir's ``run.json`` (CID1)."""
        return {
            "run_id": self.run_id,
            "process_id": self.process_id,
            "parent_id": self.parent_id,
            "root_id": self.root_id,
            "ts_ms": self.ts_ms,
        }


def mint(
    process_id: str,
    *,
    parent: "RunId | str | None" = None,
    root_id: str | None = None,
    clock_ms: Callable[[], int] = _default_clock_ms,
    entropy: Callable[[], int] = _default_entropy,
) -> RunId:
    """Mint a fresh ``RunId`` for one invocation of ``process_id``.

    Lineage: pass ``parent`` (a RunId or its run_id string) for a child; the
    child inherits the parent's ``root_id`` and sets ``parent_id`` to the
    parent's run_id. A root (operator-initiated) passes no parent and becomes
    its own root. ``root_id`` may be passed explicitly when only the string is
    known (e.g. inherited from an env var across a `claude -p` boundary).

    ``clock_ms`` / ``entropy`` are injected in tests for determinism.
    """
    if not process_id:
        raise ValueError("process_id is required (e.g. 'fanout', 'dispatch-loop')")
    proc = process_id if process_id.startswith(PROCESS_PREFIX) else PROCESS_PREFIX + process_id

    ts_ms = int(clock_ms())
    token = PREFIX + _b32(ts_ms, width=_TS_WIDTH) + _b32(entropy() & ((1 << _ENTROPY_BITS) - 1), width=_ENTROPY_WIDTH)

    parent_id: str | None
    if parent is None:
        parent_id = None
    elif isinstance(parent, RunId):
        parent_id = parent.run_id
        root_id = root_id or parent.root_id
    else:
        parent_id = str(parent)

    resolved_root = root_id or token  # a root is its own root
    return RunId(
        run_id=token,
        process_id=proc,
        parent_id=parent_id,
        root_id=resolved_root,
        ts_ms=ts_ms,
    )


def is_run_id(s: str) -> bool:
    """True iff ``s`` is a structurally-valid minted run-id token."""
    if not isinstance(s, str) or not s.startswith(PREFIX):
        return False
    body = s[len(PREFIX):]
    if len(body) != _TS_WIDTH + _ENTROPY_WIDTH:
        return False
    return all(c in _CROCKFORD for c in body)


def ts_ms_of(run_id: str) -> int | None:
    """Decode the epoch-ms a run-id encodes (None if not a valid token)."""
    if not is_run_id(run_id):
        return None
    ts_part = run_id[len(PREFIX):len(PREFIX) + _TS_WIDTH]
    n = 0
    for c in ts_part:
        n = (n << 5) | _CROCKFORD.index(c)
    return n


# ---------------------------------------------------------------------------
# Lineage transport across a `claude -p` boundary (CID2/CID3 will wire this in;
# defined here so the contract lives next to the minter, not scattered).
# ---------------------------------------------------------------------------
ENV_RUN_ID = "CID_RUN_ID"
ENV_PARENT_ID = "CID_PARENT_ID"
ENV_ROOT_ID = "CID_ROOT_ID"
ENV_PROCESS_ID = "CID_PROCESS_ID"


def lineage_env(run: RunId) -> dict[str, str]:
    """The env block a parent sets so a child subprocess can inherit lineage."""
    env = {ENV_RUN_ID: run.run_id, ENV_ROOT_ID: run.root_id, ENV_PROCESS_ID: run.process_id}
    if run.parent_id:
        env[ENV_PARENT_ID] = run.parent_id
    return env


def lineage_ids(env: dict[str, str] | None = None) -> tuple[str, ...]:
    """The run-ids that identify THIS run or any of its ANCESTORS, from the env.

    A self-lease match (`pretool_sensor._find_self_lease` / `cli._resolve_self_lease`)
    must recognize a lease held by THIS run AND a lease held by an ANCESTOR — because a
    subagent inherits its parent's lineage (`lineage_env` exports `CID_ROOT_ID` /
    `CID_PARENT_ID`) but mints its OWN `CID_RUN_ID` (`supervisor.mint_child_from_env`).
    A child editing INSIDE the lane its parent leased must resolve the PARENT's lease as
    "mine" — else the held lane stays in the child's disjointness sweep and an in-lane
    child edit reads as a 100% sibling collision (issue #188). So the identity set is the
    run's own id PLUS its root and parent ids, every one of which a live lease's `run_id`
    may legitimately be.

    Ordered own → parent → root (most specific first), de-duplicated, empties dropped.
    `$DISPATCH_RUN_ID` (the older loop-spine var `CID_RUN_ID` superseded) is included for
    the same reason `_find_self_lease` already honored it on the own-id rung. PURE over
    the supplied/ambient env — no I/O, no minting.
    """
    e = env if env is not None else dict(os.environ)
    ordered = (
        e.get(ENV_RUN_ID) or "",
        e.get("DISPATCH_RUN_ID") or "",
        e.get(ENV_PARENT_ID) or "",
        e.get(ENV_ROOT_ID) or "",
    )
    seen: dict[str, None] = {}
    for rid in ordered:
        rid = str(rid).strip()
        if rid and rid not in seen:
            seen[rid] = None
    return tuple(seen)


def mint_child_from_env(
    process_id: str,
    *,
    env: dict[str, str] | None = None,
    clock_ms: Callable[[], int] = _default_clock_ms,
    entropy: Callable[[], int] = _default_entropy,
) -> RunId:
    """Mint a child run-id inheriting lineage from ``CID_*`` env vars.

    If no parent env is present (an operator-initiated root), this is a root
    mint. The parent's run_id becomes this child's ``parent_id``; the root is
    inherited from ``CID_ROOT_ID`` (falling back to the parent run_id, then to
    self for a root).
    """
    e = env if env is not None else dict(os.environ)
    parent = e.get(ENV_RUN_ID)
    root = e.get(ENV_ROOT_ID) or parent
    return mint(
        process_id,
        parent=parent,
        root_id=root,
        clock_ms=clock_ms,
        entropy=entropy,
    )


# ---------------------------------------------------------------------------
# Run-dir read-back — the CID1 query path. Resolve a run-dir → its run.json.
# ---------------------------------------------------------------------------
RUN_JSON_NAME = "run.json"


def write_run_json(run_dir: Path, run: RunId) -> Path:
    """Stamp ``run.json`` into a run-dir. Returns the path written.

    Never raises on a telemetry-only failure path the way callers expect — the
    caller wraps this; here we just do the atomic-ish write.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / RUN_JSON_NAME
    tmp = run_dir / (RUN_JSON_NAME + ".tmp")
    tmp.write_text(json.dumps(run.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    _filelock.atomic_replace(tmp, target)  # atomic on same fs; win32 rename-race hardened
    return target


def read_run_json(run_dir: Path) -> dict | None:
    """Read a run-dir's ``run.json`` (None if absent / unreadable)."""
    target = Path(run_dir) / RUN_JSON_NAME
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a corrupt stamp must not crash a read
        return None


def _cmd_mint(args: argparse.Namespace) -> int:
    run = mint(args.process, parent=args.parent, root_id=args.root)
    print(json.dumps(run.to_dict(), indent=2, sort_keys=True))
    if args.write_dir:
        path = write_run_json(Path(args.write_dir), run)
        print(f"# wrote {path}", file=sys.stderr)
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    """Resolve a run-dir → its run-id + lineage (the CID1 read-back)."""
    data = read_run_json(Path(args.dir))
    if data is None:
        print(f"no {RUN_JSON_NAME} in {args.dir}", file=sys.stderr)
        return 1
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Mint / inspect runtime run-ids (CID-series; docs/64).")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mint", help="mint a run-id (optionally stamp run.json into a dir)")
    m.add_argument("process", help="process slug, e.g. 'fanout' / 'dispatch-loop'")
    m.add_argument("--parent", default=None, help="parent run_id (for a child mint)")
    m.add_argument("--root", default=None, help="root run_id (inherited across a subprocess boundary)")
    m.add_argument("--write-dir", default=None, help="run-dir to stamp run.json into")
    m.set_defaults(func=_cmd_mint)

    s = sub.add_parser("show", help="resolve a run-dir → its run-id + lineage")
    s.add_argument("dir", help="a run-dir (e.g. docs/_fanout_runs/<ts>/)")
    s.set_defaults(func=_cmd_show)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
