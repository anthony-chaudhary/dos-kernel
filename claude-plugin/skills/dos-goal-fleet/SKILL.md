---
name: dos-goal-fleet
description: Launch N independent headless worker instances in account-balanced waves, each armed with ONE goal whose "done" is gated on a witness the worker did not author — not on its own say-so. Each objective becomes one self-stopping child wired to `dos hook stop` (the `dos-goal-gate` discipline), co-launch safety comes from `dos arbitrate` over each child's file-tree, and every claimed ship is confirmed by `dos verify` / `dos commit-audit`, never by a transcript line. Driven by `dos` verbs and the workspace's own `dos.toml` — names no host path, runtime, model, or account mechanism. Use when an operator hands a context with several independent objectives and says "launch a worker per goal", "fire a wave of goal agents", or "run these N goals in parallel". The fan-OUT analogue of `dos-goal-gate` (one self-stopping leaf) and `dos-witness-claim` (the fold).
---

# dos-goal-fleet — wave-launched self-stopping goal workers, witnessed not narrated

> **A fleet that trusts each worker's "I'm done" is a fleet of silent fails.**
> The move this skill makes is the one the whole substrate exists for: a worker's
> "the goal is met" is a *claim with a byte-author*, and the byte-author is the
> judged worker. So each child's stop is gated on `dos-goal-gate` — it cannot
> declare done until a witness it did not write (git ancestry, an effect
> read-back) corroborates the goal's effects. The fleet launches the leaves; the
> kernel decides which ones actually landed. The skill narrates; it never rules.

Take an operator-supplied context describing several **independent objectives**,
turn each into ONE headless worker armed with a single goal, and fire them in
**waves** sized to whatever seat pool the host exposes — so the launch is
load-balanced and no single account walls. This is the fan-out analogue of two
sibling skills:

- **`dos-goal-gate`** is one self-stopping leaf — a single agent whose "done" is
  grounded. `dos-goal-fleet` arms *many* such leaves at once. Each child runs the
  goal-gate discipline; this skill is the launcher and the rollup around them.
- **`dos-witness-claim`** is the *fold* — believing only the confirmed effects
  when many workers' results converge. Step 5 here is that fold, applied to the
  fleet's per-instance outcomes.

The shape is domain-free: **decompose into independent goals → arbitrate
co-launch safety → wave-launch one self-stopping child per goal → witness every
claimed ship → roll up.** The *policy* (which lanes, which plan grammar, how the
host rotates accounts, which model a child runs) is **data** read from `dos
doctor --json` / `dos.toml` and from the host's own launch convention — never a
literal this screenplay hardcodes. A shipped generic skill names no host (the
litmus); the host-specific launch verb is a seam you fill in Step 3.

## When to use this (and when not)

- **Use it** when the operator hands a context with **2+ independent
  objectives**, each describable as a single goal condition (a visible
  deliverable), and wants them run concurrently rather than serially.
- **Use `dos-goal-gate`** when it is **one** objective — just arm a single
  self-stopping agent; no fleet needed.
- **Use `dos-dispatch` / `dos-next-up`** when the work is already shaped as a
  phased-plan *dispatch packet* of disjoint repo tasks — that is packet-driven;
  this skill is goal-condition-driven over free-form objectives.
- **Use a single agent or a loop** when the objectives are a *sequenced plan* —
  one goal waits on another's output. The fleet is for **independent** goals.

> **Never arm a loop spine.** Do not goal-gate a dispatch-loop or a supervisor
> spine — those have their own typed stop authority. `dos-goal-fleet` arms *leaf*
> objectives, which is exactly the sanctioned use.

## Inputs

| Shape | What it looks like | Route |
|---|---|---|
| Explicit goal list | "goal 1: …, goal 2: …, goal 3: …" | one instance per listed goal |
| A context to decompose | a paragraph / doc / set of asks | Step 1: split into independent objectives, one instance each |
| A count + theme | "launch 5 workers to each find a bug in X" | derive 5 distinct goal conditions over X |

## Step 0 — Discover the workspace layout, and the host's seat pool

Run the doctor verb once and read the result. **Every path / lane / exit-code
below comes from here, never a literal** (the WCR on-ramp — EXAMPLES.md Recipe 0).

```bash
dos doctor --workspace . --json
```

The fields this skill uses:

