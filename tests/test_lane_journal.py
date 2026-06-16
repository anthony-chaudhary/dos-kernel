"""Lane-journal entry builders — the pure constructors + their replay folds.

These pin the entry-builder idiom (acquire/release/scavenge) and the WAL-recovery
invariant on FROZEN entry lists (no disk, the `replay()` discipline): an ACQUIRE
followed by a SCAVENGE of the same `(loop_ts, lane)` lease must fold to the empty
live-lease set — a scavenge evicts exactly like a release.
"""

from __future__ import annotations

from dos.lane_journal import (
    OP_ACQUIRE,
    OP_CHECKPOINT,
    OP_ENFORCE,
    OP_HEARTBEAT,
    OP_REFUSE,
    OP_SCAVENGE,
    OP_SPAWN,
    _STATE_MUTATING_OPS,
    acquire_entry,
    enforce_entry,
    heartbeat_entry,
    release_entry,
    replay,
    scavenge_entry,
    spawn_entry,
)

# A realistic lease row, shaped like the execution-state.yaml lease the
# scheduler mints (matches fanout_state's lease keys).
_LEASE = {
    "lane": "apply",
    "lane_kind": "concurrent",
    "tree": ("agents/apply_*.py",),
    "loop_ts": "2026-06-01T14:00Z",
    "host_id": "host-a",
    "pid": 4242,
    "acquired_at": "2026-06-01T14:00:03Z",
    "heartbeat_at": "2026-06-01T14:02:11Z",
}


def test_scavenge_entry_op_and_keys():
    """scavenge_entry stamps OP_SCAVENGE + the eviction key (loop_ts, lane) +
    host_id + reason, AND the forensic pair pid + prev_holder."""
    e = scavenge_entry(_LEASE, reason="orphan_ttl", prev_holder="host-a:4242")

    assert e["op"] == OP_SCAVENGE
    # The eviction key replay folds on.
    assert e["loop_ts"] == "2026-06-01T14:00Z"
    assert e["lane"] == "apply"
    # Host + reason.
    assert e["host_id"] == "host-a"
    assert e["reason"] == "orphan_ttl"
    # Forensic fields — who was evicted.
    assert e["pid"] == 4242
    assert e["prev_holder"] == "host-a:4242"


def test_scavenge_entry_default_reason():
    """The default reason is 'scavenged' and prev_holder defaults to None."""
    e = scavenge_entry(_LEASE)
    assert e["reason"] == "scavenged"
    assert e["prev_holder"] is None


def test_replay_acquire_then_scavenge_evicts_lease():
    """replay([ACQUIRE(L), SCAVENGE(L)]) folds to the empty live-lease set —
    a scavenge evicts the lease exactly like a release."""
    entries = [acquire_entry(_LEASE), scavenge_entry(_LEASE)]

    # Sanity: the ACQUIRE alone yields exactly one live lease.
    assert len(replay([entries[0]])) == 1
    assert replay([entries[0]])[0]["lane"] == "apply"

    # ACQUIRE then SCAVENGE of the SAME (loop_ts, lane) -> evicted.
    assert replay(entries) == []


# ── run_id on the ACQUIRE (docs/118 Size S / docs/137 — the WAL↔spine join) ──


def test_acquire_entry_carries_run_id_join_ready_shape():
    """`acquire_entry(run_id=…)` produces the join-ready shape docs/118 measured at
    `0`: an ACQUIRE carrying BOTH a `loop_ts` AND a parseable `lease.run_id`, which
    `replay` reconstructs onto the live lease so a *held* lane (not just a *refused*
    one) is traceable back to its run."""
    e = acquire_entry(_LEASE, run_id="RID-ABCD1234567")
    # The id rides on the NESTED lease (so replay carries it + an ADOPT preserves it).
    assert e["lease"]["run_id"] == "RID-ABCD1234567"
    # Both join keys present on the reconstructed live lease — the measured-0 shape.
    live = replay([e])
    assert live[0]["loop_ts"] == "2026-06-01T14:00Z"
    assert live[0]["run_id"] == "RID-ABCD1234567"


