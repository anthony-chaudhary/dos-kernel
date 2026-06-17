"""claude — the Claude Code ``AccountAuthSpec`` (the verified baseline).

The default agent (``dos.account_auth.DEFAULT_AGENT_KIND``). Its literals mirror the
vendored ``account_switcher`` core exactly, so resolving ``agent_kind="claude"`` and
asking the spec for env/token/validation gives the SAME answers the switcher's own
``CLAUDE_CONFIG_DIR`` / ``CLAUDE_CODE_OAUTH_TOKEN`` / ``.oauth-token`` / ``sk-ant-oat``
constants always have. The spec is the generic FLOOR; the switcher keeps Claude's
richer launch behavior (the docs/380 fresh-creds deferral in ``env_for``) — a host
that needs that calls ``account_switcher.env_for`` for a Claude seat, and the spec's
``env_overrides`` is the simple shared shape every agent has.

Enrollment: ``setup-token`` mints a ~1-year token (printed, no creds file) stored at
``<config_dir>/.oauth-token``; ``login`` writes the auto-refreshing
``.credentials.json``. (docs/380 / the account-switcher enroll recipe.)
"""
from __future__ import annotations

from dos.account_auth import AccountAuthSpec

SPEC = AccountAuthSpec(
    agent_kind="claude",
    config_dir_env="CLAUDE_CONFIG_DIR",
    token_env="CLAUDE_CODE_OAUTH_TOKEN",
    token_file_name=".oauth-token",
    creds_file_name=".credentials.json",
    token_prefix="sk-ant-oat",  # `claude setup-token` → sk-ant-oat01-… (version-tolerant)
    enroll_methods=("setup-token", "login"),
    enroll_hint=(
        'CLAUDE_CONFIG_DIR=<dir> claude setup-token  '
        "# mints + prints a ~1yr token; store it to <dir>/.oauth-token"
    ),
)