- `lanes` / `lanes.trees` — the lane taxonomy and each lane's file-tree, for the
  `dos arbitrate` co-launch check in Step 2.
- `paths.plans_glob` / `stamp` — the host's plan grammar and ship-stamp
  convention, for the `dos verify` ship-witness in Step 5.
- `git` — if `false`, the git-phase ship-witness (Step 5) has no history; every
  claimed ship resolves `source="none"`. Say so; do not silently pass it.
- `runtime_hooks` — which runtimes already have `dos hook stop` wired (Step 3).

**The seat pool (sizes the waves).** The wave size is the host's serving-account
count — read it from the host's own account/seat command, **not** from any verb
this kernel ships (DOS names no account mechanism). The skill needs one number,
`SERVING` = how many worker seats can run concurrently right now:

- **`SERVING == 0` (every seat walled)** → STOP. Do not launch a paid child into
  a standing usage wall. Report the soonest reset the host exposes and offer to
  schedule the launch for after it.
- **`SERVING == 1`** → rotation is a no-op; launch serially or in a wave of 1, and
  warn the operator that all goals share one seat's window.
- **`SERVING >= 2`** → proceed; wave size = `min(SERVING, total_goals,
  --max-concurrent)`.

> **Account rotation is a host seam, not a DOS verb.** How launches spread across
> a seat pool — round-robin cursor, per-launch origin overlay, whatever — is the
> *host's* concern, wired into the launch command of Step 3. This skill only
> requires that the host's launch verb rotates seats per instance; it names no
> such mechanism itself. If the host has none, every child shares one seat — set
> `SERVING = 1` and say so in the rollup.

## Step 1 — Derive one goal per objective (the three-clause contract)

If the operator gave an explicit goal list, use it verbatim. Otherwise split the
context into **independent objectives** — each must stand alone, no cross-goal
dependency. For each objective write a goal **condition** stated as a *checkable
effect*, obeying three clauses (this is what makes the child's "done" witnessable
by `dos-goal-gate`, not self-certified):

1. **Deliverable as a visible effect** — a `(plan, phase)` the child will ship, a
   named file it will write, a commit it will land. State it as the thing a
   witness can later read back (`dos verify`, a fresh GET). **Never** "the work is
   good" — that has no byte-author but the worker.
2. **The explicit-blocker escape clause** — append literally: *"— or the final
   report explicitly names why it could not be produced"*. Without it an honestly
   blocked child burns its whole turn ceiling re-trying. A blocked child that
   says *why* is a clean outcome, not a failure.
3. **No uppercase verdict tokens** in the condition text (no `SHIPPED` / `BLOCKED`
   / `DONE` literals) — the condition is echoed into the child's log and any
   grep-based verdict scan must not false-match it. Lowercase prose.

> **Decompose into effects; abstain rather than invent.** An objective you cannot
> name a checkable identifier for is not a passable goal — it is a NO_CLAIM to
> surface for the operator, never an effect you fabricate. This is the same
> decomposition `dos-goal-gate` Step 2 spells out; the goal-gate wired into each
> child (Step 3) enforces it at the child's stop.

Write the derived `(label, goal_condition, expected_paths, in_prompt_directives)`
tuples to a plan file under the run dir (Step 2) and show them to the operator
**before launching**. If you derived the split (vs. the operator listing it),
pause and let the operator confirm or edit it — a wrong decomposition wastes a
whole wave.

## Step 2 — Create the run dir and arbitrate co-launch safety

```bash
RUN_DIR="<host-scratch-root>/goal-fleet/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$RUN_DIR"   # always mkdir -p BEFORE any `>` redirect, or the shell
                      # silently creates an empty file in the parent (hygiene)
```

(`<host-scratch-root>` is the host's gitignored scratch dir, read from config or
chosen by the operator — not a literal this skill names.)

**Collision check — the load-bearing safety step.** Independent *goals* can still
touch overlapping *files* if they ship code. Two same-wave children writing the
same paths is the exact cross-pollution `dos arbitrate` exists to prevent. For
each goal, pass its `expected_paths` (from Step 1) and the leases already held by
the children you have already decided to co-launch this wave:

```bash
dos arbitrate --workspace . --lane <lane> --tree "<this goal's expected paths>" --json
```

- `outcome: "acquire"` → its file-tree is disjoint from the wave's other
  children; **co-launch it**.
