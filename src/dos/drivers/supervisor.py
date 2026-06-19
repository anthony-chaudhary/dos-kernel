"""dos.drivers.supervisor — the long-lived watchdog that ENACTS `supervise()`.

The supervisor verdict (`dos.supervise`, docs/99) is a PURE per-tick plan:
SPAWN these free lanes, REAP these STALLED leases, FLAG these spinners. The
kernel emits the plan and stops there — `dos loop` prints it, it never launches a
worker or writes the journal. This driver is the layer that *acts on* the plan:
each tick it gathers the evidence (reusing the kernel boundary helper
`cli._supervise_evidence`), calls the pure verdict, then turns the plan into
effects — `subprocess.Popen` a worker dispatch-loop per SPAWN, append a SCAVENGE
to the lane journal per REAP.

It is a **driver** (layer 4): the one place where subprocess + journal-write +
policy live. The kernel never imports it (the `import dos.drivers` litmus); it
`import dos` like any consumer. It is the population-axis analogue of the loop
*screenplay* a host builds over `liveness` — the kernel ships the verdict, the
driver puts it on a cadence and gives it hands.

## Why a driver may write the journal (and must serialize)

`lane_journal.append` is deliberately lock-free: "journal order must equal
registry-mutation order and only the caller knows the surrounding critical
section." Today the kernel ships no in-tree writer; this driver is the first.
So it brings its own serialization — a single `O_CREAT|O_EXCL` lock file next to
the journal, held only across the append. The supervisor is single-writer-per-host
by design, so the lock serializes the supervisor's OWN appends; it does NOT (and
need not) coordinate with a worker's `lane_journal.append` ACQUIRE, which stays
lock-free — `seq` is cosmetic for `replay` (it folds by append order and ignores
`seq`), so an ACQUIRE/SCAVENGE seq-collision is benign. The lock's real job is
**crash-safety**: a supervisor killed mid-append (SIGKILL / OOM / power-loss on
this multi-day watchdog) must not wedge every future reap. So, like
`archive_lock`, it STEALS a lock older than a short TTL, and `run()` clears any
pre-existing lock once at startup (safe: single-writer-per-host).

## The double-spawn race belt (the driver half of the kernel guard)

Between the tick that `Popen`s a worker and the tick where that worker's ACQUIRE
lands in the journal, the lane reads FREE — so a naive re-tick would launch a
second worker. The driver keeps a `launched: {lane: launched_at_ms}` set and, on
the next tick, marks every lane launched within `cooldown_ms` as `pending=True`
in the evidence. The pure verdict then counts it alive-or-coming and does not
re-emit a SPAWN for it (the kernel's `pending` guard). The belt bounds the race
to at most one extra worker per lane per cooldown window — never an unbounded
stampede. A lane drops out of `launched` once its lease is visible (its ACQUIRE
journalled), so a worker that came up healthy stops being treated as pending.

## Structure (testable without real I/O)

`plan_tick(cfg, *, target, now_ms, launched, cooldown_ms)` is near-pure: it
derives `pending` from `launched`, gathers evidence, calls `supervise()`, and
returns the verdict — NO effects. `tick(...)` calls `plan_tick` and then performs
the effects (Popen + scavenge), returning `(verdict, actions)`. `run(...)` loops
`tick` + sleep. Tests drive `plan_tick`/`tick` with `subprocess.Popen` and
`lane_journal.append` monkeypatched, so no real `claude` and no real git run.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dos import config as _config
from dos import lane_journal, run_id, supervise

# The worker launch argv the SPAWN plan turns into. The template is workspace
# policy (`dos.toml [supervise].worker_launch_template`), so the enacting driver
# can run under Claude, Codex, a local harness, or a shell wrapper without this
# module naming a host binary.
WORKER_PROCESS_ID = "PROC-dos-dispatch-loop"
DEFAULT_INTERVAL_S = 300.0       # the watchdog wakes rarely — init's reaper cadence
DEFAULT_COOLDOWN_MS = 120_000    # ~2 min: covers a worker's cold-start + first ACQUIRE


def _worker_argv(template: str, lane: str) -> list[str]:
    """Render the configured worker launch template into argv for `lane`.

    The template is validated by `SupervisePolicy` to contain `{lane}`. Splitting
    with `shlex` keeps the common host declarations (`claude -p "... {lane}"`,
    `codex exec "... {lane}"`, wrapper scripts) as argv without routing through a
    shell. On Windows we use non-POSIX tokenization so `C:\\...` paths keep their
    backslashes, then trim wrapper quotes from grouped arguments. The default bare
    skill template becomes `["/dos-dispatch-loop", "--lane", lane]`; a host that
    needs a real runtime declares that wrapper in `dos.toml`.
    """
    parts = shlex.split(template.format(lane=lane), posix=(os.name != "nt"))
    if os.name != "nt":
        return parts
    return [
        part[1:-1]
        if len(part) >= 2 and part[0] == part[-1] and part[0] in ("'", '"')
        else part
        for part in parts
    ]


# --------------------------------------------------------------------------
# Journal write-lock — a dedicated O_CREAT|O_EXCL lock file next to the journal,
# held only across an append. The supervisor is single-writer-per-host by design,
# so this lock serializes the supervisor's OWN appends (it does NOT, and need not,
# coordinate with a worker's `lane_journal.append` ACQUIRE — that path is lock-free
# and `seq` is cosmetic for `replay`, which folds by append order and ignores it).
# Its real job is crash-safety: it MUST recover from a stale lock a crashed
# supervisor (SIGKILL / OOM / power-loss) left behind, or every future reap wedges
# forever. So, like `archive_lock`, it STEALS a lock older than a short TTL — the
# append is sub-second, so a few seconds is ample — and `run()` clears any
# pre-existing lock once at startup (safe: single-writer-per-host).
# --------------------------------------------------------------------------
_LOCK_TTL_S = 10.0  # an append is sub-second; a lock older than this is a crash orphan


def _journal_lock_path(cfg) -> Path:
    return Path(str(cfg.paths.lane_journal) + ".supervisor.lock")


def _lock_age_s(lp: Path) -> "float | None":
    """Age of the lock file in seconds by its mtime; None if it cannot be read."""
    try:
        return max(0.0, time.time() - lp.stat().st_mtime)
    except OSError:
        return None


def _clear_stale_lock(cfg) -> None:
    """Unlink the journal write-lock if it exists (startup cleanup / steal helper).

    Safe because the supervisor is single-writer-per-host: at `run()` startup there
    is no other legitimate holder, so any lock present is a crash orphan from a
    prior run. Also used to STEAL a lock older than the TTL mid-run.
    """
    lp = _journal_lock_path(cfg)
    try:
        lp.unlink()
    except OSError:
        pass


def _scavenge_under_lock(cfg, lease: dict, *, reason: str) -> bool:
    """Append a SCAVENGE for `lease` to the lane journal under a write-lock.

    Returns True on a clean append, False if a FRESH lock was held (the supervisor
    is mid-append elsewhere — skip this tick, the next one retries) or the append
    failed. A failed reap is never fatal: the lane stays STALLED and the next tick
    re-emits the REAP, the idempotent-reconcile property.

    Crash-safety: a lock older than `_LOCK_TTL_S` is a crash orphan (a real append
    is sub-second), so it is STOLEN — unlinked and re-created — rather than
    deferred forever. Without this, a supervisor killed mid-append would wedge
    every future reap for the life of the host.
    """
    lp = _journal_lock_path(cfg)
    lp.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lp), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        # A lock is present. If it is older than the TTL it is a crash orphan —
        # steal it and retry once. A fresh lock means a real concurrent append
        # (only possible if someone ran two supervisors); defer to the next tick.
        age = _lock_age_s(lp)
        if age is None or age <= _LOCK_TTL_S:
            return False
        _clear_stale_lock(cfg)
        try:
            fd = os.open(str(lp), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except OSError:
            return False  # lost the steal race — retry next tick
    except OSError:
        return False
    try:
        os.write(fd, f"supervisor pid={os.getpid()}\n".encode("utf-8"))
        os.close(fd)
        entry = lane_journal.scavenge_entry(lease, reason=reason,
                                            prev_holder=lease.get("host_id"))
        lane_journal.append(entry, path=cfg.paths.lane_journal)
        return True
    except Exception:  # noqa: BLE001 — a failed reap is non-fatal; retry next tick
        return False
    finally:
        try:
            lp.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------
# The tick — plan (near-pure) then enact (effects).
# --------------------------------------------------------------------------
@dataclass
class LaunchFailure:
    """A spawn the supervisor attempted but the host launcher rejected."""

    lane: str
    error: str

    def to_dict(self) -> dict:
        return {"lane": self.lane, "error": self.error}


@dataclass
class TickActions:
    """What a tick actually did — the audit record a test asserts on."""

    spawned: list[str] = field(default_factory=list)   # lanes a worker was Popen'd for
    reaped: list[str] = field(default_factory=list)     # lanes a SCAVENGE was appended for
    flagged: list[str] = field(default_factory=list)    # lanes surfaced (advisory)
    skipped_reaps: list[str] = field(default_factory=list)  # REAPs the lock deferred
    failed_spawns: list[LaunchFailure] = field(default_factory=list)
    # Lanes a *proposed* halt was surfaced for (acting-on-spin, docs/90 §5). PURELY
    # ADVISORY: the driver surfaces the proposal exactly as it surfaces `flagged` —
    # it Popens nothing, writes NO OP_RELEASE / OP_SCAVENGE, kills no process. A
    # spinner whose halt is proposed STILL holds its lease; actuation is the
    # operator's explicit `dos halt`, never the supervisor's (the docs/99 floor).
    proposed_halts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "spawned": list(self.spawned),
            "reaped": list(self.reaped),
            "flagged": list(self.flagged),
            "skipped_reaps": list(self.skipped_reaps),
            "failed_spawns": [f.to_dict() for f in self.failed_spawns],
            "proposed_halts": list(self.proposed_halts),
        }


def _pending_from_launched(launched: dict, *, now_ms: int, cooldown_ms: int) -> frozenset:
    """Lanes launched within the cooldown window — the race belt's `pending` set."""
    return frozenset(
        lane for lane, ts in launched.items() if now_ms - ts < cooldown_ms
    )


