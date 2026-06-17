"""Lanes & paths are per-workspace DATA — `arbitrate`/`verify` honor a declared
`[lanes]`/`[paths]` grammar (WCR), generic-by-default, replace-on-declare.

Before WCR, `dos init` scaffolded `[lanes]` (+`[lanes.trees]`) and `[paths]` but
`cli._apply_workspace` read back ONLY `[reasons]` and `[stamp]` — so an external
repo that declared `api`/`worker` lanes still saw `main`/`global` at `arbitrate`,
and a repo whose plans lived under `planning/` couldn't say so in data. The
scaffold promised lane/path customization and silently no-op'd it.

This test pins the two ends of the WCR seam together, mirroring the SCV test's
library-AND-CLI discipline so a regression in either the pure loader
(`config.load_lanes_from_toml` / `load_paths_from_toml`) or the CLI readback
(`_apply_workspace`) is caught:

  * **Phase 1 — lanes reach the arbiter.** A `dos.toml` declaring `api`/`worker`
    trees produces an ADMIT for disjoint trees and a COLLISION for overlapping
    ones, *through the CLI path*, proving `_apply_workspace` installed them.
    A `dos.toml` with only `[reasons]` yields today's taxonomy unchanged
    (additive degradation).
  * **Phase 2 — paths override discovery.** A declared `plans_glob` changes where
    `verify`/`doctor` look; an unknown `[paths]` key fails loud.
  * **Phase 3 — precedence + the completeness rail.** A `dos.toml [lanes]` plus
    `--job` resolves to the TOML lanes (TOML wins); a lane in `concurrent` but
    absent from `[lanes.trees]` is a `dos doctor --check` finding.

Litmus for Law 1 (kernel imports no host): every declared lane here is pure
workspace data — `LaneTaxonomy.from_table` builds a value and names no job lane.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

from dos import config as _config
from dos.config import (
    LaneTaxonomy,
    PathLayout,
    WorkspaceFacts,
    default_config,
    gather_workspace_facts,
    job_config,
    load_lanes_from_toml,
    load_paths_from_toml,
)


def _write_toml(repo: Path, body: str) -> None:
    """Write a BOM-free dos.toml (PowerShell's utf8 BOM trips tomllib; the test
    runner uses Path.write_text which is BOM-free, but be explicit)."""
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "dos.toml").write_text(body, encoding="utf-8")


def _isolated_env(repo: Path) -> dict:
    """The parent env with workspace-resolution overrides STRIPPED + pinned to `repo`.

    #125: a spawned `dos.cli` resolves its workspace from `--workspace` OR a
    `DISPATCH_WORKSPACE` env var. Under a concurrent suite, a sibling fleet's
    process can leak `DISPATCH_WORKSPACE` into the inherited env, so a subprocess
    here could read/write a DIFFERENT workspace's WAL (the cross-wire that poisons a
    fresh tmp dir). Strip the env override and pin it to `repo` so `--workspace` is
    the sole, unambiguous source — a subprocess can no longer cross into another
    run's `.dos/`."""
    import os
    env = dict(os.environ)
    env.pop("DISPATCH_WORKSPACE", None)
    env["DISPATCH_WORKSPACE"] = str(repo)
    return env


def _cli(repo: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *argv, "--workspace", str(repo)],
        capture_output=True, text=True, cwd=str(repo), env=_isolated_env(repo),
    )


# A realistic foreign taxonomy: two disjoint cluster lanes + one exclusive.
_FOREIGN_LANES = """\
[lanes]
concurrent = ["api", "worker", "web"]
exclusive  = ["infra"]
autopick   = ["api", "worker"]

[lanes.trees]
api    = ["src/api/**"]
worker = ["src/worker/**"]
web    = ["web/**"]
infra  = ["deploy/**", "terraform/**"]

[lanes.aliases]
svc = "api"
"""


# ===========================================================================
# Phase 1 — [lanes] read-back (the pure loader + the CLI throughline)
# ===========================================================================


