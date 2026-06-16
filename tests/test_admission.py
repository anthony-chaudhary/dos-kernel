"""Tests for the admission-predicate seam (`dos.admission` + `dos.self_modify`) — ADM, docs/73.

The pinned litmus from the plan, phase by phase:

  Phase 1 (AdmissionVerdict + the conjunctive runner):
    * the whole existing arbiter/overlap suite stays green routed through
      `run_predicates` (proven by `tests/test_arbiter.py` — not re-asserted here);
    * a predicate that RAISES yields a REFUSE (fail-closed), not a crash;
    * two refusing predicates surface the FIRST (the conjunction short-circuits).

  Phase 2 (the SELF_MODIFY built-in):
    * a lease tree including `src/dos/arbiter.py` is REFUSED with the SELF_MODIFY
      reason;
    * the same lease with `--force` ACQUIRES;
    * `dos man wedge SELF_MODIFY` renders (the reason is in the registry);
    * a non-runtime tree (`src/api/**`) is unaffected.

  Phase 3 (discovery + the conjunctive-only safety guarantee):
    * a discovered predicate's refusal blocks admission;
    * a discovered predicate returning admit() CANNOT override a built-in
      disjointness refusal (the load-bearing safety test of the whole plan);
    * `dos doctor` names the active predicates.

The conjunctive runner is tested directly (pure, hermetic) AND through
`arbiter.arbitrate(predicates=...)` (the integration the arbiter actually uses),
so a regression in either the runner or the wiring is caught.
"""

from __future__ import annotations

import dataclasses

import pytest

from dos import arbiter
from dos.config import job_config
from dos.admission import (
    AdmissionRequest,
    AdmissionVerdict,
    DisjointnessPredicate,
    active_predicate_names,
    active_predicates,
    built_in_predicates,
    run_predicates,
)
from dos.self_modify import SelfModifyPredicate, _DISPATCH_RUNTIME_FILES
from dos.config import WorkspaceFacts

# A foreign-repo config: `/work/userland-app` is NOT the DOS kernel's own tree, so
# its gathered `workspace` facts carry an EMPTY kernel-runtime-file set. The
# arbiter's default trusts those facts, so a kernel-NAMED lane against this config
# ADMITS (editing a file literally called `src/dos/arbiter.py` *in the reference
# userland app* does not rewrite the live, pip-installed kernel). The right baseline
# for "is the wiring present" tests that do NOT mean to trip self-modify.
CFG = job_config("/work/userland-app")

# The kernel-serving-itself config: facts forced to the FULL runtime-file set, the
# one situation SELF_MODIFY must fire in — a live loop whose lease would rewrite the
# very kernel adjudicating it. Built without touching the real FS (we inject the
# facts directly) so the test is hermetic and doesn't depend on where DOS lives.
from pathlib import Path as _Path
KERNEL_CFG = dataclasses.replace(
    CFG,
    workspace=WorkspaceFacts(
        root=_Path("/work/dos-kernel"),
        kernel_runtime_files=_DISPATCH_RUNTIME_FILES,
        is_kernel_repo=True,
    ),
)

_APPLY_TREE = ["agents/apply_*.py", "agents/_phase_*.py"]


def _lease(lane, kind, tree):
    return {"lane": lane, "lane_kind": kind, "tree": tree, "loop_ts": "20260601T0000Z"}


def _req(lane="x", kind="keyword", tree=()):
    return AdmissionRequest(lane=lane, kind=kind, tree=tuple(tree))


# ── shared fakes ────────────────────────────────────────────────────────────
class _AlwaysAdmit:
    name = "always-admit"
    def __call__(self, request, live_lease, config):
        return AdmissionVerdict.admit()


class _AlwaysRefuse:
    def __init__(self, name, reason):
        self.name = name
        self._reason = reason
    def __call__(self, request, live_lease, config):
        return AdmissionVerdict.refuse(self._reason)


class _Raises:
    name = "boom"
    def __call__(self, request, live_lease, config):
        raise RuntimeError("predicate exploded")


