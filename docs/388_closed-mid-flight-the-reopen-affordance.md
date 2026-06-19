# 388 — Closed mid-flight: the reopen affordance

> **A session that walks out on live work should not strand it in silence.** When
> an operator closes a session while a run still holds a lane and has steps left,
> the work does not finish and nobody is told. DOS already knows the run is live
> (`liveness`), already knows where to re-enter it (`resume`), and already knows
> how to push a clickable notification out a transport (docs/387). This wires those
> three together: on a close-while-in-progress, file a durable record, fire a
> **system notification**, and make the click a **one-step way back into the run**.

## The one idea

You cannot talk to a session that is already gone. So the recovery has to be three
things at once: **durable** (it outlives the dead session), **out-of-band** (it
reaches the operator who is no longer at that terminal), and **re-entrant** (it
hands back a safe way to pick the run up). That is exactly the shape the
account-switcher already built for a different cause — see "The twin" below. This
doc is the human-close twin of that machine-wall mechanism.

It is a **composition, not new mechanism**. Three parts DOS already ships:

- the **Stop / SessionEnd hook** seam (the only point DOS gets control as a session ends),
- the **`resume`** syscall (`src/dos/resume.py`) — it computes a safe re-entry SHA and the residual steps, and it *proposes, never executes*,
- the **clickable notify spine** (docs/387) — `Notification` + `NotifyAction` + `send_safely`.

## What "closed while in progress" means (a checkable definition)

Not a vibe — a verdict read from ground truth, the same way every DOS syscall is:

1. **A live lease is held.** `lane_lease.live_leases(cfg, expire_dead=True)` folds the
   WAL and drops provably-dead leases. A surviving lease means a run claimed a lane
   and never released it.
2. **The run still has work to do.** Run `resume.resume_plan(...)` over that run's
   intent ledger. Its verdict decides whether a reopen is even offered:
   - `RESUMABLE` (clean re-entry SHA + non-empty residual) → **offer the reopen.**
   - `COMPLETE` → nothing left; stay quiet.
   - `DIVERGED` / `UNRESUMABLE` → no safe re-entry; stay quiet (never guess a reopen).

So the trigger is: *at session close, there exists a live lease whose run is
`RESUMABLE`.* Both inputs are existing pure verdicts; the hook gathers them at the
CLI boundary and folds — no new adjudication logic, no self-report.

The lease→run→session join is the same join `dos top` and the account ledger
already need (a `run_id` stamped on the `ACQUIRE` record, carried in lineage env).
Where that join is still partial (the docs/107 §1 gap), the hook **fails toward
surfacing**: it offers a reopen for any live, still-`RESUMABLE` run in the workspace
rather than dropping it — a reopen the operator dismisses costs a click; a silently
stranded run costs the work.

## The trigger: SessionEnd is the true signal; Stop is the earlier checkpoint

- **`SessionEnd`** is the precise "the session closed" event (Claude Code fires it on
  `clear` / `logout` / `prompt_input_exit` / `other`). It is the right primary trigger
  — and it is **not wired today** (the plugin wires `SessionStart` but no `SessionEnd`),
  so this adds it, parallel to the existing SessionStart hook.
- **`Stop`** fires at the end of each turn. The existing Stop hook (`cmd_hook_stop`,
  `src/dos/cli.py`) already asks a related question — *are you trying to stop on a false
  done?* — and **blocks** to keep the loop going. The reopen hook is its complement,
  not a replacement, and the posture difference is the whole point:

| | the existing Stop hook | the reopen hook |
|---|---|---|
| when | end of a turn, loop still alive | the session is closing / gone |
| move | **block** ("not done — keep going") | **record + notify** ("you left — here's the way back") |
| works because | the harness loop is still there to continue | the record + toast outlive the dead loop |

Blocking is useless once the operator has closed the terminal — there is no loop left
to hold open. That is why the reopen hook does not block: it **files a durable handoff
and fires an out-of-band notification** instead.

## The durable record: `ReopenHandoff` (modeled on `RotationHandoff`)

