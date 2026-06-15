"""Pin the git-cleanup classifier (`scripts/git_cleanup.py`).

The tool removes stray fleet worktrees + landed branches — a destructive sweep over
a hot, multi-loop tree. Its whole safety story rests on the PURE classifier: given a
record `(head_sha, landed, uncommitted_count, is_main, branch, …)`, does it return
KEEP / PRUNE / REFUSE correctly? These tests drive that classifier with synthetic
records so they assert the DOS distrust rules WITHOUT a real repo (no worktrees
created, no git run):

  * a worktree with ANY uncommitted file → REFUSE (never discard unsaved work);
  * a detached worktree whose HEAD is NOT landed → REFUSE (commits live nowhere else);
  * only a LANDED + clean + DETACHED worktree → PRUNE;
  * the main tree and a named-branch worktree → always KEEP;
  * a landed local branch → PRUNE via `git branch -d` (never `-D`);
  * an unlanded / current / protected branch → KEEP;
  * dry-run is the default — the plan-builder produces actions but main() runs none
    of them without --apply.

The destructive git itself (`worktree remove`, `branch -d`) is never exercised here;
it is the thin shell over the classifier these tests pin. The `-d`-not-`-D` rule is
asserted on the emitted action argv.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HELPER = Path(__file__).resolve().parents[1] / "scripts" / "git_cleanup.py"
_spec = importlib.util.spec_from_file_location("git_cleanup", _HELPER)
gc = importlib.util.module_from_spec(_spec)
# Register before exec so dataclasses can resolve the module's own annotations
# (frozen dataclasses look the module up by __module__ during _process_class).
sys.modules["git_cleanup"] = gc
_spec.loader.exec_module(gc)


# --------------------------------------------------------------------------- #
# Worktree classification
# --------------------------------------------------------------------------- #

def _wt(**kw) -> "gc.WorktreeRecord":
    """A worktree record with safe defaults (landed + clean + detached candidate)."""
    base = dict(
        path="C:/work/fleet-runs/wt-issue-42",
        head_sha="abc123def456",
        branch=None,
        is_main=False,
        landed=True,
        uncommitted_count=0,
    )
    base.update(kw)
    return gc.WorktreeRecord(**base)


def test_main_worktree_always_keep():
    v = gc.classify_worktree(_wt(is_main=True, path="C:/work/repo"))
    assert v.verdict == gc.KEEP
    assert v.action == []


def test_named_branch_worktree_keep_even_when_landed():
    # A worktree checked out on a named branch is a durable handle — KEEP, not PRUNE,
    # even when its HEAD is landed and the tree is clean.
    v = gc.classify_worktree(_wt(branch="lane/docs/branching-cutover", landed=True))
    assert v.verdict == gc.KEEP
    assert "named branch" in v.reason


def test_uncommitted_worktree_refuses():
    v = gc.classify_worktree(_wt(uncommitted_count=1))
    assert v.verdict == gc.REFUSE
    assert "uncommitted" in v.reason
    assert v.action == []


def test_uncommitted_refuses_even_when_landed():
    # The dirty rule outranks landed-ness: a landed but dirty tree still REFUSEs
    # (the unsaved edit is the thing we must not discard — the #117 cli.py case).
    v = gc.classify_worktree(_wt(uncommitted_count=3, landed=True))
    assert v.verdict == gc.REFUSE
    assert "uncommitted" in v.reason


def test_unlanded_detached_worktree_refuses():
    v = gc.classify_worktree(_wt(landed=False, uncommitted_count=0))
    assert v.verdict == gc.REFUSE
    assert "ancestor" in v.reason
    assert v.action == []


def test_landed_clean_detached_worktree_prunes():
    v = gc.classify_worktree(_wt(landed=True, uncommitted_count=0, branch=None))
    assert v.verdict == gc.PRUNE
    assert v.action[:2] == ["worktree", "remove"]
    assert v.action[-1] == "C:/work/fleet-runs/wt-issue-42"


def test_dirty_outranks_unlanded_in_reason():
    # Both refuse, but a dirty AND unlanded tree reports the dirty reason first
    # (the more urgent fix — save the work before worrying about landing).
    v = gc.classify_worktree(_wt(landed=False, uncommitted_count=2))
    assert v.verdict == gc.REFUSE
    assert "uncommitted" in v.reason


# --------------------------------------------------------------------------- #
# Branch classification
# --------------------------------------------------------------------------- #

def _br(**kw) -> "gc.BranchRecord":
    base = dict(
        name="scoreboard/reframe-10x",
        head_sha="def789abc012",
        is_current=False,
        landed=True,
        is_protected=False,
    )
    base.update(kw)
    return gc.BranchRecord(**base)


def test_landed_branch_prunes_with_lowercase_d():
    v = gc.classify_branch(_br(landed=True))
    assert v.verdict == gc.PRUNE
    # The -d-not-D safety rule, asserted on the emitted argv.
    assert v.action == ["branch", "-d", "scoreboard/reframe-10x"]
    assert "-D" not in v.action


def test_unlanded_branch_keeps():
    v = gc.classify_branch(_br(name="rsi/phase1-ratchet-kernel", landed=False))
    assert v.verdict == gc.KEEP
    assert v.action == []


def test_current_branch_keeps():
    v = gc.classify_branch(_br(is_current=True, landed=True))
    assert v.verdict == gc.KEEP
    assert "checked-out" in v.reason


def test_protected_branch_keeps():
    v = gc.classify_branch(_br(name="master", is_protected=True, landed=True))
    assert v.verdict == gc.KEEP
    assert v.action == []


def test_protected_set_covers_trunk_and_pages():
    assert "master" in gc._PROTECTED_BRANCHES
    assert "main" in gc._PROTECTED_BRANCHES
    assert "gh-pages" in gc._PROTECTED_BRANCHES


# --------------------------------------------------------------------------- #
# Plan shape + dry-run discipline
# --------------------------------------------------------------------------- #

def test_no_action_ever_uses_force_delete():
    # Sweep every verdict the classifiers can emit: no action may contain -D.
    samples = [
        gc.classify_worktree(_wt(is_main=True)),
        gc.classify_worktree(_wt(branch="feat/x")),
        gc.classify_worktree(_wt(uncommitted_count=1)),
        gc.classify_worktree(_wt(landed=False)),
        gc.classify_worktree(_wt(landed=True, uncommitted_count=0)),
        gc.classify_branch(_br(landed=True)),
        gc.classify_branch(_br(landed=False)),
        gc.classify_branch(_br(is_protected=True)),
    ]
    for v in samples:
        assert "-D" not in v.action, f"{v.target} emitted a force-delete: {v.action}"
        assert "--force" not in v.action


def test_verdict_vocabulary_is_closed():
    # Only the three tokens exist — a typo'd verdict would break the table renderer
    # and any consumer grepping the JSON.
    assert {gc.KEEP, gc.PRUNE, gc.REFUSE} == {"KEEP", "PRUNE", "REFUSE"}


def test_dry_run_default_runs_no_actions(monkeypatch):
    # main() without --apply must never call apply_plan. We stub build_plan to a
    # fixed plan with a PRUNE row and assert apply_plan is not invoked.
    fake_plan = {
        "workspace": "X", "trunk": "origin/master",
        "verdicts": [{"target": "wt-x", "kind": "worktree", "verdict": gc.PRUNE,
                      "reason": "r", "action": ["worktree", "remove", "wt-x"]}],
        "counts": {gc.KEEP: 0, gc.PRUNE: 1, gc.REFUSE: 0},
    }
    monkeypatch.setattr(gc, "build_plan", lambda root: fake_plan)
    monkeypatch.setattr(gc, "repo_root", lambda ws: Path("."))

    called = {"apply": False}
    monkeypatch.setattr(gc, "apply_plan", lambda plan, root: called.__setitem__("apply", True) or [])

    rc = gc.main(["--workspace", "."])
    assert rc == 0
    assert called["apply"] is False


def test_apply_invokes_actions(monkeypatch):
    # With --apply, apply_plan IS called and a clean run exits 0.
    fake_plan = {
        "workspace": "X", "trunk": "origin/master",
        "verdicts": [{"target": "wt-x", "kind": "worktree", "verdict": gc.PRUNE,
                      "reason": "r", "action": ["worktree", "remove", "wt-x"]}],
        "counts": {gc.KEEP: 0, gc.PRUNE: 1, gc.REFUSE: 0},
    }
    monkeypatch.setattr(gc, "build_plan", lambda root: fake_plan)
    monkeypatch.setattr(gc, "repo_root", lambda ws: Path("."))
    monkeypatch.setattr(
        gc, "apply_plan",
        lambda plan, root: [{"target": "wt-x", "kind": "worktree",
                             "argv": ["worktree", "remove", "wt-x"],
                             "returncode": 0, "ok": True, "output": ""}],
    )
    rc = gc.main(["--workspace", ".", "--apply"])
    assert rc == 0


def test_apply_failure_exits_nonzero(monkeypatch):
    # A failed --apply step (e.g. git -d refusing an un-merged branch) → exit 1 so a
    # calling loop notices.
    fake_plan = {
        "workspace": "X", "trunk": "origin/master", "verdicts": [],
        "counts": {gc.KEEP: 0, gc.PRUNE: 0, gc.REFUSE: 0},
    }
    monkeypatch.setattr(gc, "build_plan", lambda root: fake_plan)
    monkeypatch.setattr(gc, "repo_root", lambda ws: Path("."))
    monkeypatch.setattr(
        gc, "apply_plan",
        lambda plan, root: [{"target": "br-x", "kind": "branch",
                             "argv": ["branch", "-d", "br-x"],
                             "returncode": 1, "ok": False, "output": "not fully merged"}],
    )
    rc = gc.main(["--workspace", ".", "--apply"])
    assert rc == 1


def test_apply_only_acts_on_prune_rows(monkeypatch):
    # apply_plan must skip KEEP/REFUSE rows entirely — only PRUNE actions run.
    plan = {
        "workspace": "X", "trunk": "origin/master",
        "verdicts": [
            {"target": "keep-me", "kind": "worktree", "verdict": gc.KEEP,
             "reason": "r", "action": []},
            {"target": "refuse-me", "kind": "worktree", "verdict": gc.REFUSE,
             "reason": "r", "action": []},
            {"target": "prune-me", "kind": "branch", "verdict": gc.PRUNE,
             "reason": "r", "action": ["branch", "-d", "prune-me"]},
        ],
        "counts": {gc.KEEP: 1, gc.PRUNE: 1, gc.REFUSE: 1},
    }
    ran: list[list[str]] = []
    monkeypatch.setattr(gc, "_git", lambda args, root: (ran.append(args) or (0, "")))
    results = gc.apply_plan(plan, Path("."))
    # Only the branch -d ran, plus the trailing `worktree prune -v`.
    assert ["branch", "-d", "prune-me"] in ran
    assert ["worktree", "prune", "-v"] in ran
    assert not any(a[:2] == ["worktree", "remove"] for a in ran)
    assert all(r["ok"] for r in results)


def test_render_table_is_stringable():
    plan = {
        "workspace": "X", "trunk": "origin/master",
        "verdicts": [
            {"target": "wt-a", "kind": "worktree", "verdict": gc.PRUNE,
             "reason": "landed + clean", "action": ["worktree", "remove", "wt-a"]},
            {"target": "wt-b", "kind": "worktree", "verdict": gc.REFUSE,
             "reason": "dirty", "action": []},
        ],
        "counts": {gc.KEEP: 0, gc.PRUNE: 1, gc.REFUSE: 1},
    }
    out = gc.render_table(plan)
    assert "PRUNE" in out and "REFUSE" in out
    assert "wt-a" in out and "wt-b" in out
    assert "--apply" in out  # the dry-run hint fires because a PRUNE row exists
