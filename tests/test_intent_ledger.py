"""docs/107 Phase 2 — the intent ledger I/O + replay fold, and the resume_plan verdict.

Two surfaces, both pure-testable without a live crash (the `loop_decide`/`liveness`
design value, restated for the resume axis):

  * `intent_ledger` — append/read_all/replay over the run-dir `.jsonl`, mirroring
    `lane_journal`'s ARIES discipline (fsync, torn-tail tolerance, `_CORRUPT`
    sentinel) PLUS the §6 schema gate (an UNREADABLE_NEWER record is refused at
    read, not best-effort-parsed).
  * `resume.resume_plan` — the pure verdict over a frozen `LedgerState` + frozen
    `AncestryFacts`: RESUMABLE / COMPLETE / DIVERGED / UNRESUMABLE, the fail-closed
    claimed-but-not-in-ancestry case, and the non-forgeable-rung guard (§5 req 2).
"""

from __future__ import annotations

import json
from pathlib import Path

from dos import config as _config
from dos import durable_schema as _ds
from dos import intent_ledger as il
from dos import resume as rz
from dos.intent_ledger import LedgerState, VerifiedStep
from dos.resume import AncestryFacts, Resume, ResumePolicy


# ==========================================================================
# intent_ledger — the durable surface (I/O + replay).
# ==========================================================================


def _ledger(tmp_path: Path) -> Path:
    return tmp_path / "intent.jsonl"


def test_append_stamps_run_id_ts_and_schema(tmp_path: Path):
    p = _ledger(tmp_path)
    e = il.append("RID-ABC", il.intent_entry(goal="do the thing", start_sha="aaaa"),
                  path=p)
    assert e["run_id"] == "RID-ABC"
    assert e["ts"]  # stamped
    assert e["schema"] == {"family": "intent-ledger", "version": 1}
    # The file holds exactly one canonical-JSON line.
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["goal"] == "do the thing"


def test_read_all_round_trips_append_order(tmp_path: Path):
    p = _ledger(tmp_path)
    il.append("RID-1", il.intent_entry(goal="g", start_sha="s0",
                                       declared_steps=["s1", "s2"]), path=p)
    il.append("RID-1", il.step_claimed_entry("s1", "c1"), path=p)
    il.append("RID-1", il.step_verified_entry("s1", "c1", via="file-path"), path=p)
    entries = il.read_all(path=p)
    assert [e["op"] for e in entries] == ["INTENT", "STEP_CLAIMED", "STEP_VERIFIED"]


def test_read_all_skips_only_the_torn_final_line(tmp_path: Path):
    p = _ledger(tmp_path)
    il.append("RID-1", il.intent_entry(goal="g", start_sha="s0"), path=p)
    # Simulate a crash mid-append: a partial trailing line with no newline.
    with p.open("a", encoding="utf-8") as f:
        f.write('{"op": "STEP_CLAIMED", "step_id": "s1"')  # torn — no closing brace
    entries = il.read_all(path=p)
    # The torn final line is dropped (didn't happen); the good record survives.
    assert [e["op"] for e in entries] == ["INTENT"]


def test_read_all_keeps_a_midfile_corrupt_sentinel(tmp_path: Path):
    p = _ledger(tmp_path)
    il.append("RID-1", il.intent_entry(goal="g"), path=p)
    with p.open("a", encoding="utf-8") as f:
        f.write("this is not json\n")  # corrupt, NOT the trailing line...
    il.append("RID-1", il.step_claimed_entry("s1", "c1"), path=p)  # ...this is
    entries = il.read_all(path=p)
    ops = [e["op"] for e in entries]
    assert ops == ["INTENT", "_CORRUPT", "STEP_CLAIMED"]


def test_read_all_schema_gate_refuses_a_too_new_record(tmp_path: Path):
    p = _ledger(tmp_path)
    il.append("RID-1", il.intent_entry(goal="g"), path=p)
    # A record written by a FUTURE kernel (schema v99) — non-additively newer.
    future = {**_ds.tag("intent-ledger", 99), "op": "STEP_VERIFIED",
              "step_id": "s1", "run_id": "RID-1", "ts": "2026-06-03T00:00:00Z"}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(future, sort_keys=True) + "\n")
    # An OLD reader (understands only v1) refuses to parse it as data — it becomes
    # an _UNREADABLE sentinel, NOT a best-effort STEP_VERIFIED.
    entries = il.read_all(path=p, understands=1)
    ops = [e["op"] for e in entries]
    assert ops == ["INTENT", "_CORRUPT"]
    assert entries[1]["_unreadable"]["readability"] == "UNREADABLE_NEWER"
    # replay surfaces it: unreadable_newer is set → resume must refuse (§6).
    state = il.replay(entries)
    assert state.unreadable_newer is True


