"""Fold a root's CHILDREN's structural refusals out of the WAL (docs/354, #189).

A dispatched subagent that is **structurally refused** — a PreToolUse hook DENY, an
admission refusal — leaves its typed refusal in its OWN tool-call stream
(`permissionDecision: deny`). That deny is ALSO journaled (the pretool writer
appends an `OP_ENFORCE` record), but a parent/supervisor that dispatched the child
cannot fold "the refusals under MY root": the child inherits its parent's
`CID_ROOT_ID`/`CID_PARENT_ID` across the `claude -p` boundary yet mints its OWN
`CID_RUN_ID` (`run_id.mint_child_from_env`), so a record keyed on `run_id` alone is
an opaque per-child id the parent never learns. Without a lineage-keyed read, a
blocked child is indistinguishable from a finished one — the gap that let #188 stay
hidden until it was empirically reproduced.

docs/354 closed the WRITE half: the WAL records the pretool sensor already writes
now carry `root_id`/`parent_id` when the actor has lineage (`lane_journal.enforce_entry`
/ `refuse_entry`, stamped by `cli._journal_pretool_outcome`). This module is the
READ half: `fold_child_refusals(entries, root_id=...)` resolves

    "child RID-X under root RID-ROOT was refused N times with reason_class=…"

from the WAL ALONE — never the child's transcript. A parent answers "is any child
of mine blocked?" by passing its own run-id/root-id as `root_id`.

Layer-1 PURE projection (the `status.py` posture): the WAL read is the caller's I/O;
the fold is the unit-test surface. Names no host. The lineage taxonomy (`CID_*` /
`run_id`/`root_id`/`parent_id`) is the correlation spine's, already kernel-owned.
Delete this module and you lose the reader, not any data.

This is NOT the `decisions._from_enforce_storms` fold. That answers the OPERATOR's
question ("an agent keeps hitting a SELF_MODIFY wall; a human must act", the breaker
HUMAN rung). This answers the DISPATCHER's question ("which child I launched is
blocked, by what, right now") so it can re-route / re-lease / escalate — the
supervisor's read, not the operator queue's. Same WAL source, different rung.
"""
from __future__ import annotations

from dataclasses import dataclass

from dos import lane_journal

# The durable_schema floor (docs/116 §6): a record other tools read carries a
# schema tag, so a consumer that understands an older shape refuses a newer one
# rather than misparsing it.
CHILD_REFUSALS_SCHEMA = 1

# The forensic refusal ops a child's structural block lands as. An `OP_ENFORCE`
# with a BLOCK/deny shape is the PreToolUse hook DENY (the pretool writer's record);
# an `OP_REFUSE` is an admission refusal. Both are NON-state-mutating (`replay`
# ignores them), so reading them adds history and grants nothing.
_REFUSAL_OPS = frozenset({lane_journal.OP_ENFORCE, lane_journal.OP_REFUSE})


@dataclass(frozen=True)
class ChildRefusal:
    """One child's structural refusal, folded and keyed by lineage (docs/354 §P3).

    The typed, lineage-keyed record the issue's done-condition names. Every field is
    a WAL fact, never the child's self-narrated "I'm blocked":

      child_run_id — the refused child's OWN minted run-id (the `CID_RUN_ID` it set).
      parent_id    — the run that dispatched it (its `CID_PARENT_ID`), or "" if the
                     record carried only a root.
      root_id      — the root this refusal folds under (the queried lineage key).
      reason_class — the typed refusal token (SELF_MODIFY / SCOPE_ESCAPE / …), or ""
                     for a contention refusal that carried none.
      tool         — the tool/target the refusal was about (the `tool`/`lane` field).
      count        — how many identical refusals this child accumulated (a loop
                     retrying the same blocked edit lands N records; they fold to one
                     row carrying the count, the same posture as `decisions._dedup`).
      latest_ts    — the newest refusal's journal timestamp, for an age read.
    """

    child_run_id: str
    parent_id: str
    root_id: str
    reason_class: str
    tool: str
    count: int = 1
    latest_ts: str = ""

    def to_dict(self) -> dict:
        return {
            "child_run_id": self.child_run_id,
            "parent_id": self.parent_id,
            "root_id": self.root_id,
            "reason_class": self.reason_class,
            "tool": self.tool,
            "count": self.count,
            "latest_ts": self.latest_ts,
        }


