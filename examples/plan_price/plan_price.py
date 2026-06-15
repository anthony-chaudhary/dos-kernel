#!/usr/bin/env python3
"""plan_price — price a PROPOSED fan-out before any agent runs.

The flip (docs/347). The kernel today adjudicates effects that already landed:
`verify` reads the past, `arbitrate` refuses a colliding acquire one lease at a
time, AS each agent reaches the turnstile. That is a *court* — it rules after (or
as) the write is attempted. This script proves the kernel's own pure geometry can
answer a strictly FORWARD question the court shape never asks:

    Given a proposed N-agent partition of the workspace — the file-trees a fan-out
    would hand its workers BEFORE launch — what is the collision graph, the true
    collision-free maximum concurrency, the expected rework price, and the cheapest
    disjoint re-partition?

The whole point: this needs ZERO new trust. It stands entirely on the shipped,
agent-blind predicate `dos._tree.lane_trees_disjoint` — the same geometry the
reactive arbiter already trusts. No agent authored a file-tree's GEOMETRY; the
prefix algebra decides overlap without believing anyone. So the predictive price
is exactly as un-forgeable as the reactive verdict, and available a full fan-out
earlier.

Run:  python examples/plan_price/plan_price.py
      python examples/plan_price/plan_price.py --json
"""
from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import dataclass, field

# Stand on the REAL kernel predicate — do not reimplement it. The proof is only
# worth anything if it uses the same geometry the shipped arbiter uses.
from dos._tree import lane_trees_disjoint


@dataclass
class Agent:
    """One proposed worker in a fan-out, with the file-tree it would be handed."""

    name: str
    tree: list[str]


@dataclass
class PlanPrice:
    """The forward-looking price of a proposed partition — all pure geometry."""

    n_agents: int
    collisions: list[tuple[str, str]]  # pairs that provably overlap
    max_concurrency: int  # largest set that can run at once (independent set)
    safe_now: list[str]  # the names in that largest collision-free set
    expected_rework: float  # a simple price; see _expected_rework
    repartition: dict[str, str]  # name -> suggested disjoint subtree, where fixable
    reactive_cost: int  # how many agents the COURT shape would launch before it refuses
    fields: dict = field(default_factory=dict)


def _pairwise_collisions(agents: list[Agent]) -> list[tuple[str, str]]:
    """Every pair whose trees are NOT provably disjoint — straight from the kernel.

    `lane_trees_disjoint` is conservative: an empty/unknown tree is NOT disjoint,
    so an under-declared scope counts as a collision. That is the right default for
    a price — an unknown blast radius is a risk, not a free pass.
    """
    out: list[tuple[str, str]] = []
    for a, b in itertools.combinations(agents, 2):
        if not lane_trees_disjoint(a.tree, b.tree):
            out.append((a.name, b.name))
    return out


def _max_independent_set(names: list[str], edges: list[tuple[str, str]]) -> list[str]:
    """Largest set of agents that pairwise do NOT collide — the true max concurrency.

    This is the maximum-independent-set on the collision graph. It is the honest
    answer to "how many of these N can actually run at once," which the reactive
    arbiter never computes because it only ever sees one acquire against the leases
    already live. Exact via brute force (fan-outs are small — tens, not thousands);
    a real kernel verb would swap in a greedy/branch-and-bound bound past ~30.
    """
    adj = {n: set() for n in names}
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    best: list[str] = []
    # Try every subset largest-first; first independent one wins. Fine for small N.
    for size in range(len(names), 0, -1):
        for combo in itertools.combinations(names, size):
            s = set(combo)
            if all(not (adj[n] & s) for n in combo):
                return list(combo)
        if best:
            break
    return best or (names[:1] if names else [])


def _expected_rework(n_agents: int, collisions: list[tuple[str, str]]) -> float:
    """A transparent price: expected colliding-pairs cost, normalized per agent.

    Deliberately simple and honest — one rework unit per colliding pair. The point
    of the experiment is NOT a calibrated dollar model; it is to show the price is
    *computable from geometry before launch* and *monotone in contention*, which is
    the property the reactive court cannot deliver pre-launch.
    """
    if n_agents <= 1:
        return 0.0
    return round(len(collisions) / n_agents, 4)


def _cheapest_repartition(agents: list[Agent], collisions: list[tuple[str, str]]) -> dict[str, str]:
    """For each colliding agent, suggest the narrowest subtree that would clear it.

    The cheapest fix for an empty/over-broad tree is to NAME a concrete subtree.
    We can't know the agent's true intent, so we surface the SHAPE of the fix (a
    per-agent placeholder subtree) — the action a planner takes to make the
    partition provably disjoint. Honest about its limit: it proposes geometry, not
    semantics (it cannot know which subtree the agent actually needs)."""
    colliding = {n for pair in collisions for n in pair}
    out: dict[str, str] = {}
    for a in agents:
        if a.name in colliding:
            # The narrowest non-colliding scope is a private subtree keyed by name.
            out[a.name] = f"{(a.tree[0].split('*')[0].rstrip('/') if a.tree else 'workspace')}/_{a.name}/**"
    return out