# ============================================================================
# Phase 1 — AdmissionVerdict + the conjunctive runner
# ============================================================================
class TestAdmissionVerdict:
    def test_admit_has_no_force_constructor(self):
        # The conjunctive-only invariant is enforced by the TYPE: there is no
        # force-admit. Only `.admit()` and `.refuse()` exist.
        assert AdmissionVerdict.admit().admitted is True
        v = AdmissionVerdict.refuse("nope")
        assert v.admitted is False
        assert v.reason == "nope"
        assert not hasattr(AdmissionVerdict, "force_admit")

    def test_refuse_carries_optional_reason_class(self):
        v = AdmissionVerdict.refuse("x", reason_class="SELF_MODIFY")
        assert v.reason_class == "SELF_MODIFY"
        assert AdmissionVerdict.admit().reason_class == ""

    def test_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            AdmissionVerdict.admit().reason = "mutate"  # type: ignore[misc]


class TestConjunctiveRunner:
    def test_no_leases_still_evaluates_request_absolute_predicates(self):
        # CORRECTED semantics (adversarial-review fix): with zero live leases the
        # conjunction runs ONCE against a synthetic empty lease, so a
        # REQUEST-ABSOLUTE predicate (one that refuses from the request alone, like
        # SELF_MODIFY) STILL fires. This closes the idle-repo gap — SELF_MODIFY is
        # never silently bypassed just because nothing else is live.
        v = run_predicates([_AlwaysRefuse("r", "would refuse")], _req(), [], CFG)
        assert v.admitted is False
        assert v.reason == "would refuse"

    def test_no_leases_lease_relative_predicate_admits(self):
        # A LEASE-RELATIVE predicate (disjointness) sees the empty-lease sentinel,
        # hits its "empty lease tree ⇒ admit" branch, and contributes nothing — so
        # a free lane with no live leases still admits, exactly as before the fix.
        v = run_predicates([DisjointnessPredicate()], _req(tree=["a/**"]), [], CFG)
        assert v.admitted is True

    def test_all_admit_admits(self):
        v = run_predicates([_AlwaysAdmit(), _AlwaysAdmit()], _req(),
                           [_lease("a", "cluster", ["x/**"])], CFG)
        assert v.admitted is True

    def test_raising_predicate_fails_closed(self):
        # Phase-1 litmus: a predicate that RAISES yields a REFUSE, not an admit
        # and not a crash.
        v = run_predicates([_Raises()], _req(),
                           [_lease("a", "cluster", ["x/**"])], CFG)
        assert v.admitted is False
        assert "boom" in v.reason and "fail-closed" in v.reason

    def test_first_refusal_wins(self):
        # Phase-1 litmus: two refusing predicates surface the FIRST; the
        # conjunction short-circuits (predicate-inner, so the first predicate in
        # the list against the first lease wins).
        first = _AlwaysRefuse("first", "FIRST refuse")
        second = _AlwaysRefuse("second", "SECOND refuse")
        v = run_predicates([first, second], _req(),
                           [_lease("a", "cluster", ["x/**"])], CFG)
        assert v.admitted is False
        assert v.reason == "FIRST refuse"

    def test_raise_short_circuits_before_a_later_admit(self):
        # A raising predicate refuses immediately even if a later predicate would
        # admit — fail-closed is not overridden by a subsequent admit.
        v = run_predicates([_Raises(), _AlwaysAdmit()], _req(),
                           [_lease("a", "cluster", ["x/**"])], CFG)
        assert v.admitted is False

    def test_predicate_returning_none_fails_closed(self):
        # A buggy predicate that returns a NON-AdmissionVerdict (None) must
        # fail-closed-REFUSE, not crash arbitration with an AttributeError on
        # `.admitted`. (Adversarial-review regression: the try/except wrapped only
        # the call, not the return-type handling.)
        class _ReturnsNone:
            name = "garbage"
            def __call__(self, r, l, c):
                return None
        v = run_predicates([_ReturnsNone()], _req(),
                           [_lease("a", "cluster", ["x/**"])], CFG)
        assert v.admitted is False
        assert "not an AdmissionVerdict" in v.reason

    def test_fake_admit_object_cannot_leak_an_admit(self):
        # A duck-typed look-alike with `.admitted = True` must NOT be trusted —
        # we type-check, so a foreign object can never sneak an admit through.
        class _Fake:
            admitted = True
            reason = ""
            reason_class = ""
        class _ReturnsFake:
            name = "fake"
            def __call__(self, r, l, c):
                return _Fake()
        v = run_predicates([_ReturnsFake()], _req(),
                           [_lease("a", "cluster", ["x/**"])], CFG)
        assert v.admitted is False  # NOT admitted, despite the fake's .admitted

    def test_fake_admit_cannot_override_a_builtin_refusal(self):
        # The strongest form: a fake-admit object placed after a real disjointness
        # refusal still refuses (the conjunction never reaches a state where a
        # non-verdict could loosen admission).
        from dos.admission import DisjointnessPredicate
        class _Fake:
            admitted = True
        class _ReturnsFake:
            name = "fake"
            def __call__(self, r, l, c):
                return _Fake()
        overlap_req = _req(tree=_APPLY_TREE)
        v = run_predicates([DisjointnessPredicate(), _ReturnsFake()], overlap_req,
                           [_lease("apply", "cluster", _APPLY_TREE)], CFG)
        assert v.admitted is False


