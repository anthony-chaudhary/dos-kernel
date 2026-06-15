# 97 — Concurrency-class model: collapse the lane-kinds into one mechanism

> **Status:** PLAN (not yet built). Motivated by the 2026-06-01 plan-lane wedge —
> see the worked lesson in
> [`96`](96_from-plan-lanes-to-concurrency-classes.md) and the primitive in
> [`89`](89_the-lane-is-a-region-lock.md). The immediate overlap-math fix
> (`lane_overlap.REFUSE_EXACT_GLOB`) and the job-side stop-gaps (cluster-member
> defocus + pre-dispatch health gate) already shipped 2026-06-01; this plan is the
> *structural* fix above them.

## Problem (one line)

The lane taxonomy hard-codes three concurrency-kinds (`concurrent` / `exclusive` /
`autopick`) and the auto-pick ladder's `by_slot_then_status` rung mints **one
ad-hoc lane per live plan** (~125), a third of which overlap a cluster by
construction — the conflation that produced the wedge. There is no first-class
notion of "a *class* of contended region with a *concurrency budget*."

## Goal

Replace the three hard-coded kinds with a **concurrency-class registry**: a class
is `{name, region_source, max_concurrent, exclusive?}`. Clusters, exclusive lanes,
named lanes, and "priority work" all become *instances* of this one mechanism.
"3 priority slots" becomes `class=priority, max_concurrent=3` — the concurrency
the operator asked for, expressed as data, with disjointness enforced at
region-bind time so concurrent priority workers cannot collide.

**Non-goal:** do NOT introduce fixed lanes named `priority-1/2/3`. Per
[`89`](89_the-lane-is-a-region-lock.md) §4.4 that is a swim-lane category error (a
fixed lane count). A priority slot is an *anonymous holder of the `priority`
class*, bound to a disjoint region at grab time.

---

## The model

```
ConcurrencyClass:
  name           : str            # "clusters" | "exclusive" | "named" | "priority"
  max_concurrent : int            # budget: how many holders of THIS class at once
                                  #   exclusive → 1 (and refuses across all classes)
  region_source  : RegionSource   # how a free region of this class is produced
  rank           : int            # ladder order for a bare auto-pick (lower first)

RegionSource (one of):
  FixedTrees(trees: dict[name -> tree])   # clusters, named — a closed menu
  WholeWorkspace()                        # exclusive — the coarsest lock
  TopPickablePlan(pool, derive_tree)      # priority — open set, disjoint-checked
```

A **lease** gains two fields (back-compat: both optional, default to today's
behavior):

```
lease.class    : str   # which concurrency-class this holder belongs to
lease.payload  : str   # legible cargo — the plan/region bound (e.g. "TM"); NOT identity
```

`arbitrate` admission becomes, for a request against class `C`:

1. **Class budget check.** Count live leases with `class == C`. If
   `count >= C.max_concurrent` → refuse (`CLASS_BUDGET_EXHAUSTED`). For an
   `exclusive` class, the budget is 1 *and* it refuses if **any** lease (any
   class) is live, and any new request refuses while it is live (unchanged
   `global`/`orchestration` semantics).
2. **Free-region resolution.** Ask `C.region_source` for a region disjoint from
   every live lease's tree (using the existing `overlap_verdict`, now exact-glob
   aware). `FixedTrees` walks its menu; `TopPickablePlan` returns the top-ranked
   plan whose derived tree is admissible. None found → refuse
   (`NO_FREE_REGION`).
3. **Bind + lease.** Write the lease with `class=C`, `tree=<resolved region>`,
   `payload=<region label>` (cluster name, or the plan id for priority).

The bare auto-pick ladder is then just "walk classes by `rank`, return the first
that admits" — the *same* walk as today, but over classes instead of a flat
mixed rung list, and the `priority` class replaces `by_slot_then_status` wholesale.

---

## How the special cases collapse

| Today | Class instance |
|---|---|
| `concurrent: (apply, tailor, discovery)` | `clusters`: `FixedTrees(cluster_trees)`, `max_concurrent=3`, rank=2 |
| `exclusive: (global, orchestration)` | `exclusive`: `WholeWorkspace()`, `max_concurrent=1`, refuses-across-classes, rank=0 |
| `autopick` named lanes | `named`: `FixedTrees(named_trees)`, `max_concurrent=len(named)`, rank=3 |
| `by_slot_then_status` (mint 125 plan-lanes) | `priority`: `TopPickablePlan(pool, derive)`, `max_concurrent=3` (config), rank=4 |
| `slot:P1` elevation | a `priority` variant with a slot filter, or a higher-rank `priority` class — TBD in Phase 2 |

The `LaneTaxonomy` dataclass (`concurrent`/`exclusive`/`autopick`/`trees`/
`aliases`) is **replaced by `classes: tuple[ConcurrencyClass, ...]`** (with a
back-compat shim that synthesizes the classes from the old fields so existing
workspace configs keep working until they migrate).

---

## Phases (throughline-first — each ships an enabled slice)