def _reactive_cost(agents: list[Agent]) -> int:
    """How many agents the COURT shape launches before it discovers the collision.

    Simulate the reactive arbiter: admit agents one at a time (in declared order);
    each new acquire is checked against the trees ALREADY admitted. The first one
    that collides is REFUSED — but by then every prior agent has already launched
    and is mutating the tree. The 'reactive cost' is how many agents were committed
    before the kernel could say no. The predictive price pays ZERO of this — it
    refuses the whole bad plan before agent #1 starts.
    """
    admitted: list[Agent] = []
    for a in agents:
        if all(lane_trees_disjoint(a.tree, prev.tree) for prev in admitted):
            admitted.append(a)
        else:
            # Collision found — but `len(admitted)` agents are already running.
            return len(admitted)
    return len(admitted)


def price_plan(agents: list[Agent]) -> PlanPrice:
    names = [a.name for a in agents]
    collisions = _pairwise_collisions(agents)
    safe = _max_independent_set(names, collisions)
    return PlanPrice(
        n_agents=len(agents),
        collisions=collisions,
        max_concurrency=len(safe),
        safe_now=safe,
        expected_rework=_expected_rework(len(agents), collisions),
        repartition=_cheapest_repartition(agents, collisions),
        reactive_cost=_reactive_cost(agents),
    )


# ---------------------------------------------------------------------------
# Three scenarios that make the flip's value concrete.
# ---------------------------------------------------------------------------
SCENARIOS: dict[str, list[Agent]] = {
    # A clean fan-out — disjoint by construction. Price should be ~0; the flip
    # adds no friction in the light (the falsifier: when truly disjoint, lift = 0).
    "clean_fanout": [
        Agent("auth", ["src/auth/**"]),
        Agent("billing", ["src/billing/**"]),
        Agent("ui", ["web/ui/**"]),
        Agent("docs", ["docs/**"]),
    ],
    # The dangerous case: a plausible-looking fan-out with a HIDDEN collision the
    # planner didn't see — two agents both reach into src/core/**. The reactive
    # court launches several agents before it trips; the price flags it pre-launch.
    "hidden_collision": [
        Agent("feature_a", ["src/core/api/**", "src/feature_a/**"]),
        Agent("feature_b", ["src/feature_b/**"]),
        Agent("refactor", ["src/core/**"]),  # collides with feature_a via src/core/
        Agent("tests", ["tests/**"]),
    ],
    # The under-declared case: one agent ships an EMPTY tree (unknown blast radius).
    # The kernel's conservatism makes it collide with everything — the price says
    # "name your scope before you launch," which a post-hoc verify never could.
    "under_declared": [
        Agent("worker_1", ["src/a/**"]),
        Agent("worker_2", []),  # empty — unknown blast radius
        Agent("worker_3", ["src/c/**"]),
    ],
}


def _render(name: str, price: PlanPrice) -> str:
    lines = [f"== {name} =="]
    lines.append(f"  agents proposed:        {price.n_agents}")
    lines.append(f"  provable collisions:    {len(price.collisions)}  {price.collisions or ''}")
    lines.append(f"  max safe concurrency:   {price.max_concurrency}  (run now: {price.safe_now})")
    lines.append(f"  expected rework price:  {price.expected_rework}")
    lines.append(
        f"  reactive court cost:    {price.reactive_cost} agent(s) launch before the "
        f"collision is found"
    )
    lines.append(
        "  predictive flip cost:   0 agent(s) launch — the bad plan is priced before #1 runs"
    )
    if price.repartition:
        lines.append("  cheapest re-partition:")
        for n, sub in price.repartition.items():
            lines.append(f"      {n:12s} -> {sub}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit machine-readable prices")
    args = ap.parse_args(argv)

    priced = {name: price_plan(agents) for name, agents in SCENARIOS.items()}

    if args.json:
        out = {
            name: {
                "n_agents": p.n_agents,
                "collisions": p.collisions,
                "max_concurrency": p.max_concurrency,
                "safe_now": p.safe_now,
                "expected_rework": p.expected_rework,
                "reactive_cost": p.reactive_cost,
                "predictive_cost": 0,
                "repartition": p.repartition,
            }
            for name, p in priced.items()
        }
        print(json.dumps(out, indent=2))
        return 0

    print(
        "plan_price — pricing a PROPOSED fan-out from the kernel's own geometry,\n"
        "before any agent runs (docs/347, the predictive flip).\n"
    )
    for name, p in priced.items():
        print(_render(name, p))
        print()

    # The headline the experiment exists to print: the gap between court and oracle.
    hc = priced["hidden_collision"]
    print(
        f"HEADLINE: on 'hidden_collision', the reactive court launches "
        f"{hc.reactive_cost} agent(s) before it can refuse;\n"
        f"          the predictive price refuses the same plan with 0 agents launched.\n"
        f"          Same geometry, same un-forgeable predicate — one fan-out earlier."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
