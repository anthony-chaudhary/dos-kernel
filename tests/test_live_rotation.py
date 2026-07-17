"""Tests for the PURE live-rotation verdict (dos.live_rotation, docs/391).

These pin the kernel decision with NO I/O, NO clock (now_epoch injected), and NO driver
import beyond the litmus that the mirrored kind-strings equal the switcher's. They cover
every ``decide`` branch (HOLD / ROTATE / WALL_WAIT / NO_ALTERNATE), the anti-stampede
``spread_targets`` fan-out, and ``plan_fleet`` (a whole herd at once).

Run:  python -m pytest tests/test_live_rotation.py -q
"""
from __future__ import annotations

from dos import live_rotation as lr

NOW = 1_700_000_000.0


def _seat(name, kind, util=None, reset=None):
    return lr.Seat(name=name, kind=kind, utilization=util, reset_at_epoch=reset)


# --------------------------------------------------------------------------- #
# Litmus: the mirrored kind strings MUST equal the driver's, or the hook folds
# AccountStates into Seats the verdict misreads.
# --------------------------------------------------------------------------- #
def test_kind_strings_mirror_the_switcher():
    from dos.drivers import account_switcher as sw

    assert lr.KIND_SERVING == sw.ACCT_SERVING
    assert lr.KIND_NEAR_CAP == sw.ACCT_NEAR_CAP
    assert lr.KIND_WALLED == sw.ACCT_WALLED


def test_no_hint_wait_mirrors_the_switcher():
    from dos.drivers import account_switcher as sw

    assert lr._NO_HINT_WAIT_SECONDS == sw._NO_HINT_WAIT_SECONDS
    assert lr._MIN_HEADROOM == sw._MIN_HEADROOM


# --------------------------------------------------------------------------- #
# from_account_state — the one driver-shape adapter
# --------------------------------------------------------------------------- #
def test_from_account_state_adapts_the_driver_shape():
    from dos.drivers import account_switcher as sw

    acct = sw.Account(name="acctA", config_dir="/x")
    st = sw.AccountState(
        account=acct, kind=sw.ACCT_NEAR_CAP, creds_present=True,
        token_expired=False, utilization=0.97, reset_at_epoch=int(NOW + 3600),
    )
    seat = lr.from_account_state(st)
    assert seat.name == "acctA"
    assert seat.kind == lr.KIND_NEAR_CAP
    assert seat.utilization == 0.97
    assert seat.reset_at_epoch == int(NOW + 3600)


def test_from_account_state_is_tolerant_of_missing_fields():
    seat = lr.from_account_state(object())  # no attrs at all
    assert seat.name == "" and seat.kind == "" and seat.utilization is None


# --------------------------------------------------------------------------- #
# decide — every branch
# --------------------------------------------------------------------------- #
def test_serving_seat_holds():
    d = lr.decide(_seat("A", lr.KIND_SERVING, 0.2), [_seat("B", lr.KIND_SERVING)], now_epoch=NOW)
    assert d.verdict is lr.LiveVerdict.HOLD
    assert d.to_account is None


def test_near_cap_rotates_to_serving_alternate():
    d = lr.decide(
        _seat("A", lr.KIND_NEAR_CAP, 0.96),
        [_seat("B", lr.KIND_SERVING, 0.1), _seat("C", lr.KIND_SERVING, 0.05)],
        now_epoch=NOW,
    )
    assert d.verdict is lr.LiveVerdict.ROTATE
    assert d.to_account == "B"  # serving alternates kept in roster order
    assert d.from_account == "A"
    assert d.trigger == lr.KIND_NEAR_CAP


def test_walled_rotates_to_serving_alternate():
    d = lr.decide(
        _seat("A", lr.KIND_WALLED, 1.0, int(NOW + 600)),
        [_seat("B", lr.KIND_SERVING, 0.3)],
        now_epoch=NOW,
    )
    assert d.verdict is lr.LiveVerdict.ROTATE
    assert d.to_account == "B"
    assert d.trigger == lr.KIND_WALLED


def test_rotation_prefers_serving_then_lowest_util_near_cap():
    # No clean-serving alternate; two near-cap ones — the lower-util wins.
    d = lr.decide(
        _seat("A", lr.KIND_WALLED, 1.0),
        [_seat("B", lr.KIND_NEAR_CAP, 0.98), _seat("C", lr.KIND_NEAR_CAP, 0.91)],
        now_epoch=NOW,
    )
    assert d.verdict is lr.LiveVerdict.ROTATE
    assert d.to_account == "C"  # lowest-util near-cap


def test_near_cap_with_no_alternate_holds():
    # Near-cap but the only serving seat — usable, so HOLD (pick_account near-cap fallback).
    d = lr.decide(_seat("A", lr.KIND_NEAR_CAP, 0.97), [], now_epoch=NOW)
    assert d.verdict is lr.LiveVerdict.HOLD


def test_near_cap_with_only_walled_alternates_holds():
    d = lr.decide(
        _seat("A", lr.KIND_NEAR_CAP, 0.97),
        [_seat("B", lr.KIND_WALLED, 1.0)],
        now_epoch=NOW,
    )
    assert d.verdict is lr.LiveVerdict.HOLD  # nowhere better to go


def test_walled_with_all_alternates_walled_waits_for_soonest_reset():
    d = lr.decide(
        _seat("A", lr.KIND_WALLED, 1.0, int(NOW + 5000)),
        [_seat("B", lr.KIND_WALLED, 1.0, int(NOW + 1200))],
        now_epoch=NOW,
    )
    assert d.verdict is lr.LiveVerdict.WALL_WAIT
    assert d.wait_seconds == 1200  # soonest reset across the walled set
    assert d.reset_at_epoch == int(NOW + 1200)


