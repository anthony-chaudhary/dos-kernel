# docs/374 — 10× stewards: one invariant per steward

> **Status:** 📋 planned — design note proposing a *pattern* and a starter catalog, built
> on shipped machinery. Reuses the supervisor seam
> ([docs/210](210_the-supervise-config-seam.md)), the lifecycle gardener
> (`dos-class-cycle`), and the self-tuning enforcement loop
> ([docs/365](365_self-tuning-enforcement-policy-the-pep-feedback-loop.md)). No new
> kernel mechanism is required — the proposal is to run the existing judge/loop seams
> *wider and cheaper*, as a population of single-invariant units.
>
> Origin: operator thread 2026-06-16 — *"10x more cron tasks like stewards for steering
> and other validation … more verification at every stage."*

## The gap

DOS already has recurring adjudicators: the supervisor keeps a target population of
dispatch loops alive ([docs/210](210_the-supervise-config-seam.md)); `dos-class-cycle`
ticks plan lifecycle under a judge; `dos-enforce-tune`
([docs/365](365_self-tuning-enforcement-policy-the-pep-feedback-loop.md)) tunes
enforcement from outcomes. But these are a *few heavy* loops. The operator wants the
inverse shape: **10× as many, each far cheaper and narrower** — a steward per invariant,
not a monolith that checks everything.

## The thesis

> A steward is a small, narrow, recurring unit that owns exactly **one** invariant, is
> **fail-to-abstain** (it acts only on evidence it did not author, else it abstains), and
> does both **validation** (catch a violation) and **steering** (nudge the fleet's
> distribution of work back toward the invariant). Run a *population* of them — like
> systemd units in a supervisor tree — and garden the population itself with a
> meta-steward.

The design rule is the cap on complexity: one invariant per steward. A steward that
watches two things is two stewards. This is what keeps "10× more cron tasks" from
becoming "10× more noise."

## A starter catalog

Each steward names its witness (the non-self-authored evidence it reads) and its
steering action. All are advisory / fail-to-abstain by default.

| Steward | Invariant it owns | Witness | Steering action |
|---|---|---|---|
| ship-backing | every claimed "done" has a backing commit | git (`dos verify`) | re-dispatch the unbacked claim |
| lane-collision | no two workers race the same file tree | the file tree (`dos arbitrate`) | refuse the second acquire |
| spin | no worker re-confirms the same drain N× | the run archive | surface to `dos decisions` |
| recurring-wedge | no BLOCKED cause repeats across runs | the run-archive trail | propose a structural fix (`dos-unstick`) |
| pickable | no unit is silently un-pickable | the pickable gate | surface the typed HoldReason (`dos-promote`) |
| pollution | no tool result entered context without admission | the context-MMU log | quarantine + compress (`headroom`) |
| preflight-miss | no doomed call reached rung 3 that a cheaper rung could refute | the pre-flight log ([docs/373](373_preflight-mechanistic-verification-the-cheapest-rung-that-refutes.md)) | tighten the rung registry |
| freshness | no fresh plan starves behind churn | attempt history ([docs/254](254_the-freshness-sort-key-prefer-new-work-over-churn.md)) | re-order within tier |
| **meta-steward** | the stewards still earn their keep | each steward's own fire-rate | prune never-firing stewards; propose new ones for recurring un-caught failures |

The meta-steward is load-bearing, not optional: it is the only thing that stops steward
sprawl, and it is the recursion hook — a steward whose invariant is *"the steward set is
healthy."*

## Validation vs steering

Most monitoring stops at validation (raise an alarm). A steward also **steers**: it is a
closed loop over the fleet's work distribution. The lane-collision steward doesn't just
report a race, it refuses the colliding acquire; the freshness steward doesn't just note
churn, it re-orders the queue. Steering is what makes "stewards for steering" (the
operator's phrase) more than a dashboard.

## What ships (proposed)

No new kernel mechanism. The proposal is configuration + a thin runner:

| Piece | Home |
|---|---|
| A `[stewards]` table declaring each unit (invariant id, cadence, witness source, action, advisory/enforcing) | `dos.toml` (policy is data) |
| A `dos steward <id>` verb that runs one steward tick and returns a typed verdict | `dos steward` in `src/dos/cli.py` |
| The judge seam each steward calls (read-only, fail-to-abstain) | reuse `dos.judges` (the `dos-class-cycle` seam) |
| The population/cadence policy | reuse the `[supervise]` seam ([docs/210](210_the-supervise-config-seam.md)) |
| Tests | `tests/test_steward.py` |

## Litmus

- The kernel names no specific invariant — every steward in the catalog is a row in the
  `dos.toml [stewards]` table; the kernel only provides the run/judge/cadence mechanism.
- Each steward is fail-to-abstain: no witness → abstain, never act.
- A steward reads a witness it did not author (the DOS floor); a steward that judges its
  own output is rejected at config time.

## Done condition (proposed)

- `dos steward ship-backing` runs one tick, reads git, and surfaces an unbacked claim —
  pinned by `tests/test_steward.py`.
- The meta-steward prunes a deliberately-inert steward in a fixture run.
- A measured count: stewards firing per cycle, and false-fire rate, logged to the run
  archive so `dos-enforce-tune` can tune cadences.

## Related

- [docs/210](210_the-supervise-config-seam.md) — the supervisor population seam stewards reuse.
- [docs/365](365_self-tuning-enforcement-policy-the-pep-feedback-loop.md) — the self-tuning loop the meta-steward feeds.
- [docs/372](372_the-tool-call-as-syscall-the-adjudicated-call-layer.md) / [docs/373](373_preflight-mechanistic-verification-the-cheapest-rung-that-refutes.md) — the call-path stewards (pollution, preflight-miss) watch.
- Private: `dispatch-os-the-fused-agent-kernel.md` §4 (RSI-first).
