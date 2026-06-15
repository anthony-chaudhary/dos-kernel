"""Tests for the structured-refusal plane (`dos.wedge_reason` + `dos.picker_oracle`)
and the verdict vocabulary (`dos.tokens` + `dos.gate_classify`).

The refusal plane is the syscall the dispatch-os-vision §6 ranks the kernel's
single most-important feature: a closed `reason_class` enum that is simultaneously
emittable, verifiable, and refusable. These tests pin the lockstep property the
origin guards (`tests/test_dispatch_pick_observability.py`): the oracle's
`REASON_CLASS_MAP` recognizes every closed `WedgeReason` member.
"""

from __future__ import annotations

import dataclasses

import pytest

from dos import wedge_reason as wr
from dos import picker_oracle
from dos import tokens
from dos import gate_classify
from dos import config as _config
from dos import reason_morphology as rm
from dos.reasons import BASE_REASONS, ReasonSpec, ReasonRegistry, specs_from_table, KNOWN_CATEGORIES


class TestWedgeReasonClosedEnum:
    def test_every_member_has_a_category(self):
        for r in wr.WedgeReason:
            assert r in wr.REASON_TO_CATEGORY

    def test_coerce_roundtrips_and_is_case_insensitive(self):
        assert wr.coerce("LANE_DRAINED") is wr.WedgeReason.LANE_DRAINED
        assert wr.coerce("lane_drained") is wr.WedgeReason.LANE_DRAINED
        assert wr.coerce("  LANE_DRAINED  ") is wr.WedgeReason.LANE_DRAINED

    def test_unknown_token_is_unclassified(self):
        assert wr.coerce("NOT_A_REAL_TOKEN") is None
        assert wr.category_for("NOT_A_REAL_TOKEN") is wr.NoPickCategory.UNCLASSIFIED

    def test_known_reasons_are_refusals(self):
        assert wr.is_refusal("LANE_DRAINED")
        # An unrecognised no-pick token is refused conservatively.
        assert wr.is_refusal("MYSTERY_TOKEN")


class TestNoFreeRegionReason:
    """docs/97 §model — the typed refuse a `TopPickablePlan` open-pool source emits
    when every candidate region overlaps a live lease (the pool is full of contended
    regions, not empty of work). It is declared in BASE_REASONS so the
    region-source-emitted reason is simultaneously emittable, verifiable, and
    refusable — the SELF_MODIFY/UNKNOWN_LANE completeness rail."""

    def test_no_free_region_is_declared(self):
        spec = BASE_REASONS.get("NO_FREE_REGION")
        assert spec is not None
        assert spec.token == "NO_FREE_REGION"

    def test_no_free_region_rolls_up_to_true_drain(self):
        # TRUE_DRAIN, not OPERATOR_GATE/MISROUTE: the regions are all taken, the work
        # exists — wait for a holder to release, do NOT /replan.
        assert BASE_REASONS.category_for("NO_FREE_REGION") == "TRUE_DRAIN"
        assert "TRUE_DRAIN" in KNOWN_CATEGORIES

    def test_no_free_region_is_a_refusal(self):
        assert BASE_REASONS.is_refusal("NO_FREE_REGION") is True

    def test_no_free_region_is_distinct_from_class_budget_exhausted(self):
        # The two open-pool refuses must not be the same token: NO_FREE_REGION =
        # "no disjoint region remains" (budget has room); CLASS_BUDGET_EXHAUSTED =
        # "the class is at its max_concurrent". A fix line that conflates them
        # mis-routes the operator.
        spec = BASE_REASONS.get("NO_FREE_REGION")
        assert "CLASS_BUDGET_EXHAUSTED" in spec.fix  # the disambiguation is curated in


class TestRefusalLockstep:
    def test_oracle_recognizes_every_wedge_reason(self):
        # The load-bearing invariant: a reason class added to wedge_reason is
        # simultaneously verifiable by the oracle (no silent drift).
        for r in wr.WedgeReason:
            assert r.value in picker_oracle.REASON_CLASS_MAP, (
                f"{r.value} emittable but NOT verifiable by picker_oracle"
            )

    def test_reason_class_map_categories_agree_with_wedge_reason(self):
        for r, category in wr.REASON_TO_CATEGORY.items():
            cause = picker_oracle.REASON_CLASS_MAP.get(r.value)
            assert cause is not None
            # Both enums are str-valued; the category string must round-trip.
            assert str(cause.value) == str(category.value)