def test_run_dir_and_ledger_path_resolve_under_runs(tmp_path: Path):
    cfg = _config.default_config(tmp_path)
    p = il.ledger_path_for("RID-XYZ", cfg=cfg)
    # Under the generic .dos/ layout the run-dir is .dos/runs/<run_id>/.
    assert p.name == "intent.jsonl"
    assert p.parent.name == "RID-XYZ"
    # Resolving the path NEVER creates it (read-only discipline).
    assert not p.exists()


# ==========================================================================
# replay — the fold into a LedgerState.
# ==========================================================================


def test_replay_reconstructs_intent_and_separates_claimed_from_verified():
    entries = [
        il.intent_entry(goal="five-edit change", plan="P", phase="phi",
                        start_sha="START", declared_steps=["s1", "s2", "s3"]),
        il.step_claimed_entry("s1", "c1"),
        il.step_verified_entry("s1", "c1", via="file-path"),
        il.step_claimed_entry("s2", "c2"),  # claimed, NOT verified
    ]
    state = il.replay(entries)
    assert state.goal == "five-edit change"
    assert state.plan == "P" and state.phase == "phi"
    assert state.start_sha == "START"
    assert state.declared_steps == ("s1", "s2", "s3")
    assert state.claimed == {"s1": "c1", "s2": "c2"}
    assert set(state.verified) == {"s1"}
    assert state.verified["s1"] == VerifiedStep("s1", "c1", via="file-path")
    assert state.has_intent


def test_replay_empty_is_the_unresumable_floor():
    state = il.replay([])
    assert not state.has_intent
    assert state.declared_steps == ()


def test_replay_a_fresh_intent_reopens_a_suspended_run():
    entries = [
        il.intent_entry(goal="g", declared_steps=["s1"]),
        il.suspend_entry(reason="operator pause", resume_sha="SUS"),
    ]
    assert il.replay(entries).suspended is True
    entries.append(il.intent_entry(goal="g2", declared_steps=["s1", "s2"]))
    reopened = il.replay(entries)
    assert reopened.suspended is False
    assert reopened.goal == "g2"


def test_replay_records_resume_proposed_for_idempotence():
    entries = [
        il.intent_entry(goal="g", declared_steps=["s1"]),
        il.resume_proposed_entry(predecessor_run_id="RID-DEAD", resume_sha="X"),
    ]
    assert il.replay(entries).resume_proposed == ("RID-DEAD",)


def test_replay_records_resume_diverged_for_idempotence():
    entries = [
        il.intent_entry(goal="g", declared_steps=["s1"]),
        il.resume_diverged_entry(
            predecessor_run_id="RID-DEAD", resume_sha="X", residual=["s1"],
            reason="fresh lane work exists",
        ),
    ]
    assert il.replay(entries).resume_diverged == ("RID-DEAD",)


# ==========================================================================
# resume_plan — the verdict over frozen LedgerState + AncestryFacts.
# ==========================================================================


def _state(**kw) -> LedgerState:
    base = dict(run_id="RID-R", goal="g", plan="P", phase="phi", start_sha="START",
                declared_steps=("s1", "s2", "s3"))
    base.update(kw)
    return LedgerState(**base)


# Realistic ≥7-char short SHAs so AncestryFacts.contains' prefix guard is exercised.
_C1, _C2, _C3 = "c1aaaaa", "c2bbbbb", "c3ccccc"


def _anc(*, in_ancestry=(), verified_steps=(), diverged=False) -> AncestryFacts:
    """Build the boundary's RE-ADJUDICATED facts a pure resume_plan test needs.

    `verified_steps` is what the BOUNDARY (`gather_ancestry` →
    `adjudicate_verified_steps`) re-confirmed at read time on the non-forgeable rung —
    the authority, NOT the agent-written STEP_VERIFIED record. A pure test supplies it
    explicitly (the boundary's git re-check is exercised in test_resume_evidence).
    """
    return AncestryFacts(
        shas_in_ancestry=frozenset(in_ancestry),
        steps_verified_at_read=frozenset(verified_steps),
        lane_advanced_past_resume=diverged,
    )


def test_resumable_with_contiguous_verified_prefix():
    state = _state(
        claimed={"s1": _C1, "s2": _C2},
        verified={"s1": VerifiedStep("s1", _C1, via="file-path")},
    )
    # The boundary re-adjudicated ONLY s1 (s2 was claimed but never landed).
    anc = _anc(in_ancestry={_C1}, verified_steps={"s1"})
    plan = rz.resume_plan(state, anc)
    assert plan.verdict is Resume.RESUMABLE
    assert plan.resume_sha == _C1             # the last contiguous-verified SHA
    assert plan.residual == ("s2", "s3")      # s2 was CLAIMED but not verified → redo
    assert plan.verified == ("s1",)


