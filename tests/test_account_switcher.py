"""Tests for the vendored account-switcher core (dos.drivers.account_switcher).

These are the PURE tests, lifted byte-faithfully from the canonical core's suite
(``dos-private/tools/test_claude_account_switcher.py``) and re-pointed at the
public kernel's vendored driver. They inject a fake ``probe_fn`` and a fixed
``now_epoch``, use tmp_path config dirs, and never touch the network or any host
module. Keep in sync with the canonical suite when the core is re-vendored.

Run:  python -m pytest tests/test_account_switcher.py -q
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

from dos.drivers import account_switcher as sw


# --------------------------------------------------------------------------- #
# Fakes / fixtures
# --------------------------------------------------------------------------- #
@dataclass
class FakeProbe:
    """A stand-in for a host's live rate-limit probe state (the ProbeLike shape)."""

    status: str
    utilization: float
    reset_at_epoch: Optional[int]
    http_status: int = 200

    @property
    def allowed(self) -> bool:
        return self.status in ("allowed", "allowed_warning") and self.http_status != 429


def _enroll(config_dir: Path, *, expires_at_ms: int | None = 9_999_999_999_000) -> None:
    """Write a minimal valid .credentials.json into a config dir."""
    config_dir.mkdir(parents=True, exist_ok=True)
    oauth = {"accessToken": "tok-" + config_dir.name}
    if expires_at_ms is not None:
        oauth["expiresAt"] = expires_at_ms
    (config_dir / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": oauth}), encoding="utf-8"
    )


def _acct(name: str, config_dir: Path, *, enabled: bool = True) -> sw.Account:
    return sw.Account(name=name, config_dir=str(config_dir), enabled=enabled)


