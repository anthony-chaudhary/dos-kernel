"""Regression tests for the overlap-policy seam (`dos.overlap_policy`) + its eval
harness (`dos.overlap_eval`) — Axis 7 of hackability (docs/113).

The seam opens the kernel's most load-bearing scalar (the ⅓ soft-overlap ratio
that decides whether two agents may write concurrently) the way every other axis
is open: a pure policy object behind a by-name resolver. The non-negotiable these
tests pin is the **structural soundness floor**: a swappable scorer may turn an
ADMIT into a REFUSE, but can NEVER turn a REFUSE into an ADMIT relative to the
unforgeable prefix-disjointness floor — so a buggy/hostile/raising policy can only
refuse-MORE, never admit a collision.

Organised as:
  * `TestDefaultEquivalence`   — explicit prefix policy under the floor ==
    `overlap_verdict`, byte-for-byte, while the constructed default is lock_modes.
  * `TestSoundnessFloor`       — the security core: no policy can admit past the
    prefix floor (lying-admit, over-ratio, raise, garbage-return all fail-closed).
  * `TestSafeDirection`        — a stricter policy CAN refuse what the floor admits.
  * `TestResolver`             — built-in `prefix` first + unshadowable; unknown
    fails loud; data seam (`overlap_ratio_max` / `overlap_policy_name`).
  * `TestArbiterIntegration`   — a hostile `OverlapPolicy` routed through the REAL
    `arbiter.arbitrate` cannot double-book a held lane.
  * `TestOverlapEval`          — the instrument's confusion grid + the false-admit
    cell + the exit-code-on-leak boolean.
  * `TestConstantPinned`       — the legacy prefix ratio remains pinned while the
    active config default is the lock-mode policy.
"""
from __future__ import annotations

import dataclasses

import dos.config as config
import dos.lane_overlap as lane_overlap
import dos.overlap_policy as op
from dos.lane_overlap import OverlapDecision, Verdict, overlap_verdict


# ── helpers ─────────────────────────────────────────────────────────────────
def _cfg(**overrides):
    """A default config, optionally with overlap fields tweaked."""
    c = config.default_config(".")
    if overrides:
        c = dataclasses.replace(c, **overrides)
    return c


class _AlwaysAdmit:
    """A HOSTILE policy: claims everything is disjoint, even identical globs."""
    name = "evil-admit"
    def overlaps(self, req, lease, cfg):
        return OverlapDecision(Verdict.ADMIT_DISJOINT, 0, len(req), "I LIE: always safe")


class _AlwaysRefuse:
    """A maximally-strict policy: refuses everything."""
    name = "paranoid"
    def overlaps(self, req, lease, cfg):
        return OverlapDecision(Verdict.REFUSE_OVERLAP, 99, len(req), "I refuse everything")


class _Raises:
    name = "boom"
    def overlaps(self, req, lease, cfg):
        raise RuntimeError("policy blew up")


class _ReturnsGarbage:
    name = "garbage"
    def overlaps(self, req, lease, cfg):
        return "not an OverlapDecision"


# ── 1. byte-for-byte default equivalence ────────────────────────────────────
class TestDefaultEquivalence:
    PAIRS = [
        (["agents/apply_*.py"], ["agents/apply_*.py"]),                  # exact glob
        (["playbooks/ats/workday.yaml"], ["agents/apply_*.py"]),         # disjoint
        (["src/api/x.py", "src/api/y.py", "docs/z.md"], ["src/api/**"]),  # soft
        (["**/*"], ["**/*"]),                                            # whole-repo
        (["a.py", "b.py", "c.py", "d.py"], ["a.py"]),                    # low ratio
    ]

    def test_prefix_policy_under_floor_equals_overlap_verdict(self):
        pol = op.PrefixOverlapPolicy()
        cfg = _cfg()
        for a, b in self.PAIRS:
            direct = overlap_verdict(a, b)
            floored = op.admissible_under_floor(pol, a, b, cfg)
            assert direct.verdict == floored.verdict, (a, b, direct.verdict, floored.verdict)
            assert direct.admissible == floored.admissible, (a, b)

    def test_explicit_prefix_predicate_is_pure_prefix(self):
        # A DisjointnessPredicate(policy=PrefixOverlapPolicy()) keeps the legacy
        # ratio scorer available for explicit ratio users.
        from dos.admission import AdmissionRequest, DisjointnessPredicate
        pred = DisjointnessPredicate(policy=op.PrefixOverlapPolicy())
        cfg = _cfg()
        for a, b in self.PAIRS:
            req = AdmissionRequest(lane="x", kind="cluster", tree=tuple(a))
            v = pred(req, {"lane": "y", "tree": b}, cfg)
            expected_admit = overlap_verdict(a, b).admissible
            assert v.admitted == expected_admit, (a, b)

    def test_default_constructed_predicate_uses_lock_modes(self):
        # The current default is the sound lock-mode predicate: a low-ratio
        # write/write prefix collision refuses even though the legacy 1/3 ratio
        # would have admitted it.
        from dos.admission import AdmissionRequest, DisjointnessPredicate

        pred = DisjointnessPredicate()
        req_tree = ["src/api/x.py", "private/a.py", "private/b.py", "private/c.py"]
        lease_tree = ["src/api/**"]
        assert overlap_verdict(req_tree, lease_tree).admissible is True
        req = AdmissionRequest(lane="x", kind="cluster", tree=tuple(req_tree))
        v = pred(req, {"lane": "y", "tree": lease_tree}, _cfg())
        assert v.admitted is False
        assert "lock-mode conflict" in v.reason


