"""Pin the SHIPPED residual-review surface — kernel module, `dos review`, and MCP.

Issue #211 promoted the `examples/residual_review/` experiment to a first-class
kernel module (`dos.residual_review`), a `dos review` CLI verb, and a `dos_review`
MCP tool. These tests are the done-condition made executable:

  1. The pure projection bands correctly: a `subject-only` commit -> RESIDUAL, a
     `diff-witnessed` one -> CLEARED, an `ABSTAIN` one -> UNVERIFIABLE.
  2. The `dos review` verb exits NON-ZERO iff a RESIDUAL band exists (the CI
     "human-needed-here" gate docs/358 promised) — and exit 2 on an unreadable
     range, never a falsely-clean 0.
  3. The kernel module imports NO host / names no vendor (the layering litmus the
     rest of the kernel holds).
  4. The bands carry ZERO new trust: they are a pure re-projection of the shipped
     `commit_audit` verdict, not a recomputation.

The pure-projection band rules are exercised exhaustively in the example's own
suite (`examples/residual_review/test_residual_review.py`), which now imports the
SAME kernel module; these tests pin the kernel/CLI/MCP surface specifically.
"""
from __future__ import annotations

import ast
import inspect
import os
import subprocess

import pytest

from dos import residual_review as rr
from dos.commit_audit import ClaimKind, ClaimVerdict, Verdict, Witness


# --- helpers -----------------------------------------------------------------

def _v(sha, verdict, witness, kind=ClaimKind.CODE_EFFECT, source=(), reason="r"):
    """A synthetic ClaimVerdict — the kernel's output, hand-built for the pure layer."""
    return ClaimVerdict(
        sha=sha, verdict=verdict, claim_kind=kind, witness=witness,
        reason=reason, source_files=tuple(source),
    )


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


def _seed_repo(root):
    """A 3-commit repo: a witnessed feat, a subject-only (empty) fix, a no-claim chore.

    Returns the three shas oldest-first. The fix is an `--allow-empty` `fix:` — a
    code-effect claim with NO diff, the canonical RESIDUAL. The chore makes no
    checkable claim, the canonical UNVERIFIABLE.
    """
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _git(root, "add", "mod.py")
    _git(root, "commit", "-qm", "feat: add the f() helper")
    feat = _git(root, "rev-parse", "HEAD").stdout.strip()
    _git(root, "commit", "-q", "--allow-empty", "-m", "fix: resolve the off-by-one")
    fix = _git(root, "rev-parse", "HEAD").stdout.strip()
    (root / "NOTES.md").write_text("notes\n", encoding="utf-8")
    _git(root, "add", "NOTES.md")
    _git(root, "commit", "-qm", "chore: jot a note")
    chore = _git(root, "rev-parse", "HEAD").stdout.strip()
    return feat, fix, chore


def _run_review(workspace, *argv):
    """Invoke `dos review` in-process; return (exit_code, captured_stdout)."""
    from dos import cli
    import io
    import contextlib

    args = cli.build_parser().parse_args(
        ["--workspace", str(workspace), "review", *argv])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = args.func(args)
    return code, buf.getvalue()


# --- 1. the pure projection bands correctly ----------------------------------

def test_subject_only_is_residual():
    """The headline: a claim the diff did NOT witness is the human's 100%."""
    v = _v("bbb", Verdict.CLAIM_UNWITNESSED, Witness.SUBJECT_ONLY,
           reason="code verb but 0 source files touched")
    plan = rr.plan_review([v], "range")
    assert [i.sha for i in plan.residual] == ["bbb"]
    assert plan.cleared == [] and plan.unverifiable == []


def test_diff_witnessed_is_cleared():
    """A diff-witnessed OK claim costs ~0 review attention — Band 0."""
    v = _v("aaa", Verdict.OK, Witness.DIFF_WITNESSED, source=("src/foo.py",))
    plan = rr.plan_review([v], "range")
    assert [i.sha for i in plan.cleared] == ["aaa"]
    assert plan.residual == [] and plan.unverifiable == []
    assert plan.cleared_rate == 1.0


def test_abstain_is_unverifiable_not_residual():
    """An ABSTAIN (no claim) must NOT pollute the must-read residual."""
    v = _v("ccc", Verdict.ABSTAIN, Witness.ABSTAIN, kind=ClaimKind.NONE,
           reason="subject makes no checkable claim")
    plan = rr.plan_review([v], "range")
    assert [i.sha for i in plan.unverifiable] == ["ccc"]
    assert plan.residual == [] and plan.checkable == 0


# --- 2. the verb exits non-zero IFF a residual exists ------------------------

