"""rotation_handoff — DOS's side of the asyncRewake env-override contract (docs/386 §4).

The two-phase rotate-on-wall handoff: the StopFailure hook WRITES the serving seat to
relaunch under (+ its env-override), and the SessionStart applier CONSUMES it, appending
the env to CLAUDE_ENV_FILE exactly once. These tests pin the durable record (round-trip,
atomic overwrite, fail-soft torn read, apply-once), the env-file applier, and the
sanitization that keeps a distrusted session id from escaping its directory.
"""
from __future__ import annotations

from pathlib import Path

from dos import config as _config
from dos import rotation_handoff as rh


def _cfg(tmp_path: Path):
    return _config.default_config(tmp_path)


def test_write_read_round_trip(tmp_path):
    cfg = _cfg(tmp_path)
    h = rh.RotationHandoff(
        to_account="acctB",
        env={"CLAUDE_CONFIG_DIR": "/seats/acctB", "CLAUDE_CODE_OAUTH_TOKEN": "sk-x"},
        from_account="acctA",
        reason="rotate-on-wall: transient_overload",
    )
    assert rh.write_handoff(cfg, "s1", h, now_ms=1000)
    got = rh.read_handoff(cfg, "s1")
    assert got is not None
    assert got.to_account == "acctB"
    assert got.from_account == "acctA"
    assert got.env["CLAUDE_CONFIG_DIR"] == "/seats/acctB"
    assert got.ts_ms == 1000
    # lands under the workspace's own .dos/accounts/rotation/, never the real repo
    assert rh.handoff_path(cfg, "s1") == tmp_path / ".dos" / "accounts" / "rotation" / "s1.json"


def test_ts_ms_autostamped_when_absent(tmp_path):
    cfg = _cfg(tmp_path)
    assert rh.write_handoff(cfg, "s1", rh.RotationHandoff(to_account="a", env={"K": "v"}))
    got = rh.read_handoff(cfg, "s1")
    assert got is not None and isinstance(got.ts_ms, int) and got.ts_ms > 0


def test_write_is_single_record_latest_wins(tmp_path):
    cfg = _cfg(tmp_path)
    rh.write_handoff(cfg, "s1", rh.RotationHandoff(to_account="acctB", env={"K": "1"}), now_ms=1)
    rh.write_handoff(cfg, "s1", rh.RotationHandoff(to_account="acctC", env={"K": "2"}), now_ms=2)
    got = rh.read_handoff(cfg, "s1")
    assert got is not None and got.to_account == "acctC" and got.env["K"] == "2"


def test_consume_is_apply_once(tmp_path):
    cfg = _cfg(tmp_path)
    rh.write_handoff(cfg, "s1", rh.RotationHandoff(to_account="acctB", env={"K": "v"}), now_ms=1)
    first = rh.consume_handoff(cfg, "s1")
    assert first is not None and first.to_account == "acctB"
    assert rh.read_handoff(cfg, "s1") is None       # cleared
    assert rh.consume_handoff(cfg, "s1") is None      # second consume is a no-op


def test_read_missing_is_none(tmp_path):
    cfg = _cfg(tmp_path)
    assert rh.read_handoff(cfg, "never-seen") is None
    assert rh.consume_handoff(cfg, "never-seen") is None


def test_torn_record_reads_as_none(tmp_path):
    cfg = _cfg(tmp_path)
    rh.write_handoff(cfg, "s1", rh.RotationHandoff(to_account="acctB", env={"K": "v"}), now_ms=1)
    path = rh.handoff_path(cfg, "s1")
    path.write_text('{"to_account": "acctB", "env": {', encoding="utf-8")  # truncated json
    assert rh.read_handoff(cfg, "s1") is None  # torn → no handoff, never raises


def test_handoff_without_to_account_or_env_is_refused(tmp_path):
    cfg = _cfg(tmp_path)
    assert rh.write_handoff(cfg, "s1", rh.RotationHandoff(to_account="", env={"K": "v"})) is False
    # an empty env is allowed to write but is a no-op for the applier (see below)
    assert rh.RotationHandoff.from_dict({"to_account": "a"}) is None     # no env
    assert rh.RotationHandoff.from_dict({"env": {"K": "v"}}) is None     # no to_account
    assert rh.RotationHandoff.from_dict("nope") is None


def test_unsafe_session_id_is_sanitized_not_escaped(tmp_path):
    cfg = _cfg(tmp_path)
    assert rh.write_handoff(cfg, "../../etc/passwd", rh.RotationHandoff(to_account="a", env={"K": "v"}))
    p = rh.handoff_path(cfg, "../../etc/passwd")
    assert p is not None
    assert p.parent == rh.rotation_dir(cfg)   # stays under .dos/accounts/rotation/
    assert ".." not in p.name


def test_empty_session_id_is_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    assert rh.handoff_path(cfg, "") is None
    assert rh.write_handoff(cfg, "", rh.RotationHandoff(to_account="a", env={"K": "v"})) is False


# --------------------------------------------------------------------------- #
# apply_to_env_file — the APPLY half (writes CLAUDE_ENV_FILE)
# --------------------------------------------------------------------------- #
def test_apply_to_env_file_appends_key_value_lines(tmp_path):
    env_file = tmp_path / "claude.env"
    h = rh.RotationHandoff(
        to_account="acctB",
        env={"CLAUDE_CONFIG_DIR": "/seats/acctB", "CLAUDE_CODE_OAUTH_TOKEN": "sk-x"},
    )
    assert rh.apply_to_env_file(h, str(env_file))
    text = env_file.read_text(encoding="utf-8")
    assert "CLAUDE_CONFIG_DIR=/seats/acctB\n" in text
    assert "CLAUDE_CODE_OAUTH_TOKEN=sk-x\n" in text


def test_apply_to_env_file_appends_not_truncates(tmp_path):
    env_file = tmp_path / "claude.env"
    env_file.write_text("EXISTING=1\n", encoding="utf-8")
    rh.apply_to_env_file(rh.RotationHandoff(to_account="a", env={"K": "v"}), str(env_file))
    text = env_file.read_text(encoding="utf-8")
    assert "EXISTING=1\n" in text and "K=v\n" in text


def test_apply_to_env_file_failsoft(tmp_path):
    assert rh.apply_to_env_file(rh.RotationHandoff(to_account="a", env={}), str(tmp_path / "x")) is False
    assert rh.apply_to_env_file(rh.RotationHandoff(to_account="a", env={"K": "v"}), "") is False