def test_forged_step_verified_does_not_count_the_core_section5_fix():
    # The CRITICAL adversarial-review finding, pinned: the run wrote its OWN
    # STEP_VERIFIED {via: file-path} for s1 pointing at a real ancestry commit it
    # never actually did the work in. The boundary re-adjudication did NOT confirm s1
    # (the footprint re-check failed), so steps_verified_at_read is empty → s1 is NOT
    # done, even though the record is present and its SHA IS in ancestry. The kernel
    # does not believe the agent's STEP_VERIFIED.
    state = _state(
        claimed={"s1": _C1},
        verified={"s1": VerifiedStep("s1", _C1, via="file-path")},  # forged authority
    )
    anc = _anc(in_ancestry={_C1}, verified_steps=set())   # boundary re-check: NOT confirmed
    plan = rz.resume_plan(state, anc)
    assert plan.verdict is Resume.RESUMABLE
    assert plan.verified == ()                 # the forged record is NOT trusted
    assert "s1" in plan.residual               # so s1 must be redone — no work skipped


def test_claimed_but_not_in_ancestry_stays_in_residual_failclosed():
    # s1 has a STEP_VERIFIED record, but its SHA is NOT in ancestry (the commit was
    # never really landed / was rewritten out). Fail-closed: s1 is NOT done. The
    # boundary re-adjudication would also reject it (not in ancestry), so verified_steps
    # is empty.
    state = _state(
        claimed={"s1": _C1},
        verified={"s1": VerifiedStep("s1", _C1, via="file-path")},
    )
    anc = _anc(in_ancestry=set(), verified_steps=set())  # nothing in ancestry
    plan = rz.resume_plan(state, anc)
    assert plan.verdict is Resume.RESUMABLE
    assert plan.verified == ()                 # nothing safely verified
    assert plan.resume_sha == ""               # start SHA not in ancestry → re-derive from HEAD
    assert plan.residual == ("s1", "s2", "s3")


def test_forgeable_rung_is_not_a_safe_resume_anchor():
    # s1 is "verified" but only via the FORGEABLE subject-grep rung. Even if the
    # boundary somehow re-confirmed the step id, the policy belt-to-suspenders rejects
    # a forgeable `via` (§5 req 2) — it is NOT a safe anchor.
    state = _state(
        claimed={"s1": _C1},
        verified={"s1": VerifiedStep("s1", _C1, via="grep")},
    )
    anc = _anc(in_ancestry={_C1}, verified_steps={"s1"})
    plan = rz.resume_plan(state, anc)
    assert plan.verdict is Resume.RESUMABLE
    assert plan.verified == ()                 # forgeable via doesn't count
    assert "s1" in plan.residual               # so s1 must be redone
    # With the guard OFF, the boundary-confirmed step DOES count (the policy knob).
    loose = rz.resume_plan(state, anc, ResumePolicy(require_nonforgeable_rung=False))
    assert loose.verified == ("s1",)


def test_complete_when_every_step_verified():
    state = _state(
        verified={
            "s1": VerifiedStep("s1", _C1, via="file-path"),
            "s2": VerifiedStep("s2", _C2, via="registry"),
            "s3": VerifiedStep("s3", _C3, via="file-path"),
        },
    )
    anc = _anc(in_ancestry={_C1, _C2, _C3}, verified_steps={"s1", "s2", "s3"})
    plan = rz.resume_plan(state, anc)
    assert plan.verdict is Resume.COMPLETE
    assert plan.residual == ()
    assert plan.resume_sha == _C3


def test_complete_wins_over_diverged_when_residual_empty():
    # The adversarial-review HIGH finding: a fully-finished run with lane movement
    # past it is COMPLETE (done), NOT DIVERGED (there's no stale residual to graft).
    state = _state(
        verified={
            "s1": VerifiedStep("s1", _C1, via="file-path"),
            "s2": VerifiedStep("s2", _C2, via="file-path"),
            "s3": VerifiedStep("s3", _C3, via="file-path"),
        },
    )
    anc = _anc(in_ancestry={_C1, _C2, _C3},
               verified_steps={"s1", "s2", "s3"}, diverged=True)
    plan = rz.resume_plan(state, anc)
    assert plan.verdict is Resume.COMPLETE     # COMPLETE short-circuits before DIVERGED


def test_diverged_when_ground_truth_moved_past_the_resume_point():
    state = _state(
        verified={"s1": VerifiedStep("s1", _C1, via="file-path")},
    )
    anc = _anc(in_ancestry={_C1}, verified_steps={"s1"},
               diverged=True)  # a successor/human committed on the lane
    plan = rz.resume_plan(state, anc)
    assert plan.verdict is Resume.DIVERGED
    assert plan.residual == ("s2", "s3")       # surfaced, but refused — not grafted


