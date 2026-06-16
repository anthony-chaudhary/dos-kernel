# docs/371 — the typed non-file blast-radius axis (Agent spawn / Task* coordination / ToolSearch capability)

> **Status:** SHIPPED (first leg — the type + the visibility surface). Closes #202.
>
> Admission gains a SECOND blast-radius axis: a typed `EffectKind`
> (NONE / SPAWN / COORDINATION / CAPABILITY) for the non-file effects the file
> model cannot see. The kernel names the CLASSES; the per-tool mapping is host
> data. An `Agent` SPAWN is now VISIBLE on the journal's new axis (and a per-holder
> fold surfaces a fan-out storm) while the FILE-collision clean-pass is preserved
> byte-for-byte.
>
> **Phase stamp.** Phase `P1` (the type + the visibility leg) shipped as
> `9bc315f`; `dos verify --workspace . docs/371 P1` resolves it via this commit's
> `(docs/371 P1)` trailer. The refusing spawn-gate is P2 — out of scope here.

## The blind axis

`pretool_sensor._NO_FOOTPRINT_TOOLS` ({`Agent`, `Task`, `TaskCreate`,
`TaskUpdate`, `ToolSearch`}) classifies these as known-empty FILE footprint
(`56e14e3`) so they pass the lane-collision check clean instead of earning a
spurious "EMPTY tree (unknown blast radius)" WARN. That fix is correct for the
question it answers — *can this call collide with a lane's file tree?* These tools
write no repo file, so the answer is genuinely no, and the fix killed 200+
reason-less enforce records (Agent 141, TaskUpdate 39, …).

But **"writes no file" is not "touches nothing"**. Each mutates agent / fleet
state the file model cannot see:

- **`Agent`** forks the fleet — a new seat, token spend, and the child can itself
  spawn. The live journal showed **140 `Agent` calls from a single holder**
  (`7bb9e16d`) in 6 days. Under the file model that is invisible; as a dispatch
  fact it is a 140-wide fan-out from one worker — exactly the runaway-fan-out class
  DOS exists to serialize. The clean-pass fix HID it.
- **`Task` / `TaskCreate` / `TaskUpdate`** mutate shared coordination state — the
  work queue other agents read to claim work; two agents racing on it is a
  last-writer-wins collision, not a no-op.
- **`ToolSearch`** mutates the agent's OWN capability set — what it can do next.

So the kernel had a blind axis: a real blast radius (fleet / coordination /
capability state) with no model, collapsed into "footprint-empty like a Read."

## Why not revert the clean-pass

The lane-*collision* WARN on these tools really was noise (they cannot collide on a
file tree), and the 200+ reason-less records trained operators to skim past
PRE-admission output — defeating the one call that matters (a real SELF_MODIFY
deny). The fix must STAND. The right move is a **second axis** for non-file
effects, not re-warning a file collision that does not exist.

## What shipped — the type + the visibility leg

The kernel gets the TYPE; the host keeps the mapping; the journal gets the
visibility. No verdict changes (this leg surfaces; it does not yet refuse).

### 1. The pure kernel leaf — `src/dos/effect_kind.py`

Domain-free, layer-1, names no tool:

- **`EffectKind`** — the closed class set: `NONE` / `SPAWN` / `COORDINATION` /
  `CAPABILITY`. The kernel names the classes only.
- **`classify_spawn_pressure(live_count, policy) -> SpawnPressureVerdict`** — the
  breaker-mold pure verdict: given how many SPAWN-effect calls one holder has live,
  is that `CLEAR` / `ELEVATED` / a `STORM`? The kernel COUNTS against a policy; the
  policy (`SpawnPressurePolicy`, default `elevated_at=8` / `storm_at=25`) names the
  thresholds. The observed 140-from-one-holder is unambiguously a STORM.
- **`count_spawns_per_holder` / `spawn_storm_holders`** — the per-holder fold: from
  `(holder_id, effect_kind)` records (the caller extracts them from the journal),
  count SPAWN records per holder and surface only the holders worth flagging. PURE
  — the journal I/O is the caller's, the same boundary discipline
  `breaker`/`liveness` keep.

