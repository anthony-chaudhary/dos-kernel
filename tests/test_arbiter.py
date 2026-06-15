"""Replay tests for the pure lane-admission kernel (`dos.arbiter`).

Mirrors the origin repo's `tests/test_dispatch_lane.py` — the same safety matrix
(no-leases / disjoint-concurrency / same-lane-refused / exclusive-lanes), proving
the extracted arbiter behaves identically when fed the reference userland app's
lane taxonomy as data instead of reading it from module-level constants.
"""

from __future__ import annotations

import dataclasses

from dos import arbiter
from dos.config import job_config

CFG = job_config("/work/userland-app")

_APPLY_TREE = ["agents/apply_*.py", "agents/_phase_*.py", "agents/apply_artifact_verdict.py"]
_TAILOR_TREE = ["agents/tailor_*.py", "agents/tailor_steps/", "templates/"]
_DISCOVERY_TREE = ["agents/discovery_*.py", "job_search/scoring.py"]

# A SYNTHETIC taxonomy that POPULATES `concurrent`/`autopick` AND their `trees`,
# for the few tests that exercise the kernel's *cluster-privilege mechanism* (the
# legacy `autopick` bare-walk fallback + the auto-pick-redirect honesty). The
# reference `job_config()` taxonomy is now de-clustered (`concurrent=()`/
# `autopick=()`, 2026-06-02) AND, as of 2026-06-06 (dos/119, dynamic-claim-space),
# carries NO curated work-lane trees at all — a work lane is a handle resolving to
# a per-pick claim, so `trees` declares only the exclusive lanes. The cluster
# mechanism therefore can no longer be tested THROUGH the reference taxonomy on
# EITHER axis (names or trees). Building a complete fixture here keeps the kernel
# mechanism covered AND keeps these tests domain-free: the arbiter must auto-pick /
# narrate honestly for ANY taxonomy that declares `autopick` with trees, not just
# the reference app's. The trees are supplied explicitly (they used to be inherited
# from the reference taxonomy, which no longer has them) so the disjointness
# algebra is realistic.
CLUSTERED_CFG = dataclasses.replace(
    CFG,
    lanes=dataclasses.replace(
        CFG.lanes,
        concurrent=("apply", "tailor", "discovery"),
        autopick=("apply", "tailor", "discovery"),
        trees={
            **CFG.lanes.trees,  # keep the structural exclusive lanes (orchestration/global)
            "apply": tuple(_APPLY_TREE),
            "tailor": tuple(_TAILOR_TREE),
            "discovery": tuple(_DISCOVERY_TREE),
        },
    ),
)


def _lease(lane, kind, tree):
    return {"lane": lane, "lane_kind": kind, "tree": tree, "loop_ts": "20260531T1200Z"}


def _arb(**kw):
    kw.setdefault("config", CFG)
    kw.setdefault("requested_tree", [])
    kw.setdefault("live_leases", [])
    return arbiter.arbitrate(**kw)


def _arb_clustered(**kw):
    """`_arb` against the synthetic taxonomy that still declares autopick clusters."""
    kw.setdefault("config", CLUSTERED_CFG)
    return _arb(**kw)


class TestNoLiveLeases:
    def test_cluster_request_acquires_directly(self):
        d = _arb(requested_lane="apply", requested_kind="cluster")
        assert d.outcome == "acquire"
        assert d.lane == "apply"
        assert not d.auto_picked

    def test_bare_request_auto_picks_first_cluster(self):
        # Mechanism test: a bare request with NO explicit `auto_pick_order` walks
        # the taxonomy's `autopick` tuple. The reference taxonomy is de-clustered
        # (empty autopick), so this exercises the synthetic clustered fixture —
        # the kernel's legacy fallback must still work for any taxonomy that
        # declares autopick lanes.
        d = _arb_clustered(requested_lane="", requested_kind="")
        assert d.outcome == "acquire"
        assert d.lane == "apply"
        assert d.auto_picked

    def test_global_request_acquires_when_alone(self):
        d = _arb(requested_lane="global", requested_kind="global")
        assert d.outcome == "acquire"
        assert d.lane == "global"

    def test_orchestration_request_acquires_when_alone(self):
        d = _arb(requested_lane="orchestration", requested_kind="orchestration")
        assert d.outcome == "acquire"
        assert d.lane == "orchestration"


class TestDisjointConcurrency:
    def test_disjoint_cluster_pair_both_acquire(self):
        # The requested lane carries its derived tree explicitly (requested_tree) —
        # the dynamic-claim-space shape: a `--scope tailor` request resolves to its
        # per-pick footprint host-side, then hands the kernel the tree. (Pre-2026-06-06
        # the kernel could derive it from `CFG.lanes.trees["tailor"]`, but the
        # reference taxonomy no longer carries curated work-lane trees.)
        live = [_lease("apply", "cluster", _APPLY_TREE)]
        d = _arb(requested_lane="tailor", requested_kind="cluster",
                 requested_tree=_TAILOR_TREE, live_leases=live)
        assert d.outcome == "acquire"
        assert d.lane == "tailor"

    def test_three_clusters_can_all_be_held(self):
        live = [_lease("apply", "cluster", _APPLY_TREE),
                _lease("tailor", "cluster", _TAILOR_TREE)]
        d = _arb(requested_lane="discovery", requested_kind="cluster",
                 requested_tree=_DISCOVERY_TREE, live_leases=live)
        assert d.outcome == "acquire"
        assert d.lane == "discovery"


class TestSameLaneRefused:
    def test_cluster_request_for_held_lane_auto_picks_a_free_one(self):
        # Mechanism test: the same-lane cluster reroute walks `autopick`; uses the
        # synthetic clustered fixture (reference taxonomy is de-clustered).
        live = [_lease("apply", "cluster", _APPLY_TREE)]
        d = _arb_clustered(requested_lane="apply", requested_kind="cluster",
                           live_leases=live)
        assert d.outcome == "acquire"
        assert d.lane in ("tailor", "discovery")
        assert d.auto_picked

    def test_auto_pick_exhaustion_refuses(self):
        live = [_lease("apply", "cluster", _APPLY_TREE),
                _lease("tailor", "cluster", _TAILOR_TREE),
                _lease("discovery", "cluster", _DISCOVERY_TREE)]
        d = _arb(requested_lane="apply", requested_kind="cluster", live_leases=live)
        assert d.outcome == "refuse"

    def test_keyword_request_for_held_keyword_lane_refused(self):
        live = [_lease("workday", "keyword", ["playbooks/ats/workday.yaml"])]
        d = _arb(requested_lane="workday", requested_kind="keyword",
                 requested_tree=["playbooks/ats/workday.yaml"], live_leases=live)
        assert d.outcome == "refuse"