- `outcome: "refuse"` → it overlaps a child already in the wave; **do not
  co-launch**. Push it to a later wave (sequence it) or scope its claim narrower
  and re-arbitrate. Read `free_clusters` for a lane it *could* take instead.

For **read-only / report** goals there is no write collision — co-launch freely
(arbitrate still returns `acquire`). Only shipping goals need the gate. Prefer the
MCP form `mcp__plugin_dos-kernel_dos__dos_arbitrate` when running inside an agent
loop; the CLI form above is for a shell launcher.

## Step 3 — Wire the goal-gate, then launch one self-stopping child per goal

**First, wire the grounded Stop gate once per workspace** (idempotent — merged
into any existing hooks, never clobbering the operator's):

```bash
dos init --with-hooks --workspace .      # the default runtime
# cross-runtime:  dos init --hooks <runtime> --workspace .   (preview with --dry-run)
```

This binds `dos hook stop` — the gate that refuses a child's stop while a
phase it *claimed shipped* is not backed by git. That refusal IS the grounded
form of "keep working until the goal is met": the child cannot end on its own
word that a phase shipped. This is the whole `dos-goal-gate` discipline; read that
skill for how it composes with a host's own model-judged goal command (they
**AND** — the grounded gate can only ever *add* a refusal, never loosen one).

**Then launch each child** as ONE headless worker subprocess. The launch command
itself is the **host's** convention — this skill names no runtime binary, model,
effort, or account flag. The host's launch verb must supply, per instance:

- **The goal, armed as the runtime's stop-condition** (so the child self-stops on
  the goal). The grounded `dos hook stop` (just wired) runs alongside it and
  vetoes a false done.
- **The in-prompt directives on stdin** — context, scope, and the ship rules
  below. (A goal armed as a positional and directives on stdin compose; do not
  rely on a stdin goal line to gate — that is inert text the model only reads.)
- **Seat rotation per instance** — the host's account/origin overlay (Step 0),
  evaluated before the launch so each child in the wave lands on a different seat.
  If the host has none, every child shares one seat (`SERVING = 1`).
- **A hard turn ceiling** matched to the goal's weight (a light read/report is
  small; a ship is larger) — independent of the goal-gate, as a runaway backstop.
- **The model / effort from the host's registry**, never hardcoded here.

Per-instance launch hygiene (record into the run dir):

```bash
N=<instance-index>
ITER_DIR="$RUN_DIR/inst-$N"
mkdir -p "$ITER_DIR"
echo "[goal-fleet] inst-$N seat: ${<host seat id var>:-<default>}" > "$ITER_DIR/seat.txt"
# ... host launch verb ...  > "$ITER_DIR/run.log" 2> "$ITER_DIR/run.err"
```

- **Never merge stderr into the log** (`2>&1`) — stderr goes to the sibling
  `.err`; merging poisons the verdict scan with warnings / preflight banners.
- **In-prompt git rules** (state them even though a host hook may also enforce):
  the child may `git add` / `git commit` on the current branch **only if** the
  goal calls for shipping; it may **not** push, tag, force-push, rewrite history,
  switch branches, `git reset --hard`, `git clean`, `git restore`, or `git
  checkout -- <tracked>`. The orchestrator (you) never switches branches or leaves
  the worktree.

## Step 4 — Fire in waves; one background task per instance

Launch each instance as a **background task** so the harness auto-notifies on exit
— **do not poll** the log (wait-don't-poll). Wave discipline:

1. `WAVE = min(SERVING, remaining_goals, --max-concurrent)` (from Step 0).
2. Launch `WAVE` instances **in one message, multiple background launches** so
   they start concurrently — and so the host's per-launch seat cursor advances
   once per child, landing each on a different seat.
3. **Wait for the wave's notifications** (the harness re-invokes you on each
   exit). Do not start the next wave until the current one drains — this keeps
   concurrency ≤ the seat pool and lets walled seats reset between waves.
4. Repeat until all goals are launched.

> **Re-read the seat pool between waves.** If a live fleet (a dispatch-loop, a
> supervisor) is already consuming seats, `SERVING` overstates your free headroom
> — re-run Step 0's seat read between waves and shrink the wave to the live count.