def test_acquire_entry_without_run_id_replays_unchanged():
    """The additive contract (the lane-journal forward-compat rule): an ACQUIRE with
    no run_id carries no `run_id` key and replays byte-identically — adding the field
    never disturbs an existing entry."""
    e = acquire_entry(_LEASE)
    assert "run_id" not in e["lease"]
    live = replay([e])
    assert live[0]["lane"] == "apply"
    assert "run_id" not in live[0]


# ── heartbeat_entry (LJ2a) — the builder LJ1 omitted ────────────────────────


def test_heartbeat_entry_op_and_identity_keys():
    """heartbeat_entry stamps OP_HEARTBEAT + the (loop_ts, lane) identity the
    replay fold keys on, + the explicit heartbeat_at when given. It carries the
    identity + stamp only — a beat is not a state-change, so no full lease body."""
    e = heartbeat_entry(_LEASE, heartbeat_at="2026-06-01T14:05:30Z")
    assert e["op"] == OP_HEARTBEAT
    assert e["loop_ts"] == "2026-06-01T14:00Z"
    assert e["lane"] == "apply"
    assert e["host_id"] == "host-a"
    assert e["heartbeat_at"] == "2026-06-01T14:05:30Z"


def test_heartbeat_entry_omits_stamp_when_not_given():
    """With no explicit heartbeat_at the key is absent, so replay falls back to
    the entry `ts` (filled by append) — the documented default."""
    e = heartbeat_entry(_LEASE)
    assert "heartbeat_at" not in e


def test_replay_heartbeat_updates_live_lease_freshness():
    """replay([ACQUIRE(L), HEARTBEAT(L)]) keeps the lease live and advances its
    heartbeat_at to the beat's stamp — the fold LJ1 declared but never fed."""
    acq = acquire_entry(_LEASE)  # heartbeat_at == 14:02:11Z from _LEASE
    beat = heartbeat_entry(_LEASE, heartbeat_at="2026-06-01T14:09:00Z")
    live = replay([acq, beat])
    assert len(live) == 1
    assert live[0]["heartbeat_at"] == "2026-06-01T14:09:00Z"


def test_replay_heartbeat_on_absent_lease_is_noop():
    """A HEARTBEAT for a (loop_ts, lane) with no live ACQUIRE folds to nothing —
    a beat never resurrects an evicted/never-acquired lease."""
    beat = heartbeat_entry(_LEASE, heartbeat_at="2026-06-01T14:09:00Z")
    assert replay([beat]) == []
    # And after a release, a trailing beat does not bring it back.
    acq = acquire_entry(_LEASE)
    from dos.lane_journal import release_entry
    assert replay([acq, release_entry(_LEASE), beat]) == []


# ── refuse_entry (LJ2) — the missing PRODUCER for OP_REFUSE ──────────────────


class _StubDecision:
    """Duck-types the arbiter's LaneDecision: exposes .reason and .lane only."""

    def __init__(self, reason: str, lane: str = ""):
        self.reason = reason
        self.lane = lane


def test_refuse_entry_op_and_keys():
    """refuse_entry stamps OP_REFUSE + holder(owner) + lane/loop_ts/host_id and
    the arbiter's prose reason; pure, duck-typed off a stub decision."""
    from dos.lane_journal import refuse_entry

    d = _StubDecision(reason="lane 'apply' is already held", lane="apply")
    e = refuse_entry(d, owner="worker-7", loop_ts="2026-06-01T14:00Z",
                     host_id="host-a", run_id="RID-Z")
    assert e["op"] == OP_REFUSE
    assert e["holder"] == "worker-7"
    assert e["lane"] == "apply"           # from the decision when no explicit lane
    assert e["loop_ts"] == "2026-06-01T14:00Z"
    assert e["host_id"] == "host-a"
    assert e["run_id"] == "RID-Z"
    assert e["reason"] == "lane 'apply' is already held"
    assert e["reason_class"] == ""        # untyped by default (deferred)