class TestVerdictTokens:
    def test_legacy_wedge_aliases_normalize_to_blocked(self):
        assert tokens.normalize_token("WEDGE") == "BLOCKED"
        assert tokens.normalize_token("WEDGED") == "BLOCKED-OUTCOME"

    def test_gate_verdict_is_str_valued(self):
        assert gate_classify.Verdict.LIVE == "LIVE"
        # The permanent WEDGE alias is the same object as BLOCKED.
        assert gate_classify.Verdict.WEDGE is gate_classify.Verdict.BLOCKED

    def test_blocked_reason_catalog_lookup(self):
        info = tokens.blocked_reason_for_key("ship_oracle_false_positive")
        assert info is not None
        assert not info.operator_action_required


class TestRecurringWedgeRouting:
    """FQ-420 recurring-wedge cue gap: a RECURRING uncategorized non-ship must
    escalate to /unstick, and the body-empty / dropped-`.prompts.json` sidecar
    shape must have a typed cause that routes to /unstick on the first hit.

    The catalog's static `self_heals_via` is the *one-off* remedy; the
    recurrence-aware `recurring_self_heal_for` is the routing the /unstick sweep
    consults. A refill sweep (/replan) cannot fix a defect that keeps recurring.
    """

    def test_body_empty_picks_cause_is_typed_and_routes_to_unstick(self):
        # The FQ-420 sidecar-drop shape has a named cause (no longer a fall
        # into uncategorized) that self-heals via /unstick from the first hit —
        # /replan cannot restore a sidecar the renderer never wrote.
        info = tokens.blocked_reason_for_key("body_empty_picks")
        assert info is not None
        assert info.self_heals_via == "/unstick"
        assert not info.operator_action_required
        assert tokens.recurring_self_heal_for("body_empty_picks", runs_affected=1) == "/unstick"

    def test_one_off_uncategorized_nonship_routes_to_replan(self):
        # A single occurrence is plausibly transient — keep the /replan default.
        assert (
            tokens.recurring_self_heal_for("uncategorized_nonship", runs_affected=1)
            == "/replan"
        )

    def test_recurring_uncategorized_nonship_escalates_to_unstick(self):
        # The bug: "5th consecutive" uncategorized non-ship kept routing to
        # /replan. Once it crosses the recurrence floor it must go to /unstick.
        assert (
            tokens.recurring_self_heal_for("uncategorized_nonship", runs_affected=5)
            == "/unstick"
        )
        # And exactly at the threshold.
        assert (
            tokens.recurring_self_heal_for(
                "uncategorized_nonship",
                runs_affected=tokens.RECURRENCE_ESCALATION_RUNS,
            )
            == "/unstick"
        )

    def test_recurring_gate_wedge_unspecified_also_escalates(self):
        # The other catch-all (blocked but no structural cause named) escalates
        # the same way — a loop that keeps wedging without recording why is a
        # structural fix target, not a /replan refill.
        assert (
            tokens.recurring_self_heal_for("gate_wedge_unspecified", runs_affected=4)
            == "/unstick"
        )

    def test_recurrence_does_not_override_a_specific_channel(self):
        # A cause that already names a specific channel keeps it regardless of
        # recurrence — a soak-gated lane is /replan however often it recurs (it
        # is not a structural defect, just not-dispatchable-yet).
        assert (
            tokens.recurring_self_heal_for("lane_soak_gated", runs_affected=9)
            == "/replan"
        )

    def test_unknown_cause_has_no_remedy(self):
        assert tokens.recurring_self_heal_for("not_a_real_cause", runs_affected=9) == ""
        assert tokens.recurring_self_heal_for(None, runs_affected=9) == ""


