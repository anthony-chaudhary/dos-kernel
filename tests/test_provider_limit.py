"""Tests for `dos.provider_limit` — the canonical provider-limit category, its
policy table, and the three mappers from the upstream taxonomies.

This module is the PI5 collapse target: it does not classify, it standardizes
the OUTPUT vocabulary the dispatch family shares. The tests pin three contracts:

  1. `policy_for` is TOTAL over `ProviderLimit` (no category ships without a
     policy) and its retry semantics match the kernel's decide() expectations.
  2. The mapper tables are correct for every known upstream token, and defensive
     (unknown → NONE, never a spurious limit).
  3. The canonical overload backoff ladder equals `loop_decide._OVERLOADED_BACKOFF`
     — the two copies (shared source of truth here, hot-path copy there) must not
     drift, or a transient overload would back off differently depending on which
     module the caller read.
"""
from __future__ import annotations

import pytest

from dos.provider_limit import (
    LimitPolicy,
    ProviderLimit,
    from_apply_outcome_token,
    from_quota_error_class,
    from_rate_limit_kind,
    from_terminal_class,
    heal_for_model_death,
    policy_for,
)


# --- 1. policy_for is total + retry semantics are coherent -------------------

def test_policy_for_total_over_enum():
    """Every ProviderLimit member has a policy (exhaustiveness lock — a new
    category cannot be added without giving it a handling policy)."""
    for cat in ProviderLimit:
        pol = policy_for(cat)
        assert isinstance(pol, LimitPolicy)
        assert pol.category is cat


def test_only_transient_overload_is_retryable():
    """The load-bearing split: TRANSIENT_OVERLOAD retries the SAME unit/model;
    everything else (including MODEL_UNAVAILABLE, whose heal is a DIFFERENT model)
    is not same-iter retryable."""
    assert policy_for(ProviderLimit.TRANSIENT_OVERLOAD).retryable_same_iter is True
    for cat in (
        ProviderLimit.USAGE_WINDOW,
        ProviderLimit.HARD_QUOTA,
        ProviderLimit.MODEL_UNAVAILABLE,
        ProviderLimit.MODEL_SUSPENDED,
        ProviderLimit.NONE,
    ):
        assert policy_for(cat).retryable_same_iter is False


def test_retryable_iff_has_backoff_ladder():
    """A non-empty backoff ladder is present exactly when the category is
    retryable — an empty ladder on a 'retryable' category (or vice-versa) would
    be an incoherent policy a caller could not act on."""
    for cat in ProviderLimit:
        pol = policy_for(cat)
        assert bool(pol.backoff_seconds) == pol.retryable_same_iter


def test_only_hard_quota_needs_operator_and_no_timer():
    """HARD_QUOTA and MODEL_SUSPENDED both need an operator AND no timer clears
    them. MODEL_UNAVAILABLE has no timer reset either, but does NOT need an
    operator (a sibling model heals it) — that reroute-vs-escalate split is the
    load-bearing difference between MODEL_UNAVAILABLE and MODEL_SUSPENDED."""
    hq = policy_for(ProviderLimit.HARD_QUOTA)
    assert hq.operator_action_required is True
    assert hq.resets_on_timer is False
    # The two timer-reset categories do reset on their own.
    assert policy_for(ProviderLimit.TRANSIENT_OVERLOAD).resets_on_timer is True
    assert policy_for(ProviderLimit.USAGE_WINDOW).resets_on_timer is True
    # MODEL_UNAVAILABLE: no timer reset (a down model is not a window that
    # reopens on a clock) — but NO operator action either (route to a sibling).
    mu = policy_for(ProviderLimit.MODEL_UNAVAILABLE)
    assert mu.resets_on_timer is False
    assert mu.operator_action_required is False
    # MODEL_SUSPENDED: a policy pull — needs an operator (no sibling, no timer).
    ms = policy_for(ProviderLimit.MODEL_SUSPENDED)
    assert ms.operator_action_required is True
    assert ms.resets_on_timer is False
    # ...and none of the reroute/timer categories demand operator action.
    for cat in (
        ProviderLimit.TRANSIENT_OVERLOAD,
        ProviderLimit.USAGE_WINDOW,
        ProviderLimit.MODEL_UNAVAILABLE,
        ProviderLimit.NONE,
    ):
        assert policy_for(cat).operator_action_required is False