def test_lanes_from_table_builds_value_naming_no_host():
    """`LaneTaxonomy.from_table` is pure and names no job lane (Law 1 litmus)."""
    import tomllib
    table = tomllib.loads(_FOREIGN_LANES)["lanes"]
    lanes = LaneTaxonomy.from_table(table)
    assert lanes.concurrent == ("api", "worker", "web")
    assert lanes.exclusive == ("infra",)
    assert lanes.autopick == ("api", "worker")
    assert lanes.tree_for("api") == ["src/api/**"]
    assert lanes.tree_for("infra") == ["deploy/**", "terraform/**"]
    assert lanes.aliases == {"svc": "api"}
    # no job lane resurrected
    for job_lane in ("apply", "tailor", "discovery", "orchestration"):
        assert job_lane not in lanes.concurrent
        assert job_lane not in lanes.exclusive


def test_load_lanes_from_toml_absent_returns_base(tmp_path: Path):
    """Absent file / no `[lanes]` table → base taxonomy unchanged."""
    base = default_config(tmp_path).lanes
    # no dos.toml at all
    assert load_lanes_from_toml(tmp_path / "dos.toml", base=base) is base
    # a dos.toml with no [lanes] table
    _write_toml(tmp_path, "[reasons.FOO]\ncategory='OPERATOR_GATE'\n")
    assert load_lanes_from_toml(tmp_path / "dos.toml", base=base) is base


def test_load_lanes_from_toml_present_replaces(tmp_path: Path):
    """A present `[lanes]` table replaces — not merges — the base."""
    _write_toml(tmp_path, _FOREIGN_LANES)
    base = default_config(tmp_path).lanes
    lanes = load_lanes_from_toml(tmp_path / "dos.toml", base=base)
    assert set(lanes.concurrent) == {"api", "worker", "web"}
    assert "main" not in lanes.concurrent  # base's `main` is gone — replace, not merge


def test_lanes_from_toml_reaches_arbiter(tmp_path: Path):
    """North-star Phase 1: declared lanes reach `dos arbitrate` through the CLI.

    Two proofs the TOML lanes actually installed (not just that the pure loader
    works):

      1. A bare `--lane api --kind cluster` request to a FREE lane acquires using
         api's DECLARED tree (`src/api/**`) — the lease the arbiter hands back
         carries the tree the `[lanes.trees]` table named, which only happens if
         `_apply_workspace` read it back.
      2. The overlap algebra runs on the DECLARED trees: a KEYWORD request that
         supplies NO `--tree` (so the CLI must source api's tree from the
         `[lanes.trees]` table it read back — see `cli.cmd_arbitrate`'s
         `tree = cfg.lanes.tree_for(args.lane)` fallback) is REFUSED against a
         live lease whose tree overlaps api's declared tree, and ADMITTED against
         a disjoint one. Omitting `--tree` is what makes this load-bearing: with
         the lanes NOT read back, `tree_for("api")` is empty and the request would
         degrade instead of running the overlap algebra, so the assertions only
         hold when `_apply_workspace` actually installed the declared trees.
    """
    _write_toml(tmp_path, _FOREIGN_LANES)

    # (1) cluster request on a free lane acquires with the DECLARED tree.
    admit = _cli(
        tmp_path, "arbitrate", "--lane", "api", "--kind", "cluster",
        "--leases", json.dumps([{"lane": "worker", "tree": ["src/worker/**"]}]),
    )
    assert admit.returncode == 0, admit.stderr
    decision = json.loads(admit.stdout)
    assert decision["outcome"] == "acquire", decision
    assert decision["tree"] == ["src/api/**"], decision  # the declared tree, read back

    # (2a) keyword request with NO --tree → arbiter sources api's DECLARED tree
    #      (src/api/**), which OVERLAPS the live lease → refuse. Vacuous unless
    #      the [lanes.trees] table was read back.
    collide = _cli(
        tmp_path, "arbitrate", "--lane", "api", "--kind", "keyword",
        "--leases", json.dumps([{"lane": "web", "tree": ["src/api/handlers.py"]}]),
    )
    assert collide.returncode == 1, collide.stdout
    assert json.loads(collide.stdout)["outcome"] == "refuse", collide.stdout

    # (2b) same, but the live lease's tree is DISJOINT from api's declared tree
    #      → acquire (on api's read-back tree).
    ok = _cli(
        tmp_path, "arbitrate", "--lane", "api", "--kind", "keyword",
        "--leases", json.dumps([{"lane": "worker", "tree": ["src/worker/**"]}]),
    )
    assert ok.returncode == 0, ok.stderr
    okd = json.loads(ok.stdout)
    assert okd["outcome"] == "acquire", okd
    assert okd["tree"] == ["src/api/**"], okd  # acquired on the declared tree


