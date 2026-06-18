---
name: dos-enforce-tune
description: "Run the DOS enforcement-policy self-tuning loop. Use when Codex should tune `[intervention_policy]`, `[intervention]`, or `[improve]` knobs from observed false-deny vs held-catch outcomes with `dos enforce-tune`, measuring candidates in an isolated worktree and keeping only kernel-witnessed improvements: suite green, truth clean, strict net_task_delta gain, and no runtime-logic edits. Driven by `dos` verbs and workspace `dos.toml`; escalate after repeated non-keeps. (docs/365)."
---

# dos-enforce-tune — the loop where DOS tunes its OWN enforcement, from outcomes

> **DOS is a sound PDP with no feedback from the PEP.** The kernel decides an
> intervention verdict, a host acts on it, the act is journaled — but nothing fed
> *whether the act was right* back into the policy that drove it. This loop closes
> that. It learns the enforcement thresholds from the journal's own ground truth:
> a deny the operator **later overrode** is a false-DENY (too aggressive); a deny
> that stood is a held catch. The loop tunes the policy to drive false-DENIES down
> while holding the catches — and keeps an edit **only if the kernel, not the
> agent, measures that it helped.**

This is [[dos-self-improve]] pointed at the enforcement policy, with one twist: the
metric is not a generic count, it is the docs/143 `net_task_delta` of the policy over
labelled cases the loop did not author.

> A self-tuner's fatal failure mode is grading its own homework — relabelling its
> outcomes so its policy edit looks good. `dos enforce-tune` closes that hole the same
> way `dos improve` does: the metric is computed BY THE KERNEL from cases the loop did
> not author (a frozen corpus ∪ the live enforcement journal). The loop **cannot keep
> a policy edit by claiming it is better.** The only path to KEEP is to actually move
> `net_task_delta`.

## What the kernel decides vs. what you do

| Step | Who | What |
|---|---|---|
| **Read the outcomes** | `dos enforce-outcomes` | the live false-DENY / held-catch ledger — what to tune toward |
| **Propose** ONE policy-knob edit | YOU (a subagent) | the untrusted step — edit `[intervention_policy]` / `[intervention]` ranks / `[improve]` in a worktree |
| **Measure** | `dos enforce-tune` | score the candidate policy's `net_task_delta` over the corpus, on the worktree |
| **Keep / revert / escalate** | `dos enforce-tune` (rides `dos improve`) | the kernel's typed verdict over env-authored facts — NOT your opinion |
| **Merge / discard / escalate** | YOU (or the autonomous cadence) | carry out the kernel's verdict |

## Inputs

- `--cases <corpus.jsonl>` (required) — the labelled `InterventionCase` corpus the
  candidate policy is scored over (the docs/143 §13.2 ground truth). The reference
  corpus is the benchmark's `intervention_cases.jsonl`.
- the autonomous cadence (below) reads the `dos enforce-tune` EXIT CODE and merges on
  a KEEP. Run interactively WITHOUT auto-merge to inspect the verdict first (the safe
  default); let the cadence auto-merge once you trust it. Either way the verdict is the
  kernel's, and the runtime-logic rail still reverts a candidate that edited
  adjudication logic.
- `--max-cycles <N>` (default 5) — the backstop cap.
- `--max-reverts <N>` (default 3) — ESCALATE to a human after this many non-keeps in a
  row (the breaker).
- `--lane <name>` (optional) — the lane to take; a bare loop auto-picks a free one.

## Step 0 — Read what enforcement is getting wrong

```bash
dos enforce-outcomes --workspace . --json
```

This folds the `OP_ENFORCE` journal into the false-DENY / held-catch ledger. A
standing `false_denies` count is the signal: the policy is refusing edits the
workspace genuinely makes. (The `dos pulse` heartbeat surfaces the same count when it
crosses the threshold, so an always-on operator sees it without asking.) Note the
targets being over-blocked — they tell you which way to tune (usually: loosen a rung
or raise a confidence bar so a marginal mint earns WARN, not BLOCK).

