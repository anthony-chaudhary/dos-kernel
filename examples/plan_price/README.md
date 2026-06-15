# plan_price — price a proposed fan-out before any agent runs

The runnable proof for **[docs/347 — the predictive flip](../../docs/347_the-predictive-flip-from-court-to-pricing-oracle.md)**.

DOS today is a **court**: `verify` reads a commit that already shipped, `arbitrate`
refuses a colliding acquire *as* each agent reaches the turnstile. It rules after, or
as, the effect is attempted. This example shows the kernel's own pure geometry can
answer a strictly **forward** question the court shape never asks:

> Given a proposed N-agent partition — the file-trees a fan-out would hand its workers
> *before launch* — what is the collision graph, the true collision-free maximum
> concurrency, the expected rework, and the cheapest disjoint re-partition?

The whole point is that this needs **zero new trust**. It stands entirely on the
shipped, agent-blind predicate `dos._tree.lane_trees_disjoint` — the same geometry the
reactive arbiter already trusts. No agent authors a file-tree's *geometry*; the prefix
algebra decides overlap without believing anyone. So the predictive price is exactly as
un-forgeable as the reactive verdict, available one fan-out earlier.

## Run

```bash
# from the repo root, with the kernel importable (editable install or PYTHONPATH=src)
python examples/plan_price/plan_price.py          # the rendered price table
python examples/plan_price/plan_price.py --json   # machine-readable prices
python -m pytest examples/plan_price/test_plan_price.py -q   # the falsifiers (green)
```

## What it proves

| scenario | the point it makes |
|---|---|
| `clean_fanout` | The **falsifier**: a truly disjoint partition prices to 0. The flip adds no friction in the light. |
| `hidden_collision` | The **headline**: the reactive court launches 2 agents before it can refuse a hidden overlap; the predictive price refuses the whole plan with 0 launched, and still hands back the maximal safe set to run. |
| `under_declared` | An empty tree (unknown blast radius) is priced as colliding-with-everything — "name your scope before launch," a thing no post-hoc `verify` can say because there is no past-tense effect to read. |

## Honest limits (full list in docs/347 §5)

- **Geometric, not semantic** — it prices file-tree overlap, the same ceiling the arbiter
  has; two disjoint trees can still collide semantically.
- **Over a declared (forgeable) partition** — the trees are a plan; the price is a forward
  estimate, not the unforgeable acquire-time floor.
- **Illustrative rework number** — one unit per colliding pair, not a calibrated cost model.
- **An experiment, not a shipped verb** — promoting it to `dos price-plan` is the follow-on
  (see the issues filed from docs/347).