class TestExclusiveLanes:
    def test_global_refused_when_a_cluster_lease_is_live(self):
        live = [_lease("apply", "cluster", _APPLY_TREE)]
        d = _arb(requested_lane="global", requested_kind="global", live_leases=live)
        assert d.outcome == "refuse"

    def test_a_live_global_lease_refuses_a_cluster_request(self):
        live = [_lease("global", "global", [])]
        d = _arb(requested_lane="apply", requested_kind="cluster", live_leases=live)
        assert d.outcome == "refuse"

    def test_a_live_orchestration_lease_refuses_bare_request(self):
        live = [_lease("orchestration", "orchestration", ["scripts/next_up*.py"])]
        d = _arb(requested_lane="", requested_kind="", live_leases=live)
        assert d.outcome == "refuse"


class TestForceOverride:
    def test_force_honors_named_lane_despite_same_lane_collision(self):
        live = [_lease("apply", "cluster", _APPLY_TREE)]
        d = _arb(requested_lane="apply", requested_kind="cluster",
                 requested_tree=_APPLY_TREE, live_leases=live, force=True)
        assert d.outcome == "acquire"
        assert d.lane == "apply"

    def test_force_cannot_override_a_live_exclusive_lane(self):
        live = [_lease("global", "global", [])]
        d = _arb(requested_lane="apply", requested_kind="cluster",
                 requested_tree=_APPLY_TREE, live_leases=live, force=True)
        assert d.outcome == "refuse"


class TestPickOracleGate:
    def test_zero_pick_lanes_are_skipped_then_refused(self):
        # Every lane reports 0 picks → refuse at Step 0, never lease an empty lane.
        ladder = [("apply", "cluster", _APPLY_TREE),
                  ("tailor", "cluster", _TAILOR_TREE)]
        d = _arb(requested_lane="", requested_kind="",
                 auto_pick_order=ladder, pick_oracle=lambda n, k, t: 0)
        assert d.outcome == "refuse"
        assert d.pick_count == 0

    def test_a_lane_with_picks_is_admitted(self):
        ladder = [("apply", "cluster", _APPLY_TREE),
                  ("tailor", "cluster", _TAILOR_TREE)]
        d = _arb(requested_lane="", requested_kind="",
                 auto_pick_order=ladder,
                 pick_oracle=lambda n, k, t: 3 if n == "tailor" else 0)
        assert d.outcome == "acquire"
        assert d.lane == "tailor"


class TestProjectInvariance:
    def test_generic_default_taxonomy_arbitrates(self):
        # The third-directory case: a generic `main`/`global` taxonomy still
        # arbitrates cleanly — proving the kernel never hard-codes a host's lanes.
        #
        # The generic `main` lane carries the everything-tree `**/*`. `default_config`
        # GATHERS workspace facts at build time, so the config knows `C:/tmp` is a
        # foreign repo (no `src/dos/` files exist) — the arbiter's own default reads
        # those CACHED facts (no I/O in `arbitrate`), the SELF_MODIFY guard admits,
        # and the lane acquires. No boundary `predicates=` needed: a config built
        # the normal way is already workspace-aware.
        from dos.config import default_config
        cfg = default_config("C:/tmp")
        d = arbiter.arbitrate(requested_lane="main", requested_kind="cluster",
                              requested_tree=[], live_leases=[], config=cfg)
        assert d.outcome == "acquire"
        assert d.lane == "main"

    def test_facts_carrying_config_admits_foreign_whole_repo_lane(self):
        # The corrected posture (the live-run finding fix): a config that CARRIES
        # gathered workspace facts is TRUSTED by the pure default. `C:/tmp` is
        # foreign, so its facts say "no kernel files here" and a whole-repo `**/*`
        # lane is NOT self-modifying → admit. The I/O happened at config-build
        # time; `arbitrate` itself stays pure (it reads cached data). This is the
        # fix for the foreign-repo over-refusal that `import dos` consumers hit.
        from dos.config import default_config
        cfg = default_config("C:/tmp")
        assert cfg.workspace is not None and cfg.workspace.kernel_runtime_files == ()
        d = arbiter.arbitrate(requested_lane="main", requested_kind="cluster",
                              requested_tree=[], live_leases=[], config=cfg)
        assert d.outcome == "acquire"

    def test_factless_config_is_conservative_about_whole_repo_self_modify(self):
        # The conservative path is now keyed on ABSENT facts, not on "no
        # workspace= arg". A hand-built config whose `workspace` facts were never
        # gathered (`None`) cannot know the repo is foreign, so the pure default
        # stays safe-by-direction: a whole-repo `**/*` lane is treated as
        # self-modifying and REFUSES. Purity is about WHEN the probe runs (build
        # time, not arbitrate time), so a factless config gets the full static set.
        import dataclasses
        from dos.config import default_config
        cfg = dataclasses.replace(default_config("C:/tmp"), workspace=None)
        assert cfg.workspace is None
        d = arbiter.arbitrate(requested_lane="main", requested_kind="cluster",
                              requested_tree=[], live_leases=[], config=cfg)
        assert d.outcome == "refuse"


class TestWholeRepoTreeCollision:
    """Regression for the `**/*`-normalizes-to-empty disjointness bug.

    Two whole-repo CONCURRENT lanes on DIFFERENT names slip past the lane-identity
    refusal (different lanes) and reach the disjointness predicate, which BEFORE
    the fix called two `**/*` trees "fully disjoint" and admitted them — two agents
    over the entire repo. The live lease is CONCURRENT (not exclusive) so the
    arbiter's exclusive-lane short-circuit does NOT fire — isolating the
    disjointness predicate as the actual gate. SELF_MODIFY is scoped to a foreign
    workspace (tmp_path has no src/dos files) so it stays out of the way too.
    """

    def _two_concurrent_whole_repo_cfg(self, root):
        # Both `api` and `worker` are concurrent clusters carrying the everything
        # tree — the shape that exposes the bug (different names, same `**/*`).
        from dos.config import default_config
        import dataclasses
        base = default_config(root)
        lanes = dataclasses.replace(
            base.lanes,
            concurrent=("api", "worker"),
            autopick=("api", "worker"),
            exclusive=("global",),
            trees={"api": ("**/*",), "worker": ("**/*",), "global": ("**/*",)},
        )
        return dataclasses.replace(base, lanes=lanes)

    def _scoped_preds(self, cfg):
        from dos.admission import built_in_predicates
        return built_in_predicates(workspace=cfg.root)

    def test_two_whole_repo_concurrent_lanes_refuse(self, tmp_path):
        cfg = self._two_concurrent_whole_repo_cfg(tmp_path)
        # `worker` (concurrent) holds the everything-tree; request `api` (also
        # concurrent, also `**/*`). Different lane names → no identity refusal;
        # both concurrent → no exclusive short-circuit. Only disjointness stands
        # between them, and it must REFUSE (pre-fix: it wrongly admitted).
        live = [{"lane": "worker", "lane_kind": "cluster", "tree": ["**/*"],
                 "loop_ts": "20260601T0000Z"}]
        d = arbiter.arbitrate(
            requested_lane="api", requested_kind="cluster",
            requested_tree=["**/*"], live_leases=live, config=cfg,
            predicates=self._scoped_preds(cfg),
        )
        assert d.outcome == "refuse", (
            "two whole-repo concurrent lanes must collide on disjointness, not "
            "slip through as 'fully disjoint'"
        )

    def test_force_overrides_the_disjointness_collision(self, tmp_path):
        cfg = self._two_concurrent_whole_repo_cfg(tmp_path)
        live = [{"lane": "worker", "lane_kind": "cluster", "tree": ["**/*"],
                 "loop_ts": "20260601T0000Z"}]
        # --force skips the disjointness/overlap refuse for an explicit lane (the
        # live lease is concurrent, not exclusive, so force IS allowed to punch
        # through). Pins that the fix left the documented force override intact.
        d = arbiter.arbitrate(
            requested_lane="api", requested_kind="cluster",
            requested_tree=["**/*"], live_leases=live, config=cfg, force=True,
            predicates=self._scoped_preds(cfg),
        )
        assert d.outcome == "acquire"
        assert d.lane == "api"


