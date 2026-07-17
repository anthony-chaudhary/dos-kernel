"""Tests for `dos.salience` — the true-but-PARKED (prevent-silent-loss) verdict (docs/391).

Groups:
  * the abstain floor — no policy / no evidence / no signal → INDETERMINATE (RETAIN);
  * the park rungs — declared / superseded / unreachable / not-in-hotpath, each a typed,
    RECOVERABLE PARKED (PARKED ≠ delete);
  * the MEASURED rung — low contribution on enough trials → PARKED(LOW_CONTRIBUTION),
    with the thin-evidence floor (never park on too few trials — the `retire` ceiling);
  * the decision ORDER — the more-deliberate / harder signal wins;
  * LIVE + the honesty boundary (LIVE is "no park-reason fired", NEVER "important");
  * the prevent-silent-loss invariant — every state is RETAINED; `partition` drops nothing;
  * fail-safe — None inputs never raise and degrade toward RETAIN, never toward a drop;
  * `to_dict` round-trip + the enum helper asymmetry;
  * the `dos salience` CLI verb — the verdict IS the exit code (LIVE 0, PARKED 3,
    INDETERMINATE 4, contract error 2), published in `dos doctor --json`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from dos import salience as _salience
from dos.salience import (
    GENERIC_PARK_REASONS,
    GENERIC_SALIENCE_POLICY,
    PARK_LOW_CONTRIBUTION,
    PARK_NOT_IN_HOTPATH,
    PARK_SUPERSEDED,
    PARK_UNREACHABLE,
    Salience,
    SalienceEvidence,
    SaliencePartition,
    SaliencePolicy,
    SalienceVerdict,
    classify,
    partition,
)


# ---------------------------------------------------------------------------
# The abstain floor — no rules / no evidence → INDETERMINATE (which means RETAIN).
# ---------------------------------------------------------------------------

def test_no_policy_is_indeterminate():
    """No rules → cannot judge → abstain → RETAIN (never a silent drop, never a false LIVE)."""
    v = classify(SalienceEvidence(label="x", default_on=False), policy=None)
    assert v.state is Salience.INDETERMINATE
    assert not v.is_live
    assert not v.is_parked
    assert v.is_retained  # the floor still retains


def test_none_evidence_is_indeterminate_not_a_crash():
    v = classify(None)
    assert v.state is Salience.INDETERMINATE
    assert v.label == ""


def test_no_signal_is_indeterminate():
    """An item with no usefulness evidence at all → abstain (distinct from a confident LIVE)."""
    v = classify(SalienceEvidence(label="bare"))
    assert v.state is Salience.INDETERMINATE
    assert v.label == "bare"


def test_superseded_false_alone_is_not_a_signal():
    """`superseded=False` is the safe default, not evidence — so it abstains, never LIVE."""
    v = classify(SalienceEvidence(label="x", superseded=False))
    assert v.state is Salience.INDETERMINATE


# ---------------------------------------------------------------------------
# The park rungs — each a typed, RECOVERABLE PARKED.
# ---------------------------------------------------------------------------

def test_not_in_hotpath_is_parked():
    """THE docs/391 canonical case: off the default path → PARKED(NOT_IN_HOTPATH), retained."""
    v = classify(SalienceEvidence(label="F12", default_on=False))
    assert v.state is Salience.PARKED
    assert v.reason_class == PARK_NOT_IN_HOTPATH
    assert v.is_parked
    assert v.is_retained  # parked is NOT dropped


def test_unreachable_is_parked():
    v = classify(SalienceEvidence(label="dead", reachable=False))
    assert v.state is Salience.PARKED
    assert v.reason_class == PARK_UNREACHABLE


def test_superseded_is_parked():
    v = classify(SalienceEvidence(label="old", superseded=True))
    assert v.state is Salience.PARKED
    assert v.reason_class == PARK_SUPERSEDED


def test_declared_reason_is_parked_with_that_class():
    """A host's own typed park class is honored verbatim (the open extension point)."""
    v = classify(SalienceEvidence(label="z", declared_reason="OUT_OF_SCOPE"))
    assert v.state is Salience.PARKED
    assert v.reason_class == "OUT_OF_SCOPE"
    assert v.reason_class not in GENERIC_PARK_REASONS  # a host class, not a kernel default


def test_reachable_true_does_not_park_for_reachability():
    """Only an explicit False parks; True is a positive signal → LIVE."""
    v = classify(SalienceEvidence(label="ok", reachable=True))
    assert v.state is Salience.LIVE


def test_reachable_none_never_parks():
    """Unknown reachability (None) never parks — the fail-safe (absence never loses)."""
    v = classify(SalienceEvidence(label="ok", default_on=True, reachable=None))
    assert v.state is Salience.LIVE  # default_on=True is the signal; None reachability is ignored


