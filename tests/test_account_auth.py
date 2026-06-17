"""account_auth — the vendor-neutral seam that makes the account switcher pluggable.

The seat picker is agent-blind; only the AUTH glue (env-var names, token/creds file
names, token prefix, enroll flow) varies per agent. These tests pin: the by-name
resolution (in-tree + fail-loud on unknown), the three shipped specs' data, and a
PARITY assertion that the Claude spec mirrors the vendored switcher's real env output
— so the seam and the switcher cannot silently disagree about Claude's identity.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dos import account_auth as aa
from dos.drivers import account_switcher as sw


# --------------------------------------------------------------------------- #
# Resolution — by name, in-tree first, fail-LOUD on unknown
# --------------------------------------------------------------------------- #
def test_default_is_claude():
    assert aa.resolve_account_auth(None).agent_kind == "claude"
    assert aa.resolve_account_auth("").agent_kind == "claude"
    assert aa.DEFAULT_AGENT_KIND == "claude"


def test_resolve_is_case_insensitive():
    assert aa.resolve_account_auth("CLAUDE").agent_kind == "claude"
    assert aa.resolve_account_auth("  Codex ").agent_kind == "codex"


def test_resolve_unknown_raises_with_known_set():
    with pytest.raises(ValueError) as ei:
        aa.resolve_account_auth("not-an-agent")
    msg = str(ei.value)
    assert "not-an-agent" in msg
    # names the known set and refuses to silently fall back (the wrong-identity guard)
    assert "claude" in msg and "codex" in msg
    assert "Refusing to fall back" in msg


def test_available_lists_the_shipped_agents():
    names = aa.available_account_auths()
    assert {"claude", "codex", "gemini"}.issubset(set(names))
    assert names == sorted(names)  # stable, sorted


# --------------------------------------------------------------------------- #
# The shipped specs' data
# --------------------------------------------------------------------------- #
def test_claude_spec_fields():
    s = aa.resolve_account_auth("claude")
    assert s.config_dir_env == "CLAUDE_CONFIG_DIR"
    assert s.token_env == "CLAUDE_CODE_OAUTH_TOKEN"
    assert s.token_file_name == ".oauth-token"
    assert s.creds_file_name == ".credentials.json"
    assert s.token_prefix == "sk-ant-oat"
    assert s.supports_config_dir_isolation is True
    assert "setup-token" in s.enroll_methods


def test_codex_spec_isolates_via_codex_home():
    s = aa.resolve_account_auth("codex")
    assert s.config_dir_env == "CODEX_HOME"
    assert s.creds_file_name == "auth.json"
    assert s.token_env == "OPENAI_API_KEY"
    assert s.supports_config_dir_isolation is True


def test_gemini_spec_is_honest_about_no_config_dir_env():
    s = aa.resolve_account_auth("gemini")
    assert s.config_dir_env == ""               # Gemini has no config-dir override env
    assert s.supports_config_dir_isolation is False
    # env_overrides then emits only the token env, never a no-op config-dir key
    env = s.env_overrides("/ignored", token="g-key")
    assert "CONFIG" not in json.dumps(env)
    assert env == {"GEMINI_API_KEY": "g-key"}


# --------------------------------------------------------------------------- #
# Pure methods — validate_token + env_overrides
# --------------------------------------------------------------------------- #
def test_validate_token_prefix_enforced_for_claude():
    s = aa.resolve_account_auth("claude")
    assert s.validate_token("sk-ant-oat01-realtoken") is True
    assert s.validate_token("nope") is False
    assert s.validate_token("") is False
    assert s.validate_token(None) is False


def test_validate_token_no_prefix_accepts_any_nonempty():
    s = aa.resolve_account_auth("codex")  # token_prefix == ""
    assert s.validate_token("anything-nonempty") is True
    assert s.validate_token("   ") is False


def test_env_overrides_omits_token_when_absent():
    s = aa.resolve_account_auth("claude")
    assert s.env_overrides("/cfg") == {"CLAUDE_CONFIG_DIR": "/cfg"}
    assert s.env_overrides("/cfg", token="sk-ant-oat01-x") == {
        "CLAUDE_CONFIG_DIR": "/cfg", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-x"}


def test_launch_env_fn_is_a_capability_not_a_name():
    """The rich, disk-aware launch-env builder is a spec CAPABILITY a consumer reads
    (`spec.launch_env_fn`) — so it never has to branch `if kind == "claude"` (the
    vendor-blindness litmus). Claude ships one (the docs/380 env_for); others don't."""
    claude = aa.resolve_account_auth("claude")
    assert claude.launch_env_fn is not None
    assert callable(claude.launch_env_fn)
    assert aa.resolve_account_auth("codex").launch_env_fn is None
    assert aa.resolve_account_auth("gemini").launch_env_fn is None