class TestDisjointnessPredicate:
    """The built-in disjointness predicate reproduces `_lease_blocks` exactly."""
    P = DisjointnessPredicate()

    def test_empty_lease_tree_admits(self):
        assert self.P(_req(tree=["a/**"]), _lease("l", "k", []), CFG).admitted

    def test_empty_request_tree_vs_known_lease_refuses(self):
        assert not self.P(_req(tree=[]), _lease("l", "k", ["a/**"]), CFG).admitted

    def test_disjoint_trees_admit(self):
        v = self.P(_req(tree=["agents/tailor_*.py"]),
                   _lease("apply", "cluster", _APPLY_TREE), CFG)
        assert v.admitted is True

    def test_overlapping_trees_refuse(self):
        v = self.P(_req(tree=_APPLY_TREE),
                   _lease("apply", "cluster", _APPLY_TREE), CFG)
        assert v.admitted is False
        assert "cannot share" in v.reason


# ============================================================================
# Phase 2 — the SELF_MODIFY built-in predicate
# ============================================================================
class TestSelfModifyPredicate:
    P = SelfModifyPredicate()
    # A disjoint live lease so the arbiter's collision sweep runs at all.
    LIVE = [_lease("apply", "cluster", _APPLY_TREE)]

    def test_predicate_refuses_kernel_file(self):
        v = self.P(_req(tree=["src/dos/arbiter.py"]), self.LIVE[0], CFG)
        assert v.admitted is False
        assert v.reason_class == "SELF_MODIFY"

    def test_predicate_refuses_kernel_dir_glob(self):
        # A directory glob that CONTAINS a runtime file is caught (prefix collision
        # in both directions, the same algebra `_tree.lane_trees_disjoint` uses).
        v = self.P(_req(tree=["src/dos/"]), self.LIVE[0], CFG)
        assert v.admitted is False
        assert v.reason_class == "SELF_MODIFY"

    def test_predicate_admits_non_runtime_tree(self):
        v = self.P(_req(tree=["src/api/handlers.py"]), self.LIVE[0], CFG)
        assert v.admitted is True

    def test_predicate_admits_non_runtime_kernel_sibling(self):
        # `timeline.py` is kernel code but NOT in a live loop's decision path —
        # deliberately out of the T1 runtime set, so it admits.
        v = self.P(_req(tree=["src/dos/timeline.py"]), self.LIVE[0], CFG)
        assert v.admitted is True

    def test_self_modify_refuses_kernel_tree_via_arbiter(self):
        # Phase-2 litmus: a lease tree including src/dos/arbiter.py is REFUSED
        # through the real arbiter, with the SELF_MODIFY reason in the message.
        # Uses KERNEL_CFG (the kernel serving itself) — that is the situation the
        # guard exists for; against a foreign repo the same tree admits (see
        # `test_foreign_repo_kernel_named_lane_admits`).
        d = arbiter.arbitrate(
            requested_lane="kernel", requested_kind="keyword",
            requested_tree=["src/dos/arbiter.py"], live_leases=self.LIVE, config=KERNEL_CFG,
        )
        assert d.outcome == "refuse"
        assert "SELF_MODIFY" in d.reason

    def test_self_modify_force_override(self):
        # Phase-2 litmus: the same lease with --force ACQUIRES, even against
        # KERNEL_CFG (where the guard would otherwise fire) — --force is the sole
        # documented override of a SELF_MODIFY refusal.
        d = arbiter.arbitrate(
            requested_lane="kernel", requested_kind="keyword",
            requested_tree=["src/dos/arbiter.py"], live_leases=self.LIVE,
            config=KERNEL_CFG, force=True,
        )
        assert d.outcome == "acquire"
        assert d.lane == "kernel"

    def test_non_runtime_tree_unaffected_via_arbiter(self):
        # Phase-2 litmus: a lease on a non-runtime tree is unaffected (still
        # admitted by the keyword soft-overlap path).
        d = arbiter.arbitrate(
            requested_lane="svc", requested_kind="keyword",
            requested_tree=["src/api/handlers.py"], live_leases=self.LIVE, config=CFG,
        )
        assert d.outcome == "acquire"

    def test_runtime_set_names_the_arbiter(self):
        assert "src/dos/arbiter.py" in _DISPATCH_RUNTIME_FILES

    # ── everything-tree (`**/*`) regression ─────────────────────────────────
    # The `**/*` lane normalizes to the empty (universal) prefix. The OLD code
    # filtered the empty prefix out, so the broadest possible blast radius read
    # as "touches nothing" and evaded the guard. The default (workspace-blind)
    # predicate must now treat a whole-repo lease as touching every runtime file.
    def test_whole_repo_glob_refused_by_default_guard(self):
        v = self.P(_req(tree=["**/*"]), self.LIVE[0], CFG)
        assert v.admitted is False, "a '**/*' lease must trip the default SELF_MODIFY guard"
        assert v.reason_class == "SELF_MODIFY"

    def test_whole_repo_glob_hits_every_runtime_file(self):
        from dos.self_modify import _tree_touches_runtime
        hits = _tree_touches_runtime(["**/*"])
        # The universal prefix collides with every runtime path, in order.
        assert hits == list(_DISPATCH_RUNTIME_FILES)

    def test_blank_entries_still_touch_nothing(self):
        # A LITERALLY-empty entry carries no path information and must NOT be
        # promoted to the universal prefix (that would refuse everything).
        from dos.self_modify import _tree_touches_runtime
        assert _tree_touches_runtime([]) == []
        assert _tree_touches_runtime([""]) == []

    # ── case-bypass regression (the case-insensitive-FS hole) ───────────────
    # On Windows (DOS's primary platform) `SRC/dos/arbiter.py` IS the kernel's own
    # running arbiter, but the case-SENSITIVE prefix compare judged it disjoint from
    # the lowercase runtime path, so a mixed-case lane edited the kernel mid-flight
    # and slipped past the guard. `_tree.norm_tree_prefix` now case-folds, closing
    # the bypass at the shared chokepoint (so lane_overlap's false-ADMIT closes too).
    @pytest.mark.parametrize("variant", [
        "SRC/dos/arbiter.py", "Src/Dos/Arbiter.py", "src/DOS/ARBITER.PY",
    ])
    def test_self_modify_guard_is_case_insensitive(self, variant):
        from dos.self_modify import _tree_touches_runtime
        assert _tree_touches_runtime([variant]) == ["src/dos/arbiter.py"], (
            f"mixed-case kernel path {variant!r} must trip the SELF_MODIFY guard"
        )
        v = self.P(_req(tree=[variant]), self.LIVE[0], CFG)
        assert v.admitted is False
        assert v.reason_class == "SELF_MODIFY"

    # ── workspace-aware scoping (the boundary contract) ─────────────────────
    # The guard protects the kernel's OWN source. A boundary caller scopes it to
    # the runtime files that EXIST under the served workspace, so a `**/*` lane
    # in a FOREIGN repo (which has no src/dos/*.py) must admit, while the same
    # lane against the DOS repo itself refuses.
    def test_existing_runtime_files_empty_for_foreign_workspace(self, tmp_path):
        from dos.self_modify import existing_runtime_files
        # tmp_path is a bare dir — none of src/dos/*.py exist under it.
        assert existing_runtime_files(tmp_path) == ()

    def test_workspace_scoped_guard_admits_whole_repo_on_foreign_repo(self, tmp_path):
        from dos.self_modify import existing_runtime_files
        scoped = SelfModifyPredicate(runtime_files=existing_runtime_files(tmp_path))
        v = scoped(_req(tree=["**/*"]), self.LIVE[0], CFG)
        assert v.admitted is True, "a '**/*' lane in a foreign repo edits no kernel file"

    def test_existing_runtime_files_finds_real_kernel_sources(self, tmp_path):
        from dos.self_modify import existing_runtime_files, _DISPATCH_RUNTIME_FILES
        # Materialize two of the runtime files under a fake workspace; only those
        # two should be reported as present (the existence I/O is real).
        for rel in ("src/dos/arbiter.py", "src/dos/admission.py"):
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# stub\n", encoding="utf-8")
        present = existing_runtime_files(tmp_path)
        assert set(present) == {"src/dos/arbiter.py", "src/dos/admission.py"}
        assert all(f in _DISPATCH_RUNTIME_FILES for f in present)

    def test_none_workspace_stays_conservative(self):
        # A falsy workspace cannot prove non-existence → keep the full static set
        # (the safe direction for a safety guard).
        from dos.self_modify import existing_runtime_files
        assert existing_runtime_files(None) == _DISPATCH_RUNTIME_FILES