# ── 2. the structural soundness floor (the security core) ───────────────────
class TestSoundnessFloor:
    # Pairs the prefix floor REFUSES (a collision at any tolerance).
    REFUSED_BY_FLOOR = [
        (["src/dos/arbiter.py"], ["src/dos/arbiter.py"]),       # exact glob
        (["src/api/a.py", "src/api/b.py"], ["src/api/**"]),     # >1/3 overlap
        (["**/*"], ["**/*"]),                                   # whole-repo
    ]

    def test_lying_admit_policy_cannot_admit_a_collision(self):
        evil = _AlwaysAdmit()
        cfg = _cfg()
        for a, b in self.REFUSED_BY_FLOOR:
            assert not op.floor_decision(a, b).admissible, ("floor should refuse", a, b)
            result = op.admissible_under_floor(evil, a, b, cfg)
            assert not result.admissible, ("HOSTILE POLICY ADMITTED A COLLISION", a, b)

    def test_raising_policy_fails_closed_when_floor_refuses(self):
        # When the floor refuses, the policy is never even called — the floor
        # verdict is returned directly (so its reason, not "raised", is carried).
        cfg = _cfg()
        for a, b in self.REFUSED_BY_FLOOR:
            result = op.admissible_under_floor(_Raises(), a, b, cfg)
            assert not result.admissible, ("raise should fail-closed", a, b)

    def test_raising_policy_fails_closed_when_floor_admits(self):
        # When the floor ADMITS, the policy IS invoked; a raise must fall back to
        # the floor (admit) — NOT propagate, NOT crash. This exercises the
        # policy-invoked fail-closed branch and names the failure.
        a, b = ["totally/disjoint.py"], ["other/region.py"]
        assert op.floor_decision(a, b).admissible, "floor should admit disjoint"
        result = op.admissible_under_floor(_Raises(), a, b, _cfg())
        assert "raised" in result.reason
        # The floor admits, so falling back to it admits — a raising policy cannot
        # turn a floor-admit into a refuse OR a crash; it degrades to today's behavior.
        assert result.admissible

    def test_garbage_return_fails_closed_when_floor_admits(self):
        a, b = ["totally/disjoint.py"], ["other/region.py"]
        result = op.admissible_under_floor(_ReturnsGarbage(), a, b, _cfg())
        assert "not an OverlapDecision".lower() in result.reason.lower() or \
               "OverlapDecision" in result.reason
        assert result.admissible  # falls back to the floor (which admits)

    def test_floor_reason_is_carried_unforgeable(self):
        # When the floor refuses, the operator-facing reason is the FLOOR's reason,
        # so a hostile policy cannot even dilute the message.
        a, b = ["src/dos/arbiter.py"], ["src/dos/arbiter.py"]
        result = op.admissible_under_floor(_AlwaysAdmit(), a, b, _cfg())
        assert "exact-glob" in result.reason  # the floor's reason, not the policy's lie

    def test_loosened_ratio_max_is_capped_by_the_floor(self):
        # The data knob can TIGHTEN below 1/3 freely, but LOOSENING above 1/3 is
        # capped — the floor stays at the kernel 1/3 (a fixed safety ceiling an
        # operator cannot raise with a config line). A 50%-overlap pair: a loose
        # policy ratio admits it, but net admission (policy AND floor) refuses.
        loose_cfg = _cfg(overlap_ratio_max=0.99)
        a, b = ["src/api/x.py", "unrelated/y.py"], ["src/api/**"]   # 1/2 = 50% shared
        pol = op.PrefixOverlapPolicy()
        # The policy itself, at 0.99, would admit the 50% pair…
        assert pol.overlaps(a, b, loose_cfg).admissible
        # …but the floor (fixed 1/3) re-refuses it, so net admission is REFUSE.
        assert not op.admissible_under_floor(pol, a, b, loose_cfg).admissible
        # And the floor verdict itself ignores the workspace ratio entirely.
        assert not op.floor_decision(a, b).admissible

    def test_tightened_ratio_max_works(self):
        # The flip side: tightening below 1/3 DOES take effect (the policy is the
        # stricter voice; the floor doesn't interfere). 20% pair admits at 1/3 but
        # not at 0.1.
        tight_cfg = _cfg(overlap_ratio_max=0.1)
        a = ["src/api/n.py", "d/a.md", "d/b.md", "d/c.md", "d/e.md"]   # 1/5 = 20%
        b = ["src/api/**"]
        assert op.admissible_under_floor(op.PrefixOverlapPolicy(), a, b, _cfg()).admissible
        assert not op.admissible_under_floor(op.PrefixOverlapPolicy(), a, b, tight_cfg).admissible