def test_refuse_entry_explicit_lane_wins():
    """An explicit lane arg overrides the decision's lane (the caller knows the
    requested lane even when the arbiter blanked it on an auto-pick refuse)."""
    from dos.lane_journal import refuse_entry

    d = _StubDecision(reason="no free lane", lane="")
    e = refuse_entry(d, owner="w", lane="src")
    assert e["lane"] == "src"


def test_refuse_is_not_state_mutating():
    """OP_REFUSE is excluded from the state-mutating set (mirrors HALT) — a denied
    request grants nothing, so replay ignores it and it can never lose a lease."""
    assert OP_REFUSE not in _STATE_MUTATING_OPS


def test_replay_acquire_then_refuse_leaves_one_lease():
    """replay([ACQUIRE(other), REFUSE]) yields exactly the prior live lease — the
    REFUSE folds to nothing (it records a denied request, not a grant)."""
    from dos.lane_journal import refuse_entry

    entries = [
        acquire_entry(_LEASE),
        refuse_entry(_StubDecision("held", "apply"), owner="latecomer",
                     loop_ts="2026-06-01T14:30Z"),
    ]
    live = replay(entries)
    assert len(live) == 1
    assert live[0]["lane"] == "apply"


# ── enforce_entry (docs/189 §C4) — the PRODUCER for OP_ENFORCE ────────────────


class _StubProposal:
    """Duck-types dos.enforce.EffectProposal: a .to_dict() is all the builder reads."""

    def __init__(self, intervention, dispatch_call, *, synthetic=None,
                 handler="", note="", reason=""):
        self._d = {
            "intervention": intervention,
            "dispatch_call": dispatch_call,
            "synthetic_result": synthetic,
            "note": note,
            "handler": handler,
            "reason": reason,
        }

    def to_dict(self):
        return dict(self._d)


def test_enforce_entry_op_and_lifted_fields():
    """enforce_entry stamps OP_ENFORCE, lifts the rung + dispatch flag to the top
    level for cheap filtering, threads correlation (owner/lane/run_id/tool), and
    stores the full proposal body. Mirrors refuse_entry's duck-typed contract."""
    p = _StubProposal("BLOCK", False, synthetic={"dos_blocked": True},
                      handler="blocker", reason="HIGH mint")
    e = enforce_entry(p, owner="agent-1", lane="src", loop_ts="2026-06-06T05:00Z",
                      host_id="host-a", run_id="r-123", tool="create_incident")
    assert e["op"] == OP_ENFORCE
    assert e["intervention"] == "BLOCK"      # lifted from the proposal body
    assert e["dispatch_call"] is False
    assert e["withheld"] is True             # the complement, for "did the call fire?"
    assert e["handler"] == "blocker"
    assert e["holder"] == "agent-1"          # owner recorded as holder (refuse idiom)
    assert e["lane"] == "src"
    assert e["loop_ts"] == "2026-06-06T05:00Z"
    assert e["host_id"] == "host-a"
    assert e["run_id"] == "r-123"
    assert e["tool"] == "create_incident"
    assert e["reason"] == "HIGH mint"
    assert e["proposal"]["synthetic_result"] == {"dos_blocked": True}


def test_enforce_entry_observe_dispatches():
    """An OBSERVE proposal dispatches the call → dispatch_call True, withheld False."""
    p = _StubProposal("OBSERVE", True, handler="observe")
    e = enforce_entry(p, owner="a", tool="read_file")
    assert e["intervention"] == "OBSERVE"
    assert e["dispatch_call"] is True
    assert e["withheld"] is False


def test_enforce_entry_accepts_a_real_effect_proposal():
    """The builder reads a real dos.enforce.EffectProposal via .to_dict(), no import
    of enforce inside lane_journal (the pure-leaf discipline holds end-to-end)."""
    from dos.enforce import EffectProposal
    from dos.intervention import Intervention

    p = EffectProposal(intervention=Intervention.WARN, dispatch_call=True,
                       note="advisory", handler="warner")
    e = enforce_entry(p, owner="a", run_id="r9")
    assert e["intervention"] == "WARN"       # the enum value, lifted to a plain str
    assert e["dispatch_call"] is True
    assert e["handler"] == "warner"


