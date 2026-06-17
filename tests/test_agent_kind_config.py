"""Tests for the account-auth seam's CONFIG selector (docs/386).

The agent the account switcher emits auth env for is chosen by
`SubstrateConfig.agent_kind` (a name), loaded from `dos.toml [agent] kind`. These
pin — mirroring the `[vcs] backend` selector (`test_vcs_config.py`):
  * the default is `claude` (byte-compatible with today);
  * `[agent] kind = "codex"` selects the codex spec, resolvable via account_auth;
  * an unknown kind is a host's own-claim error: warned + base-kept (the shared
    warn-and-fall-back posture), never a silent fall-back that would launch a fleet
    under the wrong agent's auth env.
"""
from __future__ import annotations

from pathlib import Path

from dos import account_auth as aa
from dos.config import default_config, load_workspace_config


def _write_toml(repo: Path, body: str) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "dos.toml").write_text(body, encoding="utf-8")


def test_default_agent_kind_is_claude():
    cfg = default_config()
    assert cfg.agent_kind == "claude"
    # and it resolves to the shipped claude spec
    assert aa.resolve_account_auth(cfg.agent_kind).config_dir_env == "CLAUDE_CONFIG_DIR"


def test_toml_selects_codex(tmp_path: Path):
    _write_toml(tmp_path, '[agent]\nkind = "codex"\n')
    cfg = load_workspace_config(tmp_path)
    assert cfg.agent_kind == "codex"
    assert aa.resolve_account_auth(cfg.agent_kind).config_dir_env == "CODEX_HOME"


def test_absent_agent_table_keeps_claude_default(tmp_path: Path):
    _write_toml(tmp_path, '[reasons]\n')  # a dos.toml with no [agent] table
    cfg = load_workspace_config(tmp_path)
    assert cfg.agent_kind == "claude"


def test_unknown_kind_warns_and_falls_back(tmp_path: Path, capsys):
    _write_toml(tmp_path, '[agent]\nkind = "not-an-agent"\n')
    cfg = load_workspace_config(tmp_path)
    assert cfg.agent_kind == "claude"  # base-kept, never silently the wrong agent
    err = capsys.readouterr().err
    assert "agent" in err and "not-an-agent" in err


def test_non_string_kind_warns_and_falls_back(tmp_path: Path):
    _write_toml(tmp_path, '[agent]\nkind = 42\n')
    cfg = load_workspace_config(tmp_path)
    assert cfg.agent_kind == "claude"