# ── 3. the safe direction: a stricter policy CAN refuse-more ────────────────
class TestSafeDirection:
    def test_strict_policy_refuses_what_floor_admits(self):
        # A genuinely-disjoint pair the floor ADMITS; a paranoid policy refuses it.
        a, b = ["totally/disjoint.py"], ["other/region.py"]
        assert op.floor_decision(a, b).admissible, "floor should admit disjoint"
        result = op.admissible_under_floor(_AlwaysRefuse(), a, b, _cfg())
        assert not result.admissible, "a stricter policy must be able to refuse-more"

    def test_strict_policy_reason_surfaces(self):
        a, b = ["totally/disjoint.py"], ["other/region.py"]
        result = op.admissible_under_floor(_AlwaysRefuse(), a, b, _cfg())
        assert "refuse everything" in result.reason


# ── 4. resolver + data seam ─────────────────────────────────────────────────
class TestResolver:
    def test_prefix_is_builtin_and_resolvable(self):
        assert op.resolve_overlap_policy("prefix").name == "prefix"

    def test_unknown_policy_fails_loud(self):
        import pytest
        with pytest.raises(ValueError, match="unknown overlap policy"):
            op.resolve_overlap_policy("nonesuch-xyz")

    def test_active_policy_defaults_to_lock_modes_with_no_config(self):
        assert op.active_overlap_policy().name == "lock_modes"

    def test_active_policy_reads_config_name(self):
        # A config naming `prefix` resolves the built-in without discovery.
        cfg = _cfg(overlap_policy_name="prefix")
        assert op.active_overlap_policy(config=cfg).name == "prefix"

    def test_ratio_max_from_toml_selects_legacy_prefix_policy(self, tmp_path):
        (tmp_path / "dos.toml").write_text(
            "[overlap]\nratio_max = 0.1\n",
            encoding="utf-8",
        )
        cfg = config.load_workspace_config(tmp_path)
        assert cfg.overlap_policy_name == "prefix"
        assert cfg.overlap_ratio_max == 0.1

    def test_ratio_max_from_config_used_by_prefix_policy(self):
        # A stricter ratio makes the prefix policy refuse a pair the default admits.
        # 1 shared of 3 = 33.3%: admits at ⅓ (33.3 <= 33.3? no, it's strictly >);
        # use a pair that admits at ⅓ but not at 0.2.
        a = ["src/api/x.py", "docs/a.md", "docs/b.md", "docs/c.md", "docs/d.md"]  # 1/5 = 20%
        b = ["src/api/**"]
        default_cfg = _cfg()                              # ratio_max = 1/3
        strict_cfg = _cfg(overlap_ratio_max=0.1)          # ratio_max = 0.1
        pol = op.PrefixOverlapPolicy()
        assert pol.overlaps(a, b, default_cfg).admissible       # 20% <= 33% → admit
        assert not pol.overlaps(a, b, strict_cfg).admissible    # 20% > 10% → refuse

    def test_ratio_max_malformed_falls_back_to_default(self):
        for bad in (None, "abc", -1.0, 5.0, 0.0):
            cfg = _cfg(overlap_ratio_max=bad) if bad is not None else _cfg()
            # _ratio_max_from_config guards: a bad value yields the kernel default.
            got = op._ratio_max_from_config(cfg if bad is not None else object())
            assert abs(got - lane_overlap.OVERLAP_RATIO_MAX) < 1e-12, bad


