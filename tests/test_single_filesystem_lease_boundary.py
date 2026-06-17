"""Boundary honesty for the single-filesystem lease scope (docs/366, issue #196).

Three things pinned here:

1. SECURITY.md states the lease-coordination limit explicitly.
2. docs/ARCHITECTURE.md carries the scope note on the arbitrate/lease row.
3. `dos doctor` prints the lease-scope note when the workspace has a git remote
   and is silent about it when no remote is configured.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import dos


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


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


# ---------------------------------------------------------------------------
# 1. doc-string assertions
# ---------------------------------------------------------------------------

def test_security_md_states_lease_boundary():
    """SECURITY.md must explicitly say lease coordination is local-filesystem
    only so readers do not infer cross-host mutual exclusion from the fleet
    framing elsewhere in the docs."""
    root = Path(dos.__file__).parents[2]
    text = (root / "SECURITY.md").read_text(encoding="utf-8")
    assert "Lease coordination is local-filesystem only" in text, (
        "SECURITY.md is missing the single-filesystem lease boundary statement "
        "(docs/366 §2)"
    )


def test_architecture_md_states_lease_boundary():
    """ARCHITECTURE.md's arbitrate/lease syscall row must carry the scope note
    so an editor of that row knows the WAL is local-filesystem only."""
    root = Path(dos.__file__).parents[2]
    text = (root / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "local filesystem" in text and "lease" in text.lower(), (
        "ARCHITECTURE.md is missing the local-filesystem scope note on the "
        "lease/arbitrate row (docs/366 §2)"
    )
    # the note specifically calls out the cross-machine gap
    assert "docs/366" in text, (
        "ARCHITECTURE.md should reference docs/366 on the arbitrate row"
    )


# ---------------------------------------------------------------------------
# 2. dos doctor emits the note when a remote is present
# ---------------------------------------------------------------------------

def test_doctor_prints_lease_scope_when_remote_present(tmp_path: Path):
    """`dos doctor` must print the lease-scope line when the workspace has a
    configured git remote — the one-line surface that tips off a multi-host
    operator (docs/366 §2)."""
    _plain_repo(tmp_path)
    # add a fake remote (no network needed — we just want `git remote` to list one)
    _git(tmp_path, "remote", "add", "origin", "https://github.com/example/repo.git")
    proc = _cli(tmp_path, "doctor")
    assert proc.returncode == 0, proc.stderr
    assert "lease scope" in proc.stdout, (
        "dos doctor should print 'lease scope' when the workspace has a git "
        f"remote.\nstdout:\n{proc.stdout}"
    )
    assert "local filesystem" in proc.stdout, (
        f"dos doctor lease-scope line should mention 'local filesystem'.\n"
        f"stdout:\n{proc.stdout}"
    )


def test_doctor_omits_lease_scope_when_no_remote(tmp_path: Path):
    """`dos doctor` must NOT print the lease-scope line when there is no git
    remote — a purely local workspace has no multi-host ambiguity."""
    _plain_repo(tmp_path)
    # deliberately no remote added
    proc = _cli(tmp_path, "doctor")
    assert proc.returncode == 0, proc.stderr
    assert "lease scope" not in proc.stdout, (
        "dos doctor should not print 'lease scope' for a workspace with no "
        f"git remote.\nstdout:\n{proc.stdout}"
    )
