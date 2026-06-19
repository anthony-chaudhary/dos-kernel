---
name: dos-plan-price
description: "Price a proposed multi-agent fan-out before launch by computing tree collisions, safe concurrency, and a cheaper disjoint partition. Use before `dos-goal-fleet`, a `dos-next-up` dispatch packet, or any N-agent tree split."
---

# dos-plan-price — price the fan-out before you launch it

> **DOS is a court; this skill asks it to quote a price first.** Every other DOS
> trust surface reads the past or present tense: `verify` reads a commit that
> already shipped, `arbitrate` refuses a colliding acquire *as* the worker reaches
> the turnstile. A court rules after the collision. But the cheapest moment to
> catch a colliding fan-out is *before any worker runs* — and the kernel's own
> file-tree geometry can answer that forward question with **zero new trust**: the
> partition's overlap is a fact about glob prefixes, decidable without believing
> any agent's intent (docs/347, the predictive flip). This skill prices the
> proposed partition so a bad plan is refused with **0 agents launched**, instead
> of the reactive court launching K-1 workers before the Kth acquire collides.

The shape is domain-free: **discover the layout → collect the proposed partition →
price it from the un-authored geometry → act on the price → fall back to the
reactive lease floor.** The *policy* (which lanes exist, where things live) is data
read from `dos doctor --json`, never literals.

## When to use this (and when not)

- **Use it** right before a fan-out: a `dos-goal-fleet` wave, a `dos-next-up`
  packet's dispatch list, or any "launch N agents over these trees" moment. The
  dispatch list IS a proposed partition; price it before you spend the launches.
- **Do not use it** for a single agent (N=1 has no partition to price — the price
  is trivially 0), or as a substitute for `dos arbitrate` at acquire time. The
  price is a forward *estimate*; the lease arbiter is the unforgeable floor. This
  skill runs *ahead* of the floor, it does not replace it.
- **This is a layer-3 helper / screenplay (a PDP, docs/99).** It mints no new
  verdict; it reads the kernel's pure geometry and narrates the price. The
  geometry is the judgment; this skill is the narrator.

## The honest seam — no first-party verb yet (read this before Step 2)

There is **no `dos price-plan` CLI verb today** (it is filed as issue #178). The
un-forgeable primitive the price stands on IS shipped: `dos._tree.lane_trees_disjoint`,
the same agent-blind prefix geometry `dos arbitrate` already trusts. The reference
implementation that lifts that pairwise predicate into a whole-partition price is
the kernel's own experiment, `examples/plan_price/plan_price.py` (a clean
`price_plan(agents) -> PlanPrice` API and a `--json` main; docs/347).

So this skill **shells/imports that example as the interim primitive and emits a
one-line `log` naming the gap** — exactly the docs/345 discipline for a step whose
first-party verb does not exist yet. It never pretends a `dos price-plan` verb is
there. If the example (or the kernel) is absent, the predictive step degrades to
advisory and you fall straight to Step 4 (the `dos arbitrate` floor), which is
always available.

## Step 0 — Discover the workspace once (the WCR on-ramp)

```bash
dos doctor --workspace . --json
```

Stash it. The fields this skill uses:

- `lanes.trees` — the active lane→file-tree map. If a proposed worker is named by a
  lane rather than an explicit tree, resolve its tree from here (never a literal).
- `paths.root` — so declared trees are workspace-relative, comparable by the geometry.
- `git` — informational; the price needs no git history (it reads the *proposed*
  partition, which has no commits yet — that is the whole point).

## Step 1 — Collect the PROPOSED partition

Build the list of agents the fan-out is about to launch, each with the file-tree it
would be handed (globs). The source is whichever planner you are gating:

- A `dos-goal-fleet` wave → one entry per objective, its declared scope.
- A `dos-next-up` packet → one entry per dispatch-list pick, the plan's load-bearing
  files as its tree.
- An operator's hand-written "these N agents over these trees" → as given.

Each entry is `{name, tree: [glob, ...]}`. An agent whose tree you cannot resolve —
an **empty/unknown blast radius** — is kept with an empty tree on purpose: the
geometry will (correctly, conservatively) price it as colliding with everything,
which is the signal "name this worker's scope before you launch it."

## Step 2 — Price the partition BEFORE launch

Compute the price from the un-authored geometry. Shell the interim primitive and
**log the gap** in the same breath:

```bash
# interim — no `dos price-plan` verb yet (#178); the example lifts the shipped
# `dos._tree.lane_trees_disjoint` predicate into a whole-partition price.
python examples/plan_price/plan_price.py --json
```

…or, for your own partition rather than the example scenarios, import the API:

```python
from plan_price import Agent, price_plan          # examples/plan_price/
price = price_plan([Agent(name, tree) for name, tree in proposed])
```

Then emit the gap note so it is surfaced at runtime, not just documented here:

```
log: predictive price is example-backed (examples/plan_price) pending the
     first-party `dos price-plan` verb (#178); the underlying predicate
     (dos._tree.lane_trees_disjoint) is the shipped kernel geometry.
```

The price (`PlanPrice`) carries:

- `collisions` — the proposed pairs that **provably** overlap (one is a prefix of
  the other in the prefix algebra). These are the launches you must not spend yet.
- `max_concurrency` + `safe_now` — the largest set of proposed agents that can run
  at once with no collision (the maximum independent set on the collision graph),
  and their names. This is the honest "how many of these N can actually run
  together" — a number the per-acquire arbiter never computes.
- `repartition` — for each colliding agent, the narrowest private subtree that would
  clear it. A *geometric* suggestion (it cannot know the agent's true intent), so
  treat it as the shape of the fix, not the fix.
- `expected_rework` — a transparent contention price (monotone in collisions);
  ordinal, not a calibrated cost (calibration is #179).

## Step 3 — Act on the price (the court → oracle move)

This is the value. Branch on the price *before* launching:

- **0 collisions** → launch the full fan-out. The price added no friction (the
  falsifier: when the partition is truly disjoint, the lift is exactly 0).
- **≥1 collision** → **do not launch the colliding plan.** Pick one:
  - Launch only `safe_now` this wave (the maximal disjoint set), and hold the
    colliding agents for a later wave; or
  - Apply the `repartition` suggestion (narrow the colliding trees), re-run Step 2,
    and launch once the price is 0.

Either way you have refused the bad plan with **0 agents launched**. Contrast the
reactive court (Step 4): admitting agents one at a time, it only refuses when the
colliding acquire arrives — by which point the earlier agents have already launched
and are mutating the tree. The measured gap (docs/347): on a 4-agent plan with one
hidden overlap, the reactive court launches 2 agents before it can refuse; the price
refuses with 0.

## Step 4 — The reactive floor still applies (EXAMPLES.md R2, `dos arbitrate`)

Pricing the plan does **not** replace the lease floor. Every worker still takes its
lane lease at acquire time — the unforgeable check over the leases *actually held*:

```bash
dos arbitrate --workspace . --lane <lane>      # honor the verdict; redirect on refuse
```

Be explicit about why both exist: the predictive price is a forward estimate over an
**agent-declared (and therefore forgeable) partition** — it reduces wasted launches
and surfaces the collision early, but it is not a guarantee. `dos arbitrate` over the
real WAL leases is the floor that cannot be talked around. Price first to save the
launches; arbitrate at acquire to be safe.

## Step 5 — Return

Print the price and the recommended action, so a caller (`dos-goal-fleet`,
`dos-next-up`) can chain it:

```
Priced <N> agents: <C> collisions, max safe concurrency <M> (run now: <safe_now>).
Action: <LAUNCH_ALL | LAUNCH_SAFE_SET | REPARTITION_AND_REPRICE>.
```

Return the action as the final line so the launch step branches on it, not on prose.

## What this skill deliberately does NOT do (no silent gap)

- **No first-party verb.** It shells `examples/plan_price` because `dos price-plan`
  (#178) is not built. It logs that gap every run rather than faking the verb.
- **No semantic collision detection.** The price is *geometric* — it catches two
  trees that overlap by prefix, not two disjoint trees whose *intents* conflict
  (the docs/347 §5 ceiling, shared with `arbitrate`). It moves collision latency
  earlier; it does not widen what geometry can see.
- **No leasing.** It computes a price; it journals no lease. The lease is `dos
  arbitrate`'s job at acquire time (Step 4). The price is advisory until then.

**Log the gap, never silently skip it.** The first time the skill prices a partition
without the first-party verb, emit the one-line `log` from Step 2 — so the capability
gap is visible at runtime, not just here.

## Anti-patterns

- ❌ Launching a plan the price flagged as colliding "because arbitrate will catch
  it anyway". That is the reactive court — you spend the launches you could have
  saved. Price first; launch the safe set or re-partition.
- ❌ Treating the price as the unforgeable floor and **skipping** `dos arbitrate` at
  acquire. The price is a forward estimate over a forgeable declared partition; the
  lease arbiter is the floor. Do both.
- ❌ Faking a `dos price-plan` call. The verb does not exist yet (#178) — shell the
  example and log the gap.
- ❌ Naming a specific lane or path as a literal — resolve trees from `dos doctor
  --json` (`lanes.trees`).
- ❌ Pricing a single agent. N=1 has no partition; the price is trivially 0.
