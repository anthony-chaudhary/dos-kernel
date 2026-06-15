# 110 — The concurrency-class operator surface: make the class budget reachable, and declarable as data

> **Status:** PLAN (not yet built). The docs/97 Phase-1 claim-budget *admission
> step* already landed in the **pure arbiter** — `arbitrate(...,
> class_budgets={"priority": 3})` takes the per-kind budgets
> (`src/dos/arbiter.py:159`), counts live leases per kind on the auto-pick walk
> and skips a budget-exhausted candidate (`arbiter.py:329-348`, gate at
> `:585-594`), and returns the named `CLASS_BUDGET_EXHAUSTED` refuse
> (`arbiter.py:630-655`), pinned by `tests/test_arbiter.py:429`
> (`TestConcurrencyClassBudget`). But that budget is reachable **only as a Python
> parameter**: `class_budgets` appears NOWHERE in `src/dos/cli.py` (the arbitrate
> call at `cli.py:658-666` passes no `auto_pick_order` and no `class_budgets`) and
> NOWHERE in `src/dos/config.py` — there is no `dos arbitrate --class-budget K=N`
> flag and no `[[concurrency_class]]` `dos.toml` table. This plan is the
> **operator-surface slice** docs/97 Phase 3 gestures at but never details: it
> wires the already-landed kernel mechanism out to the operator ABI and lifts the
> budget into the config seam **as declared data** — without moving one byte of
> *policy* into the kernel. The kernel keeps MINTING the budget verdict from the
> live-lease count it is *handed*; the new surfaces only **PROPOSE** a budget (a
> CLI flag, a `dos.toml` value) and gather the leases at the boundary — the
> evidence-at-the-boundary rule (`arbitrate` is state-in/decision-out,
> `cli.py:612-620`). It still never executes, never names a host lane, never
> imports a driver.

This plan is one of a **family** of admission-mechanism plans:
[`97`](97_concurrency-class-model-plan.md) is the **parent** (the class registry /
`ConcurrencyClass` / `RegionSource` model whose Phase 1 budget gate this surfaces),
[`96`](96_from-plan-lanes-to-concurrency-classes.md) is the worked lesson (the
2026-06-01 plan-lane wedge that motivated the class model), [`89`](89_the-lane-is-a-region-lock.md)
is the primitive (a lane is a leased region-lock, not a swim-lane), and
[`91`](91_value-aware-picker-plan.md) is the sibling auto-pick knob already wired
through the arbiter the same way this one will be (`rank_key`,
`arbiter.py:157`). The genericize-as-data move is the same one
[`70-73`](73_admission-predicate-plan.md) ran for predicates and the WCR series
ran for `[lanes]`/`[paths]`/`[stamp]` (closed-set → declared data, `docs/HACKING.md`).

---

## Problem (one line)

The docs/97 Phase-1 concurrency-class budget is live in the pure arbiter but
**unreachable** — no `dos arbitrate` operator can ask for it, and no workspace can
declare its budget classes as data, so the one safe N-way-concurrency knob the
kernel already has is dead weight.

## Disambiguate the three "claim" concepts first

This plan touches the word "claim" in three senses that must not be conflated —
they live on three different rungs of trust and three different modules:

