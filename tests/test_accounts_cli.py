"""`dos accounts` CLI — the agent-aware seat seam (docs/386).

End-to-end over the real CLI: the `agent` inspection verb, the per-agent `env`
emission (Claude keeps the switcher's rich builder; Codex emits CODEX_HOME), and the
workspace `dos.toml [agent] kind` default. Subprocess-driven so the argparse wiring
and the cmd_accounts routing are exercised exactly as a host runs them.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _env(extra: dict | None = None) -> dict:
    e = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "NO_COLOR": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    if extra:
        e.update(extra)
    return e


def _cli(*argv: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "from dos.cli import main; raise SystemExit(main())", *argv],
        capture_output=True, text=True, encoding="utf-8", env=_env(env))


def _roster(tmp_path: Path, *names: str) -> Path:
    lines = ["accounts:"]
    for n in names:
        d = tmp_path / n
        d.mkdir(parents=True, exist_ok=True)
        lines.append(f"  - name: {n}")
        lines.append(f"    config_dir: '{d}'")
    f = tmp_path / "roster.yaml"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


def test_accounts_agent_default_is_claude(tmp_path):
    cp = _cli("accounts", "agent", "--workspace", str(tmp_path), "--json")
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert data["agent_kind"] == "claude"
    assert data["config_dir_env"] == "CLAUDE_CONFIG_DIR"
    assert {"claude", "codex", "gemini"}.issubset(set(data["available"]))


def test_accounts_agent_override_codex(tmp_path):
    cp = _cli("accounts", "agent", "--agent-kind", "codex",
              "--workspace", str(tmp_path), "--json")
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert data["agent_kind"] == "codex"
    assert data["config_dir_env"] == "CODEX_HOME"
    assert data["supports_config_dir_isolation"] is True


def test_accounts_agent_gemini_isolation_is_honest(tmp_path):
    cp = _cli("accounts", "agent", "--agent-kind", "gemini",
              "--workspace", str(tmp_path), "--json")
    data = json.loads(cp.stdout)
    assert data["supports_config_dir_isolation"] is False
    assert data["config_dir_env"] == ""


def test_accounts_env_codex_emits_codex_home(tmp_path):
    f = _roster(tmp_path, "cdxA")
    cp = _cli("accounts", "env", "--name", "cdxA", "--agent-kind", "codex",
              "--accounts-file", str(f), "--workspace", str(tmp_path), "--json")
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert data["agent_kind"] == "codex"
    assert "CODEX_HOME" in data["env"]
    assert data["env"]["CODEX_HOME"].endswith("cdxA")


def test_accounts_env_claude_uses_config_dir(tmp_path):
    f = _roster(tmp_path, "acctA")
    # a fresh creds file → the switcher emits config-dir only (docs/380, no token)
    (tmp_path / "acctA" / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "t", "expiresAt": 9_999_999_999_000}}),
        encoding="utf-8")
    cp = _cli("accounts", "env", "--name", "acctA",
              "--accounts-file", str(f), "--workspace", str(tmp_path), "--json")
    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert data["agent_kind"] == "claude"
    assert "CLAUDE_CONFIG_DIR" in data["env"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in data["env"]  # deferred to the fresh creds file


def test_dos_toml_agent_kind_sets_default(tmp_path):
    (tmp_path / "dos.toml").write_text('[agent]\nkind = "codex"\n', encoding="utf-8")
    cp = _cli("accounts", "agent", "--workspace", str(tmp_path), "--json")
    assert cp.returncode == 0, cp.stderr
    assert json.loads(cp.stdout)["agent_kind"] == "codex"  # workspace default, no override
