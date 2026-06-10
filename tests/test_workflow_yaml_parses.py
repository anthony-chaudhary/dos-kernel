"""Every tracked GitHub workflow file must parse as YAML — the line-300 litmus.

2026-06-10: commit 29df8cc landed the ci-ok fold with a single-line plain-scalar
`run: echo "ci-ok — needed results: ${{ … }}"`. A plain YAML scalar cannot
contain ': ' (colon+space), so GitHub refused the whole file ("Invalid workflow
file: ci.yml#L300") and every push after it ran ZERO jobs — including the
v0.23.0 release push, whose publish was then refused by the ci-green witness
gate (the gate held; detection latency was the gap).

Two structural facts pick THIS stage for the check:

- CI cannot validate its own workflow file. A broken ci.yml schedules no jobs at
  all, including any would-be validator job inside it — the verifier has to live
  OUTSIDE the thing it verifies, i.e. below the push: this suite at author time,
  the machine-local pre-push hook at the boundary.
- publish.yml / dos-gate.yml fire only at tag time / on their own events, so a
  syntax error there would otherwise first surface mid-release — the most
  expensive stage there is. Parsing every tracked workflow here moves that
  discovery to author time.

PyYAML is the kernel's one runtime dependency, so the rung costs nothing new and
stays deterministic (ORACLE-rung, no agent, no network). Parse-clean is
necessary, not sufficient — an expression typo inside a string still needs
GitHub's own checker (or actionlint) — but it pins the failure class that
actually shipped. The roster is `git ls-files`, honoring "tracked here = ships":
a gitignored local scratch workflow must not redden the suite.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tracked_workflow_files() -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--", ".github/workflows"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [
        REPO_ROOT / rel
        for rel in out.split("\0")
        if rel and rel.endswith((".yml", ".yaml"))
    ]


def test_every_tracked_workflow_file_parses_as_yaml():
    files = _tracked_workflow_files()
    assert files, (
        "no tracked workflow files found under .github/workflows — "
        "either the checkout is broken or this litmus's roster query is"
    )
    failures = []
    for path in files:
        try:
            yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            failures.append(f"{path.relative_to(REPO_ROOT)}: {exc}")
    assert not failures, (
        "GitHub will refuse these workflow files at push time and run ZERO jobs "
        "on the push (the ci.yml#L300 failure mode):\n" + "\n".join(failures)
    )


def test_every_tracked_workflow_declares_runnable_jobs():
    """A parseable workflow with no runnable jobs is dead policy (docs/227 sense)."""
    for path in _tracked_workflow_files():
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        rel = path.relative_to(REPO_ROOT)
        assert isinstance(doc, dict), f"{rel}: top level is not a mapping"
        jobs = doc.get("jobs")
        assert isinstance(jobs, dict) and jobs, f"{rel}: no jobs declared"
        for job_id, job in jobs.items():
            assert isinstance(job, dict) and ("runs-on" in job or "uses" in job), (
                f"{rel}: job '{job_id}' has neither runs-on nor uses — "
                "it can never be scheduled"
            )
