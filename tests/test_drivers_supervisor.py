"""dos.drivers.supervisor — the watchdog that ENACTS the `supervise()` plan.

Hermetic: `subprocess.Popen` is replaced by a recorder, and the journal write is
exercised against a real tmp journal (no `claude`, no git). The tests drive the
`tick()` seam with crafted evidence (monkeypatching `cli._supervise_evidence`) so
the verdict's inputs are controlled exactly, then assert the EFFECTS:
  * a STALLED lane → a SCAVENGE appended to the journal (the lease evicted);
  * a FREE under-target lane → a Popen with the generic worker command;
  * a pending lane (launched within cooldown) → NO second Popen (the race belt).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from dos import config as _config
from dos import lane_journal, supervise
from dos.drivers import supervisor
from dos.liveness import Liveness


# --------------------------------------------------------------------------
# Helpers.
# --------------------------------------------------------------------------
class _Recorder:
    """A Popen stand-in that records argv + env, launches nothing."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, env=None, cwd=None):
        self.calls.append((list(argv), env, cwd))
        return object()  # a fake process handle; the driver does not wait on it

    @property
    def lanes(self):
        # the --lane <X> token from each recorded worker launch command
        out = []
        for argv, _, _ in self.calls:
            for i, part in enumerate(argv):
                if part == "--lane" and i + 1 < len(argv):
                    out.append(argv[i + 1])
                    continue
                if "--lane " in part:
                    out.append(part.rsplit("--lane ", 1)[1].strip().strip('"'))
        return out


def _cfg(tmp_path: Path):
    """A generic config pointed at a tmp workspace (the .dos/ layout)."""
    return _config.default_config(tmp_path)


def _lane(lane, lv, tree, exclusive=False, pending=False):
    return supervise.LaneLiveness(
        lane=lane, liveness=lv, tree=tuple(tree),
        is_exclusive=exclusive, pending=pending)


def _patch_evidence(monkeypatch, ev: supervise.SuperviseEvidence):
    """Force cli._supervise_evidence to return crafted evidence (control the plan)."""
    from dos import cli
    monkeypatch.setattr(cli, "_supervise_evidence",
                        lambda cfg, *, target, now_ms, pending_lanes=frozenset(),
                        policy=None: ev)