def test_enforce_entry_lifts_reason_class_to_top_level():
    """The TYPED refusal token is lifted to the top level — the regression for the
    forensic-recoverability gap. A SELF_MODIFY block came in via a raw-dict body
    (the `cli._journal_pretool_outcome` path, which builds `body['reason_class']`);
    the builder must surface that token at the top level where the decisions queue
    and `picker_oracle.resolve_cause` read it, NOT bury it only inside `proposal`.
    Before the fix every OP_ENFORCE carried top-level `reason_class: None`, so 597
    real SELF_MODIFY refusals read as UNCLASSIFIED to every recovery fold."""
    body = {
        "intervention": "BLOCK",
        "dispatch_call": False,
        "handler": "admission",
        "reason": "would edit the orchestrator's own running code (SELF_MODIFY)",
        "reason_class": "SELF_MODIFY",
    }
    e = enforce_entry(body, owner="S1", lane="Write", tool="Write")
    assert e["reason_class"] == "SELF_MODIFY"        # lifted to the top level
    assert e["proposal"]["reason_class"] == "SELF_MODIFY"  # still in the body too


def test_enforce_entry_reason_class_degrades_to_empty():
    """An ENFORCE outcome with no typed token degrades to `""` at the top level —
    the same graceful-empty contract `refuse_entry` keeps (never a missing key,
    never a None that a token reader must special-case)."""
    p = _StubProposal("OBSERVE", True, handler="observe")  # no reason_class
    e = enforce_entry(p, owner="a", tool="read_file")
    assert e["reason_class"] == ""


def test_enforce_is_not_state_mutating():
    """OP_ENFORCE is excluded from the state-mutating set (mirrors REFUSE/HALT) —
    an enforcement outcome grants/removes no lease, so replay can never lose one."""
    assert OP_ENFORCE not in _STATE_MUTATING_OPS


def test_replay_acquire_then_enforce_leaves_one_lease():
    """replay([ACQUIRE(L), ENFORCE]) yields exactly the prior live lease — the
    ENFORCE folds to nothing (it records an enforcement outcome, not a grant)."""
    p = _StubProposal("BLOCK", False, handler="blocker")
    entries = [
        acquire_entry(_LEASE),
        enforce_entry(p, owner="agent-1", lane="apply", run_id="r-1",
                      tool="create_incident"),
    ]
    live = replay(entries)
    assert len(live) == 1
    assert live[0]["lane"] == "apply"


def test_replay_enforce_alone_yields_no_lease():
    """An ENFORCE with no surrounding ACQUIRE folds to the empty set — it never
    invents a lease (the state-neutral guarantee, the REFUSE/HALT discipline)."""
    p = _StubProposal("DEFER", False, handler="deferrer")
    assert replay([enforce_entry(p, owner="a", tool="t")]) == []


# ── OP_CHECKPOINT + compact (LJ compaction) — the verdict-preserving bound ────


def test_checkpoint_is_not_state_mutating():
    """OP_CHECKPOINT is NOT in _STATE_MUTATING_OPS — replay handles it as a
    special RESET branch (placed before the gate), not an incremental op."""
    assert OP_CHECKPOINT not in _STATE_MUTATING_OPS


