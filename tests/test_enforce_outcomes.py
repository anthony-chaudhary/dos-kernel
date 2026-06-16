"""Tests for `enforce_outcomes` — the PEP-feedback fold (docs/365).

The fold reads `OP_ENFORCE` journal records and labels each TARGET the enforcement
seam acted on as a FALSE-DENY (a deny the operator later overrode) or a HELD catch (a
deny that stood). The witness tests pin the load-bearing claims: a deny FOLLOWED BY an
override-admit for the same target is a false-deny; the override may be by a DIFFERENT
holder (the operator); and order matters (an override BEFORE any deny labels nothing).
"""

from __future__ import annotations

import dos.enforce_outcomes as eo
from dos import lane_journal


# ---------------------------------------------------------------------------
# Entry builders — synthetic OP_ENFORCE records in the shape
# `lane_journal.enforce_entry` writes (top-level reason_class/intervention +
# nested proposal.decision). We build them by hand so the test is a pure fold
# over a known journal, no disk.
# ---------------------------------------------------------------------------
def _deny(holder: str, target_path: str, *, seq: int, reason_class: str = "SELF_MODIFY") -> dict:
    return {
        "op": lane_journal.OP_ENFORCE,
        "seq": seq,
        "holder": holder,
        "intervention": "BLOCK",
        "reason_class": reason_class,
        "reason": (
            f"lane would edit the orchestrator's own running code ({target_path}) "
            f"— refusing ({reason_class})."
        ),
        "proposal": {"decision": "deny", "reason_class": reason_class},
    }


def _override(holder: str, target_path: str, *, seq: int, reason_class: str = "SELF_MODIFY") -> dict:
    # The REAL override-admit echoes the refused verdict's reason verbatim (see
    # `pretool_sensor.py` — `[the refused verdict was: {reason}]`), so it carries the
    # SAME `own running code (<path>)` sentence the deny did. The fixture mirrors that
    # so `_enforce_target` resolves the override to the same target as its deny.
    return {
        "op": lane_journal.OP_ENFORCE,
        "seq": seq,
        "holder": holder,
        "intervention": "OBSERVE",
        "reason_class": reason_class,
        "reason": (
            f"lane would edit the orchestrator's own running code ({target_path}) "
            f"— refusing ({reason_class}). [override window armed — admitting]"
        ),
        "proposal": {"decision": "override-admit", "reason_class": reason_class},
    }


# ---------------------------------------------------------------------------
# The witness: a deny LATER overridden is a FALSE-DENY.
# ---------------------------------------------------------------------------
def test_deny_then_override_is_a_false_deny():
    entries = [
        _deny("agentA", "src/dos/arbiter.py", seq=1),
        _override("agentA", "src/dos/arbiter.py", seq=2),
    ]
    outcomes = eo.fold_enforce_outcomes(entries)
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.holder == "agentA"
    assert o.target == "src/dos/arbiter.py"
    assert o.n_denies == 1
    assert o.was_overridden is True
    assert o.is_false_deny is True
    assert o.is_held_catch is False
    m = eo.outcome_metric(outcomes)
    assert m.false_denies == 1
    assert m.held_catches == 0


def test_deny_with_no_override_is_a_held_catch():
    entries = [_deny("agentB", "src/dos/config.py", seq=1)]
    outcomes = eo.fold_enforce_outcomes(entries)
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.is_held_catch is True
    assert o.is_false_deny is False
    m = eo.outcome_metric(outcomes)
    assert m.held_catches == 1
    assert m.false_denies == 0


def test_override_before_any_deny_labels_nothing():
    # An override-admit with no prior deny is the seam never having refused — it is
    # NOT an enforcement outcome and is dropped (n_denies == 0).
    entries = [_override("agentC", "src/dos/config.py", seq=1)]
    outcomes = eo.fold_enforce_outcomes(entries)
    assert outcomes == []


def test_override_then_later_deny_is_held_not_false():
    # Order matters: an override at seq 1, then a fresh deny at seq 2, is a HELD
    # catch (the deny stands; the earlier override predates it and cannot overturn a
    # later refusal). This is the journal-order discipline the fold turns on.
    entries = [
        _override("agentD", "src/dos/config.py", seq=1),
        _deny("agentD", "src/dos/config.py", seq=2),
    ]
    outcomes = eo.fold_enforce_outcomes(entries)
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.n_denies == 1
    assert o.was_overridden is False
    assert o.is_held_catch is True


def test_storm_counts_consecutive_denies():
    entries = [
        _deny("agentE", "src/dos/arbiter.py", seq=1),
        _deny("agentE", "src/dos/arbiter.py", seq=2),
        _deny("agentE", "src/dos/arbiter.py", seq=3),
    ]
    outcomes = eo.fold_enforce_outcomes(entries)
    assert len(outcomes) == 1
    assert outcomes[0].n_denies == 3
    assert outcomes[0].is_held_catch is True
    m = eo.outcome_metric(outcomes)
    assert m.storms == 1
    assert m.storm_denies == 2  # excess beyond the first


def test_storm_then_override_is_a_false_deny():
    # A 3-deny storm the operator finally overrode is a FALSE-DENY (3 wasted denies).
    entries = [
        _deny("agentF", "src/dos/arbiter.py", seq=1),
        _deny("agentF", "src/dos/arbiter.py", seq=2),
        _deny("agentF", "src/dos/arbiter.py", seq=3),
        _override("agentF", "src/dos/arbiter.py", seq=4),
    ]
    outcomes = eo.fold_enforce_outcomes(entries)
    o = outcomes[0]
    assert o.n_denies == 3
    assert o.is_false_deny is True
    m = eo.outcome_metric(outcomes)
    assert m.false_denies == 1
    assert m.storms == 1
    assert m.storm_denies == 2


