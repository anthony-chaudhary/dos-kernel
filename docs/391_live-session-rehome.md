# 391 — Live-session rehome: rotating a seat BEFORE it walls, not after it dies

> **Status:** landed. The `dos verify` horizon for this phase is the git ancestry of the
> commit that lands it, not this sentence.

## The ask

The operator runs a fleet of Claude Code sessions, each pinned to a different account.
docs/386 §4 made rotation work **reactively**: when a session dies on a rate-limit wall
(`StopFailure`), the breaker backs off, a `RotationHandoff` is written, and the next
`SessionStart` relaunches the *dead* session under a serving seat. That heals a session
that already fell over.

It does nothing for a session that is **still running and merely approaching its cap** —
the one you actually want caught *before* it walls. The ask: extend rotation from "session
start + the fail-recovery of resume" into a **live switchover** — if a running session's
account is about to (or does) hit a limit, rehome it; ideally in the same window, else
print a continuation link. Headless first, UI second.

## The hard constraint (why "same process, live" is impossible — docs/380)

A live process's environment cannot be mutated from outside it, and a 429 does **not** make
the CLI re-read its credentials file. So a genuinely same-process account swap of a
*running* session cannot happen without a relaunch — there is no honest hot-swap. This is
the named residual docs/386 §4 left open, and it is a property of the harness, not a gap we
can close in the kernel.

What we **can** do — and what "live switchover" honestly means here — is two-fold:

1. **Compute + record the rotation the instant the live session crosses near-cap**, instead
   of waiting for it to die. The relaunch is then *ready before the wall*.
2. **Actuate at the earliest boundary**: headless, a supervisor relaunches the child under
   the new seat resuming the conversation (seamless); interactive, surface a one-line
   `claude --resume` continuation the operator runs same-window.

So the seam is the same two-phase handoff docs/386 §4 built — this phase just makes the
WRITE half fire **proactively, on a live boundary**, not only reactively on a death.

## The four pieces

### 1. `live_rotation.py` — the PURE verdict (the brain)

`liveness` / `breaker`'s shape — a pure fold over already-gathered state — for a new
question: *given the seat a live session is on (SERVING / NEAR_CAP / WALLED) and the other
seats available, should it HOLD, ROTATE to a named serving seat, or WAIT?* No I/O, no clock
(injected `now_epoch`), no driver import (the kind-strings MIRROR
`account_switcher.ACCT_*`, pinned equal by a litmus test — the `provider_limit` discipline).

The DoS-relevant pure piece is **`plan_fleet` / `spread_targets`** — the **thundering-herd
defence**. When N sessions cross near-cap together, each deciding alone would pick the *same*
lowest-roster alternate and instantly wall it (one wall becomes two). `spread_targets` fans
the herd across the distinct serving alternates by remaining headroom (the `allocate_seats`
largest-remainder apportionment), so the rotation can't stampede its own target. Pure and
deterministic → replay-testable, and the stress harness pins "no alternate absorbs more than
its fair share."

### 2. `usage_probe.py` — the live near-cap SIGNAL (the eyes)

The kernel is meter-blind; this is the boundary that supplies the meter for the one seat a
hook knows intimately — the seat the firing session runs on. The harness writes no
utilization/reset on disk (docs/380, re-confirmed against a real transcript), so it reads the
two weaker-but-real signals that *are* there, both keyed by the live session:

- **Breaker pressure** — `.dos/stop-failures/<sid>.json`: a session that has already taken
  (and recovered from) API walls this run is struggling; rotate before the next sticks.
- **Token budget** — the transcript's per-turn `usage` summed against a host-supplied window
  cap. **Opt-in**: with no cap there is no ceiling to divide by, so it is skipped rather than
  fabricating a fraction (never invent a wall the host did not declare).

Output is a `ProbeSnapshot` satisfying `account_switcher.ProbeLike`, so the live signal folds
through the **same** `account_state` path the fleet picker uses. **Fail-open throughout**: any
missing/torn read, or no signal at all, yields `None` → `account_state` reads that as SERVING
→ a healthy session is never rotated on a phantom wall.