class TestSelfModifyGatesEveryAdmitPath:
    """Adversarial-review regression: SELF_MODIFY is request-absolute, so it must
    refuse on EVERY admit path — not only the keyword-with-tree path. The cluster
    fast-path, the exclusive-lane fast-path, and an IDLE repo (no live leases) all
    used to bypass the predicate conjunction; these pin that they no longer do."""

    KERNEL = ["src/dos/arbiter.py"]

    # These pin that the guard FIRES on every admit path — so they must run
    # against a config that IS the kernel serving itself (`KERNEL_CFG`, full
    # runtime-file facts). Against a foreign-repo config the same kernel-named
    # lane correctly admits (it can't rewrite a kernel that isn't there), which
    # is its own test below — here we isolate "does every path consult the guard".
    def test_cluster_request_self_modify_refused(self):
        # A --kind cluster request on a FREE lane whose tree is the kernel must
        # refuse (the cluster fast-path used to return acquire without predicates).
        d = arbiter.arbitrate(
            requested_lane="apply", requested_kind="cluster",
            requested_tree=self.KERNEL, live_leases=[], config=KERNEL_CFG,
        )
        assert d.outcome == "refuse"
        assert "SELF_MODIFY" in d.reason

    def test_cluster_request_self_modify_force_overrides(self):
        d = arbiter.arbitrate(
            requested_lane="apply", requested_kind="cluster",
            requested_tree=self.KERNEL, live_leases=[], config=KERNEL_CFG, force=True,
        )
        assert d.outcome == "acquire"

    def test_exclusive_request_self_modify_refused(self):
        # An orchestration/global lease that would rewrite the kernel must refuse
        # even with NO live leases (the exclusive fast-path used to bypass it).
        d = arbiter.arbitrate(
            requested_lane="orchestration", requested_kind="orchestration",
            requested_tree=self.KERNEL, live_leases=[], config=KERNEL_CFG,
        )
        assert d.outcome == "refuse"
        assert "SELF_MODIFY" in d.reason

    def test_exclusive_request_self_modify_force_overrides(self):
        d = arbiter.arbitrate(
            requested_lane="orchestration", requested_kind="orchestration",
            requested_tree=self.KERNEL, live_leases=[], config=KERNEL_CFG, force=True,
        )
        assert d.outcome == "acquire"

    def test_idle_repo_keyword_self_modify_refused(self):
        # The idle-repo gap: a keyword lease with a kernel tree and ZERO live
        # leases. Before the fix run_predicates was a vacuous admit; now the
        # empty-lease sentinel lets SELF_MODIFY fire.
        d = arbiter.arbitrate(
            requested_lane="k", requested_kind="keyword",
            requested_tree=self.KERNEL, live_leases=[], config=KERNEL_CFG,
        )
        assert d.outcome == "refuse"
        assert "SELF_MODIFY" in d.reason

    def test_foreign_repo_kernel_named_lane_admits(self):
        # The dual of the above: the SAME kernel-named tree against a FOREIGN-repo
        # config (empty runtime-file facts) ADMITS — a file literally named
        # `src/dos/arbiter.py` in someone else's repo is not the live kernel. This
        # is the foreign-repo over-refusal fix, pinned at the arbiter's default path.
        d = arbiter.arbitrate(
            requested_lane="k", requested_kind="keyword",
            requested_tree=self.KERNEL, live_leases=[], config=CFG,
        )
        assert d.outcome == "acquire"

    def test_cluster_disjointness_not_bypassed(self):
        # The cluster fast-path also must run DISJOINTNESS: a cluster request to a
        # FREE lane name whose tree OVERLAPS a live lease of a different cluster
        # must refuse (it used to admit on lane-name-free alone).
        overlapping = ["agents/apply_*.py", "agents/extra.py"]  # 50% overlap > 33%
        d = arbiter.arbitrate(
            requested_lane="tailor", requested_kind="cluster",
            requested_tree=overlapping,
            live_leases=[_lease("apply", "cluster", _APPLY_TREE)], config=CFG,
        )
        assert d.outcome == "refuse"

    def test_cluster_free_lane_normal_tree_still_admits(self):
        # Behavior-preservation: a normal disjoint cluster request still admits
        # through the now-gated fast-path.
        d = arbiter.arbitrate(
            requested_lane="tailor", requested_kind="cluster",
            requested_tree=["agents/tailor_*.py"],
            live_leases=[_lease("apply", "cluster", _APPLY_TREE)], config=CFG,
        )
        assert d.outcome == "acquire"
        assert d.lane == "tailor"