Same-wave goals that `dos arbitrate` refused in Step 2 go to a *later* wave (their
file-trees overlap a child already running) — never co-launched.

## Step 5 — Per-instance outcome: witness, never narrate (the fold)

When an instance's task notification fires, read its result — and remember the
goal-gate is **transcript-grounded for early-quit, not git-grounded for ship**: it
proves the child didn't quit early, NOT that work landed. So fold only the
**confirmed** effects (this is `dos-witness-claim` applied to the fleet):

- **Did it meet the goal?** Read the child's final result envelope in its
  `run.log`. The honest-blocker escape clause means a blocked child names *why* in
  its final report — a clean outcome, recorded as `blocked-with-reason`, not a
  failure.
- **Did a claimed ship actually land?** Prove it by a witness the child did not
  author — never by its transcript:

  ```bash
  dos verify --workspace . <PLAN> <PHASE> --json     # SHIPPED only on real git evidence
  dos commit-audit --workspace . <claimed-sha>       # subject vs its own diff
  ```

  Read the rung: `source: "registry"` is the strongest ship; `source:
  "grep-subject"` is SHIPPED-but-weaker (a subject is forgeable); `source: "none"`
  means git does **not** back the claim — the narrated success the world doesn't
  corroborate. A goal met is not a ship verified.
- **Effect outside git** (a created file, a row, a sent message)? There is no
  `dos verify-effect` CLI verb — gather an independently-authored read-back via
  the `dos.evidence_sources` seam (the Python-API pattern in `dos-goal-gate`
  Step 2b) and fold only on CONFIRMED. Log the gap if no witness is wired; never
  launder an unwitnessed effect into "goal met".
- **Auth/launch wall on turn 1?** `is_error:true` + a not-logged-in message +
  zero cost = the seat didn't reach the child. Re-check that instance's `seat.txt`
  and relaunch only that one instance.

## Step 6 — Rollup + archive

Write a one-screen rollup to `$RUN_DIR/rollup.md`: per instance — label, seat
used, outcome (`met` / `blocked-with-reason` / `auth-failed` / `errored`), cost,
and any **verified** ship SHA (the `dos verify` / `commit-audit` verdict, not the
child's claim). Tally seats used to confirm the spread actually happened:

```bash
grep -h seat "$RUN_DIR"/inst-*/seat.txt | sort | uniq -c
```

If every instance shows the same seat id, rotation did not engage (`SERVING == 1`,
or the host's overlay failed) — say so; do not imply a spread that didn't happen.

The run dir is local scratch (gitignored) — do not commit it. If any instance
shipped code and the tree is coherent, the operator (not this skill) decides
whether to push or release — `dos-goal-fleet` authorizes per-child `commit`, never
an outward-facing push.

## Anti-patterns

- ❌ Deciding "the goal is met" by reading the child's last message. That is
  consistency, not grounding — the worker judging its own bytes. Witness every
  ship with `dos verify` / `commit-audit`; fold only the confirmed (Step 5).
- ❌ Co-launching two shipping goals without `dos arbitrate`. Independent goals
  can still write the same files; arbitrate each child's tree before the wave and
  sequence the refusals (Step 2).
- ❌ Launching more concurrent instances than serving seats. That forces ≥2 onto
  one seat's window and defeats the load-balance; re-read the pool between waves.
- ❌ Naming a specific runtime binary, model, effort flag, or account mechanism in
  this skill. The launch verb and seat rotation are the **host's** seam (Step 3);
  this skill names no host (the litmus).
- ❌ Goal-gating a loop spine (a dispatch-loop, a supervisor). Those carry their
  own typed stop authority; arm only *leaf* objectives.
- ❌ Treating a `/goal`-style model-judge as the ship oracle. The model-judge stops
  early-quitting (transcript-grounded); the git gate (`dos hook stop`) and
  `dos verify` are what confirm a ship. They AND — the grounded gate only ever
  adds a refusal.

## The one rule under this skill

Each child's "the goal is met" is a **claim with a byte-author** — the child. The
fleet lets a child *stop* only when `dos hook stop` finds git backs its claimed
phases, and folds a child's ship into the rollup only when `dos verify` /
`commit-audit` — a witness the child did not write — confirms it. The worker's
confidence and its fluent description of success are irrelevant to both gates,
which is the whole point: across a whole fleet, the part that decides a goal is
done is never the part being judged.
