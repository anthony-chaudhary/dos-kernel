"""The apply-gate FENCE — docs/342 M2 (P-FENCE) / docs/114 §A2 (Kleppmann's token).

The M1 apply-gate (`test_apply_gate.py`) refuses a write that ESCAPES the held lane
or COLLIDES with a sibling's live lease. This file pins the M2 addition: a write
whose held GENERATION is superseded by a LATER grant on an overlapping region is
refused with `STALE_GENERATION`. The hazard (docs/114 §A2, routine for LLM agents
whose pauses outlive a lease TTL):

    A acquires src/** → A stalls > TTL without crashing → the scavenger frees the
    lane → B acquires src/** (a strictly higher generation) → A wakes ignorant and
    writes the same region. Nothing rejected A's write before the fence.

The fence is the missing fencing token: a monotonic generation stamped on each grant
in the WAL (`lane_journal.lease_generations`, folded by append-order), CHECKED AT THE
GATE — never trusted from the holder's heartbeat (itself a self-report, §A2's sharpest
point). These tests pin, at three levels:

  * the pure fold (`lease_generations`) — monotonic by construction; a re-grant after
    a scavenge takes a strictly higher number; survives compaction;
  * the pure gate (`apply_gate.decide` / `_stale_fence`) — the P-FENCE witness verdict
    (A refused, B passes), fail-CLOSED on an unresolvable generation, dormant when no
    generation evidence is supplied (M1 behavior byte-identical), refuse-MORE only;
  * the END-TO-END WAL interleave — acquire A, scavenge A, re-acquire B over the same
    region, fold the REAL WAL, and run the gate: A's write is REFUSED on a superseded
    generation and B's PASSES — the witness the holder's own belief can't pass.
"""

from __future__ import annotations

import pytest

from dos import lane_lease
from dos import config as _config
from dos.apply_gate import ApplyDecision, ApplyEvidence, SCOPE_ESCAPE, STALE_GENERATION, decide
from dos.lane_journal import (
    acquire_entry,
    compact,
    lease_generations,
    read_all,
    release_entry,
    replay,
    scavenge_entry,
)


# ===========================================================================
# 1. The pure generation fold — monotonic by construction (the WAL token).
# ===========================================================================


def _acq(loop_ts: str, lane: str = "src", tree=("src/**",)) -> dict:
    return acquire_entry({"lane": lane, "loop_ts": loop_ts, "tree": tuple(tree)})


def test_generations_are_monotonic_by_append_order():
    """Each grant takes the NEXT generation in append order — strictly increasing,
    the monotonicity the fence's supersede test rests on."""
    entries = [_acq("tA"), _acq("tB", lane="docs", tree=("docs/**",)), _acq("tC", lane="x", tree=("x/**",))]
    gens = lease_generations(entries)
    assert gens[("tA", "src")] == 1
    assert gens[("tB", "docs")] == 2
    assert gens[("tC", "x")] == 3
    # Strictly increasing — no two grants share a generation.
    vals = sorted(gens.values())
    assert vals == [1, 2, 3]
    assert len(set(vals)) == len(vals)


def test_regrant_after_scavenge_takes_a_higher_generation():
    """THE FENCE PRIMITIVE: A is granted (gen 1), scavenged (dropped from the map),
    then the SAME region is re-granted to B (gen 2 > 1). A holds no generation
    anymore; B holds the strictly-higher one — the supersede the gate checks."""
    entries = [_acq("tA"), scavenge_entry({"lane": "src", "loop_ts": "tA"}), _acq("tB")]
    gens = lease_generations(entries)
    # A's identity is gone (scavenged); only B's live grant carries a generation.
    assert ("tA", "src") not in gens
    assert gens[("tB", "src")] == 2  # strictly higher than A's old 1


def test_release_also_drops_the_generation():
    """A RELEASE drops the generation exactly like a SCAVENGE — a freed lease holds
    no token; the region's live generation belongs to whoever holds it NOW."""
    entries = [_acq("tA"), release_entry({"lane": "src", "loop_ts": "tA"})]
    assert lease_generations(entries) == {}