class TestSelfModifyReasonIsDocumented:
    """Phase-2 litmus: the SELF_MODIFY reason is a real registry member, so it is
    emittable / verifiable / refusable / `dos man wedge SELF_MODIFY`-documented."""

    def test_reason_in_base_registry(self):
        from dos.reasons import BASE_REASONS
        spec = BASE_REASONS.get("SELF_MODIFY")
        assert spec is not None
        assert spec.category == "MISROUTE"
        assert spec.refusal is True

    def test_reason_resolves_through_wedge_helpers(self):
        from dos import wedge_reason as wr
        from dos import config as _config
        saved = _config._ACTIVE
        try:
            _config.set_active(job_config("/work/userland-app"))
            assert wr.is_known_reason("SELF_MODIFY")
            assert wr.category_for("SELF_MODIFY") is wr.NoPickCategory.MISROUTE
            assert wr.is_refusal("SELF_MODIFY") is True
        finally:
            _config._ACTIVE = saved

    def test_man_page_renders(self):
        # The man page is a render of the registry; a SELF_MODIFY entry must
        # produce NAME / CATEGORY / REFUSAL lines (the DOM completeness rail).
        from dos.reasons import BASE_REASONS
        spec = BASE_REASONS.get("SELF_MODIFY")
        assert spec.summary  # has a NAME-line continuation
        assert spec.fix      # has a TYPICAL FIX line