class TestValueAwarePicker:
    """The value-aware bare-auto-pick reorder (docs/91, research-area §90.3).

    `rank_key` chooses the ORDER the unchanged disjointness+availability gate is
    tried in; the admitted lane is the highest-ranked one that is also admissible
    (argmax over the admissible set). With `rank_key=None` the walk is byte-
    identical to the legacy first-disjoint-wins ladder (the regression guard), and
    a `rank_key` can NEVER admit a colliding lane (the soundness floor).
    """

    LADDER = [("apply", "cluster", _APPLY_TREE),
              ("tailor", "cluster", _TAILOR_TREE),
              ("discovery", "cluster", _DISCOVERY_TREE)]

    def test_no_rank_key_is_byte_identical_first_fit(self):
        # Absent a ranker, the bare walk still takes the FIRST ladder lane — the
        # documented regression guard (rank_key=None must not change anything).
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self.LADDER)
        assert d.outcome == "acquire"
        assert d.lane == "apply"          # ladder order, unchanged
        assert "priority ladder" in d.reason

    def test_rank_key_picks_the_argmax_not_the_first(self):
        # `discovery` is LAST on the ladder but ranks highest → it wins over the
        # first-fit `apply`. Proves the reorder selects the argmax.
        rank = {"apply": 1.0, "tailor": 2.0, "discovery": 9.0}
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self.LADDER,
                 rank_key=lambda n, k, t: rank.get(n))
        assert d.outcome == "acquire"
        assert d.lane == "discovery"
        assert "value-aware rank" in d.reason

    def test_rank_key_skips_to_next_best_when_top_is_busy(self):
        # Highest-ranked `discovery` is already held → argmax over the ADMISSIBLE
        # set is the next-highest disjoint lane (`tailor`), never the busy top.
        live = [_lease("discovery", "cluster", _DISCOVERY_TREE)]
        rank = {"apply": 1.0, "tailor": 2.0, "discovery": 9.0}
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self.LADDER,
                 live_leases=live, rank_key=lambda n, k, t: rank.get(n))
        assert d.outcome == "acquire"
        assert d.lane == "tailor"

    def test_rank_key_that_raises_falls_back_to_ladder(self):
        # A broken estimator must degrade to ladder order, never crash (fail-soft,
        # the `pick_oracle` best-effort rule applied to ranking).
        def boom(n, k, t):
            raise RuntimeError("estimator exploded")
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self.LADDER,
                 rank_key=boom)
        assert d.outcome == "acquire"
        assert d.lane == "apply"          # ladder order — the raise was swallowed

    def test_none_rank_sinks_below_opinionated_candidates(self):
        # `apply` gets no opinion (None) while the later `tailor` is ranked → the
        # opinionated lane is tried first, so a None-ranked earlier lane does NOT
        # win by ladder position. (None sinks below every ranked candidate.)
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self.LADDER,
                 rank_key=lambda n, k, t: 5.0 if n == "tailor" else None)
        assert d.outcome == "acquire"
        assert d.lane == "tailor"

    def test_rank_cannot_admit_a_colliding_lane(self):
        # THE SOUNDNESS PIN. A live lease holds `apply`'s tree; a malicious ranker
        # scores the colliding `apply` highest. Ranking only sets the visiting
        # order — the disjointness gate still refuses `apply` and the arbiter falls
        # to the next disjoint lane. Rank can never re-admit a collision.
        live = [_lease("apply", "cluster", _APPLY_TREE)]
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self.LADDER,
                 live_leases=live,
                 rank_key=lambda n, k, t: 99.0 if n == "apply" else 1.0)
        assert d.outcome == "acquire"
        assert d.lane != "apply"          # the highest-ranked lane was NOT admitted
        assert d.lane in ("tailor", "discovery")

    def test_rank_among_busy_still_refuses_when_all_collide(self):
        # If every candidate collides, a ranker cannot manufacture an admission —
        # the walk falls through to the ladder-exhausted refuse.
        live = [_lease("apply", "cluster", _APPLY_TREE),
                _lease("tailor", "cluster", _TAILOR_TREE),
                _lease("discovery", "cluster", _DISCOVERY_TREE)]
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self.LADDER,
                 live_leases=live, rank_key=lambda n, k, t: 1.0)
        assert d.outcome == "refuse"


