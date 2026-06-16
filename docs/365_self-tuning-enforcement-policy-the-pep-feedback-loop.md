# docs/365 — Self-tuning enforcement policy: the PEP-feedback loop

> **DOS is a sound PDP with no feedback loop from the PEP.** The kernel *decides* an
> intervention verdict; a host consumer *acts*; the act is journaled. Nothing fed
> *what the act turned out to be* back into the policy that drove it. This is the
> loop that closes that gap — and it tunes the enforcement policy **autonomously**,
> keeping only the edits a witness the loop did not author confirms.

## The gap (docs/189 §A1, restated)

DOS hardened the *verdict* (the ORACLE→JUDGE→HUMAN ladder, the forgeability axiom)
and the *actuation* (docs/143 — the intervention ladder, confidence-gated, the −9pp
disruption lesson). What it never had is the third leg: **a loop from the enforcement
OUTCOME back to the enforcement POLICY.** The intervention policy
(`InterventionPolicy`: `on_high_confidence` / `on_low_confidence` / `floor` /
`ceiling`, and the `InterventionLadder` ranks) is set once in `dos.toml` and never
learns from whether its denials were right.

The cost is visible in DOS's own journal. `dos enforce-outcomes` over this repo's WAL
finds ~19 distinct targets the SELF_MODIFY guard acted on, with hundreds of *excess*
denies (storms) and a handful the operator later **overrode** — i.e. the guard
refused an edit that was, in hindsight, legitimate. Those overrides are the
ground-truth signal nothing was using.

## The two ground-truth signals (both already on disk)

Every enforcement act is an `OP_ENFORCE` record (`lane_journal.enforce_entry`)
carrying the rung, the target, the reason class, and `proposal.decision`
(`deny` / `override-admit`). From these:

- **FALSE-DENY** — a `deny` for a target LATER followed by an `override-admit` for the
  same target. The operator armed the override window and the refused edit went
  through ⇒ the refusal was too aggressive. **The override is almost always by a
  DIFFERENT holder than the denied agent** (the operator corrects the agent), so the
  fold keys on **target**, not `(holder, target)` — keying on the pair would split the
  deny from its correction and miss the signal entirely. This is the load-bearing key
  choice; `enforce_outcomes.fold_enforce_outcomes` documents and tests it.
- **HELD catch** — a `deny` with no later override. The refusal stood.

These are env-authored: the operator's armed window writes the override, git/the WAL
writes the record. The loop authors none of it.

## The honest metric (reuse docs/143 §13.2)

The loop's improvement metric is `intervention_eval.score(policy, cases, ladder)
.net_task_delta` — the SAME scorer `dos intervention-eval` already uses, which scores
a policy by its **net task delta** (and reports `wasted_disruption_rate` = false-DENY
and `coverage` = catches held). The `cases` are env-authored from two sources: a
frozen labelled corpus (the docs/143 baseline) ∪ the live `OP_ENFORCE`-derived
outcomes. Because the metric is computed **by the kernel from cases the loop did not
author**, the loop cannot keep a policy edit by claiming it is better — the
`improve.classify` / docs/234 invariant, aimed at the enforcement policy. The only
path to KEEP is to actually move `net_task_delta`.

## The loop (reuse docs/280)

`drivers.enforce_tune` is `drivers.self_improve` pointed at the enforcement policy. It
reuses `self_improve.run_loop` verbatim — propose → gather → classify → actuate, the
worktree isolation, the ratchet, the breaker-to-human escalation — supplying only the
four callbacks and the metric. The keep-decision is `improve.classify`: KEEP only on
suite-green ∧ truth-clean ∧ strictly-higher kernel-measured `net_task_delta`.

```
propose  (untrusted agent)  → ONE policy-knob edit in an isolated worktree
gather   (env-authored)     → suite exit · commit-audit · net_task_delta of the candidate policy
classify (improve.classify) → KEEP / REVERT / ESCALATE   ← the non-forgeable keep-bit
actuate  (driver)           → merge / discard / file a dos decisions escalation
```

## Autonomous apply — and why it is safe by construction

The operator chose a **fully autonomous loop**: a cadence fires `dos enforce-tune` per
cycle and, reading its exit code, auto-merges a KEEP — no human in the merge path for a
witnessed improvement. The autonomy is safe not by a gate but by three structural
properties:

1. **The keep-bit is non-forgeable.** `improve.classify` KEEPs only on a
   kernel-measured strict gain; a no-op or regression auto-REVERTs. The loop cannot
   talk its way to a merge.
2. **The breaker still escalates to a human.** `--max-reverts` consecutive non-keeps
   ESCALATE and stop the loop, filing a `dos decisions` row — the RSI literature's
   irreducible "human-judgment-on-which-problems-matter" seed, pulled in exactly when
   the loop runs dry.
3. **The runtime-logic rail.** Auto-apply is scoped to enforcement-POLICY KNOBS
   (`[intervention]` / `[intervention_policy]` / `[improve]` values + ladder ranks). A
   candidate whose diff touched enforcement *logic*
   (`self_modify._DISPATCH_RUNTIME_FILES` — `arbiter.py`, `admission.py`,
   `self_modify.py`, …) is auto-REVERTed **regardless of its metric**
   (`enforce_tune.candidate_touches_runtime` forces truth=dirty). The loop owns the
   knobs; the SELF_MODIFY guard still owns the logic blast radius. A policy tuner that
   rewrote the arbiter to make a number go up is the self-modification hazard DOS
   refuses — so it is refused here too, before merge.

## Continuous observation (the always-on half)

`dos pulse` gains an observe leg: each tick folds the OP_ENFORCE journal
(`enforce_outcomes.outcome_metric`) and, when the false-DENY count crosses a threshold
(default 3), surfaces ONE WARN line ("enforcement over-blocking; run
`dos enforce-tune`"). Pulse is fail-soft and SILENT-when-clear, so an operator
watching the heartbeat sees what the autonomous loop acts on without any new noise
when enforcement is behaving.

## What ships

| Piece | File |
|---|---|
| Pure fold leaf | `src/dos/enforce_outcomes.py` (stdlib + `lane_journal` only) |
| Driver (the loop) | `src/dos/drivers/enforce_tune.py` |
| CLI verbs | `dos enforce-tune` (per-cycle keep-gate) / `dos enforce-outcomes` (read-only ledger) in `src/dos/cli.py` |
| Pulse observe leg | `src/dos/pulse.py` |
| Skill (the runbook) | `src/dos/skills/dos-enforce-tune/SKILL.md` |

## Litmus

- `enforce_outcomes.py` imports only stdlib + `lane_journal` (kernel-clean — no
  `decisions` import; the three OP_ENFORCE readers are re-implemented locally and
  pinned in lockstep with `decisions` by a test).
- The driver is the only I/O home (it reuses the `self_improve` engine; the metric is
  a pure `intervention_eval.score`).
- The shipped skill names no host (`dos doctor --json` / `dos.toml` for specifics).
- The metric is kernel-measured from env-authored cases; the proposing agent's
  narration is carried and parsed for nothing.

## Status

Built and tested. The fold, the driver, the two verbs, and the pulse leg are green on
the kernel suite. The autonomous cadence (a runner firing `dos enforce-tune` per cycle
and merging on exit 0) is operator-side wiring documented in the skill — a durable cron
/ Windows Task via a gitignored `.dos/` runner, the `dos pulse` runner sibling pattern
— not a tracked file.