def _enroll_token(config_dir: Path, *, token: str = "sk-ant-oat01-faketoken123456") -> None:
    """Persist a setup-token for an account (the headless-pool enrollment path)."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / ".oauth-token").write_text(token + "\n", encoding="utf-8")


NOW = 1_700_000_000.0


# --------------------------------------------------------------------------- #
# account_state — the per-account fold (disk + injected probe)
# --------------------------------------------------------------------------- #
def test_no_creds_is_needs_enroll(tmp_path):
    a = _acct("a", tmp_path / "a")  # dir doesn't exist
    st = sw.account_state(a, probe_fn=None, now_epoch=NOW)
    assert st.kind == sw.ACCT_NEEDS_ENROLL
    assert st.creds_present is False
    assert st.pickable is False


def test_disabled_account_is_disabled(tmp_path):
    cfg = tmp_path / "a"
    _enroll(cfg)
    a = _acct("a", cfg, enabled=False)
    st = sw.account_state(a, probe_fn=None, now_epoch=NOW)
    assert st.kind == sw.ACCT_DISABLED
    assert st.pickable is False


def test_expired_token_is_needs_enroll(tmp_path):
    cfg = tmp_path / "a"
    _enroll(cfg, expires_at_ms=int((NOW - 100) * 1000))  # expired before NOW
    st = sw.account_state(_acct("a", cfg), probe_fn=None, now_epoch=NOW)
    assert st.kind == sw.ACCT_NEEDS_ENROLL
    assert st.token_expired is True


def test_enrolled_no_probe_is_serving_failopen(tmp_path):
    cfg = tmp_path / "a"
    _enroll(cfg)
    st = sw.account_state(_acct("a", cfg), probe_fn=None, now_epoch=NOW)
    assert st.kind == sw.ACCT_SERVING  # fail-open: no probe → assume serving


def test_present_but_unknown_expiry_is_usable(tmp_path):
    cfg = tmp_path / "a"
    _enroll(cfg, expires_at_ms=None)  # no expiresAt → CLI auto-refreshes
    st = sw.account_state(_acct("a", cfg), probe_fn=None, now_epoch=NOW)
    assert st.kind == sw.ACCT_SERVING


def test_probe_rejected_is_walled(tmp_path):
    cfg = tmp_path / "a"
    _enroll(cfg)
    probe = lambda creds, now: FakeProbe("rejected", 1.0, int(NOW + 3600), http_status=429)
    st = sw.account_state(_acct("a", cfg), probe_fn=probe, now_epoch=NOW)
    assert st.kind == sw.ACCT_WALLED
    assert st.reset_at_epoch == int(NOW + 3600)


def test_probe_high_util_is_near_cap(tmp_path):
    cfg = tmp_path / "a"
    _enroll(cfg)
    probe = lambda creds, now: FakeProbe("allowed_warning", 0.95, None)
    st = sw.account_state(_acct("a", cfg), probe_fn=probe, now_epoch=NOW, near_cap_util=0.9)
    assert st.kind == sw.ACCT_NEAR_CAP
    assert st.pickable is True


# --------------------------------------------------------------------------- #
# setup-token enrollment path (the durable headless-pool path)
# --------------------------------------------------------------------------- #
def test_setup_token_alone_enrolls(tmp_path):
    cfg = tmp_path / "a"
    _enroll_token(cfg)  # no .credentials.json at all
    st = sw.account_state(_acct("a", cfg), probe_fn=None, now_epoch=NOW)
    assert st.kind == sw.ACCT_SERVING  # fail-open serving, enrolled via token
    assert "setup-token" in st.detail


def test_setup_token_takes_precedence_over_expired_creds(tmp_path):
    cfg = tmp_path / "a"
    _enroll(cfg, expires_at_ms=int((NOW - 100) * 1000))  # expired login creds
    _enroll_token(cfg)  # but a live setup-token is present
    st = sw.account_state(_acct("a", cfg), probe_fn=None, now_epoch=NOW)
    assert st.kind == sw.ACCT_SERVING  # token wins → not needs_enroll
    assert st.token_expired is False


def test_write_account_token_roundtrip(tmp_path):
    a = _acct("a", tmp_path / "a")
    path = sw.write_account_token(a, "  sk-ant-oat01-realtoken-xyz\n")
    assert path == sw.account_token_path(a)
    assert sw.read_account_token(a) == "sk-ant-oat01-realtoken-xyz"


def test_write_account_token_rejects_non_token(tmp_path):
    a = _acct("a", tmp_path / "a")
    with pytest.raises(sw.TokenError):
        sw.write_account_token(a, "not-a-token")
    assert sw.read_account_token(a) is None  # nothing written


def test_env_for_emits_oauth_token_when_present(tmp_path):
    cfg = tmp_path / "claude-a"
    _enroll_token(cfg, token="sk-ant-oat01-tok-a")
    env = sw.env_for(_acct("a", cfg))
    assert env["CLAUDE_CONFIG_DIR"] == str(cfg)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-tok-a"


# --------------------------------------------------------------------------- #
# token vs. auto-refreshing creds file — the live-session propagation fix.
# A static CLAUDE_CODE_OAUTH_TOKEN freezes a live process at launch-time creds
# (a running process's env cannot be updated). When a present+unexpired
# `.credentials.json` is there, the launcher must defer to it — Claude
# auto-refreshes that file in place, so a refresh propagates to live sessions.
# --------------------------------------------------------------------------- #
def test_env_for_prefers_refreshing_creds_over_static_token(tmp_path):
    cfg = tmp_path / "claude-a"
    _enroll(cfg)          # present + unexpired .credentials.json (auto-refreshes)
    _enroll_token(cfg)    # AND a stored setup-token
    env = sw.env_for(_acct("a", cfg))
    # config-dir only: the live session reads the auto-refreshing file, so a
    # token/login refresh on disk propagates instead of being shadowed + frozen.
    assert env == {"CLAUDE_CONFIG_DIR": str(cfg)}
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_env_for_injects_token_when_creds_expired(tmp_path):
    cfg = tmp_path / "claude-a"
    _enroll(cfg, expires_at_ms=int((1_700_000_000.0 - 100) * 1000))  # expired creds
    _enroll_token(cfg, token="sk-ant-oat01-tok-a")
    # The on-disk access token is stale → the file can't serve, so the durable
    # setup-token must still be spliced (else the `-p` child auths as not-logged-in).
    env = sw.env_for(_acct("a", cfg))
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-tok-a"


def test_account_env_overrides_skips_token_when_fresh_creds(tmp_path):
    cfg = tmp_path / "claude-a"
    _enroll(cfg)
    _enroll_token(cfg)
    env = sw.account_env_overrides(_acct("a", cfg))
    assert env == {"CLAUDE_CONFIG_DIR": str(cfg)}  # defers to the refreshing file


def test_account_env_overrides_injects_token_when_token_only(tmp_path):
    cfg = tmp_path / "claude-a"
    _enroll_token(cfg, token="sk-ant-oat01-tok-a")  # token only, no creds file
    env = sw.account_env_overrides(_acct("a", cfg))
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-tok-a"


def test_has_fresh_login_creds_reflects_creds_state(tmp_path):
    cfg = tmp_path / "claude-a"
    a = _acct("a", cfg)
    assert sw._has_fresh_login_creds(a, now_epoch=NOW) is False  # no file
    _enroll(cfg)
    assert sw._has_fresh_login_creds(a, now_epoch=NOW) is True   # present+unexpired
    _enroll(cfg, expires_at_ms=int((NOW - 100) * 1000))          # now expired
    assert sw._has_fresh_login_creds(a, now_epoch=NOW) is False


# --------------------------------------------------------------------------- #
# pick_account — the switcher decision
# --------------------------------------------------------------------------- #
def test_pick_first_serving_in_roster_order(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _enroll(a)
    _enroll(b)
    accounts = [_acct("a", a), _acct("b", b)]
    probe = lambda creds, now: FakeProbe("allowed", 0.1, None)
    pick = sw.pick_account(accounts, probe_fn=probe, now_epoch=NOW)
    assert pick.ok
    assert pick.account.name == "a"  # roster order preserved


def test_pick_skips_walled_to_serving(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _enroll(a)
    _enroll(b)
    accounts = [_acct("a", a), _acct("b", b)]

    def probe(creds, now):
        if creds.replace("\\", "/").endswith("/a/.credentials.json"):
            return FakeProbe("rejected", 1.0, int(NOW + 3600), http_status=429)
        return FakeProbe("allowed", 0.2, None)

    pick = sw.pick_account(accounts, probe_fn=probe, now_epoch=NOW)
    assert pick.ok
    assert pick.account.name == "b"  # a is walled → fall through to b


def test_pick_skips_needs_enroll(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    # a not enrolled (no creds), b enrolled
    _enroll(b)
    accounts = [_acct("a", a), _acct("b", b)]
    probe = lambda creds, now: FakeProbe("allowed", 0.1, None)
    pick = sw.pick_account(accounts, probe_fn=probe, now_epoch=NOW)
    assert pick.ok
    assert pick.account.name == "b"


def test_all_walled_picks_soonest_reset_with_wait(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _enroll(a)
    _enroll(b)
    accounts = [_acct("a", a), _acct("b", b)]
    reset_a = int(NOW + 7200)
    reset_b = int(NOW + 1800)  # b resets sooner

    def probe(creds, now):
        norm = creds.replace("\\", "/")
        if norm.endswith("/a/.credentials.json"):
            return FakeProbe("rejected", 1.0, reset_a, http_status=429)
        return FakeProbe("rejected", 1.0, reset_b, http_status=429)

    # Inject a deterministic wait function (no host backoff helper needed).
    pick = sw.pick_account(
        accounts, probe_fn=probe, now_epoch=NOW,
        wait_for_walled=lambda reset, now: int((reset or now) - now),
    )
    assert pick.ok is False  # caller must wait, not launch
    assert pick.account.name == "b"  # soonest reset
    assert pick.soonest_reset_epoch == reset_b
    assert pick.wait_seconds == 1800


def test_all_walled_default_wait_fallback_is_pure(tmp_path):
    # With NO wait_for_walled injected, the pure fallback uses the reset delta
    # (no host backoff module imported). b resets in 1800s → wait 1800.
    a, b = tmp_path / "a", tmp_path / "b"
    _enroll(a)
    _enroll(b)
    accounts = [_acct("a", a), _acct("b", b)]
    reset_b = int(NOW + 1800)

    def probe(creds, now):
        norm = creds.replace("\\", "/")
        if norm.endswith("/a/.credentials.json"):
            return FakeProbe("rejected", 1.0, int(NOW + 7200), http_status=429)
        return FakeProbe("rejected", 1.0, reset_b, http_status=429)

    pick = sw.pick_account(accounts, probe_fn=probe, now_epoch=NOW)
    assert pick.ok is False
    assert pick.account.name == "b"
    assert pick.wait_seconds == 1800  # pure delta fallback


def test_default_wait_no_reset_hint_is_floor():
    # No reset hint at all → the typical-window floor, never a crash.
    assert sw._default_wait_for_walled(None, NOW) == sw._NO_HINT_WAIT_SECONDS
    # Already-reset epoch → wait 0 (re-probe now).
    assert sw._default_wait_for_walled(int(NOW - 10), NOW) == 0


def test_near_cap_fallback_when_no_account_below_threshold(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _enroll(a)
    _enroll(b)
    accounts = [_acct("a", a), _acct("b", b)]

    def probe(creds, now):
        norm = creds.replace("\\", "/")
        # a at 98%, b at 92% — both >= 0.9, b lower
        return FakeProbe("allowed_warning", 0.98 if norm.endswith("/a/.credentials.json") else 0.92, None)

    pick = sw.pick_account(
        accounts, probe_fn=probe, now_epoch=NOW, policy=sw.RotationPolicy(near_cap_util=0.9)
    )
    assert pick.ok
    assert pick.account.name == "b"  # lowest utilization among near-cap


def test_failopen_none_probe_treats_as_serving(tmp_path):
    a = tmp_path / "a"
    _enroll(a)
    accounts = [_acct("a", a)]
    pick = sw.pick_account(accounts, probe_fn=lambda c, n: None, now_epoch=NOW)
    assert pick.ok  # None probe must never manufacture a wall
    assert pick.account.name == "a"


def test_empty_roster_picks_nothing(tmp_path):
    pick = sw.pick_account([], probe_fn=lambda c, n: None, now_epoch=NOW)
    assert pick.ok is False
    assert pick.account is None
    assert "no accounts" in pick.reason


def test_nothing_enrolled_names_the_gap(tmp_path):
    a = tmp_path / "a"  # not enrolled
    pick = sw.pick_account([_acct("a", a)], probe_fn=lambda c, n: None, now_epoch=NOW)
    assert pick.ok is False
    assert pick.account is None
    assert "enroll" in pick.reason.lower()


# --------------------------------------------------------------------------- #
# load_roster — fail-open parsing
# --------------------------------------------------------------------------- #
def test_load_roster_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv(sw.ACCOUNTS_FILE_ENV, str(tmp_path / "nope.yaml"))
    accounts, policy = sw.load_roster()
    assert accounts == []
    assert policy.near_cap_util == 0.9  # default policy


def test_load_roster_parses_accounts_and_policy(tmp_path, monkeypatch):
    f = tmp_path / "roster.yaml"
    f.write_text(
        "accounts:\n"
        "  - name: x\n"
        '    config_dir: "~/.claude-x"\n'
        "    email: x@example.com\n"
        "    enabled: true\n"
        "  - name: y\n"
        '    config_dir: "~/.claude-y"\n'
        "    enabled: false\n"
        "rotation:\n"
        "  order: by_reset\n"
        "  near_cap_util: 0.75\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(sw.ACCOUNTS_FILE_ENV, str(f))
    accounts, policy = sw.load_roster()
    assert [a.name for a in accounts] == ["x", "y"]
    assert accounts[1].enabled is False
    assert policy.near_cap_util == 0.75


def test_load_roster_malformed_is_empty(tmp_path, monkeypatch):
    f = tmp_path / "bad.yaml"
    f.write_text("accounts: [this is not: valid: yaml: : :", encoding="utf-8")
    monkeypatch.setenv(sw.ACCOUNTS_FILE_ENV, str(f))
    accounts, policy = sw.load_roster()
    assert accounts == []  # fail-open, never raises


def test_load_roster_skips_entries_missing_required_fields(tmp_path, monkeypatch):
    f = tmp_path / "roster.yaml"
    f.write_text(
        "accounts:\n"
        "  - name: ok\n"
        '    config_dir: "~/.claude-ok"\n'
        "  - email: no-name@example.com\n"  # missing name + config_dir
        "  - name: no-dir\n",  # missing config_dir
        encoding="utf-8",
    )
    monkeypatch.setenv(sw.ACCOUNTS_FILE_ENV, str(f))
    accounts, _ = sw.load_roster()
    assert [a.name for a in accounts] == ["ok"]


# --------------------------------------------------------------------------- #
# env_for — the launcher contract; missing dir is fail-LOUD
# --------------------------------------------------------------------------- #
def test_env_for_emits_config_dir(tmp_path):
    cfg = tmp_path / "claude-x"
    cfg.mkdir()
    env = sw.env_for(_acct("x", cfg))
    assert env == {"CLAUDE_CONFIG_DIR": str(cfg)}


def test_env_for_missing_dir_raises(tmp_path):
    with pytest.raises(sw.OriginError):
        sw.env_for(_acct("x", tmp_path / "does-not-exist"))


# --------------------------------------------------------------------------- #
# enroll_recipe — the one-time login bootstrap
# --------------------------------------------------------------------------- #
def test_enroll_recipe_setup_token_default(tmp_path):
    a = sw.Account(name="c10", config_dir=str(tmp_path / "c10"),
                   chrome_profile="Profile 7")
    lines = sw.enroll_recipe(a, save_token_cmd="store-token {name}")
    body = "\n".join(lines)
    assert "setup-token" in body
    assert "store-token c10" in body  # the token-persist step
    assert f'$env:CLAUDE_CONFIG_DIR = "{tmp_path / "c10"}"' in body
    assert "Profile 7" in body  # provenance comment
    assert "/login" not in body


def test_enroll_recipe_login_method(tmp_path):
    a = sw.Account(name="c10", config_dir=str(tmp_path / "c10"))
    lines = sw.enroll_recipe(a, method=sw.ENROLL_METHOD_LOGIN)
    body = "\n".join(lines)
    assert "/login" in body
    assert "setup-token" not in body


def test_enroll_recipe_expands_home():
    a = sw.Account(name="c10", config_dir="~/.claude-c10")
    lines = sw.enroll_recipe(a)
    cfg_line = next(l for l in lines if l.startswith("$env:CLAUDE_CONFIG_DIR"))
    assert "~" not in cfg_line
    assert str(Path("~/.claude-c10").expanduser()) in cfg_line


def test_enroll_recipe_unknown_method_raises():
    a = sw.Account(name="c10", config_dir="/tmp/c10")
    with pytest.raises(ValueError, match="unknown enroll method"):
        sw.enroll_recipe(a, method="oauth-magic")


# --------------------------------------------------------------------------- #
# serving_pool — the population answer (spread N workers across N windows)
# --------------------------------------------------------------------------- #
def test_serving_pool_returns_all_serving_in_roster_order(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for d in (a, b, c):
        _enroll(d)
    accounts = [_acct("a", a), _acct("b", b), _acct("c", c)]
    probe = lambda creds, now: FakeProbe("allowed", 0.1, None)
    pool = sw.serving_pool(accounts, probe_fn=probe, now_epoch=NOW)
    assert [x.name for x in pool] == ["a", "b", "c"]  # roster order preserved


def test_serving_pool_excludes_walled(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for d in (a, b, c):
        _enroll(d)
    accounts = [_acct("a", a), _acct("b", b), _acct("c", c)]

    def probe(creds, now):
        norm = creds.replace("\\", "/")
        if norm.endswith("/b/.credentials.json"):
            return FakeProbe("rejected", 1.0, None, http_status=429)
        return FakeProbe("allowed", 0.1, None)

    pool = sw.serving_pool(accounts, probe_fn=probe, now_epoch=NOW)
    names = [x.name for x in pool]
    assert "b" not in names
    assert set(names) == {"a", "c"}


def test_serving_pool_near_cap_tail_after_serving(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        _enroll(d)
    accounts = [_acct("a", a), _acct("b", b)]

    def probe(creds, now):
        norm = creds.replace("\\", "/")
        # a near-cap (0.95), b serves clean (0.1). near_cap default ~0.9.
        if norm.endswith("/a/.credentials.json"):
            return FakeProbe("allowed", 0.95, None)
        return FakeProbe("allowed", 0.1, None)

    pool = sw.serving_pool(accounts, probe_fn=probe, now_epoch=NOW)
    assert [x.name for x in pool] == ["b", "a"]  # clean serving first, near-cap tail


def test_serving_pool_empty_when_all_walled(tmp_path):
    a = tmp_path / "a"
    _enroll(a)
    accounts = [_acct("a", a)]
    probe = lambda creds, now: FakeProbe("rejected", 1.0, None, http_status=429)
    pool = sw.serving_pool(accounts, probe_fn=probe, now_epoch=NOW)
    assert pool == []  # nothing serves → caller keeps single-account default


def test_serving_pool_roundrobin_spread_distributes_8_across_5():
    pool = ["w", "x", "y", "z", "v"]
    assign = [pool[i % len(pool)] for i in range(8)]
    from collections import Counter
    counts = Counter(assign)
    assert max(counts.values()) <= 2
    assert set(counts) == set(pool)  # every serving window gets used


# --------------------------------------------------------------------------- #
# allocate_seats — the WINDOW-FILL optimisation (headroom-weighted seats)
# --------------------------------------------------------------------------- #
from collections import Counter as _Counter  # noqa: E402


def _counts(accts) -> dict:
    return dict(_Counter(a.name for a in accts))


def test_allocate_seats_equal_util_is_even_spread(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        _enroll(d)
    accounts = [_acct("a", a), _acct("b", b)]
    probe = lambda creds, now: FakeProbe("allowed", 0.2, None)
    seats = sw.allocate_seats(accounts, 4, probe_fn=probe, now_epoch=NOW)
    assert len(seats) == 4
    assert _counts(seats) == {"a": 2, "b": 2}


def test_allocate_seats_weights_by_headroom(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        _enroll(d)
    accounts = [_acct("a", a), _acct("b", b)]

    def probe(creds, now):
        norm = creds.replace("\\", "/")
        return FakeProbe("allowed", 0.10 if norm.endswith("/a/.credentials.json") else 0.70, None)

    seats = sw.allocate_seats(accounts, 8, probe_fn=probe, now_epoch=NOW)
    counts = _counts(seats)
    assert len(seats) == 8
    assert counts["a"] == 6 and counts["b"] == 2  # 0.9:0.3 apportionment


def test_allocate_seats_near_cap_window_seats_fewer(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        _enroll(d)
    accounts = [_acct("a", a), _acct("b", b)]

    def probe(creds, now):
        norm = creds.replace("\\", "/")
        return FakeProbe("allowed", 0.05 if norm.endswith("/a/.credentials.json") else 0.95, None)

    seats = sw.allocate_seats(accounts, 10, probe_fn=probe, now_epoch=NOW)
    counts = _counts(seats)
    assert counts["a"] > counts["b"]  # near-empty window seats the majority
    assert counts.get("b", 0) >= 1    # near-cap window not starved to 0


def test_allocate_seats_failopen_full_headroom(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        _enroll(d)
    accounts = [_acct("a", a), _acct("b", b)]
    seats = sw.allocate_seats(accounts, 4, probe_fn=lambda c, n: None, now_epoch=NOW)
    assert _counts(seats) == {"a": 2, "b": 2}


def test_allocate_seats_excludes_walled(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        _enroll(d)
    accounts = [_acct("a", a), _acct("b", b)]

    def probe(creds, now):
        norm = creds.replace("\\", "/")
        if norm.endswith("/b/.credentials.json"):
            return FakeProbe("rejected", 1.0, None, http_status=429)
        return FakeProbe("allowed", 0.1, None)

    seats = sw.allocate_seats(accounts, 6, probe_fn=probe, now_epoch=NOW)
    counts = _counts(seats)
    assert "b" not in counts          # walled window excluded
    assert counts["a"] == 6           # all seats land on the serving window


def test_allocate_seats_empty_pool_when_all_walled(tmp_path):
    a = tmp_path / "a"
    _enroll(a)
    accounts = [_acct("a", a)]
    probe = lambda creds, now: FakeProbe("rejected", 1.0, None, http_status=429)
    seats = sw.allocate_seats(accounts, 5, probe_fn=probe, now_epoch=NOW)
    assert seats == []  # nothing serves → caller keeps single-account default


def test_allocate_seats_zero_workers_is_empty(tmp_path):
    a = tmp_path / "a"
    _enroll(a)
    seats = sw.allocate_seats([_acct("a", a)], 0, probe_fn=lambda c, n: None, now_epoch=NOW)
    assert seats == []


def test_allocate_seats_total_equals_n_and_consecutive_distinct(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for d in (a, b, c):
        _enroll(d)
    accounts = [_acct("a", a), _acct("b", b), _acct("c", c)]
    probe = lambda creds, now: FakeProbe("allowed", 0.2, None)  # equal headroom
    seats = sw.allocate_seats(accounts, 6, probe_fn=probe, now_epoch=NOW)
    assert len(seats) == 6
    assert _counts(seats) == {"a": 2, "b": 2, "c": 2}
    assert len({s.name for s in seats[:3]}) == 3  # first 3 touch 3 distinct windows


def test_allocate_seats_fewer_workers_than_pool_uses_highest_headroom(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for d in (a, b, c):
        _enroll(d)
    accounts = [_acct("a", a), _acct("b", b), _acct("c", c)]

    def probe(creds, now):
        norm = creds.replace("\\", "/")
        if norm.endswith("/a/.credentials.json"):
            return FakeProbe("allowed", 0.10, None)  # headroom 0.90 (highest)
        if norm.endswith("/b/.credentials.json"):
            return FakeProbe("allowed", 0.40, None)  # headroom 0.60 (middle)
        return FakeProbe("allowed", 0.80, None)      # headroom 0.20 (lowest)

    seats = sw.allocate_seats(accounts, 2, probe_fn=probe, now_epoch=NOW)
    assert len(seats) == 2
    assert {s.name for s in seats} == {"a", "b"}  # two highest-headroom windows


# --------------------------------------------------------------------------- #
# pick_account_spread — the cross-process rotation
# --------------------------------------------------------------------------- #
def test_spread_rotates_across_serving_by_seat_index(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for d in (a, b, c):
        _enroll(d)
    accounts = [_acct("a", a), _acct("b", b), _acct("c", c)]
    probe = lambda creds, now: FakeProbe("allowed", 0.10, None)
    picks = [
        sw.pick_account_spread(accounts, seat_index=i, probe_fn=probe, now_epoch=NOW)
        for i in range(6)
    ]
    names = [p.account.name for p in picks]
    assert names[:3] == ["a", "b", "c"]
    assert names[3:] == ["a", "b", "c"]  # wraps via seat_index % pool_width
    assert all(p.ok for p in picks)


def test_spread_orders_by_headroom_highest_first(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for d in (a, b, c):
        _enroll(d)
    accounts = [_acct("a", a), _acct("b", b), _acct("c", c)]

    def probe(creds, now):
        norm = creds.replace("\\", "/")
        if norm.endswith("/a/.credentials.json"):
            return FakeProbe("allowed", 0.80, None)  # headroom 0.20 (lowest)
        if norm.endswith("/b/.credentials.json"):
            return FakeProbe("allowed", 0.10, None)  # headroom 0.90 (highest)
        return FakeProbe("allowed", 0.40, None)      # headroom 0.60 (middle)

    assert sw.pick_account_spread(accounts, seat_index=0, probe_fn=probe, now_epoch=NOW).account.name == "b"
    assert sw.pick_account_spread(accounts, seat_index=1, probe_fn=probe, now_epoch=NOW).account.name == "c"
    assert sw.pick_account_spread(accounts, seat_index=2, probe_fn=probe, now_epoch=NOW).account.name == "a"


def test_spread_single_account_is_passthrough_to_pick_account(tmp_path):
    a = tmp_path / "a"
    _enroll(a)
    accounts = [_acct("a", a)]
    probe = lambda creds, now: FakeProbe("allowed", 0.1, None)
    for i in (0, 1, 2, 7):
        p = sw.pick_account_spread(accounts, seat_index=i, probe_fn=probe, now_epoch=NOW)
        assert p.ok and p.account.name == "a"


def test_spread_all_walled_falls_through_to_pick_account_wait(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _enroll(a)
    _enroll(b)
    accounts = [_acct("a", a), _acct("b", b)]
    probe = lambda creds, now: FakeProbe("rejected", 1.0, int(NOW + 3600), http_status=429)
    p = sw.pick_account_spread(accounts, seat_index=3, probe_fn=probe, now_epoch=NOW)
    assert not p.ok
    assert p.wait_seconds is not None
    ref = sw.pick_account(accounts, probe_fn=probe, now_epoch=NOW)
    assert p.account.name == ref.account.name
    assert p.wait_seconds == ref.wait_seconds


def test_spread_single_serving_among_walled_is_that_account(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _enroll(a)
    _enroll(b)
    accounts = [_acct("a", a), _acct("b", b)]

    def probe(creds, now):
        if creds.replace("\\", "/").endswith("/a/.credentials.json"):
            return FakeProbe("rejected", 1.0, int(NOW + 3600), http_status=429)
        return FakeProbe("allowed", 0.2, None)

    for i in (0, 1, 5):
        p = sw.pick_account_spread(accounts, seat_index=i, probe_fn=probe, now_epoch=NOW)
        assert p.ok and p.account.name == "b"


# --------------------------------------------------------------------------- #
# seed_account_settings — write settings.json into account config dir
# --------------------------------------------------------------------------- #
_SAMPLE_SETTINGS = {
    "model": "opus",
    "effortLevel": "xhigh",
    "permissions": {"defaultMode": "bypassPermissions"},
}


def test_seed_account_settings_writes_if_absent(tmp_path):
    a = _acct("a", tmp_path / "a")
    path = sw.seed_account_settings(a, _SAMPLE_SETTINGS)
    assert path == sw.account_settings_path(a)
    assert path.is_file()
    data = json.loads(path.read_text())
    assert data["model"] == "opus"
    assert data["effortLevel"] == "xhigh"
    assert data["permissions"]["defaultMode"] == "bypassPermissions"


def test_seed_account_settings_creates_config_dir(tmp_path):
    a = _acct("a", tmp_path / "new-account")
    assert not (tmp_path / "new-account").exists()
    sw.seed_account_settings(a, _SAMPLE_SETTINGS)
    assert (tmp_path / "new-account").is_dir()


def test_seed_account_settings_skips_if_present(tmp_path):
    a = _acct("a", tmp_path / "a")
    first = sw.seed_account_settings(a, _SAMPLE_SETTINGS)
    assert first is not None
    second = sw.seed_account_settings(a, {"model": "haiku"})
    assert second is None
    data = json.loads(sw.account_settings_path(a).read_text())
    assert data["model"] == "opus"  # unchanged


def test_seed_account_settings_overwrite(tmp_path):
    a = _acct("a", tmp_path / "a")
    sw.seed_account_settings(a, _SAMPLE_SETTINGS)
    path = sw.seed_account_settings(a, {"model": "haiku"}, overwrite=True)
    assert path is not None
    data = json.loads(sw.account_settings_path(a).read_text())
    assert data["model"] == "haiku"


def test_seed_account_settings_empty_is_noop(tmp_path):
    a = _acct("a", tmp_path / "a")
    result = sw.seed_account_settings(a, {})
    assert result is None
    assert not sw.account_settings_path(a).exists()


# --------------------------------------------------------------------------- #
# load_roster_defaults — fail-open parsing of defaults.settings
# --------------------------------------------------------------------------- #
def test_load_roster_defaults_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv(sw.ACCOUNTS_FILE_ENV, str(tmp_path / "nope.yaml"))
    d = sw.load_roster_defaults()
    assert d.settings == {}


def test_load_roster_defaults_parses_settings(tmp_path, monkeypatch):
    f = tmp_path / "roster.yaml"
    f.write_text(
        "accounts:\n"
        "  - name: x\n"
        '    config_dir: "~/.claude-x"\n'
        "defaults:\n"
        "  settings:\n"
        "    model: opus\n"
        "    effortLevel: xhigh\n"
        "    permissions:\n"
        "      defaultMode: bypassPermissions\n"
        "      skipDangerousModePermissionPrompt: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(sw.ACCOUNTS_FILE_ENV, str(f))
    d = sw.load_roster_defaults()
    assert d.settings["model"] == "opus"
    assert d.settings["effortLevel"] == "xhigh"
    assert d.settings["permissions"]["defaultMode"] == "bypassPermissions"
    assert d.settings["permissions"]["skipDangerousModePermissionPrompt"] is True


def test_load_roster_defaults_no_defaults_section(tmp_path, monkeypatch):
    f = tmp_path / "roster.yaml"
    f.write_text(
        "accounts:\n"
        "  - name: x\n"
        '    config_dir: "~/.claude-x"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv(sw.ACCOUNTS_FILE_ENV, str(f))
    d = sw.load_roster_defaults()
    assert d.settings == {}


def test_load_roster_defaults_malformed_is_empty(tmp_path, monkeypatch):
    f = tmp_path / "bad.yaml"
    f.write_text("accounts: [this is not: valid: yaml: : :", encoding="utf-8")
    monkeypatch.setenv(sw.ACCOUNTS_FILE_ENV, str(f))
    d = sw.load_roster_defaults()
    assert d.settings == {}  # fail-open, never raises


def test_seed_and_enroll_roundtrip(tmp_path):
    """write_account_token + seed_account_settings together: the full enroll flow."""
    cfg = tmp_path / "acct1"
    cfg.mkdir()
    a = _acct("acct1", cfg)
    defaults = sw.RosterDefaults(settings={"model": "opus", "effortLevel": "xhigh"})
    sw.write_account_token(a, "sk-ant-oat01-realtoken-xyz")
    seeded = sw.seed_account_settings(a, defaults.settings)
    assert seeded is not None
    data = json.loads(sw.account_settings_path(a).read_text())
    assert data["model"] == "opus"
    assert data["effortLevel"] == "xhigh"


# --------------------------------------------------------------------------- #
# merge_account_settings — deep-merge defaults into an EXISTING settings.json
# (the `dos accounts sync` primitive, issue #219)
# --------------------------------------------------------------------------- #
def test_deep_merge_settings_defaults_win_and_preserve():
    base = {"theme": "dark", "model": "haiku", "permissions": {"a": 1}}
    overlay = {"model": "opus", "permissions": {"b": 2}}
    out = sw._deep_merge_settings(base, overlay)
    assert out["model"] == "opus"          # overlay wins
    assert out["theme"] == "dark"          # base-only key preserved
    assert out["permissions"] == {"a": 1, "b": 2}  # nested dict MERGED, not replaced
    # purity: inputs untouched
    assert base["model"] == "haiku"
    assert base["permissions"] == {"a": 1}


def test_deep_merge_settings_non_dict_overlap_replaced():
    base = {"x": {"k": 1}}
    overlay = {"x": [1, 2, 3]}  # list replaces dict (not merged element-wise)
    out = sw._deep_merge_settings(base, overlay)
    assert out["x"] == [1, 2, 3]


def test_merge_account_settings_creates_when_absent(tmp_path):
    a = _acct("a", tmp_path / "a")
    assert not sw.account_settings_path(a).exists()
    path, changed = sw.merge_account_settings(a, _SAMPLE_SETTINGS)
    assert changed is True
    assert path == sw.account_settings_path(a)
    data = json.loads(path.read_text())
    assert data["model"] == "opus"
    assert data["permissions"]["defaultMode"] == "bypassPermissions"


def test_merge_account_settings_preserves_account_keys(tmp_path):
    a = _acct("a", tmp_path / "a")
    # the account already has its OWN settings (a per-account model + a theme)
    sw.account_settings_path(a).parent.mkdir(parents=True, exist_ok=True)
    sw.account_settings_path(a).write_text(
        json.dumps({"model": "haiku", "theme": "light",
                    "permissions": {"alwaysAllow": ["Read"]}}),
        encoding="utf-8",
    )
    path, changed = sw.merge_account_settings(a, _SAMPLE_SETTINGS)
    assert changed is True
    data = json.loads(path.read_text())
    assert data["model"] == "opus"                # default WON over the account's haiku
    assert data["theme"] == "light"              # account-only key preserved
    assert data["effortLevel"] == "xhigh"        # default added
    # nested permissions: the account's own key survives, the default is added
    assert data["permissions"]["alwaysAllow"] == ["Read"]
    assert data["permissions"]["defaultMode"] == "bypassPermissions"


def test_merge_account_settings_idempotent(tmp_path):
    a = _acct("a", tmp_path / "a")
    first_path, first_changed = sw.merge_account_settings(a, _SAMPLE_SETTINGS)
    assert first_changed is True
    # a second run with the same defaults changes nothing (the #219 done-condition)
    second_path, second_changed = sw.merge_account_settings(a, _SAMPLE_SETTINGS)
    assert second_changed is False
    assert second_path == first_path


def test_merge_account_settings_idempotent_despite_key_order(tmp_path):
    a = _acct("a", tmp_path / "a")
    # a file that already carries the defaults, written in a DIFFERENT key order /
    # without trailing newline — the change test compares parsed dicts, not bytes.
    sw.account_settings_path(a).parent.mkdir(parents=True, exist_ok=True)
    sw.account_settings_path(a).write_text(
        '{"effortLevel": "xhigh", "permissions": {"defaultMode": "bypassPermissions"}, '
        '"model": "opus"}',
        encoding="utf-8",
    )
    _, changed = sw.merge_account_settings(a, _SAMPLE_SETTINGS)
    assert changed is False


def test_merge_account_settings_dry_run_does_not_write(tmp_path):
    a = _acct("a", tmp_path / "a")
    path, changed = sw.merge_account_settings(a, _SAMPLE_SETTINGS, dry_run=True)
    assert changed is True          # it WOULD change
    assert not path.exists()        # but wrote nothing


def test_merge_account_settings_empty_is_noop(tmp_path):
    a = _acct("a", tmp_path / "a")
    path, changed = sw.merge_account_settings(a, {})
    assert path is None
    assert changed is False
    assert not sw.account_settings_path(a).exists()


def test_merge_account_settings_malformed_existing_is_replaced(tmp_path):
    a = _acct("a", tmp_path / "a")
    sw.account_settings_path(a).parent.mkdir(parents=True, exist_ok=True)
    sw.account_settings_path(a).write_text("{not valid json", encoding="utf-8")
    path, changed = sw.merge_account_settings(a, _SAMPLE_SETTINGS)
    assert changed is True
    data = json.loads(path.read_text())  # now valid, carries the defaults
    assert data["model"] == "opus"


# --------------------------------------------------------------------------- #
# dedupe_by_identity — collapse phantom duplicate seats (same login bucket)
# --------------------------------------------------------------------------- #
def test_dedupe_collapses_same_oauth_token(tmp_path):
    # Two seats, DIFFERENT dirs, IDENTICAL setup-token = one rate-limit bucket
    # (a copied-login phantom). The second must be dropped.
    a_dir, b_dir = tmp_path / "acct-a", tmp_path / "acct-b"
    _enroll_token(a_dir, token="sk-ant-oat01-shared-bucket-xyz")
    _enroll_token(b_dir, token="sk-ant-oat01-shared-bucket-xyz")
    a, b = _acct("acct-a", a_dir), _acct("acct-b", b_dir)
    out = sw.dedupe_by_identity([a, b])
    assert [x.name for x in out] == ["acct-a"]  # first roster occurrence wins


def test_dedupe_collapses_same_creds_access_token(tmp_path):
    # Same login via .credentials.json (no setup-token) is also a duplicate.
    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    for d in (a_dir, b_dir):
        d.mkdir(parents=True, exist_ok=True)
        (d / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "AT-same"}}), encoding="utf-8")
    out = sw.dedupe_by_identity([_acct("a", a_dir), _acct("b", b_dir)])
    assert [x.name for x in out] == ["a"]


def test_dedupe_keeps_distinct_logins(tmp_path):
    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    _enroll_token(a_dir, token="sk-ant-oat01-aaaaaaaa")
    _enroll_token(b_dir, token="sk-ant-oat01-bbbbbbbb")
    out = sw.dedupe_by_identity([_acct("a", a_dir), _acct("b", b_dir)])
    assert [x.name for x in out] == ["a", "b"]  # different buckets → both kept


def test_dedupe_keeps_distinct_logins_sharing_a_stale_setup_token(tmp_path):
    # Regression: two DISTINCT logins (different .credentials.json) that
    # carried the SAME copied .oauth-token. Creds identity must win so they stay
    # distinct — keying on the copyable token first would wrongly fuse two accounts.
    a_dir, b_dir = tmp_path / "acct-a", tmp_path / "acct-b"
    _enroll(a_dir)  # creds accessToken = "tok-acct-a"
    _enroll(b_dir)  # creds accessToken = "tok-acct-b"
    _enroll_token(a_dir, token="sk-ant-oat01-STALE-COPIED")  # same token, both
    _enroll_token(b_dir, token="sk-ant-oat01-STALE-COPIED")
    out = sw.dedupe_by_identity([_acct("acct-a", a_dir), _acct("acct-b", b_dir)])
    assert [x.name for x in out] == ["acct-a", "acct-b"]  # both kept — distinct logins


def test_dedupe_never_collapses_unidentifiable_seats(tmp_path):
    # Neither dir carries a readable identity → each stays distinct (a read
    # failure must never silently shrink the pool).
    a, b = _acct("a", tmp_path / "a"), _acct("b", tmp_path / "b")  # no creds/token
    out = sw.dedupe_by_identity([a, b])
    assert [x.name for x in out] == ["a", "b"]


def test_dedupe_excludes_phantom_at_load_roster(tmp_path):
    # End-to-end: the duplicate is gone from the loaded roster (the Python host
    # seam), so every downstream consumer (pool/seats/launch) sees only the first.
    a_dir, b_dir = tmp_path / ".claude-acct-a", tmp_path / ".claude-acct-b"
    _enroll_token(a_dir, token="sk-ant-oat01-shared")
    _enroll_token(b_dir, token="sk-ant-oat01-shared")
    roster = tmp_path / "roster.yaml"
    roster.write_text(
        "accounts:\n"
        f"  - name: acct-a\n    config_dir: {a_dir}\n"
        f"  - name: acct-b\n    config_dir: {b_dir}\n",
        encoding="utf-8")
    accounts, _policy = sw.load_roster(roster)
    assert [a.name for a in accounts] == ["acct-a"]
    # and the pinned ranking core still ranks whatever it is GIVEN, unchanged.
    pool = sw.serving_pool(accounts, now_epoch=1.0)
    assert [a.name for a in pool] == ["acct-a"]


# --------------------------------------------------------------------------- #
# remove_account — retire a seat (drop from roster + tombstone its dir)
# --------------------------------------------------------------------------- #
_REMOVE_ROSTER = """\
# a roster
accounts:
  # keep this one
  - name: keep-me
    config_dir: {keep}
  # the phantom dup to retire
  - name: kill-me
    config_dir: {kill}
    email: dup@x
