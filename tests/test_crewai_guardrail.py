"""Contract tests for `dos.drivers.crewai_guardrail` (docs/305 Phase 3).

The whole point of this adapter is that CrewAI's guardrail contract is a plain
tuple over a duck-typed input — so EVERY test here runs end-to-end with no
crewai installed: a stand-in TaskOutput carries ``.raw``, exactly the field
the adapter reads.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from dos.drivers._effect_gate import CommitClaim, FileClaim
from dos.drivers.crewai_guardrail import dos_task_guardrail


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    ).stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "ws"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    (r / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(r, "add", "seed.txt")
    _git(r, "commit", "-q", "-m", "seed")
    return r


def _task_output(text: str) -> SimpleNamespace:
    """A structural stand-in for crewai's TaskOutput — the adapter reads .raw."""
    return SimpleNamespace(raw=text)


# ---------------------------------------------------------------------------
# The tuple contract.
# ---------------------------------------------------------------------------


def test_overclaim_fails_task_with_actionable_reason(repo: Path) -> None:
    guardrail = dos_task_guardrail(str(repo), expect=[CommitClaim()])
    ok, reason = guardrail(_task_output("done! committed the fix."))
    assert ok is False
    # The False-reason is the retry feedback CrewAI hands the agent: it names
    # the absent effect and what would make it pass.
    assert "ABSENT" in reason
    assert "do the work" in reason


def test_landed_commit_passes_identity(repo: Path) -> None:
    guardrail = dos_task_guardrail(str(repo), expect=[CommitClaim()])
    (repo / "work.txt").write_text("w\n", encoding="utf-8")
    _git(repo, "add", "work.txt")
    _git(repo, "commit", "-q", "-m", "land work")
    out = _task_output("done! committed the fix.")
    ok, value = guardrail(out)
    assert ok is True
    assert value is out  # identity: safe in a guardrails=[...] chain


def test_retry_loop_story(repo: Path) -> None:
    # The seat's whole point: attempt 1 over-claims and FAILS; the agent then
    # actually does the work; attempt 2 passes the SAME guardrail instance.
    guardrail = dos_task_guardrail(str(repo), expect=[CommitClaim()])
    ok1, reason = guardrail(_task_output("done"))
    assert ok1 is False and "no commit beyond" in reason
    (repo / "fix.txt").write_text("f\n", encoding="utf-8")
    _git(repo, "add", "fix.txt")
    _git(repo, "commit", "-q", "-m", "the actual fix")
    ok2, _ = guardrail(_task_output("done (for real)"))
    assert ok2 is True


def test_abstain_never_burns_a_retry(tmp_path: Path) -> None:
    bare = tmp_path / "no-repo"
    bare.mkdir()
    guardrail = dos_task_guardrail(str(bare), expect=[CommitClaim()])
    ok, value = guardrail(_task_output("done"))
    assert ok is True  # could-not-tell passes (fail-to-abstain), never fails


def test_no_claims_passes(repo: Path) -> None:
    guardrail = dos_task_guardrail(str(repo))
    out = _task_output("free prose, nothing declared")
    ok, value = guardrail(out)
    assert ok is True and value is out


def test_plain_string_output_is_duck_typed(repo: Path) -> None:
    # No .raw attribute at all — the adapter falls back to str(output).
    guardrail = dos_task_guardrail(str(repo), expect=[FileClaim("missing.md")])
    ok, reason = guardrail("just a string")
    assert ok is False and "missing.md" in reason


def test_extractor_seam_passthrough(repo: Path) -> None:
    def extract(text: str):
        return [FileClaim("named-in-output.md")] if "named-in-output" in text else []

    guardrail = dos_task_guardrail(str(repo), extract=extract)
    ok, reason = guardrail(_task_output("wrote named-in-output.md"))
    assert ok is False and "named-in-output.md" in reason
    ok2, _ = guardrail(_task_output("did something unrelated"))
    assert ok2 is True
