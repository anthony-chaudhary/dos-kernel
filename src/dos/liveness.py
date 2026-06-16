"""LVN — the liveness verdict: *is the agent actually moving, or just spinning?*

docs/82 — the 4th distrust syscall, the **temporal completion of `verify()`**.
`verify` distrusts a *finished* claim ("I shipped P"); LVN distrusts an
*in-flight* one ("I'm making progress"). A spinning agent is a systematically
optimistic narrator of its own motion — it reports "almost there / refining the
approach" while re-editing the same file and landing zero commits. It cannot see
its own loop; the operator can, but only by reading output and judging by hand
the thing DOS exists to adjudicate mechanically. LVN is the verdict that ends the
watching: it asks the **git history and the lane journal**, never the agent,
whether ground-truth state advanced.

This module is `loop_decide`'s sibling — a **pure** verdict function, the
`arbitrate()` / `gate_policy` shape:

    arbiter.arbitrate          (request, live_leases, config)  -> decision
    loop_decide.decide         (LoopState, IterationOutcome)    -> LoopDecision
    liveness.classify          (ProgressEvidence, policy)       -> LivenessVerdict
                               ^ THIS module

All I/O — reading git, the journal, the clock — happens in the CALLER (the
`dos liveness` CLI's evidence-gather), exactly as `pick_oracle`'s reads happen
outside `arbitrate()` and `verify`'s git reads happen outside the classifier.
`classify()` makes no subprocess, file, or clock call: `now_ms` is a field on the
evidence, never read inside the verdict. That is what lets the whole verdict be
replay-tested on frozen fixtures, away from anything that needs a live
multi-minute agent run to reproduce (the `loop_decide` design value, restated for
the temporal axis).

The verdict ladder, top to bottom — the whole point is that a reader holds it in
their head:

  1. ADVANCING — **any forward delta**: ≥1 commit since the run's start SHA, OR a
     state-mutating lane-journal event since start. State moved. (This rung wins
     over everything below — a run that committed is advancing regardless of how
     fresh its heartbeat is.) Also the **young-and-alive** case: a run with a
     fresh heartbeat that is younger than `grace_ms` has not earned a SPINNING
     accusation yet — it is alive and we decline to judge it stuck (no liveness
     *problem* detected), the grace guard against a false-positive on a run that
     simply hasn't committed in its first minute. NOTE: LVN says bytes *moved* (or
     "no problem yet"), never that they moved *well* — quality is an advisory
     judge's call (`llm_judge`), never this deterministic kernel verb (the
     distrust-state / distrust-judgment line).
  2. SPINNING  — no forward delta, the run is **alive** (a heartbeat fresher than
     `spin_ms`), AND it has been alive long enough to judge (run-age ≥
     `grace_ms`): alive, narrating, not moving — the signal with no existing
     home. This is the rung `loop_decide`'s self-report breakers can't reach,
     because it reads ground truth, not the caller's `IterationOutcome` token.
  3. STALLED   — no forward delta and **not alive**: the newest heartbeat is older
     than `spin_ms`, or there is no heartbeat at all. The run is dead or hung —
     the orphan-sweep's input, not a spin.

`SPINNING` is ADVISORY. LVN reports; it never kills a process or refuses a lease.
A loop may consult LVN and choose to stop (the natural first consumer, LVN-3a),
and the decisions queue may surface a spinning run — but the liveness verdict and
the admission decision stay different syscalls (a `LivenessPredicate` over ADM's
conjunctive seam is a possible *separate* opt-in driver policy, not LVN).

No-plan discipline (`test_verify_no_plan` sibling): LVN must return a verdict in a
plain git repo with a run-id and a start SHA and *nothing else* — no plan, no
registry, no journal, no telemetry. Commits-since-start alone is a sufficient
ADVANCING/SPINNING/STALLED signal; every richer input (`journal_events_since`,
`last_heartbeat_age_ms`, `tokens_spent_since`) is OPTIONAL and the verdict
degrades to the commit + caller-supplied-heartbeat rungs when they are absent.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional


class Liveness(str, enum.Enum):
    """The typed liveness verdict — three states, mutually exclusive.

    `str`-valued so it round-trips through a CLI stdout token / exit-code map
    without a lookup table (mirrors `gate_classify.Verdict` and
    `loop_decide.OutcomeKind`).
    """

    ADVANCING = "ADVANCING"  # ground-truth state moved since the run started
    SPINNING = "SPINNING"    # the run is alive (heartbeat fresh) but state is NOT moving
    STALLED = "STALLED"      # no fresh heartbeat, no commits — dead/hung, not spinning

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class LivenessPolicy:
    """The windows that separate ADVANCING/SPINNING/STALLED — policy, not mechanism.

    The same "mechanism is kernel, thresholds are config" split as
    `loop_decide`'s `max_unclear` / `max_iterations`. The defaults are GENERIC
    (no host tuning); a workspace declares its own in `dos.toml [liveness]`
    (LVN-3c) read back through `SubstrateConfig`, the closed-config-as-data
    pattern (`[lanes]` / `[stamp]` / `[reasons]`).

    The two windows have distinct jobs (the spec is loose on their interaction;
    this is the resolution Phase 1 pins):

      spin_ms  — the **heartbeat-freshness bound** that proves the run is *alive*.
                 A heartbeat younger than this means the process is up; older
                 (or absent) means it is not demonstrably alive → STALLED. It is
                 the alive/dead boundary.
      grace_ms — the **minimum run-age** an alive-but-idle run must reach before
                 LVN will accuse it of SPINNING. Below it, a run with a fresh
                 heartbeat and no commits is simply young — alive, not yet stuck —
                 and the verdict withholds the accusation (reports ADVANCING:
                 "no liveness problem yet"). This is the false-positive guard so a
                 run that hasn't committed in its first minute isn't called
                 spinning. (Run-age is `now_ms - run_started_ms`, both on the
                 evidence — this is the one rung that reads them.)

    Defaults: 30 min grace, 15 min spin. So: a run is alive while its heartbeat is
    ≤15 min old; an alive run with no forward delta is called SPINNING only once
    it is ≥30 min old; otherwise it is too young to judge (ADVANCING-benign). The
    granularity matches what the spine records (minutes), per the
    no-sub-second-liveness non-goal.
    """

    grace_ms: int = 30 * 60 * 1000   # 30 minutes — min run-age before SPINNING
    spin_ms: int = 15 * 60 * 1000    # 15 minutes — heartbeat-freshness (alive) bound

    def __post_init__(self) -> None:
        if self.grace_ms < 0 or self.spin_ms < 0:
            raise ValueError("liveness windows must be non-negative (ms)")


DEFAULT_POLICY = LivenessPolicy()


@dataclass(frozen=True)
class ProgressEvidence:
    """Everything `classify()` needs, gathered by the CALLER before the call.

    No git, no journal, no clock inside the verdict — the arbiter rule. The CLI's
    evidence-gather (the boundary) decodes `run_id.ts_ms_of(run_id)` for the
    start, counts `git_delta.commits_since(start_sha)` for the commit delta, folds
    the lane journal for the heartbeat/event rungs, and reads the wall clock —
    then freezes all of it here and hands it to the pure classifier.

      run_started_ms       — epoch-ms the run began (`run_id.ts_ms_of`). Carried
                             for the `--output json` consumer + age framing; the
                             verdict reads ages, not absolute times.
      now_ms               — wall-clock epoch-ms, injected at the boundary (the
                             env BANS `Date.now()` in reproducible paths; the
                             verdict never reads a clock).
      commits_since_start  — the authoritative forward delta
                             (`len(git_delta.commits_since(start_sha))`). ≥1 ⇒
                             ADVANCING, on its own, with everything else absent
                             (the no-plan floor).
      journal_events_since — count of lease-*work* lane-journal events
                             (ACQUIRE/RELEASE/SCAVENGE/RECONCILE — NOT a keepalive
                             HEARTBEAT, which is a *beat* not progress; see
                             `journal_delta._EVENT_OPS`) since start. ≥1 ⇒
                             ADVANCING even with 0 commits — work is happening at
                             the lease layer (wired in LVN Phase 2; 0 in Phase 1).
      last_heartbeat_age_ms — now − newest HEARTBEAT/ACQUIRE ts. None = never
                             beat (or the journal rung is absent). Separates
                             SPINNING (fresh) from STALLED (stale/None past grace).
      tokens_spent_since   — OPTIONAL waste signal: cost burned with no commit. A
                             workspace that tracks per-run cost may pass it; one
                             that doesn't passes None and the verdict is unaffected
                             (never required — the no-telemetry discipline). Echoed
                             for the json consumer; not an input to the ladder.
      process_alive        — OPTIONAL **unforgeable** OS-process rung (docs/95,
                             gathered by `proc_delta.probe` at the boundary):
                             True = the OS confirms the run's pid is up, False =
                             the OS confirms it is gone, None = could not tell
                             (no pid / foreign host / unsupported platform / a
                             PID-REUSE mismatch — the pid exists but its OS
                             starttime no longer matches the baseline the run
                             recorded, so it is a recycled number, not this run;
                             docs/95 §4.2). It is
                             **demote-only**: a confident `False` flips an
                             otherwise-SPINNING run to STALLED (a fresh heartbeat on
                             a dead process is the forgeable-beat gap this closes);
                             True/None NEVER promote a dead/stalled run to alive.
                             A workspace that can't probe passes None and the
                             verdict is byte-identical to before this field existed.
    """

    run_started_ms: int
    now_ms: int
    commits_since_start: int
    journal_events_since: int = 0
    last_heartbeat_age_ms: Optional[int] = None
    tokens_spent_since: Optional[int] = None
    process_alive: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.commits_since_start < 0 or self.journal_events_since < 0:
            raise ValueError("evidence counts must be non-negative")


@dataclass(frozen=True)
class LivenessVerdict:
    """The single verdict `classify()` returns, with the evidence echoed back.

    `verdict` is the typed `Liveness`. `reason` is a one-line operator-facing
    summary (the tally-row string). `evidence` is the `ProgressEvidence` that
    drove the call, carried so `dos liveness --output json` can emit the verdict
    *and the facts behind it* in one object (the renderer seam, RND/Axis-4) —
    legible distrust: the operator sees not just SPINNING but *why* (0 commits,
    heartbeat 8m fresh). `to_dict` is the json shape.
    """

    verdict: Liveness
    reason: str
    evidence: ProgressEvidence

    def to_dict(self) -> dict:
        ev = self.evidence
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "evidence": {
                "run_started_ms": ev.run_started_ms,
                "now_ms": ev.now_ms,
                "commits_since_start": ev.commits_since_start,
                "journal_events_since": ev.journal_events_since,
                "last_heartbeat_age_ms": ev.last_heartbeat_age_ms,
                "tokens_spent_since": ev.tokens_spent_since,
                "process_alive": ev.process_alive,
            },
        }


def classify(
    ev: ProgressEvidence, policy: LivenessPolicy = DEFAULT_POLICY
) -> LivenessVerdict:
    """Classify one run's liveness from already-gathered evidence. PURE — no I/O.

    Reads the ladder top to bottom (this function IS the answer to "is it
    moving?"):

      1. ADVANCING — any forward delta (≥1 commit OR ≥1 state-mutating journal
         event since start), OR the young-and-alive case (a fresh heartbeat on a
         run younger than `grace_ms`): state moved, or there is no liveness
         problem to flag yet.
      2. SPINNING  — no forward delta, a heartbeat fresher than `spin_ms` (alive),
         AND run-age ≥ `grace_ms` (old enough to judge): alive, narrating, not
         moving.
      3. STALLED   — no forward delta and not alive: the newest heartbeat is older
         than `spin_ms`, or there is none at all. Dead/hung.

    The ADVANCING/rest boundary is the *forward delta* (never clock-dependent).
    The alive/dead boundary is heartbeat freshness vs `spin_ms`. The
    too-young-to-judge guard is run-age (`now_ms - run_started_ms`) vs `grace_ms`
    — the ONE rung that reads the clock fields, and it reads them as a delta the
    caller pre-stamped, not by calling a clock (the clock-is-injected rule).
    """
    # 1a. ADVANCING (forward delta) — the authoritative rung and the no-plan
    #     floor: a commit since start answers in a plain git repo with no journal
    #     at all. A state-mutating journal event counts too — work at the lease
    #     layer is progress even with no commit yet (wired in Phase 2;
    #     journal_events_since is 0 in Phase 1, so this reduces to the commit
    #     rung). Checked FIRST so a run that moved is never mislabelled on a
    #     stale-heartbeat technicality.
    if ev.commits_since_start >= 1:
        return LivenessVerdict(
            verdict=Liveness.ADVANCING,
            reason=(
                f"{ev.commits_since_start} commit(s) since the run's start SHA "
                f"— ground-truth state moved"
            ),
            evidence=ev,
        )
    if ev.journal_events_since >= 1:
        return LivenessVerdict(
            verdict=Liveness.ADVANCING,
            reason=(
                f"{ev.journal_events_since} state-mutating lane-journal event(s) "
                f"since start — progress at the lease layer (0 commits)"
            ),
            evidence=ev,
        )

    # No forward delta. Heartbeat freshness decides alive-vs-dead; run-age decides
    # whether an alive run is old enough to be called spinning.
    age = ev.last_heartbeat_age_ms
    alive = age is not None and age <= policy.spin_ms

    if alive:
        # 1b. STALLED (proc-rung demote, docs/95) — the heartbeat says alive, but
        #     the OS says the run's pid is CONFIDENTLY gone. A fresh heartbeat on a
        #     dead process is the forgeable-beat gap: the wrapper kept touching the
        #     beat (or the crash left a fresh `heartbeat_at`) after the process
        #     died. The unforgeable OS rung overrides the forgeable one — DEMOTE
        #     to STALLED. Checked BEFORE the young-and-alive guard: a confidently-
        #     dead process is dead regardless of run-age — the "too young to judge"
        #     grace exists to spare a LIVE young run a false SPINNING, and its
        #     premise (the run is up) is exactly what the OS just refuted. Only a
        #     confident `False` demotes; `True`/`None` fall through and the
        #     heartbeat verdict stands (the rung never PROMOTES dead→alive — it can
        #     only make the verdict more skeptical).
        if ev.process_alive is False:
            return LivenessVerdict(
                verdict=Liveness.STALLED,
                reason=(
                    f"heartbeat {age} ms old says alive, but the OS reports the "
                    f"run's process is gone — STALLED (the unforgeable proc rung "
                    f"overrides a forgeable heartbeat; docs/95)"
                ),
                evidence=ev,
            )

        # 1c. ADVANCING (young-and-alive guard) — an alive run younger than
        #     `grace_ms` has not earned a SPINNING accusation. It is up, it just
        #     hasn't had time to commit; reporting SPINNING here would be a
        #     false positive on a run in its first minutes. We withhold and report
        #     "no liveness problem yet" (ADVANCING is the benign/no-action verdict
        #     — it explicitly does NOT claim a commit landed; the reason says so).
        run_age = ev.now_ms - ev.run_started_ms
        if run_age < policy.grace_ms:
            return LivenessVerdict(
                verdict=Liveness.ADVANCING,
                reason=(
                    f"alive (heartbeat {age} ms old) and only {run_age} ms into "
                    f"the run (< grace {policy.grace_ms} ms) — too young to judge "
                    f"spinning; no liveness problem yet (0 commits so far)"
                ),
                evidence=ev,
            )

        # 2. SPINNING — alive, old enough to judge, and not moving. A heartbeat
        #    fresher than `spin_ms` proves the run is up; with 0 commits and 0 new
        #    journal events past the grace age, it is burning tokens narrating
        #    motion it isn't making. The signal with no home in `loop_decide`
        #    (which reads the self-report, not ground truth).
        #
        #    The OPTIONAL waste signal (docs/82, fed by docs/300 §7): when
        #    `tokens_spent_since` is present, name the cost burned with no commit —
        #    the "spinning AND it cost N tokens" sentence an operator means by waste.
        #    The count NEVER moved the verdict here (it is SPINNING on the commit /
        #    journal / heartbeat rungs alone, docs/219); it only makes the reason
        #    legible. Absent ⇒ the reason is byte-identical to before the slot was fed.
        return LivenessVerdict(
            verdict=Liveness.SPINNING,
            reason=(
                f"alive (heartbeat {age} ms old ≤ spin window {policy.spin_ms} ms) "
                f"and {run_age} ms into the run (≥ grace {policy.grace_ms} ms) but "
                f"0 commits and 0 lane events since start — spinning"
                + (f" (burned {ev.tokens_spent_since} tokens while not moving)"
                   if ev.tokens_spent_since is not None else "")
            ),
            evidence=ev,
        )

    # 3. STALLED — no forward delta and not alive: the newest heartbeat is older
    #    than `spin_ms`, or there is none at all (None). The run is dead or hung,
    #    the orphan-sweep's input — not a spin (a spin requires proof of life).
    if age is None:
        reason = (
            "no heartbeat and 0 commits since start "
            "— run is dead or hung (never beat)"
        )
    else:
        reason = (
            f"heartbeat {age} ms old (> spin window {policy.spin_ms} ms) and "
            f"0 commits since start — run is dead or hung, not spinning"
        )
    return LivenessVerdict(verdict=Liveness.STALLED, reason=reason, evidence=ev)