## Step 1 — Pre-flight: take a lane, record the GREEN baseline net_task_delta

```bash
dos doctor --workspace . --json
dos arbitrate --workspace . --lane <LANE>
RID=$(dos run-id mint dos-enforce-tune \
      | python -c "import sys,json;print(json.load(sys.stdin)['run_id'])")
python -m pytest -q                      # MUST be green to start
dos enforce-tune --cases <corpus> --suite-passed --truth-clean \
  --baseline-work 0 --json --workspace . # read measured_work = the BASELINE policy's score
```

Record the baseline `measured_work` `B`. **You cannot measure improvement from a red
baseline** — if the suite is red, fix it first (an ordinary task, not a tuning cycle).
`RID` is the loop's correlation run-id; pass it with `--observe` on every
`dos enforce-tune` call so `dos observe --loops` can fold this always-on tuner's
trajectory while it runs (docs/383) — otherwise a long-lived cadence is invisible
between its commits.

## Step 2 — Per cycle: propose ONE policy-knob edit in an ISOLATED worktree

The enforcement policy is the kernel's own config — editing it on the live tree is the
SELF_MODIFY hazard ([[self-modification-hazard]]). Work in an isolated worktree:

```bash
git worktree add ../_et-candidate HEAD
```

Spawn ONE subagent with a tight brief, working in that worktree:

> Improve DOS's enforcement policy to reduce false-DENIES without losing catches.
> Edit ONLY the policy KNOBS: the `[intervention_policy]` confidence-gating values
> (`on_high_confidence` / `on_low_confidence` / `floor` / `ceiling`), the
> `[intervention]` ladder ranks, or the `[improve]` thresholds — in `dos.toml`. Make
> the SMALLEST edit that should move `net_task_delta`. Do NOT touch enforcement LOGIC
> (`self_modify.py`, `admission.py`, `arbiter.py`, the ladder code) — that edit will
> be reverted regardless of its metric. Commit your change. Return what you changed
> and why. If you cannot find a real improvement, say so — do not invent one.

## Step 3 — Measure + decide (the kernel decides)

Gather the env-authored witnesses on the worktree, then ask the kernel:

```bash
cd ../_et-candidate && python -m pytest -q ; SUITE=$?
CHANGED=$(git diff --name-only HEAD~1 HEAD)   # the candidate's diff — for the rail

dos enforce-tune --cases <corpus> \
  --policy-toml ../_et-candidate/dos.toml \
  --baseline-work "$B" \
  $( [ "$SUITE" -eq 0 ] && echo --suite-passed ) \
  --truth-clean \
  --changed-files $CHANGED \
  --max-reverts <N> \
  --observe --run-id "$RID" --subject "cycle-$CYCLE" --json --workspace .
```

The `--observe --run-id "$RID" --subject "cycle-$CYCLE"` line records the cycle to the
verdict journal so `dos observe --loops` folds this loop's trajectory live (docs/383).

`dos enforce-tune` re-scores the CANDIDATE policy (loaded from its worktree dos.toml),
applies the runtime-logic rail to `--changed-files`, and rides `dos improve`. The
verdict IS the exit code:

- **`0` KEEP** — suite green, truth clean, candidate `net_task_delta` strictly beat
  `B`, no runtime-logic edit. Witnessed. Go to Step 4-KEEP.
- **`3` REVERT** — a regression (suite red / a runtime-logic edit / truth dirty) or a
  no-op (the metric did not beat `B`). Go to Step 4-REVERT.
- **`4` ESCALATE** — the breaker is open (`--max-reverts` non-keeps in a row). STOP.

## Step 4 — Carry out the verdict

- **KEEP** — merge the worktree commit onto the lane, RAISE the baseline to the
  candidate's `measured_work` (the ratchet), reset the revert count. The autonomous
  cadence does this on exit 0; by hand: `git merge --ff-only <sha>` then
  `B=<measured_work>`.
- **REVERT** — discard the worktree candidate; the live policy is untouched. Bump the
  revert count. `git worktree remove --force ../_et-candidate`.
