"""Tests for the lineage-keyed child-refusal fold (docs/354, issue #189).

A dispatched subagent that is structurally refused leaves its typed refusal only
in its OWN tool-call stream. docs/354 makes that refusal legible UP THE LINEAGE: the
WAL records the pretool sensor writes now carry the child's `root_id`/`parent_id`, and
`child_refusals.fold_child_refusals(root_id=…)` resolves a parent's children's blocks
from the WAL alone.

The load-bearing tests are the issue's done-condition, both halves:

  * POSITIVE — `test_parent_reads_child_block_by_root_without_transcript`: a child run
    records a structural refusal (journaled through the REAL pretool writer path), and a
    parent-side fold keyed on `CID_ROOT_ID` resolves it — reason_class, tool, count —
    reading ONLY the WAL, never the child's stream.
  * NEGATIVE — `test_clean_child_produces_no_refusal_report`: a child that finished
    cleanly (no DENY) produces NOTHING, so a finished child is never mistaken for a
    blocked one.

Plus the lineage-discrimination pins (a different root / the parent's own refusals do
not surface) and the additive-shape pin (a record built with no lineage is byte-identical
to the pre-#189 shape, and `replay` ignores both ops for state regardless).
"""
from __future__ import annotations

import json
from pathlib import Path

from dos import child_refusals as cr
from dos import lane_journal as lj
from dos.config import default_config


# ---------------------------------------------------------------------------
# Helpers — build the WAL records the way the kernel builds them.
# ---------------------------------------------------------------------------


def _child_deny(run_id, root_id, *, parent_id="", reason_class="SCOPE_ESCAPE",
                tool="Edit", ts="2026-06-16T00:00:00Z"):
    """An OP_ENFORCE DENY (withheld call) the way the pretool writer records one."""
    e = lj.enforce_entry(
        {"intervention": "BLOCK", "reason_class": reason_class,
         "reason": "out-of-lane write", "dispatch_call": False},
        run_id=run_id, root_id=root_id, parent_id=parent_id, tool=tool,
    )
    e["ts"] = ts
    return e


def _child_warn(run_id, root_id, *, tool="Write", ts="2026-06-16T00:00:00Z"):
    """A WARN-and-pass OP_ENFORCE — NOT a block (dispatch_call True)."""
    e = lj.enforce_entry(
        {"intervention": "WARN", "reason": "soft", "dispatch_call": True},
        run_id=run_id, root_id=root_id, tool=tool,
    )
    e["ts"] = ts
    return e


# ---------------------------------------------------------------------------
# The pure fold — the done-condition, both halves.
# ---------------------------------------------------------------------------


class TestFoldChildRefusals:
    def test_parent_reads_child_block_by_root_without_transcript(self):
        """POSITIVE: a child DENY under root R resolves via the root key, WAL-only."""
        wal = [_child_deny("RID-CHILD", "RID-ROOT", parent_id="RID-ROOT")]
        rows = cr.fold_child_refusals(wal, root_id="RID-ROOT")
        assert len(rows) == 1
        r = rows[0]
        assert r.child_run_id == "RID-CHILD"
        assert r.root_id == "RID-ROOT"
        assert r.parent_id == "RID-ROOT"
        assert r.reason_class == "SCOPE_ESCAPE"
        assert r.tool == "Edit"
        assert r.count == 1

    def test_clean_child_produces_no_refusal_report(self):
        """NEGATIVE: a child that only WARNed (never blocked) surfaces NOTHING."""
        wal = [_child_warn("RID-CLEAN", "RID-ROOT")]
        assert cr.fold_child_refusals(wal, root_id="RID-ROOT") == ()

    def test_empty_wal_is_empty(self):
        assert cr.fold_child_refusals([], root_id="RID-ROOT") == ()

    def test_identical_blocks_fold_to_one_row_with_count(self):
        """A loop retrying the same blocked edit folds to one row carrying the count."""
        wal = [
            _child_deny("RID-C", "RID-ROOT", ts="2026-06-16T00:00:00Z"),
            _child_deny("RID-C", "RID-ROOT", ts="2026-06-16T00:05:00Z"),
            _child_deny("RID-C", "RID-ROOT", ts="2026-06-16T00:09:00Z"),
        ]
        rows = cr.fold_child_refusals(wal, root_id="RID-ROOT")
        assert len(rows) == 1
        assert rows[0].count == 3
        # latest_ts is the NEWEST of the group.
        assert rows[0].latest_ts == "2026-06-16T00:09:00Z"

    def test_refusal_under_a_different_root_does_not_surface(self):
        """Lineage discrimination: only the queried root's children fold."""
        wal = [
            _child_deny("RID-C", "RID-ROOT"),
            _child_deny("RID-E", "RID-OTHER"),
        ]
        rows = cr.fold_child_refusals(wal, root_id="RID-ROOT")
        assert [r.child_run_id for r in rows] == ["RID-C"]

    def test_the_roots_own_refusal_is_excluded(self):
        """A parent wants its CHILDREN's blocks, not its own (run_id == root_id)."""
        wal = [_child_deny("RID-ROOT", "RID-ROOT")]  # the root refusing itself
        assert cr.fold_child_refusals(wal, root_id="RID-ROOT") == ()

    def test_op_refuse_admission_block_also_surfaces(self):
        """An OP_REFUSE (admission refusal), not just an OP_ENFORCE DENY, is a block."""
        class _D:
            reason = "lane held"
            lane = "src"
        e = lj.refuse_entry(_D(), owner="sess", run_id="RID-C",
                            root_id="RID-ROOT", reason_class="CONTENTION")
        e["ts"] = "2026-06-16T00:00:00Z"
        rows = cr.fold_child_refusals([e], root_id="RID-ROOT")
        assert len(rows) == 1 and rows[0].reason_class == "CONTENTION"

    def test_empty_root_id_query_is_empty(self):
        wal = [_child_deny("RID-C", "RID-ROOT")]
        assert cr.fold_child_refusals(wal, root_id="") == ()

    def test_malformed_entries_do_not_crash(self):
        wal = [None, 42, "garbage", {"op": "REFUSE"},  # no run_id/root_id
               _child_deny("RID-C", "RID-ROOT")]
        rows = cr.fold_child_refusals(wal, root_id="RID-ROOT")
        assert [r.child_run_id for r in rows] == ["RID-C"]


