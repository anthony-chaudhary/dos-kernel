"""docs/342 §4 P-EXACTLY-ONCE — the re-drive contract over a `ResumePlan`.

The honest floor of docs/342 Milestone 4: DOS guarantees exactly-once for
**git-resident effects only**; a non-git side effect is outside the reliability
envelope and the host owns idempotency. This file proves the verdict-side half of
that declaration — `resume.redrive_contract` — which turns the module docstring's
"the uncommitted tail is idempotent by construction" claim into a CHECKABLE bound:

  * the residual `resume_plan` proposes re-drives ONLY the uncommitted tail (no
    verified/committed step), so a re-drive never re-commits a git effect; and
  * a plan that WOULD re-drive a committed step is an `ENVELOPE_BREACH`, refused —
    the fail-closed assertion that catches a hand-built/forged plan.

The pure `resume_plan` verdict itself is exercised in `test_intent_ledger.py`; the
boundary's git re-adjudication is exercised in `test_resume_evidence.py`. This file
adjudicates the contract on top of both, with the same frozen-fixture discipline
(no live crashed run needed).
"""

from __future__ import annotations

import pytest

from dos import resume as rz
from dos.intent_ledger import LedgerState, VerifiedStep
from dos.resume import (
    AncestryFacts,
    RedriveContract,
    RedriveVerdict,
    Resume,
    ResumePolicy,
    redrive_contract,
    resume_plan,
)


# Mirror test_intent_ledger.py's fixtures so the two files agree on shape.
def _state(**kw) -> LedgerState:
    base = dict(run_id="RID-R", goal="g", plan="P", phase="phi", start_sha="START",
                declared_steps=("s1", "s2", "s3"))
    base.update(kw)
    return LedgerState(**base)


# Realistic ≥7-char short SHAs so AncestryFacts.contains' prefix guard is exercised.
_C1, _C2, _C3 = "c1aaaaa", "c2bbbbb", "c3ccccc"


def _anc(*, in_ancestry=(), verified_steps=(), diverged=False) -> AncestryFacts:
    return AncestryFacts(
        shas_in_ancestry=frozenset(in_ancestry),
        steps_verified_at_read=frozenset(verified_steps),
        lane_advanced_past_resume=diverged,
    )


# ==========================================================================
# The git-idempotent bound — a normal RESUMABLE residual is safe to re-drive.
# ==========================================================================


def test_redrive_of_a_resumable_residual_is_git_idempotent():
    # s1 committed + re-adjudicated; s2 claimed-but-unverified; s3 untouched.
    state = _state(
        claimed={"s1": _C1, "s2": _C2},
        verified={"s1": VerifiedStep("s1", _C1, via="file-path")},
    )
    anc = _anc(in_ancestry={_C1}, verified_steps={"s1"})
    plan = resume_plan(state, anc)
    assert plan.verdict is Resume.RESUMABLE
    assert plan.residual == ("s2", "s3")   # the uncommitted tail
    assert plan.verified == ("s1",)        # the committed prefix is NOT re-driven

    contract = redrive_contract(plan, state, anc)
    assert isinstance(contract, RedriveVerdict)
    assert contract.contract is RedriveContract.GIT_IDEMPOTENT
    assert contract.is_git_idempotent
    assert contract.offending_steps == ()
    # The committed step never appears in what would be re-driven (the bound).
    assert "s1" not in plan.residual


def test_the_bound_residual_is_disjoint_from_the_committed_prefix():
    # State the bound as an invariant the contract proves: residual ∩ verified == ∅.
    state = _state(
        claimed={"s1": _C1, "s2": _C2},
        verified={
            "s1": VerifiedStep("s1", _C1, via="file-path"),
            "s2": VerifiedStep("s2", _C2, via="registry"),
        },
    )
    anc = _anc(in_ancestry={_C1, _C2}, verified_steps={"s1", "s2"})
    plan = resume_plan(state, anc)
    assert plan.verdict is Resume.RESUMABLE
    assert plan.verified == ("s1", "s2")
    assert plan.residual == ("s3",)
    # No committed step is re-driven — the exactly-once-for-git bound, set-wise.
    assert set(plan.residual).isdisjoint(set(plan.verified))
    assert redrive_contract(plan, state, anc).is_git_idempotent


