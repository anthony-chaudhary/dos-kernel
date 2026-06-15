"""Pin the skill_span wall (docs/352 — the span the harness won't fence).

These tests are the FALSIFIERS made executable. Each would go red if the wall
overstated what it can honestly say:

  * `test_clean_session_attributes_both` — when the env DID author a usage
    snapshot at each fence, the wall must report a real duration AND a token delta
    that equals the snapshot difference exactly. If it can't ground both, the wall
    buys nothing.
  * `test_no_snapshot_refuses_tokens_keeps_duration` — the LOAD-BEARING honesty
    falsifier: with no usage snapshots, the wall must report the real duration and
    REFUSE the token number (`tokens is None`), never invent a proportional split.
    A wall that guesses a number no environment authored is the self-report DOS
    exists to reject.
  * `test_open_span_is_unattributed` — a skill fired as the last record has no end
    fence; the wall must say UNATTRIBUTED, not claim a zero-length span.
  * `test_stands_on_real_spend_primitive` — the token delta must BE a
    `dos.spend.SpendBreakdown` (the same primitive the kernel's efficiency ladder
    reads), mirroring plan_price's "stands on the real kernel predicate" falsifier.
    A reimplementation would be a different, unverified claim.
  * `test_non_monotonic_usage_refused` — a backwards usage snapshot must RAISE
    (refused loudly), never clamp to zero — the dos.spend double-count discipline.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from skill_span import (  # noqa: E402
    SCENARIOS,
    SpanVerdict,
    StreamRecord,
    fence_spans,
)
from dos.spend import SpendBreakdown  # noqa: E402


def test_clean_session_attributes_both():
    """The lift in the light: both fences snapshotted → duration + grounded tokens."""
    spans = fence_spans(SCENARIOS["clean_with_snapshots"])
    assert len(spans) == 2
    first, second = spans

    assert first.name == "deep-research"
    assert first.verdict is SpanVerdict.ATTRIBUTED
    assert first.duration_ms == 40_000  # 10:00:00 -> 10:00:40
    # The token delta equals the snapshot difference EXACTLY (not a split, not a guess):
    # start total 1200 (1000+200), end total 14800 (9000+1800+4000) -> 13600.
    assert first.tokens is not None
    assert first.tokens.total == 13_600

    assert second.name == "code-review"
    assert second.verdict is SpanVerdict.ATTRIBUTED
    assert second.duration_ms == 50_000  # 10:00:40 -> 10:01:30
    # end total 21600 (12000+2600+7000) - start total 14800 -> 6800.
    assert second.tokens is not None
    assert second.tokens.total == 6_800


def test_no_snapshot_refuses_tokens_keeps_duration():
    """The honesty floor: no usage snapshot → real duration, REFUSED tokens.

    The wall must NOT manufacture a token number. Duration is grounded in the
    timestamps it genuinely has; tokens are honestly absent (None), and the verdict
    names why. This is the whole point of the wall — refusing what no env authored.
    """
    spans = fence_spans(SCENARIOS["no_usage_snapshots"])
    assert len(spans) == 2
    for sp in spans:
        assert sp.verdict is SpanVerdict.DURATION_ONLY
        assert sp.duration_ms is not None and sp.duration_ms > 0  # duration is real
        assert sp.tokens is None  # tokens REFUSED, never guessed
        assert "usage snapshot" in sp.reason


def test_open_span_is_unattributed():
    """A dangling final skill fire has no end fence → UNATTRIBUTED, not a 0-ms lie."""
    spans = fence_spans(SCENARIOS["open_final_span"])
    assert len(spans) == 1
    sp = spans[0]
    assert sp.verdict is SpanVerdict.UNATTRIBUTED
    assert sp.duration_ms is None  # no claimed duration for an unclosed span
    assert sp.tokens is None
    assert sp.end_ts is None


def test_stands_on_real_spend_primitive():
    """The token delta is a real dos.spend.SpendBreakdown — not a reimplementation.

    Mirrors plan_price.test_price_stands_on_the_real_kernel_predicate: the proof is
    only worth something if it rides the same primitive the shipped kernel trusts.
    """
    spans = fence_spans(SCENARIOS["clean_with_snapshots"])
    attributed = [s for s in spans if s.verdict is SpanVerdict.ATTRIBUTED]
    assert attributed  # there is at least one grounded span to check
    for sp in attributed:
        assert isinstance(sp.tokens, SpendBreakdown)
        # The breakdown carries the disjoint inside, not just a scalar — so a
        # consumer can see input/output/cache share, the kernel's spend vocabulary.
        assert sp.tokens.total == (
            sp.tokens.input + sp.tokens.output
            + sp.tokens.cache_read + sp.tokens.cache_creation
        )


def test_non_monotonic_usage_refused():
    """A backwards usage snapshot RAISES (refused loudly), never clamps to zero.

    The end fence bills FEWER tokens than the start — impossible for a cumulative
    counter. The wall must not silently produce a negative or zero delta; it must
    refuse, the dos.spend 'a silently-mended usage record is how double-counts ship'
    discipline. The raise comes from SpendBreakdown.of's own non-negativity check.
    """
    records = [
        StreamRecord(
            ts="2026-06-15T13:00:00Z", tool_name="Skill", skill="x",
            usage={"input_tokens": 5000, "output_tokens": 1000},
        ),
        StreamRecord(
            ts="2026-06-15T13:00:10Z", tool_name="Edit",
            usage={"input_tokens": 100, "output_tokens": 10},  # backwards — below start
        ),
    ]
    with pytest.raises(ValueError):
        fence_spans(records)


def test_session_with_no_skill_is_empty():
    """A session that never loaded a skill fences no spans — the honest empty."""
    records = [
        StreamRecord(ts="2026-06-15T14:00:00Z", tool_name="WebSearch"),
        StreamRecord(ts="2026-06-15T14:00:05Z", tool_name="Edit"),
    ]
    assert fence_spans(records) == []