def test_only_model_unavailable_reroutes():
    """MODEL_UNAVAILABLE is the one category whose heal is to change WHICH model
    runs the unit. reroute_model is the auto-routing flag the 'model down on a
    child/grandchild' case keys on — and it is orthogonal to retryable_same_iter
    (re-running the same model would just re-hit the down model). MODEL_SUSPENDED
    deliberately does NOT reroute (#140: a sibling may also be pulled)."""
    mu = policy_for(ProviderLimit.MODEL_UNAVAILABLE)
    assert mu.reroute_model is True
    assert mu.retryable_same_iter is False  # the same model is down; same-iter is futile
    assert mu.backoff_seconds == ()         # no backoff ladder — route now, don't wait
    # No other category reroutes — the heal axis is unique to MODEL_UNAVAILABLE.
    for cat in (
        ProviderLimit.TRANSIENT_OVERLOAD,
        ProviderLimit.USAGE_WINDOW,
        ProviderLimit.HARD_QUOTA,
        ProviderLimit.MODEL_SUSPENDED,
        ProviderLimit.NONE,
    ):
        assert policy_for(cat).reroute_model is False


def test_overload_escalates_after_three_others_after_one():
    assert policy_for(ProviderLimit.TRANSIENT_OVERLOAD).escalate_after == 3
    for cat in (
        ProviderLimit.USAGE_WINDOW,
        ProviderLimit.HARD_QUOTA,
        ProviderLimit.MODEL_UNAVAILABLE,
        ProviderLimit.MODEL_SUSPENDED,
        ProviderLimit.NONE,
    ):
        assert policy_for(cat).escalate_after == 1


# --- 2. mapper tables --------------------------------------------------------

@pytest.mark.parametrize("kind, expected", [
    ("OVERLOADED", ProviderLimit.TRANSIENT_OVERLOAD),
    ("RATE_LIMITED", ProviderLimit.USAGE_WINDOW),
    ("CREDIT_LOW", ProviderLimit.HARD_QUOTA),
    ("NONE", ProviderLimit.NONE),
    ("something-unknown", ProviderLimit.NONE),  # defensive
])
def test_from_rate_limit_kind(kind, expected):
    assert from_rate_limit_kind(kind) is expected


def test_from_rate_limit_kind_accepts_enum_value_object():
    """The mapper accepts the str-valued enum member directly (str(Kind.X) == 'X')."""
    class _FakeKind(str):
        pass
    assert from_rate_limit_kind(_FakeKind("OVERLOADED")) is ProviderLimit.TRANSIENT_OVERLOAD


@pytest.mark.parametrize("qec, expected", [
    ("rpm_throttled", ProviderLimit.TRANSIENT_OVERLOAD),
    ("transient_429", ProviderLimit.TRANSIENT_OVERLOAD),
    ("daily_quota_exhausted", ProviderLimit.USAGE_WINDOW),
    ("subscription_blackout", ProviderLimit.USAGE_WINDOW),
    ("unknown_class", ProviderLimit.NONE),  # defensive
])
def test_from_quota_error_class(qec, expected):
    assert from_quota_error_class(qec) is expected


@pytest.mark.parametrize("token, expected", [
    ("LLM-QUOTA-EXHAUSTED", ProviderLimit.USAGE_WINDOW),
    ("LLM-QUOTA-EXHAUSTED-DURABLE", ProviderLimit.USAGE_WINDOW),
    ("LLM-MODEL-UNAVAILABLE", ProviderLimit.MODEL_UNAVAILABLE),  # named model down
    ("MODEL-UNAVAILABLE", ProviderLimit.MODEL_UNAVAILABLE),
    ("CORRELATED-OUTAGE", ProviderLimit.NONE),          # an outage, not a limit
    ("BROWSER-SERVICE-UNAVAILABLE", ProviderLimit.NONE),  # an outage, not a limit
    ("SHIPPED", ProviderLimit.NONE),                    # not a limit token at all
])
def test_from_apply_outcome_token(token, expected):
    assert from_apply_outcome_token(token) is expected


@pytest.mark.parametrize("cls, expected", [
    # The result_state.TerminalClass → heal-category bridge.
    ("MODEL_UNAVAILABLE", ProviderLimit.MODEL_UNAVAILABLE),
    ("RATE_LIMIT", ProviderLimit.USAGE_WINDOW),
    ("USAGE_LIMIT", ProviderLimit.USAGE_WINDOW),
    ("AUTH", ProviderLimit.HARD_QUOTA),         # credential block — a human acts
    ("SERVER", ProviderLimit.TRANSIENT_OVERLOAD),  # a 500 is a transient blip
    ("OTHER", ProviderLimit.NONE),              # no actionable heal — don't fabricate one
    ("NONE", ProviderLimit.NONE),
    ("not-a-class", ProviderLimit.NONE),        # defensive
])
def test_from_terminal_class(cls, expected):
    assert from_terminal_class(cls) is expected


