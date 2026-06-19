# 389 - Always-on fleet loop hygiene

> **Goal:** make the "always-on" loop stack reusable, not a pile of repo-local
> runbooks. A long-lived fleet needs three separate floors: a truthful dispatcher,
> an operator surface that names the next view, and a janitor that keeps the host
> from slowly filling with leaked work.

## What is working

- `dos loop` / `supervise()` already owns the population decision: spawn, hold,
  flag, or reap is a typed verdict over lane leases and liveness, not prose.
- `dos pulse` and `dos notify` give an operator-facing surface for decisions,
  live lanes, and heartbeat freshness. This slice added a structured
  `NotifyAction` so a notice also carries the read-only view to open.
- `scripts/cleanup_sweep.py` is the right host-local home for safe recurring
  chores. It shells existing verbs, records each step, and is dry-run by default.
- The reference userland app proved one RSI lesson that should stay generic:
  routing findings is not enough. Every acceleration loop needs a consumption
  check, or the loop can look busy while closure stays low.

## What was not working yet

- Notifications named the problem but not the next view. A human still had to
  remember whether to open `dos decisions`, `dos top`, or `dos pulse`.
- A hot local fleet can leak subprocesses after a parent dies. Two real leak
  classes are generic enough to automate conservatively: orphaned whole-disk
  scanners and resident-memory outliers.
- The cleanup sweep had no process hygiene step, so always-on loops could be
  truthfully supervised while the machine still degraded underneath them.

## New reusable pieces

1. `NotifyAction` on `Notification`
   - Maps `decisions`, `top`, and `pulse` to read-only `dos` commands.
   - Renders as plain terminal text, OSC 8 terminal links, Slack link buttons
     when a URL exists, and a webhook `action` object.
   - Never enacts a stop or lease change; it only opens the relevant view.

2. `scripts/proc_reaper.py`
   - Pure classifier: `ProcRecord` to `KEEP` / `REAP` / `REFUSE`.
   - Dry-run default; `--apply` kills only `REAP` rows.
   - Reaps only two waste classes: dead-parent root scans past a CPU floor, and
     processes over a hard RSS cap.
   - Protected fleet processes always `KEEP`, even if heavy. A live-parent scan
     is `REFUSE` and surfaced, not killed.

3. `cleanup_sweep` integration
   - Adds `proc_reaper` as a fail-soft step.
   - Missing `psutil` is a recorded skip, not a failed sweep.
   - A failed kill under `--apply` makes the sweep not-ok so a scheduler can
     page an operator.

## How the fleet repo should consume this

- Keep `dos.toml` lanes as the source of concurrency truth; do not copy
  userland lane names.
- Declare local `[heartbeats.jobs]` only where the scheduler and checker share a
  filesystem. Ephemeral CI runners should not declare persistent local beats.
- Run `python scripts/cleanup_sweep.py --workspace <repo> --json` on the same
  host that runs the local fleet. Promote to `--apply` only after dry-run output
  is boring.
- Tune `proc_reaper` thresholds per machine. Keep-patterns are policy; the
  classifier floor stays the same.
- Pair every future `*-improve --route` style accelerator with a closure or
  consumption check before calling it productive.

## Verification from this slice

- `python scripts/proc_reaper.py --json` on the current host: no `REAP` or
  `REFUSE` rows.
- `python scripts/cleanup_sweep.py --workspace . --json`: the new
  `proc_reaper` step ran and reported `would reap 0 runaway process(es), 0
  surfaced`.
- Focused tests: notification renderers, cleanup sweep, and process reaper are
  green.
