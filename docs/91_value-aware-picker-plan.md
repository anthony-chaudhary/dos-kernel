# Value-aware auto-pick — a plan note (research area §3 → specced)

> **Today's auto-pick is greedy first-disjoint-wins on a fixed ladder
> (`arbiter.py:386`). This note specs the *better* picker named in
> [`90_open-research-areas.md`](90_open-research-areas.md) §3 — one that ranks the
> admissible lanes by expected verified-yield instead of taking the first — and,
> just as important, specs the two benchmark changes without which the win is
> unmeasurable. It is the highest-leverage / lowest-risk research area because the
> kernel seam already exists and the soundness floor is automatic.**

A phased plan in the form of [`70`](70_stamp-convention-plan.md)–[`73`](73_admission-predicate-plan.md)
and [`82`](82_liveness-oracle-plan.md): small, separately-testable slices, each
green before the next. It is **directional** until demand pulls it forward — it is
not yet in the [`next-stage-plan`](next-stage-plan.md) table.

The one-line thesis: **the picker is already an injected boundary, so a smarter one
is a new oracle, not a kernel change — but the benchmark currently never exercises
the picker and holds ground-truth ships fixed, so proving "smarter" requires
teaching the harness to measure throughput under a budget, not just cost-to-drain.**

---

## 0. What exists today (the seam, verified)

The pure arbiter already takes the picker as data — this is the whole reason the
plan touches no kernel logic:

- `arbitrate(..., auto_pick_order=…, pick_oracle=…)` (`arbiter.py:135`). On a
  **bare** request (no `requested_lane`) it walks `auto_pick_order` and takes the
  **first** lane whose tree is disjoint from every live lease (`arbiter.py:386`).