class TestSiblingDisjointPreference:
    """FQ-449 single-pick-ceiling: the bare auto-pick PREFERS a ladder lane disjoint
    from every un-leased live SIBLING, falling back to the unchanged first-fit walk
    only when none is sibling-disjoint.

    A *sibling* (unlike a lease) holds no lease yet but will collide post-acquire.
    Without this, the bare loop picks the top-priority lane, acquires it, then self-
    arrests at the Step-0 sibling-scan — shipping at most one pick. Spreading across
    disjoint lanes at SELECTION time is the structural fix. With no `live_siblings`
    the walk is byte-identical to the legacy first-fit (the regression guard); the
    filter can NEVER admit a lane that collides with a real lease (the lease gate
    still wins). These call `arbitrate` directly (hermetic — no host adapter, no
    monkeypatched gatherer).
    """

    LADDER = [("apply", "cluster", _APPLY_TREE),
              ("tailor", "cluster", _TAILOR_TREE),
              ("discovery", "cluster", _DISCOVERY_TREE)]

    @staticmethod
    def _lookup(mapping):
        """A sibling lane -> tree resolver over a fixed dict (host scans loop dirs)."""
        return lambda lane: mapping.get(lane)

    def _sib(self, lane):
        """An un-leased live sibling — a dict with at least {lane} (no lease)."""
        return {"lane": lane}

    def test_no_siblings_is_byte_identical_first_fit(self):
        # The regression guard: with no live_siblings, the bare walk is unchanged —
        # the top ladder lane wins, the reason carries no sibling annotation.
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self.LADDER)
        assert d.outcome == "acquire"
        assert d.lane == "apply"
        assert "priority ladder" in d.reason
        assert "disjoint from all live siblings" not in d.reason

    def test_passing_lookup_without_siblings_is_still_first_fit(self):
        # The lookup alone (empty sibling list) must not arm the filter — the first
        # pass only runs when there is at least one sibling to avoid.
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self.LADDER,
                 live_siblings=[],
                 sibling_tree_lookup=self._lookup({"apply": _APPLY_TREE}))
        assert d.outcome == "acquire"
        assert d.lane == "apply"
        assert "disjoint from all live siblings" not in d.reason

    def test_prefers_disjoint_lane_over_top_overlapping_sibling(self):
        # THE CEILING FIX. The top lane `apply` overlaps an un-leased sibling that
        # is editing the apply tree. Instead of acquiring `apply` and self-arresting,
        # the walk skips it and picks the next ladder lane disjoint from the sibling.
        siblings = [self._sib("apply")]
        lookup = self._lookup({"apply": _APPLY_TREE})
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self.LADDER,
                 live_siblings=siblings, sibling_tree_lookup=lookup)
        assert d.outcome == "acquire"
        assert d.lane == "tailor"          # apply skipped — it overlaps the sibling
        assert "disjoint from all live siblings" in d.reason

    def test_falls_back_to_top_lane_when_all_overlap_a_sibling(self):
        # D4 fallback contract: if NO ladder lane is sibling-disjoint, the worst case
        # must be byte-identical to today — admit the top-priority lane anyway and
        # let the post-acquire sibling-scan handle it. Never block harder than now.
        siblings = [self._sib("apply"), self._sib("tailor"), self._sib("discovery")]
        lookup = self._lookup({"apply": _APPLY_TREE, "tailor": _TAILOR_TREE,
                               "discovery": _DISCOVERY_TREE})
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self.LADDER,
                 live_siblings=siblings, sibling_tree_lookup=lookup)
        assert d.outcome == "acquire"
        assert d.lane == "apply"           # fell back to the top lane (today's behavior)
        assert "disjoint from all live siblings" not in d.reason

    def test_filter_never_admits_a_lane_colliding_with_a_lease(self):
        # THE SOUNDNESS PIN. The sibling filter is layered ON TOP of the unchanged
        # lease-disjointness gate — it can only ADD skips, never re-admit a leased
        # collision. `apply` is held by a real LEASE; a sibling sits on `tailor`.
        # The walk must skip apply (lease) AND tailor (sibling) → discovery.
        live = [_lease("apply", "cluster", _APPLY_TREE)]
        siblings = [self._sib("tailor")]
        lookup = self._lookup({"tailor": _TAILOR_TREE})
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self.LADDER,
                 live_leases=live, live_siblings=siblings, sibling_tree_lookup=lookup)
        assert d.outcome == "acquire"
        assert d.lane == "discovery"

    def test_unknown_sibling_tree_is_conservative_overlap(self):
        # A sibling whose tree the lookup cannot resolve (returns None) is an UNKNOWN
        # blast radius — treated as overlapping EVERY candidate, so the first pass
        # finds nothing and the walk falls back to the top lane (never silently
        # treats unknown == safe). Mirrors lane_trees_disjoint's empty-tree posture.
        siblings = [self._sib("mystery")]
        lookup = self._lookup({})          # 'mystery' resolves to None
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self.LADDER,
                 live_siblings=siblings, sibling_tree_lookup=lookup)
        assert d.outcome == "acquire"
        assert d.lane == "apply"           # no lane provably disjoint → fall back
        assert "disjoint from all live siblings" not in d.reason

    def test_sibling_filter_composes_with_rank_key(self):
        # The sibling filter runs inside the SAME walk the ranker reorders, so the
        # admitted lane is the highest-RANKED lane that is also disjoint from leases
        # AND from all siblings. Top-ranked `apply` overlaps a sibling → the next
        # best sibling-disjoint ranked lane wins.
        siblings = [self._sib("apply")]
        lookup = self._lookup({"apply": _APPLY_TREE})
        rank = {"apply": 9.0, "tailor": 2.0, "discovery": 5.0}
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self.LADDER,
                 live_siblings=siblings, sibling_tree_lookup=lookup,
                 rank_key=lambda n, k, t: rank.get(n))
        assert d.outcome == "acquire"
        assert d.lane == "discovery"       # highest-ranked sibling-disjoint lane
        assert "value-aware rank" in d.reason


class TestRedirectReasonHonesty:
    """The auto-pick-redirect reason must not narrate a false "was busy".

    Found by running `dos arbitrate` against a foreign repo whose taxonomy is the
    generic default `{global, main}`: a request for a lane that simply does not
    exist there (`src`, a typo, anything off-taxonomy) was redirected to `main`
    with the reason "requested 'src' was busy" — false, since the lane was never a
    lane here and nothing was held. The kernel built to refuse self-narrated false
    accounts was emitting one in its own refusal surface (docs/104 §4). The reason
    must distinguish *held* (a real lane, busy) from *unknown* (not a lane here).
    """

    def test_held_known_lane_still_reports_was_busy(self):
        # `apply` IS a real cluster lane in this taxonomy and IS held → a *cluster*
        # request for it falls through to auto-pick (arbiter.py same-lane branch),
        # and the redirect away from it is a genuine busy diagnosis; that wording
        # must survive — the fix only rewrites the UNKNOWN-lane case.
        # Mechanism test: needs `apply` auto-pickable to reach the redirect →
        # synthetic clustered fixture (reference taxonomy is de-clustered).
        live = [_lease("apply", "cluster", _APPLY_TREE)]
        d = _arb_clustered(requested_lane="apply", requested_kind="cluster",
                           live_leases=live)
        assert d.outcome == "acquire" and d.auto_picked
        assert "was busy" in d.reason
        assert "is not a lane in this workspace" not in d.reason

    def test_unknown_lane_is_not_reported_as_busy(self):
        # The exact CLI path that exposed the bug: `dos arbitrate --lane X` with no
        # `--kind` → requested_kind="". `zzz_nonexistent` is NOT in the taxonomy, so
        # the auto-pick redirect must say the lane is unknown, NEVER "was busy".
        # Needs a non-empty `autopick` to land on → synthetic clustered fixture.
        d = _arb_clustered(requested_lane="zzz_nonexistent", requested_kind="")
        assert d.outcome == "acquire" and d.auto_picked
        assert "is not a lane in this workspace" in d.reason
        assert "was busy" not in d.reason

    def test_unknown_lane_honesty_holds_on_the_priority_ladder_path(self):
        # Same guarantee on the auto-pick redirect for an unknown NAMED lane. With
        # `requested_kind=""` and a non-empty (unknown) `requested_lane`, the request
        # is neither bare nor an unresolved keyword, so the arbiter walks the
        # taxonomy's `autopick` (the `else` branch) — needs the synthetic clustered
        # fixture (reference taxonomy is de-clustered). The redirect must still
        # narrate "not a lane in this workspace", never "was busy".
        ladder = [("apply", "cluster", _APPLY_TREE),
                  ("tailor", "cluster", _TAILOR_TREE)]
        d = _arb_clustered(requested_lane="zzz_nonexistent", requested_kind="",
                           auto_pick_order=ladder)
        assert d.outcome == "acquire" and d.auto_picked
        assert "is not a lane in this workspace" in d.reason


