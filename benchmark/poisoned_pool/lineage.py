"""lineage.py — the credit-assignment channel hiding in the poisoned pool (PURE).

THE SECOND DATASET. The poisoned-pool loop is generational: generation N's
prompts are conditioned on exemplars SAMPLED from generation N-1's admitted pool
(`run._sample_exemplars`). So every gen-≥1 trajectory has PARENTS — the admitted
exemplar trajectories whose (patch + CLAIM) seeded its prompt — and a ROOT: the
gen-0 ancestor at the head of its exemplar chain. The headline metric
`pool_poison_frac` measures the pool in AGGREGATE; this module measures the same
poison per-LINEAGE — which gen-0 root's over-claim propagated, and to how many
descendants, under each arm. That per-root reach is the credit-assignment
dataset that was always derivable from the run but never recorded.

It reads the EDGE that `run.py` now stamps on each row (`exemplar_ids`, the
traj_ids of the batch's admitted exemplars) and folds it. It is data, not policy:
stdlib only — it imports no kernel, no model, no vendor, and re-decides nothing.
The over-claim predicate is the SAME one `harness.fold_batch` uses
(`claim == RESOLVED and not witness_confirmed`); we never re-run a witness, we
read the fossilised verdict the row already carries.

THE STRUCTURAL CLAIM IT MAKES VISIBLE. Arm W's pool is poison-free by
construction (`dos.reward.admit` cannot admit a refuted claim), so a poisoned
trajectory NEVER enters W's pool and therefore can NEVER become an exemplar.
A poison root's transitive descendant count under W is exactly 0 — not by luck,
by the kernel. Under Arm S the same root's over-claim is banked and reaches its
descendants. The per-root table makes that containment a per-lineage fact, not a
mean over the whole pool.

CREDIT-SHARING RULE: set-membership. A descendant with M roots counts toward
EVERY root in its ancestor set (not 1/M fractional). For a CONTAINMENT claim
("did this poison root reach anyone?") set-membership is the honest default — it
never under-counts a poison root's reach, so a "reach = 0 under W" verdict is the
strong form. The choice is recorded in the report (`credit_rule`) so it is never
silently assumed.

HONESTY FLOOR. A run whose rows predate this channel carries no `exemplar_ids`.
The report then says `lineage_recorded: false` and emits no per-root table —
rather than treating every row as its own root and printing an all-singletons
"finding" that looks like a result. Same direction as every DOS surface: absent
evidence is reported as absent, never dressed as a measurement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Set

# The over-claim predicate, restated locally so the module imports nothing but
# stdlib. A trajectory over-claims when it CLAIMED resolved and the witness
# refused to confirm it — identical to harness.fold_batch's `overclaims`.
_CLAIM_RESOLVED = "RESOLVED"


def _is_overclaim(row: Dict) -> bool:
    return row.get("claim") == _CLAIM_RESOLVED and not row.get("witness_confirmed")


# Arms that share one lineage. Gen-0 is arm "B" (the common root batch); its
# admitted entries seed BOTH S and W at gen 1, so a root may be reached down
# either arm. We scope a descendant's roots by following its recorded edge —
# the edge already encodes which pool (S or W) the exemplar came from, because
# a poison trajectory is simply absent from W's pool and so never appears as a
# W-batch parent.


@dataclass
class RootCredit:
    """One gen-0 root and the poison it propagated, per arm.

    `descendants` / `overclaim_descendants` are sets of traj_ids (set-membership
    credit). `poison_root` is the root's OWN over-claim status — the thing whose
    reach Arm W contains to zero."""
    root_id: str
    task_id: str
    poison_root: bool
    descendants: Dict[str, Set[str]] = field(default_factory=dict)
    overclaim_descendants: Dict[str, Set[str]] = field(default_factory=dict)

    def _arm(self, arm: str) -> None:
        self.descendants.setdefault(arm, set())
        self.overclaim_descendants.setdefault(arm, set())

    def to_json(self) -> Dict:
        arms = sorted(self.descendants)
        return {
            "root_id": self.root_id,
            "task_id": self.task_id,
            "poison_root": self.poison_root,
            "reach": {a: len(self.descendants[a]) for a in arms},
            "overclaim_reach": {a: len(self.overclaim_descendants[a]) for a in arms},
        }


def _index(rows: Sequence[Dict]) -> Dict[str, Dict]:
    return {r["traj_id"]: r for r in rows if "traj_id" in r}


def lineage_recorded(rows: Sequence[Dict]) -> bool:
    """True iff at least one row carries a non-empty exemplar edge — i.e. the run
    was produced by a harness that records lineage. An all-`[]` (or key-absent)
    set means a pre-channel artifact: there is no edge to fold."""
    return any(r.get("exemplar_ids") for r in rows)


def roots_of(traj_id: str, by_id: Dict[str, Dict],
             _seen: Set[str] | None = None) -> Set[str]:
    """The set of gen-0 root traj_ids an ancestor walk from `traj_id` reaches.

    A trajectory whose `exemplar_ids` is empty IS a root (gen 0 / arm B, or any
    row the channel did not record an edge for). Otherwise its roots are the
    union of its parents' roots. `_seen` guards the (impossible-by-construction
    but cheap-to-defend) cycle: a parent is always an earlier generation, so the
    walk is a DAG, but a malformed artifact must not hang the fold."""
    seen = _seen if _seen is not None else set()
    if traj_id in seen:
        return set()
    seen.add(traj_id)
    row = by_id.get(traj_id)
    if row is None:
        # An edge naming a parent we have no row for: treat the parent itself as a
        # terminal root so its reach is still attributed, never silently dropped.
        return {traj_id}
    parents = row.get("exemplar_ids") or []
    if not parents:
        return {traj_id}
    roots: Set[str] = set()
    for p in parents:
        roots |= roots_of(p, by_id, seen)
    return roots


def build_ancestry(rows: Sequence[Dict]) -> Dict[str, RootCredit]:
    """Fold the recorded edges into per-root credit. Returns {root_id ->
    RootCredit}. Each non-root trajectory contributes itself to every root in its
    ancestor set (set-membership), under its OWN arm (the arm that conditioned
    it). A root contributes nothing to itself: reach counts DESCENDANTS."""
    by_id = _index(rows)
    credits: Dict[str, RootCredit] = {}

    def _credit_for(root_id: str) -> RootCredit:
        if root_id not in credits:
            r = by_id.get(root_id, {})
            credits[root_id] = RootCredit(
                root_id=root_id,
                task_id=r.get("task_id", ""),
                poison_root=_is_overclaim(r) if r else False,
            )
        return credits[root_id]

    for row in rows:
        tid = row.get("traj_id")
        if not tid:
            continue
        parents = row.get("exemplar_ids") or []
        if not parents:
            # A root: register it (so a poison root with zero reach still shows up
            # as a contained lineage), but it is not its own descendant.
            _credit_for(tid)
            continue
        arm = row.get("arm", "?")
        is_oc = _is_overclaim(row)
        for root_id in roots_of(tid, by_id):
            c = _credit_for(root_id)
            c._arm(arm)
            c.descendants[arm].add(tid)
            if is_oc:
                c.overclaim_descendants[arm].add(tid)
    return credits


def fold_root_credit(rows: Sequence[Dict]) -> List[Dict]:
    """The per-root credit table (the second dataset), JSON-ready and sorted:
    poison roots first, then by reach descending — the lineages that carried the
    most poison surface at the top."""
    credits = build_ancestry(rows)
    table = [c.to_json() for c in credits.values()]
    table.sort(key=lambda d: (not d["poison_root"],
                              -max(d["reach"].values() or [0]),
                              d["root_id"]))
    return table


def _poison_root_reach(table: List[Dict], arm: str) -> Dict:
    """Mean / total descendants of the POISON roots under one arm — the metric
    the containment claim rests on. A poison root absent from an arm's table (it
    never entered that arm's pool) contributes a reach of 0, which is the point."""
    poison = [d for d in table if d["poison_root"]]
    reaches = [d["reach"].get(arm, 0) for d in poison]
    n = len(poison)
    total = sum(reaches)
    return {
        "poison_roots_n": n,
        "total_reach": total,
        "mean_reach": (total / n) if n else 0.0,
    }