def test_compact_preserves_replay_equivalence():
    """THE WAL-SAFETY HERO: for a sequence INCLUDING a still-live old ACQUIRE,
    replay(compact(E)) == replay(E). The still-live lease survives via the
    checkpoint snapshot — the catastrophic lost-live-lease bug a naive
    truncate-old-lines would cause is foreclosed."""
    from dos.lane_journal import compact, release_entry

    other = dict(_LEASE, lane="docs", loop_ts="2026-06-01T15:00Z")
    entries = [
        acquire_entry(_LEASE),                                   # stays LIVE
        acquire_entry(other),
        heartbeat_entry(_LEASE, heartbeat_at="2026-06-01T14:05Z"),
        release_entry(other),                                    # dead history
        scavenge_entry(dict(_LEASE, lane="x", loop_ts="t-x")),   # evicts nothing live
        {"op": OP_REFUSE, "lane": "y", "loop_ts": "t-y", "holder": "z"},
    ]
    compacted = compact(entries)
    before = replay(entries)
    after = replay(compacted)
    assert before == after
    assert len(before) == 1 and before[0]["lane"] == "apply"
    # NON-VACUITY: equivalence alone passes under a `compact() == identity`
    # mutation. Assert compaction actually FOLDED to a CHECKPOINT (and shrank the
    # 6-entry input), so an identity no-op can't masquerade as a passing hero.
    assert any(e.get("op") == OP_CHECKPOINT for e in compacted)
    assert len(compacted) < len(entries)


def test_compact_is_pure(monkeypatch):
    """compact does no I/O and reads no clock — poison the I/O surfaces (the
    test_fold_is_pure discipline) and assert a clean fold still returns."""
    import builtins
    import subprocess
    import time as _time

    from dos.lane_journal import compact

    def _boom(*a, **k):  # pragma: no cover - only runs if purity is violated
        raise AssertionError("compact performed I/O — it must be pure")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(builtins, "open", _boom)
    monkeypatch.setattr(_time, "time", _boom)

    out = compact([acquire_entry(_LEASE)])
    assert out and out[0]["op"] == OP_CHECKPOINT


def test_compact_preserves_corrupt_sentinel():
    """A _CORRUPT sentinel in the input SURVIVES into compact's output — a
    mid-file integrity breach is real signal a rewrite must not silently erase."""
    from dos.lane_journal import compact

    entries = [
        acquire_entry(_LEASE),
        {"op": "_CORRUPT", "_raw": "{bad", "_line": 1},
    ]
    out = compact(entries)
    assert any(e.get("op") == "_CORRUPT" for e in out)
    assert any(e.get("op") == OP_CHECKPOINT for e in out)


def test_replay_checkpoint_resets_then_folds_tail():
    """replay([CHECKPOINT(leases=[L1, L2]), RELEASE(L1), ACQUIRE(L3)]) == [L2, L3]
    — the checkpoint RESETS the live set to its payload, then the tail folds onto
    it (a release drops L1, an acquire adds L3)."""
    from dos.lane_journal import checkpoint_entry

    l1 = dict(_LEASE, lane="a", loop_ts="t-a")
    l2 = dict(_LEASE, lane="b", loop_ts="t-b")
    l3 = dict(_LEASE, lane="c", loop_ts="t-c")
    entries = [
        checkpoint_entry([l1, l2], seq_watermark=10),
        {"op": OP_SCAVENGE, "lane": "a", "loop_ts": "t-a"},  # RELEASE-equiv on L1
        acquire_entry(l3),
    ]
    live = replay(entries)
    lanes = sorted(l["lane"] for l in live)
    assert lanes == ["b", "c"]


def test_checkpoint_rebase_discards_prior_fold():
    """A CHECKPOINT mid-stream DISCARDS whatever was folded before it (a re-base,
    not a merge): replay([ACQUIRE(L1), CHECKPOINT([L2])]) == [L2] — L1 is gone."""
    from dos.lane_journal import checkpoint_entry

    l1 = dict(_LEASE, lane="a", loop_ts="t-a")
    l2 = dict(_LEASE, lane="b", loop_ts="t-b")
    live = replay([acquire_entry(l1), checkpoint_entry([l2], seq_watermark=5)])
    assert [l["lane"] for l in live] == ["b"]