A near-twin of `src/dos/rotation_handoff.py`, same fail-soft discipline (atomic
`os.replace`, torn record reads back as "none", a write fault never breaks the hook),
filed per session under `.dos/reopen/<safe_sid>.json`:

```python
@dataclass(frozen=True)
class ReopenHandoff:
    run_id: str                    # the abandoned run to re-enter
    lane: str                      # the lane it still held
    resume_sha: str                # the safe re-entry SHA (from resume_plan)
    residual: tuple[str, ...]      # the step ids still owed
    command: str                   # the advisory reopen verb: `dos resume --run <id> --workspace <root>`
    reason: str = ""               # "closed mid-flight: lane src, 3 steps left"
    ts_ms: int = 0
```

The record is what makes recovery survive the session that created it — the same
reason rotate-on-wall persists a `RotationHandoff` instead of trying to hand the env
to a session that is already dying.

## The notification: one new source row, one new pure adapter

On the notify spine (`src/dos/notify.py`), this is the smallest possible addition —
the docs/387 design said a new follow-up "adds one row to `action_for_source`, never
a transport edit," and that holds here:

```python
# one row added to the closed source→verb map (notify.py:_SOURCE_ACTION)
"reopen": ("reopen the abandoned run", "resume"),
```

and one pure adapter beside `notification_for_top`:

```python
def notification_for_reopen(handoff, *, summary="", root="") -> Notification:
    """A ReopenHandoff → a WARN Notification whose action reopens the run."""
    # severity WARN: a stranded run needs you, but nothing is on fire.
    # source="reopen"; the action enriches the generic `dos resume` verb with the
    # run id the handoff carries (the way notification_for_top self-sources its root).
```

`action_for_source("reopen", root=root)` returns
`NotifyAction("reopen the abandoned run", "dos resume --workspace <root>")`; the
adapter folds the `--run <id>` onto it from the handoff, and may set the optional
`url` deep link a host/driver renders.

## The "system notification": a `notify_desktop` driver

A terminal OSC 8 line or a Slack button (docs/387) is fine when the operator is
*looking*; "closed the session" means they are not. So the click target wants an
**OS-level toast** — a new transport driver `notify_desktop` registered under the
`dos.notifiers` entry-point group, beside `notify_slack` / `notify_webhook`:

- it renders the neutral `Notification` as a native toast (Windows toast / macOS
  `display notification` / Linux `notify-send`) — all OS-specific code lives **in the
  driver** (layer 4), where vendor/OS names are allowed; the kernel stays blind;
- the toast's activation carries the `NotifyAction` — clicking runs its `command`
  (or opens its `url`), i.e. `dos resume --run <id>`;
- it is fail-soft like every notifier: no toast backend present → `delivered=False`,
  never a raise, via `send_safely`.

## What the click does — and the advisory floor (docs/99)

The click **opens the reopen**: it runs `dos resume`, which computes the re-entry SHA
and the residual and **proposes** them. It does **not** force a relaunch. The operator
re-enters by choice — the same way a LIVENESS notification carries a paste-to-stop the
operator runs by choice, never a one-click stop. A notification reports; it does not
act on the fleet.

This is the deliberate difference from the twin below: rotate-on-wall *auto*-relaunches
because a machine wall plus the harness `asyncRewake` make an unattended recovery safe.
A human who closed a session is the one who decides whether to reopen it, so the kernel
default stays **advisory and human-gated**. A host that owns its loop *may* opt into a
one-click full reopen by reusing the very same SessionStart applier path the
rotate-on-wall handoff already uses — but that is the host's choice, downstream of the
verdict, never the kernel's.

## The twin — why this is "the account switcher thing"

Rotate-on-wall (docs/386 §4) already solved "a session died with live work, recover it
across the gap." It just had a *different cause* (a rate-limit wall, not a human close)
and could afford a *different posture* (auto, not advisory). Strip those away and the
skeleton is identical — which is the point: this hook reuses a proven shape rather than
inventing one.