> **Status (2026-06-03): Phase 1 has PARTIALLY landed at the kernel API.** The
> claim-budget *admission step* this phase is built around — "at most N leases of a
> kind" — is live in the pure arbiter: `arbitrate(..., class_budgets={"priority": 3})`
> takes the per-kind budgets (`arbiter.py:159`), counts live leases per kind on the
> auto-pick walk and skips a budget-exhausted candidate (`arbiter.py:329-348`), and
> returns the named `CLASS_BUDGET_EXHAUSTED` refuse (`arbiter.py:637-655`), pinned by
> `tests/test_arbiter.py`. `class_budgets=None` is byte-identical to the old behavior
> (the back-compat floor). What is NOT yet built from Phase 1: the `ConcurrencyClass`
> + `RegionSource` *types*, the `TopPickablePlan` priority-region routing, and any
> operator surface — `class_budgets` is reachable only as a Python parameter (no
> `dos arbitrate --class-budget K=N` flag, no `dos.toml` declaration). Surfacing it
> is its own slice (see [[the safe-vs-phased split in the 2026-06-03 notes pass]]);
> Phases 2–3 remain as written below.
>
> **Update (2026-06-15): the operator surface + the type model + the routing now
> ship.** The `--class-budget K=N` flag and the `[[concurrency_class]]` `dos.toml`
> declaration shipped (docs/110 Phases 1-2, `dos.concurrency_class`). The
> `ConcurrencyClass` fields (`rank`, `region_source`) and the `RegionSource` types
> shipped — `FixedTrees` / `WholeWorkspace` / `TopPickablePlanKind` plus the
> call-boundary `TopPickablePlanBinding` (the single design `TopPickablePlan(pool,
> derive_tree)` was split into a TOML-declarable discriminant + a call-boundary
> binding holding the callables). The `TopPickablePlan` priority-region routing
> ships as `concurrency_class.project_to_ladder` — the host projects its open pool
> into the existing `auto_pick_order` and the unchanged arbiter does the
> disjointness + budget admission; the `NO_FREE_REGION` refuse (this doc's §model)
> is declared. The full §Test-obligations set drives through the projection
> (`tests/test_arbiter.py::TestOpenPoolRegionRouting`). What remains is the host
> driver wiring + the optional lazy in-arbiter resolver — see docs/110 Phase 3's
> status note.

**Phase 1 — the class registry + the `priority` class, behind the old behavior.**
Add `ConcurrencyClass` + `RegionSource` to `dos.arbiter`/`config`. Synthesize the
four classes from the existing `LaneTaxonomy` so behavior is byte-identical, THEN
route the `priority` class through `TopPickablePlan` with `max_concurrent` from
config (default 3). The visible change: a bare auto-pick that today mints a
plan-lane now grabs a `priority`-class lease (payload = the plan id), and a 2nd/3rd
concurrent bare loop can grab a *disjoint* priority region — real N-way priority
concurrency. `by_slot_then_status` is deleted. Ship + soak.

**Phase 2 — fold clusters/named/exclusive into classes.** Replace the
`concurrent`/`exclusive`/`autopick` walk with the class-by-rank walk. Old
`LaneTaxonomy` fields become a back-compat synthesizer. `slot:P1` re-expressed as
a class. Display (`--leases`) shows `class` + `payload`.

**Phase 3 — the registry is config.** Workspaces declare classes in `dos.toml`
(`[[concurrency_class]]` tables); adding a `maintenance` class is config, not
code. Tombstone the old taxonomy fields.

---

## Test obligations (carried from the incident)

- **No-collision invariant:** two concurrent `priority` grabs return disjoint
  regions (the exact-glob floor makes TM-over-tailor impossible; this asserts it
  at the class level).
- **Budget invariant:** the `(N+1)`-th `priority` grab refuses
  `CLASS_BUDGET_EXHAUSTED` while N are live.
- **Reachability invariant (LSR0):** every live plan with a derivable tree is
  reachable via the `priority` class when no cluster covers it pickably — the
  guarantee the cluster-member defocus already preserves
  (`tests/test_default_ladder.py`), re-asserted against the class model.
- **Exclusive invariant:** an `exclusive`-class lease refuses every other request
  and is refused while any lease is live (unchanged `global`/`orchestration`).
- **Back-compat:** a workspace with the old `LaneTaxonomy` fields and no `classes`
  produces byte-identical arbitration through the synthesizer.

---

## Boundary (DOS vs host)

- **DOS owns:** `ConcurrencyClass`, the budget+region admission logic in
  `arbiter`, the `FixedTrees`/`WholeWorkspace` region sources, the lease `class`/
  `payload` fields. The arbiter stays domain-agnostic — it never names `apply` or
  `TM`.
- **Host (job) owns:** the `TopPickablePlan` region source's *inputs* — the plan
  pool and `derive_tree` (today `_load_plan_pool` + `_derive_tree_for_plan` in
  `fanout_state.py`) — passed into the arbiter, exactly as the auto-pick ladder is
  passed in today. The `priority` `max_concurrent` default + override key live in
  the host's `execution-state.yaml` (`priority_slots:` or a `[[concurrency_class]]`
  table).

---

## See also

- [`96_from-plan-lanes-to-concurrency-classes.md`](96_from-plan-lanes-to-concurrency-classes.md)
  — the worked lesson / why this exists (read first).
- [`89_the-lane-is-a-region-lock.md`](89_the-lane-is-a-region-lock.md) — the
  primitive; §4.2 forward-points at exactly this ("a richer concurrency model on
  the same disjointness gate"), §4.4 is the litmus this design passes.
- [`73_admission-predicate-plan.md`](73_admission-predicate-plan.md) — the
  pluggable admission predicates the class budget composes with.
- Job-side stop-gaps that bought time for this plan:
  `scripts/fanout_state.py` (`_walk_priority_ladder` cluster-member defocus) and
  `scripts/dispatch_lane_health.py` (pre-dispatch health gate).
