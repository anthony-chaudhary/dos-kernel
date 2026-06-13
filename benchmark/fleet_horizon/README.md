# FleetHorizon — the open-loop vs closed-loop A/B benchmark

> **A long-designed benchmark, finally built.** The design called for an
> open-loop-vs-closed-loop A/B over a long-horizon task; this is the runnable
> instrument. It drives the **real** DOS kernel (`dos.oracle.is_shipped`,
> `dos.arbiter.arbitrate`, `dos.run_id`, `dos.lane_journal`) against a **real**
> git repo — no mocks, no LLM-judge.

## What it measures — and why it must be long *and* wide

DOS's value is **monotonically increasing in horizon × fanout**. A single agent
finishing a single PR rarely lies, rarely collides, rarely thrashes — at that
scale a non-believing kernel looks like pure overhead. The failure modes DOS
catches — self-misreport, silent last-write-wins, re-doing already-shipped work,
busy-wait spend — are *negligible at one effort × three steps* and *compounding
at N efforts × M steps*. So the benchmark is **long and wide by construction**:
a **fleet** of `N` concurrent long-horizon efforts, each `M` sequential phases,
on **shared repository state**.

This is the axis single-PR benchmarks (SWE-bench & kin) structurally cannot
see: they score *one* agent's *one* patch in *isolation*. FleetHorizon scores
*sustained fleet integrity* — what survives when many long efforts run at once
and nobody is allowed to believe what the workers say they did.

> **Two axes, and this now measures BOTH.** The four metrics below are the
> **integrity** axis (did the fleet lie / clobber). The **velocity** axis — the
> collaboration economics an operator actually feels — is now implemented too
> (`--velocity-sweep`), per the design note
> [`docs/81_velocity-economics-and-the-fleet-benchmark.md`](../../docs/81_velocity-economics-and-the-fleet-benchmark.md):
> the **human-review fraction** (the open loop must human-confirm everything; the
> closed loop routes only kernel-surfaced exceptions — the lever against the Faros
> productivity paradox), the **conflict-detonation cost** (`κ` multiplier on banked
> silent overwrites — a hand-merge tax), the **review-queue wait** (Kingman M/M/1
> ρ/(1−ρ) — the open loop saturates to unbounded wait), and the headline
> **verified-velocity-per-$** (real ships ÷ *fully-loaded* cost). `κ` and the review
> service rate `μ` are **swept, not picked** — the velocity headlines are the
> model-free human-review fraction and the **break-even κ**, compared against the
> published merge-cost literature.

### The two arms (same workload, same seed, same simulated work)

| Arm | Claims adjudicated? | Concurrent writes arbitrated? | What it models |
|---|---|---|---|
| **open-loop** | No — a `{shipped: true, sha}` self-report is **believed** | No — agents write whenever they like | A plain orchestrator: `agent({schema})` returns a shape, the loop banks it. (Dynamic-Workflows-shaped.) |
| **closed-loop** | Yes — every claim re-checked by `dos.oracle.is_shipped` against real git history | Yes — every write gated by `dos.arbiter.arbitrate` over live leases | The same work under DOS. |

### The four metrics (`metrics.py`)

1. **Lie rate** — phases the open loop *banked as shipped* that the oracle proves
   did **not** ship (no real commit closing them). The headline.
2. **Silent overwrites** — concurrent writes to the same file the open loop lets
   through as undetected data loss; the closed loop's arbiter refuses/reschedules.
3. **Wasted spend** — re-picked already-shipped work + collision rework + the
   busy-wait class, priced from a flat per-action cost.
4. **Verified-shipped-per-dollar** — the honest denominator: oracle-confirmed
   ships ÷ total spend. The number open-loop orchestration "has never quantified."

## The honesty discipline (don't rig it)

