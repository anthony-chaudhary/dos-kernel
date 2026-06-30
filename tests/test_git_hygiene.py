"""The git-hygiene reporter is advisory by construction — it never blocks a Stop.

`scripts/git_hygiene.py` is wired as a second, advisory `Stop` hook in the
maintainer's machine-local `.claude/settings.local.json` (kept out of the shipped
clone — a cold user's session must not inherit the rig). The load-bearing
contract — the one the docs/274 scar
makes non-negotiable — is that the reporter is a REPORTER: in its default mode it
prints a nudge and **always exits 0**, so it can never force-loop an interactive
turn (a non-zero / blocking Stop hook does exactly that). The only non-zero exit is
the explicit, opt-in `--strict` / `DOS_GIT_HYGIENE=strict` mode, meant for a
headless loop that WANTS to act on dirty or hidden work.

This test pins that contract structurally against scaffolded SACRIFICIAL git repos
(never the live tree — the docs/274 sister-law: a destructive/stateful proof runs
against a throwaway target). It checks:

  * default mode exits 0 whether the tree is clean OR dirty (advisory),
  * a clean tree reports `clean: true` and prints nothing,
  * `--strict` exits 1 on stranded work but 0 on a clean tree,
  * a live lane-lease subtracts its region from the stranded set (the WAL fold),
  * durable untracked files warn as hot-tree risk while any live lease exists,
  * a stale lease does NOT (its region is fair game → stranded),
  * scratch (`.err`, `_scratch/`, `scripts/_probe.py`) is bucketed, not nagged,
  * stash entries are reported as hidden work and point away from hot-tree pop/drop.

Dev/workflow TOOLING, not kernel — it operates ON the package, never imported BY it.
Loaded by path because `scripts/` is not an importable package.
"""

from __future__ import annotations

import datetime
import json
import subprocess
import sys
from pathlib import Path

import dos

_REPO_ROOT = Path(dos.__file__).resolve().parents[2]
_HYGIENE_PY = _REPO_ROOT / "scripts" / "git_hygiene.py"