### 3. `dos hook live-rotate` — the proactive live boundary (the trigger)

Wired to the **Stop** hook (once per turn, cheap; PreToolUse is an option). While the session
is up it reads the live signal, folds current + alternates, and asks `live_rotation.decide`.
On ROTATE it does the docs/386 §4 WRITE half **early** — persists the handoff to a serving
seat so the next `SessionStart` relaunch auto-applies it — and prints a same-window
`--resume` continuation. Context-only / advisory: never blocks, always exit 0, fails to
silence, and emits **nothing** unless a live signal actually says near-cap.

### 4. `dos accounts rehome` — the actuation nucleus (the hands)

The verb both the hook and an operator/supervisor call. Given `--session-id` (+ `--from`,
default `$CID_ACCOUNT`): pick a serving alternate, build its launch env via the resolved
`AccountAuthSpec` (byte-identical to what the launcher emits — config-dir only when fresh
creds can serve, per docs/380), write the handoff, and emit the `--resume` continuation.
PROPOSE by default (the `dos resume` discipline — never act unasked); `--exec` spawns the
headless relaunch under the rotated env. The spawn is a module-level indirection so it is
testable without launching a real `claude`.

## The flow, end to end

```
live turn boundary (Stop hook)
        │  dos hook live-rotate
        ▼
usage_probe.assess_current_seat ──► ProbeSnapshot (or None → fail-open, stop)
        │
account_switcher.account_state (current, probe) + (alternates, fail-open)
        │
live_rotation.decide ──► HOLD | ROTATE(to=seat) | WALL_WAIT | NO_ALTERNATE
        │ (ROTATE)
        ├─► rotation_handoff.write_handoff   (docs/386 §4 WRITE — fired EARLY, pre-wall)
        ├─► account_ledger.record_failure    (attribute to the leaving seat)
        └─► print "continue now:  CLAUDE_CONFIG_DIR=… claude --resume <sid>"
        ⋮
next SessionStart ──► rotation_handoff APPLY (docs/386 §4) ──► relaunch on the new seat
   (headless: dos accounts rehome --exec relaunches immediately; resume preserved)
```

## Proving it out (the DoS pass)

`tests/test_live_rehome_stress.py` is the adversarial proof; the threat model is a fleet under
load:

1. **Concurrent handoff store** — many threads writing handoffs at once (distinct sessions =
   the real shape, plus a pathological same-session pile-up): a concurrent reader never
   crashes and never sees a torn record. `os.replace` atomicity holds under contention.
2. **Thundering herd** — `plan_fleet` over 2000 near-cap sessions sharing 5 alternates fans
   out within ±1 of the fair share; no alternate absorbs the herd; headroom skew is respected.
3. **Breaker flood** — 5000 consecutive failures OPEN the breaker at the threshold and it
   stays bounded (counts are plain ints, verdict stably OPEN→HUMAN), so the asyncRewake loop
   is capped; a flapping seat still trips the total rung.
4. **Per-session store bound** — 3000 rotations of one session leave exactly ONE handoff file
   (latest-wins), so repeated rotation can't fill the disk.

## What stays the harness's (named, not waved off)

A direct env channel on the asyncRewake event itself would let an interactive session rehome
truly in-process. The harness exposes none (docs/386 §4); until it does, the interactive path
is "surface the `--resume` command, operator runs it same-window," and the headless path is a
supervisor relaunch. The rotation is **computed + recorded the instant the wall approaches**,
and applied at the first env-capable boundary — never lost.

## References

- [`380`](380_account-credential-propagation-to-live-sessions.md) — why a live process's env
  can't be hot-swapped; the fresh-creds launch deferral `_seat_launch_env` reuses.
- [`386`](386_the-agent-pluggable-account-switcher.md) — the two-phase rotate-on-wall handoff
  this phase fires proactively; §4 is the residual this extends.
- [`223`](223_the-circuit-breaker-primitive-failure-counting-as-mechanism.md) — the breaker
  the rewake loop and the live pressure signal ride on.