def test_distinct_targets_kept_separate():
    entries = [
        _deny("agentA", "src/dos/arbiter.py", seq=1),
        _override("agentA", "src/dos/arbiter.py", seq=2),   # false-deny
        _deny("agentB", "src/dos/config.py", seq=3),        # held catch
    ]
    outcomes = eo.fold_enforce_outcomes(entries)
    assert len(outcomes) == 2
    m = eo.outcome_metric(outcomes)
    assert m.false_denies == 1
    assert m.held_catches == 1
    assert m.n_pairs == 2


def test_override_by_a_different_holder_still_labels_false_deny():
    # THE LOAD-BEARING KEY CHOICE (docs/365): the operator who overrides is a
    # DIFFERENT holder than the denied agent. Keying on (holder, target) would miss
    # this; keying on TARGET catches it. An agent is denied editing config.py; the
    # OPERATOR session later admits the same edit → the deny was a false-DENY.
    entries = [
        _deny("agent-uuid-1", "src/dos/config.py", seq=1),
        _override("operator-session-S1", "src/dos/config.py", seq=2),
    ]
    outcomes = eo.fold_enforce_outcomes(entries)
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.is_false_deny is True
    assert o.holder == "agent-uuid-1"  # the first agent that hit the wall, for legibility
    m = eo.outcome_metric(outcomes)
    assert m.false_denies == 1


def test_two_agents_deny_same_target_then_operator_overrides():
    # Two different agents both hit the same wall; the operator overrides. All denies
    # of that target are a single false-DENY outcome (the target was legit).
    entries = [
        _deny("agentA", "src/dos/arbiter.py", seq=1),
        _deny("agentB", "src/dos/arbiter.py", seq=2),
        _override("operator", "src/dos/arbiter.py", seq=3),
    ]
    outcomes = eo.fold_enforce_outcomes(entries)
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.n_denies == 2
    assert o.is_false_deny is True
    m = eo.outcome_metric(outcomes)
    assert m.false_denies == 1
    assert m.storms == 1  # 2 denies of one target = a storm


# ---------------------------------------------------------------------------
# The metric: work is non-negative, higher = better, a false-deny costs what a
# held catch is worth (the "do not buy a catch with a false-deny" stance).
# ---------------------------------------------------------------------------
def test_work_is_non_negative_and_monotone():
    good = eo.outcome_metric([
        eo.EnforceOutcome("a", "t1", "SELF_MODIFY", n_denies=1, was_overridden=False),
        eo.EnforceOutcome("b", "t2", "SELF_MODIFY", n_denies=1, was_overridden=False),
    ])
    mixed = eo.outcome_metric([
        eo.EnforceOutcome("a", "t1", "SELF_MODIFY", n_denies=1, was_overridden=False),
        eo.EnforceOutcome("b", "t2", "SELF_MODIFY", n_denies=1, was_overridden=True),
    ])
    bad = eo.outcome_metric([
        eo.EnforceOutcome("a", "t1", "SELF_MODIFY", n_denies=1, was_overridden=True),
        eo.EnforceOutcome("b", "t2", "SELF_MODIFY", n_denies=1, was_overridden=True),
    ])
    assert good.work > mixed.work > bad.work
    assert good.work >= 0 and mixed.work >= 0 and bad.work >= 0


def test_a_held_catch_offsets_a_false_deny_exactly():
    # The default weight makes a false-deny exactly as costly as a held catch is
    # valuable → one of each (no storm) nets back to the floor.
    m = eo.outcome_metric([
        eo.EnforceOutcome("a", "t1", "SELF_MODIFY", n_denies=1, was_overridden=False),
        eo.EnforceOutcome("b", "t2", "SELF_MODIFY", n_denies=1, was_overridden=True),
    ])
    assert m.work == eo._WORK_FLOOR


def test_empty_journal_folds_to_zero_pairs():
    assert eo.fold_enforce_outcomes([]) == []
    m = eo.outcome_metric([])
    assert m.n_pairs == 0
    assert m.false_denies == 0 and m.held_catches == 0


def test_non_enforce_ops_are_ignored():
    entries = [
        {"op": lane_journal.OP_ACQUIRE, "lane": "src", "loop_ts": "x"},
        {"op": lane_journal.OP_REFUSE, "lane": "src", "reason": "held"},
        _deny("agentA", "src/dos/arbiter.py", seq=3),
    ]
    outcomes = eo.fold_enforce_outcomes(entries)
    assert len(outcomes) == 1
    assert outcomes[0].holder == "agentA"


# ---------------------------------------------------------------------------
# Lockstep with `decisions` — the OP_ENFORCE record grammar has ONE home; both
# readers must parse it identically. Pin the three tiny helpers in lockstep.
# ---------------------------------------------------------------------------
def test_enforce_readers_match_decisions():
    from dos import decisions
    e = _deny("agentA", "src/dos/arbiter.py", seq=1)
    assert eo._enforce_target(e) == decisions._enforce_target(e)
    assert eo._enforce_reason_class(e) == decisions._enforce_reason_class(e)
    assert eo._enforce_decision_tag(e) == decisions._enforce_decision_tag(e)
    ov = _override("agentA", "src/dos/arbiter.py", seq=2)
    assert eo._enforce_decision_tag(ov) == decisions._enforce_decision_tag(ov)