def plan_tick(cfg, *, target, now_ms, launched, cooldown_ms=DEFAULT_COOLDOWN_MS):
    """Gather evidence (with the pending race-belt) and return the PURE verdict.

    No effects — this is the testable seam. `launched` is the driver's
    {lane: launched_at_ms} set; lanes inside the cooldown window are marked
    `pending` so the verdict does not re-spawn a worker whose ACQUIRE has not yet
    journalled. Imports `cli._supervise_evidence` so SUP and `dos loop` gather
    through the SAME boundary code.

    The population POLICY is the workspace's `dos.toml [supervise]` declaration
    (`cfg.supervise`: count_spinning_as_alive + reap_stalled), with `target`
    overridden by the driver's effective target for this run — the same
    config-sourced policy the `dos loop` emitter uses, so the watchdog and the
    hand-run emitter can never diverge on whether a spinner counts as up or the
    dead are reaped.
    """
    import dataclasses

    from dos import cli  # consumer→consumer import (driver may import the CLI)

    policy = dataclasses.replace(cfg.supervise, target=target)
    pending = _pending_from_launched(launched, now_ms=now_ms, cooldown_ms=cooldown_ms)
    ev = cli._supervise_evidence(
        cfg, target=target, now_ms=now_ms, pending_lanes=pending, policy=policy
    )
    return supervise.supervise(ev, policy)