def _is_deny(entry: dict) -> bool:
    """True iff this forensic record is a REFUSAL (a deny), not a WARN/observe. Pure.

    An `OP_REFUSE` is always a denied request. An `OP_ENFORCE` is a refusal only when
    its outcome WITHHELD the call — the pretool writer records that as
    `dispatch_call == False` / `withheld == True` / `intervention == "BLOCK"` (or a
    nested `proposal.decision == "deny"`). A WARN-and-pass `OP_ENFORCE`
    (`dispatch_call == True`) is NOT a block — the child was not refused, so it must
    not surface as one (the negative-pin discipline: never a false "blocked" signal).
    """
    op = entry.get("op")
    if op == lane_journal.OP_REFUSE:
        return True
    if op != lane_journal.OP_ENFORCE:
        return False
    if entry.get("withheld") is True or entry.get("dispatch_call") is False:
        return True
    if str(entry.get("intervention") or "").strip().upper() == "BLOCK":
        return True
    proposal = entry.get("proposal")
    if isinstance(proposal, dict) and str(proposal.get("decision") or "").strip().lower() == "deny":
        return True
    return False


def fold_child_refusals(
    entries: list[dict],
    *,
    root_id: str,
) -> tuple[ChildRefusal, ...]:
    """Fold a root's CHILDREN's structural refusals out of WAL `entries`. PURE.

    `entries` is the WAL read at the caller boundary (`lane_journal.read_all`);
    `root_id` is the lineage key — a parent passes its OWN run-id/root-id to ask
    "which of my children is blocked." Returns one `ChildRefusal` per
    `(child_run_id, reason_class, tool)` group, sorted for a stable read.

    The lineage discipline, three ways (docs/354 done-condition):
      * Only records whose `root_id` equals the queried root are folded — a refusal
        under a DIFFERENT root never surfaces here.
      * The root's OWN refusals are EXCLUDED (`run_id == root_id`): a parent wants its
        CHILDREN's blocks, not its own. (A child's `run_id` is its own minted id, ≠
        the root, so a genuine child is kept.)
      * Only DENY-shaped records count (`_is_deny`): a clean child that was never
        refused — or one that only WARNed — produces NOTHING, so a finished child is
        never mistaken for a blocked one.

    Reads ONLY the WAL — never a child's tool-call stream. Degrades to an empty
    tuple on a malformed entry list (a read-only projection never crashes a caller).
    """
    want_root = str(root_id or "").strip()
    if not want_root:
        return ()
    # (child_run_id, reason_class, tool) -> accumulated [parent_id, count, latest_ts].
    groups: dict[tuple[str, str, str], list] = {}
    try:
        ordered = list(entries)
    except TypeError:
        return ()
    for e in ordered:
        if not isinstance(e, dict):
            continue
        if e.get("op") not in _REFUSAL_OPS:
            continue
        if str(e.get("root_id") or "").strip() != want_root:
            continue
        child = str(e.get("run_id") or "").strip()
        if not child or child == want_root:
            # No child id, or the root's own refusal — not a child block.
            continue
        if not _is_deny(e):
            continue
        reason_class = str(e.get("reason_class") or "").strip()
        tool = str(e.get("tool") or e.get("lane") or "").strip()
        parent = str(e.get("parent_id") or "").strip()
        ts = str(e.get("ts") or "").strip()
        key = (child, reason_class, tool)
        slot = groups.get(key)
        if slot is None:
            groups[key] = [parent, 1, ts]
        else:
            slot[1] += 1
            # Keep the newest timestamp (lexicographic compare is chronological for
            # the ISO-8601 / journal_now_iso stamps the WAL writes).
            if ts > slot[2]:
                slot[2] = ts
            # A later record may carry the parent a first one lacked.
            if parent and not slot[0]:
                slot[0] = parent
    out = [
        ChildRefusal(
            child_run_id=child,
            parent_id=parent,
            root_id=want_root,
            reason_class=reason_class,
            tool=tool,
            count=count,
            latest_ts=ts,
        )
        for (child, reason_class, tool), (parent, count, ts) in groups.items()
    ]
    # Stable order: newest refusal first, then by child id for determinism.
    out.sort(key=lambda r: (r.latest_ts, r.child_run_id), reverse=True)
    return tuple(out)