_TWO_LANES = """\
[lanes]
concurrent = ["alpha", "beta"]
autopick   = ["alpha", "beta"]

[lanes.trees]
alpha = ["alpha/**"]
beta  = ["beta/**"]
"""


def _git_init(repo: Path) -> None:
    """A throwaway git repo so `lease-lane` can create `.dos/` and journal."""
    import subprocess as _sp
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@e.com"],
        ["git", "config", "user.name", "t"],
    ):
        _sp.run(argv, cwd=repo, check=True, capture_output=True)


def _cli_sub(repo: Path, verb: str, *argv: str) -> subprocess.CompletedProcess:
    """Like `_cli` but for a verb with SUBcommands (e.g. `lease-lane acquire`):
    `--workspace` must precede the subcommand, so it goes right after `verb`,
    not at the tail (argparse rejects a top-level flag after a subparser's args)."""
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", verb, "--workspace", str(repo), *argv],
        capture_output=True, text=True, cwd=str(repo), env=_isolated_env(repo),
    )


def test_arbitrate_default_loads_live_wal_no_double_book(tmp_path: Path, monkeypatch):
    """`dos arbitrate` with NO `--leases` loads the live lane-journal WAL, so a lease
    a sibling durably holds (`dos lease-lane acquire`) is SEEN — the headline verb
    refuses/redirects instead of double-booking against an empty world.

    Regression: before this, `cmd_arbitrate` defaulted `--leases` to `[]`, so
    `dos lease-lane acquire alpha` then `dos arbitrate --lane alpha` would happily
    re-grant `alpha` — the collision-prevention only fired if the caller hand-piped
    `lease-lane live` into `--leases`. The pure `arbiter.arbitrate` was always
    correct; this pins the CLI wiring that feeds it the durable live set by default.

    Three behaviors, all with NO explicit `--leases` (the real default path):
      1. named-but-busy `--lane alpha` → REFUSE + `free_clusters:[beta]` (the menu);
      2. bare `--lane ''`            → ACQUIRE `beta` (the auto-redirect);
      3. explicit `--leases '[]']`    → still ACQUIRE `alpha` (the override / pure
         path is preserved: the caller asserting an empty world gets one).
    """
    # Pin the WAL to a per-test path so this is HERMETIC against any shared journal
    # (every other lease-WAL test does this — `test_lane_lease`, `test_lane_adopt`,
    # `test_lane_halt`, `test_apply_gate_fence`, …). Without it the WAL resolves
    # workspace-relative, which is correct in isolation but exposes the test to a
    # concurrent dispatch loop on the same box momentarily redirecting the journal
    # (a leaked `DISPATCH_LANE_JOURNAL_PATH`/`JOB_LANE_JOURNAL_PATH`) — foreign
    # leases then leak into the live set and the "fresh world acquires" assertion
    # flakes. The override is honored identically by `arbitrate` and `lease-lane`
    # (`_journal_path` checks it first), so the default-load behavior under test is
    # exercised at the pinned path. The path matches the `_wal` poison-guard below,
    # and `_isolated_env` forwards the env to every subprocess (it strips only
    # `DISPATCH_WORKSPACE`), so all `dos` calls fold the same durable WAL.
    monkeypatch.setenv(
        "DISPATCH_LANE_JOURNAL_PATH", str(tmp_path / ".dos" / "lane-journal.jsonl"))
    _write_toml(tmp_path, _TWO_LANES)
    _git_init(tmp_path)

    # #125 legibility guard: this test asserts a FRESH workspace has an empty WAL,
    # so the first arbitrate acquires. Under a concurrent suite on Windows, a sibling
    # run's subprocess can cross-wire into this tmp dir (shared %TEMP%/pytest-of-USER
    # + a truncated-basename collision), poisoning `.dos/lane-journal.jsonl` BEFORE
    # this body runs — which then surfaces as a misleading "lane 'alpha' already
    # held" refusal. Fail LEGIBLY on a poisoned dir instead: name the pre-existing
    # WAL content so the real cause (cross-run contamination, not a kernel bug) is
    # obvious, rather than a confusing arbitrate refusal on an "empty" world.
    _wal = tmp_path / ".dos" / "lane-journal.jsonl"
    assert not _wal.exists(), (
        f"#125: tmp workspace was POISONED before the test ran — a pre-existing WAL "
        f"at {_wal} (a concurrent suite cross-wired into this tmp dir). Content:\n"
        f"{_wal.read_text(encoding='utf-8', errors='replace')[:500]}")

    # No lease yet → a fresh workspace genuinely has an empty WAL → acquire alpha.
    pre = _cli(tmp_path, "arbitrate", "--lane", "alpha")
    assert pre.returncode == 0, (
        f"#125: first arbitrate on a fresh workspace did not acquire — "
        f"stderr={pre.stderr!r} stdout={pre.stdout!r}; WAL now: "
        f"{_wal.read_text(encoding='utf-8', errors='replace')[:300] if _wal.exists() else '(absent)'}")
    assert json.loads(pre.stdout)["outcome"] == "acquire", pre.stdout

    # Durably take alpha — writes ACQUIRE to the WAL the next arbitrate must read.
    acq = _cli_sub(tmp_path, "lease-lane", "acquire", "--lane", "alpha", "--owner", "a1")
    assert acq.returncode == 0, acq.stderr
    live = _cli_sub(tmp_path, "lease-lane", "live")
    assert [l["lane"] for l in json.loads(live.stdout)] == ["alpha"], live.stdout

    # (1) named-but-busy, NO --leases → the WAL is auto-loaded → refuse + menu.
    busy = _cli(tmp_path, "arbitrate", "--lane", "alpha")
    assert busy.returncode == 1, busy.stdout  # refuse → exit 1
    bd = json.loads(busy.stdout)
    assert bd["outcome"] == "refuse", bd
    assert bd["free_clusters"] == ["beta"], bd  # the way-forward is the free menu

    # (2) bare request, NO --leases → auto-pick the free disjoint lane (the redirect).
    bare = _cli(tmp_path, "arbitrate", "--lane", "")
    assert bare.returncode == 0, bare.stderr
    brd = json.loads(bare.stdout)
    assert brd["outcome"] == "acquire" and brd["lane"] == "beta", brd
    assert brd["auto_picked"] is True, brd

    # (3) explicit --leases '[]' OVERRIDES the WAL load → empty world → acquire alpha.
    override = _cli(tmp_path, "arbitrate", "--lane", "alpha", "--leases", "[]")
    assert override.returncode == 0, override.stderr
    assert json.loads(override.stdout)["outcome"] == "acquire", override.stdout