# --- issue #140: suspension escalates, transient reroutes (text-aware heal) -----

def test_heal_140_transient_still_reroutes():
    """#140 done-condition (1): a transient '<model> is currently unavailable'
    death still routes to reroute_model=True — no regression."""
    cat = heal_for_model_death("Claude Fable 5 is currently unavailable")
    assert cat is ProviderLimit.MODEL_UNAVAILABLE
    assert policy_for(cat).reroute_model is True


@pytest.mark.parametrize("text", [
    "Claude Fable 5 is suspended",
    "The model has been disabled by policy",
    "This model is not available in your region",
    "claude-fable-5 unavailable in your region",
    "model withdrawn by export control directive",
])
def test_heal_140_suspension_escalates_not_reroutes(text):
    """#140 done-condition (2): a suspension/policy death routes to escalate —
    reroute_model=False, escalate_after=1, operator_action_required — so a
    pulled model surfaces to the operator instead of silently draining budget
    rerouting to a sibling that may also be pulled."""
    cat = heal_for_model_death(text)
    assert cat is ProviderLimit.MODEL_SUSPENDED
    pol = policy_for(cat)
    assert pol.reroute_model is False
    assert pol.escalate_after == 1
    assert pol.operator_action_required is True


def test_heal_140_no_new_terminal_class():
    """#140 done-condition (3): NO new result_state terminal class — the split is
    in the HEAL (provider_limit), keyed on text. result_state still has the one
    MODEL_UNAVAILABLE class; there is no MODEL_SUSPENDED terminal class."""
    from dos.result_state import TerminalClass

    assert "MODEL_UNAVAILABLE" in TerminalClass.__members__
    assert "MODEL_SUSPENDED" not in TerminalClass.__members__


def test_heal_140_cue_is_shape_not_a_model_name():
    """Provider-invariance floor (#140): the suspension cue is the SHAPE of the
    sentence, never a hardcoded model name. A suspension sentence about ANY model
    name escalates; the bare-outage sentence about the SAME name reroutes."""
    assert heal_for_model_death("totally-made-up-model-xyz is suspended") is ProviderLimit.MODEL_SUSPENDED
    assert heal_for_model_death("totally-made-up-model-xyz is currently unavailable") is ProviderLimit.MODEL_UNAVAILABLE


def test_from_terminal_class_bridges_real_result_state_class():
    """The bridge accepts the REAL result_state.TerminalClass enum member (it is
    str-valued), so a fold site can go straight from the death-witness to the
    heal category without a manual token. The MODEL_UNAVAILABLE death → the
    reroute_model heal is the goal's end-to-end path."""
    from dos.result_state import TerminalClass

    cat = from_terminal_class(TerminalClass.MODEL_UNAVAILABLE)
    assert cat is ProviderLimit.MODEL_UNAVAILABLE
    assert policy_for(cat).reroute_model is True


# --- 3. cross-module backoff-ladder agreement --------------------------------

def test_overload_backoff_ladder_matches_loop_decide():
    """The canonical ladder here MUST equal loop_decide's hot-path copy, or a
    transient overload would back off differently depending on which module a
    caller read. This is the drift-lock between the two intentional copies."""
    from dos.loop_decide import _OVERLOADED_BACKOFF

    assert policy_for(ProviderLimit.TRANSIENT_OVERLOAD).backoff_seconds == _OVERLOADED_BACKOFF


def test_overload_escalate_matches_loop_decide_default():
    """escalate_after for an overload must equal loop_decide's default
    max_overloaded (the 'stop on the Kth consecutive 529' constant)."""
    from dos.loop_decide import LoopState

    assert policy_for(ProviderLimit.TRANSIENT_OVERLOAD).escalate_after == LoopState().max_overloaded


def test_enum_is_str_valued_round_trip():
    """ProviderLimit round-trips as its str value (token convention)."""
    assert ProviderLimit.USAGE_WINDOW == "usage_window"
    assert str(ProviderLimit.TRANSIENT_OVERLOAD) == "transient_overload"