The steelman says "you defined lie-rate as what your oracle catches, so the
A/B is a tautology." The rebuttal this harness is built to honor: **lie rate and
silent-overwrite count are ground-truth properties of the open loop's own
output**, not DOS artifacts. The open loop *produced* the false `{shipped:true}`
and the clobbered file whether or not anyone measured them. The closed loop
merely *reveals* defects the open loop *shipped*. To keep that honest:

- The **agent failure model is identical in both arms** (same seed → same lies,
  same collision attempts). DOS does not get a "better agent"; it gets the *same*
  agent and a kernel that doesn't believe it.
- The oracle runs against a **real git repo**: a "lie" is literally "claimed
  shipped, but `git` shows no commit closing that phase." Checkable by hand.
- Failure rates are **swept parameters**, not hand-picked to win. The result is
  reported as a *curve over (horizon, fleet size)*, and the **gap → 0 as horizon
  → 1** is shown explicitly — the benchmark proves its own "nowhere else" clause.
- Metric 4 prices in the closed loop's verification overhead, as the steelman demands.

## Agent-agnostic — not a Claude artifact (`vendors.py`, `test_vendors.py`)

A second steelman: *"this only works because the agent is Claude."* It does not —
the kernel never reads who is acting. Two proofs, one behavioral and one
structural:

- **A heterogeneous fleet.** `vendors.py` gives each effort its own failure
  profile carrying a vendor label — a Gemini-flavored over-claimer (higher lie
  rate), a Codex-flavored flaky executor (higher silent-commit-failure rate), a
  Claude-flavored steady baseline. A `FleetProfile` is a **drop-in for one
  `FailureModel`** (same `worker(effort)` method), so a *mixed-vendor* fleet runs
  through **both arms unchanged** and the kernel **unchanged**. `test_vendors.py`
  pins: the honesty invariant survives heterogeneity (both arms, same ground
  truth); the closed loop banks **zero** lies whatever the vendor mix; and every
  caught lie **attributes back** to the vendor that emitted it (the kernel
  adjudicated a foreign claim, and the scorer can still name the culprit). The
  qualitative verdict is identical for an all-Gemini or all-Codex fleet — only the
  magnitudes move. The labels are illustrative archetypes, **not measured vendor
  lie-rates**; the claim is the conditional one (given a mixed fleet at these
  rates, DOS catches and attributes every banked falsehood).
- **The kernel cannot see the vendor.** `tests/test_vendor_agnostic_kernel.py`
  (in the kernel suite) shows there is no *channel* for identity: `arbitrate` /
  `AdmissionRequest` / `is_shipped` carry no agent/vendor/model parameter, free-text
  identity on a lease does not move the arbiter's verdict, and no kernel module
  names a vendor in code. Vendor-blind by construction, not by configuration.

