# How do I price a parallel agent fan-out before launching it?

> Compute the collision graph from the file-trees the fan-out would hand its workers, *before* agent #1 runs: `pip install dos-kernel`, then run the `examples/plan_price/` oracle (the forward sibling of `dos arbitrate`). The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter; never install that.

## The short answer

Don't trust the planner's confidence that a fan-out is disjoint — check the geometry the planner didn't get to author. A proposed N-agent partition is just a set of file-trees, one per worker. Whether two of those trees overlap is a fact about glob prefixes, decidable without believing any agent's intent. The `examples/plan_price/` oracle stands on exactly that fact: it feeds each pair of proposed trees through the shipped kernel predicate `dos._tree.lane_trees_disjoint` — the same agent-blind geometry the reactive arbiter already trusts — and returns the collision graph, the true collision-free maximum concurrency, an expected-rework price, and the cheapest disjoint re-partition.

This is the predictive flip (docs/347): DOS today is a *court*. `verify` reads a commit that already shipped, `arbitrate` refuses a colliding acquire *as* the Kth agent reaches the turnstile — it rules after, or as, the effect is attempted. `plan_price` asks the strictly forward question off the same pure predicate: given the trees a fan-out *would* hand its workers, what collides? It costs zero new trust — it is the un-forgeable disjointness check fired one fan-out earlier. The reactive court launches agents in declared order and only discovers a hidden overlap once the colliding acquire arrives, by which point earlier agents are already mutating the tree; the forward price refuses the whole bad plan with zero agents launched.

## The evidence

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| The forward price stands on the SAME predicate the live arbiter trusts, not a private reimplementation | zero new trust — imports `dos._tree.lane_trees_disjoint` directly | the prefix geometry of the declared trees; no agent authors a tree's overlap | [`examples/plan_price/plan_price.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/plan_price/plan_price.py) |
| A hidden collision the reactive court only catches mid-launch is priced before agent #1 runs | on `hidden_collision`: court launches **2** agents before it can refuse; predictive cost **0** launched; max safe concurrency **3 of 4** | the collision graph computed from the proposed trees, not any worker's report | [`docs/347`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/347_the-predictive-flip-from-court-to-pricing-oracle.md) |
| A truly disjoint fan-out prices to nothing — the flip adds no friction in the light | `clean_fanout`: **0** collisions, full concurrency, no re-partition | the disjointness of the four declared trees | [`examples/plan_price/README.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/plan_price/README.md) |

The collision-free maximum concurrency is a plan-level quantity the reactive arbiter never computes — it only ever sees one acquire against the leases already live, never the whole proposed set at once.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
python examples/plan_price/plan_price.py --json   # machine-readable prices, exit 0
```

The oracle prices three proposed fan-outs and emits the collision graph, the max safe concurrency, and the cheapest re-partition for each:

```text
{
  "hidden_collision": {
    "n_agents": 4,
    "collisions": [["feature_a", "refactor"]],
    "max_concurrency": 3,
    "safe_now": ["feature_a", "feature_b", "tests"],
    "reactive_cost": 2,
    "predictive_cost": 0,
    "repartition": {"feature_a": "src/core/api/_feature_a/**", "refactor": "src/_refactor/**"}
  }
}
```

The reactive court would launch 2 agents before it could refuse this hidden overlap; the forward price refuses the whole plan with 0 launched, names the colliding pair, and still hands back the maximal safe set to run. Same un-forgeable geometry, one fan-out earlier.

## What this does — and does not — certify

The price is **geometric, not semantic** — it prices file-tree *overlap*, the same ceiling the arbiter has. Two provably-disjoint trees can still collide in meaning; the forward price inherits that boundary exactly, just one tense earlier. The declared partition is itself **agent-authored and forgeable**: a plan can under-declare its scope, and the price can only score the geometry it was handed (an empty tree is conservatively priced as colliding-with-everything — "name your scope before launch"). The acquire-time arbiter over real leases stays the un-forgeable floor *after* launch; the price is a forward *estimate* that reduces collisions, not a BLOCK guarantee. The rework number is illustrative — one unit per colliding pair, transparent and monotone in contention, deliberately not a calibrated cost model. And this is an **experiment, not a shipped `dos` verb**: it lives in `examples/`, imports the kernel, and is pinned by tests; promoting it to a real picker-family verb is the follow-on.

## Sources / reproduce

- [`examples/plan_price/plan_price.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/plan_price/plan_price.py) — the oracle: prices a proposed N-agent partition off the real kernel predicate.
- [`examples/plan_price/README.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/plan_price/README.md) — the runnable proof, standing on `dos._tree.lane_trees_disjoint` at zero new trust.
- [`docs/347`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/347_the-predictive-flip-from-court-to-pricing-oracle.md) — the design: flip the court into a forward pricing oracle.
- [How to stop two AI agents overwriting each other](how-to-stop-two-ai-agents-overwriting-each-other.md) — the reactive arbiter this prices forward.
- [Lease-based file locking for parallel agents](lease-based-file-locking-for-parallel-agents.md) — the acquire-time floor the forward price estimates ahead of.
- [FAQ](../FAQ.md) — the trust thesis and the rest of the corpus.

## Also asked as

- How do I know if a parallel agent plan will have collisions before I run it?
- Estimate the cost of a multi-agent fan-out before launch
- Which of my N proposed agents can actually run at once without overwriting each other?
- Check a parallel agent partition for file-tree overlap ahead of time
- Predict agent collisions before dispatching the fleet
- How many concurrent agents can I safely run on this repo?
- Price a multi-agent dispatch plan up front
- Catch a hidden collision in a fan-out before any agent starts writing

> The kernel is the part that doesn't believe the agents.