def test_complete_plan_has_an_empty_residual_so_re_drive_is_vacuously_safe():
    state = _state(
        verified={
            "s1": VerifiedStep("s1", _C1, via="file-path"),
            "s2": VerifiedStep("s2", _C2, via="registry"),
            "s3": VerifiedStep("s3", _C3, via="file-path"),
        },
    )
    anc = _anc(in_ancestry={_C1, _C2, _C3}, verified_steps={"s1", "s2", "s3"})
    plan = resume_plan(state, anc)
    assert plan.verdict is Resume.COMPLETE
    assert plan.residual == ()
    c = redrive_contract(plan, state, anc)
    assert c.is_git_idempotent
    assert c.offending_steps == ()


def test_free_form_goal_residual_is_git_idempotent():
    # A run with a free-form goal and no enumerated steps: the residual is the whole
    # goal re-entered from the start SHA — no committed step, so git-idempotent.
    state = LedgerState(run_id="RID-F", goal="ship the thing", start_sha=_C1)
    anc = _anc(in_ancestry={_C1})
    plan = resume_plan(state, anc)
    assert plan.verdict is Resume.RESUMABLE
    assert plan.residual == ("ship the thing",)
    assert redrive_contract(plan, state, anc).is_git_idempotent


# ==========================================================================
# ENVELOPE_BREACH — a re-drive from an UN-committed anchor is refused.
# ==========================================================================


def test_a_resume_anchor_not_in_ancestry_is_an_envelope_breach():
    # A HAND-BUILT (malformed) plan whose resume_sha is a non-empty SHA that is NOT a
    # committed fossil. resume_plan never emits this (it gates the anchor to
    # in-ancestry-or-empty), but the contract is the PROOF — it must catch it,
    # fail-closed: re-driving "from" a self-reported, uncommitted point is the
    # docs/103 disease and outside the exactly-once envelope.
    state = _state()
    anc = _anc(in_ancestry={_C1})           # only _C1 is a real fossil
    bad_plan = rz.ResumePlan(
        verdict=Resume.RESUMABLE,
        reason="(constructed) anchor is a self-reported SHA never committed",
        run_id="RID-R",
        resume_sha="deadbee",               # NOT in ancestry
        residual=("s2", "s3"),
        verified=(),
    )
    c = redrive_contract(bad_plan, state, anc)
    assert c.contract is RedriveContract.ENVELOPE_BREACH
    assert not c.is_git_idempotent
    assert c.offending_steps == ("deadbee",)
    assert "ancestry" in c.reason
    assert "docs/342" in c.reason


def test_empty_anchor_is_safe_re_derive_from_head():
    # An empty resume_sha tells the driver to re-derive from HEAD — the honest
    # fallback when no committed prefix exists. That is git-idempotent, never a breach.
    state = _state()
    anc = _anc(in_ancestry={_C1})
    plan = rz.ResumePlan(
        verdict=Resume.RESUMABLE, reason="no verified prefix", run_id="RID-R",
        resume_sha="", residual=("s1", "s2", "s3"), verified=(),
    )
    c = redrive_contract(plan, state, anc)
    assert c.is_git_idempotent
    assert c.offending_steps == ()
    assert "HEAD" in c.reason


def test_downstream_of_a_hole_re_drive_is_idempotent_not_a_breach():
    # The subtle case the anchor framing gets right: s2 is committed but sits
    # downstream of a HOLE (s1 not verified), so resume_plan correctly restarts from
    # the start anchor and puts s2 BACK in the residual. Re-driving s2 re-applies a
    # commit already in ancestry — git-idempotent (content-addressed), NOT a breach.
    state = _state(
        claimed={"s2": _C2},
        verified={"s2": VerifiedStep("s2", _C2, via="file-path")},
    )
    # s2 is re-adjudicated committed, but s1 (the prefix head) is NOT → hole.
    anc = _anc(in_ancestry={_C2}, verified_steps={"s2"})
    plan = resume_plan(state, anc)
    assert plan.verdict is Resume.RESUMABLE
    assert plan.verified == ()                       # no contiguous prefix (the hole)
    assert plan.residual == ("s1", "s2", "s3")       # s2 is re-driven despite its commit
    assert plan.resume_sha == ""                     # start not in ancestry → re-derive
    c = redrive_contract(plan, state, anc)
    assert c.is_git_idempotent                       # re-applying s2's commit is a no-op
    assert c.offending_steps == ()


# ==========================================================================
# Distrust: the anchor check uses re-adjudicated ancestry, not a stored claim.
# ==========================================================================