# ---------------------------------------------------------------------------
# The MEASURED rung — the retire bridge, but PARK not DROP, with the thin-evidence floor.
# ---------------------------------------------------------------------------

def test_measured_rung_off_by_default():
    """The generic policy leaves the measured rung OFF: low contribution alone never parks."""
    v = classify(SalienceEvidence(label="m", contribution=0.0, trials=100))
    assert v.state is Salience.LIVE  # contribution is a signal, but the rung is unarmed → LIVE


def test_low_contribution_on_enough_trials_is_parked():
    pol = SaliencePolicy(min_contribution=0.1, min_trials=5)
    v = classify(SalienceEvidence(label="m", contribution=0.0, trials=9), policy=pol)
    assert v.state is Salience.PARKED
    assert v.reason_class == PARK_LOW_CONTRIBUTION


def test_thin_evidence_never_parks():
    """Below the trials floor the measured rung ABSTAINS — a sparsely-measured truth stays LIVE."""
    pol = SaliencePolicy(min_contribution=0.1, min_trials=5)
    v = classify(SalienceEvidence(label="m", contribution=0.0, trials=2), policy=pol)
    assert v.state is Salience.LIVE  # contribution present (signal) but trials too thin to park


def test_contribution_at_floor_does_not_park():
    """Strict `<`: contribution exactly at the floor is NOT below it → not parked (LIVE)."""
    pol = SaliencePolicy(min_contribution=0.1, min_trials=5)
    v = classify(SalienceEvidence(label="m", contribution=0.1, trials=9), policy=pol)
    assert v.state is Salience.LIVE


def test_contribution_above_floor_is_live():
    pol = SaliencePolicy(min_contribution=0.1, min_trials=5)
    v = classify(SalienceEvidence(label="m", contribution=0.9, trials=9), policy=pol)
    assert v.state is Salience.LIVE


def test_trials_exactly_at_floor_can_park():
    """`trials >= min_trials` — exactly at the floor is enough to consult the rung."""
    pol = SaliencePolicy(min_contribution=0.1, min_trials=5)
    v = classify(SalienceEvidence(label="m", contribution=0.0, trials=5), policy=pol)
    assert v.state is Salience.PARKED
    assert v.reason_class == PARK_LOW_CONTRIBUTION


def test_measured_rung_disarmed_when_min_trials_zero():
    """min_trials=0 leaves the rung off even with a contribution floor set (the `> 0` gate)."""
    pol = SaliencePolicy(min_contribution=0.5, min_trials=0)
    v = classify(SalienceEvidence(label="m", contribution=0.0, trials=100), policy=pol)
    assert v.state is Salience.LIVE


# ---------------------------------------------------------------------------
# The decision ORDER — the more-deliberate / harder signal wins.
# ---------------------------------------------------------------------------

def test_declared_reason_beats_superseded():
    """The host's explicit declared reason is honored before the mechanical rungs."""
    v = classify(SalienceEvidence(label="z", declared_reason="OPERATOR_PARK", superseded=True))
    assert v.reason_class == "OPERATOR_PARK"


def test_superseded_beats_unreachable_and_default_off():
    v = classify(SalienceEvidence(label="z", superseded=True, reachable=False, default_on=False))
    assert v.reason_class == PARK_SUPERSEDED


def test_unreachable_beats_default_off():
    v = classify(SalienceEvidence(label="z", reachable=False, default_on=False))
    assert v.reason_class == PARK_UNREACHABLE


def test_a_disarmed_declared_rung_falls_through():
    """If park_declared is off, a declared_reason is ignored and the next rung decides."""
    pol = SaliencePolicy(park_superseded=True, park_declared=False)
    v = classify(SalienceEvidence(label="z", declared_reason="X", superseded=True), policy=pol)
    assert v.reason_class == PARK_SUPERSEDED  # the declared rung was off; superseded fired


# ---------------------------------------------------------------------------
# LIVE + the honesty boundary.
# ---------------------------------------------------------------------------

def test_clean_live():
    v = classify(SalienceEvidence(label="hot", reachable=True, default_on=True))
    assert v.state is Salience.LIVE
    assert v.is_live
    assert v.reason_class == ""


def test_live_is_not_a_claim_of_importance():
    """THE honesty boundary: LIVE = 'no park-reason fired', NOT 'this is important'.

    A trivial-but-reachable thing is LIVE (shape/mechanics, not worth). If this ever
    becomes a worth judgment the module has drifted from W2-mechanics into W3-semantics —
    the same trap `answer_shape` guards one axis over.
    """
    v = classify(SalienceEvidence(label="trivial", reachable=True))
    assert v.state is Salience.LIVE
    assert "NOT a claim of importance" in v.reason


