"""Tests for `dos.vcs` — the pluggable VCS evidence seam (docs/360).

Three layers:
  1. `NullVcs` + the resolver — PURE, no git: the honest-empty backend, built-ins
     resolve first and are unshadowable, an unknown name fails loud, `active_vcs`
     reads the configured name.
  2. `GitBackend` against a real temp repo — every core method + the optional
     `read_blob` capability return the right shape, and degrade (not crash) on a
     non-git dir / unknown ref.
  3. `git_delta` through the seam — the public `{sha, subject}` dict shape and the
     empty-list contract are UNCHANGED (the slice-1 invariant: the seam is invisible
     to existing callers).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dos import git_delta
from dos.vcs import (
    Commit,
    FileDelta,
    GitBackend,
    NullVcs,
    VcsBackend,
    active_vcs,
    active_vcs_names,
    resolve_vcs,
)


# --- layer 1: NullVcs + the resolver (pure, no git) ------------------------


def test_nullvcs_is_a_vcsbackend():
    assert isinstance(NullVcs(), VcsBackend)


def test_nullvcs_every_read_is_honest_empty():
    n = NullVcs()
    assert n.commits_since("abc") == []
    assert n.recent_commits(10) == []
    assert n.log_subjects(limit=50) == []
    assert n.files_in_commit("abc") is None
    assert n.is_ancestor("abc") is None
    assert n.head_sha() is None
    assert n.commit_meta("HEAD") is None
    assert n.read_blob("abc", "x.py") is None
    assert n.history_search(kind="pickaxe") is None


def test_resolve_builtins_bound_to_root(tmp_path: Path):
    assert isinstance(resolve_vcs("git", root=tmp_path), GitBackend)
    assert isinstance(resolve_vcs("null", root=tmp_path), NullVcs)


def test_resolve_unknown_fails_loud_with_known_list(tmp_path: Path):
    with pytest.raises(ValueError) as ei:
        resolve_vcs("mercurial", root=tmp_path)
    msg = str(ei.value)
    assert "mercurial" in msg
    assert "git" in msg and "null" in msg  # the known list is surfaced


def test_active_vcs_defaults_to_git(tmp_path: Path):
    # No cfg, no DISPATCH_* — the default backend is git.
    assert active_vcs(root=tmp_path).name == "git"


def test_active_vcs_reads_cfg_backend_name(tmp_path: Path):
    class _Cfg:
        vcs_backend = "null"

    assert active_vcs(root=tmp_path, cfg=_Cfg()).name == "null"


def test_active_vcs_empty_backend_name_falls_back_to_git(tmp_path: Path):
    class _Cfg:
        vcs_backend = ""  # an empty/missing declaration → git, not a crash

    assert active_vcs(root=tmp_path, cfg=_Cfg()).name == "git"


def test_active_vcs_names_lists_builtins_first():
    names = active_vcs_names()
    assert names[:2] == ["git", "null"]


def test_commit_to_dict_shape():
    assert Commit(sha="abc123", subject="feat: x").to_dict() == {
        "sha": "abc123",
        "subject": "feat: x",
    }


# --- layer 2: GitBackend against a real repo -------------------------------


def _git_ok() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


gitmark = pytest.mark.skipif(not _git_ok(), reason="git not available")


def _init_repo(d: Path) -> str:
    """Init a 2-commit repo; return the first commit's full sha."""
    def g(*a):
        return subprocess.run(
            ["git", "-C", str(d), *a], capture_output=True, text=True
        )
    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    g("config", "commit.gpgsign", "false")
    (d / "src").mkdir()
    (d / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    g("add", "src/app.py")
    g("commit", "-qm", "docs/AT: AT1 — initial")
    first = g("rev-parse", "HEAD").stdout.strip()
    (d / "src" / "two.py").write_text("y = 2\n", encoding="utf-8")
    g("add", "src/two.py")
    g("commit", "-qm", "feat: second commit")
    return first


@gitmark
def test_gitbackend_commits_since(tmp_path: Path):
    first = _init_repo(tmp_path)
    rows = GitBackend(root=tmp_path).commits_since(first)
    assert len(rows) == 1  # only the second commit is after `first`
    assert rows[0].subject == "feat: second commit"


@gitmark
def test_gitbackend_recent_commits_newest_first(tmp_path: Path):
    _init_repo(tmp_path)
    rows = GitBackend(root=tmp_path).recent_commits(10)
    assert [r.subject for r in rows] == ["feat: second commit", "docs/AT: AT1 — initial"]


@gitmark
def test_gitbackend_log_subjects_oneline_and_paths(tmp_path: Path):
    _init_repo(tmp_path)
    gb = GitBackend(root=tmp_path)
    subs = [c.subject for c in gb.log_subjects(limit=10)]
    assert "feat: second commit" in subs
    # path-restricted: only the commit that touched two.py
    only_two = gb.log_subjects(limit=10, paths=("src/two.py",))
    assert [c.subject for c in only_two] == ["feat: second commit"]


@gitmark
def test_gitbackend_log_subjects_bodies(tmp_path: Path):
    _init_repo(tmp_path)
    rows = GitBackend(root=tmp_path).log_subjects(limit=10, bodies=True)
    assert any(r.body for r in rows)
    assert any(r.subject == "feat: second commit" for r in rows)


@gitmark
def test_gitbackend_files_in_commit(tmp_path: Path):
    _init_repo(tmp_path)
    files = GitBackend(root=tmp_path).files_in_commit("HEAD")
    assert files == ["src/two.py"]


@gitmark
def test_gitbackend_files_in_commit_unknown_is_none(tmp_path: Path):
    _init_repo(tmp_path)
    assert GitBackend(root=tmp_path).files_in_commit("deadbeef") is None


@gitmark
def test_gitbackend_is_ancestor_three_valued(tmp_path: Path):
    first = _init_repo(tmp_path)
    gb = GitBackend(root=tmp_path)
    assert gb.is_ancestor(first) is True            # first IS an ancestor of HEAD
    assert gb.is_ancestor("HEAD", of=first) is False  # HEAD is NOT an ancestor of first
    assert gb.is_ancestor("deadbeef") is None       # unknown sha → unresolvable


@gitmark
def test_gitbackend_head_sha(tmp_path: Path):
    _init_repo(tmp_path)
    gb = GitBackend(root=tmp_path)
    full = gb.head_sha()
    short = gb.head_sha(short=True)
    assert full and short
    assert full.startswith(short)


@gitmark
def test_gitbackend_commit_meta(tmp_path: Path):
    _init_repo(tmp_path)
    meta = GitBackend(root=tmp_path).commit_meta("HEAD")
    assert meta is not None
    assert meta.subject == "feat: second commit"


@gitmark
def test_gitbackend_read_blob(tmp_path: Path):
    _init_repo(tmp_path)
    blob = GitBackend(root=tmp_path).read_blob("HEAD", "src/two.py")
    assert blob == b"y = 2\n"
    assert GitBackend(root=tmp_path).read_blob("HEAD", "nope.py") is None


@gitmark
def test_gitbackend_degrades_on_non_git_dir(tmp_path: Path):
    # No `git init` — every read is the honest empty/None, never a raise.
    gb = GitBackend(root=tmp_path)
    assert gb.commits_since("abc") == []
    assert gb.recent_commits(10) == []
    assert gb.log_subjects(limit=10) == []
    assert gb.files_in_commit("HEAD") is None
    assert gb.is_ancestor("abc") is None
    assert gb.head_sha() is None
    assert gb.commit_meta("HEAD") is None


# --- layer 3: git_delta through the seam — shape unchanged -----------------


@gitmark
def test_git_delta_shape_unchanged_through_seam(tmp_path: Path):
    first = _init_repo(tmp_path)
    rows = git_delta.commits_since(first, root=tmp_path)
    # Still a list of {sha, subject} dicts — the pre-seam contract.
    assert isinstance(rows, list)
    assert rows and set(rows[0]) == {"sha", "subject"}
    assert rows[0]["subject"] == "feat: second commit"
    assert git_delta.count_commits_since(first, root=tmp_path) == 1
    recent = git_delta.recent_commits(2, root=tmp_path)
    assert [r["subject"] for r in recent] == [
        "feat: second commit",
        "docs/AT: AT1 — initial",
    ]


def test_git_delta_empty_start_is_empty(tmp_path: Path):
    assert git_delta.commits_since("", root=tmp_path) == []
    assert git_delta.count_commits_since("", root=tmp_path) == 0


# --- the slice-3 reads: commits_in_range + commit_diffstat -----------------


@gitmark
def test_gitbackend_commits_in_range_full_sha(tmp_path: Path):
    first = _init_repo(tmp_path)
    gb = GitBackend(root=tmp_path)
    rows = gb.commits_in_range(f"{first}..HEAD", full_sha=True)
    assert len(rows) == 1
    assert len(rows[0].sha) == 40  # full 40-char sha, not the short form
    # commits_since is the special case of commits_in_range
    assert [c.subject for c in gb.commits_since(first)] == [r.subject for r in rows]


@gitmark
def test_gitbackend_commit_diffstat(tmp_path: Path):
    _init_repo(tmp_path)
    deltas = GitBackend(root=tmp_path).commit_diffstat("HEAD")
    assert deltas is not None
    assert len(deltas) == 1
    d = deltas[0]
    assert d.path == "src/two.py"
    assert d.added == 1 and d.removed == 0


@gitmark
def test_gitbackend_commit_diffstat_unknown_is_none(tmp_path: Path):
    _init_repo(tmp_path)
    assert GitBackend(root=tmp_path).commit_diffstat("deadbeef") is None


def test_nullvcs_optional_caps_are_none():
    n = NullVcs()
    assert n.commits_in_range("a..b") == []
    assert n.commit_diffstat("abc") is None
