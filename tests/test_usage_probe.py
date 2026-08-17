"""Tests for the live near-cap signal (dos.usage_probe, docs/391).

Pin the two signals (breaker pressure, opt-in token budget), the MAX combine, the
status thresholds, and — most importantly — the fail-open floor: a clean session with no
configured cap yields NO snapshot, so it is never spuriously rotated.

Run:  python -m pytest tests/test_usage_probe.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

from dos import breaker as _brk
from dos import config as _config
from dos import stop_failure_sensor as _sfs
from dos import usage_probe as up


def _cfg(tmp_path: Path):
    return _config.default_config(tmp_path)


def _seed_breaker(cfg, sid, *, consecutive, total=None):
    _sfs.save_counts(
        sid, _brk.BreakerCounts(consecutive=consecutive, total=total or consecutive), cfg
    )


def _transcript(tmp_path: Path, turns) -> str:
    """Write a tiny transcript; each turn is (input, output, cache_read, cache_create)."""
    p = tmp_path / "transcript.jsonl"
    lines = []
    for (i, o, cr, cc) in turns:
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"usage": {
                "input_tokens": i, "output_tokens": o,
                "cache_read_input_tokens": cr, "cache_creation_input_tokens": cc,
            }},
        }))
    # a user record with empty usage contributes nothing
    lines.append(json.dumps({"type": "user", "message": {"usage": {}}}))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------- #
# turn_token_total — pure sum over transcript lines
# --------------------------------------------------------------------------- #
def test_turn_token_total_sums_all_usage_fields():
    lines = [json.dumps({"type": "assistant", "message": {"usage": {
        "input_tokens": 2, "output_tokens": 100,
        "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 500,
    }}})]
    assert up.turn_token_total(lines) == 1602


def test_turn_token_total_skips_torn_and_empty_usage():
    lines = [
        '{"type":"assistant","message":{"usage":{',          # torn
        json.dumps({"type": "user", "message": {"usage": {}}}),  # empty
        json.dumps({"type": "assistant", "message": {"usage": {"output_tokens": 42}}}),
        "not json at all",
    ]
    assert up.turn_token_total(lines) == 42


def test_transcript_token_total_failsoft_on_missing_file():
    assert up.transcript_token_total("/no/such/file.jsonl") == 0
    assert up.transcript_token_total("") == 0


# --------------------------------------------------------------------------- #
# assess_current_seat — the combined signal
# --------------------------------------------------------------------------- #
def test_clean_session_no_cap_is_no_signal(tmp_path):
    cfg = _cfg(tmp_path)
    # No breaker state, no configured cap → None (fail-open → serving downstream).
    assert up.assess_current_seat(cfg, "s1") is None


def test_breaker_pressure_one_failure_is_partial_util(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_breaker(cfg, "s1", consecutive=1)
    snap = up.assess_current_seat(cfg, "s1", near_cap_util=0.9)
    assert snap is not None
    assert abs(snap.utilization - (1 / 3)) < 1e-9
    assert snap.status == up.STATUS_ALLOWED  # below near_cap — serving
    assert snap.allowed is True


def test_breaker_pressure_at_full_is_walled(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_breaker(cfg, "s1", consecutive=3)
    snap = up.assess_current_seat(cfg, "s1", near_cap_util=0.9)
    assert snap is not None
    assert snap.utilization == 1.0
    assert snap.status == up.STATUS_REJECTED
    assert snap.allowed is False  # a recorded wall → WALLED downstream


def test_token_budget_near_cap_warns(tmp_path):
    cfg = _cfg(tmp_path)
    tx = _transcript(tmp_path, [(0, 0, 0, 9500)])  # 9500 tokens
    snap = up.assess_current_seat(
        cfg, "s1", transcript_path=tx, near_cap_util=0.9, window_token_cap=10000
    )
    assert snap is not None
    assert abs(snap.utilization - 0.95) < 1e-9
    assert snap.status == up.STATUS_WARNING  # >= near_cap, not yet walled
    assert snap.allowed is True


def test_combine_takes_the_max_of_signals(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_breaker(cfg, "s1", consecutive=1)            # pressure ~0.33
    tx = _transcript(tmp_path, [(0, 0, 0, 9200)])      # tokens 0.92
    snap = up.assess_current_seat(
        cfg, "s1", transcript_path=tx, near_cap_util=0.9, window_token_cap=10000
    )
    assert snap is not None
    assert abs(snap.utilization - 0.92) < 1e-9  # max(0.33, 0.92)
    assert snap.status == up.STATUS_WARNING


def test_token_budget_disabled_without_cap(tmp_path):
    cfg = _cfg(tmp_path)
    tx = _transcript(tmp_path, [(0, 0, 0, 9_999_999)])  # huge spend
    # No cap configured → the token leg is skipped; no breaker → no signal at all.
    assert up.assess_current_seat(cfg, "s1", transcript_path=tx, window_token_cap=0) is None


def test_reset_epoch_passes_through(tmp_path):
    cfg = _cfg(tmp_path)
    _seed_breaker(cfg, "s1", consecutive=3)
    snap = up.assess_current_seat(cfg, "s1", reset_at_epoch=1_700_003_600)
    assert snap is not None and snap.reset_at_epoch == 1_700_003_600


def test_assess_is_failsoft_on_bad_session(tmp_path):
    cfg = _cfg(tmp_path)
    # Empty session id, weird transcript path — must not raise, just None / no token leg.
    assert up.assess_current_seat(cfg, "") is None


def test_snapshot_satisfies_probelike_for_account_state(tmp_path):
    """The snapshot folds through account_state exactly like the fleet picker's probe."""
    from dos.drivers import account_switcher as sw

    cfg = _cfg(tmp_path)
    cdir = tmp_path / "seatA"
    cdir.mkdir()
    (cdir / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "t", "expiresAt": 9_999_999_999_000}}),
        encoding="utf-8",
    )
    _seed_breaker(cfg, "s1", consecutive=3)  # → rejected snapshot
    snap = up.assess_current_seat(cfg, "s1")
    acct = sw.Account(name="seatA", config_dir=str(cdir))
    st = sw.account_state(acct, probe_fn=lambda *_: snap, now_epoch=1_700_000_000.0)
    assert st.kind == sw.ACCT_WALLED  # rejected snapshot → WALLED fold