def lineage_report(rows: Sequence[Dict]) -> Dict:
    """The foldable lineage summary for a run's rows. Honest about absence: a
    pre-channel artifact returns `lineage_recorded: false` and no table."""
    if not lineage_recorded(rows):
        return {
            "lineage_recorded": False,
            "credit_rule": "set-membership",
            "note": ("no row carries an exemplar edge — this run predates the "
                     "lineage channel; no per-root credit can be folded"),
        }
    table = fold_root_credit(rows)
    arms = sorted({a for d in table for a in d["reach"]})
    reach = {a: _poison_root_reach(table, a) for a in arms}
    # The containment headline: poison-root reach under W relative to S. W is
    # poison-free by construction, so a poison root cannot become a W exemplar —
    # its W reach is 0. We report the raw pair; the ratio is informational.
    s_reach = reach.get("S", {}).get("total_reach", 0)
    w_reach = reach.get("W", {}).get("total_reach", 0)
    return {
        "lineage_recorded": True,
        "credit_rule": "set-membership",
        "roots_n": len(table),
        "poison_roots_n": sum(1 for d in table if d["poison_root"]),
        "poison_root_reach": reach,
        "containment": {
            "poison_reach_S": s_reach,
            "poison_reach_W": w_reach,
            "w_contained": w_reach == 0,
        },
        "roots": table,
    }