### 2. The host-shaped adapter — `pretool_sensor`

`pretool_sensor` is the host-shaped CC verdict-adapter (its docstring: it "knows
only the generic CC edit/write tools; a host with other tools declares its own
mapping in a driver"). It already carries the CC tool-name frozensets
(`_READ_ONLY_TOOLS`, `_WRITE_TOOLS`, `_NO_FOOTPRINT_TOOLS`). So the CC
tool→`EffectKind` map lives HERE, exactly as the file-tree extraction
(`_tree_from_event`) is host-shaped here today:

- **`_effect_kinds_map()`** — `Agent`→SPAWN, `Task`/`TaskCreate`/`TaskUpdate`→
  COORDINATION, `ToolSearch`→CAPABILITY. A tool not in the map is `NONE` (the file
  axis covers its blast radius).
- **`effect_from_event(event)`** — the pure non-file twin of `_tree_from_event`.

### 3. The visibility — the journaled outcome carries the effect

`decide` computes the effect once and tags the forensic `outcome` record with
`effect_kind` whenever it is non-file. This is **purely additive**: it changes no
dialect and no decision. The two passthrough returns a no-footprint tool can reach
— the proven-no-footprint admission pass (a lease is live) and the Rung B
read/non-mutating pass (no lease) — both now carry `effect_kind: "spawn"` for an
`Agent`. The FILE-collision clean-pass (`tree_known and not tree` → no dialect) is
untouched. A fold over the journal's `effect_kind`-tagged records, keyed by the run
lineage (`run_id` / `CID_*`), then counts per-holder spawns and surfaces a storm.

## The layering litmus — satisfied

- **The kernel names no host.** `effect_kind.py` names only the classes
  (`SPAWN`/…) and thresholds; no tool name. The tool→effect mapping
  (`Agent`→SPAWN) lives in `pretool_sensor`, the host-shaped adapter — the same
  layer that already hardcodes the CC edit/write tool names for the file axis.
- **A driver is the only place host policy lives** — a host with other
  orchestration tools declares its own mapping in a driver, the seam
  `_tree_from_event` already documents; the kernel leaf is unchanged.
- **An overlap policy can only refuse-MORE.** This leg adds NO refusal — it only
  TAGS the outcome. The file-disjointness floor is untouched; `_NO_FOOTPRINT_TOOLS`
  still passes the file check clean.
- **Paths resolve via config**; no `__file__`.

## Done condition (met)

- An admission outcome carries a typed non-file `effect_kind` for
  `Agent`/`Task*`/`ToolSearch`, sourced from host/driver data
  (`pretool_sensor._effect_kinds_map`), not hardcoded in the kernel leaf.
- A SPAWN-effect call is adjudicable on a fleet/seat axis — countable per-holder
  (`count_spawns_per_holder`) so a fan-out storm is surfaceable
  (`spawn_storm_holders` → STORM at the observed 140), without re-introducing a
  false file-collision WARN.
- `_NO_FOOTPRINT_TOOLS`'s file-axis clean-pass is preserved (no regression to the
  200+-warn noise) — pinned by the existing
  `test_no_footprint_tool_passes_clean_no_advisory` staying green.
- A test proves an `Agent` fan-out from one holder is visible on the new axis while
  still passing the file-collision check clean
  (`tests/test_effect_kind_axis.py::test_agent_spawn_visible_on_axis_while_file_check_passes_clean`).

## The next leg (out of scope here, by design)

This is the visibility leg. A REFUSING spawn-gate — deny an Agent call when the
holder already has a STORM's worth of children live — is the natural next leg, and
it must be **opt-in** like the apply-gate (`DOS_APPLY_GATE`): a default install
keeps today's behavior (surface, never block). Surfacing first is the sound order:
you measure the fan-out distribution before you pick a refusing threshold. Tracked
for a follow-up; #203 (surface an Agent-spawn fan-out burst in `dos top` / `dos
pulse`) is the operator-facing consumer of the tag this leg adds.