# ---------------------------------------------------------------------------
# The prevent-silent-loss invariant.
# ---------------------------------------------------------------------------

def test_every_state_is_retained():
    """No salience state ever means delete — the load-bearing invariant."""
    assert Salience.LIVE.is_retained
    assert Salience.PARKED.is_retained
    assert Salience.INDETERMINATE.is_retained


def test_partition_drops_nothing():
    """Every input lands in exactly one bucket; total == input count (no silent loss)."""
    items = [
        SalienceEvidence(label="a", default_on=False),     # PARKED
        SalienceEvidence(label="b", reachable=True),         # LIVE
        SalienceEvidence(label="c"),                          # INDETERMINATE
        SalienceEvidence(label="d", superseded=True),        # PARKED
        SalienceEvidence(label="e", default_on=True),        # LIVE
    ]
    part = partition(items)
    assert isinstance(part, SaliencePartition)
    assert part.total == len(items)
    assert {v.label for v in part.parked} == {"a", "d"}
    assert {v.label for v in part.live} == {"b", "e"}
    assert {v.label for v in part.indeterminate} == {"c"}


def test_partition_empty_is_empty():
    part = partition([])
    assert part.total == 0
    assert part.live == part.parked == part.indeterminate == ()


def test_partition_surfaces_park_reasons():
    """The parked bucket carries each item's typed reason — the recovery affordance."""
    part = partition([SalienceEvidence(label="a", default_on=False)])
    assert part.parked[0].reason_class == PARK_NOT_IN_HOTPATH


# ---------------------------------------------------------------------------
# The re-entry affordance — the load-bearing distinction from `retire` (evict-to-archive).
# ---------------------------------------------------------------------------

def test_parked_carries_a_reactivation_line():
    """Every PARKED verdict carries a concrete 'how to pull it back' line — recoverable, not lost."""
    v = classify(SalienceEvidence(label="a", default_on=False))
    assert v.reactivation
    assert "re-activates" in v.reactivation


def test_live_and_indeterminate_have_no_reactivation():
    """A non-parked thing needs no re-entry line — it is already in/near the hotpath."""
    assert classify(SalienceEvidence(label="a", reachable=True)).reactivation == ""
    assert classify(SalienceEvidence(label="a")).reactivation == ""


def test_each_kernel_park_reason_has_its_own_line():
    """The four kernel park classes resolve to distinct, specific re-entry lines."""
    lines = {r: _salience.reactivation_for(r) for r in GENERIC_PARK_REASONS}
    assert len(set(lines.values())) == len(GENERIC_PARK_REASONS)  # all distinct
    assert all(lines.values())  # none empty


def test_host_declared_reason_falls_to_generic_line():
    """A host's own park class still gets a recovery affordance (the generic fallback)."""
    v = classify(SalienceEvidence(label="z", declared_reason="OUT_OF_SCOPE"))
    assert v.reactivation == _salience.reactivation_for("OUT_OF_SCOPE")
    assert v.reactivation  # never empty — even an unknown class is recoverable, never silently lost


def test_reactivation_in_to_dict():
    v = classify(SalienceEvidence(label="a", default_on=False))
    assert v.to_dict()["reactivation"] == v.reactivation


# ---------------------------------------------------------------------------
# Fail-safe — never raises, degrades toward RETAIN.
# ---------------------------------------------------------------------------

def test_none_policy_with_parkable_evidence_retains():
    """A null policy must RETAIN a would-be-parked item (abstain), never crash or drop."""
    v = classify(SalienceEvidence(label="x", reachable=False, superseded=True), policy=None)
    assert v.state is Salience.INDETERMINATE  # retained, surfaced — not parked-away, not lost


def test_classify_never_raises_on_weird_inputs():
    for ev in (None, SalienceEvidence(), SalienceEvidence(label="", contribution=-1.0, trials=-5)):
        v = classify(ev)  # must not raise
        assert isinstance(v, SalienceVerdict)


# ---------------------------------------------------------------------------
# to_dict + enum helpers.
# ---------------------------------------------------------------------------

def test_to_dict_round_trip():
    v = classify(SalienceEvidence(label="a", default_on=False))
    d = v.to_dict()
    assert d["state"] == "PARKED"
    assert d["reason_class"] == "NOT_IN_HOTPATH"
    assert d["is_parked"] is True
    assert d["is_live"] is False
    assert d["is_retained"] is True
    assert d["label"] == "a"
    assert d["reason"]


def test_partition_to_dict():
    part = partition([SalienceEvidence(label="a", default_on=False), SalienceEvidence(label="b", reachable=True)])
    d = part.to_dict()
    assert d["total"] == 2
    assert len(d["parked"]) == 1 and len(d["live"]) == 1
    assert d["parked"][0]["reason_class"] == "NOT_IN_HOTPATH"


