"""Deterministic tests for the skill_dos_ablation harness (issue #176) — $0, no key, no network.

The load-bearing assertions mirror the issue's done-condition:
  * >=3 skills, each scored as original vs -dos;
  * the silent over-claim rate is computed against the EXPLICIT rigged-failure denominator;
  * every GROUNDABLE -dos variant drives the over-claim rate to 0 (refuses the rigged failure);
  * the NEGATIVE skill shows DOS NOT helping, without crashing or hiding it;
  * the cost axis (added tokens/latency) is reported per variant.
"""

from __future__ import annotations

from benchmark.skill_dos_ablation import corpus as C
from benchmark.skill_dos_ablation import harness as H
from benchmark.skill_dos_ablation.witnesses import (
    FAILED, UNWITNESSABLE, WITNESSED_OK,
    witness_phase_shipped, witness_worker_alive, witness_loop_advanced,
    witness_memory_fresh, witness_prose_taste,
)


# ----------------------------------------------------------------- corpus byte-fidelity (the 4 shapes)
def test_corpus_has_at_least_three_groundable_skills_plus_the_negative():
    skills = set(C.SKILLS)
    assert C.NEGATIVE_SKILL in skills
    groundable = skills - {C.NEGATIVE_SKILL}
    assert len(groundable) >= 3, f"need >=3 DOS-groundable skills, got {sorted(groundable)}"


def test_each_skill_has_one_rigged_failure_and_one_clean():
    tasks = C.corpus()
    for skill in C.SKILLS:
        st = [t for t in tasks if t["skill"] == skill]
        truths = sorted(t["truth"] for t in st)
        assert truths == ["FAILED", "OK"], f"{skill}: {truths}"


def test_rigged_failure_denominator_is_explicit_and_correct():
    assert H.total_failed() == len(C.SKILLS)  # one rigged failure per skill


def test_the_four_named_rigged_shapes_each_witness_FAILED():
    tasks = {t["task_id"]: t for t in C.corpus()}
    # (1) claimed-but-not-shipped commit -> git ancestry FAILED
    assert witness_phase_shipped(tasks["ship-rigged-not-landed"]) == FAILED
    # (2) fan-out worker that died synthetically -> env terminal FAILED
    assert witness_worker_alive(tasks["fanout-rigged-worker-died"]) == FAILED
    # (3) tool-loop with no progress (identical result bytes) -> tool-stream FAILED
    assert witness_loop_advanced(tasks["loop-rigged-no-progress"]) == FAILED
    # (4) recalled memory now stale -> recall staleness FAILED
    assert witness_memory_fresh(tasks["memory-rigged-stale"]) == FAILED


def test_clean_tasks_each_witness_WITNESSED_OK():
    tasks = {t["task_id"]: t for t in C.corpus()}
    assert witness_phase_shipped(tasks["ship-clean-landed"]) == WITNESSED_OK
    assert witness_worker_alive(tasks["fanout-clean-all-done"]) == WITNESSED_OK
    assert witness_loop_advanced(tasks["loop-clean-advanced"]) == WITNESSED_OK
    assert witness_memory_fresh(tasks["memory-clean-fresh"]) == WITNESSED_OK


# -------------------------------------------------------------------------- the metric (over-claim)
def test_original_variant_overclaims_100pct_on_every_rigged_failure():
    """The forgeable seam: the original reads only the agent's claim, so it leaks on every rigged task."""
    scores = H.compute()
    for skill in C.SKILLS:
        s = scores[skill]["original"]
        assert s.overclaim_rate == 1.0, f"{skill} original {s.overclaim_rate}"
        assert s.added_tokens == 0 and s.added_ms == 0  # original pays no witness cost


def test_groundable_dos_variant_drives_overclaim_to_zero():
    scores = H.compute()
    for skill in C.SKILLS:
        if skill == C.NEGATIVE_SKILL:
            continue
        s = scores[skill]["-dos"]
        assert s.overclaim_rate == 0.0, f"{skill} -dos {s.overclaim_rate}"
        assert s.refused == s.n_failed   # it REFUSED the rigged failure (advisory, not 'caught')


def test_negative_skill_shows_dos_not_helping_without_crashing():
    """THE NEGATIVE (docs/333): a pure-prose skill. DOS has no env byte to ground on, so the -dos
    over-claim rate equals the original's — and every rigged failure is UNWITNESSABLE. Shown, not hidden."""
    scores = H.compute()
    neg = scores[C.NEGATIVE_SKILL]
    assert neg["-dos"].overclaim_rate == neg["original"].overclaim_rate == 1.0
    assert neg["-dos"].unwitnessable == neg["-dos"].n_failed
    # the rung is structurally UNWITNESSABLE on every prose task
    for t in C.corpus():
        if t["skill"] == C.NEGATIVE_SKILL:
            assert witness_prose_taste(t) == UNWITNESSABLE


def test_cost_axis_is_reported_for_the_dos_variant():
    scores = H.compute()
    for skill in C.SKILLS:
        s = scores[skill]["-dos"]
        assert s.added_tokens >= 0 and s.added_ms >= 0
    # the groundable skills pay a real witness cost; the negative's cost buys nothing
    assert scores["ship-verify"]["-dos"].added_tokens > 0
    assert scores["ship-verify"]["-dos"].added_ms > 0


# ----------------------------------------------------------------------- the in-band falsifier + CLI
def test_invariants_hold_on_the_committed_corpus():
    assert H.check_invariants(H.compute()) == []


def test_main_exit_zero_and_prints_rates_and_negative(capsys):
    rc = H.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "RIGGED-FAILURE DENOMINATOR: 5" in out
    assert "NEGATIVE" in out and "DOS does NOT help" in out
    assert "100% -> 0%" in out      # a groundable refusal
    assert "100% -> 100%" in out    # the negative, no improvement


def test_check_subcommand_exit_zero():
    assert H.main(["--check"]) == 0


def test_json_emits_denominator_and_rows():
    import io
    import json
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = H.main(["--json"])
    payload = json.loads(buf.getvalue())
    assert rc == 0
    assert payload["rigged_failure_denominator"] == 5
    assert payload["negative_skill"] == C.NEGATIVE_SKILL
    assert payload["invariants_held"] is True
    assert payload["rows"]["ship-verify"]["-dos"]["overclaim_rate"] == 0.0
    assert payload["rows"][C.NEGATIVE_SKILL]["-dos"]["overclaim_rate"] == 1.0
