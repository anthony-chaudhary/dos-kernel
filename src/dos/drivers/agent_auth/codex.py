"""codex — the OpenAI Codex CLI ``AccountAuthSpec``.

Codex stores its state under ``CODEX_HOME`` (default ``~/.codex``), overridable per
process — so per-account isolation works exactly like Claude's ``CLAUDE_CONFIG_DIR``:
give each account its own ``CODEX_HOME`` and the seat picker spreads across them
unchanged. File-based credentials live in ``<CODEX_HOME>/auth.json`` (treat it like a
password — it holds access tokens); an API-key child authenticates via
``OPENAI_API_KEY``.

Enroll is interactive ``codex login`` (writes ``auth.json``); there is no
print-a-token flow like Claude's ``setup-token``, so ``token_file_name`` is empty and
``token_prefix`` is unset (an OAuth ``auth.json`` token and an ``sk-`` API key have
different shapes — refuse to reject either). Verified against the Codex config
reference (CODEX_HOME / auth.json), 2026-06.
"""
from __future__ import annotations

from dos.account_auth import AccountAuthSpec

SPEC = AccountAuthSpec(
    agent_kind="codex",
    config_dir_env="CODEX_HOME",
    token_env="OPENAI_API_KEY",
    token_file_name="",          # no sibling token store — creds live in auth.json
    creds_file_name="auth.json",
    token_prefix="",             # OAuth token vs sk- API key differ; accept any non-empty
    enroll_methods=("login",),
    enroll_hint='CODEX_HOME=<dir> codex login  # interactive; writes <dir>/auth.json',
)
