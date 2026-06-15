"""resume — the third ARIES phase: replay-to-the-last-verified-point, then PROPOSE re-dispatch (docs/107 §2,§3.3,§5).

> **A crashed or paused run is a stale self-report about unfinished work, so
> resuming it is the distrust primitive pointed at the run's own intent — record
> what it *meant* to do (distrusted), re-verify how far the *fossils* say it got,
> fold the difference into a residual and a non-forgeable re-entry SHA, and
> *propose* — never perform — the continuation.**

`lane_journal.replay` is the ARIES **redo** fold; `94 §3.2` named that DOS does not
own **undo**. Resume is the *third* phase the WAL framing implies: **analysis →
redo → CONTINUE**. This module is the pure verdict over a reconstructed intent —
`liveness.classify`'s sibling, field-for-field:

    arbiter.arbitrate    (request, live_leases, config)        -> decision
    liveness.classify    (ProgressEvidence, policy)             -> LivenessVerdict
    resume.resume_plan   (LedgerState, AncestryFacts, policy)   -> ResumePlan
                         ^ THIS module

All I/O — reading the ledger, asking git which claimed SHAs are in ancestry,
re-verifying a step on the non-forgeable rung — happens in the CALLER (the `dos
resume` CLI's evidence-gather), exactly as `liveness`'s git/journal reads happen
outside `classify`. `resume_plan` makes no subprocess, file, or clock call: the
ancestry membership is a field on `AncestryFacts`, never re-derived inside the
verdict. That is what lets the whole recovery LOGIC be replay-tested on frozen
ledger + frozen ancestry fixtures — no live multi-minute crashed run needed
(`docs/107 §3.3`, the `loop_decide`/`journal_delta` design value, restated for the
resume axis).

The belief/effect line (`docs/107 §2`, the `94 §2` checkpoint/restore split):

  * **A resume point is a BELIEF the kernel may MINT** — "run R got verifiably as
    far as SHA `abc123` (steps 1–2 of intent I); the residual is steps 3–5." An
    epistemic claim over unforgeable git artifacts. The kernel is allowed to
    produce it; `resume_plan` is where it does.
  * **A resume is an EFFECT the kernel may only PROPOSE.** Re-spawning, re-acquiring
    the lane, handing the worker the residual — those mutate the world. They live
    behind a human (`dos resume --plan` prints the residual + re-entry SHA and
    exits; a `decisions` emit-and-exit row prints the re-dispatch command) or a
    driver. **The kernel never re-spawns and never re-runs the work** (the §8
    non-goal, the `99` advisory-only floor on the resume axis).

The safety property (`docs/107 §5`, the load-bearing "*safely*"): the resume point
stands on the most-accountable fossil (git ancestry), NEVER on the ledger's
self-reported "I finished step 3." You resume *from the last committed, verified
SHA*, never the last *claimed* step — so re-execution re-does at most the
uncommitted tail, which is idempotent by construction (it produced no durable
effect, or it would be a commit). The dead run's `STEP_CLAIMED` records are treated
exactly as `103` treats a recalled memory: a prior commitment, re-verified against
ground truth at read time, never replayed as present fact.

The reliability envelope (`docs/342 §4` P-EXACTLY-ONCE, the honest floor)
=========================================================================
The "idempotent by construction" claim above is true for exactly one class of
effect, and `docs/342 §4` makes the boundary a DECLARED fact rather than an
unstated assumption — drawing the line tight is itself the equal-caliber move
(TCP's caliber includes *knowing exactly what it does and does not guarantee*):

  * **Inside the envelope — git-resident effects.** A residual step's durable
    effect is a git commit, which is atomic. Re-driving it re-does at most the
    *uncommitted* tail: a step with a real durable git effect is in `verified`
    (its SHA is in ancestry), and `resume_plan` keeps the verified prefix OUT of
    the residual. So a re-drive never re-commits a committed step — exactly-once
    holds for git effects, the same way TCP guarantees the byte stream.

  * **Outside the envelope — non-git side effects.** The moment a step fires a
    non-git effect *before* it commits (an external POST, a charged card, a sent
    email, a pushed artifact), re-driving the residual is **at-least-once
    execution** of that effect, and DOS has no Undo (`docs/114`: the ARIES third
    phase was dropped; the lineage is forward recovery over the event-sourced
    intent ledger, not backward recovery). DOS does NOT and cannot guarantee
    exactly-once for an effect it never witnessed. The host owns idempotency
    there — an idempotency key on the POST, or accepting at-least-once — exactly
    as the application owns end-to-end exactly-once above TCP (the end-to-end
    argument, `335 §4`).

`redrive_contract` below turns this boundary from a docstring claim into a
CHECKABLE verdict: given a `ResumePlan`, it ASSERTS the re-drive starts from a
COMMITTED anchor — `resume_sha` is a git fossil (in ancestry) or empty (re-derive
from HEAD), never an unverified self-reported SHA. Re-driving the residual forward
of a committed anchor is git-idempotent because git is content-addressed (re-doing
work already in history is a no-op / identical commit, never a doubled durable
effect). It is the verdict-side belt to the docstring's "idempotent by
construction": a plan whose anchor is NOT a committed fossil is an
`ENVELOPE_BREACH`, refused, not silently re-driven from a self-report. The kernel
still PROPOSES the residual; the contract only proves the re-drive it proposes
restarts from ground truth, never doubling a git effect. Effects
outside the git envelope are the host adapter's to dedup (`docs/342 §4` option 2,
an effect-ledger with idempotency keys, is the stretch fix layered on top).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from dos.intent_ledger import LedgerState


class Resume(str, enum.Enum):
    """The typed resume verdict — four states, mutually exclusive (§3.3).

    `str`-valued so it round-trips a `--json` token / exit-code map without a
    lookup table (the `Liveness` / `gate_classify.Verdict` idiom).
    """

    RESUMABLE = "RESUMABLE"        # clean resume-point SHA + non-empty residual: continue from here
    COMPLETE = "COMPLETE"          # residual empty — every declared step verified; nothing to resume
    DIVERGED = "DIVERGED"          # ground truth moved past the resume point — REFUSE, raise a decision
    UNRESUMABLE = "UNRESUMABLE"    # no INTENT / corrupt-past-fold / too-new schema: don't guess a residual

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class ResumePolicy:
    """The knobs that shape the resume verdict — policy, not mechanism.

    The `LivenessPolicy` split: mechanism is the kernel, thresholds are config.
    Defaults are GENERIC (no host tuning); a workspace could declare its own in
    `dos.toml [resume]` (a future seam, like `[liveness]`).

      require_nonforgeable_rung — when True (the §5 req-2 default), a step's
        `STEP_VERIFIED` must have been minted on a NON-forgeable rung (`via` is
        `file-path`/`registry`, never the forgeable subject-grep). A step whose
        verified `via` is forgeable/empty is NOT counted toward the resume point
        (it stays in the residual): a resume point built on a forgeable verdict is
        worse than none — it would skip work that never really happened. The mint
        (`intent_ledger.step_verified_entry`) is supposed to refuse a forgeable
        rung already; this is the verdict-side belt to that suspenders.
      treat_untagged_as_corrupt — when True, an UNTAGGED/legacy ledger record makes
        the fold unsound (UNRESUMABLE). Default False: a legacy record is folded
        permissively (the tolerant side of the `durable_schema.UNTAGGED` contract);
        only an UNREADABLE_NEWER (too-new schema) forces UNRESUMABLE regardless.
    """

    require_nonforgeable_rung: bool = True
    treat_untagged_as_corrupt: bool = False


DEFAULT_POLICY = ResumePolicy()

# The verify rungs a step may stand on (§5 req 2). The forgeable subject-grep rung
# (`grep`/`subject`/`body-mention`/`direct`/… — anything that matches a phase token
# in commit PROSE) is DEFEATED by `git commit --allow-empty` (an empty commit whose
# subject names the step), so it is NOT a safe resume anchor. The non-forgeable
# rungs prove an ARTEFACT moved: the registry row (a ship-time stamp) or the
# file-path / diff-content overlap. These are the only `via` values
# `resume_evidence.verify_step` can MINT (it always writes `file-path`); the set is
# an ALLOW-LIST (fail-closed: an unknown `via` is treated as forgeable).
#
# IMPORTANT (the docs/107 §5 / docs/103 fix): `via` is an UNAUTHENTICATED HINT, not
# authority. The intent ledger is written BY the distrusted run, so a malicious run
# can append its own `STEP_VERIFIED {via: "file-path"}` for a step it never did.
# The kernel therefore RE-ADJUDICATES the non-forgeable rung at READ time
# (`resume_evidence.adjudicate_verified_steps` re-runs the footprint check on the
# claimed SHA) and the pure verdict consumes that re-adjudicated set
# (`AncestryFacts.steps_verified_at_read`), NEVER the stored `via`. `via` survives
# only as a forensic label + the policy belt-to-suspenders in `_verified_on_safe_rung`.
NONFORGEABLE_RUNGS = frozenset({"file-path", "registry"})

# The minimum hex length a prefix match in `AncestryFacts.contains` will honour
# (below it, only an exact match counts) — git's own abbreviated-SHA floor, so a
# 1–2 char claimed token can never spuriously collide with an unrelated ancestry
# commit. 7 is git's conventional short-SHA length.
_MIN_PREFIX = 7


@dataclass(frozen=True)
class AncestryFacts:
    """The boundary's RE-ADJUDICATED evidence — gathered by the CALLER (§3.3, §5).

    Frozen and handed to the pure verdict (the `ProgressEvidence` shape). The CLI's
    evidence-gather (`resume_evidence.gather_ancestry`) decodes the ledger
    (`intent_ledger.replay`), asks git which SHAs are reachable from HEAD AND
    RE-RUNS the non-forgeable footprint check on each claimed step — never trusting
    the agent-written `STEP_VERIFIED` record's authority (it is written by the
    distrusted run; docs/107 §5, the docs/103 move).

      shas_in_ancestry  — the set of commit SHAs reachable from HEAD on the served
                          workspace. A claimed step whose SHA is NOT in here is a
                          step the agent claimed but never landed — fail-closed, the
                          resume must redo it.
      steps_verified_at_read — the set of STEP IDS the boundary RE-ADJUDICATED as
                          standing on the non-forgeable rung AT READ TIME (their
                          claimed/recorded SHA is in ancestry AND its commit footprint
                          is real, not `--allow-empty`). THIS is the authority the
                          pure verdict trusts for "done" — NOT the agent-written
                          `STEP_VERIFIED.via`. A forged `STEP_VERIFIED` pointing at an
                          unrelated real commit fails the footprint-region re-check and
                          is absent here, so it is redone. Empty ⇒ no step re-verified
                          (the safe floor when the boundary couldn't re-check).
      head_sha          — the workspace's current HEAD (DIVERGED framing / forensics).
      lane_advanced_past_resume — True iff ground truth advanced on the run's lane
                          PAST the would-be resume point in a way the residual can't
                          be cleanly grafted onto. The CALLER computes it; the verdict
                          consumes it. True ⇒ DIVERGED (§5 req 3).
    """

    shas_in_ancestry: frozenset[str] = frozenset()
    steps_verified_at_read: frozenset[str] = frozenset()
    head_sha: str = ""
    lane_advanced_past_resume: bool = False

    def contains(self, sha: str) -> bool:
        """True iff `sha` is in ancestry. Prefix-tolerant but collision-guarded.

        Git short SHAs and full SHAs both appear (the ledger stores whatever the
        agent claimed; `git_delta` returns short). Match by prefix in EITHER
        direction so a 7-char claimed sha matches a 40-char ancestry sha and vice
        versa — the same tolerant comparison `oracle` does. To foreclose a SPURIOUS
        prefix collision (a 1–2 char claimed token matching an unrelated ancestry
        sha), a prefix match requires the shorter side to be ≥ `_MIN_PREFIX` hex
        chars; below that only an EXACT match counts. An empty sha never matches.
        """
        s = (sha or "").strip().lower()
        if not s:
            return False
        for a in self.shas_in_ancestry:
            a = (a or "").strip().lower()
            if not a:
                continue
            if s == a:
                return True
            # A prefix match is only honoured when the SHORTER side is long enough
            # to be an unambiguous abbreviated git SHA — git itself rejects ambiguous
            # short SHAs, and a 1–2 char token must not match an unrelated commit.
            shorter = min(len(s), len(a))
            if shorter >= _MIN_PREFIX and (s.startswith(a) or a.startswith(s)):
                return True
        return False


@dataclass(frozen=True)
class ResumePlan:
    """The single verdict `resume_plan` returns, with the derivation echoed back.

    `verdict` is the typed `Resume`. `reason` is the operator-facing one-liner.
    `resume_sha` is the minted re-entry point (the newest commit backing a
    contiguous prefix of verified steps; "" when none / COMPLETE / UNRESUMABLE).
    `residual` is the ordered remaining step ids (declared-minus-verified, with a
    claimed-but-unverified step staying IN the residual — fail-closed). `verified`
    is the contiguous-verified prefix the resume point rests on. `predecessor_run_id`
    is the dead/parked run; `already_proposed` echoes the §5-req-4 idempotence flag.
    `to_dict` is the `--json` shape (the `LivenessVerdict.to_dict` idiom)."""

    verdict: Resume
    reason: str
    run_id: str
    resume_sha: str = ""
    residual: tuple[str, ...] = ()
    verified: tuple[str, ...] = ()
    predecessor_run_id: str = ""
    already_proposed: bool = False

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "run_id": self.run_id,
            "resume_sha": self.resume_sha,
            "residual": list(self.residual),
            "verified": list(self.verified),
            "predecessor_run_id": self.predecessor_run_id,
            "already_proposed": self.already_proposed,
        }


def _verified_on_safe_rung(state: LedgerState, step_id: str, ancestry: AncestryFacts,
                           policy: ResumePolicy) -> bool:
    """True iff `step_id` is a safe resume anchor — RE-ADJUDICATED at read, not trusted.

    The docs/107 §5 / docs/103 fix. The intent ledger is written BY the distrusted
    run, so a `STEP_VERIFIED` record's `via`/`sha` are an UNAUTHENTICATED HINT, never
    authority. A step counts as done ONLY when the BOUNDARY re-adjudicated it at read
    time — i.e. `step_id ∈ ancestry.steps_verified_at_read`, the set
    `resume_evidence.adjudicate_verified_steps` built by RE-RUNNING the non-forgeable
    footprint check (`step_stands_on_nonforgeable_rung`) on the claimed SHA. A forged
    `STEP_VERIFIED {via: "file-path"}` pointing at an unrelated real commit fails that
    re-check (its footprint isn't the step's work) and is absent from the set, so it
    is redone — the §5 break the adversarial review found, closed.

    The stored `via` survives only as the policy belt-to-suspenders: when
    `require_nonforgeable_rung` is True (the default) a record whose `via` is itself
    forgeable is rejected even if the boundary somehow re-adjudicated it, and the
    in-ancestry guard defends against a re-adjudicated SHA later rewritten out of
    history. The AUTHORITY is `steps_verified_at_read`; `via`/`contains` only narrow
    it further (fail-closed). When the boundary supplied NO re-adjudication (an empty
    `steps_verified_at_read` — a pure test or a boundary that couldn't re-check), the
    safe floor is "nothing verified," so a stored `STEP_VERIFIED` alone never counts.
    """
    if step_id not in ancestry.steps_verified_at_read:
        return False
    vs = state.verified.get(step_id)
    if vs is None or not vs.sha:
        return False
    if not ancestry.contains(vs.sha):
        return False
    if policy.require_nonforgeable_rung and vs.via not in NONFORGEABLE_RUNGS:
        return False
    return True


def resume_plan(
    state: LedgerState,
    ancestry: AncestryFacts,
    policy: ResumePolicy = DEFAULT_POLICY,
) -> ResumePlan:
    """Compute the resume verdict from a folded ledger + ancestry facts. PURE — no I/O.

    The fold (`docs/107 §3.3`):
      1. **UNRESUMABLE floor first.** No INTENT record (nothing declared → no
         residual to ground), OR an UNREADABLE_NEWER schema record (this kernel is
         too old to soundly read the ledger; refuse, don't guess — §6), OR a corrupt
         fold the policy treats as unsound: return UNRESUMABLE. *Don't guess a
         residual you can't ground* (the `94 §4.2` INSUFFICIENT_DATA twin).
      2. **Compute the verified set, fail-closed.** A declared step counts as done
         ONLY if `_verified_on_safe_rung` (a `STEP_VERIFIED` whose SHA is in
         ancestry, on a non-forgeable rung). A `STEP_CLAIMED` without such a
         verification — including one the agent claimed but never landed — is NOT
         done; it stays in the residual (the agent must redo it).
      3. **Residual + resume point.** residual = declared_steps minus the verified
         set (order preserved). The resume-point SHA is the SHA backing the LAST
         step of the *contiguous verified prefix* — the last point past which
         nothing is confirmed. (Contiguous: a hole — step 2 verified but step 1 not
         — means the resume must restart from before the hole, so only the unbroken
         leading run of verified steps anchors the point.)
      4. **DIVERGED if ground truth moved past it.** If `ancestry
         .lane_advanced_past_resume`, the lane advanced past the resume point in a
         way the residual can't cleanly graft onto → DIVERGED (refuse + raise a
         decision, never overwrite fresh work; §5 req 3).
      5. **COMPLETE if the residual is empty** — every declared step verified; the
         run finished, it just never wrote a clean terminal record.
      6. **RESUMABLE otherwise** — a clean resume point (or the start SHA when no
         step is verified yet) + a non-empty residual: continue from here.

    The verdict is advisory: it MINTS the resume point and computes the residual;
    the act of continuing is a driver's/human's (the §8 non-goal, the `99` floor).
    """
    rid = state.run_id

    # 1. The UNRESUMABLE floor.
    if state.unreadable_newer:
        return ResumePlan(
            verdict=Resume.UNRESUMABLE,
            reason=(
                "ledger contains a record this kernel is too OLD to read soundly "
                "(schema newer than understood) — refusing to guess a residual from "
                "a misread intent; run the explicit migration fold (§6)"
            ),
            run_id=rid,
        )
    if policy.treat_untagged_as_corrupt and state.corrupt_lines > 0:
        return ResumePlan(
            verdict=Resume.UNRESUMABLE,
            reason=(
                f"ledger has {state.corrupt_lines} corrupt/unreadable record(s) and "
                f"policy treats those as an unsound fold — refusing to guess a residual"
            ),
            run_id=rid,
        )
    if not state.has_intent:
        return ResumePlan(
            verdict=Resume.UNRESUMABLE,
            reason=(
                "no INTENT record in the ledger — the run declared no goal, so there "
                "is no residual to ground; nothing to resume (the honest floor)"
            ),
            run_id=rid,
        )

    declared = list(state.declared_steps)
    already = bool(state.resume_proposed)
    # The start SHA is the fallback anchor, but it comes from the agent's INTENT
    # record — a SELF-REPORT (docs/107 §3.2). Echoing it as the "non-forgeable
    # re-entry SHA" without checking ancestry would be the docs/103 disease (trusting
    # a self-report) inside the kernel built to refuse it. Gate it: only an
    # in-ancestry start SHA is a real anchor; otherwise drop to "" (force the driver
    # to re-derive from HEAD) — never echo an unverified self-reported SHA.
    safe_start = state.start_sha if ancestry.contains(state.start_sha) else ""

    # A run with a free-form goal and NO enumerated steps: re-enter from the
    # (ancestry-checked) start SHA with the whole goal as the residual. We cannot
    # compute a step-granular resume point, so the goal itself is the single residual
    # unit. DIVERGED still applies — a free-form resume must NOT overwrite fresh work
    # any more than an enumerated one (the §5 req-3 refusal has no free-form carve-out).
    if not declared:
        residual_goal = (state.goal or f"{state.plan} {state.phase}".strip() or "(declared goal)",)
        if ancestry.lane_advanced_past_resume:
            return ResumePlan(
                verdict=Resume.DIVERGED,
                reason=(
                    f"free-form goal but ground truth advanced past the resume point "
                    f"{safe_start[:12] or '(start)'} on this run's lane — refusing to "
                    f"re-do the whole goal over fresh work (§5 req 3 applies to "
                    f"free-form resume too)"
                ),
                run_id=rid, resume_sha=safe_start, residual=residual_goal,
                verified=(), already_proposed=already,
            )
        return ResumePlan(
            verdict=Resume.RESUMABLE,
            reason=(
                "no enumerated steps — re-enter from the run's start SHA with the "
                "whole declared goal as the residual (step-granular resume needs a "
                "declared step list)"
                + ("" if safe_start else "; start SHA not in ancestry, re-derive from HEAD")
            ),
            run_id=rid,
            resume_sha=safe_start,
            residual=residual_goal,
            verified=(),
            already_proposed=already,
        )

    # 2 + 3. The verified set (fail-closed, RE-ADJUDICATED) and the contiguous prefix.
    contiguous_prefix: list[str] = []
    broken = False
    for sid in declared:
        is_done = _verified_on_safe_rung(state, sid, ancestry, policy)
        if is_done and not broken:
            contiguous_prefix.append(sid)
        elif not is_done:
            broken = True  # first hole — nothing at/after it counts as done for resume

    # The residual = declared MINUS the CONTIGUOUS verified prefix (NOT minus the full
    # verified set). A step verified but DOWNSTREAM of a hole (s2 verified, s1 not)
    # must still be redone — the resume restarts from before the hole, so everything
    # at/after it is residual. Basing the residual on the contiguous prefix keeps the
    # coverage invariant `verified ∪ residual == declared` AND ensures no residual
    # step is excluded while the re-entry SHA sits before it (the disagreement the
    # adversarial review found). `verified` (reported) == the contiguous prefix.
    prefix_set = set(contiguous_prefix)
    residual = tuple(s for s in declared if s not in prefix_set)

    # The resume-point SHA = the SHA backing the LAST contiguous-verified step (the
    # last point past which nothing is confirmed). No verified prefix ⇒ the
    # ancestry-checked start SHA (re-enter from the start of the run's work).
    if contiguous_prefix:
        resume_sha = state.verified[contiguous_prefix[-1]].sha
    else:
        resume_sha = safe_start

    # 4. COMPLETE before DIVERGED — a fully-finished run (empty residual) is DONE, not
    #    diverged: there is no stale residual to graft, so lane movement past it is
    #    irrelevant. (Checking DIVERGED first mislabelled a finished run and defeated
    #    its GC — the adversarial-review high finding.)
    if not residual:
        return ResumePlan(
            verdict=Resume.COMPLETE,
            reason=(
                f"all {len(declared)} declared step(s) verified against ancestry — "
                f"nothing to resume; the run finished, it just never wrote a clean "
                f"terminal record"
            ),
            run_id=rid,
            resume_sha=resume_sha,
            residual=(),
            verified=tuple(contiguous_prefix),
            already_proposed=already,
        )

    # 5. DIVERGED — there IS a residual AND ground truth moved past the resume point.
    if ancestry.lane_advanced_past_resume:
        return ResumePlan(
            verdict=Resume.DIVERGED,
            reason=(
                f"ground truth advanced past the resume point {resume_sha[:12] or '(start)'} "
                f"on this run's lane — a successor or a human committed there; refusing "
                f"to graft {len(residual)} stale residual step(s) over fresh work "
                f"(merge-conflict-as-verdict, §5 req 3)"
            ),
            run_id=rid,
            resume_sha=resume_sha,
            residual=residual,
            verified=tuple(contiguous_prefix),
            already_proposed=already,
        )

    # 6. RESUMABLE — a clean resume point + a non-empty residual.
    n_claimed_unverified = sum(
        1 for s in residual if s in state.claimed and s not in prefix_set
    )
    claimed_note = (
        f" ({n_claimed_unverified} were CLAIMED but not re-verified against ancestry "
        f"— the resume must redo them)" if n_claimed_unverified else ""
    )
    return ResumePlan(
        verdict=Resume.RESUMABLE,
        reason=(
            f"re-verified {len(contiguous_prefix)}/{len(declared)} step(s) against "
            f"ancestry; resume from {resume_sha[:12] or '(start SHA)'} with "
            f"{len(residual)} residual step(s){claimed_note}"
        ),
        run_id=rid,
        resume_sha=resume_sha,
        residual=residual,
        verified=tuple(contiguous_prefix),
        already_proposed=already,
    )


# --------------------------------------------------------------------------
# The re-drive contract — the docs/342 §4 P-EXACTLY-ONCE envelope, made checkable.
# --------------------------------------------------------------------------


class RedriveContract(str, enum.Enum):
    """Is a `ResumePlan`'s residual SAFE to re-drive inside the git envelope? (docs/342 §4).

    The verdict-side belt to the module docstring's "idempotent by construction"
    claim. `resume_plan` PROPOSES a residual to re-drive from a resume anchor; this
    asks whether re-driving from that anchor stays inside DOS's declared reliability
    envelope — exactly-once for git-resident effects only. Two states, mutually
    exclusive:

      GIT_IDEMPOTENT  — the re-drive starts from a COMMITTED anchor (`resume_sha`
                        in ancestry, or empty = re-derive from HEAD) and re-does
                        the residual FORWARD of it. Git is content-addressed, so
                        re-applying work past a committed fossil never doubles a
                        durable git effect (it is a no-op / identical commit). Safe
                        to re-drive; the envelope holds. (A re-drive may still
                        re-fire a NON-git side effect a step took before committing
                        — OUTSIDE the envelope and the host adapter's to dedup;
                        this verdict speaks only to the git effect DOS witnesses.)
      ENVELOPE_BREACH — the resume anchor is a non-empty SHA NOT in ancestry: a
                        re-drive "from" a self-reported, uncommitted point. DOS
                        cannot guarantee the durable git effect is not skipped or
                        doubled, so it REFUSES — re-derive from HEAD instead. By
                        construction `resume_plan` gates its anchor to
                        in-ancestry-or-empty, so this fires only on a hand-built/
                        forged plan; fail-closed is the safe direction.

    `str`-valued for the `--json` token / exit-code idiom (the `Resume` shape).
    """

    GIT_IDEMPOTENT = "GIT_IDEMPOTENT"      # residual is the uncommitted tail — safe to re-drive
    ENVELOPE_BREACH = "ENVELOPE_BREACH"    # residual re-drives a committed step — refuse

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class RedriveVerdict:
    """The typed re-drive-contract verdict + the residual it adjudicated.

    `contract` is the `RedriveContract`. `reason` is the operator-facing one-liner.
    `offending_steps` carries the BREACH cause when ENVELOPE_BREACH (empty when
    GIT_IDEMPOTENT) — the un-committed resume anchor the re-drive would start from.
    `non_git_caveat` is always True: a GIT_IDEMPOTENT verdict guarantees only the
    git effect is not double-done; a non-git side effect a step fired before
    committing is outside the envelope and the host must make idempotent
    (`docs/342 §4`). `to_dict` is the `--json` shape (the `ResumePlan.to_dict` idiom).
    """

    contract: RedriveContract
    reason: str
    run_id: str
    offending_steps: tuple[str, ...] = ()
    non_git_caveat: bool = True

    @property
    def is_git_idempotent(self) -> bool:
        return self.contract is RedriveContract.GIT_IDEMPOTENT

    def to_dict(self) -> dict:
        return {
            "contract": self.contract.value,
            "reason": self.reason,
            "run_id": self.run_id,
            "offending_steps": list(self.offending_steps),
            "non_git_caveat": self.non_git_caveat,
        }


def redrive_contract(
    plan: ResumePlan,
    state: LedgerState,
    ancestry: AncestryFacts,
    policy: ResumePolicy = DEFAULT_POLICY,
) -> RedriveVerdict:
    """Assert a `ResumePlan`'s residual is git-idempotent to re-drive. PURE — no I/O.

    The docs/342 §4 P-EXACTLY-ONCE contract, made a checkable verdict. The bound it
    proves is the module docstring's load-bearing safety property restated as a test:
    **the re-drive starts from a COMMITTED anchor — `resume_sha` is in ancestry (a
    git fossil) or empty (re-derive from HEAD), NEVER a non-ancestry self-reported
    SHA.** Re-driving the residual *forward of a committed anchor* is git-idempotent
    by construction: git is content-addressed, so re-applying work whose commit is
    already in history yields the same tree (a no-op / identical commit), never a
    doubled durable git effect. That is exactly TCP's guarantee on its own layer —
    re-sending bytes past the acknowledged point is harmless.

    Why the ANCHOR, not a per-step "is this residual step committed?" check: a step
    that was verified but sits DOWNSTREAM of a hole (s2 committed, s1 not) is
    correctly put back in the residual by `resume_plan` (the resume restarts from
    before the hole). Re-driving s2 then re-applies a commit already in ancestry —
    which is git-idempotent, NOT a breach, because git dedups identical content. The
    real breach is re-driving from an anchor that is NOT a committed fossil, which
    would skip the discipline of restarting from ground truth (the docs/103 disease:
    trusting a self-reported SHA). So the contract guards the anchor.

    The anchor is checked against the SAME re-adjudicated `ancestry` the resume
    verdict used (`AncestryFacts.contains`), NEVER the agent-written record — the
    docs/107 §5 distrust rule. `resume_plan` already gates its `resume_sha` to
    in-ancestry-or-empty, so its own output is always GIT_IDEMPOTENT; this fires
    `ENVELOPE_BREACH` only on a hand-built/forged plan whose anchor is an unverified
    self-report. Fail-CLOSED: the contract is the proof, so it must catch a malformed
    plan, not assume well-formedness.

    The verdict speaks ONLY to the git effect: a GIT_IDEMPOTENT re-drive may still
    re-fire a NON-git side effect a step took before committing (`non_git_caveat` is
    always True). That is outside DOS's reliability envelope and the host adapter's
    to dedup (`docs/342 §4` option 2) — the kernel does not witness the POST, so it
    cannot and does not claim exactly-once for it. `policy` is accepted for signature
    symmetry with `resume_plan` (a future seam); the anchor check needs none of it.
    """
    rid = plan.run_id or state.run_id
    # The breach: a non-empty resume anchor that is NOT a committed fossil. Re-driving
    # "from" a self-reported, non-ancestry SHA is the docs/103 disease — it skips the
    # restart-from-ground-truth discipline that makes the re-drive git-idempotent. An
    # EMPTY anchor is safe (it tells the driver to re-derive from HEAD, the honest
    # fallback). An in-ancestry anchor is safe (a real fossil to re-drive forward of).
    anchor = (plan.resume_sha or "").strip()
    if anchor and not ancestry.contains(anchor):
        return RedriveVerdict(
            contract=RedriveContract.ENVELOPE_BREACH,
            reason=(
                f"resume anchor {anchor[:12]} is NOT in ancestry — re-driving from a "
                f"self-reported, uncommitted point is outside DOS's exactly-once "
                f"envelope (it cannot guarantee the durable git effect is not skipped "
                f"or doubled; docs/342 §4); refusing — re-derive from HEAD instead"
            ),
            run_id=rid,
            offending_steps=(anchor,),
        )
    return RedriveVerdict(
        contract=RedriveContract.GIT_IDEMPOTENT,
        reason=(
            f"re-drive starts from a committed anchor "
            f"({anchor[:12] + ' (in ancestry)' if anchor else 're-derive from HEAD'}) "
            f"and re-does {len(plan.residual)} residual step(s) forward of it — "
            f"git-idempotent (non-git side effects remain the host's to make "
            f"idempotent, docs/342 §4)"
        ),
        run_id=rid,
        offending_steps=(),
    )


# --------------------------------------------------------------------------
# Reachability — the docs/106 GC verdict, extended from leases to unfinished work (§4).
# --------------------------------------------------------------------------


class Reachability(str, enum.Enum):
    """Is a run-dir GARBAGE (collectible) or REACHABLE (retain)? — the §4 GC clause.

    `docs/106`'s reachability-is-a-verdict rule, extended from leases to *unfinished
    work*: **what is reachable is what an adjudicator says can still make progress,
    not what holds a reference or beat a clock.** A refcount/TTL is UNSOUND here — a
    crashed run's `intent.jsonl` holds a resumable residual that no live reference
    points at, yet it is NOT garbage; and a SUSPENDED (parked) run released its lane
    but its ledger is reachable, not collectible. So reachability is ADJUDICATED (the
    `ResumePlan`), never counted.

    `str`-valued for the `--json` token / exit-code idiom.
    """

    REACHABLE = "REACHABLE"      # still resumable (or parked-and-resumable) — RETAIN regardless of age
    COLLECTIBLE = "COLLECTIBLE"  # terminal-COMPLETE or UNRESUMABLE — safe to GC the run-dir

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class ReachabilityVerdict:
    """The typed run-dir reachability verdict + the resume verdict it rests on.

    Carries the `ResumePlan.verdict` it derived from + whether the run is SUSPENDED
    so a surfaced GC decision is legible ("retained: SUSPENDED-RESUMABLE, parked by
    the operator" vs "collectible: COMPLETE — every step verified"). `to_dict` is
    the `--json` shape.
    """

    reachability: Reachability
    reason: str
    run_id: str
    resume_verdict: Resume
    suspended: bool = False

    @property
    def is_collectible(self) -> bool:
        return self.reachability is Reachability.COLLECTIBLE

    def to_dict(self) -> dict:
        return {
            "reachability": self.reachability.value,
            "reason": self.reason,
            "run_id": self.run_id,
            "resume_verdict": self.resume_verdict.value,
            "suspended": self.suspended,
        }


def classify_run_dir_reachability(
    state: LedgerState,
    plan: ResumePlan,
) -> ReachabilityVerdict:
    """Is this run-dir garbage? PURE — over a folded ledger + its resume plan (§4).

    The single §4 clause, stated exactly: **a run-dir is garbage only if its run is
    terminal-COMPLETE or its resume plan is UNRESUMABLE; a SUSPENDED-with-RESUMABLE
    run-dir is retained regardless of age.** Concretely:

      * RESUMABLE  → REACHABLE (a residual a successor can still make progress on —
        this is the whole point of the ledger; never GC it). A SUSPENDED-RESUMABLE
        run is the same verdict via a different door (`docs/107 §4`): pause made
        scavenge-immune.
      * DIVERGED   → REACHABLE (it needs a HUMAN decision, not a reaper — collecting
        it would silently drop a conflict that must be surfaced; the merge-conflict-
        as-verdict rule keeps the evidence around).
      * COMPLETE   → COLLECTIBLE (every declared step verified — the run finished; its
        ledger is forensics, safe to reap under the host's retention window).
      * UNRESUMABLE→ COLLECTIBLE (no INTENT / unreadable-newer / unsound fold — there
        is nothing to resume, so the run-dir is not holding recoverable work).

    The age/TTL is deliberately ABSENT from this verdict (the `docs/106` unsound-
    refcount lesson): retention is decided by the ADJUDICATOR (can it still make
    progress?), never by a clock. A host's reaper may add an age GRACE *on top* —
    "collectible AND older than N days" — but it may never reap a REACHABLE run-dir
    no matter how old, which is the safety property this clause guarantees.
    """
    rid = plan.run_id or state.run_id
    susp = state.suspended
    if plan.verdict is Resume.RESUMABLE:
        door = "SUSPENDED-RESUMABLE (parked, scavenge-immune)" if susp else "RESUMABLE"
        return ReachabilityVerdict(
            reachability=Reachability.REACHABLE,
            reason=(
                f"{door} — a residual a successor can still make progress on; "
                f"retained regardless of age (reachability is adjudicated, not "
                f"refcounted — docs/106)"
            ),
            run_id=rid, resume_verdict=plan.verdict, suspended=susp,
        )
    if plan.verdict is Resume.DIVERGED:
        return ReachabilityVerdict(
            reachability=Reachability.REACHABLE,
            reason=(
                "DIVERGED — needs a human decision, not a reaper; retained so the "
                "conflict is surfaced, never silently collected"
            ),
            run_id=rid, resume_verdict=plan.verdict, suspended=susp,
        )
    # COMPLETE or UNRESUMABLE — nothing recoverable; collectible (host adds an age grace).
    why = ("COMPLETE — every declared step verified; the run finished"
           if plan.verdict is Resume.COMPLETE
           else "UNRESUMABLE — no recoverable work to resume")
    return ReachabilityVerdict(
        reachability=Reachability.COLLECTIBLE,
        reason=f"{why}; the run-dir holds no resumable residual — safe to GC",
        run_id=rid, resume_verdict=plan.verdict, suspended=susp,
    )