class TestKindlessSoftHintTakesTheNamedLane:
    """A kindless request naming a KNOWN, FREE cluster lane must take THAT lane.

    The CLI shape `dos arbitrate --lane docs` (no `--kind`) used to skip every
    direct-grant branch (each is gated on `requested_kind`), fall into the
    auto-pick walk — which never tries the hinted name — and acquire a DIFFERENT
    free lane with the reason "requested 'docs' was busy", while nothing held
    `docs` at all: the TestRedirectReasonHonesty disease, KNOWN-free-name
    edition (found dogfooding on the kernel repo itself, where `dos top` showed
    every lane FREE while the kindless arbitrate narrated busy). The exclusive
    branch already honored a kindless exclusive NAME; this pins the cluster
    analogue, plus the two arms that must NOT change: a held name still refuses
    same-lane, and a hint refused on its merits falls through to the walk with
    the conjunction's REAL reason in the redirect parenthetical — never a false
    "was busy".
    """

    def test_kindless_free_known_cluster_lane_is_granted_directly(self):
        d = _arb_clustered(requested_lane="tailor", requested_kind="")
        assert d.outcome == "acquire"
        assert d.lane == "tailor"
        assert not d.auto_picked
        assert "was busy" not in d.reason

    def test_kindless_held_known_lane_still_refuses_same_lane(self):
        live = [_lease("tailor", "cluster", _TAILOR_TREE)]
        d = _arb_clustered(requested_lane="tailor", requested_kind="",
                           live_leases=live)
        assert d.outcome == "refuse"
        assert "already held" in d.reason

    def test_kindless_hint_refused_on_merits_redirects_with_the_real_reason(self):
        # The hinted lane's NAME is free but its TREE is held under another
        # lease → the conjunction refuses the hint. The soft hint must not
        # hard-refuse (the caller never committed to a kind) NOR narrate a
        # false "was busy" — it falls through to the walk and the redirect
        # parenthetical carries the conjunction's own refusal.
        live = [_lease("regionhog", "keyword", _TAILOR_TREE)]
        d = _arb_clustered(requested_lane="tailor", requested_kind="",
                           live_leases=live)
        assert d.outcome == "acquire"
        assert d.auto_picked
        assert d.lane != "tailor"
        assert "was refused:" in d.reason
        assert "was busy" not in d.reason


class TestBusyNamedLaneNoAutopickReportsFreeConcurrentLanes:
    """A busy named cluster lane with no autopick candidate must not narrate a
    false "all lanes held" (issue #118).

    When a NAMED `--kind cluster` lane is held and the autopick ladder offers no
    free candidate, the legacy terminal refuse used to hard-code
    `free_clusters=[]` and the reason "all concurrent cluster lanes are held by
    live loops" — both false when a concurrent cluster lane is free but simply
    OFF the autopick ladder. (The KINDLESS same-lane path already got this right,
    filling `free_clusters` from the live-disjoint set.) This is the docs/104 §4
    false-"busy" narration class the `_redirect_why` fix closed for redirects,
    reaching the no-autopick-candidate branch. Fire-shrinking only: the outcome
    stays `refuse`; only the reason and `free_clusters` change.

    The fixture mirrors the issue's taxonomy: three CONCURRENT cluster lanes, an
    EMPTY autopick ladder (so the bare walk finds nothing), and one live lease on
    the requested lane. The two siblings are free — and must be reported.
    """

    # concurrent ⊋ autopick: the whole point. `qemu-rig`/`driver`/`tests` are
    # leaseable cluster lanes; none is on the (empty) autopick ladder, so the
    # legacy bare-walk fallback has no candidate to try.
    CFG_NO_LADDER = dataclasses.replace(
        CFG,
        lanes=dataclasses.replace(
            CFG.lanes,
            concurrent=("qemu-rig", "driver", "tests"),
            autopick=(),
            trees={
                **CFG.lanes.trees,
                "qemu-rig": ("rig/**",),
                "driver": ("driver/**",),
                "tests": ("tests/**",),
            },
        ),
    )

    def _arb_no_ladder(self, **kw):
        kw.setdefault("config", self.CFG_NO_LADDER)
        return _arb(**kw)

    def test_free_concurrent_lanes_are_listed_not_empty(self):
        # The issue's exact reproduction: request the held lane with `--kind
        # cluster`; the autopick walk has nothing, so it falls to the terminal
        # refuse. `free_clusters` must list the actually-free siblings, not [].
        live = [_lease("qemu-rig", "cluster", ["rig/**"])]
        d = self._arb_no_ladder(requested_lane="qemu-rig", requested_kind="cluster",
                                live_leases=live)
        assert d.outcome == "refuse"
        assert set(d.free_clusters) == {"driver", "tests"}

    def test_reason_does_not_claim_all_lanes_held_when_some_are_free(self):
        live = [_lease("qemu-rig", "cluster", ["rig/**"])]
        d = self._arb_no_ladder(requested_lane="qemu-rig", requested_kind="cluster",
                                live_leases=live)
        assert d.outcome == "refuse"
        # The false narration is gone; the true cause + the free siblings are named.
        assert "all concurrent cluster lanes are held" not in d.reason
        assert "driver" in d.reason and "tests" in d.reason

    def test_genuinely_all_held_still_says_so(self):
        # The boundary the fix must NOT cross: when EVERY concurrent cluster lane
        # really is held, the all-held wording is true and must survive, with an
        # empty `free_clusters`.
        live = [_lease("qemu-rig", "cluster", ["rig/**"]),
                _lease("driver", "cluster", ["driver/**"]),
                _lease("tests", "cluster", ["tests/**"])]
        d = self._arb_no_ladder(requested_lane="qemu-rig", requested_kind="cluster",
                                live_leases=live)
        assert d.outcome == "refuse"
        assert d.free_clusters == []
        assert "all concurrent cluster lanes are held" in d.reason

    def test_outcome_is_unchanged_fire_shrinking_only(self):
        # The fix touches narration + free_clusters only — never the admit/refuse
        # verdict. Both the some-free and all-held worlds still REFUSE.
        some_free = self._arb_no_ladder(
            requested_lane="qemu-rig", requested_kind="cluster",
            live_leases=[_lease("qemu-rig", "cluster", ["rig/**"])])
        all_held = self._arb_no_ladder(
            requested_lane="qemu-rig", requested_kind="cluster",
            live_leases=[_lease("qemu-rig", "cluster", ["rig/**"]),
                         _lease("driver", "cluster", ["driver/**"]),
                         _lease("tests", "cluster", ["tests/**"])])
        assert some_free.outcome == "refuse" and all_held.outcome == "refuse"


