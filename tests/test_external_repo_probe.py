"""Tests for scripts/external_repo_probe.py (#199).

The probe is dev tooling, not kernel code. These tests pin the two promised
paths:

* offline: fold committed scoreboard-shaped sweep JSON with no network;
* live fixture: ask public CLI verbs about a local git repo and keep stamp
  verifiability separate from commit-audit checkability.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "external_repo_probe.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("external_repo_probe", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_sweep(root: Path, org: str, name: str, doc: dict) -> Path:
    path = root / org / name / "sweep.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_offline_scoreboard_fold_separates_audit_from_verifiability(tmp_path):
    probe = _load_module()
    root = tmp_path / "scoreboard"
    _write_sweep(
        root,
        "acme",
        "one",
        {
            "repo": "acme/one",
            "summary": {
                "commits": 10,
                "checkable": 4,
                "witnessed": 4,
                "unwitnessed": 0,
                "abstained": 6,
                "by_kind": {},
                "unwitnessed_shas": [],
            },
        },
    )
    _write_sweep(
        root,
        "acme",
        "two",
        {
            "commits": 5,
            "checkable": 0,
            "witnessed": 0,
            "unwitnessed": 0,
            "abstained": 5,
            "by_kind": {},
            "unwitnessed_shas": [],
        },
    )

    probes = probe.probes_from_scoreboard(root)
    payload = probe.as_json(probes, stamp="2026-06-30")
    agg = payload["aggregate"]

    assert agg["repos"] == 2
    assert agg["audit"]["commits"] == 15
    assert agg["audit"]["checkable"] == 4
    assert agg["audit"]["checkable_rate"] == 4 / 15
    assert agg["verifiability"]["repos"] == 0
    assert agg["failure_modes"]["stamp-grammar-not-measured"] == 2
    assert agg["failure_modes"]["claim-grammar-low-fit"] == 1
    assert agg["failure_modes"]["no-checkable-commit-claims"] == 1


def test_report_names_live_mode_when_only_scoreboard_data_is_present(tmp_path):
    probe = _load_module()
    root = tmp_path / "scoreboard"
    _write_sweep(
        root,
        "acme",
        "one",
        {
            "repo": "acme/one",
            "summary": {
                "commits": 2,
                "checkable": 1,
                "witnessed": 1,
                "unwitnessed": 0,
                "abstained": 1,
                "by_kind": {},
                "unwitnessed_shas": [],
            },
        },
    )

    payload = probe.as_json(probe.probes_from_scoreboard(root), stamp="2026-06-30")
    report = probe.render_report(payload)

    assert "repos with live `doctor` verifiability | 0" in report
    assert "run the same script over local clones" in report
    assert "stamp-grammar-not-measured" in report


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _commit(repo: Path, message: str) -> None:
    _git(
        repo,
        "-c",
        "user.name=Jane Human",
        "-c",
        "user.email=jane@example.com",
        "commit",
        "--quiet",
        "-m",
        message,
    )


def _make_conventional_repo(root: Path) -> Path:
    repo = root / "conventional"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    (repo / "mod.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "mod.py")
    _commit(repo, "chore: initial import")
    (repo / "mod.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "mod.py")
    _commit(repo, "fix: handle empty input")
    (repo / "notes.txt").write_text("notes\n", encoding="utf-8")
    _git(repo, "add", "notes.txt")
    _commit(repo, "chore: add notes")
    return repo


def test_live_workspace_probe_measures_stamp_absence_separately(tmp_path):
    probe = _load_module()
    repo = _make_conventional_repo(tmp_path)

    got = probe.probe_workspace(repo, audit_range="HEAD~2..HEAD")

    assert got.error is None
    assert got.audit["commits"] == 2
    assert got.audit["checkable"] >= 1
    assert got.audit["witnessed"] >= 1
    assert got.verifiability is not None
    assert got.verifiability["commits_read"] >= 3
    assert got.verifiability["verifiable"] == 0
    assert "stamp-grammar-absence" in got.failure_modes


def test_cli_writes_report_and_json_from_fixture_scoreboard(tmp_path):
    root = tmp_path / "scoreboard"
    _write_sweep(
        root,
        "acme",
        "one",
        {
            "repo": "acme/one",
            "summary": {
                "commits": 3,
                "checkable": 2,
                "witnessed": 2,
                "unwitnessed": 0,
                "abstained": 1,
                "by_kind": {},
                "unwitnessed_shas": [],
            },
        },
    )
    report = tmp_path / "report.md"
    payload = tmp_path / "payload.json"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--scoreboard-root",
            str(root),
            "--out",
            str(report),
            "--json-out",
            str(payload),
            "--stamp",
            "2026-06-30",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "External-repo conformance probe" in report.read_text(encoding="utf-8")
    data = json.loads(payload.read_text(encoding="utf-8"))
    assert data["schema"] == "dos-external-repo-conformance/v1"
    assert data["aggregate"]["audit"]["checkable"] == 2

    check = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--scoreboard-root",
            str(root),
            "--out",
            str(report),
            "--stamp",
            "2026-06-30",
            "--check",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert check.returncode == 0, check.stderr
    assert "matches probe inputs" in check.stdout
