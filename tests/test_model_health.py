"""Tests for `dos.model_health` — the per-MODEL fleet death rollup.

The projection that answers "WHICH model is down across a fleet's children and
grandchildren?" by folding already-adjudicated `result_state` verdicts, grouped by
the model NAME each MODEL_UNAVAILABLE death carries in its error text. The pins:

  * model-name extraction is PURE and best-effort — a parseable name is captured,
    an unparseable one is the honest `UNNAMED_MODEL` sentinel, never a guess.
  * the fold mints ZERO new labels — every death it counts was already adjudicated
    by `result_state` (it only groups).
  * the fail-safe floor carries through — UNREADABLE is LIVE, never a death.
  * `any_model_down` / `reroute_targets` / `headline` are the at-a-glance
    auto-routing surface the goal keys on.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dos import model_health as mh
from dos import result_state as rs


# ── model-name extraction (pure) ─────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("Claude Fable 5 is currently unavailable", "Claude Fable 5"),
    ("The model claude-fable-5 is currently unavailable", "claude-fable-5"),
    ("The requested model 'opus-x' is unavailable", "opus-x"),
    ("`claude-fable-5` is currently unavailable", "claude-fable-5"),
    ("model is unavailable", mh.UNNAMED_MODEL),          # no name → honest sentinel
    ("API Error: 429 Rate limited", mh.UNNAMED_MODEL),   # not an unavailability clause
    ("", mh.UNNAMED_MODEL),
])
def test_model_name_from_text(text, expected):
    assert mh.model_name_from_text(text) == expected


def test_model_name_never_raises_on_junk():
    """Best-effort by construction — junk text yields the sentinel, never an error."""
    for junk in ("\x00\x01", "unavailable unavailable unavailable", "is is is unavailable"):
        assert isinstance(mh.model_name_from_text(junk), str)


# ── the pure fold ────────────────────────────────────────────────────────────

def _v(state, cls=rs.TerminalClass.NONE):
    """A bare ResultStateVerdict for the fold (no transcript needed)."""
    return rs.ResultStateVerdict(state=state, dead=state is not rs.TerminalState.HEALTHY
                                 and state is not rs.TerminalState.UNREADABLE, cls=cls)


def test_fold_groups_model_unavailable_by_name():
    verdicts = [
        _v(rs.TerminalState.SYNTHETIC, rs.TerminalClass.MODEL_UNAVAILABLE),
        _v(rs.TerminalState.SYNTHETIC, rs.TerminalClass.MODEL_UNAVAILABLE),
        _v(rs.TerminalState.SYNTHETIC, rs.TerminalClass.MODEL_UNAVAILABLE),
    ]
    texts = [
        "Claude Fable 5 is currently unavailable",
        "Claude Fable 5 is currently unavailable",
        "opus-x is currently unavailable",
    ]
    sources = ["child:a", "grandchild:b", "child:c"]
    health = mh.fold_model_health(verdicts, sources=sources, texts=texts)
    assert health.any_model_down is True
    assert health.model_unavailable == 3
    # Fable has 2 deaths, opus-x 1 → Fable first.
    assert health.tallies[0].model == "Claude Fable 5"
    assert health.tallies[0].deaths == 2
    assert set(health.tallies[0].sources) == {"child:a", "grandchild:b"}
    assert health.reroute_targets == ("Claude Fable 5", "opus-x")
    assert "MODEL DOWN" in health.headline


def test_fold_partitions_other_states_correctly():
    verdicts = [
        _v(rs.TerminalState.HEALTHY),
        _v(rs.TerminalState.UNREADABLE),  # LIVE — never a death
        _v(rs.TerminalState.SYNTHETIC, rs.TerminalClass.RATE_LIMIT),  # other_dead, not a model-down
        _v(rs.TerminalState.EMPTY),  # other_dead (no deliverable)
        _v(rs.TerminalState.SYNTHETIC, rs.TerminalClass.MODEL_UNAVAILABLE),
    ]
    texts = ["", "", "", "", "claude-x is currently unavailable"]
    health = mh.fold_model_health(verdicts, texts=texts)
    assert health.healthy == 1
    assert health.unreadable == 1
    assert health.other_dead == 2  # rate-limit + empty
    assert health.model_unavailable == 1
    assert health.considered == 5


def test_unreadable_is_never_a_model_down():
    """The fail-safe floor carries through: a read fault must not fabricate a
    model-down (which would route AWAY from a model that may be fine)."""
    health = mh.fold_model_health([_v(rs.TerminalState.UNREADABLE)])
    assert health.any_model_down is False
    assert health.unreadable == 1
    assert health.reroute_targets == ()


def test_unnamed_model_death_is_counted_not_dropped():
    """A MODEL_UNAVAILABLE death whose text carries no parseable name is still a
    model-down the operator must see — counted under the UNNAMED sentinel."""
    health = mh.fold_model_health(
        [_v(rs.TerminalState.SYNTHETIC, rs.TerminalClass.MODEL_UNAVAILABLE)],
        texts=["the model is unavailable"],
    )
    assert health.any_model_down is True
    assert health.model_unavailable == 1
    assert health.tallies[0].model == mh.UNNAMED_MODEL


def test_all_healthy_headline_and_no_reroute():
    health = mh.fold_model_health([_v(rs.TerminalState.HEALTHY), _v(rs.TerminalState.HEALTHY)])
    assert health.any_model_down is False
    assert health.reroute_targets == ()
    assert "all models healthy" in health.headline


def test_missing_texts_degrade_to_unnamed_not_error():
    """texts omitted entirely → every model-down is counted as UNNAMED, never raised."""
    health = mh.fold_model_health(
        [_v(rs.TerminalState.SYNTHETIC, rs.TerminalClass.MODEL_UNAVAILABLE)]
    )
    assert health.model_unavailable == 1
    assert health.tallies[0].model == mh.UNNAMED_MODEL


def test_to_dict_round_trips_json():
    health = mh.fold_model_health(
        [_v(rs.TerminalState.SYNTHETIC, rs.TerminalClass.MODEL_UNAVAILABLE)],
        texts=["Claude Fable 5 is currently unavailable"],
        sources=["grandchild:deep"],
    )
    d = health.to_dict()
    assert d["any_model_down"] is True
    assert d["reroute_targets"] == ["Claude Fable 5"]
    # JSON-serializable (the --json surface).
    json.dumps(d)


# ── the boundary reader (real transcripts) ───────────────────────────────────

def _synthetic_record(text, *, api_status=None):
    return {
        "type": "assistant",
        "isApiErrorMessage": True,
        **({"apiErrorStatus": api_status} if api_status is not None else {}),
        "message": {
            "model": "<synthetic>",
            "role": "assistant",
            "stop_reason": "stop_sequence",
            "content": [{"type": "text", "text": text}],
        },
    }


def _healthy_record(text="the answer is 42"):
    return {
        "type": "assistant",
        "message": {
            "model": "claude-opus-4-8",
            "role": "assistant",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text}],
        },
    }


def _write(tmp_path: Path, records, name) -> Path:
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return p


def test_from_transcripts_attributes_the_down_model(tmp_path):
    """End-to-end: a child and a grandchild both dead on the same down model → the
    rollup names it once, with both as sources, and flags the reroute."""
    child = _write(tmp_path, [_synthetic_record("Claude Fable 5 is currently unavailable")],
                   "child.jsonl")
    grandchild = _write(tmp_path, [_synthetic_record("Claude Fable 5 is currently unavailable")],
                        "grandchild.jsonl")
    healthy = _write(tmp_path, [_healthy_record()], "ok.jsonl")
    health = mh.model_health_from_transcripts(
        [str(child), str(grandchild), str(healthy)],
        sources=["child", "grandchild", "child2"],
    )
    assert health.any_model_down is True
    assert health.model_unavailable == 2
    assert health.healthy == 1
    assert health.tallies[0].model == "Claude Fable 5"
    assert health.tallies[0].deaths == 2
    assert set(health.tallies[0].sources) == {"child", "grandchild"}
    assert health.reroute_targets == ("Claude Fable 5",)


def test_from_transcripts_missing_file_is_unreadable_not_down(tmp_path):
    """A missing transcript is UNREADABLE (LIVE) — never a fabricated model-down."""
    health = mh.model_health_from_transcripts([str(tmp_path / "nope.jsonl")])
    assert health.any_model_down is False
    assert health.unreadable == 1


def test_from_transcripts_default_source_is_the_path(tmp_path):
    child = _write(tmp_path, [_synthetic_record("opus-x is currently unavailable")], "c.jsonl")
    health = mh.model_health_from_transcripts([str(child)])
    assert health.tallies[0].sources == (str(child),)