class TestUnknownKeywordRefusesNotDegrades:
    """An EXPLICIT `--kind keyword` naming a lane the taxonomy never heard of must
    REFUSE (UNKNOWN_LANE), not silently auto-pick a different free lane.

    Found in the field: a fleet asked for `--scope playbooks --kind keyword`; the
    `playbooks` tree is not a registered lane, so the keyword resolved to an EMPTY
    tree, was folded into the bare auto-pick walk (arbiter.py `bare = ... or
    unresolved_keyword`), and ACQUIRED a random non-apply cluster (CID). The lease
    then described CID's tree while the agent intended to touch `playbooks/**` — so
    the disjointness check guarded the WRONG region (a soundness hole), and the only
    trace of the original intent was a parenthetical in the reason. Auto-pick's
    license is "the caller expressed NO preference"; an explicit keyword NAME is a
    preference the kernel cannot place → refuse-don't-guess (docs/104 §4, the
    control-flow twin of the `_redirect_why` reason-string fix).

    The split is by how hard the caller COMMITTED to the name:
      * `--kind keyword` + unknown name → ASSERTION the kernel can't honor → REFUSE.
      * empty `--kind` + unknown name   → soft HINT → redirect-with-honest-reason
        (TestRedirectReasonHonesty above; still acquires). Both are honest; they
        differ because the caller's commitment differs.
    """

    def test_unknown_keyword_refuses_with_known_lane_list(self):
        # `playbooks` is not in the synthetic clustered taxonomy (apply/tailor/
        # discovery). An explicit keyword request with no resolvable tree must
        # refuse — naming the lanes the workspace DOES know, never auto-picking one.
        d = _arb_clustered(requested_lane="playbooks", requested_kind="keyword",
                           requested_tree=[])
        assert d.outcome == "refuse"
        assert "UNKNOWN_LANE" in d.reason
        assert "is not a lane in this workspace" in d.reason
        # The refuse is actionable: it lists the real lanes so the operator can
        # re-aim, instead of leaving them to discover the silent CID landing.
        assert "apply" in d.reason and "tailor" in d.reason

    def test_unknown_keyword_does_not_acquire_a_substitute_lane(self):
        # The soundness point: even with free lanes available to redirect onto, the
        # kernel must NOT hand back a lane the caller did not name. No `acquire`,
        # no `auto_picked`, no foreign tree on the decision.
        d = _arb_clustered(requested_lane="playbooks", requested_kind="keyword",
                           requested_tree=[])
        assert d.outcome != "acquire"
        assert not d.auto_picked
        assert d.lane in (None, "", "playbooks")  # never a substituted lane name

    def test_known_keyword_with_no_live_plan_still_degrades(self):
        # The OTHER arm must be preserved: `apply` IS a known lane. An explicit
        # keyword request for it with an empty tree (its live plan isn't running)
        # is a legitimate "I wanted that, fall through to auto-pick" — it must still
        # degrade to the bare walk and acquire, NOT refuse as UNKNOWN_LANE. (This is
        # the behavior the field path of `_unresolved_suffix` depends on.)
        d = _arb_clustered(requested_lane="apply", requested_kind="keyword",
                           requested_tree=[])
        assert d.outcome == "acquire"
        assert "UNKNOWN_LANE" not in d.reason
        assert "matched no live plan" in d.reason  # the honest degrade marker

    def test_bare_unknown_name_empty_kind_still_redirects(self):
        # Guard the boundary the fix must NOT cross: empty `--kind` keeps the old
        # soft-hint redirect (TestRedirectReasonHonesty). Only the keyword ASSERTION
        # arm refuses.
        d = _arb_clustered(requested_lane="zzz_nonexistent", requested_kind="")
        assert d.outcome == "acquire" and d.auto_picked
        assert "UNKNOWN_LANE" not in d.reason