def test_unresumable_with_no_intent():
    state = LedgerState(run_id="RID-R")  # no goal, no plan, no steps
    plan = rz.resume_plan(state, AncestryFacts())
    assert plan.verdict is Resume.UNRESUMABLE


def test_unresumable_when_schema_too_new():
    state = _state(unreadable_newer=True)
    plan = rz.resume_plan(state, AncestryFacts())
    assert plan.verdict is Resume.UNRESUMABLE
    assert "too OLD to read" in plan.reason or "schema newer" in plan.reason


def test_unresumable_when_corrupt_and_policy_strict():
    state = _state(corrupt_lines=2)
    # Default policy folds permissively → still RESUMABLE-shaped.
    assert rz.resume_plan(state, AncestryFacts()).verdict is not Resume.UNRESUMABLE
    # Strict policy treats any corrupt line as an unsound fold → UNRESUMABLE.
    strict = ResumePolicy(treat_untagged_as_corrupt=True)
    assert rz.resume_plan(state, AncestryFacts(), strict).verdict is Resume.UNRESUMABLE


def test_resumable_freeform_goal_with_no_declared_steps():
    # start_sha IS in ancestry → it is a real anchor.
    state = LedgerState(run_id="RID-R", goal="refactor the auth module",
                        start_sha=_C1)
    plan = rz.resume_plan(state, _anc(in_ancestry={_C1}))
    assert plan.verdict is Resume.RESUMABLE
    assert plan.resume_sha == _C1
    assert plan.residual == ("refactor the auth module",)


def test_freeform_start_sha_not_in_ancestry_is_not_echoed():
    # The start_sha-gating fix: an unverified self-reported start SHA is NOT echoed as
    # the re-entry anchor — it drops to "" (re-derive from HEAD).
    state = LedgerState(run_id="RID-R", goal="g", start_sha="deadbeefNOTREAL")
    plan = rz.resume_plan(state, _anc(in_ancestry=set()))
    assert plan.verdict is Resume.RESUMABLE
    assert plan.resume_sha == ""               # not echoed — start SHA not in ancestry


def test_freeform_goal_diverged_is_refused_too():
    # The adversarial-review HIGH finding: a free-form resume must honor DIVERGED.
    state = LedgerState(run_id="RID-R", goal="refactor auth", start_sha=_C1)
    plan = rz.resume_plan(state, _anc(in_ancestry={_C1}, diverged=True))
    assert plan.verdict is Resume.DIVERGED


def test_noncontiguous_verified_excludes_nothing_downstream_of_resume_sha():
    # s1 NOT verified, s2 verified: the hole at s1 means only the contiguous leading
    # run (empty) anchors the resume point — restart from start, and EVERY step
    # at/after the hole (s1, s2, s3) is in the residual. s2 must NOT be silently
    # dropped while resume_sha sits before its commit (the coverage-invariant bug).
    state = _state(
        verified={"s2": VerifiedStep("s2", _C2, via="file-path")},
    )
    anc = _anc(in_ancestry={_C2}, verified_steps={"s2"})
    plan = rz.resume_plan(state, anc)
    assert plan.verdict is Resume.RESUMABLE
    assert plan.verified == ()                 # s1 hole breaks contiguity
    assert plan.resume_sha == ""               # no contiguous prefix; START not in ancestry
    assert plan.residual == ("s1", "s2", "s3") # s2 redone too — coverage invariant holds
    # verified ∪ residual == declared (no step in neither).
    assert set(plan.verified) | set(plan.residual) == {"s1", "s2", "s3"}


def test_ancestry_contains_matches_short_and_full_sha():
    anc = AncestryFacts(shas_in_ancestry=frozenset({"abc1234"}))
    assert anc.contains("abc1234567890")       # full matches the ≥7-char short prefix
    assert anc.contains("abc1234")             # exact
    assert not anc.contains("def5678")
    assert not anc.contains("")


def test_ancestry_contains_rejects_short_spurious_prefix():
    # The prefix-collision guard: a 2-char token must not match an unrelated 40-char
    # ancestry sha just because it is a prefix.
    anc = AncestryFacts(shas_in_ancestry=frozenset({"ab" + "0" * 38}))
    assert not anc.contains("ab")              # too short to be an unambiguous prefix
    assert anc.contains("ab" + "0" * 38)       # exact still matches


def test_resume_plan_to_dict_is_json_round_trippable():
    state = _state(verified={"s1": VerifiedStep("s1", "c1", via="file-path")})
    anc = AncestryFacts(shas_in_ancestry=frozenset({"c1"}))
    d = rz.resume_plan(state, anc).to_dict()
    assert json.loads(json.dumps(d)) == d
    assert d["verdict"] == "RESUMABLE"