def test_verb_exits_nonzero_iff_residual_exists(tmp_path):
    """The CI gate (docs/358): `dos review` exits 1 over a range with a RESIDUAL,
    and 0 over a range with none. This is the issue's witness-of-done."""
    repo = tmp_path / "repo"
    repo.mkdir()
    feat, fix, chore = _seed_repo(repo)

    # The range feat..fix isolates the subject-only fix -> a residual exists -> exit 1.
    code_resid, out_resid = _run_review(repo, f"{feat}..{fix}")
    assert code_resid == 1, f"residual range must exit 1, got {code_resid}\n{out_resid}"
    assert fix[:9] in out_resid

    # A range whose only commit is the witnessed feat -> no residual -> exit 0.
    parent = _git(repo, "rev-parse", f"{feat}~1").stdout.strip() if _has_parent(repo, feat) else None
    if parent:
        code_clean, _ = _run_review(repo, f"{parent}..{feat}")
        assert code_clean == 0, f"witnessed-only range must exit 0, got {code_clean}"

    # An empty-but-valid range is clean -> exit 0 (NOT a contract error).
    code_empty, _ = _run_review(repo, f"{fix}..{fix}")
    assert code_empty == 0


def _has_parent(repo, sha):
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", f"{sha}~1"],
                       capture_output=True, text=True)
    return r.returncode == 0


def test_verb_exit_2_on_unreadable_range(tmp_path):
    """A bad range is a CONTRACT ERROR (exit 2), never a falsely-clean exit 0.
    `audit_range` returns [] for both an unreadable and an empty range, so the
    verb must probe the range itself to tell them apart."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    code, _ = _run_review(repo, "no-such-ref-xyz..HEAD")
    assert code == 2


def test_walk_path_also_gates_on_residual(tmp_path):
    """`--walk` renders the residual as cards and still exits 1 iff a residual exists."""
    repo = tmp_path / "repo"
    repo.mkdir()
    feat, fix, chore = _seed_repo(repo)
    code, out = _run_review(repo, "--walk", f"{feat}..{fix}")
    assert code == 1
    assert "card" in out and fix[:9] in out


# --- 3. the kernel module imports no host / names no vendor (litmus) ----------

def test_module_imports_no_host_or_vendor():
    """`dos.residual_review` is a KERNEL leaf: it must name no host directory /
    lane / commit prefix and no vendor as a code identifier — the same litmus the
    rest of the kernel holds. (Risk-surface tokens like 'auth' live in a string
    table, not as identifiers, so the AST check below does not see them.)"""
    src = inspect.getsource(rr)
    tree = ast.parse(src)
    ids: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            ids.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            ids.add(node.attr.lower())
    forbidden = {"job", "apply", "tailor", "claude", "gemini", "codex", "openai",
                 "anthropic", "gpt"}
    leaked = {tok for tok in forbidden if tok in ids}
    assert not leaked, f"dos.residual_review names a host/vendor in code: {leaked}"


def test_module_imports_only_kernel_and_stdlib():
    """The only non-stdlib import is `dos.commit_audit`.

    The VCS reads for ranges, subjects, and review-card diffstats live at the
    CLI/MCP/example boundaries. The kernel surface stands on the SHIPPED verdict
    and recomputes no rung.
    """
    src = inspect.getsource(rr)
    tree = ast.parse(src)
    modules = {n.module for n in ast.walk(tree)
               if isinstance(n, ast.ImportFrom) and n.module}
    non_stdlib = {m for m in modules if m.startswith("dos")}
    assert non_stdlib == {"dos.commit_audit"}, non_stdlib


# --- 4. zero new trust: a pure re-projection of the shipped verdict -----------

def _repo_has_history(n: int = 30) -> bool:
    """True iff the repo this test lives in has >= n commits — robust to a worktree
    `.git` file (vs a `.git` dir), so the soundness test actually RUNS in a worktree."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        out = subprocess.run(["git", "-C", root, "rev-list", "--count", "HEAD"],
                             capture_output=True, text=True, check=False)
        return out.returncode == 0 and int(out.stdout.strip() or 0) >= n
    except (OSError, ValueError):
        return False


@pytest.mark.skipif(not _repo_has_history(), reason="needs real git history")
def test_projection_equals_the_shipped_verdict():
    """Every residual the surface produces is a sha the shipped `audit_range`
    graded as NOT (witnessed AND OK) — the projection invents no residual and
    hides none."""
    from dos.commit_audit import audit_range

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rng = "HEAD~30..HEAD"
    verdicts = audit_range(rng, root=root)
    plan = rr.plan_review(verdicts, rng)

    cleared = {v.sha for v in verdicts
               if v.verdict is Verdict.OK
               and v.witness in (Witness.DIFF_WITNESSED, Witness.DATA_WITNESSED)}
    abstain = {v.sha for v in verdicts if v.verdict is Verdict.ABSTAIN}
    expected_residual = {v.sha for v in verdicts} - cleared - abstain

    assert {i.sha for i in plan.cleared} == cleared
    assert {i.sha for i in plan.residual} == expected_residual
    assert {i.sha for i in plan.unverifiable} == abstain


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
