"""dos.drivers._effect_gate — the shared effect gate behind the guardrail adapters (docs/305).

The vendor-free core both framework adapters wrap (`openai_agents_guardrail`,
`crewai_guardrail`): a finished agent task asserts its work landed; this gate
re-reads the world from surfaces the agent did not author — git's object
database, the filesystem, the ship oracle — and folds each declared deliverable
through the kernel's PURE `effect_witness.witness_effect` join. The gate is the
boundary-I/O half of that keystone: it GATHERS the read-backs; the kernel
decides what they mean.

The claim model — declared, never parsed
========================================

The kernel never parses prose, so the gate does not either. A host DECLARES
what the task is supposed to produce, at gate construction:

  * ``CommitClaim(baseline=None)``   — "new commit(s) landed beyond baseline".
    ``baseline=None`` → the gate captures ``HEAD`` at construction (build the
    guardrail before the run starts; anything the run lands is then visible).
  * ``FileClaim(path, non_empty=…)`` — "this file exists (and has bytes)".
  * ``ShippedClaim(plan, phase)``    — "this (plan, phase) shipped", answered
    by the ship oracle — which needs no plan registry, so the gate works
    against a plain git repo (the `verify`-needs-no-plan litmus, held through
    the adapter).

A host that DOES want claims read out of the output text injects
``extract: (text) -> Sequence[Claim]`` — its parser, its policy, never ours.
Declared and extracted claims combine; both flow through the same join.

**Completion is the claim.** A finished run handing back a final output IS the
completion claim — the gate does not hunt the text for the word "done". A host
whose agents honestly report failure narrows this with ``extract``.

The verdict fold (docs/305 §1)
==============================

Per claim: one `EffectClaim` + one read-back `EvidenceFacts` (``attest`` /
``refute`` / ``no_signal``; accountability ``OS_RECORDED`` — read by the gate
process, never narrated by the agent) → `witness_effect`. Over the rows:

  * any REFUTED            → ``TRIPPED``   (the only outcome that blocks)
  * all CONFIRMED          → ``CLEAR``
  * no claims at all       → ``NO_CLAIM``
  * otherwise              → ``ABSTAINED`` (some row UNWITNESSED)

Fail-to-abstain (the judges discipline, docs/86): a gate that CRASHES —
extractor raise, git unreachable — abstains with the failure named in the
verdict, never fabricates a trip, never raises out of the seat. And an abstain
is never silent: every verdict, including the abstain, is handed to the seat's
``output_info`` channel so a consumer can see the gate looked and could not
tell. Only a REFUTED — an accountable read-back saying the claimed effect is
ABSENT — trips; that is the safe direction.
"""

from __future__ import annotations

import enum
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from dos.effect_witness import EffectClaim, EffectWitnessVerdict, witness_effect
from dos.evidence import Accountability, EvidenceFacts

__all__ = [
    "Claim",
    "CommitClaim",
    "FileClaim",
    "ShippedClaim",
    "GateOutcome",
    "GateVerdict",
    "EffectGate",
]


# ---------------------------------------------------------------------------
# The claim kinds — what a host declares the task must produce.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    """Base of the declared-deliverable kinds. Subclass per effect family."""


@dataclass(frozen=True)
class CommitClaim(Claim):
    """New commit(s) landed beyond ``baseline`` (a ref/SHA; None → gate-construction HEAD)."""

    baseline: str | None = None


@dataclass(frozen=True)
class FileClaim(Claim):
    """``path`` (workspace-relative or absolute) exists; ``non_empty`` → and has bytes."""

    path: str
    non_empty: bool = False


@dataclass(frozen=True)
class ShippedClaim(Claim):
    """The ship oracle finds ``(plan, phase)`` in git ancestry."""

    plan: str
    phase: str


# ---------------------------------------------------------------------------
# The gate verdict — the fold over the per-claim witness rows.
# ---------------------------------------------------------------------------


class GateOutcome(str, enum.Enum):
    TRIPPED = "TRIPPED"      # an accountable read-back REFUTED a claimed effect
    CLEAR = "CLEAR"          # every declared effect CONFIRMED present
    ABSTAINED = "ABSTAINED"  # could not tell (unreached witness, gate crash)
    NO_CLAIM = "NO_CLAIM"    # nothing declared or extracted — nothing to check

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class GateVerdict:
    """The seat-facing answer: trip / clear / abstain, with the rows behind it.

    ``tripped`` is the ONLY bit a seat may block on. ``reason`` is the
    one-line legible-distrust string — on a trip it names what was claimed,
    what the witness saw, and what would make it pass (the retry feedback).
    ``rows`` carry the per-claim `EffectWitnessVerdict`s for the operator
    surface / ``output_info``.
    """

    outcome: GateOutcome
    reason: str
    rows: tuple[EffectWitnessVerdict, ...] = field(default_factory=tuple)

    @property
    def tripped(self) -> bool:
        return self.outcome is GateOutcome.TRIPPED

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "tripped": self.tripped,
            "reason": self.reason,
            "rows": [r.to_dict() for r in self.rows],
        }


