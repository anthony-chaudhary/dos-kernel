# docs/354 — children self-report structural refusals up the lineage chain

> **Status:** shipped. Lineage-stamping on the WAL records the pretool sensor
> already writes (`lane_journal.enforce_entry` / `refuse_entry` carry optional
> `root_id`/`parent_id`; `cli._journal_pretool_outcome` stamps them from the
> child's `CID_ROOT_ID`/`CID_PARENT_ID` env), a pure read leaf
> `src/dos/child_refusals.py` that folds those records by root, and a
> `dos status --children` surface. (The CODE is the ship, witnessed by tests +
> `dos commit-audit`; this 1-file doc reads NOT_SHIPPED via `none` on
> `dos verify docs/354 P1` — an evidence-horizon honesty, exactly like docs/353.)

## The one sentence

A dispatched child that is **structurally refused** (a hook DENY, an admission
refusal) leaves its typed refusal only in its **own** tool-call stream — so a
parent/supervisor cannot tell a *blocked* child from a *finished* one; this adds
the missing **report channel**: the child's refusal is stamped with its lineage
on the WAL the kernel already writes, and a parent folds those refusals **by
`CID_ROOT_ID`** without ever reading the child's transcript.

## Why this is #188's invisibility, not #188's bug

#188 was a *wrong* refusal — a subagent's in-lane edit hard-DENIED as a sibling
collision. It was fixed (commit `653689d`). But the reason #188 stayed hidden
until it was **empirically reproduced** against the live decider is orthogonal:
a child's DENY lands in the child's `permissionDecision: deny` stream, and no
structured channel carries that typed refusal **up the lineage**
(`CID_PARENT_ID` / `CID_ROOT_ID`) to the layer that dispatched it. So the parent
sees only "the child process exited" — indistinguishable across *finished*,
*structurally blocked*, and *crashed*. A child that could self-report "I was
refused with `reason_class=X` on tree Y" would have surfaced #188 the first time
it fired.

#188 stopped the wrong refusal. This makes **any** refusal legible to the
dispatcher. They are independent: a correct future refusal (a real SCOPE_ESCAPE,
a real SELF_MODIFY) must ALSO be visible up-chain, or the supervisor still can't
re-route, re-lease, or escalate.

## The spine already exists — the gap is one missing stamp + one read

The kernel already has everything this needs; nothing here is new mechanism.

- **Lineage is recorded end-to-end.** `run_id.lineage_env` exports
  `CID_RUN_ID` / `CID_ROOT_ID` / `CID_PARENT_ID`; `mint_child_from_env` inherits
  it across the `claude -p` boundary (`run_id.py`). A child KNOWS its root.
- **Refusals are already typed.** The closed `reason_class` vocabulary
  (`refuse_reasons` / `check_reason`), already carried on `OP_REFUSE` /
  `OP_ENFORCE` records.
- **The pretool DENY is already journaled.** `cli._journal_pretool_outcome`
  (`cli.py:5355`) appends an `OP_ENFORCE` record for every non-passthrough PRE
  outcome — a child's hook DENY among them.
- **The WAL is append-only and parent-readable.** `lane_journal.replay` ignores
  `OP_ENFORCE` / `OP_REFUSE` for state (they are forensic, NOT in
  `_STATE_MUTATING_OPS`), so adding history can never invent or lose a lease.
- **A reader already wants the field.** `decisions._from_lane_journal`
  (`decisions.py:883`) already falls back to `e.get("run_id") or e.get("root_id")`
  — but **no writer ever stamps `root_id`**, so that fallback is dead today.

**The one gap:** the `OP_ENFORCE` the pretool writer emits stamps only
`run_id=os.environ["CID_RUN_ID"]` — the child's **own** minted id
(`mint_child_from_env` gives the child its own `CID_RUN_ID`, ≠ the parent's).
With no `root_id`/`parent_id` on the record, a parent cannot fold "the refusals
under MY root" — it can only see a flat list keyed on opaque per-child ids it
never learns. The read at `decisions.py:883` is there; the value it reads is not.

## The change (additive, refuse-MORE-only, PDP-preserving)

Four small edits, no new actuation. A child still cannot force itself admitted —
it can only make its refusal **legible** to the layer that dispatched it.

### P1 — stamp lineage on the WAL entry builders

`lane_journal.enforce_entry` and `refuse_entry` gain optional `root_id` /
`parent_id` kwargs. When present (a non-empty string), they are stamped as
top-level fields on the record, next to the existing `run_id`. When absent
(today's callers, an operator session with no lineage env), the record is
byte-identical to today — pure additive, version-forward (a reader that doesn't
know the fields ignores them; `replay` already ignores the whole op for state).

### P2 — stamp lineage at the pretool journal writer

`cli._journal_pretool_outcome` reads `CID_ROOT_ID` / `CID_PARENT_ID` from the
env (the lineage the child already inherited across the `claude -p` boundary) and
passes them to `enforce_entry`. A root run (operator session) has no
`CID_ROOT_ID` → nothing stamped → unchanged. A child run carries its root → the
refusal is now foldable by root.

### P3 — the read: a parent folds child refusals by root

A new pure leaf `src/dos/child_refusals.py` folds the WAL into the typed,
lineage-keyed answer the issue's done-condition names:

> "child RID-X under root RID-ROOT was refused N times with `reason_class=…`"

`fold_child_refusals(entries, *, root_id) -> tuple[ChildRefusal, ...]` reads the
`OP_ENFORCE` / `OP_REFUSE` records whose `root_id` matches the queried root
**and whose `run_id` is NOT the root itself** (a child, not the parent's own
refusals), groups by `(child_run_id, reason_class, tool/target)`, and returns one
`ChildRefusal` per group carrying the child's run-id, its parent, the typed
reason class, the offending tool/tree, and the count. Pure stdlib, read-only —
the WAL is the only I/O, done by the caller; the fold is the unit-test surface.
A parent answers "is any child of mine blocked?" by passing its own
`CID_RUN_ID`/`CID_ROOT_ID` as `root_id` — **never** by reading a child's stream.

### P4 — the surface: `dos status --children`

`dos status` already folds one run's liveness/progress/region/resume.
`--children` adds the child-refusal fold for the run's root: a parent (or a
supervisor tick, or a `/goal-fleet` barrier) reads the typed up-chain signal from
the same verb it already uses to ask "what is this run's state." The
`StatusDigest` gains an optional `children` leg, null/absent unless `--children`
is passed (fail-closed: the digest still has no `claimed` field — a child's
refusal is a WAL fact, never the child's self-narrated "I'm blocked").

## The done-condition (the witness)

Witnessed by a test in which:

1. **Positive — up-chain read without the transcript.** A child run
   (`CID_ROOT_ID=RID-ROOT`, its own `CID_RUN_ID=RID-CHILD`) records a structural
   refusal (an `OP_ENFORCE` DENY appended via the real pretool writer path), and a
   parent-side `fold_child_refusals(read_all(wal), root_id="RID-ROOT")` resolves
   that refusal — its `reason_class`, its tree, its count — keyed on the root
   lineage, reading ONLY the WAL, never the child's tool-call stream.
2. **Negative — a clean child is silent.** A child that finishes cleanly (no DENY
   journaled) produces **no** `ChildRefusal` — the fold emits nothing, so a
   finished child is never mistaken for a blocked one (no false "blocked" signal).
3. **Lineage discrimination.** A refusal under a DIFFERENT root does not surface
   under this root; the parent's OWN refusals (run_id == root_id) are excluded
   (the parent wants its *children's* blocks, not its own).

## What this is NOT

- **Not a new actuation.** The kernel stays PDP. A child cannot force itself
  admitted; the report channel only makes the existing refusal legible. No
  `--force`, no re-admit, no lease grant flows from a child's report.
- **Not a new store.** The WAL is the sink — the same append-only journal the
  refusal already lands in. `child_refusals.py` is a read-only projection (delete
  it and you lose the reader, not any data), exactly like `decisions.py`.
- **Not the ENFORCE_BREAKER storm fold.** `decisions._from_enforce_storms`
  answers the OPERATOR's question ("an agent keeps hitting a SELF_MODIFY wall;
  a human must act"). This answers the DISPATCHER's question ("which of the
  children I launched is blocked, by what, right now"). Same WAL source, different
  reader, different rung (the supervisor/parent, not the HUMAN queue).

## Layering

- `lane_journal` (kernel leaf) — additive kwargs on two existing builders; no new
  op, no `_STATE_MUTATING_OPS` change.
- `child_refusals.py` (kernel leaf, layer 1) — pure fold, stdlib only, names no
  host. The lineage taxonomy is `run_id`'s (`CID_*`), already kernel-owned.
- `cli._journal_pretool_outcome` + `cmd_status` (helpers, layer 3) — the I/O
  boundary that reads the env and the WAL.
- No driver, no config edit, no host name. The fold reads the same `CID_*`
  lineage the correlation spine already defines.

Related surface: `run_id.py` (the lineage spine), `cli.py:5355`
(`_journal_pretool_outcome`, the stamping site), `decisions.py:883` (the reader
that already wanted `root_id`), `drivers/supervisor.py` (the dispatcher that
folds), and #188 (the failure this would have surfaced).