def test_no_lanes_table_is_unchanged(tmp_path: Path):
    """A `dos.toml` with only `[reasons]` yields today's taxonomy (degradation)."""
    _write_toml(
        tmp_path,
        "[reasons.LANE_PARKED]\ncategory = 'OPERATOR_GATE'\nsummary = 'parked'\n",
    )
    proc = _cli(tmp_path, "doctor")
    assert proc.returncode == 0, proc.stderr
    # the generic default taxonomy is intact
    assert "concurrent lanes    main" in proc.stdout
    assert "exclusive lanes     global" in proc.stdout


def test_malformed_lanes_table_warns_and_falls_back(tmp_path: Path):
    """A present-but-malformed `[lanes]` warns, never crashes a non-arbitrate cmd."""
    _write_toml(tmp_path, "[lanes]\nconcurrent = 42\n")
    proc = _cli(tmp_path, "doctor")
    assert proc.returncode == 0, proc.stderr
    assert "malformed [lanes]" in proc.stderr
    # fell back to the generic default
    assert "concurrent lanes    main" in proc.stdout


# ===========================================================================
# Phase 2 — [paths] read-back (override the layout without a driver)
# ===========================================================================


def test_paths_with_overrides_pure_relative_resolves_against_root(tmp_path: Path):
    """`PathLayout.with_overrides` resolves a relative path field against root."""
    base = PathLayout.for_dos_dir(tmp_path)
    out = base.with_overrides({"plans_glob": "planning/*.md",
                               "execution_state": "planning/state.yaml"})
    assert out.plans_glob == "planning/*.md"
    assert out.execution_state == (tmp_path / "planning" / "state.yaml")
    # an unnamed field is inherited untouched
    assert out.lane_journal == base.lane_journal