def test_next_seq_monotonic_across_compact(tmp_path):
    """next_seq after a compaction is >= before, via the checkpoint's
    seq_watermark — even though the rewrite discarded the lines holding the prior
    high-water mark. A compacted file must never let the next append REUSE a seq."""
    from dos.lane_journal import append, compact, next_seq, read_all

    p = tmp_path / "j.jsonl"
    for i in range(1, 11):  # seqs 1..10
        append({"op": OP_ACQUIRE, "lane": f"L{i}", "loop_ts": f"t{i}",
                "lease": {"lane": f"L{i}", "loop_ts": f"t{i}"}, "seq": i}, p)
    seq_before = next_seq(p)
    assert seq_before == 11

    # Compact and rewrite: the single CHECKPOINT replaces all 10 lines.
    import json
    compacted = compact(read_all(p))
    p.write_text("".join(json.dumps(e, sort_keys=True, default=str) + "\n"
                         for e in compacted), encoding="utf-8")
    seq_after = next_seq(p)
    assert seq_after >= seq_before  # never reuses a seq from the discarded prefix
    assert seq_after == 11          # the watermark (10) + 1


def test_torn_tail_preserved_after_compact(tmp_path):
    """A torn final line appended to a post-compaction file still reads cleanly —
    read_all's trailing-skip survives the rewrite (the WAL torn-tail rule)."""
    from dos.lane_journal import append, compact, read_all

    p = tmp_path / "j.jsonl"
    append({"op": OP_ACQUIRE, "lane": "L1", "loop_ts": "t1",
            "lease": {"lane": "L1", "loop_ts": "t1"}}, p)
    import json
    compacted = compact(read_all(p))
    body = "".join(json.dumps(e, sort_keys=True, default=str) + "\n"
                   for e in compacted)
    p.write_text(body + '{"op": "ACQUIRE", "lane": "L2"', encoding="utf-8")  # torn
    entries = read_all(p)
    # The torn trailing line is skipped; the checkpoint reads cleanly.
    assert any(e.get("op") == OP_CHECKPOINT for e in entries)
    assert all(e.get("lane") != "L2" for e in entries)


def test_tail_docstring_no_false_rotation_claim():
    """DOCSTRING-HONESTY PIN: the false 'size-rotated in LJ2' claim is gone from
    tail.__doc__ (the journal is NOT auto-rotated; `dos journal compact` bounds
    it). Guards against the doc-vs-code drift from re-creeping back in."""
    from dos.lane_journal import tail

    assert "size-rotated in LJ2" not in (tail.__doc__ or "")


# ── JOURNAL_PATH is resolved LAZILY, not at import (docs/275) ────────────────
#
# The module-level `JOURNAL_PATH` used to be an eager `Path(... or
# _default_journal_path())`, and `_default_journal_path()` calls
# `config.active()` → `default_config()` → the git-SHA subprocess + the WMI
# platform probe in `gather_env_print`. So merely `import dos` (which imports
# lane_journal) paid that ~tens-of-ms cost at import. docs/275 made it a PEP 562
# module `__getattr__` that resolves on first ACCESS instead. These pin that the
# lazy contract holds AND the name still resolves correctly when read.


def test_journal_path_is_not_materialized_at_import():
    """`JOURNAL_PATH` is resolved lazily — it is NOT a key in the module __dict__
    until accessed (PEP 562 `__getattr__`), so `import dos` does not force the
    config build the eager assignment used to trigger."""
    import dos.lane_journal as lj

    # The eager `JOURNAL_PATH = Path(...)` would have stamped the name into the
    # module dict at import; the lazy `__getattr__` does not. (Accessing it via
    # `lj.JOURNAL_PATH` would resolve it, so check the raw dict instead.)
    assert "JOURNAL_PATH" not in vars(lj), (
        "JOURNAL_PATH is materialized at import — the docs/275 lazy resolution "
        "regressed (import dos would re-pay the config-build cost)")
    assert callable(getattr(lj, "__getattr__", None)), \
        "the PEP 562 module __getattr__ that resolves JOURNAL_PATH lazily is gone"


