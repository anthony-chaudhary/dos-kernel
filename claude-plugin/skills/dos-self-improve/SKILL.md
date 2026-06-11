---
name: dos-self-improve
description: "Run a self-improving work loop where the kernel — not the agent's say-so — decides whether each candidate change actually improved the codebase. The propose→verify→measure→keep-or-revert cycle with one rule no prior auto-improver enforced: a candidate is KEPT only if a witness the candidate's author did not write CONFIRMS it improved (the test suite green on a clean worktree, the truth syscall clean, and a strictly-measured metric gain). Otherwise it is REVERTED. The keep/revert/escalate decision is the kernel's typed `improve` verdict (`dos improve`), not inline prose; a run of non-keeps trips a breaker that ESCALATEs to a human. Each candidate is applied in an ISOLATED git worktree so the kernel adjudicating it is never the kernel being rewritten. Driven entirely by `dos` verbs + the workspace's own `dos.toml` — no host-specific paths, lanes, or commit conventions. The DOS reference recursive-self-improvement loop (SKP Axis 5, docs/280)."
---

# dos-self-improve — the loop where DOS adjudicates its own improvement

> **The first self-improving work loop for DOS.** It proposes a change, checks it,
> measures it, and keeps it **only if a witness the change's author did not write
> confirms it improved** — the green suite on a clean worktree, the truth syscall,
> and a strictly-measured metric gain. Everything else is reverted. The keep
> decision is the kernel's `improve` verdict, not the loop's opinion of its own
> work. A run of candidates nothing accepts trips a breaker that hands the
> judgment back to a human.

This is the dispatch loop (`/dos-dispatch-loop`) **turned inward on a codebase's
own source**, with the one rule no prior auto-improver enforced:

> A self-improving loop's fatal failure mode is **grading its own homework** — the
> agent that wrote the change is the same one that says "yes, this is better," so it
> learns to *narrate* improvement instead of making it. `dos improve` closes that
> hole: the keep-bit is a pure function of facts the loop did not author, so the
> loop **cannot keep a change by claiming it is better.** The only path to KEEP is
> to actually move a metric the environment measures.