class TestReasonClassProseFallback:
    """The producer prints `reason_class=` into the headless session result even
    when the structured `.verdict-<tag>.json` envelope drops the field. Measured
    on job's real corpus (2026-06-02), 29 of 62 unclassifiable NO-PICKs carried a
    recoverable token in that prose — recall was vacuous over them for a plumbing
    reason, not a real one. `classify` recovers the emitted-but-unlifted token so
    the oracle grades the decision instead of abstaining to UNCLASSIFIED."""

    def _no_pick_verdict_env(self, **extra):
        # A WEDGE verdict envelope WITHOUT a reason_class field (the gap shape).
        env = {"tag": "next-up-2026-06-02-1", "verdict": "WEDGE", "all_clear": False}
        env.update(extra)
        return env

    def test_recovers_reason_class_from_dispatch_result(self):
        # The token lives only in the dispatch result prose; the structured
        # envelope omits it. The oracle should still classify (here: a STALE_CLAIM
        # category token), not fall to UNCLASSIFIED.
        v = picker_oracle.classify(
            run_ts="20260602T000000Z",
            verdict_env=self._no_pick_verdict_env(),
            dispatch_env={
                "result": "verdict=WEDGE tag=next-up-2026-06-02-1 "
                "reason_class=STALE_CLAIM_COLLISION route=/replan"
            },
            readme="",
            state={},
        )
        assert v.outcome is picker_oracle.PickerOutcome.NO_PICK
        assert v.no_pick_cause is not picker_oracle.NoPickCause.UNCLASSIFIED
        assert any("recovered reason_class" in e for e in v.evidence)

    def test_structured_field_wins_over_prose(self):
        # When the envelope DOES carry reason_class, prose is never consulted —
        # the structured value is authoritative.
        v = picker_oracle.classify(
            run_ts="20260602T000001Z",
            verdict_env=self._no_pick_verdict_env(reason_class="DRAIN"),
            dispatch_env={"result": "reason_class=MIS_ROUTED_FINDING"},
            readme="",
            state={},
        )
        assert v.no_pick_cause is picker_oracle.NoPickCause.TRUE_DRAIN
        assert not any("recovered reason_class" in e for e in v.evidence)

    def test_genuinely_absent_token_stays_unclassified(self):
        # No token anywhere → the honest UNCLASSIFIED abstention is preserved
        # (the fallback must not invent a cause from nothing).
        v = picker_oracle.classify(
            run_ts="20260602T000002Z",
            verdict_env=self._no_pick_verdict_env(),
            dispatch_env={"result": "no structured token here at all"},
            readme="just prose, no machine tokens",
            state={},
        )
        assert v.no_pick_cause is picker_oracle.NoPickCause.UNCLASSIFIED

    def test_recover_helper_scans_in_order_and_misses_cleanly(self):
        # Unit-level: first text wins; lowercase/garbage yields "".
        assert (
            picker_oracle._recover_reason_class("x reason_class=LANE_FOO y", "reason_class=BAR")
            == "LANE_FOO"
        )
        assert picker_oracle._recover_reason_class("", "reason_class=BAZ") == "BAZ"
        assert picker_oracle._recover_reason_class("reason_class=lowercase", "") == ""
        assert picker_oracle._recover_reason_class("", "") == ""


@pytest.fixture
def restore_active_config():
    """Save/restore the process-active config so a test that installs a custom
    registry doesn't leak it into the next test (the active config is process-
    global by design)."""
    saved = _config._ACTIVE
    try:
        yield
    finally:
        _config._ACTIVE = saved