def test_walled_no_reset_hint_uses_floor_constant():
    d = lr.decide(_seat("A", lr.KIND_WALLED), [_seat("B", lr.KIND_WALLED)], now_epoch=NOW)
    assert d.verdict is lr.LiveVerdict.WALL_WAIT
    assert d.wait_seconds == lr._NO_HINT_WAIT_SECONDS


def test_walled_single_account_roster_is_no_alternate():
    d = lr.decide(_seat("A", lr.KIND_WALLED, 1.0, int(NOW + 600)), [], now_epoch=NOW)
    assert d.verdict is lr.LiveVerdict.NO_ALTERNATE
    assert d.to_account is None


def test_wall_wait_clamps_past_reset_to_zero():
    d = lr.decide(
        _seat("A", lr.KIND_WALLED, 1.0, int(NOW - 100)),  # already reset
        [_seat("B", lr.KIND_WALLED, 1.0, int(NOW - 50))],
        now_epoch=NOW,
    )
    assert d.verdict is lr.LiveVerdict.WALL_WAIT
    assert d.wait_seconds == 0  # re-probe immediately


# --------------------------------------------------------------------------- #
# spread_targets — the thundering-herd defence
# --------------------------------------------------------------------------- #
def test_spread_empty_pool_is_empty():
    assert lr.spread_targets(5, []) == []
    assert lr.spread_targets(0, [_seat("B", lr.KIND_SERVING)]) == []


def test_spread_distributes_across_distinct_alternates_first():
    pool = [_seat("B", lr.KIND_SERVING, 0.0), _seat("C", lr.KIND_SERVING, 0.0)]
    out = lr.spread_targets(2, pool)
    assert sorted(out) == ["B", "C"]  # two sessions → two distinct seats, no stampede


def test_spread_weights_by_headroom():
    # B has lots of headroom (util 0.1), C is near-cap (util 0.9): of 10 sessions,
    # B should absorb far more than C.
    pool = [_seat("B", lr.KIND_SERVING, 0.1), _seat("C", lr.KIND_NEAR_CAP, 0.9)]
    out = lr.spread_targets(10, pool)
    assert len(out) == 10
    assert out.count("B") > out.count("C")
    assert out.count("C") >= 1  # near-cap still gets a sliver (never literally zero)


def test_spread_no_single_alternate_gets_the_whole_herd():
    # Three equal-headroom serving alternates, 9 sessions → ~3 each, never 9 on one.
    pool = [_seat(n, lr.KIND_SERVING, 0.0) for n in ("B", "C", "D")]
    out = lr.spread_targets(9, pool)
    for name in ("B", "C", "D"):
        assert out.count(name) == 3


def test_spread_is_deterministic():
    pool = [_seat("B", lr.KIND_SERVING, 0.2), _seat("C", lr.KIND_SERVING, 0.4)]
    assert lr.spread_targets(7, pool) == lr.spread_targets(7, pool)


# --------------------------------------------------------------------------- #
# plan_fleet — a whole herd at once, spread not stampeded
# --------------------------------------------------------------------------- #
def test_plan_fleet_spreads_a_herd_across_alternates():
    # 6 sessions all near-cap on their own seats, sharing two clean serving alternates.
    sessions = [(f"s{i}", _seat(f"seat{i}", lr.KIND_NEAR_CAP, 0.97)) for i in range(6)]
    alts = [_seat("X", lr.KIND_SERVING, 0.0), _seat("Y", lr.KIND_SERVING, 0.0)]
    decisions = lr.plan_fleet(sessions, alts, now_epoch=NOW)
    targets = [d.to_account for d in decisions.values()]
    assert all(d.verdict is lr.LiveVerdict.ROTATE for d in decisions.values())
    # The herd fanned across BOTH alternates, ~3 each — not all 6 onto X.
    assert targets.count("X") == 3 and targets.count("Y") == 3


def test_plan_fleet_holds_serving_sessions_and_rotates_only_the_hot_ones():
    sessions = [
        ("healthy", _seat("seatH", lr.KIND_SERVING, 0.2)),
        ("hot", _seat("seatHot", lr.KIND_NEAR_CAP, 0.98)),
    ]
    alts = [_seat("X", lr.KIND_SERVING, 0.0)]
    decisions = lr.plan_fleet(sessions, alts, now_epoch=NOW)
    assert decisions["healthy"].verdict is lr.LiveVerdict.HOLD
    assert decisions["hot"].verdict is lr.LiveVerdict.ROTATE
    assert decisions["hot"].to_account == "X"


def test_plan_fleet_with_no_alternates_falls_back_to_per_session_verdict():
    sessions = [("a", _seat("seatA", lr.KIND_WALLED, 1.0, int(NOW + 300)))]
    decisions = lr.plan_fleet(sessions, [], now_epoch=NOW)
    assert decisions["a"].verdict is lr.LiveVerdict.NO_ALTERNATE


def test_decision_to_dict_round_trips_the_fields():
    d = lr.decide(
        _seat("A", lr.KIND_NEAR_CAP, 0.96), [_seat("B", lr.KIND_SERVING, 0.1)], now_epoch=NOW
    )
    j = d.to_dict()
    assert j["verdict"] == "ROTATE" and j["to_account"] == "B" and j["from_account"] == "A"
    assert j["trigger"] == "near_cap"