- **ESCALATE** — discard, file a `dos decisions` entry with the kernel's reason, STOP.
  The loop has run dry of witnessed improvements; a person decides what matters next.

Re-create a fresh worktree off the (possibly advanced) lane HEAD and return to Step 2
until `--max-cycles` or an ESCALATE.

## Always-on: the autonomous cadence

To run continuously, wire a durable scheduler (a cron / Windows Task via a gitignored
`.dos/` runner — the `dos pulse` runner sibling pattern). The runner does ONE bounded
cycle per firing and merges on a KEEP by reading the exit code:

```bash
# per firing, in an isolated worktree the runner created off the lane HEAD:
dos enforce-tune --cases <corpus> --policy-toml <wt>/dos.toml \
  --baseline-work "$B" --suite-passed --truth-clean \
  --changed-files $(git -C <wt> diff --name-only HEAD~1 HEAD) \
  --observe --run-id "$RID" --subject "cycle-$N" --workspace .
case $? in
  0) git merge --ff-only <candidate-sha> ;;   # KEEP — auto-merge + ratchet B
  4) dos decisions ... ; exit 0 ;;            # ESCALATE — surface to a human, stop
  *) : ;;                                      # REVERT — discard, leave the policy
esac
```

The cadence keeps ONE `$RID` across all its firings (persist it in the runner's `.dos/`
state), so every cycle folds into the SAME trajectory — an always-on tuner that would
otherwise be invisible between commits becomes a live curve in `dos observe --loops`.

Each firing is bounded (one cycle; over many firings the breaker + cap still hold),
so it always terminates — never an unbounded run. `dos pulse` keeps its observe leg,
so the heartbeat surfaces the false-DENY rate the loop acts on.

**Watch the tuner live:** `dos observe --loops` (every loop) or
`dos observe --loops --run "$RID"` (this tuner's full per-cycle curve) — the metric
high-water, the `breaker N/max` distance-to-escalate, and the ACTIVE/STALLED/ESCALATED
band, all folded from the kernel's own verdicts (docs/383).

## Why the autonomous merge is safe

- **The keep-bit is non-forgeable.** `dos enforce-tune` KEEPs only on a
  kernel-measured strict `net_task_delta` gain with the suite green and truth clean. A
  no-op or regression auto-REVERTs — the loop cannot narrate its way to a merge.
- **The breaker escalates to a human** exactly when the loop runs dry — the
  human-in-the-loop seed, preserved even when fully autonomous.
- **The runtime-logic rail** reverts ANY candidate whose diff touched the kernel's
  adjudication logic (`self_modify._DISPATCH_RUNTIME_FILES`), regardless of its
  metric. The loop owns the policy KNOBS; the SELF_MODIFY guard still owns the LOGIC
  blast radius. A tuner that rewrote the arbiter to make a number go up is the
  self-modification hazard DOS refuses — refused here too, before merge.

## What this skill deliberately does NOT do (no silent gap)

- **No logic edits.** The loop tunes thresholds/ranks, never the enforcement code. A
  logic edit is auto-reverted; the brief forbids it.
- **No metric the loop can game.** `net_task_delta` is computed by the kernel from
  cases the loop did not author (the frozen corpus ∪ the live journal). A metric the
  subagent computes about itself is back to grading its own homework — refuse it.
- **No unbounded run.** Every firing terminates into the cap or a human ESCALATE.

## Anti-patterns

- ❌ Believing the subagent's "it's better" — re-measure via `dos enforce-tune`; only
  the kernel verdict decides KEEP.
- ❌ Editing enforcement LOGIC to move the metric — the runtime-logic rail reverts it.
- ❌ Tuning from a red baseline — fix the suite first; you cannot measure a gain you
  cannot see.
- ❌ Loosening the policy until false-DENIES hit zero — that tanks `coverage` (catches
  held), which `net_task_delta` penalizes; the kernel will REVERT an over-loosened
  candidate because the metric balances both.
