"""Tests for the operator-decision queue (`dos.decisions` + `dos.decisions_tui`).

The queue is a read-only projection over four on-disk sources (lane-journal
`OP_REFUSE`, verdict envelopes, preflight refusals, soak gates), joined and
ranked at call-time, with the detail/action text projected from the
`ReasonRegistry`. These tests pin:

  * each source reader lifts the right `Decision` (kind, lane, token, run_id);
  * the LLM-as-judge axis: `resolver_kind` is derived correctly (HUMAN /
    ORACLE / JUDGE) and the default `resolver="HUMAN"` filter narrows the queue;
  * `next_steps` emits the right action commands, incl. the `[j]` judge action
    only for JUDGE-resolvable rows;
  * ranking (refusals before soak gates, oldest-first within a kind);
  * the TUI's curses-unavailable fallback prints the plain list and exits 0
    (the Windows-without-windows-curses floor) and the curses event loop maps
    keys to the right emitted command.

All pure — no real git, no real terminal (curses is faked / forced absent).
"""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest

import datetime as _dt

from dos import decisions as D
from dos import decisions_tui as T
from dos.config import default_config


# The seeded fixtures all stamp 2026-05-31 / 2026-06-02 timestamps. `collect_decisions`
# now applies a recency filter against the wall clock (job finding #476 — drop
# refusals older than retention.journal_max_age_days, default 30d), so without a
# frozen clock these tests would silently start returning 0 decisions once real
# "today" drifts >30 days past the seeds. Freeze `_now` near the seeds so every
# test stays date-stable forever; the recency-filter behaviour itself is covered
# explicitly in TestRecencyFilter with its own seeded ages.
@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    frozen = _dt.datetime(2026, 6, 2, 12, 0, 0, tzinfo=_dt.timezone.utc)
    monkeypatch.setattr(D, "_now", lambda: frozen)
    return frozen


# ---------------------------------------------------------------------------
# Fixtures — seed the four sources under a tmp workspace.
# ---------------------------------------------------------------------------


def _seed_arbiter_refuse(cfg, *, lane="tailor", token="LANE_BLOCKED_ON_SOAK_GATED_PHASES",
                         ts="2026-05-31T10:00:00Z", run_id="RID-abc", seq=1):
    lj = cfg.paths.lane_journal
    lj.parent.mkdir(parents=True, exist_ok=True)
    with lj.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "op": "REFUSE", "seq": seq, "ts": ts, "lane": lane,
            "reason": "every concurrency-free lane has 0 pickable phases",
            "reason_class": token, "run_id": run_id,
        }) + "\n")


def _seed_acquire_refused(cfg, *, lane="lane-01", seq=1, ts="2026-05-31T10:00:00Z",
                          reason="REFUSED: lane 'lane-01' is already held by a live loop",
                          run_id="RID-acq", nested=False):
    """Append an ACQUIRE row that is REALLY a refusal (docs/139 — a mislabeled op).

    `nested=True` puts the REFUSED reason on the nested `lease.reason` instead of
    the top-level `reason`, the other shape a writer leaves it in.
    """
    lj = cfg.paths.lane_journal
    lj.parent.mkdir(parents=True, exist_ok=True)
    row = {"op": "ACQUIRE", "seq": seq, "ts": ts, "lane": lane, "run_id": run_id}
    if nested:
        row["lease"] = {"reason": reason, "lane": lane}
    else:
        row["reason"] = reason
    with lj.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _seed_acquire_granted(cfg, *, lane="lane-09", seq=1, ts="2026-05-31T10:00:00Z"):
    """Append a GENUINE successful ACQUIRE — must NOT become a decision."""
    lj = cfg.paths.lane_journal
    lj.parent.mkdir(parents=True, exist_ok=True)
    with lj.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "op": "ACQUIRE", "seq": seq, "ts": ts, "lane": lane,
            "reason": "lane-lease:worker-7", "lease": {"holder": "worker-7", "lane": lane},
        }) + "\n")


def _seed_refuse_blocked_by(cfg, *, lane="TF", blocking_lane="AAF", seq=1,
                            ts="2026-05-31T10:00:00Z",
                            reason="lane 'TF' cannot share live lane 'AAF': exact-glob overlap"):
    """A REFUSE that carries a structured `blocking_trees` naming the colliding lane.

    This is the real shape the kernel arbiter writes (`blocking_trees` keyed by the
    live lane that blocked the request). `reason_class` is prose here (as the host
    writes it), so it stays in reason_text, not the token.
    """
    lj = cfg.paths.lane_journal
    lj.parent.mkdir(parents=True, exist_ok=True)
    with lj.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "op": "REFUSE", "seq": seq, "ts": ts, "lane": lane,
            "reason_class": reason,
            "blocking_trees": {blocking_lane: ["agents/shared.py"]},
        }) + "\n")


def _seed_refuse_already_held(cfg, *, lane="replan", seq=1, ts="2026-05-31T10:00:00Z"):
    """A self/already-held REFUSE — no other lane named; resolved when L frees."""
    lj = cfg.paths.lane_journal
    lj.parent.mkdir(parents=True, exist_ok=True)
    with lj.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "op": "REFUSE", "seq": seq, "ts": ts, "lane": lane,
            "reason_class": f"lane '{lane}' is already held by a live loop — pick a different --lane or wait.",
        }) + "\n")


def _seed_refuse_prose(cfg, *, lane, prose, seq=1, ts="2026-05-31T10:00:00Z",
                       blocking_lane=None):
    """A REFUSE whose prose is in BOTH `reason` and `reason_class` (the real host
    shape — the host writes the same sentence to both). Used to test the
    backpressure classifier, which reads `reason` (=reason_text)."""
    lj = cfg.paths.lane_journal
    lj.parent.mkdir(parents=True, exist_ok=True)
    row = {"op": "REFUSE", "seq": seq, "ts": ts, "lane": lane,
           "reason": prose, "reason_class": prose}
    if blocking_lane:
        row["blocking_trees"] = {blocking_lane: ["agents/shared.py"]}
    with lj.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _seed_release(cfg, *, lane, seq, ts="2026-05-31T11:00:00Z", reason="explicit"):
    lj = cfg.paths.lane_journal
    lj.parent.mkdir(parents=True, exist_ok=True)
    with lj.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "op": "RELEASE", "seq": seq, "ts": ts, "lane": lane, "reason": reason,
        }) + "\n")


def _seed_scavenge(cfg, *, lane, seq, ts="2026-05-31T11:00:00Z", reason="dead_for_reclaim"):
    lj = cfg.paths.lane_journal
    lj.parent.mkdir(parents=True, exist_ok=True)
    with lj.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "op": "SCAVENGE", "seq": seq, "ts": ts, "lane": lane, "reason": reason,
        }) + "\n")


def _seed_verdict_envelope(cfg, *, tag="next-up-2026-05-31-1", verdict="WEDGE",
                           token="LANE_ALL_INFLIGHT_OR_DEFERRED", lane="apply",
                           generated_at="2026-05-31T11:42:00Z", run_id="RID-def",
                           run_ts="", do_not_render=False):
    nd = cfg.paths.next_packets
    nd.mkdir(parents=True, exist_ok=True)
    env = {
        "tag": tag, "verdict": verdict, "all_clear": False,
        "reason_class": token, "reason": "remaining phases all soft-claimed",
        "scope": {"lane": lane}, "generated_at": generated_at, "run_id": run_id,
        "do_not_render": do_not_render,
    }
    if run_ts:
        env["run_ts"] = run_ts
    (nd / f".verdict-{tag}.json").write_text(json.dumps(env), encoding="utf-8")