# ---------------------------------------------------------------------------
# End-to-end through the REAL pretool writer — the WAL the parent reads is the
# one the child's hook DENY actually lands in (not a hand-built fixture).
# ---------------------------------------------------------------------------


class TestPretoolWriterStampsLineage:
    def test_child_deny_journaled_with_lineage_then_folded_by_root(
            self, tmp_path: Path, monkeypatch):
        """The full #189 path: a child's hook DENY → WAL with lineage → parent fold.

        Drives the REAL `cli._journal_pretool_outcome`, with the child's CID_* env set
        the way `mint_child_from_env` inherits it across the `claude -p` boundary, then
        reads the WAL back and folds it by the ROOT — never the child's transcript.
        """
        from dos import cli

        cfg = default_config(tmp_path)
        # The child inherits root/parent but mints its OWN run-id (the #188/#189 shape).
        monkeypatch.setenv("CID_RUN_ID", "RID-CHILD")
        monkeypatch.setenv("CID_ROOT_ID", "RID-ROOT")
        monkeypatch.setenv("CID_PARENT_ID", "RID-PARENT")

        event = {"tool_name": "Edit", "session_id": "child-sess"}
        outcome = {
            "rung": "apply-gate", "decision": "deny",
            "reason_class": "SCOPE_ESCAPE",
            "reason": "write escapes the held lane",
        }
        cli._journal_pretool_outcome(event, outcome, cfg)

        # Read the WAL back — the only I/O a parent does. NOT the child's stream.
        wal = lj.read_all(path=cfg.paths.lane_journal)
        rows = cr.fold_child_refusals(wal, root_id="RID-ROOT")
        assert len(rows) == 1
        r = rows[0]
        assert r.child_run_id == "RID-CHILD"
        assert r.parent_id == "RID-PARENT"
        assert r.reason_class == "SCOPE_ESCAPE"
        assert r.tool == "Edit"

    def test_operator_session_deny_carries_no_lineage(
            self, tmp_path: Path, monkeypatch):
        """A ROOT (operator) session has no CID_ROOT_ID → no lineage stamped.

        The record is the pre-#189 shape (no root_id/parent_id keys), so it never
        surfaces as anyone's 'child' block.
        """
        from dos import cli

        cfg = default_config(tmp_path)
        for var in ("CID_RUN_ID", "CID_ROOT_ID", "CID_PARENT_ID"):
            monkeypatch.delenv(var, raising=False)

        event = {"tool_name": "Edit", "session_id": "operator-sess"}
        outcome = {"rung": "apply-gate", "decision": "deny",
                   "reason_class": "SELF_MODIFY", "reason": "T1 edit"}
        cli._journal_pretool_outcome(event, outcome, cfg)

        wal = lj.read_all(path=cfg.paths.lane_journal)
        enforce = [e for e in wal if e.get("op") == "ENFORCE"]
        assert len(enforce) == 1
        assert "root_id" not in enforce[0]
        assert "parent_id" not in enforce[0]
        # And it folds under no root.
        assert cr.fold_child_refusals(wal, root_id="RID-ROOT") == ()


# ---------------------------------------------------------------------------
# Additive-shape pins on the entry builders — pre-#189 records unchanged.
# ---------------------------------------------------------------------------


class TestEntryBuilderLineageIsAdditive:
    def test_enforce_entry_without_lineage_has_no_new_keys(self):
        e = lj.enforce_entry({"intervention": "BLOCK", "reason": "x"},
                             run_id="RID-X", tool="Edit")
        assert "root_id" not in e and "parent_id" not in e

    def test_refuse_entry_without_lineage_has_no_new_keys(self):
        class _D:
            reason = "no"
            lane = "src"
        e = lj.refuse_entry(_D(), owner="o", run_id="RID-X")
        assert "root_id" not in e and "parent_id" not in e

    def test_lineage_stamped_only_when_non_empty(self):
        e = lj.enforce_entry({"intervention": "BLOCK"}, run_id="RID-X",
                             root_id="", parent_id="   ", tool="Edit")
        # Empty / whitespace-only lineage is NOT stamped.
        assert "root_id" not in e and "parent_id" not in e

    def test_replay_ignores_lineage_stamped_refusals_for_state(self):
        """Lineage is forensic — `replay` still grants/removes no lease."""
        e = lj.enforce_entry({"intervention": "BLOCK"}, run_id="RID-C",
                             root_id="RID-ROOT", tool="Edit")
        r = lj.refuse_entry(type("D", (), {"reason": "no", "lane": "src"})(),
                            owner="o", run_id="RID-C", root_id="RID-ROOT")
        assert lj.replay([e, r]) == []

    def test_round_trips_through_json(self):
        """The lineage fields survive a journal write/read (canonical JSON)."""
        e = _child_deny("RID-C", "RID-ROOT", parent_id="RID-P")
        back = json.loads(json.dumps(e))
        assert back["root_id"] == "RID-ROOT"
        assert back["parent_id"] == "RID-P"