class TestConcurrencyClassBudget:
    """The docs/97 Phase 1 `class_budgets` gate — the `priority` concurrency
    budget lifted from the job-side `auto_pick_order` pre-filter into the kernel.

    Three distinct priority plan-lanes with pairwise-disjoint trees stand in for
    the `by_slot_then_status` rung the host feeds in: apply / tailor / discovery
    trees are mutually disjoint, so the only thing that can stop a 2nd/3rd grab is
    the budget, not a tree collision — which is exactly what lets these tests
    isolate the budget logic.
    """

    PLAN_A = ("PA", "priority", _APPLY_TREE)
    PLAN_B = ("PB", "priority", _TAILOR_TREE)
    PLAN_C = ("PC", "priority", _DISCOVERY_TREE)

    def _ladder(self):
        return [self.PLAN_A, self.PLAN_B, self.PLAN_C]

    def _prio_lease(self, lane, tree):
        return {"lane": lane, "lane_kind": "priority", "tree": tree,
                "loop_ts": "20260531T1200Z"}

    # --- back-compat: no budgets ⇒ byte-identical to the pre-budget walk -----

    def test_no_budgets_is_byte_identical_first_fit(self):
        # `class_budgets=None` (the default) must not change a single decision —
        # the regression guard. A bare walk still grabs the first disjoint lane.
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self._ladder())
        assert d.outcome == "acquire" and d.lane == "PA" and d.lane_kind == "priority"
        # Explicit None is identical to omitting it.
        d2 = _arb(requested_lane="", requested_kind="",
                  auto_pick_order=self._ladder(), class_budgets=None)
        assert d2.outcome == "acquire" and d2.lane == "PA"

    # --- the budget invariant: the (N+1)-th grab refuses ---------------------

    def test_first_priority_grab_admits_under_budget(self):
        # 0 live priority leases, budget 3 → the first grab is admitted.
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self._ladder(),
                 class_budgets={"priority": 3})
        assert d.outcome == "acquire" and d.lane == "PA"

    def test_grab_at_budget_refuses_class_budget_exhausted(self):
        # budget=1, one priority lease already live → the next bare grab must
        # refuse, and name the budget as the cause (NOT drain / ladder-exhausted).
        live = [self._prio_lease("PA", _APPLY_TREE)]
        d = _arb(requested_lane="", requested_kind="",
                 auto_pick_order=[self.PLAN_B, self.PLAN_C], live_leases=live,
                 class_budgets={"priority": 1})
        assert d.outcome == "refuse"
        assert "CLASS_BUDGET_EXHAUSTED" in d.reason
        assert "priority (1/1)" in d.reason
        # The lever is "wait", explicitly NOT /replan (the work exists).
        assert "do NOT /replan" in d.reason

    def test_nth_plus_one_refuses_two_live_budget_two(self):
        # budget=2, two live → the third refuses.
        live = [self._prio_lease("PA", _APPLY_TREE),
                self._prio_lease("PB", _TAILOR_TREE)]
        d = _arb(requested_lane="", requested_kind="",
                 auto_pick_order=[self.PLAN_C], live_leases=live,
                 class_budgets={"priority": 2})
        assert d.outcome == "refuse" and "CLASS_BUDGET_EXHAUSTED" in d.reason

    def test_under_budget_with_one_live_still_admits_a_disjoint_second(self):
        # budget=3, one live → a 2nd disjoint priority lane is still admitted
        # (genuine N-way concurrency, the property the budget must PRESERVE).
        live = [self._prio_lease("PA", _APPLY_TREE)]
        d = _arb(requested_lane="", requested_kind="",
                 auto_pick_order=[self.PLAN_B, self.PLAN_C], live_leases=live,
                 class_budgets={"priority": 3})
        assert d.outcome == "acquire" and d.lane == "PB"

    # --- the no-collision invariant: the N admitted bind disjoint regions ----

    def test_two_concurrent_priority_grabs_are_disjoint(self):
        # Simulate two sequential bare grabs under budget=3 and assert the regions
        # they bind do not overlap (the exact-glob floor + disjointness gate make
        # a PA-over-PB collision impossible; this asserts it at the budget level).
        from dos._tree import lane_trees_disjoint
        first = _arb(requested_lane="", requested_kind="",
                     auto_pick_order=self._ladder(), class_budgets={"priority": 3})
        assert first.outcome == "acquire"
        live = [self._prio_lease(first.lane, first.tree)]
        second = _arb(requested_lane="", requested_kind="",
                      auto_pick_order=self._ladder(), live_leases=live,
                      class_budgets={"priority": 3})
        assert second.outcome == "acquire"
        assert second.lane != first.lane
        assert lane_trees_disjoint(first.tree, second.tree)

    # --- the budget is bare-only: a direct/forced request is never gated ------

    def test_direct_scope_request_bypasses_budget(self):
        # An explicit --scope (keyword request with a tree) is NOT a bare auto-pick,
        # so the budget never touches it — the budget gates the ladder, not the
        # operator's explicit choice. Even at budget, a directly-named disjoint lane
        # acquires.
        live = [self._prio_lease("PA", _APPLY_TREE)]
        d = _arb(requested_lane="PB", requested_kind="keyword", requested_tree=_TAILOR_TREE,
                 auto_pick_order=self._ladder(), live_leases=live,
                 class_budgets={"priority": 1})
        assert d.outcome == "acquire" and d.lane == "PB" and not d.auto_picked

    def test_force_bypasses_budget(self):
        # --force is the operator override; a budget is a (refuse-only) concurrency
        # gate, and force overrides refuses — so a forced lane is never budget-gated.
        live = [self._prio_lease("PA", _APPLY_TREE)]
        d = _arb(requested_lane="PB", requested_kind="keyword", requested_tree=_TAILOR_TREE,
                 live_leases=live, force=True, class_budgets={"priority": 1})
        assert d.outcome == "acquire" and d.lane == "PB"

    # --- only the budgeted KIND is gated; other kinds pass freely ------------

    def test_unbudgeted_kind_is_never_gated(self):
        # budget names only "priority"; a "cluster"-kind candidate has no budget, so
        # it admits even when priority is full. Mixed ladder: priority full (1/1),
        # cluster lane free → the cluster wins (priority skipped on budget).
        live = [self._prio_lease("PA", _APPLY_TREE)]
        ladder = [self.PLAN_B, ("disco", "cluster", _DISCOVERY_TREE)]
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=ladder,
                 live_leases=live, class_budgets={"priority": 1})
        assert d.outcome == "acquire" and d.lane == "disco" and d.lane_kind == "cluster"

    def test_budget_skip_then_disjoint_other_kind_is_not_a_budget_refuse(self):
        # When a priority candidate is budget-skipped BUT a different-kind candidate
        # is admitted, that is an acquire, not a CLASS_BUDGET_EXHAUSTED refuse — the
        # budget-refuse only fires when budgets were the SOLE blocker.
        live = [self._prio_lease("PA", _APPLY_TREE)]
        ladder = [self.PLAN_B, ("disco", "cluster", _DISCOVERY_TREE)]
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=ladder,
                 live_leases=live, class_budgets={"priority": 1})
        assert d.outcome == "acquire"
        assert "CLASS_BUDGET_EXHAUSTED" not in d.reason

    # --- normalization: a non-positive / garbled budget is ignored (safe) -----

    def test_zero_budget_is_ignored_not_a_lockout(self):
        # A budget of 0 would silently wedge the whole class; the safe direction is
        # "no budget for that kind" (mirrors the host's bad-config fallback). So a
        # zero budget behaves as if no budget were set → first lane admits.
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self._ladder(),
                 class_budgets={"priority": 0})
        assert d.outcome == "acquire" and d.lane == "PA"

    def test_bool_budget_is_ignored(self):
        # `True` is an int subclass; a stray bool must not be read as a budget of 1.
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=self._ladder(),
                 class_budgets={"priority": True})
        assert d.outcome == "acquire" and d.lane == "PA"
        assert "was busy" not in d.reason