def test_paths_with_overrides_absolute_kept(tmp_path: Path):
    """An absolute override path is taken as-is, not re-rooted."""
    base = PathLayout.for_dos_dir(tmp_path)
    abs_state = (tmp_path / "elsewhere" / "s.yaml").resolve()
    out = base.with_overrides({"execution_state": str(abs_state)})
    assert out.execution_state == abs_state


def test_unknown_path_key_raises(tmp_path: Path):
    """A typo'd `[paths]` key (`plnas_glob`) fails loud (host mistake surfaced)."""
    base = PathLayout.for_dos_dir(tmp_path)
    import pytest
    with pytest.raises(ValueError) as ei:
        base.with_overrides({"plnas_glob": "planning/*.md"})
    assert "plnas_glob" in str(ei.value)


def test_load_paths_from_toml_absent_returns_base(tmp_path: Path):
    """Absent file / no `[paths]` table → base layout unchanged."""
    base = default_config(tmp_path).paths
    assert load_paths_from_toml(tmp_path / "dos.toml", base=base) is base
    _write_toml(tmp_path, "[lanes]\nconcurrent=['main']\n")
    assert load_paths_from_toml(tmp_path / "dos.toml", base=base) is base


def test_paths_override_changes_plan_discovery(tmp_path: Path):
    """A declared `plans_glob` makes `doctor` report the overridden glob.

    The end-to-end Phase 2 proof: `_apply_workspace` reads `[paths]` and the
    resolved glob reaches the config the commands see.
    """
    _write_toml(
        tmp_path,
        "[lanes]\nconcurrent=['main']\nexclusive=['global']\nautopick=['main']\n"
        "[lanes.trees]\nmain=['**/*']\n"
        "[paths]\nplans_glob = 'planning/*.md'\n",
    )
    proc = _cli(tmp_path, "doctor")
    assert proc.returncode == 0, proc.stderr
    assert "plans glob          planning/*.md" in proc.stdout


def test_unknown_path_key_warns_via_cli(tmp_path: Path):
    """A typo'd `[paths]` key warns through the CLI's malformed guard."""
    _write_toml(tmp_path, "[paths]\nplnas_glob = 'planning/*.md'\n")
    proc = _cli(tmp_path, "doctor")
    assert proc.returncode == 0, proc.stderr
    assert "malformed [paths]" in proc.stderr
    assert "plnas_glob" in proc.stderr


# ===========================================================================
# Phase 3 — precedence + completeness rail
# ===========================================================================


def test_precedence_toml_over_job_flag(tmp_path: Path):
    """`--job` plus a `dos.toml [lanes]` resolves to the TOML lanes (TOML wins)."""
    _write_toml(tmp_path, _FOREIGN_LANES)
    proc = _cli(tmp_path, "doctor", "--job")
    assert proc.returncode == 0, proc.stderr
    # TOML's api/worker/web won over job's apply/tailor/discovery
    assert "concurrent lanes    api, worker, web" in proc.stdout
    assert "apply" not in proc.stdout.split("concurrent lanes")[1].split("\n")[0]


def test_doctor_check_flags_treeless_lane(tmp_path: Path):
    """A lane in `concurrent` but absent from `[lanes.trees]` is a `--check` finding.

    The lane analogue of the reason completeness rail: a lane with no tree can't
    be arbitrated (the disjointness algebra has nothing to compare).
    """
    _write_toml(
        tmp_path,
        "[lanes]\nconcurrent = ['api', 'worker']\nexclusive = []\n"
        "autopick = ['api']\n"
        "[lanes.trees]\napi = ['src/api/**']\n",  # worker declared but no tree
    )
    proc = _cli(tmp_path, "doctor", "--check")
    # The finding is reported (worker has no tree). Land-as-warning: non-zero or a
    # clearly-marked finding line — assert the lane name surfaces.
    assert "worker" in (proc.stdout + proc.stderr)
    assert "tree" in (proc.stdout + proc.stderr).lower()