# ── 5. arbiter-level integration (a hostile plugin can't double-book) ───────
class TestArbiterIntegration:
    def test_hostile_policy_cannot_double_book_via_arbitrate(self):
        # The end-to-end proof: route a lying-admit policy through the REAL
        # arbiter (not just admissible_under_floor in isolation). A held lane with
        # an identical tree must STILL be refused — the floor is under the predicate
        # the arbiter runs, so the hostile policy cannot loosen admission.
        from dos import arbiter
        from dos.admission import DisjointnessPredicate
        from dos.self_modify import SelfModifyPredicate

        cfg = _cfg()
        evil_predicates = [DisjointnessPredicate(policy=_AlwaysAdmit()), SelfModifyPredicate()]
        live = [{"lane": "held", "lane_kind": "cluster", "tree": ["src/api/**"]}]
        decision = arbiter.arbitrate(
            requested_lane="contender",
            requested_kind="cluster",
            requested_tree=["src/api/**"],   # identical region → must refuse
            live_leases=live,
            config=cfg,
            predicates=evil_predicates,
        )
        assert decision.outcome == "refuse", (
            "a hostile overlap policy double-booked a held lane through arbitrate — "
            "the soundness floor is NOT structural at the arbiter level"
        )

    def test_default_arbitrate_still_admits_disjoint(self):
        # The flip side: the seam must not over-refuse a genuinely-disjoint pair on
        # the default path.
        from dos import arbiter
        cfg = _cfg()
        live = [{"lane": "held", "lane_kind": "cluster", "tree": ["src/worker/**"]}]
        decision = arbiter.arbitrate(
            requested_lane="web",
            requested_kind="cluster",
            requested_tree=["src/web/**"],
            live_leases=live,
            config=cfg,
        )
        assert decision.outcome == "acquire"


# ── 6. the eval harness ─────────────────────────────────────────────────────
class TestOverlapEval:
    def _cases(self):
        from dos.overlap_eval import OverlapCase
        return [
            OverlapCase(["src/api/x.py"], ["src/api/x.py"], collided=True, label="same"),
            OverlapCase(["src/web/**"], ["src/worker/**"], collided=False, label="disjoint"),
            # 1 shared of 4 = 25% ≤ 33% → soft-admit, labelled safe. The shared
            # entry collides by PREFIX (src/api/*) with the lease, not exact-glob.
            OverlapCase(["src/api/n.py", "w/p.py", "w/q.py", "w/r.py"], ["src/api/**"], collided=False, label="low-ratio"),
            OverlapCase(["src/api/a.py", "src/api/b.py"], ["src/api/**"], collided=True, label="heavy"),
        ]

    def test_prefix_policy_perfectly_classifies_path_collisions(self):
        from dos import overlap_eval
        report = overlap_eval.score(op.PrefixOverlapPolicy(), self._cases(), config=_cfg())
        assert report.n == 4
        assert report.false_admit == 0           # no collision admitted
        assert report.correct_refuse == 2        # both colliding pairs refused
        assert report.correct_admit == 2         # both safe pairs admitted
        assert report.safe_forgone == 0
        assert report.leaked is False
        assert report.false_admit_rate == 0.0
        assert report.decisive_accuracy == 1.0

    def test_leak_is_detected_when_policy_admits_a_collision(self):
        # A semantic collision under DISJOINT paths: the prefix policy admits it
        # (the floor can't see it) → a false-admit → leaked True.
        from dos import overlap_eval
        from dos.overlap_eval import OverlapCase
        cases = [
            OverlapCase(["src/featureflags.py"], ["config/flags.yaml"], collided=True),
            OverlapCase(["src/web/**"], ["src/worker/**"], collided=False),
        ]
        report = overlap_eval.score(op.PrefixOverlapPolicy(), cases, config=_cfg())
        assert report.false_admit == 1
        assert report.leaked is True
        assert report.collision_leak_rate == 1.0   # 1 of 1 colliding pairs leaked

    def test_eval_runs_policy_under_the_floor(self):
        # Even a lying-admit policy cannot register a false-admit on a path-colliding
        # pair in the eval, because score() runs it UNDER the floor.
        from dos import overlap_eval
        from dos.overlap_eval import OverlapCase
        cases = [OverlapCase(["src/api/x.py"], ["src/api/x.py"], collided=True)]
        report = overlap_eval.score(_AlwaysAdmit(), cases, config=_cfg())
        assert report.false_admit == 0   # the floor refused it despite the lie
        assert report.correct_refuse == 1
        assert report.leaked is False


# ── 7. the constant must not drift ──────────────────────────────────────────
class TestConstantPinned:
    def test_config_default_has_no_active_ratio(self):
        assert config._DEFAULT_OVERLAP_RATIO_MAX is None

    def test_substrate_config_default_is_lock_modes(self):
        cfg = config.default_config(".")
        assert cfg.overlap_ratio_max is None
        assert cfg.overlap_policy_name == "lock_modes"

    def test_legacy_prefix_fallback_ratio_mirrors_lane_overlap(self):
        assert op._ratio_max_from_config(object()) == lane_overlap.OVERLAP_RATIO_MAX