def test_heartbeat_does_not_mint_a_new_generation():
    """A HEARTBEAT is a beat, not a grant — the generation is unchanged (only an
    ACQUIRE/RECONCILE re-fences a region)."""
    from dos.lane_journal import heartbeat_entry

    entries = [_acq("tA"), heartbeat_entry({"lane": "src", "loop_ts": "tA"}, heartbeat_at="2026-06-01T14:05Z")]
    gens = lease_generations(entries)
    assert gens == {("tA", "src"): 1}  # still gen 1, the beat did not bump it


def test_generations_survive_compaction_and_stay_monotonic():
    """A CHECKPOINT carries the surviving leases' generations in its OWN field, so a
    compacted WAL yields the same generations — and a post-checkpoint grant continues
    strictly above the watermark (no reset, the `seq_watermark` discipline for the
    fencing token)."""
    entries = [_acq("tA"), scavenge_entry({"lane": "src", "loop_ts": "tA"}), _acq("tB")]
    comp = compact(entries)
    assert lease_generations(comp) == lease_generations(entries) == {("tB", "src"): 2}
    # A grant AFTER the checkpoint must take a generation above the watermark (3),
    # never reuse/reset below B's surviving 2.
    post = lease_generations(comp + [_acq("tC", lane="docs", tree=("docs/**",))])
    assert post[("tC", "docs")] == 3
    assert post[("tB", "src")] == 2


def test_compaction_preserves_replay_equivalence_with_generations():
    """The hero invariant is UNTOUCHED by M2: `replay(compact(E)) == replay(E)`.
    Generations ride in a SEPARATE checkpoint field, so `replay`'s reconstructed
    live set is byte-identical (no `generation` key leaks onto the lease dict)."""
    entries = [_acq("tA"), scavenge_entry({"lane": "src", "loop_ts": "tA"}), _acq("tB")]
    assert replay(compact(entries)) == replay(entries)
    # NON-VACUITY: the live lease dict carries NO `generation` key (that would break
    # the dict-equality the invariant is asserted by).
    live = replay(entries)
    assert all("generation" not in lz for lz in live)


# ===========================================================================
# 2. The pure gate fence — the P-FENCE witness verdict + the safety rules.
# ===========================================================================


def _ev(touched, *, self_lane="src", self_tree=("src/**",), other_trees=(),
        self_generation=None, other_generations=()):
    return ApplyEvidence(
        touched_files=frozenset(touched),
        self_lane=self_lane,
        self_tree=tuple(self_tree),
        other_trees=tuple(tuple(t) for t in other_trees),
        self_generation=self_generation,
        other_generations=tuple(other_generations),
    )


def test_fence_witness_superseded_holder_is_refused():
    """THE P-FENCE WITNESS (gate half): A holds generation 1, but a later grant
    (B, generation 2) holds the overlapping region. A's write is REFUSED with the
    typed STALE_GENERATION token — the stale-fencing-token refusal, naming the gap."""
    d = decide(_ev(["src/x.py"], other_trees=(("src/**",),),
                   self_generation=1, other_generations=(2,)))
    assert isinstance(d, ApplyDecision)
    assert d.allowed is False
    assert d.reason_class == STALE_GENERATION
    assert "src/x.py" in d.refused_files
    assert "generation 1" in d.reason and "generation 2" in d.reason


def test_fence_witness_current_holder_passes():
    """THE P-FENCE WITNESS (gate half): B is the current holder (generation 2) with
    NO overlapping OTHER live lease (self is excluded from other_trees). The fence
    finds nothing to supersede it → B's write PASSES."""
    d = decide(_ev(["src/x.py"], other_trees=(), self_generation=2, other_generations=()))
    assert d.allowed is True
    assert d.reason_class == ""


def test_fence_fails_closed_on_unresolvable_generation():
    """FAIL CLOSED: a holder that resolves NO generation (paused past its scavenge,
    or never fenced) but whose write overlaps a LIVE grant is treated as superseded
    — an unresolvable token cannot prove the holder still owns the lane."""
    d = decide(_ev(["src/x.py"], other_trees=(("src/**",),),
                   self_generation=None, other_generations=(2,)))
    assert d.allowed is False
    assert d.reason_class == STALE_GENERATION
    assert "<none>" in d.reason  # the held generation is named as unresolved