def test_doctor_check_clean_when_every_lane_has_a_tree(tmp_path: Path):
    """`--check` is quiet when every concurrent/autopick lane has a tree."""
    _write_toml(tmp_path, _FOREIGN_LANES)
    proc = _cli(tmp_path, "doctor", "--check")
    assert proc.returncode == 0, proc.stderr
    # no treeless-lane finding
    assert "no tree" not in (proc.stdout + proc.stderr).lower()


def test_doctor_check_ignores_treeless_exclusive_lane(tmp_path: Path):
    """A treeless EXCLUSIVE lane is NOT a `--check` finding.

    Exclusive lanes run alone and are arbitrated on liveness — the arbiter never
    consults their tree (the disjointness algebra they're excluded from). So a
    declared exclusive lane with no `[lanes.trees]` entry is perfectly arbitrable
    and must NOT fire the treeless rail. (Regression: the rail originally scoped
    `exclusive`, which false-flagged the reference job taxonomy's treeless
    `global` and the `dos init` scaffold's own `global`.)
    """
    _write_toml(
        tmp_path,
        "[lanes]\nconcurrent = ['api']\nexclusive = ['infra']\nautopick = ['api']\n"
        "[lanes.trees]\napi = ['src/api/**']\n",  # infra exclusive, deliberately treeless
    )
    proc = _cli(tmp_path, "doctor", "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # `infra` appears in the doctor's `exclusive lanes` report line (correct), but
    # must NOT appear in a `finding:` line about a missing tree.
    finding_lines = [ln for ln in (proc.stdout + proc.stderr).splitlines()
                     if ln.startswith("finding:")]
    assert not any("infra" in ln for ln in finding_lines), finding_lines


def test_doctor_check_clean_on_job_taxonomy(tmp_path: Path):
    """`dos doctor --job --check` is clean — the reference taxonomy passes its own
    rail. (Job's `global` exclusive lane is treeless, which is fine.)"""
    proc = _cli(tmp_path, "doctor", "--job", "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_doctor_check_clean_on_fresh_init_scaffold(tmp_path: Path):
    """A freshly `dos init`'d workspace passes its OWN `--check` (no dead scaffold).

    `tmp_path/svc` is an EMPTY dir, so init scaffolds the honest single-writer
    fallback: an EXCLUSIVE `main` over the whole repo, with no concurrent/autopick
    lanes. An exclusive lane never enters the disjointness algebra, so a whole-repo
    tree is correct and the scaffold's own completeness check is clean (Phase 3c:
    no dead scaffold remains, and no degenerate concurrent lane).
    """
    ws = tmp_path / "svc"
    init = subprocess.run(
        [sys.executable, "-m", "dos.cli", "init", str(ws)],
        capture_output=True, text=True,
    )
    assert init.returncode == 0, init.stderr
    proc = _cli(ws, "doctor", "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ===========================================================================
# `dos init` auto-derives disjoint lanes from the repo's top-level directories
# (so the scaffolded default is actually usable for concurrent work, instead of
# the old unconditional whole-repo `main` lane that could never run alongside
# anything). Empty/dirless repos fall back to an honest exclusive single writer.
# ===========================================================================
def _init(ws: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", "init", str(ws)],
        capture_output=True, text=True,
    )


def test_init_scaffolds_one_disjoint_lane_per_top_level_dir(tmp_path: Path):
    for d in ("src", "tests", "docs"):
        (tmp_path / d).mkdir()
    assert _init(tmp_path).returncode == 0
    cfg = _config.load_workspace_config(tmp_path)
    # Each detected dir is a concurrent lane owning its own subtree.
    assert set(cfg.lanes.concurrent) == {"src", "tests", "docs"}
    assert set(cfg.lanes.autopick) == {"src", "tests", "docs"}
    assert list(cfg.lanes.tree_for("src")) == ["src/**"]
    assert list(cfg.lanes.tree_for("tests")) == ["tests/**"]
    # The whole-repo lane is the EXCLUSIVE `global`, never a concurrent one.
    assert "global" in cfg.lanes.exclusive
    assert list(cfg.lanes.tree_for("global")) == ["**/*"]


def test_init_scaffold_is_check_clean_and_enables_concurrency(tmp_path: Path):
    for d in ("src", "tests"):
        (tmp_path / d).mkdir()
    assert _init(tmp_path).returncode == 0
    # `--check` is clean: the lanes are disjoint and each has a tree.
    proc = _cli(tmp_path, "doctor", "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # And the payoff: two scaffolded lanes actually arbitrate concurrently.
    from dos import arbiter
    from dos.admission import built_in_predicates
    cfg = _config.load_workspace_config(tmp_path)
    live = [{"lane": "src", "lane_kind": "cluster", "tree": ["src/**"],
             "loop_ts": "20260601T0000Z"}]
    d = arbiter.arbitrate(
        requested_lane="tests", requested_kind="cluster",
        requested_tree=cfg.lanes.tree_for("tests"), live_leases=live, config=cfg,
        predicates=built_in_predicates(workspace=cfg.root),
    )
    assert d.outcome == "acquire"


def test_init_skips_noise_dirs(tmp_path: Path):
    for d in ("src", ".git", "__pycache__", "node_modules", "dist", "build"):
        (tmp_path / d).mkdir()
    assert _init(tmp_path).returncode == 0
    cfg = _config.load_workspace_config(tmp_path)
    # Only the real source dir becomes a lane; VCS/cache/build dirs are skipped.
    assert set(cfg.lanes.concurrent) == {"src"}


def test_init_empty_repo_falls_back_to_exclusive_single_writer(tmp_path: Path):
    assert _init(tmp_path).returncode == 0
    cfg = _config.load_workspace_config(tmp_path)
    # No dirs → no concurrent lanes; one exclusive `main` over the whole repo.
    assert tuple(cfg.lanes.concurrent) == ()
    assert "main" in cfg.lanes.exclusive
    assert list(cfg.lanes.tree_for("main")) == ["**/*"]
    # The honest single-writer fallback still passes its own --check.
    proc = _cli(tmp_path, "doctor", "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_init_caps_lane_count(tmp_path: Path):
    from dos.cli import _INIT_LANE_MAX
    for i in range(_INIT_LANE_MAX + 5):
        (tmp_path / f"pkg{i:02d}").mkdir()
    assert _init(tmp_path).returncode == 0
    cfg = _config.load_workspace_config(tmp_path)
    assert len(cfg.lanes.concurrent) == _INIT_LANE_MAX


# ===========================================================================
# BOM robustness — a PowerShell-default (utf-8 BOM) dos.toml must still read back
# ===========================================================================


def test_bom_prefixed_dos_toml_is_read_not_dropped(tmp_path: Path):
    """A UTF-8 BOM on dos.toml (PowerShell's default `utf8`) must NOT silently drop
    a valid declared table.

    `tomllib.load(rb)` chokes on a leading BOM ("Invalid statement at line 1"),
    which the CLI's malformed-guard would otherwise catch — demoting a perfectly
    valid `[lanes]` table to the base taxonomy with a misattributed warning. The
    loaders read via `utf-8-sig`, so the BOM is stripped and the declared lanes
    are honored. (Additive-degradation law: a present, well-formed table is never
    silently dropped.)
    """
    repo = tmp_path
    repo.mkdir(parents=True, exist_ok=True)
    body = (
        "[lanes]\nconcurrent = ['api', 'worker']\nexclusive = []\n"
        "autopick = ['api']\n[lanes.trees]\napi = ['src/api/**']\n"
        "worker = ['src/worker/**']\n"
    )
    # Write WITH a UTF-8 BOM, the exact PowerShell-5.1 `Set-Content -Encoding utf8`
    # output that trips raw tomllib.
    (repo / "dos.toml").write_text(body, encoding="utf-8-sig")
    # sanity: the bytes really start with the BOM
    assert (repo / "dos.toml").read_bytes()[:3] == b"\xef\xbb\xbf"

    proc = _cli(repo, "doctor")
    assert proc.returncode == 0, proc.stderr
    # the declared lanes were read back (NOT the base main/global)…
    assert "concurrent lanes    api, worker" in proc.stdout, proc.stdout
    # …and NO malformed-table warning fired.
    assert "malformed" not in proc.stderr, proc.stderr


class TestWorkspaceFacts:
    """`WorkspaceFacts` — the third seam-value on `SubstrateConfig`, gathered once
    at config-build time so the arbiter can be workspace-aware WITHOUT doing I/O on
    its pure admission path. The facts answer "what is true of THIS tree": chiefly,
    which of the kernel's own runtime modules exist under it (0 ⇒ foreign repo, full
    set ⇒ DOS serving itself). This is what makes a `**/*` lane admit on a foreign
    repo while still tripping SELF_MODIFY in the kernel's own repo."""

    def test_foreign_repo_gathers_empty_kernel_files(self, tmp_path):
        # A bare dir has none of `src/dos/*.py` → empty facts, not the kernel repo.
        facts = gather_workspace_facts(tmp_path)
        assert facts.root == tmp_path.resolve()
        assert facts.kernel_runtime_files == ()
        assert facts.is_kernel_repo is False

    def test_kernel_repo_gathers_full_runtime_set(self):
        # DOS serving its OWN repo: the runtime modules exist → non-empty facts.
        # We point at the package root (parent of `dos/`), where `src/dos/...`
        # resolves. The exact count is the static set's size; we assert non-empty
        # + the flag rather than pin a brittle number.
        from dos.self_modify import _DISPATCH_RUNTIME_FILES
        import dos
        repo_root = Path(dos.__file__).resolve().parents[2]  # .../dos (repo), src/dos/__init__.py → parents[2]
        facts = gather_workspace_facts(repo_root)
        # Only assert the kernel-repo shape when the source layout is actually
        # present (an editable/src checkout); a wheel-only install has no
        # `src/dos/arbiter.py`, and there the facts are legitimately empty.
        if (repo_root / "src/dos/arbiter.py").exists():
            assert facts.is_kernel_repo is True
            assert set(facts.kernel_runtime_files) <= set(_DISPATCH_RUNTIME_FILES)
            assert facts.kernel_runtime_files  # non-empty

    def test_builders_populate_facts(self, tmp_path):
        # Both config builders gather facts (the boundary that is allowed to probe).
        assert default_config(tmp_path).workspace is not None
        assert job_config(tmp_path).workspace is not None
        # …and a foreign tmp dir yields the empty/foreign fact set on both.
        assert default_config(tmp_path).kernel_runtime_files == ()
        assert job_config(tmp_path).kernel_runtime_files == ()

    def test_dataclass_default_is_factless(self):
        # The PURE construction path: a hand-built SubstrateConfig (no builder) has
        # NO facts — `kernel_runtime_files` is None ("unknown, stay conservative").
        cfg = _config.SubstrateConfig(
            lanes=LaneTaxonomy(concurrent=("main",), exclusive=("global",),
                               autopick=("main",), trees={"main": ("**/*",)}),
            paths=PathLayout.for_dos_dir("C:/tmp"),
        )
        assert cfg.workspace is None
        assert cfg.kernel_runtime_files is None

    def test_with_root_regathers_facts_when_present(self, tmp_path):
        # A facts-bearing config re-points → facts re-gathered under the NEW root
        # (never the stale old tree's). `default_config` gathers, so re-pointing it
        # to a different foreign dir keeps facts present and root-correct.
        other = tmp_path / "other"
        other.mkdir()
        cfg = default_config(tmp_path)
        moved = cfg.with_root(other)
        assert moved.workspace is not None
        assert moved.workspace.root == other.resolve()
        assert moved.kernel_runtime_files == ()  # still foreign

    def test_with_root_stays_factless_when_original_had_no_facts(self):
        # A pure (factless) config re-points WITHOUT gathering — `with_root` does no
        # surprise I/O for a hand-built config; facts stay None.
        cfg = _config.SubstrateConfig(
            lanes=LaneTaxonomy(concurrent=("main",), exclusive=("global",),
                               autopick=("main",), trees={"main": ("**/*",)}),
            paths=PathLayout.for_dos_dir("C:/tmp"),
        )
        moved = cfg.with_root("C:/elsewhere")
        assert moved.workspace is None