class TestOpenPoolRegionRouting:
    """docs/97 / docs/110 Phase 3 — a class binds a free DISJOINT region pulled from
    an OPEN pool (`TopPickablePlan`), the kernel-side "modular dynamic queue".

    The host hands a `TopPickablePlanBinding` (its pool of opaque plan ids + a
    `derive_tree` callback); `concurrency_class.project_to_ladder` expands it into
    the `auto_pick_order` rows the arbiter ALREADY walks, and the existing
    disjointness + budget admission does the rest — the arbiter is untouched. These
    drive the five docs/97 §Test-obligations + NO_FREE_REGION THROUGH the projection.

    The three disjoint trees (`_APPLY_TREE`/`_TAILOR_TREE`/`_DISCOVERY_TREE`) stand
    in for the host's plan pool; the deriver maps each plan id to its tree, so the
    only thing that can stop a 2nd/3rd grab is the budget or a real collision.
    """

    from dos import concurrency_class as _cc

    _TREE_OF = {"PA": _APPLY_TREE, "PB": _TAILOR_TREE, "PC": _DISCOVERY_TREE}

    def _binding(self, pool=("PA", "PB", "PC")):
        return self._cc.TopPickablePlanBinding(
            pool=pool, derive_tree=lambda pid: list(self._TREE_OF[pid]))

    def _prio_lease(self, lane):
        return {"lane": lane, "lane_kind": "priority", "tree": self._TREE_OF[lane],
                "loop_ts": "20260531T1200Z"}

    # --- the projection itself (pure) ----------------------------------------

    def test_project_to_ladder_expands_pool_in_order(self):
        rows = self._cc.project_to_ladder(self._binding(), "priority")
        assert [r[0] for r in rows] == ["PA", "PB", "PC"]
        assert all(kind == "priority" for _, kind, _ in rows)
        assert rows[0][2] == _APPLY_TREE

    def test_project_to_ladder_skips_held_and_bad_derivations(self):
        # A held id is skipped; a deriver that throws or yields an empty tree drops
        # that member without sinking the pool (the durability seam).
        def derive(pid):
            if pid == "PB":
                raise RuntimeError("host deriver blew up on PB")
            if pid == "PC":
                return []  # no tree → not offerable
            return list(self._TREE_OF[pid])
        binding = self._cc.TopPickablePlanBinding(pool=("PA", "PB", "PC"),
                                                  derive_tree=derive)
        rows = self._cc.project_to_ladder(binding, "priority", skip={"PA"})
        assert rows == []  # PA skipped, PB throws, PC empty → nothing offerable

    # --- obligation 1: no-collision (N admitted bind disjoint regions) --------

    def test_two_open_pool_grabs_bind_disjoint_regions(self):
        from dos._tree import lane_trees_disjoint
        ladder = self._cc.project_to_ladder(self._binding(), "priority")
        first = _arb(requested_lane="", requested_kind="",
                     auto_pick_order=ladder, class_budgets={"priority": 3})
        assert first.outcome == "acquire"
        live = [self._prio_lease(first.lane)]
        # Re-project skipping the now-leased id (a real host folds the acquire back).
        ladder2 = self._cc.project_to_ladder(self._binding(), "priority",
                                             skip={first.lane})
        second = _arb(requested_lane="", requested_kind="",
                      auto_pick_order=ladder2, live_leases=live,
                      class_budgets={"priority": 3})
        assert second.outcome == "acquire"
        assert second.lane != first.lane
        assert lane_trees_disjoint(first.tree, second.tree)

    # --- obligation 2: budget — the (N+1)-th refuses CLASS_BUDGET_EXHAUSTED ----

    def test_open_pool_budget_exhausted_refuses(self):
        live = [self._prio_lease("PA")]
        ladder = self._cc.project_to_ladder(self._binding(), "priority",
                                            skip={"PA"})
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=ladder,
                 live_leases=live, class_budgets={"priority": 1})
        assert d.outcome == "refuse"
        assert "CLASS_BUDGET_EXHAUSTED" in d.reason
        assert "priority (1/1)" in d.reason

    # --- obligation 3: reachability (LSR0) — every pool plan is reachable ------

    def test_every_pool_plan_is_reachable_as_leases_accumulate(self):
        # Re-asserted here against the open-pool projection. (The host's original
        # home for this invariant is `test_default_ladder.py`, which does NOT exist
        # in the kernel repo — it is a host test referenced by docs/97; the kernel
        # re-pins the reachability guarantee against the class-model rung.)
        acquired = []
        live = []
        for _ in range(3):
            ladder = self._cc.project_to_ladder(
                self._binding(), "priority", skip=set(acquired))
            d = _arb(requested_lane="", requested_kind="", auto_pick_order=ladder,
                     live_leases=live, class_budgets={"priority": 3})
            assert d.outcome == "acquire"
            acquired.append(d.lane)
            live.append(self._prio_lease(d.lane))
        assert set(acquired) == {"PA", "PB", "PC"}  # every plan in the pool reached

    # --- obligation 4: exclusive — a live global short-circuits the pool -------

    def test_live_global_lease_blocks_the_open_pool(self):
        # An exclusive (global) lease must refuse every other request BEFORE the
        # open-pool walk — the pool never bypasses the exclusive gate.
        live = [_lease("global", "global", ["**/*"])]
        ladder = self._cc.project_to_ladder(self._binding(), "priority")
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=ladder,
                 live_leases=live, class_budgets={"priority": 3})
        assert d.outcome == "refuse"

    # --- obligation 5: back-compat — an empty projection is byte-identical -----

    def test_empty_pool_is_a_clean_ladder_exhausted_refuse(self):
        # An empty pool → empty `auto_pick_order` rows. With no other ladder, the
        # arbiter refuses exactly as today (no open-pool special-casing leaks in).
        ladder = self._cc.project_to_ladder(self._binding(pool=()), "priority")
        assert ladder == []

    # --- obligation 6: NO_FREE_REGION — pool non-empty, every region overlaps --

    def test_no_free_region_when_every_pool_tree_overlaps(self):
        # One live lease whose tree overlaps EVERY pool member (a broad `agents/**`
        # hold) → the projection yields rows, but all collide → the arbiter refuses,
        # and the caller-side classifier re-labels it NO_FREE_REGION (distinct from
        # the budget refuse — the budget here has room).
        live = [_lease("broad", "cluster", ["agents/**", "job_search/**",
                                            "templates/"])]
        ladder = self._cc.project_to_ladder(self._binding(), "priority")
        d = _arb(requested_lane="", requested_kind="", auto_pick_order=ladder,
                 live_leases=live, class_budgets={"priority": 3})
        assert d.outcome == "refuse"
        assert "CLASS_BUDGET_EXHAUSTED" not in d.reason  # budget had room
        # The caller classifies this generic ladder-exhausted refuse as the more
        # specific open-pool cause.
        assert self._cc.is_no_free_region_refuse(
            d, projected_rows=len(ladder), budget_exhausted=False)

    def test_is_no_free_region_refuse_false_on_acquire_and_budget(self):
        # The classifier must NOT fire on an acquire, nor on a budget-caused refuse
        # (that owns its own CLASS_BUDGET_EXHAUSTED token).
        ladder = self._cc.project_to_ladder(self._binding(), "priority")
        ok = _arb(requested_lane="", requested_kind="", auto_pick_order=ladder,
                  class_budgets={"priority": 3})
        assert ok.outcome == "acquire"
        assert not self._cc.is_no_free_region_refuse(
            ok, projected_rows=len(ladder), budget_exhausted=False)
        # A budget-exhausted refuse is not a NO_FREE_REGION even with rows present.
        live = [self._prio_lease("PA")]
        ladder2 = self._cc.project_to_ladder(self._binding(), "priority", skip={"PA"})
        budgeted = _arb(requested_lane="", requested_kind="", auto_pick_order=ladder2,
                        live_leases=live, class_budgets={"priority": 1})
        assert budgeted.outcome == "refuse"
        assert not self._cc.is_no_free_region_refuse(
            budgeted, projected_rows=len(ladder2), budget_exhausted=True)
