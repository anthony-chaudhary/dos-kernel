"""gemini — the Google Gemini CLI ``AccountAuthSpec`` (honest about one gap).

Gemini CLI keeps its state under ``~/.gemini/`` (``settings.json``, OAuth creds in
``oauth_creds.json``) and authenticates via ``GEMINI_API_KEY`` or an interactive
OAuth login. Unlike Claude (``CLAUDE_CONFIG_DIR``) and Codex (``CODEX_HOME``), Gemini
CLI exposes **no documented config-dir-override env var**, so per-account isolation
cannot be expressed purely through ``env_overrides`` — ``config_dir_env`` is empty and
``supports_config_dir_isolation`` is False. A host that wants isolated Gemini seats
must isolate another way (a per-seat ``HOME``, a wrapper) until the CLI grows a
config-dir env var. Shipping this spec — with that gap VISIBLE rather than papered
over — is the honest form of "support arbitrary agents": the registry resolves
``gemini``, the seat picker still spreads across rostered Gemini accounts, and the
one capability it lacks is named, not hidden.

Verified against the Gemini CLI auth/config docs (``~/.gemini``, ``GEMINI_API_KEY``),
2026-06; ``oauth_creds.json`` is the known OAuth creds filename under that dir.
"""
from __future__ import annotations

from dos.account_auth import AccountAuthSpec

SPEC = AccountAuthSpec(
    agent_kind="gemini",
    config_dir_env="",            # Gemini CLI has no config-dir-override env var (the gap)
    token_env="GEMINI_API_KEY",
    token_file_name="",
    creds_file_name="oauth_creds.json",
    token_prefix="",              # a Google API key shape this seam should not police
    enroll_methods=("login", "api-key"),
    enroll_hint=(
        "gemini  # interactive OAuth login (writes ~/.gemini/oauth_creds.json), "
        "or set GEMINI_API_KEY"
    ),
)