def test_enum_helper_asymmetry():
    """LIVE is live-not-parked; PARKED is parked-not-live; INDETERMINATE is neither — all retained."""
    assert Salience.LIVE.is_live and not Salience.LIVE.is_parked
    assert Salience.PARKED.is_parked and not Salience.PARKED.is_live
    assert not Salience.INDETERMINATE.is_live and not Salience.INDETERMINATE.is_parked


def test_str_enum_round_trips_as_token():
    assert str(Salience.PARKED) == "PARKED"
    assert Salience("LIVE") is Salience.LIVE


def test_generic_policy_arms_the_mechanical_rungs_only():
    assert GENERIC_SALIENCE_POLICY.park_unreachable
    assert GENERIC_SALIENCE_POLICY.park_default_off
    assert GENERIC_SALIENCE_POLICY.park_superseded
    assert GENERIC_SALIENCE_POLICY.park_declared
    assert GENERIC_SALIENCE_POLICY.min_trials == 0  # measured rung off by default


# ---------------------------------------------------------------------------
# The `dos salience` CLI verb — the verdict IS the exit code.
# ---------------------------------------------------------------------------


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", *args],
        cwd=str(cwd), capture_output=True, text=True,
    )


def test_cli_parked_not_in_hotpath_exit_three(tmp_path: Path):
    r = _run_cli("salience", "--label", "F12", "--default-off", cwd=tmp_path)
    assert r.returncode == 3, r.stderr
    assert "PARKED" in r.stdout


def test_cli_live_exit_zero(tmp_path: Path):
    r = _run_cli("salience", "--label", "F12", "--reachable", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "LIVE" in r.stdout


def test_cli_no_evidence_is_indeterminate_exit_four(tmp_path: Path):
    r = _run_cli("salience", "--label", "F12", cwd=tmp_path)
    assert r.returncode == 4, r.stderr
    assert "INDETERMINATE" in r.stdout


def test_cli_superseded_parked(tmp_path: Path):
    r = _run_cli("salience", "--label", "old", "--superseded", cwd=tmp_path)
    assert r.returncode == 3, r.stderr
    assert "PARKED" in r.stdout


def test_cli_declared_reason(tmp_path: Path):
    r = _run_cli("salience", "--label", "z", "--reason", "OUT_OF_SCOPE", "--json", cwd=tmp_path)
    assert r.returncode == 3, r.stderr
    assert json.loads(r.stdout)["reason_class"] == "OUT_OF_SCOPE"


def test_cli_measured_rung(tmp_path: Path):
    r = _run_cli("salience", "--label", "m", "--contribution", "0.0",
                 "--trials", "9", "--min-contribution", "0.1", "--min-trials", "5", cwd=tmp_path)
    assert r.returncode == 3, r.stderr
    assert "PARKED" in r.stdout


def test_cli_thin_evidence_does_not_park(tmp_path: Path):
    r = _run_cli("salience", "--label", "m", "--contribution", "0.0",
                 "--trials", "2", "--min-contribution", "0.1", "--min-trials", "5", cwd=tmp_path)
    assert r.returncode == 0, r.stderr  # thin trials → not parked → LIVE
    assert "LIVE" in r.stdout


def test_cli_mutually_exclusive_reachability(tmp_path: Path):
    """--reachable and --unreachable cannot both be given (an argparse usage fault)."""
    r = _run_cli("salience", "--label", "x", "--reachable", "--unreachable", cwd=tmp_path)
    assert r.returncode == 2, (r.stdout, r.stderr)


def test_cli_json(tmp_path: Path):
    r = _run_cli("salience", "--label", "F12", "--default-off", "--json", cwd=tmp_path)
    assert r.returncode == 3, r.stderr
    obj = json.loads(r.stdout)
    assert obj["state"] == "PARKED"
    assert obj["reason_class"] == "NOT_IN_HOTPATH"
    assert obj["is_retained"] is True
    assert obj["reactivation"]  # the re-entry affordance rides the machine output
    assert obj["label"] == "F12"


def test_cli_no_plan(tmp_path: Path):
    """No-plan rail: runs in a bare dir with no git, no plan, no .dos/ (read-only)."""
    r = _run_cli("salience", "--label", "x", "--reachable", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / ".dos").exists()


def test_cli_exit_codes_published_in_doctor(tmp_path: Path):
    """The exit-code map is published in `dos doctor --json exit_codes` (anti-drift)."""
    r = _run_cli("doctor", "--workspace", str(tmp_path), "--json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    ec = json.loads(r.stdout)["exit_codes"]["salience"]
    assert ec["LIVE"] == 0
    assert ec["PARKED"] == 3
    assert ec["INDETERMINATE"] == 4
    assert ec["contract_error"] == 2