def test_anchor_check_uses_re_adjudicated_ancestry_not_a_self_report():
    # The anchor's membership is tested against AncestryFacts.contains — the boundary's
    # re-read of git — never a stored STEP_VERIFIED record. A plan that NAMES a SHA the
    # ledger "verified" but which the boundary did NOT find in ancestry is a breach: the
    # kernel does not believe the agent's claim that its anchor was committed.
    state = _state(
        verified={"s1": VerifiedStep("s1", _C1, via="file-path")},  # ledger claims _C1 done
    )
    anc = _anc(in_ancestry=set(), verified_steps=set())  # boundary: _C1 NOT in ancestry
    plan = rz.ResumePlan(
        verdict=Resume.RESUMABLE, reason="constructed", run_id="RID-R",
        resume_sha=_C1, residual=("s2", "s3"), verified=("s1",),
    )
    c = redrive_contract(plan, state, anc)
    assert c.contract is RedriveContract.ENVELOPE_BREACH  # claimed-committed ≠ committed
    assert c.offending_steps == (_C1,)


def test_policy_is_accepted_for_signature_symmetry_and_does_not_change_the_anchor():
    # The anchor check needs no policy knob; `policy` is accepted only for signature
    # symmetry with resume_plan (a future seam). A custom policy must not flip the
    # verdict — the anchor is in ancestry either way.
    state = _state(claimed={"s1": _C1}, verified={"s1": VerifiedStep("s1", _C1, via="file-path")})
    anc = _anc(in_ancestry={_C1}, verified_steps={"s1"})
    plan = resume_plan(state, anc)
    default_v = redrive_contract(plan, state, anc)
    loose_v = redrive_contract(plan, state, anc, ResumePolicy(require_nonforgeable_rung=False))
    assert default_v.contract is loose_v.contract is RedriveContract.GIT_IDEMPOTENT


# ==========================================================================
# Honesty: the verdict speaks only to the git effect, never the non-git one.
# ==========================================================================


def test_git_idempotent_still_carries_the_non_git_caveat():
    # The whole value of Phase A is honesty about the boundary: a GIT_IDEMPOTENT
    # verdict does NOT claim exactly-once for a non-git side effect a step fired
    # before committing — that is outside the envelope and the host's to dedup.
    state = _state(claimed={"s1": _C1}, verified={"s1": VerifiedStep("s1", _C1, via="file-path")})
    anc = _anc(in_ancestry={_C1}, verified_steps={"s1"})
    plan = resume_plan(state, anc)
    c = redrive_contract(plan, state, anc)
    assert c.is_git_idempotent
    assert c.non_git_caveat is True           # always declared, never dropped
    d = c.to_dict()
    assert d["contract"] == "GIT_IDEMPOTENT"
    assert d["non_git_caveat"] is True
    assert d["offending_steps"] == []


# ==========================================================================
# The bound as an invariant: resume_plan NEVER emits a residual that breaches.
# ==========================================================================


@pytest.mark.parametrize("verified_steps, in_anc, declared", [
    ((), (), ("s1", "s2", "s3")),                       # nothing verified
    (("s1",), (_C1,), ("s1", "s2", "s3")),              # contiguous prefix of 1
    (("s1", "s2"), (_C1, _C2), ("s1", "s2", "s3")),     # prefix of 2
    (("s2",), (_C2,), ("s1", "s2", "s3")),              # a HOLE (s2 verified, s1 not)
    (("s1", "s2", "s3"), (_C1, _C2, _C3), ("s1", "s2", "s3")),  # all verified (COMPLETE)
])
def test_resume_plan_output_always_satisfies_the_contract(verified_steps, in_anc, declared):
    # The load-bearing invariant: for ANY shape resume_plan emits, the residual it
    # proposes is git-idempotent to re-drive. The contract should never refuse a plan
    # the kernel itself produced — only a forged/hand-built one. This is the bound the
    # docs/342 §4 honest floor commits to, proven across the resume verdict's space.
    verified = {}
    for sid, sha in zip(("s1", "s2", "s3"), (_C1, _C2, _C3)):
        if sid in verified_steps:
            verified[sid] = VerifiedStep(sid, sha, via="file-path")
    state = _state(declared_steps=declared, claimed={s: c for s, c in
                   zip(declared, (_C1, _C2, _C3))}, verified=verified)
    anc = _anc(in_ancestry=in_anc, verified_steps=verified_steps)
    plan = resume_plan(state, anc)
    c = redrive_contract(plan, state, anc)
    assert c.is_git_idempotent, (
        f"resume_plan emitted a residual {plan.residual} that breaches the git "
        f"envelope on {c.offending_steps} (verdict={plan.verdict})"
    )
    # And the set-wise statement of the same bound.
    assert set(plan.residual).isdisjoint(set(plan.verified))
