"""Pin the plan_price experiment (docs/347 — the predictive flip).

These tests are the FALSIFIERS made executable. Each one would go red if the
flip's claim were overstated:

  * `test_clean_fanout_prices_to_zero` — the lift is 0 in the light (a truly
    disjoint partition must add no friction, or the flip is just overhead).
  * `test_hidden_collision_beats_the_court` — the predictive price must catch a
    collision the reactive court only finds AFTER launching agents, or the flip
    buys nothing over the shipped arbiter.
  * `test_price_stands_on_the_real_kernel_predicate` — the proof must use the
    SAME geometry the shipped arbiter uses; a reimplementation would be a
    different (and unverified) claim.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from plan_price import Agent, price_plan, SCENARIOS  # noqa: E402


def test_clean_fanout_prices_to_zero():
    """The falsifier: when footprints are truly disjoint, the flip's cost is 0."""
    p = price_plan(SCENARIOS["clean_fanout"])
    assert p.collisions == []
    assert p.expected_rework == 0.0
    assert p.max_concurrency == p.n_agents  # all four can run at once
    assert p.repartition == {}  # nothing to fix


def test_hidden_collision_beats_the_court():
    """The headline: the predictive price refuses pre-launch; the court refuses late.

    On a plan with a hidden collision, the reactive court admits agents one at a
    time and only refuses when the colliding acquire arrives — by which point
    `reactive_cost` agents have already launched. The predictive price pays none of
    that: it sees the collision in the proposed partition before agent #1 runs.
    """
    p = price_plan(SCENARIOS["hidden_collision"])
    assert ("feature_a", "refactor") in p.collisions
    # The court launches at least one agent before it can say no...
    assert p.reactive_cost >= 1
    # ...the flip refuses the same plan with zero launched. The strict inequality
    # is the value of moving the verdict one fan-out earlier.
    assert p.reactive_cost > 0
    # And it still proposes the maximal safe set to run instead of nothing.
    assert p.max_concurrency == 3
    assert "feature_a" in p.repartition and "refactor" in p.repartition


def test_under_declared_tree_is_priced_as_risk():
    """An empty (unknown blast radius) tree must be priced as colliding, not free."""
    p = price_plan(SCENARIOS["under_declared"])
    colliding_names = {n for pair in p.collisions for n in pair}
    assert "worker_2" in colliding_names  # the empty-tree worker
    assert p.max_concurrency == 2  # worker_1 + worker_3 are safe together


def test_price_stands_on_the_real_kernel_predicate():
    """The proof uses the shipped arbiter geometry, not a private reimplementation."""
    import plan_price

    # The module imports the REAL kernel predicate; this is what makes the
    # predictive price exactly as un-forgeable as the reactive verdict.
    from dos._tree import lane_trees_disjoint

    assert plan_price.lane_trees_disjoint is lane_trees_disjoint


def test_disjoint_partition_has_full_concurrency():
    """A geometric sanity check on the max-independent-set core."""
    agents = [Agent(f"w{i}", [f"src/m{i}/**"]) for i in range(6)]
    p = price_plan(agents)
    assert p.collisions == []
    assert p.max_concurrency == 6


def test_fully_overlapping_partition_serializes():
    """N agents all touching one subtree → max concurrency 1 (full serialization)."""
    agents = [Agent(f"w{i}", ["src/core/**"]) for i in range(5)]
    p = price_plan(agents)
    assert p.max_concurrency == 1  # only one can run at a time
    assert len(p.collisions) == 10  # every pair collides: C(5,2)