def tick(
    cfg,
    *,
    target,
    now_ms,
    launched,
    root_run=None,
    cooldown_ms=DEFAULT_COOLDOWN_MS,
    popen=subprocess.Popen,
):
    """One supervise tick: plan, then enact (Popen spawns + scavenge reaps).

    Mutates `launched` in place (records each spawn's launch ms; drops a lane once
    its lease is visible so it stops being treated as pending). `popen` is
    injectable so tests record launches without a real subprocess. Returns
    `(verdict, TickActions)`.
    """
    verdict = plan_tick(cfg, target=target, now_ms=now_ms, launched=launched,
                        cooldown_ms=cooldown_ms)
    actions = TickActions()

    # Reap first (free the dead lanes' journal state before refilling). Look up the
    # live lease dict to pass the real (loop_ts, lane) identity to scavenge_entry.
    live = _live_leases_by_lane(cfg)
    for plan in verdict.reap:
        lease = live.get(plan.lane) or {"lane": plan.lane}
        if _scavenge_under_lock(cfg, lease, reason="supervisor: STALLED"):
            actions.reaped.append(plan.lane)
            launched.pop(plan.lane, None)  # a reaped lane is no longer in-flight
        else:
            actions.skipped_reaps.append(plan.lane)

    # Spawn the free admissible lanes the plan named. Each worker gets its OWN
    # run-id minted as a CHILD of the supervisor root (process-id WORKER_PROCESS_ID),
    # so the correlation spine records "this dispatch-loop was launched by this
    # supervisor" across the configured worker boundary via the CID_* lineage env.
    for plan in verdict.spawn:
        env = dict(os.environ)
        env[_config.ENV_WORKSPACE] = str(cfg.paths.root)
        if root_run is not None:
            child = run_id.mint(WORKER_PROCESS_ID, parent=root_run)
            env.update(run_id.lineage_env(child))
        try:
            popen(
                _worker_argv(cfg.supervise.worker_launch_template, plan.lane),
                env=env,
                cwd=str(cfg.paths.root),
            )
            launched[plan.lane] = now_ms
            actions.spawned.append(plan.lane)
        except Exception as exc:  # noqa: BLE001 — retry next tick; report this one
            actions.failed_spawns.append(
                LaunchFailure(plan.lane, f"{type(exc).__name__}: {exc}")
            )

    actions.flagged = [p.lane for p in verdict.flag]

    # Acting-on-spin (docs/90 §5): surface the *proposed* halts, advisory-only.
    # CRITICAL: this is a SURFACE, not an actuation — we record the lanes and do
    # NOT Popen, NOT scavenge, NOT release a lease. A proposed halt of a live
    # spinner stays the operator's to enact (`dos halt`); the supervisor never
    # kills a live worker (the docs/99 PDP-not-PEP floor). Note we read the
    # SEPARATE `verdict.proposed_halt` tuple, never `verdict.reap` — so a proposal
    # can never flow into the reap/scavenge path above.
    actions.proposed_halts = [p.lane for p in verdict.proposed_halt]

    # Housekeeping: a lane whose lease is now visible (ACQUIRE journalled) is no
    # longer in-flight — drop it from `launched` so it stops counting as pending.
    for lane in list(launched):
        if lane in live:
            launched.pop(lane, None)

    return verdict, actions


