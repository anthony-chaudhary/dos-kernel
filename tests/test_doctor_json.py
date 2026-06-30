"""`dos doctor --json` — the machine-readable workspace report a generic skill
reads to discover its layout (SKP Phase 1a).

A generic skill must not hardcode `docs/_plans/` — it asks `dos doctor --json`
for the active paths/lanes/stamp instead (the WCR on-ramp the skill pack rides).
This test pins three properties:

  * the `--json` object carries the declared layout — a WCR `[paths] plans_glob`
    / `[lanes]` override shows up in the report (not the default), proving the
    skill reads the SAME config `arbitrate`/`verify` do;
  * the default-text output is byte-unchanged when `--json` is omitted (the
    restructure that added the branch did not move the human path);
  * `--json --check` still honours the completeness rail — findings ride along in
    a `findings` array AND set the exit code, so a `--json` consumer cannot
    silently lose `--check` (the SKP Phase 1a correction).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import dos


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def _write_toml(repo: Path, body: str) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "dos.toml").write_text(body, encoding="utf-8")


def _cli(repo: Path, *argv: str) -> subprocess.CompletedProcess:
    import os
    env = {**os.environ, "PYTHONPATH": str(Path(dos.__file__).parents[1])}
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *argv, "--workspace", str(repo)],
        capture_output=True, text=True, env=env,
    )


def _plain_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "commit", "--allow-empty", "-m", "init")


def test_doctor_json_is_a_well_formed_report(tmp_path: Path):
    """`--json` emits one object with the fields a skill needs: paths, lanes,
    stamp, git, home."""
    _plain_repo(tmp_path)
    proc = _cli(tmp_path, "doctor", "--json")
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    # the load-bearing keys a generic skill reads
    assert report["workspace"] == str(tmp_path.resolve())
    assert report["git"] is True
    assert set(report["paths"]) >= {"plans_glob", "execution_state", "next_packets",
                                    "runs", "style"}  # `runs` = the dispatch archive dir
    assert set(report["lanes"]) == {"concurrent", "exclusive", "autopick", "trees"}
    assert "subject_dirs" in report["stamp"]
    assert "home" in report
    assert report["overlap_policy"]["active"] == "lock_modes"
    assert report["overlap_policy"]["ratio_max"] is None
    assert report["overlap_policy"]["mode_default"] == "exclusive"
    # The verdict-IS-exit-code contract is published per verb (item 1) so an agent
    # discovers it instead of reverse-engineering `$?`.
    assert set(report["exit_codes"]) >= {"verify", "arbitrate", "liveness", "gate"}
    assert report["exit_codes"]["verify"]["shipped"] == 0


def test_doctor_json_reports_workspace_facts(tmp_path: Path):
    """`--json` carries the discovered `workspace_facts` (the third seam-value).

    A `tmp_path` repo is FOREIGN — none of the kernel's runtime modules exist
    under it — so the gathered facts must report `is_kernel_repo=False` and zero
    runtime files present. This is the operator-visible proof that the workspace
    is a first-class object with discovered properties (and the surface that
    explains WHY a `**/*` lane admits here but would refuse in the DOS repo)."""
    _plain_repo(tmp_path)
    proc = _cli(tmp_path, "doctor", "--json")
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    facts = report["workspace_facts"]
    assert facts is not None, "a config built by the CLI always gathers facts"
    assert facts["is_kernel_repo"] is False
    assert facts["kernel_runtime_files_present"] == 0


def test_doctor_json_reports_declared_paths(tmp_path: Path):
    """A WCR-declared `plans_glob` shows up in `--json` — the skill sees the
    OVERRIDDEN glob, not the default (proves it reads the active config)."""
    _write_toml(
        tmp_path,
        "[lanes]\nconcurrent=['main']\nexclusive=['global']\nautopick=['main']\n"
        "[lanes.trees]\nmain=['**/*']\n"
        "[paths]\nplans_glob = 'planning/*.md'\n",
    )
    proc = _cli(tmp_path, "doctor", "--json")
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["paths"]["plans_glob"] == "planning/*.md"


def test_doctor_json_reports_declared_lanes(tmp_path: Path):
    """A WCR-declared `[lanes]` taxonomy reaches the `--json` report's lanes."""
    _write_toml(
        tmp_path,
        "[lanes]\nconcurrent=['api','worker']\nexclusive=['infra']\nautopick=['api']\n"
        "[lanes.trees]\napi=['src/api/**']\nworker=['src/worker/**']\ninfra=['deploy/**']\n",
    )
    proc = _cli(tmp_path, "doctor", "--json")
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["lanes"]["concurrent"] == ["api", "worker"]
    assert report["lanes"]["trees"]["api"] == ["src/api/**"]
    assert "main" not in report["lanes"]["concurrent"]  # replaced, not merged


