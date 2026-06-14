"""`dos apply` — the binding diff turnstile as a CLI verb (docs/126 Phase 1).

`apply` is DOS's first Policy Enforcement Point: where `scope-gate` checks a
CLI-named lane and is advisory, `apply` resolves the run's OWN held lease at the
boundary and BINDS — exit 1 refuses the write, and `--force` is the operator's
sole, audited override (the agent cannot force its own gate). The pure-verdict
tests (`test_apply_gate.py`) pin the decision; these pin the WIRING they can't see:

  * the decision IS the exit code (ALLOW=0, REFUSE=1, contract_error=2);
  * `--file` supplies the proposed write-set without git (the commit-broker path);
  * `--lane X` names the held lane explicitly (the operator self-lease override),
    and a write that escapes it is REFUSED naming the spill;
  * `--force` flips a refusal to exit 0 AND writes an audited HUMAN decision row
    (docs/126 §3 rule 4) — the override is never silent;
  * `--json` carries the typed `SCOPE_ESCAPE` reason_class + the resolved self_lane.
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
    # Strip loop-context env so the boundary self-lease resolver doesn't latch
    # onto an ambient run-id/loop-ts from the test host (we drive identity via
    # --lane / --file here, the hermetic path).
    for k in ("CID_RUN_ID", "DISPATCH_RUN_ID", "DISPATCH_LOOP_TS"):
        env.pop(k, None)
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", "apply", *argv, "--workspace", str(repo)],
        capture_output=True, text=True, env=env,
    )


def _workspace_with_lane(repo: Path) -> Path:
    """A workspace whose `[lanes.trees]` declares a narrow `docs` lane + a `src` lane."""
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "dos.toml").write_text(
        "[lanes]\n"
        'concurrent = ["docs", "src"]\n'
        'exclusive = ["global"]\n'
        'autopick = ["docs", "src"]\n'
        "\n"
        "[lanes.trees]\n"
        'docs = ["docs/"]\n'
        'src = ["src/"]\n',
        encoding="utf-8",
    )
    return repo


# ---------------------------------------------------------------------------
# 1. The decision → exit-code map (the verb's binding contract).
# ---------------------------------------------------------------------------


def test_in_tree_write_allows_exit_0(tmp_path: Path):
    """Held lane `docs` (via --lane), a write inside docs/ → ALLOW, exit 0."""
    repo = _workspace_with_lane(tmp_path)
    proc = _cli(repo, "--lane", "docs", "--file", "docs/readme.md")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert proc.stdout.startswith("ALLOW")


def test_escaping_write_refuses_exit_1(tmp_path: Path):
    """Held lane `docs`, a write into src/ → REFUSE SCOPE_ESCAPE, exit 1, names it.

    The keystone: the binding turnstile blocks the escaping write BEFORE it lands,
    where `verify` would only have caught it after the commit was already history.
    """
    repo = _workspace_with_lane(tmp_path)
    proc = _cli(repo, "--lane", "docs", "--file", "src/y.py")
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert proc.stdout.startswith("REFUSE")
    assert "src/y.py" in proc.stdout


def test_empty_footprint_allows(tmp_path: Path):
    """No files (no --file, no staged diff in a non-git dir) → ALLOW (no-op floor)."""
    repo = _workspace_with_lane(tmp_path)
    proc = _cli(repo, "--lane", "docs")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert proc.stdout.startswith("ALLOW")


# ---------------------------------------------------------------------------
# 2. The --json contract (the typed reason + the resolved self-lane).
# ---------------------------------------------------------------------------


def test_json_carries_typed_reason_and_self_lane(tmp_path: Path):
    repo = _workspace_with_lane(tmp_path)
    proc = _cli(repo, "--lane", "docs", "--file", "src/y.py", "--json")
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    payload = json.loads(proc.stdout)
    assert payload["allowed"] is False
    assert payload["reason_class"] == "SCOPE_ESCAPE"
    assert payload["self_lane"] == "docs"
    assert payload["forced"] is False
    assert "src/y.py" in payload["refused_files"]


# ---------------------------------------------------------------------------
# 3. The --force operator override — flips the exit AND audits a HUMAN row.
# ---------------------------------------------------------------------------


def test_force_overrides_refusal_to_exit_0(tmp_path: Path):
    """`--force` on a refused write → exit 0, FORCE-ALLOW (the operator's hand)."""
    repo = _workspace_with_lane(tmp_path)
    proc = _cli(repo, "--lane", "docs", "--file", "src/y.py", "--force")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "FORCE-ALLOW" in proc.stdout


def test_force_writes_audited_human_decision_row(tmp_path: Path):
    """The override is never silent: a typed HUMAN row lands in the local mirror.

    docs/126 §3 rule 4 — `--force` writes a typed, ancestry-visible reason. We read
    the project's `.dos/decisions/resolved.jsonl` mirror and assert the APPLY_FORCE
    row, resolver_kind HUMAN, carrying the SCOPE_ESCAPE reason token.
    """
    repo = _workspace_with_lane(tmp_path)
    proc = _cli(repo, "--lane", "docs", "--file", "src/y.py", "--force")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)

    mirror = repo / ".dos" / "decisions" / "resolved.jsonl"
    assert mirror.exists(), "force should have written an audited decision row"
    rows = [json.loads(ln) for ln in mirror.read_text(encoding="utf-8").splitlines() if ln.strip()]
    forced = [r for r in rows if r.get("kind") == "APPLY_FORCE"]
    assert forced, f"no APPLY_FORCE row in {rows}"
    row = forced[-1]
    assert row["resolver_kind"] == "HUMAN"
    assert row["reason_token"] == "SCOPE_ESCAPE"
    assert row["lane"] == "docs"
    assert row["resolution"]["forced"] is True
    assert "src/y.py" in row["resolution"]["refused_files"]


def test_force_on_an_allowed_write_does_not_audit(tmp_path: Path):
    """`--force` is a no-op on a write that was already allowed — no spurious row.

    `forced` is `--force AND not allowed`; an in-tree write needs no override, so
    nothing is audited (the override trail records only real overrides).
    """
    repo = _workspace_with_lane(tmp_path)
    proc = _cli(repo, "--lane", "docs", "--file", "docs/readme.md", "--force")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert proc.stdout.startswith("ALLOW")  # not FORCE-ALLOW
    mirror = repo / ".dos" / "decisions" / "resolved.jsonl"
    if mirror.exists():
        rows = [json.loads(ln) for ln in mirror.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert not [r for r in rows if r.get("kind") == "APPLY_FORCE"]


# ---------------------------------------------------------------------------
# 4. Fail-closed: an unresolvable / undeclared held lane refuses a real diff.
# ---------------------------------------------------------------------------


def test_unresolved_self_lease_fails_closed(tmp_path: Path):
    """No --lane and no live lease to match → empty self_tree → REFUSE a real diff.

    The boundary could not certify which lane this run holds, so a non-empty write
    is refused rather than waved through — the conservative unknown-blast-radius
    stance, now BINDING at the apply surface.
    """
    repo = _workspace_with_lane(tmp_path)
    proc = _cli(repo, "--file", "src/y.py")  # no --lane, no live lease
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert proc.stdout.startswith("REFUSE")