The recursive-self-improvement literature names verification as the gating
constraint ("requiring verification regimes enabling labs to confirm"; "human
judgment on which problems matter remains the bottleneck"). This loop *is* that
verification regime, with the human kept in the loop by construction — the breaker
escalates to a person exactly when the loop runs dry of witnessed improvements.

## What the kernel decides vs. what you do

| Step | Who | What |
|---|---|---|
| **Propose** one scoped change | YOU (a subagent) | the capable, *untrusted* step — the only place intelligence enters the loop |
| **Verify + measure** | `dos` verbs | run the suite, run the truth syscall, measure the metric — all on an isolated worktree |
| **Keep / revert / escalate** | `dos improve` | the kernel's typed verdict over the env-authored facts — NOT your opinion |
| **Merge / discard / escalate** | YOU | carry out the kernel's verdict |

The kernel contributes ZERO intelligence to the proposal — only the refusal to
keep an unwitnessed one. That asymmetry is the whole design.

## Inputs

- `--metric <name>` (required) — what "improvement" means for THIS workspace, as a
  non-negative integer the environment measures, higher = better. The reference
  metric is **`lint-clean` = a large constant minus the `dos lint` finding count**
  (driving dead policy to zero); other honest metrics: a passing-property-test
  count, a coverage percent, a negated wall-clock budget. The kernel does not know
  the unit — it only compares magnitudes (the `productivity`/`efficiency` work-unit
  split).
- `--max-cycles <N>` (default 5) — the backstop cap (the `ITERATION_CAP` analogue).
- `--max-reverts <N>` (default 3) — ESCALATE to a human after this many candidates
  in a row that nothing accepted (the breaker).
- `--lane <name>` (optional) — the lane to take; a bare loop auto-picks a free one.

## Step 0 — Pre-flight: take a lane, record the GREEN baseline

```bash
dos doctor --workspace . --json
dos arbitrate --workspace . --lane <LANE>
```

Then establish the baseline — and this gate is non-negotiable:

```bash
python -m pytest -q          # MUST be green to start
<measure the metric>         # e.g. dos lint --workspace . --json | (1000 - finding count)
```

**You cannot measure improvement from a red baseline.** If the suite is red, STOP
and surface it — fix the suite first (that itself is a separate, ordinary task,
not a self-improvement cycle). Record the baseline metric `B`.

## Step 1 — Per cycle: propose ONE candidate in an ISOLATED worktree

The candidate edit is the `SELF_MODIFY` / `global`-lane hazard
([[self-modification-hazard]]): editing the kernel's own running path is exactly
what the arbiter refuses, and what would let a candidate rewrite the kernel that is
adjudicating it. So work in an isolated git worktree, never the live tree:

```bash
git worktree add ../_si-candidate HEAD
```

Spawn ONE subagent with a tight brief, working in that worktree:

> Improve exactly ONE thing in this codebase that will move `<metric>`. Make the
> SMALLEST diff that moves it. Do not touch tests to make them pass; do not weaken
> an assertion. Commit your change with a clear subject. Return what you changed
> and why. **If you cannot find a real improvement, say so — do not invent one.**

One candidate per cycle keeps the witness attributable (the `commit-audit` "one
commit, one claim, one diff" discipline). If the subagent returns "nothing to
improve," the cycle is a **skip** — not a revert; move on.

## Step 2 — Gather the env-authored witnesses (on the worktree)

Every fact the kernel reads is measured by the ENVIRONMENT, never taken from the
subagent's word:

```bash
# (1) the suite, on the candidate-only worktree — the runner authors the bit
cd ../_si-candidate && python -m pytest -q ; SUITE=$?

# (2) the truth syscall — git ancestry, the oracle authors the bit
dos commit-audit --workspace ../_si-candidate HEAD      # claim vs its own diff
# (and, if the candidate claims a plan phase, dos verify it)

# (3) the metric, re-measured on the worktree AFTER the candidate
<measure the metric on ../_si-candidate>   # → W

# (4) the tokens the subagent spent (the provider usage record) → T
```

A missing witness is a FAILING witness — if you cannot run the suite or the truth
syscall, treat it as red/dirty (fail-safe). Never substitute the subagent's "tests
pass" claim for the runner's exit code.

## Step 3 — Ask the kernel: KEEP / REVERT / ESCALATE (the kernel decides)

This is the load-bearing step: **the decision is a kernel mechanism, not prose.**

```bash
dos improve --workspace . \
  $( [ "$SUITE" -eq 0 ] && echo --suite-passed ) \
  $( <truth clean> && echo --truth-clean ) \
  --work "$W" --baseline-work "$B" --tokens "$T" \
  --consecutive-reverts "$REVERTS" --max-reverts <N> \
  --narrated "<the subagent's description>" --json
```

The verdict IS the exit code — branch on it, do not re-read the prose:

- **`0` KEEP** — suite green, truth clean, `W > B` (a strict env-measured gain), and
  not wasteful. The improvement is *witnessed*. Go to Step 4-KEEP.
- **`3` REVERT** — either a REGRESSION (suite red / truth dirty — the non-negotiable
  floor) or a NO-OP (safe but `W` did not beat `B`). Go to Step 4-REVERT.
- **`4` ESCALATE** — the breaker is open (`--max-reverts` non-keeps in a row). Go to
  Step 4-ESCALATE and STOP.

The `--narrated` text is carried for the operator and **parsed for nothing** — it
cannot move REVERT → KEEP (docs/234). Do not try to argue a candidate into a keep;
make the metric move or revert it.

## Step 4 — Carry out the verdict

- **KEEP** — merge the worktree commit onto the lane, then RAISE the baseline to
  `W` (the ratchet — the next candidate must beat the *improved* tree, not the
  original) and reset the revert count to 0:
  ```bash
  git merge --ff-only <candidate-sha>   # or cherry-pick onto the lane
  B=$W ; REVERTS=0
  ```
- **REVERT** — discard the worktree candidate; the live tree is untouched. Bump the
  revert count (`REVERTS=$((REVERTS+1))`). The baseline is unchanged.
  ```bash
  git worktree remove --force ../_si-candidate
  ```
- **ESCALATE** — discard the candidate, then file a human decision and STOP the
  loop (the loop has run dry of witnessed improvements; a person decides what
  matters next):
  ```bash
  dos decisions ...   # surface the escalation with the kernel's reason
  ```

Then re-create a fresh worktree for the next cycle (off the now-possibly-advanced
lane HEAD) and return to Step 1, until KEEP/REVERT cycles reach `--max-cycles` or
an ESCALATE stops the loop.

## Step 5 — Archive + release

When the loop stops, write a loop record under `paths.runs` (the per-cycle
verdicts, the kept/reverted/skipped counts, the final baseline — the high-water
mark that measures how much the loop improved the codebase, and the stop reason),
and release the lane lease. Commit any kept improvements with a generic subject
read from config — no hardcoded prefix.

## Why this is the honest version of recursive self-improvement

- The keep-bit reads **four env-authored facts** — the suite exit, the truth
  syscall, the metric before, the metric after — and **zero loop-authored bytes**.
  A loop that learns to write "great improvement" in every commit gains exactly
  zero keep-probability, because the claim is not in the decision.
- The loop's default action on an unwitnessed candidate is **undo**, not keep — the
  abstention-first discipline at loop scale.
- The breaker **escalates to a human** precisely when the loop cannot find a
  witnessed improvement — the RSI literature's irreducible bottleneck, made a
  kernel rule rather than a hope.

## What this skill deliberately does NOT do (no silent gap)

- **No open-ended autonomy.** The bound is the breaker + the cap; the loop
  terminates into a human decision, by construction. It does not run unbounded.
- **No metric the loop can game.** The metric must be measured by the environment
  on the worktree, not reported by the subagent. A metric the subagent computes
  about itself is back to grading its own homework — `log` and refuse such a metric.
- **No test-weakening.** A candidate that makes the suite pass by deleting an
  assertion REGRESSES the truth syscall / will be caught by a metric that counts
  real coverage; the brief forbids it and the green-suite floor is on the FULL
  suite, not a subset.

## Anti-patterns

- ❌ Believing the subagent's "it's better" — re-measure every fact; only `dos
  improve` over env-authored witnesses decides KEEP.
- ❌ Editing the live tree — always work in an isolated worktree; the kernel
  adjudicating a candidate must not be the kernel being rewritten by it.
- ❌ Measuring improvement from a red baseline — fix the suite first; you cannot
  measure a gain you cannot see.
- ❌ Re-running a candidate the kernel reverted with a better-sounding commit
  message — the message is not in the decision; make the metric move.
