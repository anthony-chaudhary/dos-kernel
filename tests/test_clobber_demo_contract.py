"""Pin the `examples/demo/clobber_demo` collision-moment against the real `dos` CLI.

`examples/demo/clobber_demo.sh` (+ its `.ps1` twin) is the arbiter's runnable
on-ramp: round 1 shows two agents losing an update with no referee; round 2
replays the same moment through the lease/arbitrate pair and gets a typed
refusal instead of silent data loss. Like the verify demo, the scripts are
package-DATA nothing imports — so this test replays the round-2 command
sequence THROUGH the real `dos.cli` subprocess path and asserts the
load-bearing facts the demo renders:

  * `lease-lane acquire --lane main --owner agent-a` ACQUIRES with exit **0**
    and `"journaled": true` (the grant is in the WAL, not just decided);
  * `arbitrate --lane main` while held REFUSES with exit **1** and an
    "already held" reason (the demo's punchline: refused BEFORE work is lost);
  * `lease-lane release` releases with exit **0**;
  * `arbitrate --lane main` after release ACQUIRES again with exit **0**
    (the refusal is a wait, not a dead end — the lock-manager-not-brake line).

The test does NOT shell `bash`/`pwsh` (neither is guaranteed in CI); it drives
the identical command *contract* in Python, the same discipline as
`test_verify_demo_contract.py` for the verify demo.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SRC_DIR = Path(__file__).resolve().parents[1] / "src"


def _env() -> dict:
    return {
        **os.environ,
        "PYTHONPATH": str(_SRC_DIR),
        "NO_COLOR": "1",
        "PYTHONIOENCODING": "utf-8",
    }


def _cli(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "from dos.cli import main; raise SystemExit(main())", *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_env(),
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _json_payload(stdout: str) -> dict:
    """The verdict JSON is the first `{`-opening line; info lines may precede it."""
    for line in stdout.splitlines():
        if line.lstrip().startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no JSON payload in output: {stdout!r}")


@pytest.fixture()
def demo_repo(tmp_path: Path) -> Path:
    """Reproduce what `clobber_demo.sh` builds: a fresh git repo scaffolded by
    `dos init`, with one seed commit and no lease held."""
    repo = tmp_path / "demo"
    repo.mkdir()

    init = _cli("init", str(repo))
    assert init.returncode == 0, init.stderr

    _git(repo, "init")
    _git(repo, "config", "user.email", "demo@example.com")
    _git(repo, "config", "user.name", "Demo")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "service.md").write_text("retry budget: 3\n", encoding="ascii")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed: the shared service notes")
    return repo


def test_clobber_demo_round2_contract(demo_repo: Path) -> None:
    """The full round-2 sequence: journaled grant -> typed refusal -> release -> re-admission."""
    ws = str(demo_repo)

    # Agent A takes the lane, and the grant is journaled (held, not just decided).
    acq = _cli("lease-lane", "--workspace", ws, "acquire", "--lane", "main", "--owner", "agent-a")
    assert acq.returncode == 0, f"expected acquire exit 0, got {acq.returncode}: {acq.stdout}{acq.stderr}"
    grant = _json_payload(acq.stdout)
    assert grant["outcome"] == "acquire"
    assert grant["journaled"] is True
    assert grant["owner"] == "agent-a"

    # Agent B asks for the same lane while it is held: the typed refusal, exit 1.
    refused = _cli("arbitrate", "--workspace", ws, "--lane", "main")
    assert refused.returncode == 1, (
        f"expected refuse exit 1 while held, got {refused.returncode}: {refused.stdout}{refused.stderr}"
    )
    verdict = _json_payload(refused.stdout)
    assert verdict["outcome"] == "refuse"
    assert "already held" in verdict["reason"]

    # Agent A releases.
    rel = _cli("lease-lane", "--workspace", ws, "release", "--lane", "main", "--owner", "agent-a")
    assert rel.returncode == 0, f"expected release exit 0, got {rel.returncode}: {rel.stdout}{rel.stderr}"
    assert _json_payload(rel.stdout)["released"] is True

    # The refusal was a wait, not a dead end: B is admitted the moment the lane frees.
    readmit = _cli("arbitrate", "--workspace", ws, "--lane", "main")
    assert readmit.returncode == 0, (
        f"expected acquire exit 0 after release, got {readmit.returncode}: {readmit.stdout}{readmit.stderr}"
    )
    assert _json_payload(readmit.stdout)["outcome"] == "acquire"


def test_clobber_demo_scripts_exist() -> None:
    """Guard the guard: the demo scripts this test stands in for must still exist."""
    demo_dir = _SRC_DIR.parent / "examples" / "demo"
    for name in ("clobber_demo.sh", "clobber_demo.ps1"):
        assert (demo_dir / name).is_file(), (
            f"{name} is missing — this test pins its CLI contract; update or remove "
            f"this test if the demo was intentionally retired."
        )