- `pick_oracle: (name, kind, tree) -> int | None` (`arbiter.py:146`) is a
  **best-effort availability gate**: the arbiter SKIPS a lane the oracle
  confidently reports as 0 picks; `None` (can't tell) never skips. It is resolved
  by the **caller**, never inside the pure kernel (`arbiter.py:223`), and the count
  that drove admission is cached so the reported `pick_count` matches the decision
  (`_last_pick_count`, `arbiter.py:221`).

Two facts about the *current* design that the plan must respect:

1. The picker is **availability-only** — "does this lane have work?" — and binary
   in effect (skip-if-zero). It carries **no notion of value/yield/cost**. There is
   no ranking; order is the static ladder order.
2. The benchmark **does not use this path at all.** `closed_loop.py:256` calls
   `arbitrate` with an *explicit* `requested_lane` (one fixed lane per effort,
   `lane_of`, `closed_loop.py:221`) and `requested_kind="keyword"`. So
   `auto_pick_order`/`pick_oracle` are never passed; the picker is dead code from
   the benchmark's vantage point. **Nothing today measures picker quality.**

---

## 1. The soundness floor (the invariant the whole plan rides on)

Before any mechanism: **a value-aware picker may reorder and rank ONLY among lanes
the disjointness gate already admits. It may never admit a conflicting lane to
chase yield.** Optimization rides on top of the safety verdict, never around it
([`90`](90_open-research-areas.md) §3, the same rule as the predicate-precision
floor §1). Concretely, the ranking is applied *inside* the existing
`if not _lease_collision(...)` branch (`arbiter.py:394`) — it picks the best of the
already-disjoint candidates; it does not gate admission. This makes the change
**sound by construction**: the worst a bad yield estimate can do is pick a
suboptimal-but-still-safe lane, never an unsafe one. That is why §3 is low-risk.

A second-order floor inherited from [`76`](76_flexible-goals-and-verification.md):
**the yield signal is never a kernel concept.** The kernel ranks by a number an
oracle hands it; *what makes a lane valuable* is driver/config policy. The kernel
stays domain-free; value enters only through the injected boundary. The day the
arbiter hard-codes a notion of "valuable," it has stopped being a substrate
([`79`](79_primitives-not-features.md)).

---

## 2. Phases

### Phase 1 — generalize the picker oracle from availability to yield (kernel) — ✅ SHIPPED

> **Shipped 2026-06-01.** `arbitrate` gained an optional
> `rank_key: (name, kind, tree) -> float | None` (`arbiter.py`); the bare
> auto-pick walk visits candidates in descending rank order via a stable
> `_ranked()` pre-pass (no-opinion/`None` sinks below ranked ones, ties keep ladder
> order, so `rank_key=None` is byte-identical to first-fit). Fail-soft `_safe_rank`
> swallows a raising/non-numeric estimator (and excludes `bool`, so a boolean
> ranker is "no opinion," not a silent 0/1). 7 new tests in
> `tests/test_arbiter.py::TestValueAwarePicker` — incl. the soundness pin (a ranker
> scoring a colliding lane highest still cannot get it admitted) — and the full
> kernel suite stays green (483 passed). **Phases 2–4 (benchmark wiring, budget
> cap, reference estimator) shipped 2026-06-13** — see each phase's callout below;
> the budget cap revealed that the robust win is *scheduling around contention*
> (the bare-pick infrastructure, ~1.6×), while the *ranker* refinement is ≈ neutral
> here and left open at §90.3.

The design, as built:

Today `pick_oracle` returns `int | None` (a count). Generalize the *concept* to a
**ranking key** without breaking the count contract:

- Keep `arbitrate`'s signature and the existing `pick_oracle` semantics
  byte-compatible (every current caller + test stays green — the count-as-skip
  behavior is unchanged).
- Add an **optional** `rank_key: (name, kind, tree) -> float | None` parameter (or
  let `pick_oracle` return a richer typed object the arbiter reads a `.rank` off,
  whichever keeps the diff smaller — decide at build time against the test surface).
  `None` ⇒ "no opinion," falls back to ladder order (the can't-tell-never-harms
  discipline, same as the availability gate).
- On a bare request, instead of returning the **first** disjoint+available lane,
  collect *all* disjoint+available candidates and return the **argmax of
  `rank_key`** (ties broken by ladder order, so with no ranker the behavior is
  byte-identical to today — the regression guard).

**Why this shape:** it is purely additive, the default path is unchanged, and the
ranker is resolved at the call boundary exactly like `pick_oracle` and the
`dos.predicates` discovery (`arbiter.py:189`). No kernel module learns what
"yield" means.

**Tests:** ranker-absent ⇒ byte-identical to the current first-fit (replay the
existing `test_arbiter.py` auto-pick cases); ranker-present ⇒ argmax selection;
ranker that raises ⇒ falls back to ladder order, never crashes (fail-soft, the
`pick_oracle` rule); the soundness pin — a ranker that *prefers* a conflicting lane
still cannot get it admitted (the disjointness gate wins).

### Phase 2 — exercise the picker in the closed loop (benchmark precondition) — ✅ SHIPPED

> **Shipped 2026-06-13.** `closed_loop.run` gained an opt-in `bare_pick` (+
> `rank_key` / `rank_key_factory`), all default-off so the fixed-lane A/B is
> byte-identical (the per-phase Step-2/3 body is now a shared `_commit_verify_emit`
> closure both paths call). In bare mode the loop is driven from per-effort cursors
> and issues ONE bare `arbitrate(...)` per step over an `auto_pick_order` built from
> the **ready efforts' own lanes** — the unit of pick is the EFFORT, not the lane,
> so every candidate's tree IS the phase's real footprint and the picker only
> chooses ORDER (the soundness floor held in the harness too). Tests in
> `benchmark/fleet_horizon/test_value_aware_picker.py` pin: default-path identity,
> same-ground-truth uncapped, the kernel auto-pick walk is actually driven, a ranker
> changes the pick order, and a raising ranker is fail-soft.

The benchmark must actually *use* auto-pick or the kernel work is unmeasured. Add a
**bare-request mode** to `closed_loop.py`:

- Instead of pinning `requested_lane = lane_of[effort]` and asking for that exact
  lane (`closed_loop.py:257`), allow the arm to issue a **bare** request and let
  the arbiter *pick* the lane/order from an `auto_pick_order` built from the
  workload's lanes, consulting a `rank_key`.
- This is opt-in (a flag/arm variant), so the existing fixed-lane A/B (which proves
  the integrity + velocity axes) is untouched and still the default.

**Tests:** the bare-request closed loop drains the same workload, ground-truth
ships unchanged (the honesty invariant — DOS does not get a different worker); the
picker's choices appear in the lane journal (so a replay shows *which* lane the
arbiter chose and why), reusing the existing ACQUIRE/REFUSE WAL.

### Phase 3 — make throughput measurable: a budget/deadline cap (benchmark) — ✅ SHIPPED