| | rotate-on-wall (docs/386 §4) | reopen-on-abandon (this doc) |
|---|---|---|
| what ended the session | a rate-limit **wall** (machine) | the operator **closed it** mid-flight (human) |
| trigger hook | `StopFailure` | `SessionEnd` (+ a `Stop` checkpoint) |
| durable record | `RotationHandoff` | `ReopenHandoff` |
| filed at | `.dos/accounts/rotation/<sid>.json` | `.dos/reopen/<sid>.json` |
| recovery posture | **auto** — `asyncRewake` relaunches under a new seat | **advisory** — a toast offers a one-click `dos resume` |
| who enacts | the harness (machine) | the operator (a click) |
| applied at | `SessionStart` → `CLAUDE_ENV_FILE` | the operator's next session / a host deep link |

Both obey the one rule: *you cannot hand anything to a session that is already gone, so
file a durable record and recover at the next boundary.*

## Litmus tests this keeps

- **Kernel names no host / vendor.** Detection is pure (`live_leases` + `resume_plan`);
  `ReopenHandoff`, `notification_for_reopen`, and the `reopen` row name only the generic
  `dos resume` verb. The OS-toast code and any host deep link live in the `notify_desktop`
  driver; the SessionEnd/Stop wiring is host config.
- **Advisory floor (docs/99).** The click opens a *proposing* verb; nothing auto-stops,
  auto-relaunches, or mutates. The reopen is the operator's call.
- **A new follow-up adds one row to `action_for_source`, never a transport edit** — the
  `reopen` row, exactly as docs/387 set up.
- **Fail-soft everywhere.** A handoff-write fault never breaks the SessionEnd hook; a torn
  record reads back as "none"; an undeliverable toast is `delivered=False`, never a raise
  (`send_safely`); no live-resumable run → no record, no toast, no noise.
- **`verify` needs no plan; paths resolve via `SubstrateConfig.root`.** Unchanged — the
  hook reads existing verdicts and files under `.dos/`.

## Phased plan

Each phase is independently shippable and keeps the suite green.

| Phase | What | Layer | Witness |
|---|---|---|---|
| 1 | a pure fold `closed_in_progress(leases, resume_plans) -> ReopenIntent \| None` | 1 kernel | unit test over synthetic leases + resume verdicts |
| 2 | `src/dos/reopen_handoff.py` (write / read / clear / consume), mirroring `rotation_handoff` | 1 kernel (boundary I/O) | round-trip + torn-tail test |
| 3 | `notification_for_reopen` + the `reopen` row in `_SOURCE_ACTION` | 1 kernel | adapter + source-map test |
| 4 | wire `SessionEnd` (new) + a `Stop` checkpoint to emit via the configured notifier; add `SessionEnd` to `claude-plugin/hooks/hooks.json` + the `hook_install` specs | 3 helper / host | hook command driven by a synthetic event |
| 5 | `src/dos/drivers/notify_desktop.py` — the OS-toast transport + click handler, under `dos.notifiers` | 4 driver | driver test with the OS call mocked; fail-soft |
| 6 | *(optional, host-gated)* one-click full reopen via the SessionStart applier path, reusing the rotate-on-wall relaunch seam | 4 driver / host | opt-in; advisory default unchanged |

## Files

| Path | Layer | What |
|---|---|---|
| `src/dos/reopen_handoff.py` | 1 (kernel) | `ReopenHandoff`, the durable per-session record (twin of `rotation_handoff.py`) |
| `src/dos/notify.py` | 1 (kernel) | `notification_for_reopen`; the `reopen` row in `_SOURCE_ACTION` |
| `src/dos/resume.py` | 1 (kernel) | unchanged — the re-entry the action opens |
| `src/dos/cli.py` | 3 (helper) | the SessionEnd hook + the Stop checkpoint that fold the verdict and `send_safely` the notification |
| `src/dos/drivers/notify_desktop.py` | 4 (driver) | the OS-toast transport with a click-to-reopen handler |
| `claude-plugin/hooks/hooks.json`, `src/dos/hook_install.py` | host | add the `SessionEnd` event to the wired set |
| `tests/test_reopen_handoff.py`, `tests/test_notify*.py` | — | the fold, the record, the adapter, the source row, the driver |
</content>
</invoke>