# ============================================================================
# Phase 3 — the conjunctive-only safety guarantee + composition order
# ============================================================================
class TestConjunctiveOnlySafety:
    """The load-bearing safety tests of the whole plan: a workspace predicate can
    only ever ADD a refusal; it can never loosen a built-in refusal."""

    def test_discovered_predicate_can_refuse(self):
        # Phase-3 litmus: a registered predicate's refusal blocks admission.
        preds = built_in_predicates() + [_AlwaysRefuse("budget", "over budget")]
        d = arbiter.arbitrate(
            requested_lane="svc", requested_kind="keyword",
            requested_tree=["src/api/handlers.py"],
            live_leases=[_lease("apply", "cluster", _APPLY_TREE)],
            config=CFG, predicates=preds,
        )
        assert d.outcome == "refuse"
        assert "over budget" in d.reason

    def test_discovered_predicate_cannot_admit_over_builtin(self):
        # THE load-bearing safety test: a discovered predicate returning admit()
        # does NOT override a built-in disjointness refusal — the conjunction
        # still refuses (an OVERLAPPING keyword lane stays refused even though a
        # later predicate admits).
        preds = built_in_predicates() + [_AlwaysAdmit()]
        d = arbiter.arbitrate(
            requested_lane="apply2", requested_kind="keyword",
            requested_tree=_APPLY_TREE,           # overlaps the live apply lease
            live_leases=[_lease("apply", "cluster", _APPLY_TREE)],
            config=CFG, predicates=preds,
        )
        assert d.outcome == "refuse"

    def test_admit_only_predicate_does_not_loosen_self_modify(self):
        # An always-admit workspace predicate cannot rescue a SELF_MODIFY refusal.
        preds = built_in_predicates() + [_AlwaysAdmit()]
        d = arbiter.arbitrate(
            requested_lane="kernel", requested_kind="keyword",
            requested_tree=["src/dos/arbiter.py"],
            live_leases=[_lease("apply", "cluster", _APPLY_TREE)],
            config=CFG, predicates=preds,
        )
        assert d.outcome == "refuse"

    def test_force_overrides_a_predicate_refusal(self):
        # --force remains the sole override of ANY predicate refusal.
        preds = built_in_predicates() + [_AlwaysRefuse("budget", "over budget")]
        d = arbiter.arbitrate(
            requested_lane="svc", requested_kind="keyword",
            requested_tree=["src/api/handlers.py"],
            live_leases=[_lease("apply", "cluster", _APPLY_TREE)],
            config=CFG, predicates=preds, force=True,
        )
        assert d.outcome == "acquire"