def test_equal_generation_does_not_fence():
    """Only a STRICTLY-GREATER generation supersedes (refuse-MORE only, monotonic):
    an equal generation is not a later grant, so the fence does NOT fire — the write
    falls through to the collision rung (a genuine sibling overlap)."""
    d = decide(_ev(["src/x.py"], other_trees=(("src/**",),),
                   self_generation=2, other_generations=(2,)))
    # Not fenced; the overlap is a live collision instead (a real sibling).
    assert d.allowed is False
    assert d.reason_class == SCOPE_ESCAPE


def test_lower_other_generation_does_not_fence():
    """A live lease with a LOWER generation than this run's is an OLDER grant — it
    cannot supersede a newer holder. The fence does not fire on it (it would be a
    collision if the regions overlap, but never a stale-fence refusal)."""
    d = decide(_ev(["src/x.py"], other_trees=(("src/**",),),
                   self_generation=5, other_generations=(2,)))
    assert d.reason_class != STALE_GENERATION


def test_fence_dormant_when_no_generation_evidence_m1_unchanged():
    """DORMANT: with NO generation evidence (`other_generations` empty), the fence is
    inert and the M1 gate is byte-identical — an overlap is the collision rung's
    SCOPE_ESCAPE, never a fence. This is what keeps every pre-M2 `decide` call
    unchanged (the fence only BINDS once the boundary folds generations in)."""
    d = decide(_ev(["src/x.py"], other_trees=(("src/**",),)))  # no generations
    assert d.allowed is False
    assert d.reason_class == SCOPE_ESCAPE  # collision rung, not the fence


def test_fence_only_fires_on_overlap():
    """A superseding generation on a DISJOINT region does not fence this write — the
    fence is region-scoped (the sound zero-tolerance intersection), so a higher
    generation elsewhere is irrelevant to a non-overlapping footprint."""
    d = decide(_ev(["src/x.py"], self_tree=("src/**",), other_trees=(("docs/**",),),
                   self_generation=1, other_generations=(9,)))
    # src/x.py is inside src/** (contained) and does NOT overlap docs/** → allowed.
    assert d.allowed is True
    assert d.reason_class == ""


def test_fence_is_pure_no_io(monkeypatch):
    """The fence verdict reads no file and no clock — `decide` stays the pure
    `classify(evidence, policy)` shape (the apply-gate purity litmus), so it is
    replay-testable on frozen evidence exactly like `scope.gate`."""
    import builtins

    real_open = builtins.open

    def _poisoned_open(*a, **k):  # any file touch is a purity breach
        raise AssertionError("decide() touched the filesystem")

    monkeypatch.setattr(builtins, "open", _poisoned_open)
    try:
        d = decide(_ev(["src/x.py"], other_trees=(("src/**",),),
                       self_generation=1, other_generations=(2,)))
    finally:
        monkeypatch.setattr(builtins, "open", real_open)
    assert d.reason_class == STALE_GENERATION


# ===========================================================================
# 3. END-TO-END — the full WAL interleave through the real fold + real gate.
# ===========================================================================


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    # Pin the lock + journal at tmp paths (the test_lane_lease idiom): every record
    # lands in isolation. The generic default taxonomy ('main'/'global') is in force.
    monkeypatch.setenv("DISPATCH_LANE_LEASE_LOCK_PATH", str(tmp_path / ".lane-lease.lock"))
    monkeypatch.setenv("DISPATCH_LANE_JOURNAL_PATH", str(tmp_path / "lane-journal.jsonl"))
    return _config.default_config(str(tmp_path))