> **Shipped 2026-06-13.** `closed_loop.run` gained arm-independent `budget` /
> `max_steps` caps (default-off ⇒ drain), checked at the top of each step (and the
> budget binds the deferred drain too, so it is a real ceiling). Under a cap
> `real_ships` becomes a dependent variable, freeing the numerator. The harness
> gained `run_capped_pair` (THREE arms — fixed-lane, bare-pick first-fit, bare-pick
> value-aware), `_aggregate_vv_per_dollar` (pooled Σships ÷ Σloaded over a seed
> ensemble — the win is an aggregate, not a per-seed promise), and
> `_picker_sweep` / `_picker_sweep_data` / `--picker-sweep` (a curve over the budget
> axis + two falsifiers), mirroring the host-sweep pattern.
>
> **The measured finding, stated honestly (and it sharpens §90.3).** The budget cap
> revealed that the win splits into two very different effects:
>
> * **The infrastructure win is large and robust (~1.6× verified-velocity-per-$ at
>   a tight budget, ~1.4× even at drain):** *scheduling around contention* — the
>   kernel's auto-pick walk advances a different ready effort instead of refusing a
>   colliding write and paying a retry. This is the headline, and its two falsifiers
>   hold (it relaxes at a drain budget where order matters less; it collapses to
>   ≈1.0 at `shared_ratio=0` where there is no contention to schedule around).
> * **The ranker refinement has almost no headroom in this workload:** ranking the
>   already-disjoint candidates by a yield estimate is ≈ neutral vs first-fit — and
>   *even an oracle ranker that peeks at which phases ship barely beats first-fit*.
>   Once bare-pick has scheduled around contention, first-fit over the disjoint set
>   is already near-optimal here. This is exactly the open online-scheduling optimum
>   §90.3 names; the benchmark now *measures* that the greedy point is near the
>   ceiling for this workload rather than asserting a clever ranker wins. Reported,
>   not tuned-until-it-wins.