class TestCustomReasonHackability:
    """The hackability contract: a WORKSPACE-DECLARED block reason is
    simultaneously emittable, verifiable, refusable, and man-projectable —
    through the same kernel calls a built-in reason uses, with no package edit.

    This is the load-bearing property the registry-as-data refactor buys; if it
    regresses, the kernel has silently re-coupled policy (the reason set) to
    mechanism (the package).
    """

    def _install(self, *specs: ReasonSpec) -> None:
        cfg = _config.default_config(".")
        cfg = dataclasses.replace(cfg, reasons=BASE_REASONS.extend(specs))
        _config.set_active(cfg)

    def test_builtins_are_byte_compatible_with_a_default_registry(self, restore_active_config):
        # With only the base registry active, every built-in behaves exactly as
        # before the refactor (this is what keeps the 34 legacy tests green).
        _config.set_active(_config.default_config("."))
        assert wr.coerce("LANE_DRAINED") is wr.WedgeReason.LANE_DRAINED
        assert wr.category_for("LANE_DRAINED") is wr.NoPickCategory.TRUE_DRAIN
        assert wr.is_refusal("LANE_DRAINED") is True

    def test_declared_refusal_reason_is_known_categorised_and_refused(self, restore_active_config):
        self._install(ReasonSpec(
            token="LANE_PARKED_FOR_BUDGET", category="OPERATOR_GATE",
            refusal=True, summary="budget hit", fix="raise the cap",
        ))
        # emittable
        assert wr.is_known_reason("LANE_PARKED_FOR_BUDGET")
        # categorised (man-projectable) — and the category is a real NoPickCategory
        assert wr.category_for("LANE_PARKED_FOR_BUDGET") is wr.NoPickCategory.OPERATOR_GATE
        # refusable
        assert wr.is_refusal("LANE_PARKED_FOR_BUDGET") is True
        # verifiable — the oracle resolves the SAME cause (lockstep preserved)
        assert picker_oracle.resolve_cause("LANE_PARKED_FOR_BUDGET") is \
            picker_oracle.NoPickCause.OPERATOR_GATE

    def test_declared_advisory_reason_is_known_but_not_refused(self, restore_active_config):
        # refusal=False means a workspace can declare a deferred-but-valid reason.
        self._install(ReasonSpec(
            token="LANE_ADVISORY_SLOW", category="STALE_CLAIM", refusal=False,
        ))
        assert wr.is_known_reason("LANE_ADVISORY_SLOW")
        assert wr.is_refusal("LANE_ADVISORY_SLOW") is False

    def test_undeclared_token_stays_drift_safe(self, restore_active_config):
        self._install(ReasonSpec(token="LANE_X", category="MISROUTE"))
        # A token in NEITHER the built-ins NOR the registry is unknown,
        # UNCLASSIFIED, and conservatively refused — the forward-compat default.
        assert not wr.is_known_reason("TOTALLY_MADE_UP")
        assert wr.category_for("TOTALLY_MADE_UP") is wr.NoPickCategory.UNCLASSIFIED
        assert wr.is_refusal("TOTALLY_MADE_UP") is True

    def test_registry_rejects_a_bad_category(self):
        # A reason must roll up to a category the oracle can verify against.
        with pytest.raises(ValueError):
            ReasonSpec(token="LANE_BAD", category="NOT_A_CATEGORY")

    def test_registry_rejects_a_duplicate_token(self):
        with pytest.raises(ValueError):
            BASE_REASONS.extend([ReasonSpec(token="LANE_DRAINED", category="TRUE_DRAIN")])

    def test_specs_from_toml_table_roundtrips(self):
        # The declarative (dos.toml) path produces the same specs as the API path.
        specs = specs_from_table({
            "LANE_PARKED_FOR_BUDGET": {
                "category": "OPERATOR_GATE", "refusal": True,
                "summary": "budget hit", "fix": "raise the cap",
                "see_also": ["meta budget"],
            }
        })
        assert len(specs) == 1
        s = specs[0]
        assert s.key == "LANE_PARKED_FOR_BUDGET"
        assert s.category == "OPERATOR_GATE"
        assert s.refusal is True
        assert s.see_also == ("meta budget",)

    def test_specs_from_toml_rejects_missing_category(self):
        with pytest.raises(ValueError):
            specs_from_table({"LANE_NO_CAT": {"refusal": True}})