# --------------------------------------------------------------------------- #
# launch_env — the rotate-on-wall env-override contract (docs/386 §4)
# --------------------------------------------------------------------------- #
def test_launch_env_prefers_the_disk_aware_capability_for_claude(tmp_path):
    """`spec.launch_env(account)` routes through the spec's launch_env_fn when set —
    for Claude that IS the switcher's docs/380 `env_for`, so the rotate-on-wall env is
    byte-identical to what the launcher would emit for the same seat. The consumer
    never branches on the agent name; it reads the CAPABILITY through one method."""
    cfg = tmp_path / "acctB"
    cfg.mkdir()
    (cfg / ".oauth-token").write_text("sk-ant-oat01-faketoken\n", encoding="utf-8")
    acct = sw.Account(name="acctB", config_dir=str(cfg))
    spec = aa.resolve_account_auth("claude")
    assert spec.launch_env(acct, config_dir=str(cfg)) == sw.env_for(acct)


def test_launch_env_falls_to_the_pure_floor_without_a_capability():
    """An agent with no launch_env_fn (Codex) uses the pure env_overrides floor from
    config_dir (+ token) — the same shared shape every agent has."""
    spec = aa.resolve_account_auth("codex")
    assert spec.launch_env(config_dir="/cfg", token="k") == {
        "CODEX_HOME": "/cfg", "OPENAI_API_KEY": "k"}
    # an account object without a capability is ignored; the floor still applies
    assert spec.launch_env(object(), config_dir="/cfg") == {"CODEX_HOME": "/cfg"}


def test_launch_env_is_fail_loud_on_a_missing_identity(tmp_path):
    """The capability path propagates the switcher's OriginError when the seat's
    config dir does not exist — relaunching under a logged-out identity is the exact
    hazard the contract prevents, so it must fail rather than emit an empty env."""
    acct = sw.Account(name="gone", config_dir=str(tmp_path / "does-not-exist"))
    spec = aa.resolve_account_auth("claude")
    with pytest.raises(sw.OriginError):
        spec.launch_env(acct, config_dir=acct.config_dir)


# --------------------------------------------------------------------------- #
# PARITY — the Claude spec must mirror the vendored switcher's real behavior
# --------------------------------------------------------------------------- #
def test_claude_spec_matches_switcher_token_only_env(tmp_path):
    """A token-only Claude config dir: switcher.env_for emits CLAUDE_CONFIG_DIR +
    CLAUDE_CODE_OAUTH_TOKEN. The spec's env-var NAMES must be exactly those keys —
    else `dos accounts env` (spec-routed) and the switcher disagree on Claude's
    identity. This ties the seam to the switcher's actual output, not a copy."""
    cfg = tmp_path / "acctA"
    cfg.mkdir()
    (cfg / ".oauth-token").write_text("sk-ant-oat01-faketoken\n", encoding="utf-8")
    acct = sw.Account(name="acctA", config_dir=str(cfg))
    env = sw.env_for(acct)  # the switcher's own (docs/380-aware) env builder
    spec = aa.resolve_account_auth("claude")
    assert spec.config_dir_env in env          # CLAUDE_CONFIG_DIR present
    assert spec.token_env in env               # CLAUDE_CODE_OAUTH_TOKEN spliced (token-only)
    assert set(env) == {spec.config_dir_env, spec.token_env}


def test_claude_spec_token_prefix_matches_switcher_validation(tmp_path):
    """The spec's token_prefix must match what the switcher's write_account_token
    accepts/rejects — a single source of truth for 'is this a Claude token'."""
    spec = aa.resolve_account_auth("claude")
    acct = sw.Account(name="a", config_dir=str(tmp_path / "a"))
    # a token the spec validates is accepted by the switcher's writer
    good = "sk-ant-oat01-realtoken-xyz"
    assert spec.validate_token(good) is True
    assert sw.write_account_token(acct, good)  # does not raise
    # a token the spec rejects is rejected by the switcher's writer too
    bad = "not-a-real-token"
    assert spec.validate_token(bad) is False
    with pytest.raises(sw.TokenError):
        sw.write_account_token(acct, bad)
