"""Tests for `dos.drivers.model_reroute` — the auto-healing actuator (propose-not-enact).

The driver consumes a `model_health` verdict + a host model roster and PROPOSES a
re-dispatch of each down model's stranded units on a sibling. The pins:

  * it PROPOSES, never launches — no subprocess/os/spawn (the watchdog boundary).
  * sibling selection is roster-order preference, excluding the down models.
  * fail-closed: no sibling (every model down) → ESCALATE; an UNNAMED down model →
    ESCALATE (never auto-route past a model we cannot name); never a silent drop.
  * the re-dispatch command substitutes the sibling and is carried, not run.
"""
from __future__ import annotations

import pytest

from dos import model_health as mh
from dos.drivers import model_reroute as mr


def _health_with(*models_and_counts, healthy=0):
    """Build a ModelHealth whose tallies are the given rows. Each row is
    (model, deaths, sources) or (model, deaths, sources, suspended)."""
    tallies = tuple(
        mh.ModelTally(model=row[0], deaths=row[1], sources=tuple(row[2]),
                      suspended=(row[3] if len(row) > 3 else False))
        for row in models_and_counts
    )
    total = sum(t.deaths for t in tallies)
    return mh.ModelHealth(
        tallies=tallies,
        model_unavailable=total,
        other_dead=0,
        healthy=healthy,
        unreadable=0,
        considered=total + healthy,
    )


# ── sibling selection ────────────────────────────────────────────────────────

def test_picks_first_non_down_roster_model():
    health = _health_with(("Claude Fable 5", 2, ["child:a", "grandchild:b"]))
    roster = ["Claude Fable 5", "claude-opus-4-8", "claude-sonnet-4-6"]
    props = mr.propose_reroutes(health, roster)
    assert len(props) == 1
    assert props[0].action is mr.RerouteAction.REROUTE
    assert props[0].down_model == "Claude Fable 5"
    assert props[0].sibling == "claude-opus-4-8"  # first non-down, roster order
    assert props[0].units == ("child:a", "grandchild:b")


def test_sibling_match_is_case_insensitive():
    """The death text spelling ('Claude Fable 5') and the roster spelling
    ('claude fable 5') must be treated as the same down model."""
    health = _health_with(("Claude Fable 5", 1, ["child:a"]))
    roster = ["claude fable 5", "opus"]
    props = mr.propose_reroutes(health, roster)
    assert props[0].sibling == "opus"


def test_multiple_down_models_each_get_a_proposal():
    health = _health_with(
        ("Claude Fable 5", 3, ["child:a"]),
        ("opus-x", 1, ["grandchild:c"]),
    )
    roster = ["Claude Fable 5", "opus-x", "claude-sonnet-4-6"]
    props = mr.propose_reroutes(health, roster)
    assert len(props) == 2
    # Both down models route to the one non-down sibling.
    assert {p.sibling for p in props} == {"claude-sonnet-4-6"}


# ── fail-closed ──────────────────────────────────────────────────────────────

def test_no_sibling_available_escalates():
    """Every roster model is down → ESCALATE, never a silent drop."""
    health = _health_with(("a", 1, ["child:x"]), ("b", 1, ["child:y"]))
    roster = ["a", "b"]  # both down
    props = mr.propose_reroutes(health, roster)
    assert all(p.action is mr.RerouteAction.ESCALATE for p in props)
    assert all(p.sibling == "" for p in props)


def test_unnamed_down_model_escalates():
    """A model-down whose name could not be parsed must not be auto-routed past —
    we cannot prove a sibling differs from an unnamed model."""
    health = _health_with((mh.UNNAMED_MODEL, 1, ["grandchild:z"]))
    props = mr.propose_reroutes(health, ["opus", "sonnet"])
    assert props[0].action is mr.RerouteAction.ESCALATE
    assert "unnamed" in props[0].reason.lower()


def test_empty_roster_escalates():
    health = _health_with(("Claude Fable 5", 1, ["child:a"]))
    props = mr.propose_reroutes(health, [])
    assert props[0].action is mr.RerouteAction.ESCALATE


def test_suspended_model_escalates_even_with_a_sibling_available():
    """#140 in the actuator: a SUSPENDED model escalates even when a healthy
    sibling IS in the roster — a silent reroute is the wrong heal (the sibling may
    also be pulled, and the operator must see the suspension)."""
    health = _health_with(("Claude Fable 5", 2, ["child:a", "grandchild:b"], True))
    # claude-opus-4-8 is available, but a suspension must NOT silently reroute.
    props = mr.propose_reroutes(health, ["Claude Fable 5", "claude-opus-4-8"])
    assert props[0].action is mr.RerouteAction.ESCALATE
    assert props[0].sibling == ""
    assert "suspend" in props[0].reason.lower()


def test_suspended_escalate_outranks_sibling_pick():
    """The suspension escalate is checked BEFORE the sibling pick — a roster with
    a perfectly good sibling cannot turn a suspension into a reroute."""
    health = _health_with(("opus-x", 1, ["child:a"], True))
    props = mr.propose_reroutes(health, ["sonnet", "haiku"])  # both available
    assert props[0].action is mr.RerouteAction.ESCALATE


def test_no_model_down_yields_no_proposals():
    healthy = mh.fold_model_health([])  # all healthy, nothing down
    assert mr.propose_reroutes(healthy, ["opus"]) == ()


# ── command substitution ─────────────────────────────────────────────────────

def test_command_template_substitutes_sibling():
    health = _health_with(("Claude Fable 5", 1, ["child:a"]))
    roster = ["Claude Fable 5", "claude-opus-4-8"]
    props = mr.propose_reroutes(health, roster,
                                command_template="claude -p --model {model} --resume child:a")
    assert props[0].command == "claude -p --model claude-opus-4-8 --resume child:a"


def test_no_template_means_no_command():
    health = _health_with(("Claude Fable 5", 1, ["child:a"]))
    props = mr.propose_reroutes(health, ["Claude Fable 5", "opus"])
    assert props[0].command == ""


# ── the actuation boundary: PROPOSE, never launch ────────────────────────────

def test_proposes_does_not_launch(monkeypatch):
    """The driver must never spawn — monkeypatch subprocess/os to explode and prove
    propose_reroutes touches neither (the watchdog propose-not-enact boundary)."""
    import os
    import subprocess

    def _boom(*a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("model_reroute must PROPOSE, never launch a process")

    monkeypatch.setattr(subprocess, "run", _boom, raising=False)
    monkeypatch.setattr(subprocess, "Popen", _boom, raising=False)
    monkeypatch.setattr(os, "system", _boom, raising=False)

    health = _health_with(("Claude Fable 5", 2, ["child:a", "grandchild:b"]))
    props = mr.propose_reroutes(health, ["Claude Fable 5", "opus"],
                                command_template="claude --model {model}")
    # A REROUTE was proposed (with a command) — but nothing ran.
    assert props[0].action is mr.RerouteAction.REROUTE
    assert props[0].command == "claude --model opus"


# ── rendering + serialization ────────────────────────────────────────────────

def test_render_text_and_to_dict():
    health = _health_with(("Claude Fable 5", 2, ["child:a", "grandchild:b"]))
    props = mr.propose_reroutes(health, ["Claude Fable 5", "opus"],
                                command_template="claude --model {model}")
    txt = mr.render_text(props)
    assert "REROUTE" in txt and "Claude Fable 5 → opus" in txt
    assert "claude --model opus" in txt
    d = props[0].to_dict()
    assert d["action"] == "REROUTE" and d["sibling"] == "opus"


def test_render_empty():
    assert "nothing to reroute" in mr.render_text(())