| "claim" | What it is | Module | Trust |
|---|---|---|---|
| **lease region-claim** | a live holder of a leased *region* (a lane's tree), the thing `arbitrate` admits/refuses and the WAL records | `arbiter` + `lane_journal` | **adjudicated** — the kernel grants it, disjointness-checked |
| **intent-ledger `STEP_CLAIMED`** | the agent's *say-so* that it finished a unit of work — content, distrusted, never closes a step until a `STEP_VERIFIED` matches it on the non-forgeable rung | `intent_ledger` (`OP_STEP_CLAIMED`, `intent_ledger.py:82`) | **forgeable** — adjudicated against git ancestry by `resume` |
| **host soft-claim** (`DROP_SOFT_CLAIMED` / `STALE_CLAIM`) | a *fanout tag* holding a TTL'd reservation on a renderable pick in `execution-state.yaml`, a sibling-coordination convention that lives **host-side** | `gate_classify.py:72` (`DROP_SOFT_CLAIMED`), `picker_oracle.py:132` (`STALE_CLAIM`) | **host policy** — a reservation the host honors, not a kernel lease |

This plan is about the **first** sense — the leased region-claim and its
*concurrency budget* — which is exactly the "generic claim arbitration thing"
operator note 3 names. Phase 3 below reaches toward the **third** (the host
soft-claim), but does NOT pull the soft-claim's *pool* into the kernel: it lifts
only the **budget + region-admission** for "priority work" into the class, leaving
the plan-pool and the TTL/staleness reservation host-side. The **second** sense
(`STEP_CLAIMED`) is untouched here — it is `resume`'s axis (docs/107), named only
so a reader does not mistake a budgeted *priority lease* for a ledger *step claim*.

## Goal

Make the docs/97 class budget a first-class operator surface in two additive,
back-compat moves, then take the first real step of the docs/97 Phase-3 lift:

1. an operator can pass `dos arbitrate --class-budget priority=3` and the budget
   threads straight into the existing arbiter parameter;
2. a workspace can DECLARE its budget classes in `dos.toml`
   (`[[concurrency_class]]`), and the CLI reads them as the default budget set —
   the "generic claim arbitration as data" lift;
3. the host's "priority work" routing is re-expressed as a declared `priority`
   class whose region comes from a `TopPickablePlan` `RegionSource` — the plan
   pool stays host-side (passed in, exactly as `auto_pick_order` is today), but
   the **budget + region admission** move into the kernel's class machinery.

Every step is **byte-identical when its surface is absent**: no `--class-budget`,
no `[[concurrency_class]]`, and no `RegionSource` reproduces today's arbitration
exactly (`class_budgets=None`, the regression floor proven by
`tests/test_arbiter.py:453`).

---

## The model (what already exists, and what this adds)

The kernel half is **done**. `arbitrate` already:

- accepts `class_budgets: dict[str, int] | None` (`arbiter.py:159`);
- normalizes it once — drops non-positive / non-int / `bool` budgets so a garbled
  `0` cannot silently wedge a whole class (`arbiter.py:328-332`);
- counts live leases per `lane_kind` over the passed-in `live_leases`
  (`arbiter.py:333-337`), and `_budget_exhausted(kind)` returns
  `count >= cap` (`arbiter.py:339-348`);
- gates the bare auto-pick walk on `_budget_exhausted(kind or "cluster")` BEFORE
  the disjointness check, so it never mints an `(N+1)`-th holder of a budgeted
  class (`arbiter.py:585-594`);
- refuses with the named `CLASS_BUDGET_EXHAUSTED` token, distinct from
  drain/ladder-exhausted, naming the at-budget classes as `kind (n/cap)` and the
  lever ("wait for a holder to release — do NOT /replan") (`arbiter.py:630-655`).

The budget keys on **`lane_kind`** — the same string `--kind` sets as
`requested_kind` (`cli.py:660`) and the same key a live-lease dict carries
(`lane_kind`, `cli.py:630`). So a class IS a `lane_kind` with a budget. This plan
adds, above that finished mechanism:

```
ConcurrencyClass:            # config.py — pure data, names no host lane
  name           : str       # the lane_kind this class budgets (e.g. "priority")
  max_concurrent : int       # the budget → class_budgets[name] = max_concurrent
  region_source  : RegionSource | None   # Phase 3 only; None = "kind already in the ladder"
  rank           : int       # auto-pick order (Phase 3; mirrors docs/97 rank)

RegionSource (one of):       # config.py — the docs/97 §model sources
  FixedTrees(trees)          # clusters / named — a closed menu (already config data)
  WholeWorkspace()           # exclusive — the coarsest lock
  TopPickablePlan(pool, derive_tree)   # priority — open set, disjoint-checked;
                                       #   pool + derive are HOST INPUTS, passed in
```

`ConcurrencyClass` is the docs/97 `ConcurrencyClass` shape, scoped down to what
the *operator surface* needs: `{name, max_concurrent}` is enough to build the
`class_budgets` dict the kernel already consumes; `{region_source, rank}` are
Phase-3 fields that route the `priority` class and are inert until then. The
arbiter signature does **not** change — `ConcurrencyClass` is reduced to the two
primitive arguments the arbiter already takes (`class_budgets` for the budget,
`auto_pick_order` for the ladder) at the CLI/host boundary. The kernel stays
mechanism; the class registry is config data the boundary *projects into*
existing pure parameters.

---

## Phases (throughline-first — each ships an enabled slice, smallest-first, behind the old behavior)

> **Status (2026-06-03): none of the three phases below is built.** The arbiter
> mechanism they surface is built (Phase-1 of docs/97); the surfaces are not.
>
> **Update (2026-06-15): Phases 1-2 SHIPPED, Phase 3's kernel half SHIPPED.**
> Phase 1 (`--class-budget KIND=N`) and Phase 2 (`[[concurrency_class]]` +
> `dos.concurrency_class`) are built and pinned (`tests/test_concurrency_class.py`).
> Phase 3's *kernel half* is built — the `RegionSource` model
> (`FixedTrees`/`WholeWorkspace`/`TopPickablePlanKind` + `TopPickablePlanBinding`),
> the `ConcurrencyClass.rank`/`region_source` fields, the `NO_FREE_REGION` refuse,
> and the call-boundary `project_to_ladder` that expands an open pool into the
> existing `auto_pick_order` (the arbiter signature was **kept unchanged**, the
> seam this plan's §model chose). The full docs/97 obligation suite drives through
> the projection (`tests/test_arbiter.py::TestOpenPoolRegionRouting`). What remains
> is the *host half*: a reference driver that builds a `TopPickablePlanBinding`
> from a host plan pool, and the optional lazy in-arbiter resolver — tracked as a
> backlog issue (see Phase 3's status note below). MCP parity for the budget
> shipped alongside (`dos_arbitrate(class_budgets=...)`).

### Phase 1 — the `--class-budget KIND=N` operator flag (the smallest reachable slice)

Add a repeatable `--class-budget KIND=N` flag to the `dos arbitrate` subparser
(`cli.py:3143-3159`) and thread the parsed dict into the existing
`arbiter.arbitrate(..., class_budgets=...)` call (`cli.py:658-666`).

- **Parse:** `--class-budget priority=3 --class-budget maintenance=1` →
  `{"priority": 3, "maintenance": 1}`. A value that is not `KIND=<positive-int>`
  is **operator error** — print a clean one-line message and exit on the
  contract-error code (`_ARBITRATE_EXIT_CODES["contract_error"]`, 2), the same
  posture `--leases` malformed-JSON already uses (`cli.py:627-637`). Do NOT
  pre-normalize away a `0`/negative on the CLI side beyond rejecting non-ints —
  the arbiter already drops non-positive budgets defensively (`arbiter.py:331`),
  but the *operator-facing* contract is "a budget is a positive int," so reject it
  loudly here rather than silently dropping it.
- **Thread:** pass `class_budgets=<parsed or None>` into BOTH the primary
  `arbitrate` call (`cli.py:658`) AND the `--force` unforced re-run
  (`cli.py:718-722`) — the unforced re-run must arbitrate against the same world,
  or a `--force` over a budget-refusal would mis-record the resolved decision.
- **Reachability gap closed:** without `auto_pick_order` wired (Phase 3), the
  budget gate fires only on the bare auto-pick walk — and `cli.py` passes no
  `auto_pick_order`, so the *kernel-repo* `dos arbitrate` walk uses the
  cluster/named legacy path, where the budget is keyed on `lane_kind`. Phase 1's
  honest scope: the flag is **plumbed and tested at the ABI** (the dict reaches
  the arbiter unchanged), and it bites on any path that supplies an
  `auto_pick_order` with budgeted kinds — which today is the host (job) and the
  Phase-3 `TopPickablePlan` route. State this limitation in `--help`: the flag
  caps a *kind*'s live leases; it only changes a decision when a budgeted kind is
  on the auto-pick ladder.

This touches the **operator ABI**, so it carries the docs/97 *Test obligations*
verbatim, now asserted through the CLI rather than the Python call:

- **no-collision:** two concurrent budgeted grabs return disjoint regions;
- **budget:** the `(N+1)`-th grab refuses `CLASS_BUDGET_EXHAUSTED`;
- **reachability:** a budgeted kind never starves a non-budgeted free lane;
- **byte-identical-when-absent:** `dos arbitrate` with no `--class-budget`
  produces the exact decision it produces today (the regression floor).

Ship + soak.

### Phase 2 — `[[concurrency_class]]` in `dos.toml` (the "generic claim arbitration as data" lift)

Add a `ConcurrencyClass` model + a `concurrency_classes` field to
`SubstrateConfig` (`config.py:513-519`), and a `[[concurrency_class]]` array-table
loader that mirrors the existing seam loaders (`reasons.specs_from_table` /
`stamp.convention_from_table` / `LaneTaxonomy.from_table`, `config.py:99-193`):

```toml
[[concurrency_class]]
name = "priority"
max_concurrent = 3

[[concurrency_class]]
name = "maintenance"
max_concurrent = 1
```

- **Model:** a frozen `ConcurrencyClass` dataclass in `config.py`
  (`name: str`, `max_concurrent: int`, plus the Phase-3 `region_source`/`rank`
  defaulting to `None`/a sentinel so Phase 2 declares only the budget). A
  `classmethod from_table(table)` that is **loud on malformed** — a non-int
  `max_concurrent`, a missing `name`, a non-positive budget all raise a
  `ValueError` naming the offending class, exactly like
  `LaneTaxonomy.from_table` (`config.py:132-185`). This is a *value* that names no
  host lane — Law 1 holds: a TOML-declared class is pure workspace data.
- **Load:** a `load_concurrency_classes_from_toml(toml_path, base=...)` and a
  `_layer("concurrency_class", ...)` line in `config_for_workspace`
  (`config.py:899-921`), warned-and-fall-back on malformed like its siblings, so a
  `verify` with a broken `[[concurrency_class]]` does not crash. Precedence
  matches the others: a present table REPLACES the base set (it is not additive —
  the same asymmetry `[lanes]` has, `config.py:861-864`).
- **CLI default:** `cmd_arbitrate` derives the default `class_budgets` from
  `cfg.concurrency_classes` (`{c.name: c.max_concurrent for c in ...}`). The
  Phase-1 `--class-budget` flag, when present, **OVERRIDES** the declared default
  (the same flag-over-config precedence `--leases` has over the live WAL,
  `cli.py:621-624`); absent, the declared classes are the budget. A workspace with
  no `[[concurrency_class]]` table has an empty default → `class_budgets=None` →
  byte-identical to today.
- **Surface it in `doctor`:** add a "concurrency classes" row to the `dos doctor`
  taxonomy report so the operator can SEE the declared budgets, the same way the
  lane taxonomy is shown.

This is the move that makes the kernel's budget a **declared, hackable** policy —
the real "generic claim arbitration thing." Test obligations: the malformed-table
warn-and-keep-base path (the seam-loader contract), declared-budget→decision (a
declared `priority=1` refuses the 2nd priority grab with no flag), and
flag-overrides-declared.

### Phase 3 — lift the host "priority work" into a declared `priority` class via `TopPickablePlan`

> **Status (2026-06-15): the kernel half is SHIPPED; the host half is the
> remainder.** Built: the `RegionSource` discriminant set
> (`FixedTrees`/`WholeWorkspace`/`TopPickablePlanKind`) plus the call-boundary
> `TopPickablePlanBinding{pool, derive_tree}` in `dos.concurrency_class` — note
> the design's single `TopPickablePlan(pool, derive_tree)` was **split** into a
> TOML-declarable discriminant (`TopPickablePlanKind`, what `region_source =
> "top_pickable_plan"` parses to) and a call-boundary binding (the callables,
> which a frozen TOML-loadable type cannot hold). The `ConcurrencyClass.rank` /
> `region_source` fields, the `NO_FREE_REGION` refuse, and `project_to_ladder`
> (the host-boundary projection into the existing `auto_pick_order`) are built and
> pinned by `tests/test_arbiter.py::TestOpenPoolRegionRouting`. The arbiter
> signature was kept UNCHANGED — the projection seam (this plan's §model) was
> chosen over an in-arbiter resolver. **Remaining (a backlog issue):** a reference
> host driver that builds a `TopPickablePlanBinding` from a real plan pool +
> `derive_tree`, and the optional lazy in-arbiter `region_sources` resolver (value
> only for a very large pool). The soft-claim TTL reservation stays host policy as
> written below.

The first real step of the docs/97 Phase-3 lift, and the only one that touches the
host soft-claim concept. Today the host's "priority work" is two host-side things:
the **plan pool** + **`derive_tree`** (the reference app's `_load_plan_pool` /
`_derive_tree_for_plan`, named in docs/97 §Boundary) and the **soft-claim**
TTL reservation (`gate_classify.DROP_SOFT_CLAIMED` / `picker_oracle.STALE_CLAIM`).
This phase moves the **budget + region admission** into the kernel's class
machinery while leaving the pool host-side:

- Add the `RegionSource` ADT to `config.py`: `FixedTrees(trees)` (a closed menu —
  what `[lanes.trees]` already is), `WholeWorkspace()` (the exclusive coarse
  lock), and `TopPickablePlan(pool, derive_tree)` (an open, disjoint-checked set).
  The first two are pure config data; `TopPickablePlan` carries two **callables**
  that are **host inputs** — the kernel never constructs them, it is handed them
  at the boundary, exactly as `auto_pick_order` / `rank_key` / `pick_oracle` are
  handed in today (`arbiter.py:152-157`).
- A `[[concurrency_class]]` with `region_source = "top_pickable_plan"` declares
  the `priority` class's budget and rank as data; the *callables* are supplied by
  the host driver when it builds the `auto_pick_order` it passes to `arbitrate`
  (the host already builds this ladder). The arbiter signature is **unchanged** —
  the host projects `TopPickablePlan` into the existing `auto_pick_order` (one
  `(plan_id, "priority", derived_tree)` tuple per pickable plan) and the existing
  `class_budgets={"priority": N}`. The kernel's budget gate (`arbiter.py:585-594`)
  then enforces N-way priority concurrency over disjoint regions — the docs/97
  Phase-1 no-collision + budget invariants, now reached through the *declared*
  class.
- **The soft-claim stays host policy.** The TTL'd `DROP_SOFT_CLAIMED` reservation
  and the `STALE_CLAIM` staleness rung are a host coordination convention over
  `execution-state.yaml`; per CLAUDE.md (phased-plan concepts are NOT in this
  package) they do NOT move. What moves is the *concurrency budget on the priority
  region* — the part that is mechanism (a leased-region budget), not the part that
  is host workflow (which picks are reservable, for how long). The kernel learns
  "at most N priority leases over disjoint regions"; the host keeps "which plans
  are pickable and how long a reservation lives." The clean cut: a live *lease*
  (kernel, adjudicated) supersedes a host *soft-claim* (host, advisory) — the
  budget is enforced on the lease, the reservation is enforced host-side.

**Boundary discipline (the litmus this phase must pass):** `TopPickablePlan` may
NOT cause a kernel module to name `priority`, a plan-pool loader, or
`execution-state.yaml`. The callables are opaque; the class `name` is a string
the host declares. A grep of `src/dos/` (outside `drivers/`) for the host pool
loader, a host lane, or `execution-state` returns nothing — the same
kernel-imports-no-host litmus pinned for predicates and skills. This phase is
design-sketched here; its land is gated on Phase 2 shipping and the docs/97 parent
plan's `ConcurrencyClass`/`RegionSource` types being agreed.

---

## Test obligations

Carried from docs/97 §Test obligations, re-asserted at each new surface:

- **Phase 1 (the operator ABI):**
  - `dos arbitrate --class-budget priority=1` with one live `priority` lease →
    exit on the refuse code, `CLASS_BUDGET_EXHAUSTED` in the output, `priority
    (1/1)` named (the budget invariant through the CLI);
  - two concurrent budgeted grabs bind disjoint regions (no-collision);
  - a malformed `--class-budget priority` / `priority=0` / `priority=x` →
    contract-error exit (2), one-line message, no traceback;
  - `dos arbitrate` with **no** `--class-budget` is byte-identical to today (the
    regression floor — the CLI analogue of `tests/test_arbiter.py:453`);
  - the `--force` unforced re-run threads the same `class_budgets` (a forced
    override of a budget refusal records the resolved decision correctly).
- **Phase 2 (the config seam):**
  - a `[[concurrency_class]]` table parses to the right `ConcurrencyClass` set;
  - a malformed table (non-int `max_concurrent`, missing `name`, non-positive
    budget) warns and keeps the base, never crashing a `verify`/`doctor` that does
    not touch the budget (the seam-loader warn-and-fall-back contract);
  - a declared `priority=1` refuses the 2nd priority grab with no flag present;
  - `--class-budget` overrides the declared default (flag-over-config);
  - a workspace with no table → empty default → `class_budgets=None` (byte-identical).
- **Phase 3 (the lift):**
  - N-way priority concurrency over disjoint regions admits N, refuses the
    `(N+1)`-th `CLASS_BUDGET_EXHAUSTED` (the docs/97 budget + no-collision
    invariants over the declared class);
  - **the litmus:** a grep of `src/dos/` (outside `drivers/`) for the host plan
    pool, a host lane, or `execution-state` is empty — `TopPickablePlan`'s
    callables are opaque host inputs; the kernel names no host (Law 1).

---

## Boundary (DOS vs host / what stays a driver)

- **DOS owns** (already, or by this plan): the budget admission logic in
  `arbiter` (built), the `ConcurrencyClass` model + `[[concurrency_class]]` loader
  in `config.py` (Phase 2), the `--class-budget` flag in `cli.py` (Phase 1), the
  `FixedTrees`/`WholeWorkspace` region sources (Phase 3). The arbiter stays
  domain-agnostic — it never names `priority`, `apply`, or `TM`; `priority` is a
  string the **host** declares.
- **The host (driver) owns:** the `TopPickablePlan` region source's *inputs* — the
  plan pool + `derive_tree` callables, passed into the host-built
  `auto_pick_order`, exactly as the auto-pick ladder is passed today
  (`arbiter.py:152`). The `priority` budget's *value* is declared in the host's
  `dos.toml` `[[concurrency_class]]` (or `execution-state.yaml`), read at the
  boundary, handed to the pure arbiter.
- **Stays host policy, NOT lifted:** the soft-claim TTL reservation
  (`gate_classify.DROP_SOFT_CLAIMED`) and the staleness rung
  (`picker_oracle.STALE_CLAIM`, `STALE_CLAIM_THRESHOLD_HOURS`,
  `picker_oracle.py:425`) — a host coordination convention over
  `execution-state.yaml` that CLAUDE.md keeps out of the kernel. This plan lifts
  the *budget on the priority region*, not the *reservation on a renderable pick*.
- **Untouched:** the intent-ledger `STEP_CLAIMED` (`intent_ledger.py:82`) — a
  different "claim" on a different axis (`resume`, docs/107), named here only to
  keep the three claim-concepts apart.

---

## See also

- [`97_concurrency-class-model-plan.md`](97_concurrency-class-model-plan.md) — the
  **parent**: the `ConcurrencyClass` / `RegionSource` model + Phase-1 budget gate
  this plan surfaces. Read its §Phases status block and §Test obligations first.
- [`96_from-plan-lanes-to-concurrency-classes.md`](96_from-plan-lanes-to-concurrency-classes.md)
  — the worked lesson / the 2026-06-01 plan-lane wedge that motivated the class
  model.
- [`89_the-lane-is-a-region-lock.md`](89_the-lane-is-a-region-lock.md) — the
  primitive: a lane is a leased region-lock; §4.4 is the litmus a fixed
  `priority-1/2/3` lane set fails and an anonymous budgeted class passes.
- [`91_value-aware-picker-plan.md`](91_value-aware-picker-plan.md) — the sibling
  auto-pick knob already wired through the arbiter the way `--class-budget` will be
  (`rank_key`, `arbiter.py:157`).
- [`73_admission-predicate-plan.md`](73_admission-predicate-plan.md) +
  `docs/HACKING.md` — the closed-set → declared-data pattern Phase 2 follows.
- [`107_resumable-work-and-the-intent-ledger.md`](107_resumable-work-and-the-intent-ledger.md)
  — the `STEP_CLAIMED` axis, named here only to disambiguate the three "claim"
  concepts.
