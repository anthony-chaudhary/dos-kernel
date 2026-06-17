"""Tests for the VCS-read seam's CONFIG selector (docs/360 slice 2).

The `dos.vcs` backend is chosen by `SubstrateConfig.vcs_backend` (a name), loaded
from `dos.toml [vcs] backend`. These pin:
  * the default is `git` (byte-compatible) and `cfg.vcs()` binds the backend to root;
  * `[vcs] backend = "null"` selects the honest-empty `NullVcs`, and a no-VCS read
    through it is empty everywhere git_delta feeds — without crashing;
  * an unknown backend name is a host's own-claim error: warned + base-kept (the
    shared warn-and-fall-back posture), never a silent fall-back to git that hides
    the typo.
"""
from __future__ import annotations

from pathlib import Path

from dos import config as c
from dos import git_delta
from dos.config import default_config, load_workspace_config
from dos.vcs import GitBackend, NullVcs


def _write_toml(repo: Path, body: str) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "dos.toml").write_text(body, encoding="utf-8")


def test_default_vcs_backend_is_git():
    cfg = default_config()
    assert cfg.vcs_backend == "git"
    assert isinstance(cfg.vcs(), GitBackend)


def test_cfg_vcs_binds_root(tmp_path: Path):
    cfg = default_config(tmp_path)
    backend = cfg.vcs()
    assert backend.name == "git"
    # The backend is bound to this config's root (GitBackend stores it privately).
    assert getattr(backend, "_root", None) == tmp_path


def test_env_var_constant_exists():
    # The subprocess hand-off env var (consumed in the oracle slice) is declared now.
    assert c.ENV_VCS_BACKEND == "DISPATCH_VCS_BACKEND"


def test_toml_selects_null_backend(tmp_path: Path):
    _write_toml(tmp_path, '[vcs]\nbackend = "null"\n')
    cfg = load_workspace_config(tmp_path)
    assert cfg.vcs_backend == "null"
    assert isinstance(cfg.vcs(), NullVcs)


def test_null_backend_yields_empty_evidence_without_crash(tmp_path: Path):
    # A workspace declaring the null backend reads "no history" everywhere — the
    # honest no-VCS floor, a first-class backend rather than a returncode accident.
    _write_toml(tmp_path, '[vcs]\nbackend = "null"\n')
    cfg = load_workspace_config(tmp_path)
    backend = cfg.vcs()
    assert backend.commits_since("anything") == []
    assert backend.recent_commits(10) == []
    assert backend.head_sha() is None
    assert backend.is_ancestor("abc") is None


def test_absent_vcs_table_keeps_git_default(tmp_path: Path):
    _write_toml(tmp_path, '[reasons]\n')  # a dos.toml with no [vcs] table
    cfg = load_workspace_config(tmp_path)
    assert cfg.vcs_backend == "git"


def test_unknown_backend_warns_and_falls_back(tmp_path: Path):
    _write_toml(tmp_path, '[vcs]\nbackend = "mercurial"\n')
    warnings: list[tuple[str, str]] = []
    cfg = load_workspace_config(tmp_path, warn=lambda label, msg: warnings.append((label, msg)))
    # The unknown backend is rejected (a host own-claim error), base git kept.
    assert cfg.vcs_backend == "git"
    assert any(label == "vcs" and "mercurial" in msg for label, msg in warnings)


def test_non_string_backend_warns_and_falls_back(tmp_path: Path):
    _write_toml(tmp_path, "[vcs]\nbackend = 42\n")
    warnings: list[tuple[str, str]] = []
    cfg = load_workspace_config(tmp_path, warn=lambda label, msg: warnings.append((label, msg)))
    assert cfg.vcs_backend == "git"
    assert any(label == "vcs" for label, _ in warnings)


def test_bootstrap_reads_vcs_backend_from_env(tmp_path, monkeypatch):
    """The oracle→phase_shipped subprocess hand-off: the child's bootstrap installs
    the backend NAME the parent set in ENV_VCS_BACKEND (docs/360 slice 5)."""
    import dataclasses
    from dos import phase_shipped as ps

    base = dataclasses.replace(default_config(tmp_path), vcs_backend="git")
    c.set_active(base)
    try:
        monkeypatch.setenv(c.ENV_VCS_BACKEND, "null")
        ps._bootstrap_active_config()
        # The child now serves the parent's chosen backend, not its git default.
        assert c.active().vcs_backend == "null"
    finally:
        c.set_active(default_config())  # restore the process-active config
