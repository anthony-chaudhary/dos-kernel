"""queue_saturation — is the HUMAN escalation rung still a real target, or already drowned?

> **The load-bearing lie of the scaled world.** Every recovery path in the kernel
> terminates at "surface to a HUMAN": `breaker` escalates `on_trip=HUMAN`,
> `decisions` queues for the HUMAN resolver, `pulse` counts `pending_human`. Each
> silently assumes a human exists who reads it *in time*. In a world with many more
> agents all running 24/7, decisions arrive at the queue faster than any human drains
> them — and at that point "escalate to a human" *looks like* progress while being
> silently a no-op. docs/121 §4.1 names the consequence exactly: "a refusal routed to
> an absent operator blocks forever — and 'blocks forever' silently reads, to anything
> downstream, as 'didn't happen.'" Nothing in the kernel could SEE that the rung had
> saturated: `pulse` pushes an identical line at 3 pending or 3000. This leaf is the
> verdict that turns "the operator will handle it" from an unexamined assumption into
> a checkable fact.

What this IS, named after its mechanism rather than the queueing metaphor: a
**rate-comparison verdict** — the same pure `classify(evidence, policy) -> verdict`
shape as `liveness` (state moving?), `productivity` (rate fading?), and `efficiency`
(work worth its price?), re-aimed at one more stream: the HUMAN-rung decision queue's
own *arrival vs drain* rate over a recent window. It answers the one question none of
its siblings can — `liveness` watches a single run, `fleet_roll` folds run STATES,
`breaker` escalates ONE failure class to HUMAN without ever asking whether the HUMAN
rung itself still has capacity. This leaf folds the queue's dynamics: over the window,
are HUMAN decisions arriving faster than they are being resolved?

The closed verdict vocabulary (worst-first, the `fleet_roll`/`liveness` convention):

  SATURATED  — arrivals materially outrun drains over the window (ρ ≥ saturated_ratio):
               the human rung is no longer a meaningful escalation target. A consumer
               should auto-degrade (shed, downgrade HUMAN→JUDGE, auto-approve a safe
               class) — NOT push one more unread line. The actionable verdict.
  SATURATING — arrivals are outpacing drains but not yet past the saturated floor
               (warn_ratio ≤ ρ < saturated_ratio): the queue is growing; the rung is
               under pressure but not gone. A leading indicator, surfaced at WARN.
  DRAINING   — drains keep pace (ρ < warn_ratio): the healthy steady state, AND the
               fail-to-abstain floor — too little evidence to judge degrades HERE,
               never to an accusation.

The byte-author floor (docs/138), the property a 1000x fleet pays for
====================================================================
Both counts in `QueueEvidence` are **env/journal-authored**, downstream of an
already-decided verdict: an *arrival* is a HUMAN-resolver `decisions.Decision` the
kernel itself minted (a refusal the agent could not author into existence), and a
*drain* is a later RELEASE / override-admit / supersession event already in the lane
journal — written by the lease machinery, not the worker. So a fleet cannot narrate
its way out of SATURATED: no worker's self-report ("the operator is handling it") can
move the verdict, because the verdict reads only counts the workers did not write.
This is the same floor `efficiency` rests on (the work is what git witnessed, the
tokens what the provider billed) — here the arrivals are what the kernel refused and
the drains what the lease machinery released.

PURE and TIMELESS (the `productivity` posture)
==============================================
`classify` reads a window of counts, NOT live ages — so it makes NO I/O at all (it
does not even read the clock `liveness` still reads). The boundary (`cli.py`) gathers
the arrivals/drains over the window from the SAME `decisions` + `lane_journal` sources
`pulse` already reads, freezes them into a `QueueEvidence`, and hands it in. The leaf
folds them into ρ and the verdict. This makes the whole verdict replay-testable on
frozen fixtures with zero live fleet — the `breaker.classify` property.

The advisory floor (docs/99) — a verdict, never an actuator
============================================================
This leaf REPORTS that the rung is saturated; it never acts on the fleet. The ACT —
shed work, downgrade an escalation rung, auto-approve a safe decision class — is
deliberately host/driver policy, exactly as `supervise` emits SPAWN but never
`Popen`s and `breaker` names HUMAN but never queues. The kernel computes that the
rung is gone; what to do about it is the consumer's call. A consumer that DOES auto-
degrade now has the one precondition it was missing: a verdict, read from bytes the
fleet did not author, that says the human is no longer there to ask.

⚓ Kernel discipline (the litmus): PURE Layer-1 leaf — imports only stdlib +
`dataclasses`; names no host, no vendor, no driver; the ρ thresholds + minimum-
evidence floor are POLICY (a frozen `SaturationPolicy` declared in `dos.toml [queue]`,
the closed-config-as-data seam `[breaker]`/`[liveness]` already use); the verdict
echoes its evidence (the `liveness`/`breaker` legible-distrust discipline).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


# The durable_schema floor (docs/116 §6): a verdict a dashboard / peer reads carries a
# schema tag. Bump only on an incompatible shape change.
QUEUE_SATURATION_SCHEMA = 1


class Saturation(str, enum.Enum):
    """The closed verdict vocabulary — worst-first (SATURATED is the most severe).

    A `str` enum so a `to_dict()` / JSON render carries the plain word and a stand-in
    string folds equal, the `fleet_roll.FleetState` convention.
    """

    SATURATED = "SATURATED"    # ρ ≥ saturated_ratio — the human rung is gone; auto-degrade
    SATURATING = "SATURATING"  # warn_ratio ≤ ρ < saturated_ratio — under pressure, WARN
    DRAINING = "DRAINING"      # ρ < warn_ratio — healthy, AND the abstain floor

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class SaturationPolicy:
    """The ρ thresholds + minimum-evidence floor that define the verdict — POLICY.

    The same "mechanism is kernel, thresholds are config" split as `breaker`'s maxima
    and `liveness`'s windows. A workspace declares this in `dos.toml [queue]` (closed-
    config-as-data, like `[breaker]`/`[lanes]`); the defaults are generic and
    deliberately conservative — a fleet must be clearly over capacity before the
    kernel calls the human rung gone.

      warn_ratio       — ρ at or above which the queue is SATURATING (growing but the
                         rung is not yet gone). Default 0.9 — drains keeping ≥90 % pace
                         with arrivals is still "healthy enough"; below it is DRAINING.
      saturated_ratio  — ρ at or above which the queue is SATURATED (the rung is no
                         longer a meaningful target). Default 1.0 — arrivals at least
                         matching drains with the queue already non-empty. Must be ≥
                         warn_ratio (a saturated floor below the warn floor is a config
                         contradiction — refuse it rather than build a verdict whose
                         bands overlap).
      min_arrivals     — the fail-to-abstain floor: fewer than this many arrivals in
                         the window is too little evidence to accuse, so the verdict is
                         DRAINING regardless of ρ. Default 5 — a handful of decisions is
                         not a saturated rung, it is a quiet one. 0 disables the floor
                         (every non-empty window is judged on ρ alone).

    ρ (the load factor) is arrivals / max(drains, 1) over the window — the standard
    queueing utilization, clamped so a zero-drain window does not divide by zero (it
    yields ρ = arrivals, which is correctly ≥ saturated_ratio for any real backlog).
    """

    warn_ratio: float = 0.9
    saturated_ratio: float = 1.0
    min_arrivals: int = 5

    def __post_init__(self) -> None:
        if self.warn_ratio < 0 or self.saturated_ratio < 0:
            raise ValueError("queue saturation ratios must be non-negative")
        if self.saturated_ratio < self.warn_ratio:
            raise ValueError(
                "saturated_ratio must be >= warn_ratio — a saturated floor below the "
                "warn floor would make the SATURATING band empty/inverted"
            )
        if self.min_arrivals < 0:
            raise ValueError("min_arrivals must be non-negative")


DEFAULT_POLICY = SaturationPolicy()


@dataclass(frozen=True)
class QueueEvidence:
    """The env/journal-authored counts the boundary gathered over a recent window.

    Every field is downstream of an already-decided kernel verdict — the floor that
    makes the verdict non-forgeable (a worker authored none of these):

      arrivals     — HUMAN-resolver `decisions.Decision`s that ENTERED the queue over
                     the window (a refusal the kernel minted; the worker could not
                     author its existence).
      drains       — decisions that LEFT the queue over the window: a lease RELEASE, an
                     operator override-admit, a supersession — events the lease
                     machinery wrote to the lane journal, not the worker.
      pending      — the queue depth right now (HUMAN rows currently unresolved). Used
                     only to gate the SATURATED call: a high ρ with an empty queue is
                     not saturation (everything that arrived also drained), so SATURATED
                     requires `pending > 0`. A leading SATURATING can fire on ρ alone.
      window_seconds — the wall-clock span the counts cover (for the operator-facing
                     reason + the `--json` consumer); the verdict itself is a function
                     of the counts, not the span (timeless).

    Immutable + replay-testable: a test freezes one of these and asserts the verdict,
    with no live fleet — the `breaker.BreakerCounts` property.
    """

    arrivals: int = 0
    drains: int = 0
    pending: int = 0
    window_seconds: int = 0

    def __post_init__(self) -> None:
        if self.arrivals < 0 or self.drains < 0 or self.pending < 0:
            raise ValueError("queue evidence counts must be non-negative")
        if self.window_seconds < 0:
            raise ValueError("window_seconds must be non-negative")

    @property
    def load_factor(self) -> float:
        """ρ = arrivals / max(drains, 1) — the queueing utilization, divide-safe.

        A zero-drain window yields ρ = arrivals (drains floored at 1), which is the
        correct severe reading: nothing left the queue while N things entered it.
        """
        return self.arrivals / max(self.drains, 1)


@dataclass(frozen=True)
class SaturationVerdict:
    """The verdict + the evidence it rests on — legible distrust (`liveness`/`breaker`).

    `verdict` is the typed `Saturation`. `load_factor` is the computed ρ echoed so the
    operator/forensics sees WHY (never a bare label). `reason` is the one-line
    operator-facing summary. `arrivals`/`drains`/`pending` are the folded counts the
    `--json` consumer and a `dos top` chip read.
    """

    verdict: Saturation
    load_factor: float
    reason: str
    arrivals: int = 0
    drains: int = 0
    pending: int = 0
    schema: int = QUEUE_SATURATION_SCHEMA

    @property
    def is_saturated(self) -> bool:
        """True iff the human rung is gone — the bit a consumer auto-degrades on."""
        return self.verdict is Saturation.SATURATED

    @property
    def under_pressure(self) -> bool:
        """True iff SATURATED or SATURATING — the queue is growing either way."""
        return self.verdict in (Saturation.SATURATED, Saturation.SATURATING)

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "verdict": self.verdict.value,
            "load_factor": round(self.load_factor, 4),
            "reason": self.reason,
            "arrivals": self.arrivals,
            "drains": self.drains,
            "pending": self.pending,
        }


def classify(
    evidence: QueueEvidence, policy: SaturationPolicy = DEFAULT_POLICY
) -> SaturationVerdict:
    """Is the HUMAN escalation rung saturated? PURE — counts in, verdict out, no I/O.

    The fold, top to bottom:

      1. ABSTAIN FLOOR (fail-to-abstain, the load-bearing safety property). Fewer than
         `min_arrivals` decisions in the window is too little evidence — degrade to
         DRAINING, never accuse. A 1000x fleet that auto-degrades on this verdict must
         NOT do so on a quiet window; an absent-evidence SATURATED would be the exact
         "consistency, not grounding" sin the witness family exists to prevent. This
         rung is checked FIRST so no amount of ρ can push thin evidence to SATURATED.
      2. SATURATED iff ρ ≥ saturated_ratio AND the queue is currently non-empty. The
         pending gate stops a window where everything that arrived also drained
         (ρ high, queue empty) from reading as saturated — that is a busy-but-keeping-
         up rung, not a gone one.
      3. SATURATING iff ρ ≥ warn_ratio (the queue is growing; the rung is under
         pressure but not yet gone). Fires on ρ alone — it is a leading indicator.
      4. DRAINING otherwise — drains keep pace, the healthy steady state.
    """
    rho = evidence.load_factor
    base = dict(
        load_factor=rho,
        arrivals=evidence.arrivals,
        drains=evidence.drains,
        pending=evidence.pending,
    )

    # 1. The abstain floor — thin evidence is never an accusation.
    if evidence.arrivals < policy.min_arrivals:
        return SaturationVerdict(
            verdict=Saturation.DRAINING,
            reason=(
                f"only {evidence.arrivals} arrival(s) in the window "
                f"(< min {policy.min_arrivals} to judge) — too little evidence to "
                f"call the human rung saturated; treating as DRAINING"
            ),
            **base,
        )

    # 2. SATURATED — arrivals outrun drains AND a real backlog remains.
    if rho >= policy.saturated_ratio and evidence.pending > 0:
        return SaturationVerdict(
            verdict=Saturation.SATURATED,
            reason=(
                f"{evidence.arrivals} arrivals vs {evidence.drains} drains "
                f"(ρ={rho:.2f} >= {policy.saturated_ratio:.2f}), {evidence.pending} "
                f"still pending — the HUMAN escalation rung is no longer a meaningful "
                f"target; auto-degrade (shed / HUMAN->JUDGE / auto-approve a safe "
                f"class) rather than queue one more unread decision"
            ),
            **base,
        )

    # 3. SATURATING — growing, under pressure, but the rung is not yet gone.
    if rho >= policy.warn_ratio:
        return SaturationVerdict(
            verdict=Saturation.SATURATING,
            reason=(
                f"{evidence.arrivals} arrivals vs {evidence.drains} drains "
                f"(ρ={rho:.2f} >= warn {policy.warn_ratio:.2f}) — the human queue is "
                f"growing; the rung is under pressure but not yet saturated"
            ),
            **base,
        )

    # 4. DRAINING — drains keep pace; the healthy steady state.
    return SaturationVerdict(
        verdict=Saturation.DRAINING,
        reason=(
            f"{evidence.arrivals} arrivals vs {evidence.drains} drains "
            f"(ρ={rho:.2f} < warn {policy.warn_ratio:.2f}) — drains keep pace; the "
            f"human escalation rung has capacity"
        ),
        **base,
    )


def policy_from_table(table: dict) -> SaturationPolicy:
    """Build a `SaturationPolicy` from a parsed `[queue]` table. PURE — dict in, policy out.

    The OVERRIDE shape (the `[tool_stream]`/`[liveness]` pattern): a present key replaces
    that threshold; an absent one inherits the generic default. A malformed value raises
    via `SaturationPolicy.__post_init__` (a present-but-contradictory config is a config
    error, not silently swallowed).
    """
    d = SaturationPolicy()
    return SaturationPolicy(
        warn_ratio=float(table.get("warn_ratio", d.warn_ratio)),
        saturated_ratio=float(table.get("saturated_ratio", d.saturated_ratio)),
        min_arrivals=int(table.get("min_arrivals", d.min_arrivals)),
    )


def load_from_toml(path, *, base: SaturationPolicy = DEFAULT_POLICY) -> SaturationPolicy:
    """Build a `SaturationPolicy` from a `dos.toml`'s `[queue]` table.

    Returns `base` unchanged when the file is absent, has no `[queue]` table, or
    `tomllib` is unavailable — the declarative path is purely additive, so a missing/empty
    config degrades to the generic default, never an error. A *present but malformed* table
    raises (`SaturationPolicy.__post_init__`). Reads with `utf-8-sig` to strip a
    PowerShell-written BOM (the `tool_stream.load_from_toml` fix). The I/O lives in this
    loader (the boundary), NOT in `classify` — the leaf's purity is intact.
    """
    from pathlib import Path  # noqa: PLC0415 — boundary I/O, kept out of the pure core

    p = Path(path)
    if not p.exists():
        return base
    # ONE shared, mtime-keyed parse (`_tomlcache`) - collapses the per-config-layer
    # re-read/re-parse storm on `dos.toml`. A malformed file still raises here
    # (uncached), so the caller's existing handling is unchanged; the missing-file
    # guard above is untouched. The utf-8-sig BOM strip lives inside the helper.
    from dos._tomlcache import read_toml_cached
    data = read_toml_cached(p)
    table = data.get("queue")
    if not isinstance(table, dict) or not table:
        return base
    return policy_from_table(table)