def test_end_to_end_fence_scavenged_holder_refused_current_holder_passes(cfg):
    """THE P-FENCE WITNESS, END-TO-END over the REAL WAL:

      1. A acquires the region (generation 1).
      2. A is SCAVENGED (the scavenger frees the stale lane) — A's grant is dropped.
      3. B acquires the SAME region (generation 2 > 1) — the re-grant.
      4. A WAKES and attempts a write through the gate, presenting the stale
         generation 1 it still believes it holds → REFUSED on STALE_GENERATION.
      5. B writes through the gate, presenting its current generation 2 → PASSES.

    The witness is the WAL generation record + the gate's refusal — reproducible
    here — NOT the holder's belief about whether it still holds the lease (which is
    exactly the forgeable self-report the fence exists to overrule)."""
    region = ["main/**"]  # the generic 'main' concurrent lane's region

    # 1. A acquires.
    a = lane_lease.acquire(cfg, lane="main", kind="concurrent", tree=region,
                           owner="A", loop_ts="2026-06-02T12:00Z", run_id="run-A")
    assert a.decision.outcome == "acquire"

    # 2. A is scavenged (its grant freed) — append a SCAVENGE for A's identity.
    from dos import lane_journal
    lane_journal.append(
        scavenge_entry({"lane": "main", "loop_ts": "2026-06-02T12:00Z"}, reason="orphan_ttl"),
        lane_lease._journal_path(cfg),
    )

    # 3. B re-acquires the same region — a strictly higher generation.
    b = lane_lease.acquire(cfg, lane="main", kind="concurrent", tree=region,
                           owner="B", loop_ts="2026-06-02T12:05Z", run_id="run-B")
    assert b.decision.outcome == "acquire"

    # Fold the REAL WAL: B is the one live lease; the generations show B superseded A.
    entries = read_all(lane_lease._journal_path(cfg))
    gens = lease_generations(entries)
    assert gens == {("2026-06-02T12:05Z", "main"): 2}  # only B lives; gen 2 > A's old 1

    live = lane_lease.live_leases(cfg)
    assert len(live) == 1 and live[0].get("run_id") == "run-B"

    # 4. A wakes and writes. A holds the STALE generation 1 (its old grant); the live
    #    overlapping region is B's (generation 2). The gate folds B's region as the
    #    only OTHER live lease and refuses A on the superseded generation.
    a_other_trees = (tuple(live[0].get("tree") or ()),)  # B's region, A's only sibling
    a_other_gens = (gens[("2026-06-02T12:05Z", "main")],)  # B's generation = 2
    a_decision = decide(ApplyEvidence(
        touched_files=frozenset(["main/x.py"]),
        self_lane="main", self_tree=("main/**",),
        other_trees=a_other_trees,
        self_generation=1,            # A's stale token (its scavenged grant)
        other_generations=a_other_gens,
    ))
    assert a_decision.allowed is False
    assert a_decision.reason_class == STALE_GENERATION

    # 5. B writes. B is the current holder (generation 2); excluding its own lease,
    #    there is NO overlapping OTHER live lease, so the fence finds nothing to
    #    supersede it and B PASSES.
    b_decision = decide(ApplyEvidence(
        touched_files=frozenset(["main/x.py"]),
        self_lane="main", self_tree=("main/**",),
        other_trees=(),               # B has no sibling on the region
        self_generation=2,            # B's current token
        other_generations=(),
    ))
    assert b_decision.allowed is True
    assert b_decision.reason_class == ""


def test_live_generations_boundary_folds_the_wal(cfg):
    """`lane_lease.live_generations` is the boundary I/O shell over the pure fold:
    it reads the WAL and returns the per-lease generation map a gate caller joins
    onto its live set. A re-grant of a released lane takes the NEXT generation —
    only the current (live) holder's generation is returned (the released one is
    dropped), exactly as the gate needs."""
    lane_lease.acquire(cfg, lane="main", kind="concurrent", tree=["main/**"],
                       owner="A", loop_ts="2026-06-02T12:00Z")
    # A's generation is live now (gen 1).
    assert lane_lease.live_generations(cfg) == {("2026-06-02T12:00Z", "main"): 1}
    # A releases; the lane is free, and A's generation is dropped from the map.
    lane_lease.release(cfg, lane="main", owner="A", loop_ts="2026-06-02T12:00Z")
    assert lane_lease.live_generations(cfg) == {}
    # B re-acquires the freed lane — a strictly HIGHER generation (2), the monotonic
    # re-grant the fence rests on.
    lane_lease.acquire(cfg, lane="main", kind="concurrent", tree=["main/**"],
                       owner="B", loop_ts="2026-06-02T12:05Z")
    gens = lane_lease.live_generations(cfg)
    assert gens == {("2026-06-02T12:05Z", "main"): 2}
