# docs/384 — Cron freshness: the dead-man's switch for always-on jobs

> **Status:** ✅ shipped — the `freshness` kernel verdict, the `[heartbeats]` config
> seam, the `dos beat` verb (record + `--check`), and the `pulse` freshness leg are
> all in `src/dos/` with tests (`tests/test_freshness.py`,
> `tests/test_heartbeats_config.py`, `tests/test_beat_cli.py`, the CRON-leg block in
> `tests/test_pulse.py`). This note is the design record + the wiring guide.
>
> Origin: operator goal 2026-06-16 — *"make long running always-on cron jobs and
> similar 10× more observable to DOS and human operators."* The complement to
> [docs/374](374_ten-x-stewards-one-invariant-per-steward.md), which wants 10× MORE
> stewards; this makes the stewards you already run OBSERVABLE.

## The gap: who watches the watchers?

`dos pulse` ([docs/121 §4.1](121_first-class-on-devices-and-unattended.md)) is DOS's
standing self-watch — a cron job that folds every live RUN's `liveness` and pushes
anything wrong, **silent when all-clear** (the silence rule keeps an URGENT pulse
worth reading). But `pulse` — and every other always-on job DOS leans on (the
supervisor cadence, `enforce-tune`, the [docs/374](374_ten-x-stewards-one-invariant-per-steward.md)
steward population) — has a failure mode `pulse` itself cannot see: **it can
silently stop firing.**

A cron's deadliest failure is not a loud crash; it is a quiet death. And by the
silence rule, a dead pulse emits *exactly what a healthy one does on a quiet cycle:*
nothing. The absence of an alarm reads, to anything downstream, as "all clear" — when
it may mean "the alarm itself is gone." This is the classic dead-man's-switch gap, and
[docs/344](344_the-dark-fleet-coordination-when-no-one-is-reading.md) sharpens why it
bites hardest in the dark-fleet regime: when no human reads the transcripts, the
only thing standing between the fleet and an unnoticed stall is the population of
unattended watchers — and nothing was watching *them*.

[docs/374](374_ten-x-stewards-one-invariant-per-steward.md)'s meta-steward names
exactly this need ("prune never-firing stewards … each steward's own fire-rate") but
had no primitive to read a fire-rate from. This is that primitive.

## The shape: `liveness`, lifted one level up

```
liveness.classify   — is this RUN moving?        (per-lease, ground-truth delta)
freshness.classify  — is this JOB still beating?  (per-cron, beat-ledger age)
```

Where `liveness` grades a run's commit/heartbeat age against ADVANCING / SPINNING /
STALLED windows, `freshness` grades a recurring job's newest-beat age against its
**declared cadence**:

  1. **FRESH** — newest beat within `cadence × grace_factor` (jitter slack). Firing on time.
  2. **LATE** — past the FRESH bound but within `cadence × dead_factor`: missed a window,
     probably slow/queued, not dead. WARN.
  3. **MISSING** — past `cadence × dead_factor`, OR no beat at all for a declared job: the
     dead-man's switch. URGENT for a `critical` job, WARN otherwise.

It is the temporal completion of `verify` that `liveness` is, turned on the watchers
instead of the watched. PURE: `now_ms` is injected, the verdict reads no clock —
replay-tested on frozen ages, the `liveness`/`pulse` discipline.

## What ships

| Piece | Home |
|---|---|
| The pure verdict — `Freshness`, `CadencePolicy`, `JobCadence`, `classify`, `fold`, `latest_beats`, `parse_cadence` | `src/dos/freshness.py` |
| The `[heartbeats]` config seam — declared jobs + cadences + slack factors as data | `src/dos/freshness.py` (`HeartbeatPolicy`, `load_from_toml`) → `SubstrateConfig.heartbeats` |
| The beat ledger path (a lane-journal sibling) | `PathLayout.beat_ledger` (both layouts) |
| `dos beat <job>` (record a proof-of-fire) / `dos beat --check` (fold + exit-code) | `src/dos/cli.py` (`cmd_beat`, `_append_beat`, `_read_beats`) |
| The `pulse` freshness leg (a silently-dead cron surfaces in the standing digest) | `src/dos/pulse.py` (`fold_pulse(freshness=…)`) + the `cmd_pulse` gather |

## A beat is a proof-of-fire, not a proof-of-work (the honest boundary)

`dos beat <job>` records that the scheduled thing *ran at all* at time T — the
dead-man's-switch ping. It does NOT claim the run did anything useful; that is what
`liveness` (did the run move?) and `verify` (did the claimed effect land?) are for. So
a beat is forgeable in the weak sense that a job could beat while doing nothing — but
it is **not forgeable in the dimension freshness cares about:** the *timing* is stamped
by the boundary clock at append, not by the job, so a job cannot make a missed window
look on-time. Freshness answers one narrow, high-value question — *did the cron fire
when it should have?* — and leaves correctness to the rungs built for it. Pair it with
`liveness`/`verify` for a job whose beats must also mean progress.

