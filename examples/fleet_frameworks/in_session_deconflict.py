"""Recipe 8 — the in-session deconfliction handshake, runnable.

The other recipes wire `arbitrate` into a *framework's* dispatch seam. This one
serves the case that needs no framework at all: **you are the agent**, several
of you (sub-agents, parallel `/loop` workers, a fan-out of `Task` spawns, a
`parallel()`/`pipeline()` workflow stage) are about to touch one repo, and you
want the same one-line move every time —

    "another agent is also working here; deconflict with DOS."

That move is a single `arbitrate(my-tree, the-leases-already-held)` call. It is
a *pure function*: state in, decision out, no I/O, no lock held on the world.
The verdict is `acquire` (your file-tree is disjoint from every live lease — go)
or `refuse` (it overlaps — and here are the `free_clusters` you could take
instead). Deconfliction is a **router**, not a queue: a colliding worker is
handed somewhere disjoint to work *now*, not made to wait.

This is the universal-standard framing: the handshake does not care whether the
"other agent" is a sibling sub-agent in this very session, a separate `/loop`,
or a different runtime entirely (Hermes, an Agents-SDK swarm, a cron job). They
all speak the same closed verdict over the same disjointness rule. The host's
PreToolUse hook enforces this automatically at write time; this recipe is the
*deliberate* form — the agent asking BEFORE it fans out, so it routes the work
right the first time instead of getting a write denied.

    python examples/fleet_frameworks/in_session_deconflict.py

Needs only `dos` (pip install -e . from the repo root).
"""

from __future__ import annotations

from dos import arbiter

from _fixture import admit, make_demo_repo


def fan_out_plan(my_regions: list[tuple[str, list[str]]],
                 live_leases: list[dict],
                 cfg) -> list[dict]:
    """The handshake, applied once per region I'm about to dispatch a worker to.

    `my_regions` is what *this* session wants to parallelize over — each a
    (lane, glob-tree) the next sub-agent/loop will edit. `live_leases` is what
    other agents already hold (a sibling sub-agent, another loop, a foreign
    runtime — the kernel does not distinguish them). For each region we ask the
    pure arbiter whether it is safe to start, and on a refuse we take the
    disjoint lane it routes us to instead of colliding.
    """
    plan: list[dict] = []
    held = list(live_leases)
    for lane, tree in my_regions:
        v = admit(lane, tree, held, cfg)
        if v.outcome == "acquire":
            # Disjoint from every live lease — dispatch a worker here, and add
            # our own hold so the NEXT region in this same plan deconflicts
            # against it too (two of my own sub-agents must not collide either).
            plan.append({"requested": lane, "go": True,
                         "dispatch_to": v.lane, "reason": v.reason})
            held.append({"lane": v.lane, "lane_kind": v.lane_kind, "tree": v.tree})
        else:
            # Overlaps a live lease. NOT a stall: the verdict carries the lanes
            # we could take instead — route there, or hold this worker back.
            plan.append({"requested": lane, "go": False,
                         "reason": v.reason, "free_clusters": v.free_clusters})
    return plan


def run_demo(repo=None) -> dict:
    """Two scenarios: a clean fan-out, and one that collides with a sibling."""
    cfg = make_demo_repo(repo)

    # Scenario A — I want to fan out three sub-agents over disjoint regions,
    # and nobody else holds anything. All three are admitted; each one I admit
    # is added to `held`, so my own workers are deconflicted against each other.
    clean = fan_out_plan(
        my_regions=[("src", ["src/**"]),
                    ("docs", ["docs/**"]),
                    ("tests", ["tests/**"])],
        live_leases=[],
        cfg=cfg,
    )

    # Scenario B — a sibling sub-agent (or another /loop) already holds src/**.
    # My request for `src` is refused and routed; my disjoint `docs` still goes.
    sibling_holds_src = [{"lane": "src", "lane_kind": "cluster",
                          "tree": ["src/**"]}]
    collide = fan_out_plan(
        my_regions=[("src", ["src/**"]),
                    ("docs", ["docs/**"])],
        live_leases=sibling_holds_src,
        cfg=cfg,
    )

    # The disjointness rule is by file-TREE, not by lane name: a nested region
    # under a busy parent is still admitted iff its globs don't overlap a hold.
    nested = arbiter.arbitrate(
        requested_lane="src/dos/drivers", requested_kind="cluster",
        requested_tree=["src/dos/drivers/**"],
        live_leases=[{"lane": "docs", "lane_kind": "cluster",
                      "tree": ["docs/**"]}],
        config=cfg,
    )
    return {"clean": clean, "collide": collide, "nested": nested}


def main() -> int:
    r = run_demo()

    print("A — clean fan-out (no live leases), 3 disjoint sub-agents:")
    for step in r["clean"]:
        print(f"    {step['requested']:6} -> go={step['go']}  ({step['reason']})")

    print("\nB — a sibling already holds src/** :")
    for step in r["collide"]:
        if step["go"]:
            print(f"    {step['requested']:6} -> go=True   "
                  f"dispatch_to={step['dispatch_to']}  ({step['reason']})")
        else:
            print(f"    {step['requested']:6} -> REFUSE    "
                  f"({step['reason']})  free={step['free_clusters']}")

    n = r["nested"]
    print(f"\nC — deconfliction is by file-tree, not by name:")
    print(f"    src/dos/drivers/** vs a held docs/**: "
          f"{n.outcome} ({n.reason})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
