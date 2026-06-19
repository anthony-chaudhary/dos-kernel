"""The lane-admission kernel — `arbitrate(request, live_leases, config) -> decision`.

This is the crown jewel (dispatch-os-vision §4 scheduler / ACR Plane ①): a
**pure** admission policy. State (the live leases) goes in, a decision comes out,
no I/O — so the concurrency *policy* is unit-tested without spawning a single
live loop, and you can *prove* properties about it. Almost no production OS
scheduler has this property.

Extracted from the origin repo's `scripts/fanout_state.py` (`arbitrate_lane`,
~L2772). The extraction is **lift-and-shift, not redesign**: the decision logic
is byte-for-byte the proven code. The ONE change is the §5.5.4 mechanism/policy
split — the origin function reached into `next_up_render._CLUSTERS` and module-
level `_AUTOPICK_CLUSTERS` / `_EXCLUSIVE_LANES` constants for the *job repo's*
lane taxonomy. Here those become `SubstrateConfig.lanes` (per-workspace data),
so the kernel never names a domain lane: point it at a benchmark repo's lanes,
or a calendar's, or a k8s namespace's, and it arbitrates those unchanged.

The vision's **capability-lattice generalization** (every touchable resource a
lattice node; admit iff the requested capability set is *provably disjoint*) is a
separate redesign that would sit on top of this pure arbiter — deliberately out
of scope here (PO4 scope guard / audit G4). This ships the arbiter the lattice
would later stand on.

What this arbiter *is*, named after its mechanism rather than the "lane" metaphor:
a **lock manager whose granularity is a glob-set** — a lane is a *leased
predicate-lock over a region of the workspace*, admitted by predicate-intersection
plus lock compatibility (`lock_modes`: SHARED/SHARED may coexist; anything with
EXCLUSIVE needs tree-disjointness). The legacy soft-overlap ratio remains a
configurable scorer, but it is no longer the default admission floor. The
capability-lattice above is then **the same primitive over a richer predicate
algebra than path-prefixes** — a new `prefixes_collide`, not a new arbiter. See
`docs/89_the-lane-is-a-region-lock.md` (which is also the forward litmus for what
belongs in here: a region-lock property, never a swim-lane one).
"""

from __future__ import annotations

from typing import Callable

from dos._tree import lane_trees_disjoint as _trees_disjoint  # noqa: F401
from dos._tree import tree_disjoint_from_all_live as _disjoint_from_all_live
from dos.lane_overlap import overlap_verdict
from dos.config import LaneTaxonomy, SubstrateConfig, ensure
from dos.admission import (
    AdmissionPredicate,
    AdmissionRequest,
    AdmissionVerdict,
    built_in_predicates,
    run_predicates,
)


class LaneDecision:
    """Result of `arbitrate` — what a dispatch loop should do at Step 0.

    ``outcome`` is one of:
      'acquire'  — admitted; ``lane`` is the lane to lease (may differ from the
                   requested lane when auto-pick reassigned it).
      'refuse'   — not admitted; ``reason`` explains why, ``free_clusters`` lists
                   any cluster lanes the operator could pick instead.
    ``auto_picked`` is True when ``lane`` was chosen by auto-pick.
    ``pick_count`` is the best-effort pick-availability signal (see ``pick_oracle``).
    """

    __slots__ = ("outcome", "lane", "lane_kind", "tree", "auto_picked",
                 "reason", "free_clusters", "pick_count")

    def __init__(self, outcome: str, *, lane: str = "", lane_kind: str = "",
                 tree: list[str] | None = None, auto_picked: bool = False,
                 reason: str = "", free_clusters: list[str] | None = None,
                 pick_count: int | None = None):
        self.outcome = outcome
        self.lane = lane
        self.lane_kind = lane_kind
        self.tree = tree or []
        self.auto_picked = auto_picked
        self.reason = reason
        self.free_clusters = free_clusters or []
        self.pick_count = pick_count

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome, "lane": self.lane,
            "lane_kind": self.lane_kind, "tree": self.tree,
            "auto_picked": self.auto_picked, "reason": self.reason,
            "free_clusters": self.free_clusters,
            "pick_count": self.pick_count,
        }


def _lease_blocks(requested_tree: list[str], lease_tree: list[str]) -> bool:
    """Does an EXISTING lease block a NEW acquire?

    Empty-tree rules (asymmetric on lease side):
      * empty LEASE tree → does NOT block (a lease that named no blast radius
        cannot claim conflict; otherwise one empty-tree lease wedges every
        subsequent acquire).
      * empty REQUESTED tree vs KNOWN lease tree → blocks (unknown blast radius
        is never safe).
      * both empty → does NOT block (lone-loop safe).

    Legacy helper: both-known delegates to `dos.lane_overlap.overlap_verdict`, the
    explicit prefix/ratio scorer. The default admission path now runs through
    `DisjointnessPredicate` and `lock_modes`; this remains for old exact callers
    that deliberately ask the ratio question.
    """
    if not lease_tree:
        return False
    if not requested_tree:
        return True
    return not overlap_verdict(list(requested_tree), list(lease_tree)).admissible


def _admission_verdict(
    *, lane: str, kind: str, tree: list[str], live_leases: list[dict],
    predicates: list[AdmissionPredicate], config: SubstrateConfig,
    mode: str = "",
) -> AdmissionVerdict:
    """Run the FULL admission conjunction for a candidate lane (ADM Phase 1).

    The single seam every collision check in `arbitrate` now routes through: the
    built-in `DisjointnessPredicate` (default S/X lock-mode compatibility) PLUS
    `SelfModifyPredicate` PLUS any workspace-discovered predicate, composed
    conjunctively by `admission.run_predicates` (first refusal wins; all-admit ⇒
    admit; a predicate that raises is a fail-closed refuse). Extra predicates can
    only ADD refusals (the conjunctive-only invariant), never loosen, so a
    disjoint job-lane pair still admits.
    """
    request = AdmissionRequest(lane=lane, kind=kind, tree=tuple(tree or ()), mode=mode)
    return run_predicates(predicates, request, live_leases, config)