## The host-local boundary (where freshness works — and where it doesn't)

The beat ledger is **host-local**, the same scope the lane journal draws
([docs/366](366_single-filesystem-lease-boundary.md)): freshness is meaningful only
where the cron that beats and the checker that reads share a filesystem. Concretely:

- **Works:** an operator's machine running local always-on loops (a `/dos-dispatch-loop`,
  the supervisor, an in-session cron). Each loop calls `dos beat <job>`; the ledger
  persists in `.dos/beat-ledger.jsonl`; `dos pulse` / `dos beat --check` on that same
  machine see the beats. This is the primary case and it works today.
- **Does NOT work as-is:** an ephemeral CI runner (e.g. this repo's `pulse.yml` GitHub
  Action). A `dos beat` there writes to a runner that is discarded at job end, so a
  later local check never sees it. Watching an ephemeral cron needs a **persistent
  store** the checker can also read — a committed ledger, a build artifact, or the
  provider's own run-history API (a `gh api` driver, the `non_git_oracle` seam shape).
  That is a driver, deliberately out of this kernel slice.

This is why this repo's own `dos.toml` ships the `[heartbeats]` table **commented, as a
template** rather than live: declaring `pulse = "6h"` here would read MISSING forever
locally (the GitHub pulse's beats are ephemeral), which is noise, not signal. The
no-config-no-noise rule: freshness judges only what a workspace DECLARES it expects to
beat, so a workspace that declares nothing folds to zero verdicts.

## Usage

```bash
# 1. Declare what should beat (dos.toml):
#    [heartbeats]
#    grace_factor = 1.5            # FRESH bound = cadence × this (optional)
#    dead_factor  = 3.0            # MISSING bound = cadence × this (optional)
#    [heartbeats.jobs]
#    supervise    = "30m"
#    enforce-tune = { cadence = "12h", critical = false }

# 2. Have each always-on job record a beat when it fires (the loop's last step):
dos beat supervise

# 3. Watch freshness — a meta-cron, or fold it into the standing pulse:
dos beat --check          # per-job FRESH/LATE/MISSING; exit 1 on any LATE/MISSING
dos pulse                 # a silently-dead steward now surfaces in the digest
```

## Litmus

- The kernel names no specific job — every watched job is a row in the `dos.toml
  [heartbeats]` table; the kernel provides only the FRESH/LATE/MISSING mechanism (the
  `[lanes]`/`[supervise]` closed-config-as-data pattern).
- A workspace that declares no `[heartbeats]` produces zero freshness verdicts — the
  fold and the `pulse` digest are byte-identical to before the seam (no-config-no-noise).
- The verdict reads no clock and does no I/O — `now_ms`/the ledger are gathered at the
  boundary, frozen onto the inputs (the `liveness.classify` purity floor).
- A beat's *timing* is boundary-stamped, not job-authored — a missed window cannot be
  forged on-time.

## Done condition (met)

- `dos beat <job>` appends a beat; `dos beat --check` folds it and exits non-zero on a
  declared-but-silent job — pinned by `tests/test_beat_cli.py`.
- `dos pulse` surfaces a silently-dead cron at URGENT (critical) / WARN — pinned by the
  CRON-leg block in `tests/test_pulse.py` + the boundary tests in `tests/test_beat_cli.py`.
- The `[heartbeats]` table round-trips through `config.load_workspace_config` — pinned by
  `tests/test_heartbeats_config.py`.

## Follow-ups (not in this slice)

- **Wire the reference loop skills to beat.** `/dos-supervise-loop` and
  `/dos-dispatch-loop` could call `dos beat <job>` each tick, so the operator's local
  fleet is observable out of the box (a skill/package-data change, not a kernel one).
- **An ephemeral-cron driver.** Read a provider's run-history API (GitHub Actions, etc.)
  as the beat source for crons whose own filesystem does not persist — the
  `non_git_oracle` / `dos.evidence_sources` driver shape.
- **Ledger compaction.** The beat ledger is append-only; only the newest beat per job
  matters, so a `dos reap`-style compaction (the `lane_journal.compact` posture) can
  bound it. Deferred — a beat is a single small line, so growth is slow.

## Related

- [docs/374](374_ten-x-stewards-one-invariant-per-steward.md) — the 10× stewards whose fire-rate this primitive reads (the meta-steward's missing input).
- [docs/344](344_the-dark-fleet-coordination-when-no-one-is-reading.md) — why a silently-dead watcher is the dark-fleet failure this catches.
- [docs/121](121_first-class-on-devices-and-unattended.md) §4.1 — the unattended regime `pulse` (and now freshness) serves.
- [docs/82](82_liveness-oracle-plan.md) — `liveness`, the per-run verdict this is the per-cron analogue of.
- [docs/366](366_single-filesystem-lease-boundary.md) — the host-local filesystem boundary the beat ledger shares.