class TestArbiterPurityDefault:
    """The arbiter stays PURE: its default (`predicates=None`) uses the built-in
    set ONLY and does NO entry-point discovery (I/O) on the arbitration path.
    Discovery happens at the CALL BOUNDARY (the CLI), like `pick_oracle`. This
    pins that decision so a future change can't silently re-introduce discovery
    I/O into the pure kernel."""

    def test_default_does_not_run_entry_point_discovery(self, monkeypatch):
        import dos.admission as adm
        calls = {"n": 0}
        def _boom(*a, **k):
            calls["n"] += 1
            raise AssertionError("arbitrate() must NOT run discovery on its "
                                 "default path — that is I/O in the pure kernel")
        monkeypatch.setattr(adm, "_discover_entry_point_predicates", _boom)
        # A plain arbitrate with no explicit predicates must not touch discovery.
        d = arbiter.arbitrate(
            requested_lane="apply", requested_kind="cluster",
            requested_tree=[], live_leases=[], config=CFG,
        )
        assert d.outcome == "acquire"
        assert calls["n"] == 0

    def test_default_still_applies_self_modify_guard(self):
        # `predicates=None` is built-ins-only, but self-modify IS a built-in, so a
        # programmatic caller still gets the guard without passing anything — when
        # the config's facts say this IS the kernel repo (KERNEL_CFG). The default
        # path reads those CACHED facts (no I/O), so it is both pure AND precise.
        d = arbiter.arbitrate(
            requested_lane="kernel", requested_kind="keyword",
            requested_tree=["src/dos/arbiter.py"],
            live_leases=[_lease("apply", "cluster", _APPLY_TREE)], config=KERNEL_CFG,
        )
        assert d.outcome == "refuse"
        assert "SELF_MODIFY" in d.reason


class TestPickCountConsistency:
    """Adversarial-review regression: the auto-pick path used to call the oracle
    TWICE per admitted lane — once to decide admission, once to populate
    pick_count — so a non-deterministic oracle could report a count that
    disagreed with the value that drove the decision. The arbiter now caches the
    admission-driving count and reports THAT."""

    def test_pick_count_matches_admission_call_with_nondeterministic_oracle(self):
        # An oracle that returns 7 the first time it's asked about the winner and
        # something else after. pick_count must be 7 (the value that admitted).
        calls = {}
        def oracle(name, kind, tree):
            calls[name] = calls.get(name, 0) + 1
            if name == "tailor":
                return 7 if calls[name] == 1 else 999  # drift on a 2nd call
            return 0  # apply has no picks → skipped
        ladder = [("apply", "cluster", _APPLY_TREE),
                  ("tailor", "cluster", ["agents/tailor_*.py"])]
        d = arbiter.arbitrate(
            requested_lane="", requested_kind="", requested_tree=[],
            auto_pick_order=ladder, live_leases=[], config=CFG, pick_oracle=oracle,
        )
        assert d.outcome == "acquire" and d.lane == "tailor"
        assert d.pick_count == 7, "pick_count must be the count that drove admission"
        # The winner's oracle was consulted exactly ONCE (no double call).
        assert calls["tailor"] == 1


class TestActivePredicates:
    def test_built_ins_lead_in_order(self):
        # docs/364 — the declared-call-shape guard is the THIRD built-in, appended
        # AFTER the two structural guards (so it can only refuse-MORE, never
        # displace them). It is always present but OFF by default (its EMPTY policy
        # short-circuits to admit), so its addition changes the NAME list here but
        # not the default admission VERDICT.
        names = [getattr(p, "name", type(p).__name__) for p in built_in_predicates()]
        assert names == ["disjointness", "self-modify", "call-shape"]

    def test_active_predicate_names_includes_built_ins(self):
        import io
        names = active_predicate_names(_stderr=io.StringIO())
        assert names[:3] == ["disjointness", "self-modify", "call-shape"]

    def test_active_predicates_returns_callables(self):
        import io
        for p in active_predicates(_stderr=io.StringIO()):
            assert callable(p)