def _live_leases_by_lane(cfg: _config.SubstrateConfig) -> dict:
    """The current live leases keyed by lane (read-only; [] on a missing journal)."""
    try:
        entries = lane_journal.read_all(path=cfg.paths.lane_journal)
        leases = lane_journal.replay(entries)
    except Exception:  # noqa: BLE001
        return {}
    return {str(l.get("lane") or ""): l for l in leases}


def run(
    config=None,
    *,
    target: Optional[int] = None,
    interval: float = DEFAULT_INTERVAL_S,
    max_ticks: Optional[int] = None,
    cooldown_ms: int = DEFAULT_COOLDOWN_MS,
    clock_ms=None,
    sleep=time.sleep,
    popen=subprocess.Popen,
    on_tick=None,
) -> int:
    """Run the supervisor watchdog until `max_ticks` or an operator interrupt.

    Mints a root run-id (`PROC-dos-supervise`) so every worker it launches carries
    the supervisor's lineage across the `claude -p` boundary (the correlation
    spine). Each tick gathers + plans + enacts, then sleeps `interval` (long — a
    watchdog, not a busy-poll). `clock_ms`/`sleep`/`popen` are injectable for
    deterministic tests. Returns 0 on a clean stop.

    `on_tick`, when provided, receives `(verdict, actions)` after each enacted
    tick so CLI/hosts can report what happened without duplicating the driver
    loop. `target` defaults to the workspace's standing `dos.toml [supervise]`
    target (`cfg.supervise.target`) so a watchdog launched with no explicit
    population keeps the declared one; pass an int to override it for this
    process. The two booleans (count_spinning_as_alive / reap_stalled) always
    come from the config policy via `plan_tick`.
    """
    cfg = _config.ensure(config)
    if target is None:
        target = cfg.supervise.target
    # Startup crash-recovery: clear any journal write-lock a prior (crashed)
    # supervisor left behind. Safe because the supervisor is single-writer-per-host
    # — at startup there is no other legitimate holder, so a present lock is a
    # crash orphan that would otherwise wedge the first reap.
    _clear_stale_lock(cfg)
    root_run = run_id.mint("dos-supervise")
    launched: dict = {}
    ticks = 0
    had_launch_failure = False
    _clock = clock_ms if clock_ms is not None else (lambda: int(time.time() * 1000))
    try:
        while max_ticks is None or ticks < max_ticks:
            now_ms = _clock()
            verdict, actions = tick(
                cfg, target=target, now_ms=now_ms, launched=launched,
                root_run=root_run, cooldown_ms=cooldown_ms, popen=popen)
            if actions.failed_spawns:
                had_launch_failure = True
            if on_tick is not None:
                on_tick(verdict, actions)
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            sleep(interval)
    except KeyboardInterrupt:
        return 0
    return 1 if had_launch_failure else 0
