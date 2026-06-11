"""SPEND — the token-spend breakdown vocabulary (docs/300 P1).

`dos.spend.SpendBreakdown` is the five-way split the field standardized in
early 2026 — input / output / cache_read / cache_creation / reasoning —
canonically disjoint (reasoning is the one sub-count, inside output), with the
two wire-shape normalizers (`from_additive_usage`: the input count EXCLUDES
cached tokens; `from_inclusive_usage`: it INCLUDES them, so the constructor
SUBTRACTS) and shape detection (`parse_usage`). Normalizing ONCE, at the
boundary, is what kills the cross-provider double-count bug class.

These tests pin construction, validation, the derived diagnostics (cache-hit
ratio / output share / reasoning share), and both wire shapes.
"""

from __future__ import annotations

import json

import pytest

from dos.spend import (
    SpendBreakdown,
    from_additive_usage,
    from_inclusive_usage,
    parse_usage,
)


# ---------------------------------------------------------------------------
# The breakdown: construction, validation, totals, diagnostics.
# ---------------------------------------------------------------------------


def test_breakdown_totals_are_disjoint_sums():
    """`total` is the plain sum of the four disjoint counts; reasoning (a
    sub-count of output) is never added twice."""
    b = SpendBreakdown(
        input=3_571, output=727, cache_read=6_656, cache_creation=1_000, reasoning=400
    )
    assert b.total == 3_571 + 727 + 6_656 + 1_000
    assert b.prefill == 3_571 + 6_656 + 1_000
    assert b.decode == 727


def test_breakdown_diagnostics():
    """The three headline KPIs: cache-hit ratio, output share, reasoning share."""
    b = SpendBreakdown(
        input=2_000, output=1_000, cache_read=8_000, cache_creation=0, reasoning=250
    )
    assert b.cache_hit_ratio == pytest.approx(8_000 / 10_000)
    assert b.output_share == pytest.approx(1_000 / 11_000)
    assert b.reasoning_share == pytest.approx(250 / 1_000)


def test_breakdown_diagnostics_are_division_safe():
    """Empty denominators are 0.0, never a divide-by-zero."""
    b = SpendBreakdown()
    assert b.total == 0
    assert b.cache_hit_ratio == 0.0
    assert b.output_share == 0.0
    assert b.reasoning_share == 0.0


def test_breakdown_rejects_negative_and_non_int_counts():
    with pytest.raises(ValueError):
        SpendBreakdown(input=-1)
    with pytest.raises(ValueError):
        SpendBreakdown(output="727")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        SpendBreakdown(cache_read=True)  # type: ignore[arg-type]


def test_breakdown_rejects_reasoning_above_output():
    """Reasoning is a SUB-count of output — billed inside it everywhere."""
    with pytest.raises(ValueError):
        SpendBreakdown(output=100, reasoning=101)


def test_breakdown_to_dict_carries_counts_and_diagnostics():
    b = SpendBreakdown(input=100, output=50, cache_read=900, cache_creation=0)
    d = b.to_dict()
    assert d["input"] == 100
    assert d["total"] == 1050
    assert d["prefill"] == 1000
    assert d["cache_hit_ratio"] == pytest.approx(0.9)
    # JSON-serializable (the --json contract).
    assert json.loads(json.dumps(d, sort_keys=True)) == d


# ---------------------------------------------------------------------------
# The two wire shapes, normalized at the boundary.
# ---------------------------------------------------------------------------


def test_additive_usage_is_a_straight_copy():
    """The additive shape (input EXCLUDES cached) is already canonical."""
    b = from_additive_usage(
        {
            "input_tokens": 3_571,
            "output_tokens": 727,
            "cache_read_input_tokens": 6_656,
            "cache_creation_input_tokens": 0,
        }
    )
    assert b.input == 3_571
    assert b.cache_read == 6_656
    assert b.total == 3_571 + 727 + 6_656


def test_additive_usage_tolerates_missing_and_none_fields():
    """Providers omit (or null) fields they did not meter — both read as 0."""
    b = from_additive_usage(
        {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": None}
    )
    assert b.cache_read == 0
    assert b.cache_creation == 0


def test_inclusive_usage_subtracts_the_cached_subcount():
    """The inclusive shape (input INCLUDES cached) is normalized by subtraction —
    the one move that kills the double-count bug class."""
    b = from_inclusive_usage(
        {
            "prompt_tokens": 10_000,
            "completion_tokens": 1_200,
            "prompt_tokens_details": {"cached_tokens": 8_000},
            "completion_tokens_details": {"reasoning_tokens": 300},
        }
    )
    assert b.input == 2_000        # 10,000 minus the 8,000 cached
    assert b.cache_read == 8_000
    assert b.cache_creation == 0   # this shape does not report cache writes
    assert b.reasoning == 300
    assert b.total == 10_000 + 1_200  # same billed total, no double count


def test_inclusive_usage_accepts_flat_subcounts():
    b = from_inclusive_usage(
        {"prompt_tokens": 500, "completion_tokens": 50, "cached_tokens": 200}
    )
    assert b.input == 300
    assert b.cache_read == 200


def test_inclusive_usage_refuses_cached_above_prompt():
    """A cached sub-count bigger than the prompt it sits inside is malformed —
    refused loudly, never clamped."""
    with pytest.raises(ValueError):
        from_inclusive_usage({"prompt_tokens": 100, "cached_tokens": 200})


def test_parse_usage_detects_each_shape():
    additive = parse_usage({"input_tokens": 10, "output_tokens": 5})
    inclusive = parse_usage({"prompt_tokens": 10, "completion_tokens": 5})
    assert additive.input == 10 and inclusive.input == 10
    assert additive.output == 5 and inclusive.output == 5


def test_parse_usage_refuses_ambiguous_and_unrecognized():
    with pytest.raises(ValueError):
        parse_usage({"input_tokens": 1, "prompt_tokens": 1})  # both vocabularies
    with pytest.raises(ValueError):
        parse_usage({"tokens": 42})  # neither


def test_usage_fields_must_be_nonnegative_ints():
    with pytest.raises(ValueError):
        from_additive_usage({"input_tokens": "3571"})
    with pytest.raises(ValueError):
        from_additive_usage({"input_tokens": -1})