# ---------------------------------------------------------------------------
# The gate.
# ---------------------------------------------------------------------------


_NARRATED_HEAD = 240  # chars of the output text echoed onto each claim, for the operator


class EffectGate:
    """Gather read-backs for the declared claims; fold through `witness_effect`.

    Parameters
    ----------
    workspace:
        Repo root the read-backs run under. ``None`` → the process-active dos
        config's root.
    config:
        An explicit ``SubstrateConfig`` (wins over ``workspace``).
    expect:
        The declared deliverables (`Claim` instances), checked on every
        adjudication. A ``CommitClaim(baseline=None)`` is pinned to the HEAD
        observed HERE, at construction.
    extract:
        Optional host parser ``(text) -> Sequence[Claim]`` run per
        adjudication; its claims join ``expect``'s. A raising extractor
        ABSTAINS the whole verdict (fail-to-abstain), never trips it.
    """

    def __init__(
        self,
        workspace: str | None = None,
        *,
        config: Any = None,
        expect: Sequence[Claim] = (),
        extract: Callable[[str], Sequence[Claim]] | None = None,
    ) -> None:
        self._workspace = workspace
        self._config = config
        self._extract = extract
        # Pin every baseline-less CommitClaim to the head observed NOW — the
        # "build the guardrail before the run starts" semantics. An unreadable
        # head (not a git repo) stays None and the read-back will no_signal.
        head = None
        if any(isinstance(c, CommitClaim) and c.baseline is None for c in expect):
            head = self._head()
        self._expect: tuple[Claim, ...] = tuple(
            CommitClaim(baseline=head)
            if isinstance(c, CommitClaim) and c.baseline is None
            else c
            for c in expect
        )

    # -- the seat surface -------------------------------------------------------

    def adjudicate(self, output_text: str) -> GateVerdict:
        """The finished task's output → trip / clear / abstain. Never raises."""
        try:
            return self._adjudicate(output_text or "")
        except Exception as e:  # noqa: BLE001 — fail-to-abstain, never fabricate
            return GateVerdict(
                outcome=GateOutcome.ABSTAINED,
                reason=f"effect gate failed and abstained (never a trip): {e}",
            )

    # -- the fold ----------------------------------------------------------------

    def _adjudicate(self, text: str) -> GateVerdict:
        claims = list(self._expect)
        if self._extract is not None:
            claims.extend(self._extract(text) or ())
        if not claims:
            return GateVerdict(
                outcome=GateOutcome.NO_CLAIM,
                reason="no deliverable declared or extracted — nothing to witness",
            )

        narrated = text[:_NARRATED_HEAD]
        gathered = [self._claim_and_readback(c, narrated) for c in claims]
        rows = tuple(witness_effect(ec, facts) for ec, facts in gathered)

        refuted = [(r, gathered[i][1]) for i, r in enumerate(rows) if r.is_refuted]
        if refuted:
            row, facts = refuted[0]
            # The kernel row names WHAT was refuted; the read-back's detail says
            # what would make it pass — together they are the retry feedback a
            # seat hands back to the agent.
            detail = next((f.detail for f in facts if f.detail), "")
            reason = f"{row.reason}{' — ' + detail if detail else ''}"
            return GateVerdict(
                outcome=GateOutcome.TRIPPED,
                reason=reason,
                rows=rows,
            )
        if all(r.is_confirmed for r in rows):
            return GateVerdict(
                outcome=GateOutcome.CLEAR,
                reason="every declared effect confirmed by an accountable read-back",
                rows=rows,
            )
        unreached = [r for r in rows if not r.is_confirmed]
        return GateVerdict(
            outcome=GateOutcome.ABSTAINED,
            reason=f"could not witness {len(unreached)} of {len(rows)} claim(s): "
                   f"{unreached[0].reason}",
            rows=rows,
        )

    # -- the read-backs (the boundary I/O, one per claim kind) -------------------

    def _claim_and_readback(
        self, claim: Claim, narrated: str
    ) -> tuple[EffectClaim, tuple[EvidenceFacts, ...]]:
        if isinstance(claim, CommitClaim):
            return self._readback_commit(claim, narrated)
        if isinstance(claim, FileClaim):
            return self._readback_file(claim, narrated)
        if isinstance(claim, ShippedClaim):
            return self._readback_shipped(claim, narrated)
        raise TypeError(f"unknown claim kind {type(claim).__name__!r}")

    def _readback_commit(
        self, claim: CommitClaim, narrated: str
    ) -> tuple[EffectClaim, tuple[EvidenceFacts, ...]]:
        subject = f"{claim.baseline or '?'}..HEAD"
        eclaim = EffectClaim(key=f"commit:{subject}", narrated=narrated)
        if not claim.baseline:
            return eclaim, (EvidenceFacts.no_signal(
                "git_ancestry", Accountability.OS_RECORDED, subject,
                detail="no baseline head could be captured (not a git repo?)"),)
        out = self._git("rev-list", "--count", f"{claim.baseline}..HEAD")
        if out is None:
            return eclaim, (EvidenceFacts.no_signal(
                "git_ancestry", Accountability.OS_RECORDED, subject,
                detail="git rev-list unreachable under the workspace root"),)
        count = int(out.strip() or "0")
        if count > 0:
            return eclaim, (EvidenceFacts.attest(
                "git_ancestry", Accountability.OS_RECORDED, subject,
                detail=f"{count} new commit(s) beyond the baseline"),)
        return eclaim, (EvidenceFacts.refute(
            "git_ancestry", Accountability.OS_RECORDED, subject,
            detail=f"no commit beyond {claim.baseline} — do the work, land the "
                   f"commit, then report"),)

    def _readback_file(
        self, claim: FileClaim, narrated: str
    ) -> tuple[EffectClaim, tuple[EvidenceFacts, ...]]:
        eclaim = EffectClaim(key=f"file:{claim.path}", narrated=narrated)
        try:
            p = Path(claim.path)
            if not p.is_absolute():
                p = self._root() / p
            if p.is_file() and (not claim.non_empty or p.stat().st_size > 0):
                return eclaim, (EvidenceFacts.attest(
                    "file_readback", Accountability.OS_RECORDED, claim.path,
                    detail=f"present ({p.stat().st_size} bytes)"),)
            why = ("present but empty" if p.is_file() else "absent")
            return eclaim, (EvidenceFacts.refute(
                "file_readback", Accountability.OS_RECORDED, claim.path,
                detail=f"{why} under the workspace root — create the file, "
                       f"then report"),)
        except OSError as e:
            return eclaim, (EvidenceFacts.no_signal(
                "file_readback", Accountability.OS_RECORDED, claim.path,
                detail=f"stat failed: {e}"),)

    def _readback_shipped(
        self, claim: ShippedClaim, narrated: str
    ) -> tuple[EffectClaim, tuple[EvidenceFacts, ...]]:
        subject = f"{claim.plan} {claim.phase}"
        eclaim = EffectClaim(key=f"shipped:{subject}", narrated=narrated)
        try:
            from dos import oracle

            v = oracle.is_shipped(claim.plan, claim.phase, cfg=self._cfg())
        except Exception as e:  # noqa: BLE001 — an unreachable oracle is a no_signal
            return eclaim, (EvidenceFacts.no_signal(
                "ship_oracle", Accountability.OS_RECORDED, subject,
                detail=f"oracle unreachable: {e}"),)
        if getattr(v, "shipped", False):
            return eclaim, (EvidenceFacts.attest(
                "ship_oracle", Accountability.OS_RECORDED, subject,
                detail=f"shipped via {v.source}"
                       + (f" at {v.sha}" if getattr(v, "sha", None) else "")),)
        return eclaim, (EvidenceFacts.refute(
            "ship_oracle", Accountability.OS_RECORDED, subject,
            detail="no git evidence the phase shipped — land the stamped "
                   "commit, then report"),)

    # -- workspace plumbing --------------------------------------------------------

    def _cfg(self) -> Any:
        from dos import config as _config

        if self._config is not None:
            return self._config
        if self._workspace is not None:
            return _config.load_workspace_config(workspace=self._workspace)
        return _config.ensure(None)

    def _root(self) -> Path:
        return Path(self._cfg().root)

    def _git(self, *args: str) -> str | None:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=self._root(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout

    def _head(self) -> str | None:
        out = self._git("rev-parse", "HEAD")
        return out.strip() if out else None