def test_doctor_text_unchanged_without_json(tmp_path: Path):
    """Omitting `--json` leaves the human text path byte-unchanged (the branch
    did not move the default output)."""
    _plain_repo(tmp_path)
    proc = _cli(tmp_path, "doctor")
    assert proc.returncode == 0, proc.stderr
    # the established human lines are still present and unchanged
    assert "workspace root      " in proc.stdout
    assert "concurrent lanes    main" in proc.stdout
    assert "stamp convention    " in proc.stdout
    assert "lock_modes*" in proc.stdout
    assert "ratio_max=inactive" in proc.stdout
    # and the text path emits NO JSON
    assert not proc.stdout.lstrip().startswith("{")


def test_doctor_json_check_composes_and_sets_exit(tmp_path: Path):
    """`--json --check` keeps the completeness rail: a treeless lane shows up in
    the `findings` array AND makes the command exit non-zero (the SKP Phase 1a
    correction — a `--json` path must not silently drop `--check`)."""
    _write_toml(
        tmp_path,
        "[lanes]\nconcurrent=['api','worker']\nexclusive=[]\nautopick=['api']\n"
        "[lanes.trees]\napi=['src/api/**']\n",  # worker has no tree → a finding
    )
    proc = _cli(tmp_path, "doctor", "--json", "--check")
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    report = json.loads(proc.stdout)
    assert "findings" in report
    assert any("worker" in f for f in report["findings"]), report["findings"]


def test_doctor_json_check_clean_exits_zero(tmp_path: Path):
    """`--json --check` with every lane treed and no declared-stamp mismatch is a
    clean exit 0 with an empty findings array."""
    _write_toml(
        tmp_path,
        "[lanes]\nconcurrent=['api']\nexclusive=['infra']\nautopick=['api']\n"
        "[lanes.trees]\napi=['src/api/**']\ninfra=['deploy/**']\n",
    )
    (tmp_path / "src/api").mkdir(parents=True)
    (tmp_path / "deploy").mkdir()
    proc = _cli(tmp_path, "doctor", "--json", "--check")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    report = json.loads(proc.stdout)
    assert report.get("findings") == []


def test_doctor_json_reports_the_dot_dos_surface(tmp_path: Path):
    """docs/313 P4 — the `dot_dos` section sizes the per-project state surface:
    policy provenance, the identity card, and each fossil's presence. On a
    fresh repo with no `.dos/` everything honestly reads absent; after a
    durable lease the WAL and card show up — one doctor read answers "what
    does my .dos know?"."""
    _plain_repo(tmp_path)

    proc = _cli(tmp_path, "doctor", "--json")
    assert proc.returncode == 0, proc.stderr
    dd = json.loads(proc.stdout)["dot_dos"]
    assert dd["config_declared"] is False  # no dos.toml → generic default
    assert dd["project_card"] is None
    assert set(dd["fossils"]) == {"lane_journal", "verdict_journal",
                                  "observations", "runs", "streams"}
    assert all(v is None for v in dd["fossils"].values())  # nothing written yet

    # A durable lease creates the surface; doctor now sees the WAL and the card.
    # (`--workspace` must precede the subcommand for subcommand verbs, so this
    # one call can't ride the tail-appending `_cli` helper.)
    acq = subprocess.run(
        [sys.executable, "-m", "dos.cli", "lease-lane", "--workspace", str(tmp_path),
         "acquire", "--lane", "main", "--owner", "t1"],
        capture_output=True, text=True,
    )
    assert acq.returncode == 0, acq.stderr
    dd = json.loads(_cli(tmp_path, "doctor", "--json").stdout)["dot_dos"]
    assert dd["fossils"]["lane_journal"]["rows"] >= 1
    assert dd["project_card"]["schema"] == 1


def test_doctor_text_carries_the_dot_dos_line(tmp_path: Path):
    """The human path reports the same surface in one line."""
    _plain_repo(tmp_path)
    proc = _cli(tmp_path, "doctor")
    assert proc.returncode == 0, proc.stderr
    assert ".dos surface        policy: generic default" in proc.stdout
