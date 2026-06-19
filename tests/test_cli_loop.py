"""`dos loop` — the supervisor verb: emit or enact a spawn/reap/flag plan (SUP).

Subprocess-driven (the `test_doctor_json.py` idiom): invoke `python -m dos.cli`
with `PYTHONPATH` pointing at the installed `dos`, capture stdout, parse. The
clock is pinned with `--now-ms` so every assertion is deterministic. By default
`dos loop` is EMIT-ONLY — it never spawns a process or writes the journal. The
explicit `--enact` path delegates to `dos.drivers.supervisor.run`, tested
in-process with that driver monkeypatched so no real worker is launched.

The two load-bearing cases:
  * the generic `main`/`global` roster admits ONE worker (both lanes are
    universal `**/*`), so `--target 3` is off-target with admissible 1;
  * a `dos.toml` declaring THREE pairwise-disjoint concurrent lanes makes
    `--target 3` reachable → FILLING with three SPAWN plans (the benchmark case).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import dos


def _cli(repo: Path, *argv: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(Path(dos.__file__).parents[1])}
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", "loop", *argv, "--workspace", str(repo)],
        capture_output=True, text=True, env=env,
    )


def _write_toml(repo: Path, body: str) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "dos.toml").write_text(body, encoding="utf-8")


def test_loop_json_generic_roster_target_3(tmp_path: Path):
    """A fresh workspace with no journal and the generic `main`/`global` roster:
    `dos loop --target 3 --json` parses to a well-formed verdict. The generic
    default admits one worker (both lanes are universal `**/*`), so target 3 is
    TARGET_UNREACHABLE with admissible 1 — and the plan still fills to admissible
    (one spawn on the concurrent lane)."""
    proc = _cli(tmp_path, "--target", "3", "--now-ms", "1000", "--json")
    assert proc.returncode == 0, proc.stderr
    v = json.loads(proc.stdout)
    assert v["verdict"] == "TARGET_UNREACHABLE"
    assert v["admissible"] == 1
    assert v["target"] == 3
    # the generic roster reached the verdict (main concurrent + global exclusive)
    lanes = {ln["lane"] for ln in v["evidence"]["lanes"]}
    assert lanes == {"main", "global"}
    # nothing is alive in a fresh repo, and the plan fills to admissible (1 spawn)
    assert v["alive"] == 0
    assert len(v["spawn"]) == 1


def test_loop_text_renders_with_header(tmp_path: Path):
    """The default (text) output renders without crashing and LEADS with the
    SUPERVISE header line (so `--watch` shows a changing top line)."""
    proc = _cli(tmp_path, "--target", "3", "--now-ms", "1000")
    assert proc.returncode == 0, proc.stderr
    first = proc.stdout.splitlines()[0]
    assert first.startswith("SUPERVISE ")
    assert "alive 0/3" in first
    assert "admissible 1" in first


def test_loop_now_ms_is_deterministic(tmp_path: Path):
    """`--now-ms` pins the clock: two invocations with the same value produce
    byte-identical JSON (no wall-clock leak into the verdict)."""
    a = _cli(tmp_path, "--target", "1", "--now-ms", "424242", "--json")
    b = _cli(tmp_path, "--target", "1", "--now-ms", "424242", "--json")
    assert a.returncode == 0 and b.returncode == 0, (a.stderr, b.stderr)
    assert a.stdout == b.stdout


def test_loop_three_disjoint_lanes_fills_to_three(tmp_path: Path):
    """The benchmark case: a `dos.toml` declaring THREE pairwise-disjoint
    concurrent lanes makes `--target 3` reachable → FILLING with three SPAWN
    plans, one per lane. Proves admissible tracks the declared region-locks."""
    _write_toml(
        tmp_path,
        "[lanes]\nconcurrent=['api','worker','web']\nexclusive=['infra']\n"
        "autopick=['api']\n"
        "[lanes.trees]\napi=['src/api/**']\nworker=['src/worker/**']\n"
        "web=['src/web/**']\ninfra=['deploy/**']\n",
    )
    proc = _cli(tmp_path, "--target", "3", "--now-ms", "1000", "--json")
    assert proc.returncode == 0, proc.stderr
    v = json.loads(proc.stdout)
    assert v["verdict"] == "FILLING"
    assert v["admissible"] == 3
    assert v["alive"] == 0
    spawned = sorted(p["lane"] for p in v["spawn"])
    assert spawned == ["api", "web", "worker"]
    assert all(p["disposition"] == "SPAWN" for p in v["spawn"])


def test_loop_three_disjoint_lanes_text_emits_generic_command(tmp_path: Path):
    """In text mode the three-lane case emits the generic, host-FREE AND
    vendor-FREE worker launch command line — the kernel names NO host and NO
    vendor. The default `[supervise].worker_launch_template` is the bare
    `/dos-dispatch-loop --lane {lane}` skill invocation; a host that wants a
    `claude -p "…"` wrapper declares it in its own dos.toml."""
    _write_toml(
        tmp_path,
        "[lanes]\nconcurrent=['api','worker','web']\nexclusive=['infra']\n"
        "autopick=['api']\n"
        "[lanes.trees]\napi=['src/api/**']\nworker=['src/worker/**']\n"
        "web=['src/web/**']\ninfra=['deploy/**']\n",
    )
    proc = _cli(tmp_path, "--target", "3", "--now-ms", "1000")
    assert proc.returncode == 0, proc.stderr
    assert "run: /dos-dispatch-loop --lane api" in proc.stdout
    # no host directory / job lane / job commit prefix — and no VENDOR binary —
    # leaks into the emission (the kernel emits the neutral skill, not `claude`).
    for forbidden in ("docs/_plans", "docs/dispatch:", "tailor", "discovery", "claude"):
        assert forbidden not in proc.stdout


def test_loop_worker_launch_template_flag_overrides_text_emission(tmp_path: Path):
    """A one-off host wrapper can be tested without editing dos.toml."""
    proc = _cli(
        tmp_path,
        "--target", "1", "--now-ms", "1000",
        "--worker-launch-template", 'codex exec "/dos-dispatch-loop --lane {lane}"',
    )

    assert proc.returncode == 0, proc.stderr
    assert 'run: codex exec "/dos-dispatch-loop --lane main"' in proc.stdout


# ---------------------------------------------------------------------------
# The [supervise] config seam (docs/99): the standing target comes from
# dos.toml; --target overrides it for a one-off run. Before the seam --target
# was the only lever and defaulted to 1; now an undeclared target still defaults
# to 1, a declared one stands, and an explicit flag wins.
# ---------------------------------------------------------------------------
def test_loop_reads_config_target_without_flag(tmp_path: Path):
    """With THREE disjoint lanes AND `[supervise] target = 3` declared, `dos loop`
    with NO `--target` flag reads the config target → FILLING with three spawns.
    The standing population is declared once in dos.toml, not re-passed per run."""
    _write_toml(
        tmp_path,
        "[lanes]\nconcurrent=['api','worker','web']\nexclusive=['infra']\n"
        "[lanes.trees]\napi=['src/api/**']\nworker=['src/worker/**']\n"
        "web=['src/web/**']\ninfra=['deploy/**']\n"
        "[supervise]\ntarget = 3\n",
    )
    proc = _cli(tmp_path, "--now-ms", "1000", "--json")  # NO --target
    assert proc.returncode == 0, proc.stderr
    v = json.loads(proc.stdout)
    assert v["target"] == 3, "the standing [supervise] target must be read"
    assert v["verdict"] == "FILLING"
    assert sorted(p["lane"] for p in v["spawn"]) == ["api", "web", "worker"]


def test_loop_target_flag_overrides_config(tmp_path: Path):
    """An explicit `--target 1` overrides the declared `[supervise] target = 3`
    for this run (a one-off smaller population), filling just one lane."""
    _write_toml(
        tmp_path,
        "[lanes]\nconcurrent=['api','worker','web']\nexclusive=['infra']\n"
        "[lanes.trees]\napi=['src/api/**']\nworker=['src/worker/**']\n"
        "web=['src/web/**']\ninfra=['deploy/**']\n"
        "[supervise]\ntarget = 3\n",
    )
    proc = _cli(tmp_path, "--target", "1", "--now-ms", "1000", "--json")
    assert proc.returncode == 0, proc.stderr
    v = json.loads(proc.stdout)
    assert v["target"] == 1, "an explicit --target must override the config target"
    assert len(v["spawn"]) == 1


def test_loop_without_enact_does_not_call_supervisor_driver(tmp_path, monkeypatch):
    """The default CLI surface stays emit-only: it prints the plan and does not
    call the effectful supervisor driver."""
    from dos import cli
    from dos.drivers import supervisor

    def _boom(**kwargs):
        raise AssertionError("plain `dos loop` must not enact the plan")

    monkeypatch.setattr(supervisor, "run", _boom)

    rc = cli.main(["loop", "--workspace", str(tmp_path), "--target", "1", "--now-ms", "1000"])

    assert rc == 0


def test_loop_enact_delegates_to_supervisor_driver(tmp_path, monkeypatch):
    """`--enact` is the opt-in handoff from the plan emitter to the long-lived
    driver: the effective policy is layered first, then handed to the driver."""
    from dos import cli
    from dos.drivers import supervisor

    _write_toml(
        tmp_path,
        "[supervise]\n"
        "target = 2\n"
        "max_concurrency = 7\n"
        "worker_launch_template = 'codex exec \"/dos-dispatch-loop --lane {lane}\"'\n",
    )
    captured = {}

    def _fake_run(*, config, target, interval, max_ticks, clock_ms):
        captured.update(
            config=config,
            target=target,
            interval=interval,
            max_ticks=max_ticks,
            clock_ms=clock_ms,
        )
        return 0

    monkeypatch.setattr(supervisor, "run", _fake_run)

    rc = cli.main([
        "loop", "--workspace", str(tmp_path), "--enact",
        "--target", "3", "--max-concurrency", "5",
        "--worker-launch-template", 'codex exec "/dos-dispatch-loop --lane {lane}"',
        "--max-ticks", "1", "--interval", "0", "--now-ms", "123",
    ])

    assert rc == 0
    assert captured["target"] == 3
    assert captured["interval"] == 0
    assert captured["max_ticks"] == 1
    assert captured["clock_ms"]() == 123
    policy = captured["config"].supervise
    assert policy.target == 3
    assert policy.max_concurrency == 5
    assert policy.worker_launch_template == 'codex exec "/dos-dispatch-loop --lane {lane}"'



# ---------------------------------------------------------------------------
# The _supervise_evidence boundary wiring for acting-on-spin (docs/210 pivot):
# a SPINNING lane's LaneLiveness must carry spinning_age_ms = the same heartbeat
# age liveness.classify consumed (zero new I/O). Tested at the boundary helper so
# we prove the WIRING (not just the pure verdict): classify says SPINNING -> the
# evidence carries the age; classify says ADVANCING -> the age is None.
# ---------------------------------------------------------------------------
def test_supervise_evidence_populates_spinning_age_for_a_spinner(tmp_path, monkeypatch):
    import dos.config as _config
    from dos import cli, lane_journal, liveness

    cfg = _config.default_config(tmp_path)
    # Seed a live lease so the boundary takes the held-lease branch.
    lease = {
        "lane": "main", "lane_kind": "concurrent", "tree": ("**/*",),
        "loop_ts": "2026-06-01T10:00Z", "host_id": "h", "pid": 1,
        "acquired_at": "2026-06-01T10:00:00Z",
    }
    lane_journal.append(lane_journal.acquire_entry(lease), path=cfg.paths.lane_journal)

    # Force the journal fold to report a known heartbeat age, and classify SPINNING.
    class _JD:
        events_since_start = 0
        newest_heartbeat_age_ms = 123_456

    monkeypatch.setattr(cli, "_journal_delta",
                        lambda cfg, *, started_ms, now_ms, lease_key: _JD())

    class _V:
        verdict = liveness.Liveness.SPINNING

    monkeypatch.setattr(liveness, "classify", lambda ev, *a, **k: _V())

    ev = cli._supervise_evidence(cfg, target=1, now_ms=10**12)
    main = next(ln for ln in ev.lanes if ln.lane == "main")
    assert main.liveness == liveness.Liveness.SPINNING
    # spinning_age_ms is the heartbeat age the fold reported (reused, no new I/O).
    assert main.spinning_age_ms == 123_456


def test_supervise_evidence_no_spin_age_for_advancing(tmp_path, monkeypatch):
    import dos.config as _config
    from dos import cli, lane_journal, liveness

    cfg = _config.default_config(tmp_path)
    lease = {
        "lane": "main", "lane_kind": "concurrent", "tree": ("**/*",),
        "loop_ts": "2026-06-01T10:00Z", "host_id": "h", "pid": 1,
        "acquired_at": "2026-06-01T10:00:00Z",
    }
    lane_journal.append(lane_journal.acquire_entry(lease), path=cfg.paths.lane_journal)

    class _JD:
        events_since_start = 5
        newest_heartbeat_age_ms = 1000

    monkeypatch.setattr(cli, "_journal_delta",
                        lambda cfg, *, started_ms, now_ms, lease_key: _JD())

    class _V:
        verdict = liveness.Liveness.ADVANCING

    monkeypatch.setattr(liveness, "classify", lambda ev, *a, **k: _V())

    ev = cli._supervise_evidence(cfg, target=1, now_ms=10**12)
    main = next(ln for ln in ev.lanes if ln.lane == "main")
    assert main.liveness == liveness.Liveness.ADVANCING
    # An ADVANCING lane carries NO spin age — it can never trip PROPOSE_HALT.
    assert main.spinning_age_ms is None