rotation:
  order: by_reset
  near_cap_util: 0.9
"""


def _write_remove_roster(tmp_path):
    keep_dir = tmp_path / ".claude-keep"
    kill_dir = tmp_path / ".claude-kill"
    keep_dir.mkdir(); kill_dir.mkdir()
    roster = tmp_path / "roster.yaml"
    roster.write_text(_REMOVE_ROSTER.format(keep=keep_dir, kill=kill_dir),
                      encoding="utf-8")
    return roster, keep_dir, kill_dir


def test_remove_account_drops_block_and_tombstones_dir(tmp_path):
    roster, keep_dir, kill_dir = _write_remove_roster(tmp_path)
    res = sw.remove_account("kill-me", path=roster, date_str="2026-06-25")
    assert res.removed_from_roster is True
    assert res.renamed_to == str(kill_dir.parent / ".claude-kill.DELETED-2026-06-25")
    assert (kill_dir.parent / ".claude-kill.DELETED-2026-06-25").is_dir()
    assert not kill_dir.exists()
    # roster: keep-me + ITS comment survive; kill-me + ITS comment are gone.
    body = roster.read_text(encoding="utf-8")
    assert "keep-me" in body and "# keep this one" in body
    assert "kill-me" not in body and "phantom dup to retire" not in body
    assert "near_cap_util: 0.9" in body  # policy untouched


def test_remove_account_keep_dir_does_not_rename(tmp_path):
    roster, _keep, kill_dir = _write_remove_roster(tmp_path)
    res = sw.remove_account("kill-me", path=roster, rename_dir=False)
    assert res.removed_from_roster is True
    assert res.renamed_to is None
    assert kill_dir.is_dir()  # left in place
    assert "kill-me" not in roster.read_text(encoding="utf-8")


def test_remove_account_unknown_name_raises(tmp_path):
    roster, _keep, _kill = _write_remove_roster(tmp_path)
    with pytest.raises(ValueError):
        sw.remove_account("nope", path=roster)


def test_remove_account_load_roster_after_remove(tmp_path):
    # The dropped account must no longer load (the real point of removal).
    roster, _keep, _kill = _write_remove_roster(tmp_path)
    sw.remove_account("kill-me", path=roster, rename_dir=False)
    accounts, _policy = sw.load_roster(roster)
    assert [a.name for a in accounts] == ["keep-me"]