class TestReasonMorphologyLeaf:
    """The rung-2 recognizer leaf (`dos.reason_morphology`, docs/105) — pure, no
    active config. Pins the closed-category discipline, first-match-wins order,
    and the loud-on-malformed posture mirroring `stamp.convention_from_table`."""

    def test_known_categories_equal_reasons_known_categories(self):
        # The leaf's category vocabulary must never drift from the registry's.
        assert set(rm.KNOWN_CATEGORIES) == set(KNOWN_CATEGORIES)

    def test_generic_ruleset_names_no_host_lane(self):
        # The "kernel imports no host" litmus, reason-class flavor: the SHIPPED
        # generic morphology must carry only domain-free shapes — never a host
        # lane token (apply/tailor/discovery) or a host commit/dir dialect.
        forbidden = ("APPLY", "TAILOR", "DISCOVERY", "JOB_SEARCH", "DOCS/", "DISPATCH:")
        for rule in rm.GENERIC_REASON_MORPHOLOGY.rules:
            up = rule.substring.upper()
            for bad in forbidden:
                assert bad not in up, f"generic morphology names host token {bad!r}: {rule}"

    def test_first_match_wins_is_order_sensitive(self):
        # Precedence is encoded by order, and reordering changes the verdict — the
        # auditable judgment docs/105 §3.2 calls out.
        a = rm.MorphologyRuleset((
            rm.MorphologyRule("INFLIGHT", "STALE_CLAIM"),
            rm.MorphologyRule("GATE", "OPERATOR_GATE"),
        ))
        b = rm.MorphologyRuleset((
            rm.MorphologyRule("GATE", "OPERATOR_GATE"),
            rm.MorphologyRule("INFLIGHT", "STALE_CLAIM"),
        ))
        tok = "LANE_ALL_SHIPPED_INFLIGHT_OR_SOAK_GATED"
        assert a.classify(tok) == ("STALE_CLAIM", "INFLIGHT")
        assert b.classify(tok) == ("OPERATOR_GATE", "GATE")

    def test_no_match_and_empty_return_none(self):
        assert rm.GENERIC_REASON_MORPHOLOGY.classify("OPAQUE_GIBBERISH_TOKEN_XYZ") is None
        assert rm.GENERIC_REASON_MORPHOLOGY.classify("") is None
        assert rm.GENERIC_REASON_MORPHOLOGY.classify(None) is None

    def test_malformed_rules_raise(self):
        with pytest.raises(ValueError):
            rm.MorphologyRuleset((rm.MorphologyRule("", "TRUE_DRAIN"),))   # empty substring
        with pytest.raises(ValueError):
            rm.MorphologyRuleset((rm.MorphologyRule("X", "NOT_A_CATEGORY"),))  # bad category

    def test_from_list_accepts_table_and_pair_forms(self):
        rs = rm.MorphologyRuleset.from_list([
            {"substring": "RESPAWN", "category": "TRUE_DRAIN"},
            ["MESH", "OPERATOR_GATE"],
        ])
        assert rs.classify("APPLY_LANE_POST_UNSTICK_STOP_RESPAWN") == ("TRUE_DRAIN", "RESPAWN")
        assert rs.classify("APPLY_LANE_BLOCKED_MESH") == ("OPERATOR_GATE", "MESH")

    def test_from_list_rejects_malformed_entry(self):
        with pytest.raises(ValueError):
            rm.MorphologyRuleset.from_list([{"substring": "X"}])          # missing category
        with pytest.raises(ValueError):
            rm.MorphologyRuleset.from_list(["not_a_pair"])                 # wrong shape


class TestReasonClassRecognizerLadder:
    """The three-rung resolver (`resolve_cause_with_source`, docs/105): exact →
    morphological → none, each NAMING the rung it answered on. Uses the
    process-active config (restored by the fixture) since rungs 1b/2 read it."""

    def test_exact_rung_wins_and_reports_exact(self, restore_active_config):
        _config.set_active(_config.default_config())
        # A built-in alias (DRAIN) resolves on the exact rung.
        cause, src, matched = picker_oracle.resolve_cause_with_source("DRAIN")
        assert cause is picker_oracle.NoPickCause.TRUE_DRAIN
        assert src == picker_oracle.CAUSE_SOURCE_EXACT

    def test_morphological_rung_classifies_compound_token(self, restore_active_config):
        _config.set_active(_config.default_config())
        # An LLM compound the exact rungs miss, but whose shape is legible.
        cause, src, matched = picker_oracle.resolve_cause_with_source(
            "PLAN_ID_COLLISION_FALSE_SHIPPED"
        )
        assert cause is picker_oracle.NoPickCause.STALE_CLAIM
        assert src == picker_oracle.CAUSE_SOURCE_MORPHOLOGICAL
        assert matched == "FALSE_SHIPPED"

    def test_none_rung_for_genuinely_opaque_token(self, restore_active_config):
        _config.set_active(_config.default_config())
        cause, src, matched = picker_oracle.resolve_cause_with_source(
            "APPLY_LANE_BLOCKED_MESH"  # no generic shape matches → honest floor
        )
        assert cause is picker_oracle.NoPickCause.UNCLASSIFIED
        assert src == picker_oracle.CAUSE_SOURCE_NONE
        assert matched == ""

    def test_exact_rung_precedes_morphological(self, restore_active_config):
        # A token that BOTH an exact alias and a morphology rule could match must
        # resolve on the exact rung (higher authority). `SOAK_OPEN` is a legacy
        # alias (→ OPERATOR_GATE) AND contains the `SOAK` morphology substring;
        # the exact rung must win and report "exact".
        _config.set_active(_config.default_config())
        cause, src, _ = picker_oracle.resolve_cause_with_source("SOAK_OPEN")
        assert cause is picker_oracle.NoPickCause.OPERATOR_GATE
        assert src == picker_oracle.CAUSE_SOURCE_EXACT

    def test_classify_sets_cause_source_and_records_morphological_evidence(
        self, restore_active_config
    ):
        # End-to-end through `classify`: a NO-PICK whose reason_class only matches
        # the morphological rung is graded (not UNCLASSIFIED) AND the verdict
        # carries cause_source="morphological" with an auditable evidence line.
        _config.set_active(_config.default_config())
        v = picker_oracle.classify(
            run_ts="20260602T120000Z",
            verdict_env={
                "tag": "next-up-2026-06-02-9", "verdict": "WEDGE",
                "all_clear": False, "reason_class": "REGISTRY_FALSE_SHIPPED",
            },
            dispatch_env={},
            readme="",
            state={},
        )
        assert v.no_pick_cause is picker_oracle.NoPickCause.STALE_CLAIM
        assert v.cause_source == picker_oracle.CAUSE_SOURCE_MORPHOLOGICAL
        assert any("morphological rung" in e for e in v.evidence)
        # And it serializes the rung.
        assert v.to_dict()["cause_source"] == picker_oracle.CAUSE_SOURCE_MORPHOLOGICAL

    def test_back_compat_resolve_cause_returns_bare_cause(self, restore_active_config):
        _config.set_active(_config.default_config())
        r = picker_oracle.resolve_cause("REGISTRY_FALSE_SHIPPED")
        assert r is picker_oracle.NoPickCause.STALE_CLAIM