def test_journal_path_resolves_on_access_equals_journal_path_fn():
    """Reading `lane_journal.JOURNAL_PATH` returns the live `_journal_path()` — the
    lazy handle resolves to the same path the functions use, so the back-compat
    name (`from dos.lane_journal import *`, the host re-export shims) still works."""
    import dos.lane_journal as lj

    assert lj.JOURNAL_PATH == lj._journal_path()
    # An unknown attribute still raises AttributeError (the __getattr__ guards only
    # JOURNAL_PATH) — not a silent None that would mask a typo'd import.
    import pytest

    with pytest.raises(AttributeError):
        _ = lj.THIS_NAME_DOES_NOT_EXIST


def test_importing_dos_does_not_build_the_config():
    """A fresh `import dos` must NOT build the workspace config at import time.

    The eager `JOURNAL_PATH = Path(... or _default_journal_path())` used to call
    `config.active()` → `default_config()` → `gather_env_print()` during import,
    which populates the per-process EnvPrint memo as a side effect. So the
    observable proof that the lazy fix holds: right after a CLEAN `import dos` (and
    `import dos.lane_journal`), the env-print memo is EMPTY — nothing built a config
    yet. Runs in a subprocess so the import is genuinely the first one.
    """
    import subprocess
    import sys

    prog = (
        "import dos\n"
        "import dos.lane_journal\n"                 # the module that used to force it
        "import dos.env_print as e\n"
        # If import had built a config, gather_env_print would have run and cached.
        "assert e._GATHER_CACHE == {}, "
        "    'import dos populated the EnvPrint memo — JOURNAL_PATH eager-build regressed'\n"
        # And accessing JOURNAL_PATH NOW resolves it lazily (and is still correct).
        "from pathlib import Path\n"
        "assert isinstance(dos.lane_journal.JOURNAL_PATH, Path)\n"
        "print('OK')\n"
    )
    res = subprocess.run([sys.executable, "-c", prog],
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, (
        f"import dos triggered the config build at import:\n{res.stdout}\n{res.stderr}")
    assert res.stdout.strip().endswith("OK"), res.stdout


# ── spawn_entry (the dos-top SPAWN→ACQUIRE visibility gap) ───────────────────
# A SPAWN is an INTENT TO TAKE A LANE recorded before preflight, so dos top sees a
# loop the instant it commits to a lane. It grants NO lease (non-state-mutating),
# so a never-acquired intent can never strand a phantom hold / double-book a region.


def test_spawn_entry_op_and_keys():
    """spawn_entry stamps OP_SPAWN + the identity tuple (lane/loop_ts/holder/host_id/
    pid) that joins SPAWN→ACQUIRE, and carries run_id only when given."""
    e = spawn_entry(lane="apply", loop_ts="2026-06-01T14:00Z", holder="h:1",
                    host_id="host-a", pid=4242, run_id="RID-x", reason="launch")
    assert e["op"] == OP_SPAWN
    assert e["lane"] == "apply"
    assert e["loop_ts"] == "2026-06-01T14:00Z"
    assert e["holder"] == "h:1"
    assert e["host_id"] == "host-a"
    assert e["pid"] == 4242
    assert e["run_id"] == "RID-x"
    assert e["reason"] == "launch"


def test_spawn_entry_omits_run_id_when_absent():
    """No run_id => no run_id key (a pre-join SPAWN replays/serializes unchanged)."""
    e = spawn_entry(lane="apply")
    assert "run_id" not in e
    assert e["op"] == OP_SPAWN and e["lane"] == "apply"


def test_spawn_is_not_state_mutating():
    """OP_SPAWN is excluded from the state-mutating set (mirrors REFUSE/HALT) — an
    intent grants nothing, so replay ignores it and it can never invent a lease."""
    assert OP_SPAWN not in _STATE_MUTATING_OPS


def test_replay_ignores_spawn_for_state():
    """replay([SPAWN]) folds to the EMPTY live set — the safety property: a
    not-yet-real run can never double-book a region (docs/281 mode is impossible)."""
    assert replay([spawn_entry(lane="apply", loop_ts="L")]) == []


def test_replay_spawn_then_acquire_yields_exactly_one_lease():
    """SPAWN→ACQUIRE on the same lane folds to ONE lease (the SPAWN adds nothing to
    the live set; the ACQUIRE is the sole grant)."""
    spawn = spawn_entry(lane="apply", loop_ts="2026-06-01T14:00Z", holder="h:1")
    acq = acquire_entry(_LEASE)
    live = replay([spawn, acq])
    assert len(live) == 1
    assert live[0]["lane"] == "apply"


def test_replay_spawn_then_release_leaves_no_lease():
    """A launch that records a SPAWN then aborts (RELEASE, no intervening ACQUIRE)
    leaves the live set empty — the SPAWN never created anything to remove."""
    spawn = spawn_entry(lane="apply", loop_ts="2026-06-01T14:00Z")
    assert replay([spawn, release_entry(_LEASE)]) == []


# ── proc_starttime: the PID-reuse baseline rides through the WAL (docs/95 §4.2) ──


def test_acquire_carries_proc_starttime_through_replay():
    """A lease minted with proc_starttime replays WITH it — the baseline travels on
    the nested lease dict, so the reclaim/liveness probe can corroborate identity."""
    lease = dict(_LEASE, proc_starttime=987654)
    live = replay([acquire_entry(lease)])
    assert len(live) == 1
    assert live[0]["proc_starttime"] == 987654


def test_acquire_without_proc_starttime_replays_unchanged():
    """No baseline (a host that can't read it) ⇒ the key is simply absent — the
    forward-compat contract, byte-identical to before this field existed."""
    live = replay([acquire_entry(_LEASE)])
    assert "proc_starttime" not in live[0]


def test_inline_acquire_carries_proc_starttime():
    """The forward-compat inline path (an ACQUIRE with fields flattened, no nested
    `lease`) must also reconstruct proc_starttime from the known-keys list."""
    inline = {
        "op": OP_ACQUIRE,
        "lane": "apply", "lane_kind": "concurrent", "tree": ["a/*"],
        "loop_ts": "t1", "host_id": "host-a", "pid": 4242,
        "acquired_at": "2026-06-01T14:00:03Z", "proc_starttime": 555,
    }
    live = replay([inline])
    assert len(live) == 1 and live[0]["proc_starttime"] == 555


def test_compact_preserves_replay_equivalence_with_proc_starttime():
    """The differential invariant holds with the new field: replay(compact(E)) ==
    replay(E) for a still-live lease that carries proc_starttime (the checkpoint
    snapshot must round-trip the baseline, not silently drop it)."""
    from dos.lane_journal import compact

    lease = dict(_LEASE, proc_starttime=424242)
    entries = [acquire_entry(lease), heartbeat_entry(lease, heartbeat_at="2026-06-01T14:05Z")]
    before = replay(entries)
    after = replay(compact(entries))
    assert before == after
    assert before[0]["proc_starttime"] == 424242


def test_adopt_with_new_baseline_rewrites_proc_starttime():
    """ADOPT to a new holder/pid WITH a supplied baseline rewrites proc_starttime to
    the new holder's stamp — so the reuse defense keeps corroborating the LIVE pid."""
    from dos.lane_journal import adopt_entry

    lease = dict(_LEASE, proc_starttime=111)
    adopt = adopt_entry(lease, new_holder="host-b:99", new_pid=99,
                        new_host_id="host-a", new_proc_starttime=222)
    live = replay([acquire_entry(lease), adopt])
    assert len(live) == 1
    assert live[0]["pid"] == 99
    assert live[0]["proc_starttime"] == 222


def test_adopt_changing_pid_without_baseline_drops_stale_starttime():
    """ADOPT that changes the pid but supplies NO new baseline must DROP the old
    holder's proc_starttime (degrade to existence-only), never keep a stale stamp
    that would falsely read the new pid as a PID-reuse mismatch."""
    from dos.lane_journal import adopt_entry

    lease = dict(_LEASE, proc_starttime=111)
    adopt = adopt_entry(lease, new_holder="host-b:99", new_pid=99, new_host_id="host-a")
    live = replay([acquire_entry(lease), adopt])
    assert len(live) == 1
    assert live[0]["pid"] == 99
    assert "proc_starttime" not in live[0]