def _lease_collision(
    *, lane: str, kind: str, tree: list[str], live_leases: list[dict],
    predicates: list[AdmissionPredicate], config: SubstrateConfig,
    mode: str = "",
) -> bool:
    """True iff admitting this candidate lane is refused by ANY predicate against
    ANY live lease — the boolean the auto-pick loops want (they only need "is this
    candidate blocked," then move to the next ladder rung). A thin wrapper over
    `_admission_verdict` so the loops read like the old `any(_lease_blocks(...))`."""
    return not _admission_verdict(
        lane=lane, kind=kind, tree=tree, live_leases=live_leases,
        predicates=predicates, config=config, mode=mode,
    ).admitted


def arbitrate(
    *,
    requested_lane: str,
    requested_kind: str,
    requested_tree: list[str],
    live_leases: list[dict],
    config: SubstrateConfig | None = None,
    auto_pick_order: list[tuple[str, str, list[str]]] | None = None,
    named_lanes: tuple[tuple[str, tuple[str, ...]], ...] | None = None,
    derived_lanes: list[tuple[str, list[str]]] | None = None,
    force: bool = False,
    pick_oracle: Callable[[str, str, list[str]], int | None] | None = None,
    rank_key: Callable[[str, str, list[str]], float | None] | None = None,
    predicates: list[AdmissionPredicate] | None = None,
    class_budgets: dict[str, int] | None = None,
    live_siblings: list[dict] | None = None,
    sibling_tree_lookup: Callable[[str], list[str] | None] | None = None,
    requested_mode: str = "",
) -> LaneDecision:
    """PURE admission: decide whether a loop may start, and on which lane.

    No I/O — ``live_leases`` is passed in, the decision is returned. The lane
    taxonomy (which lanes are concurrent clusters, which are exclusive, each
    lane's canonical tree) comes from ``config.lanes`` (defaulting to the active
    workspace config); the kernel never hard-codes a domain lane name.

    Inputs:
      requested_lane  — the lane the operator asked for ('' = bare invocation).
      requested_kind  — 'cluster' | 'keyword' | 'global' | '' (bare → auto-pick).
      requested_tree  — the file tree of the requested lane.
      live_leases     — dicts with at least {lane, lane_kind, tree}.
      config          — the SubstrateConfig whose `lanes` taxonomy to arbitrate
                        over (None → the process-active config).
      auto_pick_order — optional `(lane_name, lane_kind, tree)` ladder walked for
                        BARE invocations; first lane whose tree is disjoint from
                        every live lease wins (or, with `rank_key`, the highest-
                        ranked such lane — see below). Takes precedence over the
                        legacy named_lanes/derived_lanes.
      named_lanes / derived_lanes — LEGACY pre-ladder fallbacks (kept for the
                        replay tests); ignored when `auto_pick_order` is supplied.
      force           — OPERATOR OVERRIDE. With an explicit `requested_lane`,
                        honor it literally and skip the disjointness/overlap/same-
                        lane refuses. The one thing force still respects is a live
                        exclusive lane (it holds the shared spine). Never auto-picks.
      pick_oracle     — BEST-EFFORT pick-availability gate. `(name, kind, tree) ->
                        int | None`; the arbiter SKIPS any auto-pick lane the
                        oracle confidently reports as 0 picks. `None` (can't tell)
                        means DO NOT skip — oracle failure can only add skips,
                        never remove a viable fallback. Only consulted on the
                        bare/unresolved-keyword auto-pick path. Pure: the oracle
                        does any I/O, not us.
      rank_key        — OPTIONAL value-aware picker (docs/91, research-area §3).
                        `(name, kind, tree) -> float | None`; when supplied, the
                        BARE auto-pick walk visits the `auto_pick_order` candidates
                        in DESCENDING rank order instead of ladder order, so the
                        admitted lane is the highest-ranked one that is *also*
                        disjoint + available — i.e. the argmax over the admissible
                        set. `None` for a candidate (or a `rank_key` that raises on
                        it) means "no opinion" and sinks that candidate below every
                        ranked one, ties (and the all-`None` case) preserving ladder
                        order — so `rank_key=None` is byte-identical to the legacy
                        first-fit walk (the regression guard). **Soundness floor:**
                        rank_key only chooses the ORDER among candidates; it is
                        applied *before* the unchanged disjointness + availability
                        gate, so it can never admit a colliding lane — the worst a
                        bad rank can do is pick a suboptimal-but-still-disjoint lane
                        (`rank, never re-admit`, docs/89/§90.3). Like `pick_oracle`,
                        it is resolved at the CALL BOUNDARY (a driver/host yield
                        estimator); the kernel never learns what "value" means — the
                        signal stays driver-side (docs/76). Only consulted on the
                        bare auto-pick path with `auto_pick_order` supplied.
      predicates      — the admission-predicate conjunction to run the collision
                        check through (ADM Phase 1). None → the BUILT-IN set only
                        (`DisjointnessPredicate` + `SelfModifyPredicate`), which
                        are pure and always-on, so `arbitrate` itself does NO I/O
                        on its default path — staying pure exactly as documented.
                        Workspace-DISCOVERED `dos.predicates` plugins are resolved
                        at the CALL BOUNDARY and passed in (the CLI's
                        `cmd_arbitrate` does `admission.active_predicates()` and
                        threads the full list here, the same place it discovers a
                        renderer) — mirroring how `pick_oracle` is resolved by the
                        caller, never inside the pure kernel. A test injects an
                        explicit list to run a hermetic conjunction. Predicates
                        compose CONJUNCTIVELY — every one must admit — and can
                        only REFUSE, never force-admit (the one invariant that
                        keeps an open predicate set safe); `--force` remains the
                        sole override of a predicate refusal, exactly as for the
                        disjointness refuse.
      class_budgets   — OPTIONAL concurrency-class budgets (docs/97 Phase 1, the
                        worked lesson docs/96). `{lane_kind: max_concurrent}` — how
                        many live leases of a given KIND may be held at once. On the
                        BARE auto-pick walk, a candidate whose kind is already at its
                        budget (live count of that kind >= max_concurrent) is SKIPPED,
                        so the arbiter cannot mint an (N+1)-th holder of a budgeted
                        class. This is the in-kernel home for the `priority` class
                        budget the *host* used to enforce by pre-filtering the
                        `auto_pick_order` (job `fanout_state.py`'s
                        `_priority_budget_exhausted` drop) — exactly the layering the
                        plan moves down. WHY a parameter and not an
                        `AdmissionPredicate`: a budget is a CROSS-lease count over the
                        whole live set, but a predicate is called once per
                        `(request, single-live-lease)` pair and cannot count across
                        leases (admission.py "called once per (request, live_lease)
                        pair") — so the budget is its own pure step here, the same
                        shape docs/97's admission model gives it (step 1 "count live
                        leases with class == C", separate from the per-lease region
                        resolution). PURE: counts the passed-in `live_leases`, no I/O;
                        the host supplies the budget VALUE (its `priority_slots:`
                        config), the kernel owns the ADMISSION logic — the docs/97
                        DOS-vs-host boundary verbatim. `None` (default) → no budgets →
                        byte-identical to the pre-budget walk (the regression guard).
                        Only consulted on the bare auto-pick path; a directly-named or
                        `--force`d lane is never budget-gated (it is the operator's
                        explicit choice, and the same-lane/exclusive refuses already
                        bound its concurrency). The N candidates that DO pass the
                        budget still bind disjoint regions via the unchanged
                        disjointness gate, so this is genuine N-way concurrency, safe
                        by construction — never a fixed set of pre-named slots (the
                        docs/89 §4.4 swim-lane category error this deliberately avoids).
      live_siblings   — OPTIONAL un-leased live runs the bare auto-pick should
                        PREFER to avoid colliding with (the single-pick-ceiling fix,
                        FQ-449). A lease guards a lane; a *sibling* is a live run that
                        holds no lease yet (a freshly-launched loop, an un-leased
                        `/dispatch` child) but whose tree this loop will collide with
                        post-acquire. Dicts with at least `{lane}`; their trees are
                        resolved via `sibling_tree_lookup`. When supplied (with the
                        lookup), the BARE walk runs a FIRST pass that admits only a
                        candidate whose tree is provably disjoint from EVERY sibling
                        (via `dos._tree.tree_disjoint_from_all_live`, the same predicate
                        the post-acquire sibling-scan escape uses); only if that pass
                        finds nothing does it FALL BACK to the unchanged walk (which may
                        return a sibling-overlapping lane — today's behavior). So the
                        ceiling shifts: a busy fleet spreads across disjoint lanes instead
                        of all colliding on the top-priority cluster, and the worst case
                        is byte-identical to the pre-fix walk. `None` (default) → no
                        first pass → byte-identical to today (the regression guard). Like
                        `pick_oracle`/`rank_key`, the sibling state is gathered at the
                        CALL BOUNDARY (the host scans live loop dirs) and passed in; the
                        kernel does NO I/O. Only consulted on the bare auto-pick path.
      sibling_tree_lookup — `(lane) -> tree | None`; resolves a sibling's lane name to
                        its file tree for the `live_siblings` disjointness pass. Same
                        callable shape the sibling-scan escape takes. Ignored unless
                        `live_siblings` is non-empty.
    """
    cfg = ensure(config)
    # The `predicates=None` default is the built-in conjunction — but it must be
    # WORKSPACE-AWARE so a foreign repo's `**/*` lane is not refused as SELF_MODIFY
    # against kernel files that don't exist under it. We pass `config=cfg` (NOT a
    # bare `workspace=` path), so `built_in_predicates` reads the CACHED
    # `cfg.workspace` facts gathered at config-build time — NO disk I/O here, the
    # arbiter stays pure. A config whose facts are None (a hand-built test config
    # that never probed) degrades to the conservative full static set, unchanged.
    # This closes the foreign-repo over-refusal: the arbiter's own default now
    # matches what the CLI's `active_predicates(workspace=cfg.root)` already did.
    preds = predicates if predicates is not None else built_in_predicates(config=cfg)
    lanes: LaneTaxonomy = cfg.lanes
    autopick_clusters = list(lanes.autopick)

    # Lane NAMES are compared case-INsensitively, the same fold the lane TREES already
    # use (`_cluster_tree` lowercases, the disjointness/self-modify predicates fold via
    # `_tree.norm_tree_prefix`). Without this, `dos arbitrate --lane Orchestration`
    # did NOT match the canonical exclusive `orchestration` lane on a case-insensitive
    # FS, so the run-alone refuse was silently skipped and the request degraded to
    # auto-pick. `_lane_key` is the single fold every NAME membership test below runs
    # through; the original `requested_lane` string is preserved for display/output.
    def _lane_key(name: str) -> str:
        return str(name or "").casefold()

    exclusive_lanes = {_lane_key(x) for x in lanes.exclusive}
    requested_lane_key = _lane_key(requested_lane)

    def _cluster_tree(cluster: str) -> list[str]:
        """The canonical file tree for a cluster lane, from the config taxonomy."""
        return lanes.tree_for(cluster.lower()) or lanes.tree_for(cluster)

    # Every lane NAME this workspace's taxonomy recognises, case-folded. Used to
    # tell an auto-pick redirect WHY the requested lane was not granted: a name in
    # this set that the picker skipped was *held* ("busy"); a name absent from it
    # was never a lane here at all. Conflating the two makes the kernel narrate a
    # false "was busy" for a typo'd / foreign lane name (docs/104 §4).
    # `global` is the generic exclusive lane every workspace has; any other
    # exclusive lane (e.g. a host's `orchestration`) is already folded in via
    # `*lanes.exclusive`, so no host lane name is hardcoded here.
    _known_lane_keys = {
        _lane_key(n)
        for n in (*lanes.concurrent, *lanes.exclusive, *lanes.autopick,
                  *lanes.trees.keys(), *lanes.aliases.keys(),
                  "global")
    }

    # Set when a kindless soft-hint request (the cluster branch below) was
    # refused by the admission conjunction before falling through to the
    # auto-pick walk: the redirect parenthetical then carries the REAL refusal
    # instead of narrating a false "was busy" for a lane nothing held.
    _hint_refusal: dict[str, str] = {}

    def _redirect_why(default_when_busy: str) -> str:
        """Honest parenthetical for an auto-pick redirect away from a NAMED request.

        ``requested 'X' was busy`` only when X is a real lane this workspace knows
        and the picker found it held; ``'X' is not a lane in this workspace`` when
        the name is unknown; the conjunction's own refusal when a soft-hinted
        cluster lane was refused on its merits (a colliding tree, a predicate) —
        never one verdict masquerading as a diagnosis of another.
        """
        if requested_lane and requested_lane_key not in _known_lane_keys:
            return f"(requested {requested_lane!r} is not a lane in this workspace)"
        if _hint_refusal.get("reason"):
            return (f"(requested {requested_lane!r} was refused: "
                    f"{_hint_refusal['reason']})")
        return default_when_busy

    live_lanes = {_lane_key(l.get("lane", "")) for l in live_leases}
    live_kinds = {str(l.get("lane_kind", "")) for l in live_leases}

    def _free_clusters() -> list[str]:
        return [c for c in autopick_clusters if _lane_key(c) not in live_lanes]

    def _free_concurrent_clusters() -> list[str]:
        # ALL concurrent cluster lanes that are free — not just the autopick
        # ladder. A lane can be a real, leaseable cluster (in `lanes.concurrent`)
        # yet absent from `autopick`; such a lane is still a valid `--lane`
        # target. The false-"busy" refusal (issue #118) must report it, the same
        # docs/104 §4 class the `_redirect_why` fix closed for redirects.
        return [c for c in lanes.concurrent if _lane_key(c) not in live_lanes]

    # Concurrency-class budgets (docs/97 Phase 1). Normalize once: drop non-positive
    # / non-int budgets (a budget of 0 or a garbled value would silently wedge a
    # whole class — the safe direction is "no budget for that kind", matching the
    # host's `_load_priority_slots` fallback-to-default-on-bad-config posture). The
    # live count per kind is computed once over the passed-in leases (PURE — no I/O).
    _budgets: dict[str, int] = {}
    if class_budgets:
        for _k, _v in class_budgets.items():
            if isinstance(_v, int) and not isinstance(_v, bool) and _v > 0:
                _budgets[str(_k)] = _v
    _live_kind_counts: dict[str, int] = {}
    if _budgets:
        for _l in live_leases:
            _lk = str(_l.get("lane_kind", ""))
            _live_kind_counts[_lk] = _live_kind_counts.get(_lk, 0) + 1

    def _budget_exhausted(kind: str) -> bool:
        """True iff class ``kind`` is at (or over) its concurrency budget — the
        live count of leases of this kind has reached ``max_concurrent``. Kinds
        with no declared budget never exhaust (return False). The cross-lease count
        a per-lease `AdmissionPredicate` structurally cannot express (admission.py),
        so it lives here as the docs/97 step-1 budget gate."""
        cap = _budgets.get(str(kind))
        if cap is None:
            return False
        return _live_kind_counts.get(str(kind), 0) >= cap

    _saw_any_candidate = False
    _all_disjoint_were_zero = True
    # Bookkeeping for the docs/97 budget refuse: True iff the ONLY thing that kept
    # the bare walk from admitting a candidate was a class budget (every otherwise-
    # viable candidate was budget-skipped). Distinguishes "class at budget, wait for
    # a slot" from the generic "ladder exhausted / all 0-pick" refuses, so the
    # operator sees the real cause (and that waiting — not /replan — is the lever).
    _saw_budget_skip = False
    _saw_non_budget_candidate = False
    # The pick count `_admit_lane` last computed — cached so the admit DECISION
    # and the `pick_count` REPORTED on the resulting LaneDecision come from the
    # SAME oracle call. Re-calling `_picks` at the return site (the old code) let
    # a non-deterministic / side-effecting oracle report a count that disagreed
    # with the value that actually drove admission (adversarial-review finding).
    _last_pick_count: int | None = None

    def _picks(name: str, kind: str, tree: list[str]) -> int | None:
        if pick_oracle is None:
            return None
        try:
            n = pick_oracle(name, kind, list(tree))
        except Exception:
            return None  # best-effort: oracle failure never blocks a lane
        return n if isinstance(n, int) else None

    def _safe_rank(name: str, kind: str, tree: list[str]) -> float | None:
        """The value-aware picker's rank for one candidate, fail-soft (docs/91).

        Mirrors `_picks`: a `rank_key` that raises or returns a non-number yields
        `None` ("no opinion"), so a broken estimator degrades to ladder order and
        never blocks or mis-admits a lane (the `pick_oracle` best-effort rule,
        applied to ranking). PURE: the estimator does any I/O, not us.
        """
        if rank_key is None:
            return None
        try:
            r = rank_key(name, kind, list(tree))
        except Exception:
            return None
        return float(r) if isinstance(r, (int, float)) and not isinstance(r, bool) else None

    def _ranked(order: list[tuple[str, str, list[str]]]) \
            -> list[tuple[str, str, list[str]]]:
        """Reorder the bare auto-pick candidates by descending rank (docs/91 §3).

        STABLE: candidates keep their relative ladder order within an equal rank,
        and a `None` rank (no opinion) sinks below every ranked candidate — so with
        `rank_key is None` the list is returned UNCHANGED (every rank is `None`, the
        sort key is constant, Python's stable sort is a no-op) and the walk below is
        byte-identical to the legacy first-fit. Ranking only chooses the ORDER the
        unchanged disjointness+availability gate is tried in; it cannot admit a
        colliding lane (the soundness floor — `rank, never re-admit`).
        """
        if rank_key is None:
            return order
        # Sort key: opinionated candidates (rank is not None) first, by rank desc;
        # `enumerate` index makes the sort explicitly stable for ties / no-opinion.
        def _key(item: tuple[int, tuple[str, str, list[str]]]):
            idx, (name, kind, tree_seq) = item
            r = _safe_rank(name, kind or "cluster", list(tree_seq or []))
            has = r is not None
            # descending rank for the opinionated; ladder index ascending as tiebreak
            return (0 if has else 1, -(r or 0.0), idx)
        return [it for _, it in sorted(enumerate(order), key=_key)]

    def _admit_lane(name: str, kind: str, tree: list[str]) -> bool:
        """A lane that passed the concurrency check — should we admit it?

        Clusters/slot/priority/derived: admit unless the oracle is CONFIDENT the
        lane has 0 picks (abstain ⇒ admit). The NAMED rung is INVERTED: require a
        positive pick signal — an abstain there is a skip (the oracle is blind to
        named lanes' file-glob trees, so abstain ≠ "has work"). Updates the
        all-skipped-on-zero bookkeeping for the refuse path.
        """
        nonlocal _saw_any_candidate, _all_disjoint_were_zero, _last_pick_count
        _saw_any_candidate = True
        n = _picks(name, kind, tree)
        _last_pick_count = n  # cache for the pick_count on the resulting decision
        if n is None:
            if kind == "named" and pick_oracle is not None:
                return False
            _all_disjoint_were_zero = False
            return True
        if n <= 0:
            return False
        _all_disjoint_were_zero = False
        return True

    # An exclusive lane is live → nothing else may start. (force respects this.)
    # The exclusive set is config-declared (`cfg.lanes.exclusive`, folded into
    # `exclusive_lanes`), never a hardcoded host lane name — `global` is the generic
    # constant; a host's own exclusive lanes (e.g. `orchestration`) come from its
    # taxonomy. Fold the live lease kinds the same way before the membership test
    # (`live_kinds` is raw; `exclusive_lanes` is already case-folded).
    if {_lane_key(k) for k in live_kinds} & exclusive_lanes:
        held = next(l for l in live_leases
                    if _lane_key(l.get("lane_kind", "")) in exclusive_lanes)
        return LaneDecision(
            "refuse",
            reason=(f"an exclusive lane is live (lane={held.get('lane')!r}, "
                    f"kind={held.get('lane_kind')!r}, loop="
                    f"{held.get('loop_ts')!r}); it touches the whole "
                    f"portfolio — wait for it to finish."
                    + (" (--force cannot override an exclusive live lane.)"
                       if force else "")),
            free_clusters=[],
        )

    # OPERATOR OVERRIDE — `--force` with an explicit lane.
    if force and requested_lane:
        forced_kind = requested_kind or "keyword"
        same_lane = requested_lane_key in live_lanes
        return LaneDecision(
            "acquire", lane=requested_lane, lane_kind=forced_kind,
            tree=requested_tree, auto_picked=False,
            reason=(f"FORCED lane {requested_lane!r} (operator --force; "
                    f"lane concern overridden"
                    + (", same-lane sibling present — concurrent edits to the "
                       "same tree are now the operator's responsibility"
                       if same_lane else "")
                    + (", tree is EMPTY — unknown blast radius accepted"
                       if not requested_tree else "")
                    + ")."),
        )

    # Same-lane collision → refuse (auto-pick can still rescue a cluster request).
    if requested_lane and requested_lane_key in live_lanes:
        if requested_kind == "cluster":
            pass  # fall through to auto-pick
        else:
            return LaneDecision(
                "refuse",
                reason=(f"lane {requested_lane!r} is already held by a live "
                        f"loop — pick a different --lane or wait."),
                free_clusters=_free_clusters(),
            )

    # Exclusive-lane request → admit only when nothing else is live. `global` is
    # the generic exclusive KIND every workspace has; any other exclusive lane is
    # recognised via the config-declared `exclusive_lanes` set, never a hardcoded
    # host name.
    if requested_kind == "global" or requested_kind in exclusive_lanes or \
            requested_lane_key in exclusive_lanes:
        if live_leases:
            return LaneDecision(
                "refuse",
                reason=(f"{requested_lane or 'global'!r} is an exclusive lane; "
                        f"{len(live_leases)} loop(s) already live. It must run "
                        f"alone — wait for them to finish."),
                free_clusters=_free_clusters(),
            )
        # Record the exclusive lane's OWN name as the lease kind (so the live-kind
        # exclusivity check above recognises it), falling back to the generic
        # `global` kind for a bare/global request.
        kind = requested_lane_key if requested_lane_key in exclusive_lanes else "global"
        # Even an exclusive lane (which runs alone, so disjointness is moot) must
        # pass the REQUEST-ABSOLUTE predicates — an `orchestration`/`global` lease
        # whose tree rewrites the kernel's own running code is the SELF_MODIFY
        # hazard, and it must refuse here too (not just on the keyword path). The
        # conjunction runs against the empty-lease sentinel, so disjointness admits
        # and self-modify still fires. `--force` skipped this (handled above).
        verdict = _admission_verdict(
            lane=requested_lane or "global", kind=kind, tree=requested_tree,
            live_leases=live_leases, predicates=preds, config=cfg,
            mode=requested_mode,
        )
        if not verdict.admitted:
            return LaneDecision(
                "refuse", reason=verdict.reason, free_clusters=_free_clusters(),
            )
        return LaneDecision(
            "acquire", lane=requested_lane or "global", lane_kind=kind,
            tree=requested_tree, auto_picked=False,
            reason=f"exclusive lane {requested_lane or 'global'!r} — no other "
                   f"loop live, admitted.",
        )

    # Cluster request on a FREE lane → admit, but FIRST run the admission
    # conjunction (ADM): the disjointness predicate confirms the cluster's tree is
    # disjoint from every live lease (a free lane NAME can still have an
    # overlapping tree), and the request-absolute predicates (self-modify, budget)
    # fire regardless. Without this gate a `--kind cluster` request would bypass
    # both the collision check AND the SELF_MODIFY guard — the regression the
    # adversarial review caught. `--force` still skips this (handled above).
    # A KINDLESS request naming a lane the taxonomy knows as a CLUSTER rides the
    # same branch — the "soft hint" rung (docs/104 §4). The exclusive branch above
    # already honors a kindless EXCLUSIVE name; without the cluster analogue, a
    # hinted FREE cluster lane fell into the auto-pick walk — which never tries
    # the hinted name — and the redirect narrated a false "requested 'X' was
    # busy" for a lane nothing held (the TestRedirectReasonHonesty disease,
    # KNOWN-free-name edition). The hint differs from an explicit `--kind
    # cluster` only on the REFUSE arm: a conjunction refusal (a colliding tree, a
    # request-absolute predicate) falls through to the walk with the real reason
    # recorded for `_redirect_why` — a soft hint never hard-refuses. A HELD
    # hinted name keeps the same-lane refuse above; an UNKNOWN one keeps the
    # honest "not a lane in this workspace" redirect.
    _soft_cluster_hint = (
        not requested_kind and bool(requested_lane)
        and requested_lane_key in {_lane_key(c) for c in lanes.concurrent}
    )
    if (requested_kind == "cluster" or _soft_cluster_hint) \
            and requested_lane_key not in live_lanes:
        tree = requested_tree or _cluster_tree(requested_lane)
        verdict = _admission_verdict(
            lane=requested_lane, kind="cluster", tree=tree,
            live_leases=live_leases, predicates=preds, config=cfg,
            mode=requested_mode,
        )
        if verdict.admitted:
            return LaneDecision(
                "acquire", lane=requested_lane, lane_kind="cluster",
                tree=tree, auto_picked=False,
                reason=f"cluster lane {requested_lane!r} free — admitted.",
            )
        if requested_kind == "cluster":
            return LaneDecision(
                "refuse", reason=verdict.reason, free_clusters=_free_clusters(),
            )
        # The soft hint was refused by the conjunction — record the real reason
        # for the redirect parenthetical and fall through to the auto-pick walk
        # rather than hard-refusing a kind the caller never named.
        _hint_refusal["reason"] = verdict.reason

    # Keyword request with a NON-EMPTY tree → run the admission conjunction
    # (lock-mode disjointness + self-modify + any workspace predicate). First
    # refusal wins; a disjoint or shared/shared-compatible keyword lane admits,
    # while a self-modifying one refuses with the typed SELF_MODIFY reason it
    # could not carry before.
    if requested_kind == "keyword" and requested_tree:
        verdict = _admission_verdict(
            lane=requested_lane, kind="keyword", tree=requested_tree,
            live_leases=live_leases, predicates=preds, config=cfg,
            mode=requested_mode,
        )
        if not verdict.admitted:
            reason = f"{verdict.reason} Use a free cluster lane instead."
            return LaneDecision(
                "refuse", reason=reason, free_clusters=_free_clusters(),
            )
        return LaneDecision(
            "acquire", lane=requested_lane, lane_kind="keyword",
            tree=requested_tree, auto_picked=False,
            reason=(f"keyword lane {requested_lane!r} admitted (disjoint or "
                    f"lock-mode compatible vs all "
                    f"{len(live_leases)} live lease(s))."),
        )

    # Keyword request whose tree is EMPTY → degrade to "take any open plan".
    unresolved_keyword = requested_kind == "keyword" and not requested_tree

    # UNKNOWN_LANE (docs/104 §4, control-flow arm). An empty-tree keyword splits
    # into two epistemically DIFFERENT requests that the old code conflated:
    #   (a) a name THIS workspace knows (a real lane/alias whose live plan just
    #       isn't running right now) → legitimate "I wanted that, fall through to
    #       auto-pick" — the `_unresolved_suffix` degrade below is correct.
    #   (b) a name the taxonomy has NEVER heard of (`playbooks`, a typo, a foreign
    #       lane) → the operator asserted a SPECIFIC concern the kernel cannot
    #       place. Silently auto-picking a DIFFERENT free lane (CID) is the docs/103
    #       disease turned inward: the kernel narrates `acquire` for a region it was
    #       not asked about, and the lease then describes the WRONG tree — so
    #       disjointness is computed against a region the agent will not touch (a
    #       soundness hole, not just a misleading reason). Auto-pick's license is
    #       "you expressed NO preference"; it does not extend to substituting a
    #       concern you named. The honest verdict is a typed refuse that surfaces
    #       the lanes this workspace actually knows — never a guess.
    # This is the control-flow twin of `_redirect_why` (which already fixed the
    # *reason string* for the busy-redirect path but left the DEGRADE in place).
    if (unresolved_keyword and requested_lane
            and requested_lane_key not in _known_lane_keys):
        _known_sorted = sorted(
            {n for n in (*lanes.concurrent, *lanes.exclusive, *lanes.autopick)})
        return LaneDecision(
            "refuse",
            reason=(
                f"UNKNOWN_LANE: {requested_lane!r} is not a lane in this "
                f"workspace, so the kernel will not guess a substitute for it "
                f"(auto-pick only chooses when you express NO preference). "
                f"Known lanes: {', '.join(_known_sorted) or '(none)'}. "
                f"Pass one of those as --lane, run a bare invocation to "
                f"auto-pick any free lane, or register {requested_lane!r} as a "
                f"lane in dos.toml."),
            free_clusters=_free_clusters(),
        )

    bare = (not requested_lane) or unresolved_keyword
    _unresolved_suffix = (
        f" (requested --lane {requested_lane!r} matched no live plan — "
        f"degraded to auto-pick)" if unresolved_keyword else "")

    # FQ-449 single-pick-ceiling: the bare auto-pick PREFERS a ladder lane whose
    # tree is provably disjoint from every un-leased live SIBLING, falling back to
    # the unchanged walk only if none is. Active only when the host passed
    # `live_siblings` + `sibling_tree_lookup`; otherwise byte-identical to the
    # pre-fix first-fit. (A lease is already gated by `_lease_collision`; a sibling
    # holds no lease yet but will collide post-acquire — without this, the bare loop
    # picks the top lane, acquires it, then self-arrests at the Step-0 sibling-scan,
    # shipping at most one pick. Spreading the fleet across disjoint lanes at
    # SELECTION time is the structural fix.)
    _sibling_filter_active = bool(live_siblings) and sibling_tree_lookup is not None

    def _bare_pass(*, require_sibling_disjoint: bool) -> LaneDecision | None:
        """One walk of the bare auto-pick ladder; returns a decision or None.

        The legacy first-disjoint-wins loop verbatim, with ONE added gate when
        `require_sibling_disjoint` is True: a candidate that passes the lease
        disjointness + availability gates must ALSO be disjoint from every live
        sibling, else it is skipped. Re-establishes the refuse-path bookkeeping
        from scratch on each call (so whichever pass falls through last leaves the
        canonical flags the refuse branches read). The `nonlocal` set mirrors the
        flags the original inline loop mutated.
        """
        nonlocal _saw_budget_skip, _saw_non_budget_candidate
        nonlocal _saw_any_candidate, _all_disjoint_were_zero, _last_pick_count
        _saw_budget_skip = False
        _saw_non_budget_candidate = False
        _saw_any_candidate = False
        _all_disjoint_were_zero = True
        _last_pick_count = None
        # `_ranked` reorders by descending rank when a `rank_key` is supplied,
        # leaving the list UNCHANGED otherwise — so over the reordered list this
        # returns the highest-RANKED lane that is also disjoint+available, and with
        # no `rank_key` it is exactly the old first-fit. Ranking picks the order;
        # the gate still decides.
        ranked_picked = rank_key is not None
        for name, kind, tree_seq in _ranked(auto_pick_order):
            tree_list = list(tree_seq or [])
            if not tree_list:
                continue
            if name in live_lanes:
                continue
            # docs/97 Phase 1 — class budget gate, BEFORE the disjointness
            # check: a candidate whose kind is already at its `max_concurrent`
            # is skipped regardless of whether its tree would be disjoint, so
            # the arbiter never mints an (N+1)-th holder of a budgeted class.
            # The check is on `kind or "cluster"` to match the kind the admit
            # path below leases under (an empty kind defaults to "cluster").
            _cand_kind = kind or "cluster"
            if _budget_exhausted(_cand_kind):
                _saw_budget_skip = True
                continue
            _saw_non_budget_candidate = True
            if not _lease_collision(
                    lane=name, kind=_cand_kind, tree=tree_list,
                    live_leases=live_leases, predicates=preds, config=cfg,
                    mode=requested_mode):
                # FQ-449 first pass: also require sibling-disjointness. A candidate
                # that overlaps a live sibling is skipped here so a LATER ladder
                # lane (disjoint from both leases AND siblings) can win; the second
                # pass (require=False) re-admits it as the unchanged last resort.
                if require_sibling_disjoint and not _disjoint_from_all_live(
                        requested_tree=tree_list,
                        live=list(live_siblings or []),
                        sibling_tree_lookup=sibling_tree_lookup):
                    continue
                if not _admit_lane(name, _cand_kind, tree_list):
                    continue
                return LaneDecision(
                    "acquire", lane=name, lane_kind=_cand_kind,
                    tree=tree_list, auto_picked=True,
                    reason=(f"auto-picked {_cand_kind} lane {name!r} "
                            + ("by value-aware rank" if ranked_picked
                               else "from priority ladder")
                            + (" (disjoint from all live siblings)"
                               if require_sibling_disjoint else "")
                            + "." + _unresolved_suffix),
                    pick_count=_last_pick_count,  # same call that drove admission
                )
        return None

    if auto_pick_order is not None:
        if bare:
            if _sibling_filter_active:
                _decided = _bare_pass(require_sibling_disjoint=True)
                if _decided is not None:
                    return _decided
                # No sibling-disjoint lane on the ladder — fall back to the
                # unchanged walk (which may pick a sibling-overlapping lane, exactly
                # today's behavior; the post-acquire sibling-scan then handles it).
            _decided = _bare_pass(require_sibling_disjoint=False)
            if _decided is not None:
                return _decided
        else:
            for cand in autopick_clusters:
                if cand in live_lanes:
                    continue
                cand_tree = _cluster_tree(cand)
                if not _lease_collision(
                        lane=cand, kind="cluster", tree=cand_tree,
                        live_leases=live_leases, predicates=preds, config=cfg,
                        mode=requested_mode):
                    if not _admit_lane(cand, "cluster", cand_tree):
                        continue
                    return LaneDecision(
                        "acquire", lane=cand, lane_kind="cluster",
                        tree=cand_tree, auto_picked=True,
                        reason=(f"auto-picked free cluster lane {cand!r} "
                                + _redirect_why(
                                    f"(requested {requested_lane!r} was busy)")
                                + "."),
                        pick_count=_last_pick_count,  # same call that drove admission
                    )

        # docs/97 Phase 1 — the class-budget refuse, FIRST (most specific cause).
        # When the bare walk admitted nothing AND every candidate it would have
        # tried was budget-skipped (no candidate failed for any OTHER reason), the
        # binding constraint is a concurrency budget, not drained work or a tree
        # collision. Surface that honestly: the lever is "wait for a slot of this
        # class to free" — NOT /replan (the work exists; the class is just full) and
        # NOT --lane (a scoped request of the same kind hits the same budget). The
        # CLASS_BUDGET_EXHAUSTED token mirrors docs/97's named refuse so a downstream
        # cause-classifier can route it distinctly from DRAIN / ladder-exhausted.
        if _saw_budget_skip and not _saw_non_budget_candidate:
            _at = sorted(
                f"{k} ({_live_kind_counts.get(k, 0)}/{_budgets[k]})"
                for k in _budgets if _budget_exhausted(k)
            )
            return LaneDecision(
                "refuse",
                reason=("CLASS_BUDGET_EXHAUSTED: every auto-pick candidate belongs "
                        "to a concurrency class already at its max_concurrent "
                        f"budget ({', '.join(_at)}) — admitting one would exceed the "
                        "budget. The work exists and the regions are fine; the class "
                        "is simply full. Wait for a holder of that class to release "
                        "(do NOT /replan — there is nothing to refill), or raise the "
                        "class budget if the concurrency is genuinely safe."
                        + _unresolved_suffix),
                free_clusters=[c for c in autopick_clusters if c not in live_lanes],
            )

        if _saw_any_candidate and _all_disjoint_were_zero:
            return LaneDecision(
                "refuse",
                reason=("every concurrency-free lane on the priority ladder has "
                        "0 pickable phases right now (all soak-gated / sibling-"
                        "gated / already claimed) — leasing one would only DRAIN. "
                        "Refusing at Step 0 instead. Run /replan, wait for an open "
                        "soak window to close, or pass --lane <lane-with-work>."
                        + _unresolved_suffix),
                free_clusters=[c for c in autopick_clusters if c not in live_lanes],
                pick_count=0,
            )

        return LaneDecision(
            "refuse",
            reason=("priority ladder exhausted; no lane is free with a tree "
                    "disjoint from every live lease. Wait for one to release, "
                    "or pass --lane <free-lane> explicitly."
                    + _unresolved_suffix),
            free_clusters=[c for c in autopick_clusters if c not in live_lanes],
        )

    # ── LEGACY PATH — no ladder supplied. Three-rung fallback. ──────────────
    for cand in autopick_clusters:
        if cand in live_lanes:
            continue
        cand_tree = _cluster_tree(cand)
        if not _lease_collision(
                lane=cand, kind="cluster", tree=cand_tree,
                live_leases=live_leases, predicates=preds, config=cfg,
                mode=requested_mode):
            if unresolved_keyword:
                why = _unresolved_suffix.strip()
            elif requested_lane:
                why = _redirect_why(f"(requested {requested_lane!r} was busy)")
            else:
                why = "(bare invocation)"
            return LaneDecision(
                "acquire", lane=cand, lane_kind="cluster",
                tree=cand_tree, auto_picked=True,
                reason=f"auto-picked free cluster lane {cand!r} {why}.",
            )
    if bare:
        for name, tree_tup in (named_lanes or ()):
            tree_list = list(tree_tup)
            if not tree_list or name in live_lanes:
                continue
            if not _lease_collision(
                    lane=name, kind="named", tree=tree_list,
                    live_leases=live_leases, predicates=preds, config=cfg,
                    mode=requested_mode):
                return LaneDecision(
                    "acquire", lane=name, lane_kind="named",
                    tree=tree_list, auto_picked=True,
                    reason=(f"auto-picked named non-cluster lane {name!r} "
                            f"(legacy fallback path)."),
                )
        for plan_id, tree_list in (derived_lanes or []):
            if not tree_list or plan_id in live_lanes:
                continue
            if not _lease_collision(
                    lane=plan_id, kind="derived", tree=list(tree_list),
                    live_leases=live_leases, predicates=preds, config=cfg,
                    mode=requested_mode):
                return LaneDecision(
                    "acquire", lane=plan_id, lane_kind="derived",
                    tree=list(tree_list), auto_picked=True,
                    reason=(f"auto-picked derived plan lane {plan_id!r} "
                            f"(legacy fallback path)."),
                )
    # The autopick walk found no free candidate. But "no autopick candidate"
    # is NOT the same world as "every cluster lane is held" — a concurrent
    # cluster lane can be free yet off the autopick ladder (issue #118). Only
    # narrate the all-held world when it is actually true; otherwise name the
    # real cause (requested lane held + the ladder offers nothing) and point at
    # the free lanes a `--lane` could take, as the kindless same-lane refuse
    # above already does.
    _free = _free_concurrent_clusters()
    if _free:
        return LaneDecision(
            "refuse",
            reason=(f"the requested lane {requested_lane!r} is held and the "
                    "autopick ladder offers no free candidate — but other "
                    f"concurrent cluster lanes are free ({', '.join(_free)}). "
                    "Pass --lane <free-lane> to take one, or wait for the held "
                    "lane to release."
                    if requested_lane else
                    "the autopick ladder offers no free candidate — but other "
                    f"concurrent cluster lanes are free ({', '.join(_free)}). "
                    "Pass --lane <free-lane> to take one."
                    ) + _unresolved_suffix,
            free_clusters=_free,
        )
    return LaneDecision(
        "refuse",
        reason=("all concurrent cluster lanes are held by live loops — no free "
                "lane to auto-pick. Wait for one to finish, then re-invoke."
                + _unresolved_suffix),
        free_clusters=[],
    )


# Back-compat alias: the origin function was named `arbitrate_lane`.
arbitrate_lane = arbitrate
