---
name: dos-supervise-loop
description: Keep a target population of worker dispatch-loops alive across a workspace's lane roster — the supervisor cadence (the init/PID-1 analogy for a fleet). Each tick reads the active lane taxonomy from `dos doctor --json`, asks the kernel for a spawn/reap/flag plan via `dos loop --target N --json`, launches one `/dos-dispatch-loop` per SPAWN, scavenges only STALLED leases, and SURFACES (never kills) a SPINNING worker. The spawn/reap/flag decision is the kernel's typed `supervise()` verdict, not inline prose — the supervisor only carries out the plan. Driven entirely by `dos` verbs + the workspace's `dos.toml`. The DOS reference supervisor workflow (SKP Axis 5).
---

# dos-supervise-loop — the generic worker-population supervisor

> **The init/PID-1 of a dispatch fleet.** It keeps `--target` worker
> dispatch-loops alive across the lane roster: each tick it counts live lane
> leases, classifies each worker's liveness, and fills the roster up to target by
> launching one `/dos-dispatch-loop` per free admissible lane. The *what to do*
> is a typed kernel verdict — `dos loop` emits a spawn/reap/flag plan; this skill
> only carries it out. It reaps a worker ONLY when the kernel says STALLED, never
> a healthy one, and it FLAGs a SPINNING worker to the operator rather than
> killing it (acting on a spin is not the supervisor's job).

The supervisor's whole contract is one rule the kernel owns: **the population is
filled from the plan, not from a guess.** A tick is `gather evidence → ask
`dos loop` → carry out spawn/reap/flag`. The four dispositions are the kernel's:

1. **SPAWN** — a free, admissible lane below target: launch one worker on it.
2. **REAP** — a STALLED lease: scavenge it so the lane is free to refill.
3. **HOLD** — an ADVANCING (or alive-counted SPINNING) worker: leave it alone.
4. **FLAG** — a SPINNING worker (advisory) or an excess over target: surface it,
   do not kill it.

## Inputs

- `--target <N>` (default 1) — the desired live-worker population. The kernel
  caps the achievable population at the *admissible* count (how many roster lanes
  can simultaneously hold a worker given their disjointness); a `--target` above
  that yields a TARGET_UNREACHABLE verdict naming the fix.
- `--max-concurrency <N>` (optional, docs/283) — the **derived-claim concurrency
  budget**. On a DYNAMIC-CLAIM workspace (`concurrent = []`, where a lane is a
  HANDLE whose disjointness is enforced per-pick at acquire time, not by a fixed
  tree), the static admissible count is 1, so a `--target` above 1 is structurally
  unreachable. Declaring this budget lets the supervisor keep up to N workers alive
  on a fungible auto-pick handle WITHOUT pre-enumerating N disjoint trees — the
  arbiter still narrows each worker's per-pick claim at its Step 0. Declare ONE
  number, not N trees. Set it standing in `dos.toml [supervise] max_concurrency`,
  or pass `--max-concurrency` for a one-off run. Off by default (admissible stays
  the static disjoint-lane count — byte-for-byte today's).
- `--interval <seconds>` (long default) — the wakeup cadence between ticks. A
  supervisor wakes rarely; it is a watchdog, not a busy-loop.
- `--max-ticks <N>` (optional) — stop after N ticks. Omit for an open-ended run
  that stops only on an operator interrupt.

## Step 0 — Read the taxonomy, compute the plan

```bash
dos doctor --workspace . --json
dos loop --workspace . --target N --json
```

The doctor report carries the active lane roster (the concurrent + exclusive
lanes and their trees) — **read it, never assume a lane name.** `dos loop` then
gathers the evidence (the live lane leases from the journal + each lease's
liveness) and returns the typed plan: a `verdict` (AT_TARGET / FILLING /
OVER_TARGET / TARGET_UNREACHABLE), the `alive`/`admissible`/`target` tally, and
the `spawn`/`reap`/`flag` lane lists. **This plan is the kernel's decision; the
remaining steps only enact it.** If the verdict is TARGET_UNREACHABLE, read its
reason (the roster cannot reach the number) and stop — see the anti-patterns.

## Step 1 — Launch one worker per SPAWN

For each lane in the plan's `spawn` list, launch a worker dispatch-loop focused
on that lane:

```
/dos-dispatch-loop --lane <LANE>
```

The worker takes its own lane lease via `dos arbitrate` at its Step 0 and
**journals its ACQUIRE early** — that early write is what shrinks the
double-spawn window: by the next tick the lease is visible in the journal, so
the supervisor counts it alive and does not launch a second worker on the same
lane. The supervisor itself never takes a lease; it only counts them and fills
the gap. Launch exactly the lanes the plan named — no more, no fewer.

## Step 2 — Per REAP scavenge, per FLAG surface

For each lane in the plan's `reap` list (a STALLED worker): release/scavenge its
lease so the lane is free to refill on the next tick. The worker is not making
progress by the kernel's temporal verdict, so its claim on the lane is the only
thing being reclaimed — the lane returns to the roster as a spawn candidate.

For each lane in the plan's `flag` list (a SPINNING worker, or an excess over
target): **surface it to the operator and move on.** Do NOT kill a SPINNING
worker and do NOT reap an excess healthy one — a flag is advisory. Acting on a
spin (deciding a busy-but-not-advancing worker should be stopped) is an open
question the supervisor deliberately leaves to a human; its job is to make the
spin visible, not to adjudicate it.

## Step 3 — Sleep, re-tick, stop on interrupt or --max-ticks

Sleep `--interval` seconds, then re-run from Step 0: re-read the taxonomy,
recompute the plan, enact it. Each tick is independent and idempotent — it
re-derives the whole plan from the current journal state, so a missed or extra
tick self-corrects. A lane launched within the last cooldown window is treated
as *pending* (alive-or-coming) for the next tick, so a slow ACQUIRE does not
trigger a re-spawn.

Stop when the operator interrupts the run, or when `--max-ticks` is reached.
There is no "all done" terminal state — a supervisor's job is to *stay up*; it
ends only on an explicit bound or an interrupt.

## What this skill deliberately does NOT do (no silent gap, `CLAUDE.md` heavy tier)

- **No auto-kill of a SPINNING worker.** A spin is FLAGged (advisory) and
  surfaced; the supervisor never terminates a busy worker. Acting on a spin is
  open research, deliberately left to the operator.
- **No reap of a healthy worker to hit a number.** A REAP fires ONLY on the
  kernel's STALLED verdict. An ADVANCING (or alive-counted SPINNING) worker is
  HELD even when the population is over target — the excess is FLAGged, not
  killed.
- **No hardcoded lane or trunk.** Every host fact — which lanes exist, their
  trees, the ship grammar — comes from `dos doctor --json` and the workspace's
  `dos.toml`. The supervisor names the generic roster the kernel reports, nothing
  else.
- **No value-aware spawn ranking.** It fills free admissible lanes in roster
  order; a yield-greedy "spawn the highest-value lane first" picker is a driver
  concern outside this loop.

## Worked example (live transcript)

> A supervisor tick, hand-run with real `dos` verbs against this workspace
> (`dos doctor` reports `is_kernel_repo: true`, 11 runtime files). The verdicts
> below are live-captured; the per-run digest is the antidote to re-reading state.

Step 0 — read the roster, then ask the kernel who is alive:

```bash
$ dos doctor --workspace . --json
# lanes.concurrent = [benchmark, docs, examples, scripts, spikes, src, tests]
# lanes.exclusive  = [global]   stamp.style = grep   overlap_policy.active = prefix
```

A worker's lease is what the supervisor counts — and the arbiter never double-books:

```bash
$ dos arbitrate --workspace . --lane src
{"auto_picked":true,"free_clusters":[],"lane":"benchmark","lane_kind":"cluster","outcome":"acquire","pick_count":null,"reason":"auto-picked free cluster lane benchmark (requested src was refused: lane src would edit the orchestrator's own running code … (SELF_MODIFY) …)."}
```

You asked for `src`; the admission conjunction refused the hint (here SELF_MODIFY —
`src/**` is the running kernel on its own repo; a lane contended by a live lease
redirects the same way), so the arbiter redirected to `benchmark` — exit `0`
(acquire), the real reason named in the parenthetical. A free, admissible lane
you name is granted directly.

Per run, fold ONE digest instead of re-reading state files (no `claimed` field):

```bash
$ dos status RUN_ID --json     # ILLUSTRATIVE shape — folds liveness+ledger+lease+resume
# {"liveness":"SPINNING", ...}   exit 3 (SPINNING) → this run is the FLAG candidate
```

`dos status`/`dos liveness` share the liveness exit codes — `0` ADVANCING (HOLD),
`3` SPINNING (FLAG, advisory), `4` STALLED (REAP). On SPINNING the supervisor records
an OP_HALT **proposal** and surfaces it; it never signals the process. A worker can
be paused for clean re-entry instead:

```bash
$ dos halt --resumable     # ILLUSTRATIVE — SUSPEND op; run-dir retained, dos resume continues it
```

## Anti-patterns

- ❌ Re-spawning a pending lane — a worker launched last tick whose ACQUIRE has
  not yet journalled is alive-or-coming; the kernel marks it pending and the plan
  omits it. Honor that; do not double-launch.
- ❌ Reaping an ADVANCING worker — only a STALLED lease is a REAP. A worker that
  is moving keeps its lane; trust the temporal verdict, do not second-guess it.
- ❌ Killing a SPINNING worker — it is a FLAG, not a REAP. Surface it; let the
  operator decide.
- ❌ Running the supervisor where the roster cannot reach `--target` — a
  TARGET_UNREACHABLE verdict means no further disjoint concurrent lanes exist to
  fill. Two honest fixes: (a) on a STATIC-tree roster, declare more disjoint
  concurrent lanes in `dos.toml [lanes]`; (b) on a DYNAMIC-CLAIM roster
  (`concurrent = []`, per-pick disjointness), declare a `--max-concurrency` budget
  (docs/283) so the supervisor rides a fungible auto-pick handle up to N workers —
  one number instead of N trees. Do NOT force the number by inventing fake disjoint
  trees a worker would over-claim.