def _seed_soak(cfg, *, phase="AR7", lane="tailor", soak_until="2099-01-01"):
    si = cfg.paths.soaks_index
    si.parent.mkdir(parents=True, exist_ok=True)
    si.write_text(
        "soaks:\n"
        f"  - phase: {phase}\n"
        f"    lane: {lane}\n"
        f"    soak_until: {soak_until}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# collect_decisions — the three readers + resolver + filter + ranking.
# ---------------------------------------------------------------------------


class TestCollectDecisions:
    def test_all_three_sources_lift_one_decision_each(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_arbiter_refuse(cfg)
        _seed_verdict_envelope(cfg)
        _seed_soak(cfg)
        rows = D.collect_decisions(cfg, resolver=None)
        assert len(rows) == 3
        kinds = sorted(d.kind.value for d in rows)
        assert kinds == ["ARBITER_REFUSE", "SOAK_GATE", "WEDGE"]

    def test_empty_workspace_is_empty_queue_no_crash(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        assert D.collect_decisions(cfg, resolver=None) == []

    def test_resolver_kind_derivation(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # OPERATOR_GATE category -> HUMAN
        _seed_arbiter_refuse(cfg, token="LANE_BLOCKED_ON_SOAK_GATED_PHASES")
        # STALE_CLAIM category -> ORACLE (picker_oracle can cross-check)
        _seed_verdict_envelope(cfg, token="LANE_ALL_INFLIGHT_OR_DEFERRED")
        _seed_soak(cfg)  # SOAK_GATE -> always HUMAN
        by_kind = {d.kind.value: d.resolver_kind.value
                   for d in D.collect_decisions(cfg, resolver=None)}
        assert by_kind["ARBITER_REFUSE"] == "HUMAN"
        assert by_kind["WEDGE"] == "ORACLE"
        assert by_kind["SOAK_GATE"] == "HUMAN"

    def test_misroute_token_is_judge_resolvable(self, tmp_path: Path):
        # A MISROUTE-category reason has no deterministic oracle owner -> JUDGE.
        cfg = default_config(tmp_path)
        _seed_verdict_envelope(cfg, token="MIS_ROUTED_FINDING", run_id="RID-j")
        rows = D.collect_decisions(cfg, resolver=None)
        assert len(rows) == 1
        assert rows[0].resolver_kind.value == "JUDGE"

    def test_human_filter_is_the_default_and_narrows(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_arbiter_refuse(cfg)                                   # HUMAN
        _seed_verdict_envelope(cfg, token="LANE_ALL_INFLIGHT_OR_DEFERRED")  # ORACLE
        _seed_soak(cfg)                                             # HUMAN
        human = D.collect_decisions(cfg)  # default resolver="HUMAN"
        assert len(human) == 2
        assert all(d.resolver_kind.value == "HUMAN" for d in human)
        assert len(D.collect_decisions(cfg, resolver=None)) == 3

    def test_ranking_refusals_before_soak_then_oldest_first(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # Two arbiter refusals, the older one second in file order.
        _seed_arbiter_refuse(cfg, lane="apply", ts="2026-05-31T12:00:00Z", run_id="R-new", seq=1)
        _seed_arbiter_refuse(cfg, lane="tailor", ts="2026-05-31T08:00:00Z", run_id="R-old", seq=2)
        _seed_soak(cfg)
        rows = D.collect_decisions(cfg, resolver=None)
        # Soak gate sorts last (kind precedence).
        assert rows[-1].kind.value == "SOAK_GATE"
        # Within ARBITER_REFUSE, the older (08:00) outranks the newer (12:00).
        arb = [d for d in rows if d.kind.value == "ARBITER_REFUSE"]
        assert arb[0].run_id == "R-old"
        assert arb[1].run_id == "R-new"

    def test_do_not_render_envelope_is_preflight_refuse(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_verdict_envelope(cfg, do_not_render=True)
        rows = D.collect_decisions(cfg, resolver=None)
        assert len(rows) == 1
        assert rows[0].kind.value == "PREFLIGHT_REFUSE"

    def test_closed_soak_window_is_not_pending(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_soak(cfg, soak_until="2000-01-01")  # long past
        assert D.collect_decisions(cfg, resolver=None) == []

    def test_live_envelope_is_not_a_decision(self, tmp_path: Path):
        # An all_clear / LIVE envelope is a real packet, not a refusal.
        cfg = default_config(tmp_path)
        nd = cfg.paths.next_packets
        nd.mkdir(parents=True, exist_ok=True)
        (nd / ".verdict-live.json").write_text(json.dumps({
            "tag": "live", "verdict": "LIVE", "all_clear": True, "picks": [{}],
        }), encoding="utf-8")
        assert D.collect_decisions(cfg, resolver=None) == []


# ---------------------------------------------------------------------------
# Acquire-refusal recovery — the reader-side defense (docs/139). A refusal a
# writer mislabeled as `op:ACQUIRE` (reason `REFUSED: …`) must STILL surface as a
# degraded ARBITER_REFUSE, or it is invisible to the operator. A GENUINE acquire
# must NOT become a decision (the queue is "what needs me", not a lease log).
# ---------------------------------------------------------------------------


class TestAcquireRefusalRecovery:
    def test_helper_admits_top_level_refused_reason(self):
        assert D._acquire_refusal_reason(
            {"op": "ACQUIRE", "reason": "REFUSED: lane 'x' is held"}
        ) == "REFUSED: lane 'x' is held"

    def test_helper_admits_nested_lease_refused_reason(self):
        assert D._acquire_refusal_reason(
            {"op": "ACQUIRE", "lease": {"reason": "REFUSED: held"}}
        ) == "REFUSED: held"

    def test_helper_is_case_insensitive_and_tolerates_whitespace(self):
        assert D._acquire_refusal_reason({"reason": "  refused: nope"}) == "  refused: nope"

    def test_helper_rejects_a_genuine_acquire(self):
        # A normal grant reason is NOT a refusal — returns "" (not surfaced).
        assert D._acquire_refusal_reason({"reason": "lane-lease:worker-7"}) == ""
        assert D._acquire_refusal_reason({"reason": ""}) == ""
        assert D._acquire_refusal_reason({}) == ""
        # "REFUSED" must be at the START — a reason merely MENTIONING it is not one.
        assert D._acquire_refusal_reason({"reason": "granted; not REFUSED"}) == ""

    def test_mislabeled_acquire_surfaces_as_arbiter_refuse(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_acquire_refused(cfg, lane="lane-01")
        rows = D.collect_decisions(cfg, resolver=None)
        assert len(rows) == 1
        d = rows[0]
        assert d.kind.value == "ARBITER_REFUSE"
        assert d.lane == "lane-01"
        assert d.reason_text.startswith("REFUSED:")
        # The evidence MARKS it recovered so an operator can tell it from a clean
        # OP_REFUSE (the docs/139 provenance).
        assert any("recovered" in ev for ev in d.evidence)

    def test_nested_lease_reason_also_recovers(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_acquire_refused(cfg, lane="lane-02", nested=True)
        rows = D.collect_decisions(cfg, resolver=None)
        assert len(rows) == 1
        assert rows[0].kind.value == "ARBITER_REFUSE"
        assert rows[0].lane == "lane-02"

    def test_genuine_acquire_is_not_a_decision(self, tmp_path: Path):
        # The load-bearing negative: a successful lease must NOT flood the queue.
        cfg = default_config(tmp_path)
        _seed_acquire_granted(cfg, lane="lane-09")
        assert D.collect_decisions(cfg, resolver=None) == []

    def test_recovered_refusals_dedup_to_distinct_decisions(self, tmp_path: Path):
        """The docs/139 shape in miniature: N hidden refusals → distinct rows + count.

        Mirrors the live benchmark (4,505 raw → 21 distinct, sum(dup_count)==4,505):
        many identical mislabeled-ACQUIRE refusals collapse to ONE decision whose
        `dup_count` recovers the full hidden total.
        """
        cfg = default_config(tmp_path)
        for seq in range(1, 6):  # five identical hidden refusals on lane-01
            _seed_acquire_refused(cfg, lane="lane-01", seq=seq)
        for seq in range(6, 9):  # three on lane-02
            _seed_acquire_refused(
                cfg, lane="lane-02", seq=seq,
                reason="REFUSED: lane 'lane-02' is already held by a live loop")
        rows = D.collect_decisions(cfg, resolver=None)
        assert len(rows) == 2                                  # two DISTINCT decisions
        assert sum(getattr(d, "dup_count", 1) for d in rows) == 8  # all 8 recovered
        by_lane = {d.lane: getattr(d, "dup_count", 1) for d in rows}
        assert by_lane == {"lane-01": 5, "lane-02": 3}

    def test_recovered_refusal_does_not_shadow_a_real_op_refuse(self, tmp_path: Path):
        # A clean OP_REFUSE and a mislabeled ACQUIRE-refusal coexist as two rows.
        cfg = default_config(tmp_path)
        _seed_arbiter_refuse(cfg, lane="tailor", seq=1)            # clean OP_REFUSE
        _seed_acquire_refused(cfg, lane="lane-01", seq=2)          # mislabeled
        rows = D.collect_decisions(cfg, resolver=None)
        assert len(rows) == 2
        assert {d.lane for d in rows} == {"tailor", "lane-01"}
        # Exactly one carries the recovered marker.
        recovered = [d for d in rows if any("recovered" in ev for ev in d.evidence)]
        assert len(recovered) == 1
        assert recovered[0].lane == "lane-01"


# ---------------------------------------------------------------------------
# Token hygiene — a `reason_class` that is PROSE (a host wrote a whole sentence
# where a closed token belongs) must NOT become a `reason_token`, or `next_steps`
# emits garbage (`dos man wedge EVERY CONCURRENCY-FREE LANE …`). The prose stays
# in `reason_text`; the token is "".
# ---------------------------------------------------------------------------


class TestTokenHygiene:
    def test_clean_token_admits_registry_member_any_case(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # A real registry token, lower-cased, is admitted (and upper-normalized).
        assert D._clean_token("lane_blocked_on_soak_gated_phases", cfg) == \
            "LANE_BLOCKED_ON_SOAK_GATED_PHASES"

    def test_clean_token_admits_clean_upper_snake_shape(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # A not-yet-declared but token-SHAPED value is admitted (forward-compat).
        assert D._clean_token("BRAND_NEW_TOKEN_42", cfg) == "BRAND_NEW_TOKEN_42"

    def test_clean_token_rejects_prose(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        prose = "every concurrency-free lane on the priority ladder has 0 pickable phases"
        assert D._clean_token(prose, cfg) == ""
        # An UPPER-cased sentence (still has spaces) is also rejected.
        assert D._clean_token(prose.upper(), cfg) == ""

    def test_clean_token_rejects_empty_and_none(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        assert D._clean_token("", cfg) == ""
        assert D._clean_token(None, cfg) == ""
        assert D._clean_token("   ", cfg) == ""

    def test_prose_reason_class_yields_empty_token_in_journal_row(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # Seed an OP_REFUSE whose reason_class is a prose sentence (the real bug).
        _seed_arbiter_refuse(
            cfg, token="EVERY CONCURRENCY-FREE LANE HAS 0 PICKABLE PHASES")
        rows = D.collect_decisions(cfg, resolver=None)
        assert len(rows) == 1
        d = rows[0]
        assert d.reason_token == ""               # prose did NOT become a token
        # The prose still shows (it's the `reason` field) and no broken man/judge
        # step is emitted (no token → no `m`/`j` action).
        steps = D.next_steps(d, cfg)
        assert not any("man wedge" in c for _, c in steps)
        assert not any("llm_judge" in c for _, c in steps)

    def test_prose_reason_class_yields_empty_token_in_envelope_row(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_verdict_envelope(
            cfg, token="REMAINING PHASES ALL SOFT-CLAIMED RIGHT NOW")
        rows = D.collect_decisions(cfg, resolver=None)
        assert len(rows) == 1
        assert rows[0].reason_token == ""
        assert not any("man wedge" in c for _, c in D.next_steps(rows[0], cfg))


# ---------------------------------------------------------------------------
# Dynamic lane handle — a curated-cluster scope string is a pre-dos/119 relic; the
# reader must normalize it to its bare dynamic handle so the surfaced `/replan
# --scope <lane>` action is resolvable (job finding 2026-06-08: the 8-member
# "apply cluster (AFR, ALO, …)" row with a broken [r]replan).
# ---------------------------------------------------------------------------


class TestDynamicLaneHandle:
    def test_strips_cluster_member_list_to_handle(self):
        assert D._dynamic_lane_handle(
            "apply cluster (AFR, ALO, ANC, APC, CHR, LF, MLP, TFO)") == "apply"

    def test_strips_bare_paren_member_list(self):
        assert D._dynamic_lane_handle("apply (AFR, ALO)") == "apply"

    def test_strips_slash_path_to_last_segment(self):
        assert D._dynamic_lane_handle("a/b/apply") == "apply"

    def test_bare_handle_round_trips(self):
        assert D._dynamic_lane_handle("apply") == "apply"
        assert D._dynamic_lane_handle("tailor") == "tailor"

    def test_empty_and_whitespace_yield_empty(self):
        assert D._dynamic_lane_handle("") == ""
        assert D._dynamic_lane_handle("   ") == ""
        assert D._dynamic_lane_handle(None) == ""  # type: ignore[arg-type]

    def test_cluster_word_is_case_insensitive(self):
        assert D._dynamic_lane_handle("apply CLUSTER (X, Y)") == "apply"

    def test_envelope_cluster_scope_yields_resolvable_replan_action(self, tmp_path: Path):
        """The end-to-end fix: a verdict envelope carrying the curated-cluster scope
        string surfaces a `Decision` whose lane is the bare handle, so its
        `/replan --scope apply` action is resolvable — not the broken
        `/replan --scope apply cluster (AFR, …)`."""
        cfg = default_config(tmp_path)
        nd = cfg.paths.next_packets
        nd.mkdir(parents=True, exist_ok=True)
        env = {
            "tag": "next-up-2026-06-01-9",
            "verdict": "DRAIN",
            "all_clear": False,
            "scope": "apply cluster (AFR, ALO, ANC, APC, CHR, LF, MLP, TFO)",
            "generated_at": "2026-06-01T04:18:58Z",  # within the frozen-now 30d window
        }
        (nd / ".verdict-next-up-2026-06-01-9.json").write_text(
            json.dumps(env), encoding="utf-8")
        rows = D.collect_decisions(cfg, resolver=None)
        assert len(rows) == 1
        d = rows[0]
        assert d.lane == "apply"  # normalized to the bare dynamic handle
        # The [r]replan action is the resolvable bare-handle form.
        steps = dict((k, c) for k, c in D.next_steps(d, cfg))
        assert steps["r"] == "/replan --scope apply"
        # The curated-cluster relic string never reaches an action.
        assert not any("cluster (" in c for _, c in D.next_steps(d, cfg))


# ---------------------------------------------------------------------------
# Dedup — an append-only WAL re-records the same refusal every sweep, so N
# byte-identical rows must collapse to ONE carrying `dup_count=N`.
# ---------------------------------------------------------------------------


class TestDedup:
    def test_identical_refusals_collapse_with_count(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # Three identical OP_REFUSE entries (same lane/token/reason) — one decision.
        for seq in (1, 2, 3):
            _seed_arbiter_refuse(cfg, lane="apply", token="LANE_ALL_INFLIGHT_OR_DEFERRED",
                                 ts="2026-05-31T10:00:00Z", run_id="R", seq=seq)
        rows = D.collect_decisions(cfg, resolver=None)
        assert len(rows) == 1
        assert rows[0].dup_count == 3

    def test_distinct_lanes_do_not_collapse(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_arbiter_refuse(cfg, lane="apply", seq=1)
        _seed_arbiter_refuse(cfg, lane="tailor", seq=2)
        rows = D.collect_decisions(cfg, resolver=None)
        assert len(rows) == 2
        assert all(d.dup_count == 1 for d in rows)

    def test_dup_count_round_trips_through_to_dict(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        for seq in (1, 2):
            _seed_arbiter_refuse(cfg, lane="apply", token="LANE_ALL_INFLIGHT_OR_DEFERRED",
                                 ts="2026-05-31T10:00:00Z", seq=seq)
        rows = D.collect_decisions(cfg, resolver=None)
        assert rows[0].to_dict()["dup_count"] == 2

    def test_dup_count_renders_in_plain_list(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        for seq in (1, 2, 3, 4):
            _seed_arbiter_refuse(cfg, lane="apply", token="LANE_ALL_INFLIGHT_OR_DEFERRED",
                                 ts="2026-05-31T10:00:00Z", seq=seq)
        rows = D.collect_decisions(cfg, resolver=None)
        listing = D.render_list_plain(rows)
        assert "×4" in listing
        detail = D.render_detail_plain(rows[0], cfg)
        assert "4×" in detail

    def test_single_row_has_no_dup_marker(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_arbiter_refuse(cfg, lane="apply")
        rows = D.collect_decisions(cfg, resolver=None)
        assert rows[0].dup_count == 1
        assert "×" not in D.render_list_plain(rows)


# ---------------------------------------------------------------------------
# The LIVENESS source — a watchdog OP_HALT proposal surfaces as a queue row
# (docs/82 3b, docs/101 §4). Read-only, additive: a journal with no OP_HALT
# yields exactly today's decision set.
# ---------------------------------------------------------------------------


def _seed_op_halt(cfg, *, handle="4242", lane="api", run_id="RID-spin",
                  command="dos lease-lane release --lane api --owner w",
                  reason="watchdog: SPINNING (no forward delta)",
                  ts="2026-06-02T10:05:00Z", seq=1):
    lj = cfg.paths.lane_journal
    lj.parent.mkdir(parents=True, exist_ok=True)
    with lj.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "op": "HALT", "seq": seq, "ts": ts, "handle": handle, "lane": lane,
            "loop_ts": "2026-06-02T10:00:00Z", "run_id": run_id,
            "command": command, "reason": reason,
        }) + "\n")


class TestLivenessSource:
    def test_op_halt_surfaces_as_liveness_decision(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_op_halt(cfg)
        rows = D.collect_decisions(cfg, resolver=None)
        assert len(rows) == 1
        d = rows[0]
        assert d.kind.value == "LIVENESS"
        assert d.resolver_kind.value == "ORACLE"   # liveness is deterministic
        assert d.lane == "api"
        assert d.run_id == "RID-spin"
        assert d.proposed_command == "dos lease-lane release --lane api --owner w"
        assert d.handle == "4242"

    def test_liveness_next_steps_offer_the_proposed_command(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_op_halt(cfg)
        d = D.collect_decisions(cfg, resolver=None)[0]
        steps = dict(D.next_steps(d, cfg))
        # the paste-to-stop command is the [k] emit-and-exit action
        assert steps["k"] == "dos lease-lane release --lane api --owner w"
        assert steps["l"].startswith("#")          # a "let it ride" no-op

    def test_liveness_no_command_falls_back_to_handle(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_op_halt(cfg, command="")
        d = D.collect_decisions(cfg, resolver=None)[0]
        steps = dict(D.next_steps(d, cfg))
        # no host command → surface the opaque handle (naming no kill mechanism)
        assert "4242" in steps["k"]

    def test_liveness_is_hidden_by_default_human_filter(self, tmp_path: Path):
        # ORACLE-resolved → the default resolver="HUMAN" queue does NOT show it
        # (the accepted docs/101 §4 trade-off: --all / dos top surface it).
        cfg = default_config(tmp_path)
        _seed_op_halt(cfg)
        assert D.collect_decisions(cfg) == []                       # default HUMAN filter
        assert len(D.collect_decisions(cfg, resolver=None)) == 1    # --all shows it
        assert len(D.collect_decisions(cfg, resolver="ORACLE")) == 1

    def test_liveness_outranks_a_refusal(self, tmp_path: Path):
        # A run hung RIGHT NOW (burning budget) outranks a refusal (which only
        # blocks future work) — the _KIND_RANK precedence.
        cfg = default_config(tmp_path)
        _seed_arbiter_refuse(cfg)
        _seed_op_halt(cfg, seq=2)
        rows = D.collect_decisions(cfg, resolver=None)
        assert rows[0].kind.value == "LIVENESS"

    def test_no_op_halt_is_behavior_preserving(self, tmp_path: Path):
        # The new source is purely additive: a journal with only a REFUSE yields
        # exactly the pre-existing single ARBITER_REFUSE row, no LIVENESS row.
        cfg = default_config(tmp_path)
        _seed_arbiter_refuse(cfg)
        rows = D.collect_decisions(cfg, resolver=None)
        assert [d.kind.value for d in rows] == ["ARBITER_REFUSE"]


# ---------------------------------------------------------------------------
# next_steps — the action bar.
# ---------------------------------------------------------------------------


class TestNextSteps:
    def test_arbiter_refuse_offers_replan_and_force(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_arbiter_refuse(cfg, lane="tailor")
        d = D.collect_decisions(cfg, resolver=None)[0]
        steps = dict(D.next_steps(d, cfg))
        assert steps["r"] == "/replan --scope tailor"
        assert steps["f"] == "dos arbitrate --lane tailor --force"

    def test_judge_action_with_run_ts_routes_to_deterministic_judge(self, tmp_path: Path):
        # A JUDGE decision that knows its chained-run run_ts routes to the
        # DETERMINISTIC `dos judge` (picker_oracle keys on run_ts).
        cfg = default_config(tmp_path)
        _seed_verdict_envelope(cfg, token="MIS_ROUTED_FINDING",
                               run_ts="20260531T010000Z")
        d = D.collect_decisions(cfg, resolver=None)[0]
        assert d.resolver_kind.value == "JUDGE"
        assert dict(D.next_steps(d, cfg))["j"] == "dos judge wedge 20260531T010000Z"

    def test_judge_action_without_run_ts_routes_to_llm_driver(self, tmp_path: Path):
        # Without a run_ts (a bare verdict envelope), the deterministic judge has
        # nothing to classify, so [j] routes to the LLM driver outside the kernel.
        cfg = default_config(tmp_path)
        _seed_verdict_envelope(cfg, token="MIS_ROUTED_FINDING", run_id="RID-j")
        d = D.collect_decisions(cfg, resolver=None)[0]
        assert d.resolver_kind.value == "JUDGE"
        assert dict(D.next_steps(d, cfg))["j"] == "python -m dos.drivers.llm_judge RID-j"

    def test_non_judge_decision_has_no_judge_action(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_arbiter_refuse(cfg)
        d = D.collect_decisions(cfg, resolver=None)[0]
        assert "j" not in dict(D.next_steps(d, cfg))


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------


class TestRendering:
    def test_plain_list_shows_count_and_columns(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_arbiter_refuse(cfg)
        out = D.render_list_plain(D.collect_decisions(cfg, resolver=None))
        assert "operator decisions" in out
        assert "ARBITER_REFUSE" in out
        assert "1 pending" in out

    def test_empty_list_says_none(self, tmp_path: Path):
        assert "none pending" in D.render_list_plain([])

    def test_detail_projects_reason_spec(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_verdict_envelope(cfg, token="LANE_ALL_INFLIGHT_OR_DEFERRED")
        d = D.collect_decisions(cfg, resolver=None)[0]
        detail = D.render_detail_plain(d, cfg)
        # The ReasonSpec summary + fix are projected from the registry.
        assert "MEANS" in detail
        assert "soft-claimed" in detail
        assert "TYPICAL FIX" in detail
        assert "ACTIONS" in detail


# ---------------------------------------------------------------------------
# Urgency tiers + inline action hints — the at-a-glance triage helpers the TUI
# reads. Pure: built from bare Decision values, no seeding/curses needed.
# ---------------------------------------------------------------------------


def _decision(kind, **kw):
    """A minimal Decision for the pure-helper tests."""
    base = dict(
        kind=kind,
        resolver_kind=D.ResolverKind.HUMAN,
        lane="src",
        reason_token="",
        reason_text="something happened",
        run_id="",
        age_seconds=120,
        source_path="x",
    )
    base.update(kw)
    return D.Decision(**base)


class TestUrgencyAndHints:
    def test_urgency_tier_tracks_kind_rank(self):
        # rank 0–1 → NOW, 2–3 → SOON, 4+ → LATER (anchored on _KIND_RANK).
        assert D.urgency_of(_decision(D.DecisionKind.LIVENESS)) is D.Urgency.NOW
        assert D.urgency_of(_decision(D.DecisionKind.ARBITER_REFUSE)) is D.Urgency.NOW
        assert D.urgency_of(_decision(D.DecisionKind.PREFLIGHT_REFUSE)) is D.Urgency.SOON
        assert D.urgency_of(_decision(D.DecisionKind.WEDGE)) is D.Urgency.SOON
        assert D.urgency_of(_decision(D.DecisionKind.SOAK_GATE)) is D.Urgency.LATER

    def test_urgency_tier_is_consistent_with_sort_order(self):
        # The whole point: the colour the eye reads cannot disagree with the
        # order the queue sorts by. A NOW row must never rank below a LATER row.
        for kind in D.DecisionKind:
            rank = D._KIND_RANK[kind]
            tier = D.urgency_of(_decision(kind))
            order = {D.Urgency.NOW: 0, D.Urgency.SOON: 1, D.Urgency.LATER: 2}[tier]
            # rank and tier-order must be monotone together
            assert (rank <= 1) == (order == 0)
            assert (rank >= 4) == (order == 2)

    def test_glyph_has_unicode_and_ascii_forms(self):
        liv = _decision(D.DecisionKind.LIVENESS)
        soak = _decision(D.DecisionKind.SOAK_GATE)
        assert D.urgency_glyph(liv) == "●"
        assert D.urgency_glyph(soak) == "·"
        assert D.urgency_glyph(liv, ascii_only=True) == "!"
        assert D.urgency_glyph(soak, ascii_only=True) == "."

    def test_tally_splits_now_vs_later(self):
        rows = [
            _decision(D.DecisionKind.LIVENESS),
            _decision(D.DecisionKind.ARBITER_REFUSE),
            _decision(D.DecisionKind.SOAK_GATE),
        ]
        assert D.urgency_tally(rows) == "2 now · 1 later"

    def test_tally_now_only_and_empty(self):
        assert D.urgency_tally([_decision(D.DecisionKind.LIVENESS)]) == "1 now"
        assert D.urgency_tally([]) == ""
        assert D.urgency_tally([_decision(D.DecisionKind.SOAK_GATE)]) == "1 later"

    def test_action_hints_drop_copy_and_ride(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # An arbiter refusal offers replan + force; copy is dropped from the hint.
        arb = _decision(D.DecisionKind.ARBITER_REFUSE, lane="docs")
        hints = dict(D.action_hints(arb, cfg))
        assert "c" not in hints  # copy carries no triage signal
        assert hints.get("r") == "replan"
        assert hints.get("f") == "force"

    def test_action_hints_for_liveness_lead_with_stop(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        liv = _decision(
            D.DecisionKind.LIVENESS,
            reason_text="run spinning 18m",
            proposed_command="dos halt --handle 99",
        )
        # The first hint is the stop, and the no-op 'l' (let it ride) is dropped.
        hints = D.action_hints(liv, cfg)
        assert hints[0] == ("k", "stop")
        assert "l" not in dict(hints)

    def test_fmt_action_hints_caps_to_limit(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        arb = _decision(D.DecisionKind.ARBITER_REFUSE, lane="docs")
        # limit=1 shows only the first action.
        assert D.fmt_action_hints(arb, cfg, limit=1) == "[r]replan"
        assert D.fmt_action_hints(arb, cfg, limit=2) == "[r]replan [f]force"

    def test_plain_list_leads_with_glyph_and_tally_and_hint(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_op_halt(cfg, lane="api")
        out = D.render_list_plain(D.collect_decisions(cfg, resolver=None))
        # The list now carries the urgency tally, an ascii glyph column, and the
        # inline action hint — the three at-a-glance triage cues.
        assert "now" in out  # the tally word
        assert "!" in out    # the NOW glyph (LIVENESS row)
        assert "[k]stop" in out

    def test_footer_keys_show_full_set_including_ride_and_copy(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        liv = _decision(
            D.DecisionKind.LIVENESS,
            reason_text="run spinning 18m",
            proposed_command="dos halt --handle 99",
        )
        foot = D.footer_keys(liv, cfg)
        # The footer (one focused row) shows the no-signal keys the dense row
        # hint hides: let-it-ride and copy.
        assert "[k]stop" in foot
        assert "[l]ride" in foot
        assert "[c]copy" in foot


# ---------------------------------------------------------------------------
# TUI — fallback + event loop (no real terminal).
# ---------------------------------------------------------------------------


class _FakeCurses:
    KEY_DOWN = 258
    KEY_UP = 259
    KEY_RESIZE = 410
    A_REVERSE = 1
    A_BOLD = 2
    A_DIM = 4
    COLOR_RED = 1
    COLOR_YELLOW = 3
    COLOR_WHITE = 7
    error = Exception  # the real curses.error; we just need an exception class

    def curs_set(self, *a):
        pass

    # --- colour API the urgency tiers use (faithful enough to exercise it) ---
    def has_colors(self):
        return True

    def start_color(self):
        pass

    def use_default_colors(self):
        pass

    def init_pair(self, *a):
        pass

    def color_pair(self, n):
        return 1 << (n + 3)  # any distinct nonzero attr bits per pair


class _FakeScr:
    def __init__(self, keys):
        self._keys = list(keys)

    def keypad(self, *a):
        pass

    def erase(self):
        pass

    def getmaxyx(self):
        return (40, 120)

    def addnstr(self, *a):
        pass

    def refresh(self):
        pass

    def getch(self):
        return self._keys.pop(0)


class TestTuiFallback:
    def test_curses_unavailable_falls_through_to_plain_list(
        self, tmp_path: Path, capsys, monkeypatch
    ):
        cfg = default_config(tmp_path)
        _seed_arbiter_refuse(cfg)

        real_import = builtins.__import__

        def _no_curses(name, *a, **k):
            if name == "curses":
                raise ImportError("simulated: no curses on this box")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _no_curses)
        rc = T.run_tui(cfg, resolver="HUMAN")
        assert rc == 0
        out = capsys.readouterr().out
        assert "operator decisions" in out
        assert "ARBITER_REFUSE" in out


class TestTuiEventLoop:
    def _cfg_with_wedge(self, tmp_path):
        cfg = default_config(tmp_path)
        _seed_verdict_envelope(cfg, token="LANE_ALL_INFLIGHT_OR_DEFERRED", lane="apply")
        return cfg

    def test_press_replan_emits_replan_command(self, tmp_path: Path):
        cfg = self._cfg_with_wedge(tmp_path)
        res = T._main_loop(_FakeScr([ord("r")]), _FakeCurses(), cfg, None)
        assert res == ("r", "/replan --scope apply")

    def test_quit_keys_return_none(self, tmp_path: Path):
        cfg = self._cfg_with_wedge(tmp_path)
        assert T._main_loop(_FakeScr([ord("q")]), _FakeCurses(), cfg, None) is None
        assert T._main_loop(_FakeScr([27]), _FakeCurses(), cfg, None) is None  # Esc

    def test_down_is_clamped_and_action_resolves_on_selected(self, tmp_path: Path):
        cfg = self._cfg_with_wedge(tmp_path)
        # Only one row; two KEY_DOWNs clamp to it, then 'm' emits its man command.
        res = T._main_loop(_FakeScr([258, 258, ord("m")]), _FakeCurses(), cfg, None)
        assert res == ("m", "dos man wedge LANE_ALL_INFLIGHT_OR_DEFERRED")

    def test_unknown_key_is_ignored_then_quit(self, tmp_path: Path):
        cfg = self._cfg_with_wedge(tmp_path)
        # 'z' is not an action key -> ignored; then 'q' quits.
        res = T._main_loop(_FakeScr([ord("z"), ord("q")]), _FakeCurses(), cfg, None)
        assert res is None


# ---------------------------------------------------------------------------
# Recency filter (job finding #476) — stale / un-ageable refusals are dropped.
# ---------------------------------------------------------------------------


import dataclasses as _dataclasses  # noqa: E402


class TestRecencyFilter:
    """`collect_decisions` drops point-in-time refusals past the retention cutoff.

    The bug: the read surfaced EVERY `.verdict-*.json` on disk as pending, with no
    recency bound — a WEDGE resolved weeks ago, and especially the common envelope
    with NO `generated_at` (age None), showed forever. The fix uses
    `retention.journal_max_age_days` (default 30) as the cutoff and treats an
    un-timestamped verdict as stale (conservative). Soak/liveness are exempt.
    """

    def test_fresh_wedge_is_kept(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # 2 days before the frozen now (2026-06-02) -> well within 30d.
        _seed_verdict_envelope(cfg, generated_at="2026-05-31T11:42:00Z")
        rows = D.collect_decisions(cfg, resolver=None)
        assert [r.kind.value for r in rows] == ["WEDGE"]

    def test_stale_wedge_is_dropped(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # ~60 days before the frozen now -> past the 30d cutoff.
        _seed_verdict_envelope(cfg, generated_at="2026-04-01T10:00:00Z")
        assert D.collect_decisions(cfg, resolver=None) == []

    def test_timestampless_envelope_is_dropped(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # The 259-row problem: an envelope with no generated_at/ts -> age None.
        _seed_verdict_envelope(cfg, generated_at=None)
        assert D.collect_decisions(cfg, resolver=None) == []

    def test_soak_gate_is_recency_exempt(self, tmp_path: Path):
        # A soak window is forward-dated; it is pending because it is still open,
        # never aged out by the refusal-recency filter.
        cfg = default_config(tmp_path)
        _seed_soak(cfg, soak_until="2099-01-01")
        rows = D.collect_decisions(cfg, resolver=None)
        assert [r.kind.value for r in rows] == ["SOAK_GATE"]

    def test_none_cutoff_disables_filter(self, tmp_path: Path):
        # journal_max_age_days=None (the keep-everything opt-out) surfaces even a
        # very old / un-timestamped envelope — byte-for-byte the pre-#476 behaviour.
        # Distinct tokens so the two are different decisions (not deduped into one).
        cfg = default_config(tmp_path)
        cfg = _dataclasses.replace(
            cfg, retention=cfg.retention.with_overrides(journal_max_age_days=None))
        _seed_verdict_envelope(cfg, tag="old-1", token="LANE_ALL_INFLIGHT_OR_DEFERRED",
                               generated_at="2026-01-01T00:00:00Z")
        _seed_verdict_envelope(cfg, tag="old-2", token="MIS_ROUTED_FINDING",
                               generated_at=None)
        rows = D.collect_decisions(cfg, resolver=None)
        # Both old/un-ageable refusals survive when the filter is disabled.
        assert len(rows) == 2
        assert {r.reason_token for r in rows} == {
            "LANE_ALL_INFLIGHT_OR_DEFERRED", "MIS_ROUTED_FINDING"}

    def test_injected_now_controls_staleness(self, tmp_path: Path):
        # The explicit `now` arg overrides the frozen fixture: same envelope is
        # fresh at one clock, stale at a later one.
        cfg = default_config(tmp_path)
        _seed_verdict_envelope(cfg, generated_at="2026-05-31T11:42:00Z")
        fresh_now = _dt.datetime(2026, 6, 5, tzinfo=_dt.timezone.utc)
        stale_now = _dt.datetime(2026, 8, 1, tzinfo=_dt.timezone.utc)
        assert len(D.collect_decisions(cfg, resolver=None, now=fresh_now)) == 1
        assert D.collect_decisions(cfg, resolver=None, now=stale_now) == []


# ---------------------------------------------------------------------------
# Supersession — an arbiter refusal is RESOLVED the moment its contention clears.
# ---------------------------------------------------------------------------


class TestRefusalSupersession:
    """`collect_decisions` drops an ARBITER_REFUSE whose contention a later journal
    event already resolved.

    The bug (the 8-row arbiter-refuse junk-drawer, 2026-06-07): a refusal "lane TF
    cannot share live lane AAF" stayed pending forever even after AAF was scavenged
    39 min later and zero leases were live — because `_from_lane_journal` lifted
    every REFUSE and consulted only age (30d cutoff), never the RELEASE/SCAVENGE
    that resolved it. An arbiter refusal is relative to a live contended lane; the
    moment that lane frees, re-requesting would be admitted, so the refusal is not a
    pending decision. These pin the structural supersession (later free/acquire of a
    watched lane), distinct from the age-based recency filter.
    """

    def test_blocker_scavenged_supersedes_the_refusal(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # TF refused because AAF was live; AAF later scavenged → refusal resolved.
        _seed_refuse_blocked_by(cfg, lane="TF", blocking_lane="AAF", seq=10)
        _seed_scavenge(cfg, lane="AAF", seq=11)
        assert D.collect_decisions(cfg, resolver=None) == []

    def test_blocker_released_supersedes_the_refusal(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_refuse_blocked_by(cfg, lane="TF", blocking_lane="QWD", seq=10)
        _seed_release(cfg, lane="QWD", seq=12)
        assert D.collect_decisions(cfg, resolver=None) == []

    def test_own_lane_later_acquired_supersedes_the_refusal(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # TF refused, then TF itself got leased later → the refusal is moot.
        _seed_refuse_blocked_by(cfg, lane="TF", blocking_lane="AAF", seq=10)
        _seed_acquire_granted(cfg, lane="TF", seq=13)
        assert D.collect_decisions(cfg, resolver=None) == []

    def test_already_held_refusal_resolved_by_self_release(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # "replan already held" names no other lane; a later RELEASE of replan
        # (the prior holder freeing it) resolves it.
        _seed_refuse_already_held(cfg, lane="replan", seq=10)
        _seed_release(cfg, lane="replan", seq=11)
        assert D.collect_decisions(cfg, resolver=None) == []

    def test_unresolved_refusal_still_pending(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # No later free/acquire of TF or AAF → the refusal is a real pending row.
        _seed_refuse_blocked_by(cfg, lane="TF", blocking_lane="AAF", seq=10)
        rows = D.collect_decisions(cfg, resolver=None)
        assert [r.kind.value for r in rows] == ["ARBITER_REFUSE"]
        assert rows[0].lane == "TF"

    def test_free_event_BEFORE_the_refusal_does_not_supersede(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # AAF freed at seq 5, THEN TF refused against a (new) live AAF at seq 10.
        # The earlier free is not the one that resolves THIS refusal — only a free
        # at seq > 10 would. The refusal stays pending.
        _seed_scavenge(cfg, lane="AAF", seq=5)
        _seed_refuse_blocked_by(cfg, lane="TF", blocking_lane="AAF", seq=10)
        rows = D.collect_decisions(cfg, resolver=None)
        assert [r.kind.value for r in rows] == ["ARBITER_REFUSE"]

    def test_unrelated_lane_free_does_not_supersede(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # A release of some OTHER lane (not TF, not the blocker AAF) is irrelevant.
        _seed_refuse_blocked_by(cfg, lane="TF", blocking_lane="AAF", seq=10)
        _seed_release(cfg, lane="DSP", seq=11)
        rows = D.collect_decisions(cfg, resolver=None)
        assert [r.kind.value for r in rows] == ["ARBITER_REFUSE"]

    def test_one_resolved_one_pending_keeps_only_the_pending(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # TF↔AAF resolved (AAF scavenged); SHIMR↔MAS still live → only SHIMR stays.
        _seed_refuse_blocked_by(cfg, lane="TF", blocking_lane="AAF", seq=10)
        _seed_refuse_blocked_by(cfg, lane="SHIMR", blocking_lane="MAS", seq=11,
                                reason="lane 'SHIMR' cannot share live lane 'MAS'")
        _seed_scavenge(cfg, lane="AAF", seq=12)
        rows = D.collect_decisions(cfg, resolver=None)
        assert [r.lane for r in rows] == ["SHIMR"]

    def test_mislabeled_acquire_refusal_is_also_superseded(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        # A docs/139 refusal logged under op=ACQUIRE is still an arbiter refusal;
        # a later genuine acquire of its lane resolves it too.
        _seed_acquire_refused(cfg, lane="lane-01", seq=10)
        _seed_acquire_granted(cfg, lane="lane-01", seq=11)
        assert D.collect_decisions(cfg, resolver=None) == []

    def test_mislabeled_acquire_refusal_unresolved_still_pending(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_acquire_refused(cfg, lane="lane-01", seq=10)
        rows = D.collect_decisions(cfg, resolver=None)
        assert [r.kind.value for r in rows] == ["ARBITER_REFUSE"]

    # --- the pure helper, directly ---

    def test_superseded_seqs_helper_basic(self):
        entries = [
            {"op": "REFUSE", "seq": 1, "lane": "TF", "blocking_trees": {"AAF": ["x"]}},
            {"op": "SCAVENGE", "seq": 2, "lane": "AAF"},
            {"op": "REFUSE", "seq": 3, "lane": "MC", "blocking_trees": {"DSP": ["y"]}},
        ]
        # seq 1 resolved (AAF scavenged after); seq 3 not (DSP never freed).
        assert D._superseded_refuse_seqs(entries) == {1}

    def test_superseded_seqs_helper_ignores_unorderable_seq(self):
        # An entry with no integer seq cannot be placed on the timeline; it is left
        # to the recency filter, never silently superseded here.
        entries = [
            {"op": "REFUSE", "lane": "TF", "blocking_trees": {"AAF": ["x"]}},  # no seq
            {"op": "SCAVENGE", "seq": 2, "lane": "AAF"},
        ]
        assert D._superseded_refuse_seqs(entries) == set()

    def test_refuse_blocking_lanes_includes_self_and_blockers(self):
        e = {"op": "REFUSE", "lane": "TF", "blocking_trees": {"AAF": ["x"], "QWD": ["y"]}}
        assert D._refuse_blocking_lanes(e) == {"TF", "AAF", "QWD"}


# ---------------------------------------------------------------------------
# Backpressure — a refusal whose lever is "wait / re-pick" is NOT a HUMAN decision.
# ---------------------------------------------------------------------------


class TestBackpressureClassification:
    """A routine lane refusal (mutex contention the loop self-resolves) is classed
    BACKPRESSURE and hidden from the default HUMAN queue; an exact-glob hard
    collision stays HUMAN.

    The junk-drawer root (2026-06-07): raising the loop-concurrency ceiling
    multiplied "already held" / budget / soft-overlap refusals, and the queue
    surfaced every one as a HUMAN decision (the "no token ⇒ HUMAN" default). But
    these self-resolve — the loop waits or re-picks a disjoint lane — so they are
    not decisions. Only the same-file exact-glob collision is a real lane-def call.
    """

    # --- the pure classifier, against the kernel/host refuse strings verbatim ---

    @pytest.mark.parametrize("prose", [
        "lane 'replan' is already held by a live loop — pick a different --lane or wait.",
        "GLOBAL_LOOP_CEILING — 5 live dispatch-loop lease(s) >= ceiling 5",
        "CLASS_BUDGET_EXHAUSTED: every auto-pick candidate belongs to a concurrency class",
        "lane 'replan' cannot share live lane 'AB': overlap too large (6/6 = 100%)",
        "lane 'X' has an EMPTY tree (unknown blast radius) and cannot share live lane 'Y'",
        "LANE_CAPACITY_SATURATED — 6 live loops >= 6 disjoint lanes",
    ])
    def test_classifier_flags_backpressure_shapes(self, prose):
        assert D._is_backpressure_refusal(prose) is True

    @pytest.mark.parametrize("prose", [
        # An exact-glob HARD collision is the actionable structural signal — NOT bp.
        "lane 'TF' cannot share live lane 'AAF': exact-glob overlap — identical glob "
        "claimed by both lanes (1: agents/apply_pick/__init__.py)",
        # A genuine no-pick wedge reason is not a lane refusal at all.
        "every concurrency-free lane has 0 pickable phases",
        "",
    ])
    def test_classifier_keeps_real_decisions(self, prose):
        assert D._is_backpressure_refusal(prose) is False

    def test_exact_glob_vetoes_a_loose_backpressure_match(self):
        # Even if the prose ALSO contained an overlap word, the exact-glob marker
        # must win (it is the structural signal). "cannot share ... exact-glob".
        prose = ("lane 'TF' cannot share live lane 'AAF': exact-glob overlap — "
                 "identical glob claimed by both lanes")
        assert D._is_backpressure_refusal(prose) is False

    # --- end-to-end through collect_decisions ---

    def test_already_held_is_hidden_from_human_queue(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_refuse_prose(cfg, lane="replan", seq=1,
                           prose="lane 'replan' is already held by a live loop — pick a different --lane or wait.")
        assert D.collect_decisions(cfg, resolver="HUMAN") == []
        # ...but visible under --all, labeled BACKPRESSURE.
        alld = D.collect_decisions(cfg, resolver=None)
        assert [(d.lane, d.resolver_kind.value) for d in alld] == [("replan", "BACKPRESSURE")]

    def test_ceiling_and_budget_are_backpressure(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_refuse_prose(cfg, lane="TF", seq=1,
                           prose="GLOBAL_LOOP_CEILING — 5 live lease(s) >= ceiling 5")
        _seed_refuse_prose(cfg, lane="MAS", seq=2,
                           prose="CLASS_BUDGET_EXHAUSTED: every candidate is at its max_concurrent budget")
        assert D.collect_decisions(cfg, resolver="HUMAN") == []
        assert len(D.collect_decisions(cfg, resolver=None)) == 2

    def test_exact_glob_collision_stays_human(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_refuse_prose(cfg, lane="TF", seq=1, blocking_lane="AAF",
                           prose="lane 'TF' cannot share live lane 'AAF': exact-glob overlap — "
                                 "identical glob claimed by both lanes (1: agents/apply_pick/__init__.py)")
        rows = D.collect_decisions(cfg, resolver="HUMAN")
        assert [(d.lane, d.resolver_kind.value) for d in rows] == [("TF", "HUMAN")]

    def test_the_storm_shows_only_the_real_collision(self, tmp_path: Path):
        # The exact 2026-06-07 shape: 4 backpressure refusals + 1 exact-glob, none
        # superseded (all fresh). The default queue must show ONLY the collision.
        cfg = default_config(tmp_path)
        _seed_refuse_prose(cfg, lane="replan", seq=1,
                           prose="lane 'replan' is already held by a live loop")
        _seed_refuse_prose(cfg, lane="MAS", seq=2,
                           prose="lane 'MAS' is already held by a live loop")
        _seed_refuse_prose(cfg, lane="QWD", seq=3,
                           prose="lane 'QWD' is already held by a live loop")
        _seed_refuse_prose(cfg, lane="replan", seq=4,
                           prose="lane 'replan' cannot share live lane 'AB': overlap too large (6/6 = 100%)")
        _seed_refuse_prose(cfg, lane="TF", seq=5, blocking_lane="AAF",
                           prose="lane 'TF' cannot share live lane 'AAF': exact-glob overlap — identical glob")
        human = D.collect_decisions(cfg, resolver="HUMAN")
        assert [d.lane for d in human] == ["TF"]
        assert len(D.collect_decisions(cfg, resolver=None)) == 5  # all still visible under --all

    def test_backpressure_and_supersession_compose(self, tmp_path: Path):
        # A backpressure refusal that is ALSO later superseded: hidden from HUMAN
        # (backpressure) AND dropped entirely (superseded) — gone from --all too.
        cfg = default_config(tmp_path)
        _seed_refuse_prose(cfg, lane="replan", seq=1,
                           prose="lane 'replan' is already held by a live loop")
        _seed_release(cfg, lane="replan", seq=2)
        assert D.collect_decisions(cfg, resolver=None) == []

    def test_a_real_soak_wedge_is_unaffected(self, tmp_path: Path):
        # The backpressure path must not touch a genuine WEDGE/soak decision.
        cfg = default_config(tmp_path)
        _seed_soak(cfg, soak_until="2099-01-01")
        rows = D.collect_decisions(cfg, resolver="HUMAN")
        assert [r.kind.value for r in rows] == ["SOAK_GATE"]


# ---------------------------------------------------------------------------
# Enforcement storms — repeated hook denies escalate to ONE decision (issue #14).
# ---------------------------------------------------------------------------


_SM_REASON = (
    "lane 'Write' would edit the orchestrator's own running code "
    "(src/dos/arbiter.py) — refusing to let a live loop rewrite the kernel "
    "that is adjudicating it (SELF_MODIFY)."
)


def _seed_enforce(cfg, *, seq, ts="2026-06-01T10:00:00Z", holder="S1",
                  tool="Write", reason=_SM_REASON, reason_class="SELF_MODIFY",
                  decision="deny", lift_reason_class=True):
    """Append one OP_ENFORCE record, in either writer's shape.

    `lift_reason_class=True` is the Python `enforce_entry` shape (the token lifted
    to the top level); `False` leaves it ONLY inside the nested `proposal` body —
    the shape the Go fast-path writer left before the lift (the fold must read
    both, or a Go-written storm is invisible).
    """
    lj = cfg.paths.lane_journal
    lj.parent.mkdir(parents=True, exist_ok=True)
    intervention = "BLOCK" if decision == "deny" else "WARN"
    row = {
        "op": "ENFORCE", "seq": seq, "ts": ts, "lane": tool, "tool": tool,
        "holder": holder, "intervention": intervention,
        "dispatch_call": decision != "deny", "withheld": decision == "deny",
        "handler": "admission", "reason": reason,
        "proposal": {"decision": decision, "intervention": intervention,
                     "reason": reason, "reason_class": reason_class,
                     "rung": "admission"},
    }
    if lift_reason_class:
        row["reason_class"] = reason_class
    with lj.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


class TestEnforceStorms:
    """Repeated SELF_MODIFY ENFORCE denies of the same (holder, target) fold —
    through the shipped docs/223 breaker — into ONE pending HUMAN decision; an
    isolated deny raises nothing (issue #14: 21 silent identical retries while
    `dos decisions` said "(none pending)" all day).
    """

    def test_three_denies_raise_one_human_decision(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        for i in range(3):
            _seed_enforce(cfg, seq=i + 1, ts=f"2026-06-01T10:0{i}:00Z")
        rows = D.collect_decisions(cfg)  # default resolver="HUMAN"
        assert len(rows) == 1
        d = rows[0]
        assert d.kind.value == "ENFORCE_BREAKER"
        assert d.resolver_kind.value == "HUMAN"
        # The decision names the target and the count (the done-condition).
        assert "src/dos/arbiter.py" in d.reason_text
        assert "3x" in d.reason_text
        assert "S1" in d.reason_text
        assert d.reason_token == "SELF_MODIFY"
        assert d.dup_count == 3

    def test_single_deny_raises_nothing(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_enforce(cfg, seq=1)
        assert D.collect_decisions(cfg, resolver=None) == []

    def test_two_denies_stay_under_threshold(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_enforce(cfg, seq=1)
        _seed_enforce(cfg, seq=2, ts="2026-06-01T10:05:00Z")
        assert D.collect_decisions(cfg, resolver=None) == []

    def test_go_written_shape_is_read_too(self, tmp_path: Path):
        # The Go fast-path writer left reason_class only in the nested proposal;
        # the fold must read that shape or a native-binary storm is invisible.
        cfg = default_config(tmp_path)
        for i in range(3):
            _seed_enforce(cfg, seq=i + 1, lift_reason_class=False)
        rows = D.collect_decisions(cfg)
        assert [d.kind.value for d in rows] == ["ENFORCE_BREAKER"]

    def test_different_holders_do_not_pool(self, tmp_path: Path):
        # 2 denies each from two holders: neither group reaches the threshold —
        # the storm is per-(holder, target), never a global tally.
        cfg = default_config(tmp_path)
        _seed_enforce(cfg, seq=1, holder="A")
        _seed_enforce(cfg, seq=2, holder="A")
        _seed_enforce(cfg, seq=3, holder="B")
        _seed_enforce(cfg, seq=4, holder="B")
        assert D.collect_decisions(cfg, resolver=None) == []

    def test_different_targets_make_separate_decisions(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        other = _SM_REASON.replace("arbiter.py", "lane_overlap.py")
        for i in range(3):
            _seed_enforce(cfg, seq=2 * i + 1)
            _seed_enforce(cfg, seq=2 * i + 2, reason=other)
        rows = D.collect_decisions(cfg)
        assert len(rows) == 2
        targets = {d.reason_text for d in rows}
        assert any("arbiter.py" in t for t in targets)
        assert any("lane_overlap.py" in t for t in targets)

    def test_override_admit_resolves_the_storm(self, tmp_path: Path):
        # The docs/296 armed window let the edit through — record_success resets
        # the consecutive count, so the storm is RESOLVED, not pending.
        cfg = default_config(tmp_path)
        for i in range(3):
            _seed_enforce(cfg, seq=i + 1)
        _seed_enforce(cfg, seq=4, decision="override-admit")
        assert D.collect_decisions(cfg, resolver=None) == []

    def test_denies_after_resolution_count_anew(self, tmp_path: Path):
        # After an override-admit, a FRESH run of 3 denies trips again (the
        # consecutive counter, not the lifetime total, is the storm signal).
        cfg = default_config(tmp_path)
        for i in range(3):
            _seed_enforce(cfg, seq=i + 1)
        _seed_enforce(cfg, seq=4, decision="override-admit")
        _seed_enforce(cfg, seq=5)
        _seed_enforce(cfg, seq=6)
        assert D.collect_decisions(cfg, resolver=None) == []
        _seed_enforce(cfg, seq=7)
        rows = D.collect_decisions(cfg)
        assert [d.kind.value for d in rows] == ["ENFORCE_BREAKER"]
        assert rows[0].dup_count == 3

    def test_non_self_modify_enforce_never_rises(self, tmp_path: Path):
        # A provenance WARN (or any other class) is not an operator-only deny —
        # the fold under-matches by design.
        cfg = default_config(tmp_path)
        for i in range(5):
            _seed_enforce(cfg, seq=i + 1, reason_class="", decision="warn",
                          reason="suspicious provenance on arg 2")
        assert D.collect_decisions(cfg, resolver=None) == []

    def test_stale_storm_ages_out(self, tmp_path: Path):
        # A storm whose LAST deny is past the retention cutoff is no longer
        # pending — the operator resolved it long ago (or the loop died).
        cfg = default_config(tmp_path)
        for i in range(3):
            _seed_enforce(cfg, seq=i + 1, ts="2026-04-01T10:00:00Z")  # ~60d old
        assert D.collect_decisions(cfg, resolver=None) == []

    def test_storm_outranks_wedge_and_is_now_urgency(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        _seed_verdict_envelope(cfg)  # a WEDGE row
        for i in range(3):
            _seed_enforce(cfg, seq=i + 1)
        rows = D.collect_decisions(cfg, resolver=None)
        kinds = [d.kind.value for d in rows]
        assert kinds.index("ENFORCE_BREAKER") < kinds.index("WEDGE")
        storm = rows[kinds.index("ENFORCE_BREAKER")]
        assert D.urgency_of(storm).value == "NOW"

    def test_next_steps_name_hook_real_remedies_only(self, tmp_path: Path):
        cfg = default_config(tmp_path)
        for i in range(3):
            _seed_enforce(cfg, seq=i + 1)
        d = D.collect_decisions(cfg)[0]
        steps = dict(D.next_steps(d, cfg))
        assert steps.get("o") == "dos override status"
        assert steps.get("m") == "dos man wedge SELF_MODIFY"
        # No /replan (the lane is a tool name) and no arbitrate --force (the
        # storm is at the hook surface, which has no force — issue #14's trap).
        assert "r" not in steps
        assert "f" not in steps

    def test_arm_file_perimeter_denies_group_on_reason_text(self, tmp_path: Path):
        # The docs/296 arm-file deny carries no "own running code (...)" target —
        # the fold falls back to the reason text, so identical retries still trip.
        cfg = default_config(tmp_path)
        arm = ("this call would write the operator's SELF_MODIFY override arm "
               "file (.dos/override/arm.json) — only the operator arms a window")
        for i in range(3):
            _seed_enforce(cfg, seq=i + 1, reason=arm)
        rows = D.collect_decisions(cfg)
        assert [d.kind.value for d in rows] == ["ENFORCE_BREAKER"]