def _run(workspace: Path, *extra: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Invoke the reporter as a subprocess (the way the hook does)."""
    return subprocess.run(
        [sys.executable, str(_HYGIENE_PY), "--workspace", str(workspace), *extra],
        capture_output=True, text=True, env=env,
    )


def _report(workspace: Path) -> dict:
    proc = _run(workspace, "--json")
    assert proc.returncode == 0, f"--json should always exit 0: {proc.stderr}"
    return json.loads(proc.stdout)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    return repo


def test_clean_tree_is_silent_and_exit_zero(tmp_path):
    repo = _init_repo(tmp_path)
    proc = _run(repo)
    assert proc.returncode == 0
    assert proc.stderr.strip() == "", "a clean tree must print nothing"
    rep = _report(repo)
    assert rep["clean"] is True
    assert rep["dirty_total"] == 0


def test_dirty_tree_nudges_but_still_exits_zero(tmp_path):
    """The whole point: dirty → a visible nudge, but exit 0 (advisory, never block)."""
    repo = _init_repo(tmp_path)
    (repo / "realwork.py").write_text("x\n", encoding="utf-8")
    proc = _run(repo)
    assert proc.returncode == 0, "default mode must NEVER exit non-zero (docs/274)"
    assert "git-hygiene" in proc.stderr
    assert "uncommitted" in proc.stderr
    rep = _report(repo)
    assert rep["clean"] is False
    assert "realwork.py" in rep["stranded"]


def test_strict_exits_one_on_stranded_but_zero_when_clean(tmp_path):
    repo = _init_repo(tmp_path)
    # clean → strict still 0
    assert _run(repo, "--strict").returncode == 0
    # dirty → strict 1
    (repo / "realwork.py").write_text("x\n", encoding="utf-8")
    assert _run(repo, "--strict").returncode == 1


def test_env_gate_enables_strict(tmp_path):
    import os

    repo = _init_repo(tmp_path)
    (repo / "realwork.py").write_text("x\n", encoding="utf-8")
    env = dict(os.environ, DOS_GIT_HYGIENE="strict")
    assert _run(repo, env=env).returncode == 1
    # default (no env) stays advisory
    env2 = dict(os.environ)
    env2.pop("DOS_GIT_HYGIENE", None)
    assert _run(repo, env=env2).returncode == 0


def test_scratch_is_bucketed_not_stranded(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "debug.err").write_text("x\n", encoding="utf-8")
    (repo / "_scratch").mkdir()
    (repo / "_scratch" / "junk.py").write_text("x\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "_probe.py").write_text("x\n", encoding="utf-8")
    (repo / "durable.py").write_text("x\n", encoding="utf-8")
    rep = _report(repo)
    # durable.py is stranded; the scratch artifacts are NOT (they're deletable noise).
    assert "durable.py" in rep["stranded"]
    assert "debug.err" in rep["scratch"]
    assert any("_scratch" in s for s in rep["scratch"])
    assert any("scripts/_probe.py" in s.replace("\\", "/") for s in rep["scratch"])
    assert not any("debug.err" in s for s in rep["stranded"])


def _write_journal(repo: Path, *, tree: list[str], stale: bool) -> None:
    """Scaffold a dos.toml + a lane-journal with one live/stale ACQUIRE over `tree`."""
    (repo / "dos.toml").write_text(
        '[paths]\nlane_journal = ".dos/lane_journal.jsonl"\n', encoding="utf-8"
    )
    age = datetime.timedelta(seconds=(10_000 if stale else 5))
    ts = (datetime.datetime.now(datetime.timezone.utc) - age).isoformat()
    rec = {
        "op": "ACQUIRE", "lane": "benchmark", "lane_kind": "cluster",
        "tree": tree, "acquired_at": ts, "heartbeat_at": ts,
        "ttl_minutes": 5, "holder": "loop-1",
    }
    jdir = repo / ".dos"
    jdir.mkdir(exist_ok=True)
    (jdir / "lane_journal.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")


def test_live_lease_region_is_subtracted_from_stranded(tmp_path):
    """A live loop owns its dirty paths mid-flight — don't nag about them."""
    repo = _init_repo(tmp_path)
    (repo / "benchmark").mkdir()
    (repo / "benchmark" / "wip.py").write_text("x\n", encoding="utf-8")
    _write_journal(repo, tree=["benchmark/**"], stale=False)
    rep = _report(repo)
    assert rep["live_leases"] == 1
    assert any("benchmark" in p for p in rep["lease_held"])
    assert not any("benchmark" in p for p in rep["stranded"])
    assert rep["hot_untracked_at_risk"] is True


def test_live_lease_untracked_risk_mentions_worktree_escape(tmp_path):
    """A hot tree with durable untracked files gets the unrecoverable-loss warning."""
    repo = _init_repo(tmp_path)
    (repo / "notes.py").write_text("x\n", encoding="utf-8")
    _write_journal(repo, tree=["docs/**"], stale=False)
    proc = _run(repo)
    assert proc.returncode == 0
    assert "untracked file" in proc.stderr
    assert "at risk" in proc.stderr
    assert "detached git worktree off origin/master" in proc.stderr
    rep = _report(repo)
    assert rep["hot_untracked_at_risk"] is True
    assert "notes.py" in rep["durable_untracked"]


def test_stale_lease_region_counts_as_stranded(tmp_path):
    """A dead loop's region is fair game — its dirty paths are stranded, not held."""
    repo = _init_repo(tmp_path)
    (repo / "benchmark").mkdir()
    (repo / "benchmark" / "wip.py").write_text("x\n", encoding="utf-8")
    _write_journal(repo, tree=["benchmark/**"], stale=True)
    rep = _report(repo)
    # The stale lease is dropped from the live set → its region is stranded again.
    assert rep["live_leases"] == 0
    assert any("benchmark" in p for p in rep["stranded"])
    assert not rep["lease_held"]


def test_stash_entries_nudge_without_blocking(tmp_path):
    """A leftover stash is hidden work; report it, but stay advisory by default."""
    repo = _init_repo(tmp_path)
    tracked = repo / "tracked.py"
    tracked.write_text("base\n", encoding="utf-8")
    _git(repo, "add", "tracked.py")
    _git(repo, "commit", "-q", "-m", "add tracked")
    tracked.write_text("wip\n", encoding="utf-8")
    _git(repo, "stash", "push", "-q", "-m", "issue-208-probe", "--", "tracked.py")

    proc = _run(repo)
    assert proc.returncode == 0
    assert "git stash entry present" in proc.stderr
    assert "copy-aside" in proc.stderr
    assert "do not git stash pop/drop on a hot tree" in proc.stderr

    rep = _report(repo)
    assert rep["clean"] is False
    assert rep["dirty_total"] == 0
    assert rep["stash_count"] == 1
    assert any("issue-208-probe" in row for row in rep["stash_entries"])