# --------------------------------------------------------------------------
# SPAWN — a free under-target lane gets a worker launched.
# --------------------------------------------------------------------------
def test_tick_spawns_a_worker_for_a_free_lane(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    ev = supervise.SuperviseEvidence(
        lanes=(_lane("api", None, ("src/api/**",)),), target=1)
    _patch_evidence(monkeypatch, ev)
    rec = _Recorder()
    launched: dict = {}

    verdict, actions = supervisor.tick(
        cfg, target=1, now_ms=1000, launched=launched, popen=rec)

    assert actions.spawned == ["api"]
    assert rec.lanes == ["api"]                 # exactly one worker, on the free lane
    # the launched-set now tracks it (the race belt's memory)
    assert "api" in launched and launched["api"] == 1000
    # the emitted argv is the generic, host-free worker command from config
    argv, env, cwd = rec.calls[0]
    assert argv == ["/dos-dispatch-loop", "--lane", "api"]
    assert "claude" not in argv
    assert env[_config.ENV_WORKSPACE] == str(cfg.paths.root)
    assert cwd == str(cfg.paths.root)


def test_tick_uses_configured_worker_launch_template(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    cfg = dataclasses.replace(
        cfg,
        supervise=supervise.SupervisePolicy(
            worker_launch_template='codex exec "/dos-dispatch-loop --lane {lane}"'
        ),
    )
    ev = supervise.SuperviseEvidence(
        lanes=(_lane("api", None, ("src/api/**",)),), target=1)
    _patch_evidence(monkeypatch, ev)
    rec = _Recorder()

    supervisor.tick(cfg, target=1, now_ms=1000, launched={}, popen=rec)

    assert rec.calls[0][0] == ["codex", "exec", "/dos-dispatch-loop --lane api"]
    assert rec.lanes == ["api"]


def test_tick_records_failed_worker_launch(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    ev = supervise.SuperviseEvidence(
        lanes=(_lane("api", None, ("src/api/**",)),), target=1)
    _patch_evidence(monkeypatch, ev)
    launched: dict = {}

    def _missing_launcher(argv, env=None, cwd=None):
        raise OSError("missing host wrapper")

    verdict, actions = supervisor.tick(
        cfg, target=1, now_ms=1000, launched=launched, popen=_missing_launcher)

    assert verdict.verdict == supervise.SuperviseOutcome.FILLING
    assert actions.spawned == []
    assert launched == {}
    assert [f.lane for f in actions.failed_spawns] == ["api"]
    assert "missing host wrapper" in actions.failed_spawns[0].error
    assert actions.to_dict()["failed_spawns"] == [
        {"lane": "api", "error": actions.failed_spawns[0].error}
    ]


def test_plan_tick_passes_effective_policy_to_evidence_gather(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    cfg = dataclasses.replace(
        cfg, supervise=supervise.SupervisePolicy(target=1, max_concurrency=4)
    )
    captured = {}
    from dos import cli

    def _fake_gather(cfg, *, target, now_ms, pending_lanes=frozenset(), policy=None):
        captured["target"] = target
        captured["policy"] = policy
        return supervise.SuperviseEvidence(lanes=(), target=target)

    monkeypatch.setattr(cli, "_supervise_evidence", _fake_gather)

    supervisor.plan_tick(cfg, target=3, now_ms=1000, launched={})

    assert captured["target"] == 3
    assert captured["policy"].target == 3
    assert captured["policy"].max_concurrency == 4


# --------------------------------------------------------------------------
# The double-spawn RACE BELT — a lane launched within cooldown is not re-spawned.
# --------------------------------------------------------------------------
def test_tick_does_not_respawn_a_pending_lane(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    # The lane is still FREE in the journal (ACQUIRE not yet landed) BUT it was
    # launched 1s ago — inside the cooldown — so the gather marks it pending and
    # the verdict must NOT re-emit a SPAWN for it.
    from dos import cli

    captured = {}

    def _fake_gather(cfg, *, target, now_ms, pending_lanes=frozenset(), policy=None):
        captured["pending"] = pending_lanes
        lv = None  # FREE in the journal
        return supervise.SuperviseEvidence(
            lanes=(supervise.LaneLiveness(
                lane="api", liveness=lv, tree=("src/api/**",),
                pending=("api" in pending_lanes)),),
            target=target)

    monkeypatch.setattr(cli, "_supervise_evidence", _fake_gather)
    rec = _Recorder()
    launched = {"api": 1000}                     # launched at t=1000

    # Next tick at t=1500 (within the 120s cooldown) → api is pending → no respawn.
    verdict, actions = supervisor.tick(
        cfg, target=1, now_ms=1500, launched=launched, popen=rec,
        cooldown_ms=supervisor.DEFAULT_COOLDOWN_MS)

    assert "api" in captured["pending"]          # the belt marked it pending
    assert actions.spawned == []                 # NOT re-spawned
    assert rec.calls == []
    # pending counts toward alive, so the roster is AT_TARGET, not FILLING
    assert verdict.verdict == supervise.SuperviseOutcome.AT_TARGET


# --------------------------------------------------------------------------
# REAP — a STALLED lane gets a SCAVENGE appended to the real journal (evicted).
# --------------------------------------------------------------------------
def test_tick_scavenges_a_stalled_lease_to_the_journal(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    # Seed the real journal with a live ACQUIRE for the lane we'll mark STALLED, so
    # the driver can look up the real lease dict and the eviction is observable via
    # replay.
    lease = {
        "lane": "api", "lane_kind": "concurrent", "tree": ("src/api/**",),
        "loop_ts": "2026-06-01T10:00Z", "host_id": "host-a", "pid": 999,
        "acquired_at": "2026-06-01T10:00:00Z",
    }
    lane_journal.append(lane_journal.acquire_entry(lease), path=cfg.paths.lane_journal)
    assert len(lane_journal.replay(
        lane_journal.read_all(path=cfg.paths.lane_journal))) == 1

    # Craft a verdict that REAPs the api lane (STALLED) — target 0 so no refill.
    ev = supervise.SuperviseEvidence(
        lanes=(_lane("api", Liveness.STALLED, ("src/api/**",)),), target=0)
    _patch_evidence(monkeypatch, ev)
    rec = _Recorder()

    verdict, actions = supervisor.tick(
        cfg, target=0, now_ms=2000, launched={}, popen=rec)

    assert actions.reaped == ["api"]
    assert rec.calls == []                       # nothing spawned (target 0)
    # The journal now folds to an EMPTY live-lease set — the lease was evicted.
    leases = lane_journal.replay(lane_journal.read_all(path=cfg.paths.lane_journal))
    assert leases == []
    # A SCAVENGE entry is on the journal.
    ops = [e.get("op") for e in lane_journal.read_all(path=cfg.paths.lane_journal)]
    assert lane_journal.OP_SCAVENGE in ops


# --------------------------------------------------------------------------
# REAP-then-REFILL in one tick — a STALLED lane is reaped AND a worker spawned.
# --------------------------------------------------------------------------
def test_tick_reaps_and_refills_in_one_tick(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    lease = {"lane": "api", "tree": ("src/api/**",),
             "loop_ts": "2026-06-01T10:00Z", "host_id": "host-a",
             "acquired_at": "2026-06-01T10:00:00Z"}
    lane_journal.append(lane_journal.acquire_entry(lease), path=cfg.paths.lane_journal)

    ev = supervise.SuperviseEvidence(
        lanes=(_lane("api", Liveness.STALLED, ("src/api/**",)),), target=1)
    _patch_evidence(monkeypatch, ev)
    rec = _Recorder()

    verdict, actions = supervisor.tick(
        cfg, target=1, now_ms=3000, launched={}, popen=rec)

    assert actions.reaped == ["api"]             # the dead lease evicted
    assert actions.spawned == ["api"]            # AND a replacement launched
    assert rec.lanes == ["api"]


# --------------------------------------------------------------------------
# run() — bounded by max_ticks, deterministic clock, no real sleep.
# --------------------------------------------------------------------------
def test_run_is_bounded_by_max_ticks(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    # A faithful gather: it HONORS pending_lanes (marks the lane pending when the
    # belt says so), exactly as the real cli._supervise_evidence does — so the
    # race belt engages across ticks and `main` is launched only ONCE.
    from dos import cli

    def _fake_gather(cfg, *, target, now_ms, pending_lanes=frozenset(), policy=None):
        return supervise.SuperviseEvidence(
            lanes=(
                supervise.LaneLiveness(
                    lane="main", liveness=None, tree=("**/*",),
                    pending=("main" in pending_lanes)),
                supervise.LaneLiveness(
                    lane="global", liveness=None, tree=("**/*",),
                    is_exclusive=True, pending=("global" in pending_lanes)),
            ),
            target=target)

    monkeypatch.setattr(cli, "_supervise_evidence", _fake_gather)
    rec = _Recorder()
    sleeps = []

    rc = supervisor.run(
        config=cfg, target=1, interval=0.0, max_ticks=3,
        clock_ms=lambda: 1000, sleep=lambda s: sleeps.append(s), popen=rec)

    assert rc == 0
    # 3 ticks; the launched-set means only the FIRST tick spawns main (then it is
    # pending within cooldown — same clock each tick), so exactly one worker is
    # launched across the run. This is the race belt working end-to-end in run().
    assert rec.lanes == ["main"]
    # max_ticks bounds the loop: it sleeps between ticks but not after the last one.
    assert len(sleeps) == 2


def test_run_reports_launch_failures_and_returns_nonzero(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    ev = supervise.SuperviseEvidence(
        lanes=(_lane("api", None, ("src/api/**",)),), target=1)
    _patch_evidence(monkeypatch, ev)
    seen = []

    def _missing_launcher(argv, env=None, cwd=None):
        raise OSError("missing host wrapper")

    rc = supervisor.run(
        config=cfg, target=1, interval=0.0, max_ticks=1,
        clock_ms=lambda: 1000, sleep=lambda s: None, popen=_missing_launcher,
        on_tick=lambda verdict, actions: seen.append(actions.to_dict()))

    assert rc == 1
    assert seen[0]["spawned"] == []
    assert seen[0]["failed_spawns"][0]["lane"] == "api"
    assert "missing host wrapper" in seen[0]["failed_spawns"][0]["error"]


# --------------------------------------------------------------------------
# CRASH-SAFETY — a stale journal write-lock must NOT wedge reaps forever.
# (The confirmed blocker from the final review: a supervisor killed mid-append
#  leaves the .supervisor.lock behind; without steal-on-stale every future reap
#  is deferred forever and dead workers never get reaped.)
# --------------------------------------------------------------------------
def test_stale_lock_is_stolen_so_reaps_are_not_wedged(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    lease = {"lane": "api", "tree": ("src/api/**",),
             "loop_ts": "2026-06-01T10:00Z", "host_id": "host-a",
             "acquired_at": "2026-06-01T10:00:00Z"}
    lane_journal.append(lane_journal.acquire_entry(lease), path=cfg.paths.lane_journal)

    # Simulate a crash that left a STALE lock behind, then age it past the TTL.
    lock = Path(str(cfg.paths.lane_journal) + ".supervisor.lock")
    lock.write_text("supervisor pid=99999\n", encoding="utf-8")
    import os as _os
    import time as _time

    old = _time.time() - (supervisor._LOCK_TTL_S + 5)
    _os.utime(lock, (old, old))

    assert supervisor._lock_age_s(lock) > supervisor._LOCK_TTL_S

    ev = supervise.SuperviseEvidence(
        lanes=(_lane("api", Liveness.STALLED, ("src/api/**",)),), target=0)
    _patch_evidence(monkeypatch, ev)

    # The stale lock is STOLEN, the reap succeeds, the lease is evicted.
    verdict, actions = supervisor.tick(
        cfg, target=0, now_ms=2000, launched={}, popen=_Recorder())
    assert actions.reaped == ["api"]
    assert lane_journal.replay(
        lane_journal.read_all(path=cfg.paths.lane_journal)) == []
    # the lock is released after the steal+append (not left behind)
    assert not lock.exists()


def test_fresh_lock_defers_the_reap(tmp_path, monkeypatch):
    """A FRESH lock (a real concurrent append) is respected — the reap defers to
    the next tick, it is NOT stolen."""
    cfg = _cfg(tmp_path)
    lease = {"lane": "api", "tree": ("src/api/**",), "loop_ts": "t", "host_id": "h",
             "acquired_at": "2026-06-01T10:00:00Z"}
    lane_journal.append(lane_journal.acquire_entry(lease), path=cfg.paths.lane_journal)
    lock = Path(str(cfg.paths.lane_journal) + ".supervisor.lock")
    lock.write_text("supervisor pid=1\n", encoding="utf-8")  # fresh (mtime = now)

    ev = supervise.SuperviseEvidence(
        lanes=(_lane("api", Liveness.STALLED, ("src/api/**",)),), target=0)
    _patch_evidence(monkeypatch, ev)
    verdict, actions = supervisor.tick(
        cfg, target=0, now_ms=2000, launched={}, popen=_Recorder())
    assert actions.reaped == []                  # deferred, not stolen
    assert actions.skipped_reaps == ["api"]
    assert lock.exists()                         # the fresh lock survived


def test_run_clears_a_stale_lock_at_startup(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    lock = Path(str(cfg.paths.lane_journal) + ".supervisor.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("supervisor pid=99999\n", encoding="utf-8")  # crash orphan
    from dos import cli
    monkeypatch.setattr(
        cli, "_supervise_evidence",
        lambda cfg, *, target, now_ms, pending_lanes=frozenset(), policy=None:
            supervise.SuperviseEvidence(lanes=(), target=target))

    supervisor.run(config=cfg, target=1, interval=0.0, max_ticks=1,
                   clock_ms=lambda: 1000, sleep=lambda s: None, popen=_Recorder())
    # startup cleanup removed the orphan lock.
    assert not lock.exists()


# --------------------------------------------------------------------------
# Layering — the kernel never imports this driver (the import litmus, restated).
# --------------------------------------------------------------------------
def test_driver_imports_kernel_not_the_other_way():
    # The driver imports kernel modules; assert the reverse — the pure verdict must
    # not IMPORT the driver (the `import dos.drivers` litmus). We check the import
    # statements, not the prose: "supervisor"/"drivers" appear in supervise.py's
    # docstring as English, which is fine — what is forbidden is an import edge.
    import ast

    src = Path(supervise.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert not any("drivers" in m for m in imported), imported
    assert not any(m == "dos.drivers.supervisor" for m in imported), imported


# --------------------------------------------------------------------------
# Acting-on-spin in the driver (docs/210 pivot): a PROPOSE_HALT is SURFACED, never
# enacted — the driver collects the lanes into actions.proposed_halts and writes
# NO OP_RELEASE / OP_SCAVENGE and Popens nothing. The advisory-floor pin.
# --------------------------------------------------------------------------
def test_tick_surfaces_proposed_halt_without_releasing_or_killing(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    # The driver's plan_tick builds its policy from cfg.supervise; bake the spin-halt
    # threshold in (the frozen config seam — replace, never setattr).
    cfg = dataclasses.replace(
        cfg, supervise=supervise.SupervisePolicy(target=1, spin_halt_after_ms=60_000))
    # Seed a live ACQUIRE for the lane we'll mark SPINNING-past-threshold, so we can
    # prove the lease SURVIVES (a proposed halt never frees a region).
    lease = {
        "lane": "api", "lane_kind": "concurrent", "tree": ("src/api/**",),
        "loop_ts": "2026-06-01T10:00Z", "host_id": "host-a", "pid": 999,
        "acquired_at": "2026-06-01T10:00:00Z",
    }
    lane_journal.append(lane_journal.acquire_entry(lease), path=cfg.paths.lane_journal)
    n_before = len(lane_journal.read_all(path=cfg.paths.lane_journal))

    # Craft evidence: api is SPINNING for 10 min; policy threshold is 1 min, so the
    # PURE verdict escalates to PROPOSE_HALT. count_spinning_as_alive keeps it at
    # target so nothing spawns/reaps.
    ev = supervise.SuperviseEvidence(
        lanes=(supervise.LaneLiveness(
            lane="api", liveness=Liveness.SPINNING, tree=("src/api/**",),
            spinning_age_ms=600_000),),
        target=1)
    _patch_evidence(monkeypatch, ev)
    rec = _Recorder()

    verdict, actions = supervisor.tick(cfg, target=1, now_ms=2000, launched={}, popen=rec)

    # The PROPOSE_HALT was surfaced...
    assert actions.proposed_halts == ["api"]
    # ...but NOTHING was spawned, reaped, or killed.
    assert actions.spawned == [] and actions.reaped == []
    assert rec.calls == []
    # ...the lease SURVIVES (a proposed halt never frees a region)...
    leases = lane_journal.replay(lane_journal.read_all(path=cfg.paths.lane_journal))
    assert [l.get("lane") for l in leases] == ["api"]
    # ...and NO OP_RELEASE / OP_SCAVENGE was written (the advisory-floor pin).
    ops = [e.get("op") for e in lane_journal.read_all(path=cfg.paths.lane_journal)]
    assert lane_journal.OP_SCAVENGE not in ops
    assert getattr(lane_journal, "OP_RELEASE", "RELEASE") not in ops
    # the journal grew by zero rows (no write happened on a PROPOSE_HALT).
    assert len(lane_journal.read_all(path=cfg.paths.lane_journal)) == n_before
