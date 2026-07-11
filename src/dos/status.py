"""The status digest — one fail-closed, folded fact about a run (docs/120).

Layer-1 projection: folds four shipped verdicts (liveness / ledger-verified /
held lease / resume) into one record. PURE — the four verdicts are computed at
the caller boundary and handed in (the `arbitrate`/`classify` rule: state in,
verdict out, no I/O). The record carries **no `claimed` field** by construction:
a consumer of the digest cannot read a self-report, because the output type has
no slot for one (docs/120 §3, the fail-closed invariant).

This is Phase 1 of docs/120: the pure fold only. The CLI boundary that *gathers*
the four verdicts (`dos status <run_id>`) is Phase 2; the `dos_status` MCP tool is
Phase 3. Nothing imports this module yet — it is self-contained and cannot affect
the running kernel.
"""
from __future__ import annotations

from dataclasses import dataclass

from dos.intent_ledger import LedgerState
from dos.liveness import LivenessVerdict
from dos.resume import ResumePlan

# The durable_schema floor (docs/116 §6): the digest is a record other tools read,
# so tag it. A consumer that understands an older schema refuses a newer digest
# rather than misparsing it — refuse, don't guess.
STATUS_DIGEST_SCHEMA = 1


@dataclass(frozen=True)
class ProgressView:
    """The adjudicated progress view — derived from `LedgerState.verified` ONLY.

    `verified_count` / `declared_count` / `verified_steps` are read off the
    kernel-minted `verified` map (the TRUSTED rung). There is deliberately no
    `claimed` field here: the agent's self-report (`LedgerState.claimed`, a pointer
    to a commit to check, not proof) is never surfaced through the digest.
    """

    verified_count: int
    declared_count: int
    verified_steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class StatusDigest:
    """One run's status as a single folded, fail-closed fact (docs/120 §5).

    Every field is an *adjudicated* verdict, never a self-report:
      liveness  — the in-flight "is it moving" verdict (`liveness.classify`)
      progress  — verified-step view (reads `verified`, never `claimed`)
      region    — the run's held lease globs (or () if it holds none)
      resume    — the resume verdict once the run has stopped (None while live)

    `to_dict` is the `--json` A2A contract a peer agent / dashboard parses. The
    one load-bearing property of that shape: `claimed` is absent.
    """

    run_id: str
    liveness: LivenessVerdict
    progress: ProgressView
    region: tuple[str, ...] = ()
    resume: ResumePlan | None = None
    transcript_uuid: str = ""
    schema: int = STATUS_DIGEST_SCHEMA

    def to_dict(self) -> dict:
        # The --json shape. `claimed` is ABSENT — that absence is the point
        # (docs/120 §3): a consumer cannot pick a self-report it is never handed.
        return {
            "schema": self.schema,
            "run_id": self.run_id,
            "liveness": self.liveness.to_dict(),
            "progress": {
                "verified_count": self.progress.verified_count,
                "declared_count": self.progress.declared_count,
                "verified_steps": list(self.progress.verified_steps),
            },
            "region": list(self.region),
            "resume": self.resume.to_dict() if self.resume is not None else None,
            **({"transcript_uuid": self.transcript_uuid} if self.transcript_uuid else {}),
        }


def status_digest(
    *,
    run_id: str,
    ledger_state: LedgerState,
    liveness_verdict: LivenessVerdict,
    live_region: tuple[str, ...] = (),
    resume_plan: ResumePlan | None = None,
    transcript_uuid: str = "",
) -> StatusDigest:
    """Fold the four already-computed verdicts into one digest. PURE.

    The four inputs are gathered at the caller boundary (Phase 2) and handed in;
    this function makes no subprocess, file, or clock call — the same posture as
    `resume.resume_plan` and `liveness.classify`.

    `progress` is built from `ledger_state.verified` ONLY — `ledger_state.claimed`
    is read by nothing here. Fail-closed: an empty / no-intent `LedgerState` yields
    a zero `ProgressView` (declared 0, verified 0), never a raise and never a
    guessed-optimistic default. A run that declared no adjudicable intent is still
    a valid fact ("nothing verified, nothing declared"), not an error.
    """
    verified = ledger_state.verified  # {step_id: VerifiedStep} — the minted rung
    progress = ProgressView(
        verified_count=len(verified),
        declared_count=len(ledger_state.declared_steps),
        verified_steps=tuple(sorted(verified)),
    )
    return StatusDigest(
        run_id=run_id,
        liveness=liveness_verdict,
        progress=progress,
        region=live_region,
        resume=resume_plan,
        transcript_uuid=transcript_uuid,
    )