The **live** counterpart (`live_demo.py`) drives the *real* Claude/Gemini/Codex
CLIs and shows `dos.oracle.is_shipped` refereeing their real self-reports against a
harness-controlled git repo. It is **opt-in and non-deterministic** — a smoke test
of the integration, deliberately *not* the falsifiable A/B (a live LLM makes the
rates unrepeatable; see `agent.py`'s "why simulated"). Ground truth is still git,
so even live the verdict never trusts the CLI's word.

## The third axis — ORCHESTRATOR (DOS-dispatch vs ultracode/harness, `docs/98`)

The two axes above are *trust* (believe vs adjudicate) and *velocity*. There is a
third: **who drives the fanout.** DOS's own dispatch/loop is one orchestrator; a
harness `Workflow` (the Claude Code `agent()`/`parallel()`/`pipeline()` tool,
informally "ultracode") is another — and **both drive the same trust seam**, so
they are directly comparable. The question an operator has: *can I lean on the
harness for the fanout and keep the trust guarantees?*

The instrument is `--orchestrator-sweep`, crossing the orchestrator with the trust
column. The 2×2 honestly collapses to **B (DOS-dispatch × adjudicate, =
`closed_loop.py`) vs D (harness-flow × adjudicate, = `harness_loop.py`)** plus a
shared believe baseline — because the believe column never calls `arbitrate`, so
lease-visibility has no call site to differ at. The single variable is the
**lease-visibility model** (a `LeaseBook` seam in `orchestrator.py`):
`InProcessLeaseBook` (DOS-native, instant) vs `JournalLeaseBook` (harness, shared
only through the durable WAL, with a `writeback` discipline knob).

The headline metric is **prevented-at-contention vs detected-after**: a DOS-native
loop (or a harness that writes its leases back via the new `dos lease-lane` verb)
PREVENTS every collision; a naive harness whose lease visibility lags lets the same
collisions slip past the arbiter to be DETECTED after the fact — some surviving as
silent overwrites `verify` cannot undo. Measured (seed 1729, 8×30, shared_ratio
0.3): DOS-native & disciplined-harness `prevention=100%, detected=0`; naive harness
`prevention=0%, detected=10, silent=10` — same `real_ships` and `banked_lies=0` in
all three (the honesty invariants hold). The falsifier: a genuinely disjoint
workload (`generate_disjoint`) ties every orchestrator — the gap → 0 where nothing
contends. Full write-up + tables: [`docs/98`](../../docs/98_the-orchestrator-is-a-driver.md).

```bash
# the orchestrator A/B: is ultracode as safe as DOS-native dispatch?
PYTHONPATH=src python -m benchmark.fleet_horizon.harness --orchestrator-sweep

# the orchestrator honesty tests (same-ships, gap-opens, disjoint-falsifier, seam-faithful)
PYTHONPATH=src python -m pytest benchmark/fleet_horizon/test_orchestrator.py -q

# the LIVE on-disk demo: real processes, dos lease-lane preventing a real clobber
DOS_LIVE_DEMO=1 PYTHONPATH=src \
    python -m benchmark.fleet_horizon.live_orchestrator_demo --issues 4 --overlap 3
```

## The fourth axis — HOST (advisory/RECORD vs enforcement/PREVENT)

The orchestrator axis varies *how* the fleet shares leases. The host axis varies
what the host *does* with the arbiter's verdict on a collision. The arbiter renders
the same verdict in both hosts; the host decides whether that verdict **binds**:

- an **enforcement** host treats a refusal as binding — the colliding write is
  **PREVENTED** at contention (deferred, drained later on a split footprint). No
  data is lost. (This is the in-process `closed_loop.py`.)
- an **advisory** host runs the same arbiter but the refusal is only advisory; its
  lease visibility lags, so two siblings both admit a colliding tree and the
  collision is merely **RECORDED** after both writes land — a `detected_collision`
  plus a surviving `silent_overwrite` `verify` cannot undo. (This is
  `harness_loop.py` with `lease_writeback=False`.)

The instrument is `--host-sweep`, emitting the **catch-vs-prevent curve**: a
per-host RECORD/PREVENT split as the fleet grows. Measured (seed 1729, 8×20,
shared_ratio 0.3): enforcement `prevented=70, recorded=0, silent=0`; advisory
`prevented=0, recorded=12, silent=12` — same `real_ships` in both (the honesty
invariant). The falsifier: a genuinely disjoint workload (`generate_disjoint`)
records nothing and loses nothing in either host — the gap → 0 where nothing
contends on shared state. (Same-lane serialization defers in the enforcement host
are a lease-window artifact, not contention, so the boundary predicate ignores
them, exactly as the orchestrator falsifier does.)

```bash
# the host A/B: does a RECORD-only host catch what an enforcement host PREVENTS?
PYTHONPATH=src python -m benchmark.fleet_horizon.harness --host-sweep
PYTHONPATH=src python -m benchmark.fleet_horizon.harness --host-sweep --json
```

## The picker axis (docs/91): scheduling around contention

Holds trust + orchestrator + host fixed (closed loop, in-process leases,
enforcement) and varies only **how the next phase to admit is chosen** — the
value-aware auto-pick the kernel's `rank_key` seam enables (docs/91 §3). Three
arms on the same seeded workload + cap, so `real_ships` and every claim are
identical per phase; only the pick **policy** differs:

- **fixed-lane** — today's loop: the seeded `interleave` order, and a write that
  collides with a live in-flight lease is REFUSED then retried on a split footprint
  (a wasted action). The baseline.
- **first-fit** — bare-pick: the arbiter's auto-pick walk advances some ready
  effort whose footprint is disjoint from the live leases, SCHEDULING AROUND
  contention instead of refuse+retry. No ranker (ladder order).
- **value-aware** — bare-pick PLUS a reference yield estimator
  (`yield_estimator.make_yield_rank_key`, a benchmark driver, never the kernel).

The win only shows **under a budget cap**: with no cap both arms drain the same
work and tie; with a cap, a policy that wastes fewer actions banks more verified
work before the cap (so `real_ships` becomes the dependent variable). The honest,
measured finding (it sharpens docs/90 §3) is that the win **splits**:

- **The infrastructure win is large and robust** — scheduling around contention
  banks **~1.6–1.7× more verified-velocity-per-$** at a tight budget (~1.37× even
  at drain). Measured (8×20, shared_ratio 0.35, 8 seeds): fixed-lane ≈ 0.37,
  bare-pick ≈ 0.64.
- **The ranker refinement is ≈ neutral here** — once contention is already
  scheduled around, ranking the disjoint set is near-flat (even an *oracle* ranker
  barely beats first-fit). The online-scheduling optimum stays open (docs/90 §3) —
  **reported, not tuned to win.**

Two falsifiers, both on the infra win: at a **drain budget** both arms reach every
phase and the gap relaxes (pick order stops mattering); with **no in-flight window**
(`window_override=0`) nothing contends and the gap → 1.0 (the win WAS
refuse-avoidance). The picker is **sound by construction** — it ranks only among
lanes the disjointness gate already admitted, so a bad estimate picks a
suboptimal-but-safe order, never an unsafe one.

```bash
# the picker A/B: does scheduling around contention bank more per dollar under a budget?
PYTHONPATH=src python -m benchmark.fleet_horizon.harness --picker-sweep
PYTHONPATH=src python -m benchmark.fleet_horizon.harness --picker-sweep --json
```

## Layout

```
benchmark/fleet_horizon/
  README.md        # this file
  workload.py      # deterministic seeded fleet: N efforts × M phases × file trees
                   #   + generate_disjoint(): the orchestrator-axis falsifier workload
  agent.py         # deterministic worker + tunable failure model (lie/collide/thrash)
  open_loop.py     # believe self-reports; no oracle, no arbiter
  closed_loop.py   # real dos.arbiter + real dos.oracle against a real git repo (DOS-native arm)
  orchestrator.py  # the ORCHESTRATOR seam (docs/98): LeaseBook protocol + run_fleet body +
                   #   InProcessLeaseBook / JournalLeaseBook + the shared GitGround helper
  harness_loop.py  # the harness/ultracode arm — cross-process leases via the WAL, writeback knob
  metrics.py       # integrity + velocity + orchestrator metrics (+ detected-collision/prevention_rate)
  yield_estimator.py # the reference rank_key for the picker axis (docs/91 P4) — benchmark driver, names no kernel
  harness.py       # run both arms over sweeps → the A/B tables (+ run_quad / --orchestrator-sweep / --host-sweep / --picker-sweep)
  plot.py          # graph the sweeps: CSV (always) + ASCII (always) + PNG (if matplotlib)
  test_fleet_horizon.py  # proves the harness is honest (gap→0 at horizon 1, etc.)
  test_value_aware_picker.py  # proves the picker axis is honest (docs/91): default byte-identical, soundness oracle, falsifiers
  test_orchestrator.py   # proves the orchestrator axis is honest (docs/98)
  live_orchestrator_demo.py  # OPT-IN live A/B: real processes + dos lease-lane vs a naive flow
  vendors.py       # per-effort vendor profiles (Claude/Gemini/Codex-flavored) — a
                   # drop-in for one FailureModel, so a HETEROGENEOUS fleet runs
                   # through both arms with zero change to the arms or the kernel
  test_vendors.py  # proves the A/B is agent-AGNOSTIC: a mixed-vendor fleet banks
                   # no lies under the kernel; per-vendor caught-lie attribution
  live_demo.py     # OPT-IN live smoke: real claude/gemini/codex CLIs as workers,
                   # real dos.oracle verdicts (NOT the benchmark — see its banner)
  test_live_demo.py     # deterministic test of the demo logic + skipped live smoke
  RESULTS.txt      # a captured canonical run of both sweeps
  build/           # generated CSV + PNG charts (gitignored)
```

## Running

The installed `dos` may point at another worktree; pin to this repo:

```bash
# one cell of the A/B (fleet=6, horizon=20, default failure model)
PYTHONPATH=src python -m benchmark.fleet_horizon.harness --efforts 6 --phases 20

# the full sweep + the A/B table + the monotonicity curve
PYTHONPATH=src python -m benchmark.fleet_horizon.harness --sweep

# the velocity axis (human-review fraction + break-even κ)
PYTHONPATH=src python -m benchmark.fleet_horizon.harness --velocity-sweep

# graph it — CSV + ASCII always; PNG charts if matplotlib is installed
PYTHONPATH=src python -m benchmark.fleet_horizon.plot

# the honesty tests
PYTHONPATH=src python -m pytest benchmark/fleet_horizon/test_fleet_horizon.py -q

# the agent-agnosticism tests (heterogeneous Claude/Gemini/Codex fleet)
PYTHONPATH=src python -m pytest benchmark/fleet_horizon/test_vendors.py -q

# the LIVE multi-vendor demo (opt-in; spends real tokens; NOT a measurement)
DOS_LIVE_DEMO=1 PYTHONPATH=src \
    python -m fleet_horizon.live_demo --vendors claude,gemini --phases 3
```

It is **not** in the kernel release gate (`testpaths=["tests"]`); it consumes the
kernel from outside, the same boundary as `examples/`. `matplotlib` is an optional
convenience for PNG charts only — never a kernel dependency; without it `plot.py`
degrades to CSV + ASCII, never an error.

## The trajectory dataset + the distillation experiment (`docs/84`)

Beyond the A/B scalars, the closed loop emits a per-step **ground-truth
trajectory** — for each phase, the worker's claim-side features kept rigidly
apart from the git-adjudicated label (`really_committed`) and the kernel's verdict
+ provenance. That tuple is the supervision signal agent-training data lacks
(outcome-labels bake in grader over-optimism; self-narrated traces train the
narration). See [`docs/84`](../../docs/84_ground-truth-trajectories-for-training.md).

```
  trajectory.py    # the per-step (features ⟂ label ⟂ verdict) record + JSONL dump
  verifier.py      # the distillation experiment: can a claim-side model learn the label?
  test_trajectory.py  # honesty tests (label≠claim, dump banks no lies, flake floor)
```

```bash
# can a cheap claim-side verifier DISTILL the kernel's verdict? (full vs. ablated)
PYTHONPATH=src python -m benchmark.fleet_horizon.verifier --efforts 6 --phases 15

# also write the labeled dataset to disk as JSONL
PYTHONPATH=src python -m benchmark.fleet_horizon.verifier --dump traj.jsonl
```

The result is an **irreducibility boundary**, not a yes/no: a claim-side model
recovers the *pure lies* (a lie writes zero files) but **cannot** catch the
*flakes* (files written, commit silently failed) — a flake is shape-identical to a
real ship, so only git separates them. You can pre-filter with a learned model;
you cannot remove the referee. Raising the flake rate raises the floor — the
falsifier. The trajectory is a faithful projection of the SAME run the A/B scores
(the `sink` only observes), so its labels ARE the headline table's, by construction.
