"""The pre-commit ecosystem surface: `.pre-commit-hooks.yaml` + the range driver.

Two layers, mirroring docs/304:

1. The hooks MANIFEST must stay consumable and stage-correct. The v0.22.0 file
   ran commit-audit at `stages: [commit]` — git's pre-commit stage, which fires
   BEFORE the commit object exists, so `HEAD` (the CLI default) was the PARENT
   of the commit being made: an off-by-one that audited the wrong commit and
   blocked an innocent one while naming the previous. The pin here is the
   regression guard: no hook may run at a stage where the judged commit does
   not yet exist.

2. The range driver (`dos.drivers.pre_commit`) must map pre-commit's env
   contract (PRE_COMMIT_FROM_REF / PRE_COMMIT_TO_REF) onto the unchanged
   `dos commit-audit` CLI and pass the exit code through untouched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from dos.drivers import pre_commit as pc

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / ".pre-commit-hooks.yaml"

# The only stages at which the commit being judged EXISTS when the hook runs.
# (pre-commit/commit/commit-msg/prepare-commit-msg all fire before the commit
# object is created — at those stages HEAD is the parent: the off-by-one.)
_STAGES_WHERE_THE_COMMIT_EXISTS = {"pre-push", "post-commit", "manual"}


def _hooks() -> list[dict]:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def _hook(hook_id: str) -> dict:
    matches = [h for h in _hooks() if h["id"] == hook_id]
    assert matches, f"hook id {hook_id!r} missing from .pre-commit-hooks.yaml"
    return matches[0]


# --- layer 1: the manifest ---------------------------------------------------


def test_manifest_parses_to_the_two_hook_ids():
    ids = [h["id"] for h in _hooks()]
    assert ids == ["dos-commit-audit", "dos-commit-audit-warn"]


def test_no_hook_runs_at_a_stage_where_head_is_the_parent():
    """The docs/304 regression pin: every declared stage must be one where the
    commit under judgment already exists."""
    for h in _hooks():
        stages = h.get("stages")
        assert stages, f"{h['id']}: declare stages explicitly (default is pre-commit)"
        bad = set(stages) - _STAGES_WHERE_THE_COMMIT_EXISTS
        assert not bad, (
            f"{h['id']} declares stage(s) {sorted(bad)} — there the commit "
            f"object does not exist yet and commit-audit would judge the parent "
            f"(the off-by-one docs/304 fixed). Allowed: "
            f"{sorted(_STAGES_WHERE_THE_COMMIT_EXISTS)}"
        )


def test_gate_hook_contract():
    h = _hook("dos-commit-audit")
    assert h["stages"] == ["pre-push"]
    assert h["language"] == "python"
    assert h["entry"] == "python -m dos.drivers.pre_commit"
    # A whole-commit verdict, not a per-file linter.
    assert h["pass_filenames"] is False
    # Load-bearing: an `--allow-empty` over-claim changes zero files, and a
    # hook with no matching files is skipped unless always_run — i.e. the gate
    # would skip exactly the forgery it exists to catch.
    assert h["always_run"] is True


def test_warn_hook_contract():
    h = _hook("dos-commit-audit-warn")
    assert h["stages"] == ["post-commit"]
    assert h["entry"] == "python -m dos.drivers.pre_commit --warn-only"
    assert h["pass_filenames"] is False
    # post-commit passes no filenames → without always_run the hook never fires.
    assert h["always_run"] is True
    # A warn-only hook always exits 0, and pre-commit hides a passing hook's
    # output → without verbose the advisory report is silent forever.
    assert h["verbose"] is True


# --- layer 2: the range driver (env → ref mapping, pure) ---------------------


def test_range_from_both_env_vars():
    env = {"PRE_COMMIT_FROM_REF": "abc123", "PRE_COMMIT_TO_REF": "def456"}
    assert pc.resolve_ref(env) == "abc123..def456"


def test_new_branch_zero_sha_falls_back_to_tip():
    env = {"PRE_COMMIT_FROM_REF": "0" * 40, "PRE_COMMIT_TO_REF": "def456"}
    assert pc.resolve_ref(env) == "def456"


def test_sha256_zero_sha_also_counts_as_absent():
    env = {"PRE_COMMIT_FROM_REF": "0" * 64, "PRE_COMMIT_TO_REF": "def456"}
    assert pc.resolve_ref(env) == "def456"


def test_missing_from_ref_falls_back_to_tip():
    assert pc.resolve_ref({"PRE_COMMIT_TO_REF": "def456"}) == "def456"


def test_no_env_defaults_to_head():
    assert pc.resolve_ref({}) == "HEAD"
    assert pc.resolve_ref({"PRE_COMMIT_FROM_REF": "abc123"}) == "HEAD"


def test_main_delegates_with_resolved_ref_and_passthrough(monkeypatch):
    seen: dict = {}

    def fake_cli_main(argv):
        seen["argv"] = argv
        return 7

    from dos import cli
    monkeypatch.setattr(cli, "main", fake_cli_main)
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", "aaa")
    monkeypatch.setenv("PRE_COMMIT_TO_REF", "bbb")
    rc = pc.main(["--warn-only", "--json"])
    # The exit code passes through untouched — the verdict IS the exit code.
    assert rc == 7
    assert seen["argv"] == ["commit-audit", "aaa..bbb", "--warn-only", "--json"]


# --- layer 3: end-to-end against a real tmp repo ----------------------------


def _git_ok() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


gitmark = pytest.mark.skipif(not _git_ok(), reason="git not available")


def _init_repo(d: Path) -> str:
    """A repo with one honest base commit; returns the base commit's SHA."""
    def g(*a):
        return subprocess.run(
            ["git", "-C", str(d), *a], capture_output=True, text=True)
    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    g("config", "commit.gpgsign", "false")
    (d / "src").mkdir()
    (d / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    g("add", "src/app.py")
    g("commit", "-qm", "initial")
    return g("rev-parse", "HEAD").stdout.strip()


@gitmark
def test_end_to_end_over_claim_in_push_range_exits_1(tmp_path, monkeypatch):
    base = _init_repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "--allow-empty",
         "-m", "implement the cache"], capture_output=True)
    tip = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", base)
    monkeypatch.setenv("PRE_COMMIT_TO_REF", tip)
    monkeypatch.setenv("DISPATCH_WORKSPACE", str(tmp_path))
    assert pc.main([]) == 1


@gitmark
def test_end_to_end_clean_range_exits_0(tmp_path, monkeypatch):
    base = _init_repo(tmp_path)
    (tmp_path / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "src/app.py"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm",
                    "fix: bump x to 2 in app.py"], capture_output=True)
    tip = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", base)
    monkeypatch.setenv("PRE_COMMIT_TO_REF", tip)
    monkeypatch.setenv("DISPATCH_WORKSPACE", str(tmp_path))
    assert pc.main([]) == 0


@gitmark
def test_end_to_end_warn_only_never_blocks(tmp_path, monkeypatch):
    base = _init_repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "--allow-empty",
         "-m", "implement the cache"], capture_output=True)
    tip = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", base)
    monkeypatch.setenv("PRE_COMMIT_TO_REF", tip)
    monkeypatch.setenv("DISPATCH_WORKSPACE", str(tmp_path))
    assert pc.main(["--warn-only"]) == 0
