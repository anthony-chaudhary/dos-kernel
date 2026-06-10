---
name: dos-replan-loop
description: Run /dos-replan on a fixed cadence for a bounded number of iterations, then stop — an unattended planning-refresh sweep. A thin recurring wrapper over /dos-replan plus an optional guarded release; the release guard reads the workspace's trunk from config rather than assuming a branch name. Driven by `dos` verbs + the workspace's `dos.toml`. The DOS reference planning-loop workflow (SKP Axis 5).
---

# dos-replan-loop — the generic recurring planning sweep

> **The thin loop.** It is mostly sequencing: run `/dos-replan`, run an
> optional auto-commit/release contract, increment a counter, schedule the next
> wakeup, and stop at the iteration cap. The one thing it must get right
> generically is the **release guard** — it reads the workspace's trunk from
> config, not a hardcoded branch name. (This repo's trunk is `master`, not
> `main` — exactly the kind of host fact the guard must read, not assume.)

## Inputs

- `--interval <seconds>` (optional, default 600 — 10 minutes).
- `--max-iterations <N>` (optional, default 20).

## Step 1 — First entry

Set the iteration counter to 1. Record the pre-existing dirty state of the tree
(so the auto-commit guard can tell what *this* sweep changed). Invoke the
`/dos-replan` Skill.

## Step 2 — After `/dos-replan` finishes: the guarded release contract

If `/dos-replan` made garden-only writes, optionally commit + release them — but
only behind the guard. **The guard reads the trunk from config, not a literal:**

```bash
dos doctor --workspace . --json
```

There is no `trunk` field in the doctor report today (it is a host fact); resolve
it the generic way: `git symbolic-ref --short refs/remotes/origin/HEAD` (the
remote's default branch). **Fail closed:** if that cannot be resolved (no
`origin/HEAD` — common in fresh clones / CI checkouts), treat the trunk as
**UNKNOWN** and **skip the release entirely** (record the sweep only). Do NOT fall
back to "the current branch" — that would make the on-trunk check below trivially
true on any branch and let an auto-commit proceed off-trunk. Then the guard fires
only when ALL hold:

- **the trunk is positively known** (resolved above, not UNKNOWN), AND **HEAD is
  on it** (the resolved default branch — `master` here, `main` elsewhere;
  **never hardcode either**).
- the working-tree changes are docs/state-only (the replan queue + cooldown
  state, nothing in code).
- there is something to commit (a productive sweep).

If the guard passes, commit the garden writes with a generic subject and (if the
host wants it) call `/release`. If any condition fails — including an UNKNOWN
trunk — skip the release and just record the sweep — do not push code, do not
commit off-trunk.

Increment the counter; if it is below `--max-iterations`, schedule the next
wakeup `--interval` seconds out; else stop.

## Step 3 — On wakeup re-entry

Parse the counter from the prior iteration, run `/dos-replan`, and re-run Step 2
again. Stop when the counter reaches `--max-iterations`.

## What this skill deliberately does NOT do (no silent gap)

- **No hardcoded trunk.** It resolves the default branch from git, so a `master`
  trunk and a `main` trunk are both handled — the SKP Phase 4 litmus.
- **No host release ceremony.** The generic loop commits garden writes and
  optionally calls `/release`; it does not build artifacts or run a host's
  bespoke promotion gate (those are host/dev tooling, outside this loop).

## Worked example (live transcript)

> **One replan cycle.** The loop re-runs `dos verify` over the portfolio each
> cadence, then lets `dos gate` decide DRAIN-vs-continue by **exit code** — the
> typed verdict, never the prose. Read the **rung** (`source`) and the **code**,
> not the headline.

```bash
$ dos doctor --workspace . --json | python -c "import sys,json;d=json.load(sys.stdin);print(d['paths']['plans_glob'])"
docs/**/*-plan.md
```
The WCR on-ramp: the portfolio is whatever matches `plans_glob` — host fact, read from config, not assumed.

```bash
$ dos verify --workspace . docs/82_liveness-oracle-plan liveness --json
{"phase":"liveness","plan":"docs/82_liveness-oracle-plan","rung":"direct","sha":"80d4f30","shipped":true,"source":"grep-subject","summary":"80d4f30 liveness: exclude the BIRTH acquire from the ADVANCING event count"}
```
SHIPPED via the **grep-subject** rung (exit 0) — a commit SUBJECT carrying the phase token flips it green; read the rung, not the bare verdict.

```bash
$ dos verify --workspace . docs/99_runtime-validation-and-the-actuation-boundary halt --json
{"phase":"halt","plan":"docs/99_runtime-validation-and-the-actuation-boundary","shipped":false,"source":"none"}
```
NOT_SHIPPED via the **none** rung (exit 1) — git ancestry has not stamped it; this phase is still in flight.

```bash
$ dos gate dispositions.json ; echo "exit=$?"
exit=3
```
`dos gate` returns the typed exit code: **3 = DRAIN** (LIVE=0, DRAIN=3, STALE-STAMP=4, BLOCKED=5, RACE=6). Exit 3 means stop taking new work and let the in-flight phases settle this cycle — continue (LIVE=0) otherwise.

```bash
$ dos arbitrate --workspace . --lane src
{"auto_picked":true,"free_clusters":[],"lane":"benchmark","lane_kind":"cluster","outcome":"acquire","pick_count":null,"reason":"auto-picked free cluster lane benchmark (requested src was refused: lane src would edit the orchestrator's own running code … (SELF_MODIFY) …).","tree":["benchmark/**"]}
```
You asked for `src`; the arbiter handed back `benchmark` (exit 0, `outcome:acquire`) — the admission conjunction refused the hint (here SELF_MODIFY; a lane contended by a live lease redirects the same way) and the kernel named the real reason rather than double-book or false-narrate. A free, admissible lane you name is granted directly.

## Anti-patterns

- ❌ Assuming the trunk is `main` — resolve it; this repo's is `master`.
- ❌ Committing off-trunk or committing code changes — the guard is docs/state +
  on-trunk only.
- ❌ Looping unbounded — the iteration cap is the stop; honor it.
