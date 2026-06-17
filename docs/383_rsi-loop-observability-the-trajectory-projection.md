# docs/383 — RSI loop observability: the trajectory projection

> **Status:** ✅ Phases 1–3 shipped — the trajectory projection + `dos observe
> --loops`, the correlation wiring on `dos improve` / `enforce-tune`, and the loop
> skills arming `--observe` per cycle. **Phase 4 (the live screen + the shape) ships
> here:** a `--watch` live refresh and a metric **sparkline** so an operator can leave
> `dos observe --loops` open in a side terminal and read whether the loop is still
> *climbing* at a glance, not just its endpoints. Built entirely on shipped machinery —
> the verdict journal ([docs/262](262_the-verdict-journal-observability-as-a-first-class-surface.md)),
> the `improve` keep-gate ([docs/280](280_the-self-improving-work-loop-the-kernel-adjudicates-its-own-improvement.md)), the
> read-only-projection discipline `observe` / `dispatch_top`, and the `--watch`/`--max-ticks`
> cadence `dos loop` / `dos watch` already prove. **No new kernel mechanism.**
>
> Origin: operator goal 2026-06-16 — *"make long-running, always-on RSI (recursive
> self-improvement) and similar 10× more observable to DOS and human operators."*

## The gap

DOS's long-running loops — `dos-self-improve` ([docs/280](280_the-self-improving-work-loop-the-kernel-adjudicates-its-own-improvement.md)),
`dos-enforce-tune` ([docs/365](365_self-tuning-enforcement-policy-the-pep-feedback-loop.md)) —
are exactly the runs an operator most needs to *watch*: they iterate autonomously for
a long time, each cycle proposing a candidate the kernel then KEEPs or REVERTs. Yet
they are the **least observable** thing the kernel runs:

1. **The per-iteration verdict evaporates.** `dos improve` records its KEEP/REVERT/
   ESCALATE to the verdict journal only when `--observe` / `DISPATCH_OBSERVE=1` is
   armed — and the loop skills never arm it. So a multi-hour RSI run leaves **no
   per-iteration trail at all**; the only artifact is the final loop record written
   when the loop *stops* (the self-improve skill's Step 5). While it runs, it is dark.
2. **Even when journaled, the iterations can't be correlated.** `dos improve` /
   `enforce-tune` carry no `--run-id`, `--lane`, or `--subject`, so a recorded verdict
   lands under the `(unattributed)` bucket — you cannot tell loop A's cycle 7 from
   loop B's cycle 2.
3. **`dos observe` shows a flat rollup, not a trajectory.** `dos observe` answers
   "47 improve verdicts: 30 KEEP, 17 REVERT" — a *census*, never the *curve* an
   operator watching an RSI run actually needs: which iteration is current, the
   metric's high-water trend (is it still climbing or flat?), how close the breaker
   is to ESCALATE, and whether the loop is still alive or wedged.

The thing the kernel exists to do — *adjudicate ground truth across a long-running
self-narrating loop* — is the thing it is currently blindest to in flight.

## The thesis

> A self-improving loop's history is **already a non-forgeable witness stream**: every
> KEEP/REVERT/ESCALATE is a kernel verdict over env-authored facts (docs/280). The fix
> is not to ask the loop how it's doing — it is to **fold the verdict stream it already
> emits into a trajectory**, the same way `dispatch_top` folds the lease WAL into a
> live lane screen. Observability of an RSI loop is a *read-only projection over the
> verdict journal*, not a new thing the loop reports about itself.

This keeps the docs/138 invariant intact: the trajectory is built from bytes the loop
did not author (the kernel's own verdicts + the env-measured metric counts), never from
the candidate's `narrated` self-assessment (carried for context, parsed for nothing).

## What ships — Phase 1 (this leg)

`src/dos/loop_trace.py` — a pure projection, the `observe` / `dispatch_top` discipline
restated for the loop axis. It folds the verdict journal's `improve`-syscall events,
grouped by `run_id`, into one `LoopTrajectory` per loop:

| Surface | What it answers |
|---|---|
| iteration count + the ordered KEEP/REVERT/ESCALATE sequence | where is the loop, and what has it decided? |
| the metric curve — first baseline → high-water → latest `work`, net gain | is it still improving, or flat? |
| `consecutive_reverts` now vs `max_reverts` → distance-to-ESCALATE | how close is the breaker to handing back to a human? |
| last-iteration age → an ACTIVE / STALLED / ESCALATED band | is it alive, wedged, or terminal? |
| the revert-cause split (REGRESSED / NO_IMPROVEMENT / WASTEFUL) | *why* the non-keeps — a fault vs an honest dry spell |

Surfaced via **`dos observe --loops`** (every loop, newest-activity-first) and
**`dos observe --loops --run <RID>`** (one loop's full per-iteration curve). No new
top-level verb — it rides the existing verdict-journal projection, respecting the
verb-census's anti-sprawl direction (issue #20). Plain-text floor always available;
`--json` for DOS / a dashboard.

## What ships — Phases 2–3 (the follow legs)

- **Phase 2 — correlation wiring.** Add `--run-id` / `--lane` / `--subject` (the
  iteration tag) to `dos improve` and `dos enforce-tune` so each recorded verdict
  carries its loop's identity and cycle index. Additive flags; the verdict logic is
  untouched.
- **Phase 3 — emit by default.** Update the `dos-self-improve` and `dos-enforce-tune`
  skills to arm `--observe` and pass `--run-id`/`--subject` per cycle, and to end with
  a `dos observe --loops` "watch it" line. The loop becomes observable *while it runs*,
  not only in its post-mortem.

## What ships — Phase 4 (the live screen + the shape)

Phases 1–3 make the trajectory *exist* and be *correlated*; Phase 4 makes it
*watchable*. Two additions, both pure-or-read-only, no new kernel mechanism:

- **`dos observe --loops --watch`** — re-read the journal and re-render on a cadence
  (`--interval SECS`, default 5; `--max-ticks N` bounds it for a script or the suite),
  clearing the screen each tick on a TTY so the trajectory updates in place. This is the
  `dos loop` / `dos watch` `--watch`/`--max-ticks` cadence restated for the loop axis —
  it makes the goal's own instruction literal ("leave `dos observe --loops` open in a
  side terminal") on a host with no `watch(1)`. Read-only every tick: it folds the
  verdict stream, takes no lease, mutates nothing. `--watch --json` streams one JSON
  frame per tick so a dashboard can tail it.
- **The metric sparkline.** A steady climb, a KEEP/REVERT sawtooth, and a long plateau
  all read *identically* in a `38→58 (hi 58, +20)` endpoint string — yet "is it still
  climbing, or flat?" is the plan's headline question, and it is a question about the
  curve's *shape*. `loop_trace._sparkline(metric_curve(t))` renders the per-iteration
  `work` series as block glyphs (e.g. `▁▂▂▅▅█` — the two plateaus are the REVERTed
  cycles), on the summary context line and as a `curve …` line in the per-loop view.
  A flat run renders one mid-height glyph per point (never a manufactured slope); a run
  longer than the width cap is bucket-averaged so the whole shape survives. The series
  is also exposed as `work_curve` on the `--json` surface for a dashboard's own render.
  Built from the env-measured `work` counts only — the `narrated` boast never enters it
  (docs/138).

## Litmus

- **Read-only projection.** `loop_trace` reads the verdict journal only — no lease, no
  mutation, mints no belief, adjudicates nothing new (the `observe`/`dispatch_top`
  row-3 discipline). Delete it and you lose the screen, not any verdict.
- **Pure where it can be.** The fold (`trajectories_from_events`) takes events + an
  injected `now` and returns data — no disk, no clock read inside — so the suite folds
  it without a file. Only the CLI boundary reads the journal.
- **Byte-clean (docs/138).** The trajectory is built from the kernel's verdict tokens
  and the env-authored evidence counts; the candidate's `narrated` is surfaced as
  *context only* and never enters any band or count.
- **Fail-soft.** A torn/missing journal yields an empty screen, never a crash — the
  observability-never-crashes-the-observed contract (`verdict_journal.record`).
- **Names no host.** The fold keys on `run_id`/`syscall`/`verdict` — generic kernel
  fields; it knows nothing of self-improve vs enforce-tune (both ride `syscall=improve`)
  beyond their distinct run-ids.

## Done condition

- `dos observe --loops` renders one row per RSI loop with its iteration count, metric
  high-water, breaker distance-to-escalation, and liveness band — pinned by
  `tests/test_loop_trace.py` over a synthetic `improve`-event stream.
- `dos observe --loops --run <RID>` renders that loop's full per-iteration curve.
- A loop that ESCALATEd reads as terminal (distance 0); a loop whose last iteration is
  old reads STALLED; both pinned.

## Related

- [docs/262](262_the-verdict-journal-observability-as-a-first-class-surface.md) — the verdict journal this folds.
- [docs/280](280_the-self-improving-work-loop-the-kernel-adjudicates-its-own-improvement.md) — the `improve` keep-gate whose verdicts are the witness stream.
- [docs/365](365_self-tuning-enforcement-policy-the-pep-feedback-loop.md) — `enforce-tune`, the "and similar" loop covered by the same fold.
- [docs/374](374_ten-x-stewards-one-invariant-per-steward.md) — the 10×-stewards thesis this serves: more verification *surfaced*, not just more loops.