class TestReasonMorphologyConfigSeam:
    """The `dos.toml [[reasons.morphology]]` readback (docs/105) — override
    semantics, empty-list-disables, malformed-raises, mirroring `[stamp]`."""

    def _write_toml(self, tmp_path, body: str):
        (tmp_path / "dos.toml").write_text(body, encoding="utf-8")
        return tmp_path / "dos.toml"

    def test_absent_table_inherits_base(self, tmp_path):
        p = self._write_toml(tmp_path, '[stamp]\nstyle="grep"\n')
        out = rm.load_from_toml(p, base=rm.GENERIC_REASON_MORPHOLOGY)
        assert out is rm.GENERIC_REASON_MORPHOLOGY

    def test_declared_list_overrides_base(self, tmp_path):
        p = self._write_toml(tmp_path, (
            '[[reasons.morphology]]\nsubstring="MESH"\ncategory="OPERATOR_GATE"\n'
        ))
        out = rm.load_from_toml(p, base=rm.GENERIC_REASON_MORPHOLOGY)
        # Override, not merge: exactly the one declared rule.
        assert len(out.rules) == 1
        assert out.classify("APPLY_LANE_BLOCKED_MESH") == ("OPERATOR_GATE", "MESH")
        # And the generic shapes are GONE (override semantics).
        assert out.classify("PLAN_ID_COLLISION_FALSE_SHIPPED") is None

    def test_empty_list_disables_rung_two(self, tmp_path):
        p = self._write_toml(tmp_path, "reasons.morphology = []\n")
        out = rm.load_from_toml(p, base=rm.GENERIC_REASON_MORPHOLOGY)
        assert out is rm.NO_REASON_MORPHOLOGY
        assert out.classify("PLAN_ID_COLLISION_FALSE_SHIPPED") is None

    def test_malformed_rule_raises(self, tmp_path):
        p = self._write_toml(tmp_path, (
            '[[reasons.morphology]]\nsubstring="X"\ncategory="NOT_A_CATEGORY"\n'
        ))
        with pytest.raises(ValueError):
            rm.load_from_toml(p, base=rm.GENERIC_REASON_MORPHOLOGY)

    def test_load_workspace_config_wires_morphology(self, tmp_path):
        # The full config seam: a declared [[reasons.morphology]] lands on
        # SubstrateConfig.reason_morphology.
        (tmp_path / "dos.toml").write_text(
            '[[reasons.morphology]]\nsubstring="RESPAWN"\ncategory="TRUE_DRAIN"\n',
            encoding="utf-8",
        )
        cfg = _config.load_workspace_config(workspace=tmp_path)
        assert cfg.reason_morphology.classify("X_RESPAWN_Y") == ("TRUE_DRAIN", "RESPAWN")