This is the subtle, load-bearing slice. **Before this, the harness drained the
entire workload** and **held `real_ships` identical across arms by construction**
(`metrics.py` — "Same real ships in both arms; the arms differ only in the loaded
cost"). Under a drain-everything model **a better picker cannot change `real_ships`**
— so its win can ONLY show up as lower *loaded cost* (fewer refused-write retries →
fewer `action`s; fewer conflict detonations). That is a real but *second-order*
effect and undersells the point.

The auto-pick win actually lives in **throughput under a constraint**: with a fixed
budget or deadline, a smarter pick *order* completes more *verified* work before the
cap. So add to `harness.py`/`metrics.py`:

- A **budget cap** (max total `action` cost) or **deadline cap** (max steps) — the
  loop stops when the cap is hit instead of draining. Optional; default-off so the
  existing sweeps are unchanged.
- Under a cap, `real_ships` becomes a **dependent variable** again, and the headline
  is **verified-velocity-per-$ at a fixed budget** (`metrics.py:197` already
  computes the per-$ number; the cap is what lets the *numerator* move). A
  value-aware picker should bank more verified ships per fixed dollar than first-fit
  because it front-loads the high-yield, low-conflict lanes.

**Tests:** capped run stops at the cap; first-fit vs value-aware under the same cap
and seed ⇒ value-aware's `verified_velocity_per_dollar` ≥ first-fit's (the A/B that
proves §3), reported as a curve over budget like the existing κ-sweep, **with its
own falsifier** (the gap → 0 as the budget → enough-to-drain-everything, where pick
order stops mattering — the same honesty move as the fleet/horizon → 1 falsifier in
`_sweep`).

### Phase 4 — a reference yield estimator (driver/benchmark, NOT kernel) — ✅ SHIPPED

> **Shipped 2026-06-13.** `benchmark/fleet_horizon/yield_estimator.py` ships
> `make_yield_rank_key(workload, cursors)` — a `rank_key` factory that closes over
> the live cursors and ranks a ready effort by remaining horizon, demoting one whose
> next phase would reach shared state under live concurrency (the would-refuse pick).
> It is pure, total, fail-soft, and BLIND to the lie/flake rolls (it reads intent to
> touch shared, never realized success — `verify` catches lies, the picker does not).
> The litmus holds: it imports nothing from `src/dos/`, and `src/dos/` references
> nothing from it (`test_estimator_imports_nothing_from_the_kernel`). As §3's finding
> records, this reference estimator is ≈ neutral vs first-fit in this workload — the
> seam is *demonstrated and measurable*, and the question of an estimator that
> reliably beats first-fit is left open at §90.3 where it belongs.

Provide one concrete `rank_key` so the seam is demonstrated, kept firmly outside the
kernel:

- In the **benchmark**, the estimator can use ground truth it legitimately has —
  e.g. rank a lane by `(expected real-ships remaining) / (expected conflict cost)`,
  estimable from the workload's phase structure + `shared_ratio`. This is the
  *labeled* setting; it shows the ceiling.
- In **production**, document that this is a **driver-supplied oracle** (a
  `dos.predicates`-style entry point, or a host's own function) — it might score a
  lane by open-phase count × historical merge-conflict rate × priority. The kernel
  never ships a real one; it ships the *seam* and the `AbstainJudge`-equivalent
  no-op default (rank everything equal ⇒ ladder order). Same kernel/driver split as
  the judge rung ([`87`](87_the-adjudicator-trust-ladder.md)) and the renderer seam.

---

## 3. Deliverables, by layer (the litmus this plan must keep)

| Layer | Deliverable | Litmus |
|---|---|---|
| **Kernel** (`arbiter.py`) | optional `rank_key`; argmax-among-disjoint; fail-soft | default path byte-identical; ranker cannot defeat disjointness; kernel names no notion of "yield" |
| **Benchmark** (`closed_loop.py`, `harness.py`, `metrics.py`) | bare-request mode; budget/deadline cap; capped per-$ headline + falsifier | existing fixed-lane sweeps unchanged + default-off; honesty invariant (same ships absent a cap); a replayable journal of picks |
| **Driver/example** | one reference `rank_key`; docs that it's driver policy | no kernel module imports it; no-op default = ladder order |

**The boundary that makes this safe:** Phase 1 is the only kernel change and it is
purely additive + sound-by-construction. Everything that knows what "valuable"
means lives in Phases 2–4, outside `src/dos/`. If the objective ever needs to live
*inside* a verdict, that is the signal it has become a new typed verdict on the
registry ([`86`](86_the-typed-verdict-surface.md)) — **not** a reason to grow
`arbitrate`.

---

## 4. Why this is the one to do first (from §3's priority call)

- **The seam exists** — `pick_oracle`/`auto_pick_order` are already injected and
  caller-resolved; Phase 1 is additive.
- **The floor is automatic** — ranking among already-admitted lanes cannot produce
  an unsafe admission, so the highest-risk failure mode (a clever optimizer that
  corrupts state) is structurally impossible.
- **The harness is most of the way there** — it already computes
  `verified_velocity_per_dollar` and already sweeps a parameter with a falsifier;
  Phases 2–3 reuse that machinery rather than inventing a benchmark.

The honest caveat, stated so the next builder isn't surprised: **Phase 3 is the
real work.** The kernel change is an afternoon; teaching the benchmark to measure
*throughput under a budget* (so the picker's win is visible at all) is where the
thought goes — because the current model deliberately fixes ground-truth ships to
make the *integrity* A/B honest, and that same fixity hides a picker's throughput
gain until a budget cap frees the numerator.

---

## See also

- [`90_open-research-areas.md`](90_open-research-areas.md) §3 — the area this note
  specs; §6 (value-aware admission) is the general frame this is the first concrete
  instance of.
- [`89_the-lane-is-a-region-lock.md`](89_the-lane-is-a-region-lock.md) — the picker
  ranks among non-conflicting *region-locks*; the soundness floor is "rank, never
  re-admit."
- [`81_velocity-economics-and-the-fleet-benchmark.md`](81_velocity-economics-and-the-fleet-benchmark.md)
  — the `verified-velocity-per-$` headline Phase 3 makes budget-sensitive, and the
  sweep-with-a-falsifier pattern Phase 3 reuses.
- [`76_flexible-goals-and-verification.md`](76_flexible-goals-and-verification.md) —
  the law that keeps the yield signal in the driver, never the adjudication.
- `src/dos/arbiter.py` (`pick_oracle`, the bare-request walk),
  `benchmark/fleet_horizon/{closed_loop,metrics,harness}.py` — the surfaces this
  plan touches.
