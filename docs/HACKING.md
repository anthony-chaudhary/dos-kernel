# Hacking DOS

> **The kernel carries the mechanism. You carry the policy.**

DOS is built so you can add your own block concepts, block reasons, refusal/safety
rules, and output formats **without forking the package**. This doc is the map:
the seven extension axes, how plugins attach, and the one invariant that keeps an
open system honest.

The design principle is the same one the kernel already applies to lanes: a
hardcoded set in the package becomes **declared data on the `SubstrateConfig`**,
and every consumer (emit / verify / refuse / man) derives from that single
declaration. You extend by *declaring*, not by *patching*.

This whole doc only works *because* the syscalls are deliberately small — a
primitive you build on, not a feature you consume. The *why* under that —
feature-vs-primitive, and why restraint is what makes a substrate — is
[`79_primitives-not-features.md`](79_primitives-not-features.md); the *where the
give may live* is [`76_flexible-goals-and-verification.md`](76_flexible-goals-and-verification.md).
This doc is the *how-to* those two motivate.

> **Extending vs. using.** This doc is how to *extend* DOS (add a reason, a
> renderer, a predicate). If you instead want to *use* the kernel you have —
> onboard a repo, run a fleet, gate CI, drive it from Python — start with the
> task-oriented **[`examples/playbooks/`](../examples/playbooks/)** (every command
> there was run and its output pasted back verbatim). The two compose: operate
> with the playbooks, extend with this doc + [`examples/dos_ext/`](../examples/dos_ext/).

---

## The three attachment models

| You're adding… | Attach via | Why |
|---|---|---|
| **Data** — a block reason (`[reasons]`), a ship-stamp grammar (`[stamp]`), a lane taxonomy (`[lanes]`), a path layout (`[paths]`) | `dos.toml` | Declarative, no code, diffs cleanly, `dos init` scaffolds it. |
| **Behavior** — a renderer, an admission predicate, an overlap scorer | Python `entry_points` | Real code needs to be importable; packaging entry_points make it discoverable without import-path hacks. |
| **An out-of-kernel adjudicator** — e.g. the LLM judge | a `dos.drivers.*` module the kernel *points to* but never imports, OR a `dos.judges` entry-point plugin (Axis 6) | A judge may have provider/I/O surface the kernel forbids; it plugs into the JUDGE rung of the trust ladder via `dos.judges`, stays advisory (emits a verdict, mutates nothing), and is measured by `dos judge-eval`. |
| **Workflow** — the *screenplay* that sequences the syscalls (`/dos-next-up`, `/dos-dispatch`, `/dos-replan`, …) | a `SKILL.md` in the shipped skill pack (`dos/skills/`), customized via the data tables above | The order "snapshot → audit via `verify` → render → `gate` → take a lane → archive" is domain-free; only the paths/lanes/grammar it reads are policy, and those are already `dos.toml` data. So the workflow ships as prose that shells `dos` verbs, not code. |

The rule of thumb: **data in `dos.toml`, behavior in `entry_points`, provider surface in a driver, workflow in a shipped skill.**

> **Calling vs. extending — the MCP server (`docs/80_*`).** The four rows above
> are how you *extend* DOS. A different axis is how an **agent** *calls* it: the
> shipped MCP server (`pip install dos-kernel[mcp]`; the `dos-mcp` console script)
> exposes `verify` / `arbitrate` / the refusal vocabulary / `doctor` as Model
> Context Protocol tools, so Claude (Desktop / Code) or any MCP host can use the
> referee with zero Python coupling. It is the agent-facing front door — point a
> host at it and a user gets the syscalls directly, no glue code. It is a
> *consumer* of the package (it `import dos`; the kernel never imports it), not a
> fifth extension axis. The tools it exposes are still parameterized by exactly
> the `dos.toml` data above, so everything you declare there flows straight
> through to the agent. See `src/dos_mcp/README.md` for the host config snippet.

> **Readback status (be precise):** the CLI reads back eight data tables from
> `dos.toml` — `[reasons]`, `[stamp]`, `[lanes]`, `[paths]` (SCV `docs/70_*` wired
> `[stamp]`; WCR `docs/71_*` wired `[lanes]`/`[paths]`), `[enumerate]`,
> `[cooldown]`, `[lifecycle]` (docs/207 — the phase grammar, the anti-churn windows,
> the plan-class taxonomy), and `[supervise]` (docs/99 — the always-on supervisor's
> standing population policy: how many dispatch-loops `dos loop` keeps alive +
> whether a spinner counts as up + whether the dead are reaped). No scaffolded
> table is dead config any more. Of the `entry_points` axes, **both renderers AND
> admission predicates ship today** (RND `docs/72_*` — the `dos.renderers` group +
> `--output`, Axis 4 below; ADM `docs/73_*` — the `dos.predicates` group + the
> built-in disjointness/self-modify guards, Axis 3 below); the LLM-judge driver
> ships too.
> The **workflow** axis (Axis 5, SKP `docs/74_*`) ships a baseline **skill pack**
> in the wheel (`dos/skills/`), driven by the data tables above + the new
> `dos doctor --json` / `dos gate` verbs. The **judge** axis (Axis 6,
> `docs/86_*`) ships the `dos.judges` seam + the built-in `abstain` baseline + the
> shipped `llm` judge + the `dos judge-eval` instrument; a workspace adds its own
> adjudicator under the `dos.judges` entry-point group. The **overlap-scorer** axis
> (Axis 7, `docs/113`) ships the `dos.overlap_policies` seam + the built-in `prefix`
> floor scorer + the `[overlap]` data table + the `dos overlap-eval` instrument; a
> workspace swaps the disjointness scorer (import-graph / semantic / model-backed)
> under that group, AND-ed under the unforgeable prefix floor so it can only
> refuse-MORE, never admit a collision.
>
> **Resolution order** (highest precedence first) when more than one source could
> set a policy axis. For a **`dos` CLI subcommand**:
>
> 1. the `dos.toml` tables (`[lanes]`/`[paths]`/`[stamp]`/`[reasons]`),
> 2. the `--job` reference taxonomy (`dos … --job`),
> 3. the `default_config` generic (`main`/`global`, job-shaped paths).
>
> So a `dos.toml [lanes]` **overrides** `--job` (TOML wins); declaring nothing
> degrades cleanly to the generic default. A CLI subcommand always rebuilds the
> config from the pointed-at workspace, so a `dos.set_active(...)` installed
> beforehand is **not** carried into a subcommand — the workspace
> (`--workspace`/`DISPATCH_WORKSPACE`/cwd) is authoritative for the CLI.
>
> For a **direct library caller**, the explicit config you pass wins above all of
> these: `oracle.is_shipped(cfg=my_cfg)` / `arbiter.arbitrate(config=my_cfg)` use
> `my_cfg` verbatim (and `my_cfg` may itself have been built from a `dos.toml` via
> `load_lanes_from_toml`/`load_from_toml`). That is the "explicit `SubstrateConfig`
> in code" rung — it lives at the API boundary, not on top of the CLI's rebuild.
>
> The two deliberate asymmetries:
> `[reasons]` is *additive* onto the base set while `[lanes]`/`[paths]`/`[stamp]`
> *replace/override*; and lanes/paths default *generic* (you declare your real
> ones — safe direction) while stamp defaults *strict* (you loosen it knowingly —
> the permissive direction is the dangerous one for false-positive ships).

### `dos.toml` (data)

`dos init` scaffolds it. The `dos` CLI reads it from the active workspace root and
folds its declarations onto the built-in base. A missing or empty section always
degrades to the built-in default — a workspace that declares nothing is
byte-identical to today.

The four data tables, and how each folds onto the base (note the additive-vs-
replace split — see the resolution-order note above):

```toml
# dos.toml

# [lanes] — REPLACES the generic main/global taxonomy with yours wholesale.
# `dos arbitrate` runs the tree-disjointness algebra over these; `dos doctor
# --check` flags any lane declared here without a [lanes.trees] entry.
[lanes]
concurrent = ["api", "worker", "web"]   # parallel iff their trees are disjoint
exclusive  = ["infra"]                  # runs alone
autopick   = ["api", "worker"]          # the bare-request walk order
[lanes.trees]
api    = ["src/api/**"]
worker = ["src/worker/**"]
web    = ["web/**"]
infra  = ["deploy/**", "terraform/**"]
[lanes.aliases]
svc = "api"                             # keyword → named-lane routing

# [paths] — OVERRIDES only the layout fields you name; the rest inherit the
# default. Relative paths resolve against the workspace root. A typo'd key fails
# loud (it would otherwise silently no-op).
[paths]
plans_glob = "planning/*.md"            # where `verify` discovers plans

# [stamp] — OVERRIDES the grep rung's grammar (subject AND file-path rungs).
# Generic by default (a bare `<SERIES>: <PHASE>` ships, match-any dir for the
# file-path backstop); declare your own to narrow it. Every key is optional.
[stamp]
style        = "grep"
subject_dirs = ["src", "lib"]          # dirs a DIRECT-ship subject may prefix
# --- the file-path backstop rung (artefact match against a phase's named files):
code_dirs    = ["src", "lib", "tests"] # top-level dirs whose files are deliverables
                                        # (empty/omitted = match ANY top-level dir)
infra_basenames = ["fanout_state.py"]  # EXTRA hub files (∪ universal config.py/…)
infra_doc_basenames = ["architecture.mmd"]  # EXTRA bulk-regenerated doc hubs
# --- subject-rung behavior toggles (declared, never inferred from the query):
progress_markers = ["audit", "soak"]   # `<PHASE> <marker>` = progress, not a ship
sub_phase_parent_fallback = false      # `RS4-port` falls back to parent `RS4`?
trailer_stamp = false                  # also ship via an END-of-subject trailer —
                                        # `feat(x): … (<PLAN> <PHASE>)`, the
                                        # Conventional-Commits shape (docs/289)
# summary_bundle_prefixes/bookkeeping_prefixes also live here (see below).

# [reasons.*] — ADDS block reasons onto the built-in set (additive, not replace).
[reasons.LANE_PARKED_FOR_BUDGET]
category = "OPERATOR_GATE"
```

#### Where the `[lanes]` table comes from — the folders→lanes convention

The `[lanes]` block above is shown hand-written, but you rarely write it from
scratch. **`dos init` seeds it from your repo's top-level directories**: one
disjoint `concurrent` lane per immediate subdirectory (`name = ["name/**"]`),
plus an exclusive `global` lane over the whole tree. So a repo laid out as

```
myrepo/
├── api/        →  lane "api"     tree ["api/**"]
├── worker/     →  lane "worker"  tree ["worker/**"]
├── web/        →  lane "web"     tree ["web/**"]
└── docs/       →  lane "docs"    tree ["docs/**"]
                   + exclusive "global" tree ["**/*"]
```

scaffolds four concurrent lanes + `global` with no thought required. **This is
the auto-convention.** It is a *good default* for one specific reason: top-level
dirs are the partition the arbiter can prove disjoint for free — distinct path
prefixes never overlap, so `dos arbitrate` admits all four to run in parallel out
of the box and `dos doctor --check` is clean. The derivation skips VCS / build /
dependency-cache noise (`.git`, `node_modules`, `dist`, `__pycache__`, … — see
`_INIT_LANE_SKIP_DIRS`) and caps at 8 lanes so the scaffold stays readable; a flat
repo with no source dirs falls back to a single honest exclusive `main` lane
(labelled SINGLE-WRITER — it runs alone) rather than inventing concurrency that
isn't there.

**The load-bearing point: folders→lanes is a one-time *scaffold*, not a runtime
binding.** `dos init` reads your directory listing **once** and writes the result
into `dos.toml` as ordinary, editable data. From that moment the TOML is
authoritative — DOS never re-watches the filesystem, never re-derives lanes, and
does not care whether a lane name still matches a directory. The folder layout is
the *seed* for the taxonomy, not a constraint on it. That means lanes are yours to
redefine, in three tiers of increasing power:

1. **Take the folders as-is (zero config).** Run `dos init`, ship. The directory
   structure *is* your lane meaning. Best for a repo whose top-level dirs already
   correspond to the regions a fleet works on in parallel.

2. **Declare your own lane meaning in data (the common case).** Edit `[lanes]` /
   `[lanes.trees]` — the folder seed is just a starting point you reshape:
   - **Merge** dirs into one lane: `services = ["api/**", "worker/**"]` (two dirs,
     one lane — they'll never run concurrently *with each other*, but as a unit
     stay disjoint from the rest).
   - **Split** one dir finer than the filesystem: `api-core = ["api/core/**"]`,
     `api-handlers = ["api/handlers/**"]` — two concurrent lanes inside one folder.
   - **Cross-cut** the layout entirely: a lane's tree is a glob list, not a path,
     so `proto = ["api/*.proto", "worker/*.proto", "shared/schema/**"]` is a
     perfectly good lane that maps to *no single directory*. Folders seed the
     default; they do not limit what a lane can mean. (Caveat: a cross-cut that
     slices *through* another lane's tree is **mutually exclusive with it while
     both are live** — the arbiter refuses the overlap. The example `proto` shares
     `api/**` and `worker/**` with a `services = ["api/**", "worker/**"]` lane, so
     the two can't hold leases at once even though neither is whole-repo. Cross-cut
     freely, but keep concurrently-run lanes' globs disjoint — that's the whole
     admission rule.)
   - **Route by keyword** with `[lanes.aliases]` (`svc = "api"`) so a bare
     `dos arbitrate --kind keyword` request lands in the right lane.
   - **Choose which lanes parallelise**: `concurrent` (run together iff trees are
     disjoint) vs `exclusive` (run alone — a whole-repo tree is *correct* here,
     since an exclusive lane never enters the disjointness algebra), and
     `autopick` (the subset a bare pick request walks, in order).

   `dos doctor --check` keeps this honest: a lane in `concurrent`/`autopick` with
   no `[lanes.trees]` entry, or a `concurrent` lane whose tree is the whole repo,
   is flagged (it can't be arbitrated — nothing to prove disjoint).

3. **Compute the taxonomy in code (the escape hatch).** When lanes depend on
   runtime state rather than a fixed list — derived from an env var, a service
   registry, a monorepo manifest — a `dos.toml` table can't express that. Write a
   `drivers/<host>.py` that builds the `LaneTaxonomy` (this is exactly what `job`
   does: `JOB_LANE_TAXONOMY` in `dos.drivers.job` is computed reference policy, not
   a flat TOML list). Data is the floor; a driver is the ceiling.

So the answer to "does DOS map folders to lanes?" is: **yes, as the zero-config
default `dos init` scaffolds — and then the convention gets out of your way.** The
folder layout is a sensible first guess at where disjoint work lives; `[lanes]` is
where you say what your lanes *actually* mean, and a driver is for when even data
isn't enough. (Resolution order when more than one tier is present is the
precedence note above: explicit-config-in-code › `dos.toml` › `--job` › generic
default.)

#### The driver itself — a host policy-pack (`drivers/<host>.py`)

Tier 3 above says "write a driver" but not *what a driver is*. A driver here is a
**host policy-pack**: the whole `SubstrateConfig` a particular host workload
supplies on top of the kernel mechanism, in code rather than data. It is a
distinct KIND from the `entry_points` plugins below — a renderer/predicate/judge
plugin extends *one axis*; a policy-pack driver assembles the *whole config* (its
lanes, its path layout, its facts) for a host. `dos.drivers.job` (the reference
userland app's pack) is the original; `dos.drivers.workshop` is the deliberately
generic **copy-me template** — a single self-contained module that shows the whole
shape. A driver is exactly **two pieces**, the same two `job` has:

1. **A `LaneTaxonomy` constant** — the concurrency policy as pure data, named
   `<HOST>_LANE_TAXONOMY` (`WORKSHOP_LANE_TAXONOMY`,
   `src/dos/drivers/workshop.py:84`). This is the same `LaneTaxonomy` a `[lanes]`
   table builds, but constructed in Python so it can be *computed* — derived from
   an env var, a manifest, a registry — which is the whole reason to leave TOML.
2. **A `<name>_config(workspace)` factory** — binds that taxonomy to a workspace
   root and returns a `SubstrateConfig` (`workshop_config`,
   `src/dos/drivers/workshop.py:132`). The factory name **must** match the module
   stem (`workshop.py` → `workshop_config`), because that is the by-convention
   contract the CLI loader resolves.

> **The one setup step you must not skip: gather workspace facts.** The factory
> MUST call `gather_workspace_facts(root)` and cache the result on the config
> (`workspace=gather_workspace_facts(root)`, `src/dos/drivers/workshop.py:156`) —
> exactly as `job_config` / `default_config` do. This is what scopes the
> **`self-modify`** guard (Axis 3) correctly: the facts record *which of the
> kernel's own runtime files actually exist under this root*, so in a foreign repo
> (no `src/dos/`) a whole-repo glob like the `release` lane's `**/VERSION` admits
> instead of tripping SELF_MODIFY against kernel files that aren't there. Omit it
> and `config.workspace` is `None`, which forces the guard to the conservative full
> static set and **wrongly refuses** that lane. The I/O-at-the-boundary rule
> applies even here: the facts are gathered once at config-build time so the pure
> `arbitrate` verdict stays workspace-aware without re-probing the disk.

**The by-convention loader (`dos --driver <name>`).** The CLI resolves a driver by
name, never by a hardcoded host string: `dos --driver <name>` imports
`dos.drivers.<name>` and calls its `<name>_config(workspace)`
(`_resolve_driver_config`, `src/dos/cli.py:45`). So a new host is a single module
under `src/dos/drivers/` and the CLI (a layer-3 helper) never learns its name —
the same one-way arrow the kernel obeys. `--job` is just the back-compat spelling
of `--driver job`. A dotted or path-y name (`foo.bar`, `../evil`) is rejected up
front as "unknown" (a path-traversal guard), and a `ModuleNotFoundError` from a
driver's own *broken internal import* is re-raised, never masked as "no such
driver" — a genuine bug in your driver fails loud.

```bash
# the same two pieces a [lanes] table declares, but the config is built in code:
dos arbitrate --driver workshop --lane ui --kind cluster --leases '[]'   # → frontend lane
dos doctor    --driver workshop --workspace .                            # its taxonomy, facts
```

**Copy-me:** start from `src/dos/drivers/workshop.py` (160 lines, no host name, no
real dependency) — it documents inline why each lane is shaped the way it is (the
tree-disjointness rule, the docs-prefix discrimination trick, why an exclusive
lane's whole-repo glob is correct). Rename the module, the constant, and the
factory to your host; the kernel and CLI pick it up by convention with no edit.

### `entry_points` (behavior)

A behavior plugin is a normal pip-installable package that registers itself under
a `dos.*` entry-point group:

```toml
# your_plugin/pyproject.toml
[project.entry-points."dos.renderers"]
terse = "your_plugin.renderer:TerseRenderer"

[project.entry-points."dos.predicates"]
budget_guard = "your_plugin.predicates:budget_guard"

[project.entry-points."dos.judges"]
my_judge = "your_plugin.judges:MyJudge"

[project.entry-points."dos.overlap_policies"]
import_graph = "your_plugin.overlap:ImportGraphPolicy"

[project.entry-points."dos.plan_sources"]
my_plan = "your_plugin.plan:MyPlanSource"
```

`pip install your_plugin` and DOS discovers it. Nothing in the `dos` package
changes. (See `examples/dos_ext/` for a copy-me skeleton of the four plugin axes —
a `terse` renderer, a `budget_guard` predicate, a `keyword` judge, and a
`semantic-groups` overlap policy.)

**Custom plan dialects (`dos.plan_sources`).** `dos plan` reads phases from a
**plan source**; the built-in `markdown` source harvests the strict
`### N. PLAN PHASE — …` grammar (letter+digit phase ids — see
[`examples/plans/example-plan.md`](../examples/plans/example-plan.md)). A repo whose
plans use a different shape (DOS's own `### Phase N:` design-doc dialect, a YAML
front-matter plan, a registry) ships a `dos.plan_sources` plugin instead: a class
with a `name: str` and a `rows(config) -> list[PlanRow]` method
(`src/dos/plan_source.py:107` — the `PlanSource` Protocol), resolved by name and
held to **fail-to-empty** (a raising source yields no rows, never a crash). The
kernel default never guesses your format; the plugin is how you teach it — the same
discover-at-the-boundary, name-no-host discipline as the other seams.

---

## The seven axes at a glance

Each axis is one place you extend DOS *without forking it*. Six of the seven ship
today; only Axis 2 (gate verdicts) is still design.

| # | Axis | You extend… | Attach via | Status | Instrument |
|---|------|-------------|-----------|--------|------------|
| 1 | Block reasons (refusal vocabulary) | a `reason_class` | `dos.toml [reasons]` | ✅ shipped | `dos man wedge` |
| 2 | Gate verdicts (block concepts) | a typed gate outcome | TOML / entry-point | 🔜 design | — |
| 3 | Admission predicates (safety) | a refusal rule | `dos.predicates` ep | ✅ shipped | `dos doctor` |
| 4 | Renderers (TUI / output) | an `--output` format | `dos.renderers` ep | ✅ shipped | `--output <name>` |
| 5 | Workflow (the screenplay) | a `SKILL.md` | shipped skill pack | ✅ shipped | `dos gate` |
| 6 | Adjudicators (judges) | a JUDGE-rung occupant | `dos.judges` ep | ✅ shipped | `dos judge-eval` |
| 7 | Disjointness scorers (overlap) | an `OverlapPolicy` | `dos.overlap_policies` ep | ✅ shipped | `dos overlap-eval` |

Each axis carries its own **instrument** because a seam is only research-grade if
it produces a number — and its own **invariant** that keeps an *open* set safe
(conjunctive-only for predicates, fail-to-ABSTAIN for judges, the prefix floor for
overlap scorers, pure-presentation for renderers). The axis sections below are the
how-to for each row.

The whole extension surface is also **self-describing** — `dos doctor` projects the
active set so you can audit exactly what is wired:

```text
$ dos doctor --workspace .
DOS v0.23.4
stamp convention    generic (any/no dir prefix)  [style=grep]
admission predicates disjointness, self-modify, budget-guard                       # Axis 3 + your plugin
judges (JUDGE rung)  abstain, keyword, llm, operator-decision, similarity           # Axis 6 + your plugin
enforce handlers     observe
overlap policy      prefix*, semantic-groups  (ratio_max=0.333; prefix floor always on)   # Axis 7 + your plugin
stall reader        REPEATING>=3, STALLED>=5  (ignore_tools: (none))
environment print   MJ614SR7R558  (kernel v0.23.4 @ <sha>; py 3.13.7; win32-AMD64)
```

> `budget-guard` and `semantic-groups` appear here only because
> `examples/dos_ext` is pip-installed — they are *this guide's own plugin examples*
> showing up live, the proof that a declared extension lights up every surface.

---

## Axis 1 — Block reasons (the refusal vocabulary) ✅ *shipped*

**What it is:** the closed `reason_class` set a no-pick / blocked verdict may
carry — `LANE_DRAINED`, `LANE_BLOCKED_ON_SOAK_GATED_PHASES`, etc. This is the
kernel's most important syscall (structured refusal): every reason is
*simultaneously emittable, verifiable, and refusable.*

**Why it can't just be a mutable enum:** that simultaneity is the load-bearing
invariant. If a producer could emit a reason the oracle can't verify, you're back
to the `UNCLASSIFIED` prose-drift the kernel exists to kill. So a reason is not a
string you sprinkle around — it is a `ReasonSpec` you **declare once**, and the
declaration is what makes it real across all surfaces.

**How to add one (data):**

```toml
# dos.toml
[reasons.LANE_PARKED_FOR_BUDGET]
category = "OPERATOR_GATE"     # required — one of: TRUE_DRAIN OPERATOR_GATE STALE_CLAIM MISROUTE UNCLASSIFIED
refusal  = true                # optional, default true; false = advisory-only (still renders)
summary  = "lane parked: monthly token budget hit"
fix      = "raise the budget cap, or /replan"
see_also = ["meta budget", "oracle picker_oracle"]
```

That's it. Now:

```bash
dos man wedge                          # your reason is listed with the built-ins
dos man wedge LANE_PARKED_FOR_BUDGET   # a full man page, projected from your fields
```

…and in code, through the *same* calls a built-in uses:

```python
import dos.wedge_reason as wr, dos.picker_oracle as po
wr.is_known_reason("LANE_PARKED_FOR_BUDGET")   # True   — emittable
wr.category_for("LANE_PARKED_FOR_BUDGET")      # OPERATOR_GATE — man-projectable
wr.is_refusal("LANE_PARKED_FOR_BUDGET")        # True   — refusable
po.resolve_cause("LANE_PARKED_FOR_BUDGET")     # OPERATOR_GATE — verifiable
```

**How to add one (code), e.g. computed reasons:**

```python
import dataclasses, dos
from dos.reasons import BASE_REASONS, ReasonSpec

cfg = dos.default_config(".")
cfg = dataclasses.replace(cfg, reasons=BASE_REASONS.extend([
    ReasonSpec(token="LANE_PARKED_FOR_BUDGET", category="OPERATOR_GATE",
               refusal=True, summary="budget hit", fix="raise the cap"),
]))
dos.set_active(cfg)
```

`ReasonRegistry` is immutable — `extend()` returns a *new* registry. A process's
active reason set is a value installed on the config, never a global a plugin
scribbles on mid-run. That immutability is what keeps "closed set" a real
property.

**The mechanism:** `dos.reasons.ReasonSpec` / `ReasonRegistry`. `BASE_REASONS` is
the built-in seven. `dos.wedge_reason`'s `coerce`/`category_for`/`is_refusal` and
`dos.picker_oracle.resolve_cause` all consult the active registry, so one
declaration lights up every surface.

---

## Axis 2 — Block concepts (gate verdicts) 🔜 *design*

**What it is:** the typed verdicts a gate produces — `LIVE`, `DRAIN`,
`STALE-STAMP`, `BLOCKED`, `RACE` (`dos.tokens.GateVerdict`). These drive
`gate_policy()` (what the loop *does* with a verdict) and `loop_decide.decide()`
(continue/stop).

**Why this is more delicate than reasons:** the five core verdicts are wired into
the loop's control flow with hand-tuned policy (drained-twice, the dirty-zero
breaker). You can't just add `MY_VERDICT` and expect `gate_policy` to know what to
do with it. So the design is **core stays built-in; you add *extension*
verdicts paired with their policy:**

```python
# proposed shape (not yet shipped)
ExtensionVerdict(
    token="QUOTA_PAUSED",
    action=GateAction(next_mode="stop", surface=True,
                      counts_toward_drain=False, reconcile=False,
                      reason="quota window — pause, don't burn launches"),
)
```

`gate_policy()` would fall through to the workspace's extension verdicts for any
token it doesn't recognize. This keeps the core loop semantics frozen (the part
that's expensive to get wrong) while letting a workspace name and handle its own
outcomes. **Open question:** whether extension verdicts may also be declarable in
`dos.toml` (a fixed `next_mode`/`surface`/`counts_toward_drain` tuple is just
data) or must be code (if the action needs to compute). Likely: simple ones in
TOML, computed ones via an `entry_point`.

---

## Axis 3 — Refusal / admission policy (safety rules) ✅ *shipped*

**What it is:** the arbiter's admission predicates — the ≤30% soft-overlap
tree-disjointness rule (`dos.lane_overlap`) decides whether a new lease may
coexist with a live one. These *are* the safety elements: they're what stops two
agents from editing the same files concurrently.

**The hackable form:** a list of pure **admission predicates**, each
`(request, live_lease, config) -> AdmissionVerdict`, resolved from a
`dos.predicates` entry-point group (`dos.admission`, ADM `docs/73_*`). The
arbiter runs the built-in predicates plus any registered ones, and **a refusal
from any predicate refuses the lease**. Two predicates ship built-in and
always-on:

  * **`disjointness`** — the tree-overlap rule above, refactored into the first
    registered predicate (so routing the arbiter through the conjunction is
    byte-for-byte behavior-preserving — proven by the entire existing arbiter
    suite staying green through `run_predicates`).
  * **`self-modify`** — refuses a lease whose tree includes the orchestrator's
    own running code (`src/dos/arbiter.py`, the classifiers, the reason
    vocabulary, the config seam — the T1 runtime set in
    `dos.self_modify._DISPATCH_RUNTIME_FILES`). A live loop must not rewrite the
    kernel that is adjudicating it. Carries the typed `SELF_MODIFY` reason (a
    `BASE_REASONS` member → `dos man wedge SELF_MODIFY` documents it).

```python
# the working shape — see examples/dos_ext/dos_ext/predicates.py (BudgetGuard)
class BudgetGuard:
    name = "budget-guard"
    def __call__(self, request, live_lease, config) -> AdmissionVerdict:
        cap = getattr(config, "token_budget", None)
        if cap is not None and (getattr(config, "tokens_spent", 0) or 0) >= cap:
            return AdmissionVerdict.refuse("monthly token budget exhausted")
        return AdmissionVerdict.admit()
```

> **The one invariant that keeps an *open* safety-hook set safe:
> conjunctive-only.** This is the highest-leverage *and* highest-risk axis — a
> buggy predicate that *loosens* admission could let two agents collide.
> `AdmissionVerdict` has only `.admit()` / `.refuse(reason)` — there is **no
> force-admit return value** — so a workspace predicate is *structurally*
> incapable of overriding a built-in refusal. Adding a predicate can only make
> admission *stricter*, never looser (the safe direction). The worst a buggy or
> hostile predicate can do is refuse too much (a visible, safe-direction failure
> an operator notices at once), never admit a collision. A predicate that
> *raises* is caught and converted to a **refuse** (fail-closed — the inverse of
> the renderer rule, deliberately, because a safety hook that can't answer must
> not admit). The `--force` operator override stays the only thing that can
> overrule any refusal — a predicate refusal is overridable by `--force` exactly
> as the disjointness refuse is; a predicate cannot itself force anything.

`dos doctor` lists the active predicates (`admission predicates  disjointness,
self-modify, …`), the predicate analogue of "see the active reason set," so an
operator can audit exactly what gates their arbiter.

```bash
pip install -e examples/dos_ext        # registers the `budget_guard` predicate
dos doctor --workspace .               # lists: disjointness, self-modify, budget-guard
# a lease editing the kernel's own code is refused (SELF_MODIFY) …
dos arbitrate --lane k --kind keyword --tree src/dos/arbiter.py \
  --leases '[{"lane":"a","lane_kind":"cluster","tree":["agents/a_*.py"]}]'   # REFUSED
# … unless --force (the operator's explicit kernel edit):
dos arbitrate --lane k --kind keyword --tree src/dos/arbiter.py --force \
  --leases '[{"lane":"a","lane_kind":"cluster","tree":["agents/a_*.py"]}]'   # ACQUIRE
```

---

## Axis 4 — TUI / output (renderers) ✅ *shipped*

**What it is:** how a decision/verdict becomes text. Output used to be hardcoded
`print` in `cli.py` and `render_text`/`render_json` in `timeline.py`; it now
routes through a `Renderer` resolved by name (`dos.render`, RND `docs/72_*`).

**The hackable form:** a `Renderer` protocol resolved by name from a
`dos.renderers` entry-point group, selected with `--output <name>`:

```python
class Renderer(Protocol):
    name: str
    def render_decision(self, decision) -> str: ...   # arbiter LaneDecision
    def render_verdict(self, verdict) -> str: ...      # ship ShipVerdict
    # optional surfaces — default to the text form if you don't implement them:
    def render_timeline(self, timeline) -> str: ...
    def render_man(self, entry) -> str: ...
    def render_decisions(self, rows) -> str: ...
```

DOS ships `text` (the default — every command byte-identical to before the seam)
and `json` built-in; a workspace registers its own (`terse`, `color`, `html`,
`slack`, …). See `examples/dos_ext/` for a working, installable `TerseRenderer`
(`pip install -e examples/dos_ext` registers it). Resolution is by entry-point
name, so `--output terse` finds it without the package knowing it exists; an
unknown `--output` fails loud with the known list (it never silently falls back).
A plugin **cannot shadow** a built-in name (`text`/`json` resolve first), and a
plugin that implements only some surfaces inherits the `text` form for the rest
(subclass `dos.render.BaseRenderer`, or just omit the method).

```bash
pip install -e examples/dos_ext                          # registers `terse`
dos verify    --output terse PLAN PHASE                  # one-line terse form
dos verify    --output json  PLAN PHASE                  # machine-readable (built-in)
dos arbitrate --output terse --lane api --kind cluster --leases '[]'
dos man wedge --output json LANE_DRAINED                 # structured man page
dos verify    --output bogus PLAN PHASE                  # error: unknown renderer 'bogus'; known: text, json, terse
```

**Design rule:** a renderer is *pure presentation* — it is handed an
already-decided object (`ShipVerdict`, `LaneDecision`, `Timeline`, a man entry)
and returns a string. It receives no config, no leases, nothing it could decide
*with*. It never decides anything. Rendering is strictly downstream of the
kernel, so presentation can never leak policy back in — the worst a buggy
renderer can do is produce ugly text.

---

## Axis 5 — Workflow (the screenplay) ✅ *shipped (baseline pack)*

**What it is:** the *workflow that sequences the syscalls* — the Claude Code
skills that drive a plan-and-ship cycle. The pack ships **ten** skills in two
tiers. The **plan-and-ship tier** (SKP `docs/74_*`): `/dos-next-up` (snapshot the
portfolio into a dispatch packet), `/dos-dispatch` (take a lane + ship + archive),
`/dos-replan` (garden the portfolio), the two loops, and `/dos-supervise-loop` +
`/dos-witness-claim`. The **operator tier** (docs/207 Phase 5): `/dos-unstick`
(sweep recurring blockers → propose one structural fix per cause),
`/dos-promote` (surface every HELD unit + its typed unblock action), and
`/dos-class-cycle` (the judge-gated plan-lifecycle gardener). Not data
(`dos.toml`), not behavior (`entry_points`) — the *screenplay* that calls
`verify` / `gate` / `arbitrate` / `pickable` / `cooldown` / `reconcile` in order.

**Why it is a real axis, not a contradiction of "workflow is host concern":**
there is a distinction the layer table collapses. *Workflow policy* — which
lanes, which plan grammar, the commit-subject template — is the host's, declared
in `dos.toml`. *Workflow mechanism* — the *shape* "snapshot → audit each pick
against `verify` → render a packet → `gate` the empty case → take a lane lease →
archive" — is domain-free, and identical across hosts. The second is as liftable
as the syscalls were. DOS ships a reference one; a host may use it, fork it, or
ignore it (the way `BASE_REASONS` is the reference refusal vocabulary).

**How to use it (it ships in the wheel):**

```bash
pip install dos-kernel               # dist name is dos-kernel (NOT `dos` — that PyPI name is unrelated); pack ships under dos/skills/<name>/SKILL.md
dos init --skills /path/to/svc       # scaffold dos.toml AND copy the core skills
                                     #   into .claude/skills/ as editable files
                                     #   (--skill NAME for one, --all for the pack)
/dos-next-up                         # writes a packet to the configured next_packets
                                     #   path, each pick's status from `dos verify`,
                                     #   naming NO host path/lane/convention
dos gate <that-packet's-sidecar>     # LIVE | DRAIN | STALE-STAMP | BLOCKED | RACE
```

**The verbs the pack rides** (all thin surfaces over existing kernel machinery):

- `dos doctor --json` — the machine-readable workspace report (paths/lanes/stamp/
  the `[enumerate]`/`[cooldown]`/`[lifecycle]` tables/git/home) a skill reads to
  discover its layout instead of hardcoding `docs/_plans/`. The WCR on-ramp.
- `dos gate PACKET` — the typed empty-packet verdict over `gate_classify` (the
  verdict IS the exit code: `LIVE`=0, `DRAIN`=3, `STALE-STAMP`=4, `BLOCKED`=5,
  `RACE`=6, contract-error 2, unknown 7).
- `dos pickable UNIT --state '<json>'` (docs/207) — the pre-dispatch gate
  (OFFERABLE=0; a per-`HoldReason` code per hold). The operator-tier `/dos-promote`
  branches on which hold.
- `dos enumerate PLAN_DOC [--series ID]` (docs/207) — the phase-list producer (the
  unit universe + shipped/remaining + typed DriftNotes; clean=0/drift=3/empty=4).
- `dos cooldown UNIT` (docs/207) — the anti-churn verdict (CLEAR=0,
  RECENTLY_ATTEMPTED=3); the loop's pick-selection skips a cooled unit.
- `dos reconcile UNIT --claimed-done {--plan P --phase PH | --oracle-shipped}`
  (docs/207) — the quiet-completion gate (VERIFIED=0, QUIET_INCOMPLETE=3,
  HONEST_OPEN=4); the loop's archive step KEEPs a claim the oracle refutes.

**Design rule:** a generic skill **names no host path, lane, or commit
convention.** Every literal the `job` skills hardcode comes from `dos doctor
--json` (paths/lanes, via WCR) or `dos.toml [stamp]` (the ship grammar, via SCV).
A `grep` of a shipped generic skill for a host directory or a job lane returns
nothing — the skill analogue of "kernel imports no host," pinned by
`tests/test_skill_pack_*.py`.

**What is NOT in the pack (the named open seams, see `docs/74-friction-log.md`):**
the packet *template* (a `[render]` data seam / a `render_packet` protocol
method — RND's `--output` covers verdicts, not packets, so the skill assembles
the packet itself for now), host *evidence sources* (a driver hook), and the
heavy *soft-claim leasing tier* (parked in `job` by the `CLAUDE.md` heavy-tier
rule; the generic loop uses `arbitrate`/`lease` for lane coordination and `log`s
the gap). The pack ships the domain-free *shape*; these three are where a host's
policy still attaches via a future seam.

---

## Axis 6 — Adjudicators (judges) ✅ *shipped*

**What it is:** the **JUDGE rung** of DOS's trust ladder. Trace a blocked claim and
you find three adjudicators at escalating cost and trust — **ORACLE** (the kernel's
deterministic `verify`/`picker_oracle`, forgery-proof but narrow, abstains on what it
can't prove) → **JUDGE** (a model / heuristic / debate ruling on the residue) →
**HUMAN** (the `dos decisions` queue). This axis is the seam where you plug in *your
own* occupant of the JUDGE rung. The full argument is
[`87_the-adjudicator-trust-ladder.md`](87_the-adjudicator-trust-ladder.md); this is the
how-to.

**Why a judge is a *driver*, not a kernel verb:** a judge has the surface the kernel
forbids — it calls a provider, it is non-deterministic, it is *a model verifying a
model*. So it lives outside the kernel boundary (a `dos.drivers.*` module or an
installed plugin), and the kernel points to it without importing it. The reference
occupant is `dos.drivers.llm_judge:LlmJudge`.

**The contract (one method):**

```python
from dos.judges import Claim, JudgeVerdict   # Judge is a runtime-checkable Protocol

class MyJudge:
    name = "my-judge"                          # what `--judge my-judge` / `dos doctor` use
    def rule(self, claim: Claim, config) -> JudgeVerdict:
        # claim.claim_text  — what was asserted ("phase AUTH2 shipped")
        # claim.stated_reason — the agent's NARRATION (distrust it)
        # claim.evidence    — forgery-resistant facts (git lines, file state)
        if claim_is_backed_by_evidence(claim):
            return JudgeVerdict.agree("evidence supports it")
        if claim_contradicts_evidence(claim):
            return JudgeVerdict.disagree("unbacked 'done'")
        return JudgeVerdict.abstain("can't tell — route to a human")   # the safe default
```

A judge MAY do I/O inside `rule` (call a model, shell out) — unlike a renderer or a
predicate, which are pure. That is the whole reason it is a driver. Register it under
the `dos.judges` entry-point group:

```toml
# your_plugin/pyproject.toml
[project.entry-points."dos.judges"]
my-judge = "your_plugin.judges:MyJudge"
```

`pip install your_plugin`, then `dos judge-eval --judge my-judge …` resolves it and
`dos doctor` lists it. (See `examples/dos_ext/dos_ext/judge.py` for a copy-me,
zero-dependency `KeywordJudge`, and `dos.drivers.llm_judge:LlmJudge` for the model one.)

> **The four invariants that keep an *open* adjudicator set honest** (the analogue of
> Axis-3's conjunctive-only and Axis-4's pure-presentation):
> 1. **Deterministic-first** — the oracle rules first; the judge sees only the residue
>    it abstained on (enforced by the composition, `judge_eval.compose_deterministic_first`).
> 2. **Advisory-only** — a judge is handed a frozen `Claim` and returns a frozen
>    `JudgeVerdict`; it is given **nothing it could mutate**. It can no more "believe
>    itself into" a state change than a renderer can mis-verify a ship.
> 3. **Fail-to-ABSTAIN, never fail-to-AGREE** — `judges.run_judge` converts any raise
>    OR any non-`JudgeVerdict` return into an `ABSTAIN`. (The *inverse* of the predicate
>    rule, which fails to *refuse*: a safety hook fails closed, an advisory judge punts
>    to a human — neither ever becomes an approval.) So a *false-clear* (AGREE on a
>    false claim — the dangerous cell) is structurally unreachable by accident.
> 4. **Abstention is first-class** — the verdict is three-valued (AGREE/DISAGREE/
>    **ABSTAIN**); a judge that can't tell says so instead of guessing. The built-in
>    `abstain` judge is the always-available, **unshadowable** baseline (the judge
>    analogue of the `text` renderer).

**The instrument — measure what you plug in (`dos judge-eval`):** a seam is only useful
to a researcher if it produces a number. Point it at a labelled set and get the
false-clear rate:

```bash
dos judge-eval --judge my-judge --cases cases.jsonl     # confusion grid + rates
dos judge-eval --judge my-judge --cases cases.jsonl --json
```

```jsonl
# cases.jsonl — one labelled claim per line; `truth` is YOUR ground truth (from
# artifacts, not from any judge — the eval is only as honest as its labels)
{"claim_text": "phase AUTH2 shipped", "stated_reason": "done", "evidence": ["git: no commit closing AUTH2"], "truth": false}
{"claim_text": "phase WEB1 shipped", "evidence": ["commit 9f3a1c2: WEB1 done"], "truth": true}
```

The headline is **false-clear rate** — of the claims the judge *cleared*, the fraction
that were actually false (when it says "believable," how often is it wrong). The exit
code is the verdict on the judge: `0` if it false-cleared nothing, `1` if the dangerous
cell is non-empty — so a CI gate can fail on any leak. For the *system* picture,
`dos.judge_eval.compose_deterministic_first(oracle_fn, judge, cases)` reports the
**rung-occupancy table** (deterministic% | judge% | human%) — how much human-review load
the judge removes, and the per-rung false-clears it costs. This is the
bring-your-own-adjudicator measurement surface, framed for research in
[`87`](87_the-adjudicator-trust-ladder.md) §4.

**Design rule:** a judge is **advisory**. It emits a verdict; it mutates no lease,
registry, or plan. The worst a buggy/hostile judge can do is abstain too much (costs
human attention — safe) or DISAGREE too much (a needless review — safe); it can never
auto-clear a claim by failing, and it has nothing to mutate even if it tried. Acting on
a verdict is always a separate, explicit step.

---

## Axis 7 — Disjointness scorers (overlap policies) ✅ *shipped*

**What it is:** the **disjointness SCORER** — the kernel's most load-bearing verdict,
*may these two known trees run concurrently?* Until this axis it was a hardcoded `1/3`
prefix-ratio (`dos.lane_overlap`) sealed inside the arbiter; now it is a swappable
`OverlapPolicy` resolved by name. The full argument is
[`113_the-overlap-policy-seam-and-eval-per-axis.md`](113_the-overlap-policy-seam-and-eval-per-axis.md);
it implements the answer-shape [`90 §1`/`§2`](90_open-research-areas.md) named as open
research.

**Why it can't just be a constant:** the `1/3` ratio is calibrated for a path-shaped,
code-shaped world. A monorepo team wants import-graph reachability; an ML team wants
feature-table writes (paths irrelevant); a prose fleet wants section-level locks. Each
has a *legitimately different* notion of overlap. Freezing the ratio in the kernel
assumes one. The seam un-assumes it.

**The contract (one method):**

```python
from dos.lane_overlap import OverlapDecision           # the typed verdict you return
from dos.overlap_policy import OverlapPolicy            # a runtime-checkable Protocol

class ImportGraphPolicy:
    name = "import-graph"                                # what `--policy import-graph` selects
    def overlaps(self, requested_tree, lease_tree, config) -> OverlapDecision:
        # two KNOWN trees (the empty-tree / unknown-blast-radius case is the kernel's,
        # not yours). Return an OverlapDecision — ADMIT_* or REFUSE_*.
        ...
```

A policy MAY do I/O inside `overlaps` (walk an import graph, call a model) — IFF it
lives in a driver, the JUDGE-rung allowance. Register it under `dos.overlap_policies`:

```toml
# your_plugin/pyproject.toml
[project.entry-points."dos.overlap_policies"]
import-graph = "your_plugin.overlap:ImportGraphPolicy"
```

`pip install your_plugin`, then `dos overlap-eval --policy import-graph …` resolves it
and `dos doctor` lists it. (See `examples/dos_ext/dos_ext/overlap.py` for a copy-me,
zero-dependency `SemanticGroupPolicy` that catches cross-path semantic collisions the
prefix rule misses.) Or, for just a different *tolerance* of the built-in scorer, no
code at all:

```toml
# dos.toml — the data attachment (the prefix floor stays the same; only the
# ratio the default scorer admits under changes)
[overlap]
ratio_max = 0.25          # tighten the 1/3 elbow
# policy = "import-graph"  # or name a registered scorer
```

> **The one invariant that keeps an *open* scorer set safe: the deterministic prefix
> floor is ALWAYS under you.** Unlike a predicate (which can only refuse), a policy
> returns a verdict that *includes admit* — so the type alone no longer guarantees the
> safe direction. The kernel restores it structurally: whatever a policy returns,
> `overlap_policy.admissible_under_floor` AND-s it with the unforgeable
> prefix-disjointness verdict —
>
>     admit  ⟺  floor.admissible  AND  policy.admissible
>
> So a policy may turn an ADMIT into a REFUSE (catch a semantic collision the floor
> missed — the useful direction), but can NEVER turn a REFUSE into an ADMIT. A
> buggy/hostile/raising policy is *structurally incapable* of admitting a
> path-colliding pair — the worst it can do is refuse too much (a visible,
> safe-direction loss of parallelism). A policy that raises or returns the wrong type
> degrades to the floor verdict alone (fail-closed toward today's behavior). This is
> the admission analogue of Axis-3's conjunctive-only and Axis-6's fail-to-ABSTAIN, and
> the [`76`](76_flexible-goals-and-verification.md) design law applied to admission: a
> researcher changes *what counts as overlap*, never *which way the verdict fails*.

**The instrument — measure what you plug in (`dos overlap-eval`):** the friendliness
lever — a seam is only research-grade if it produces a number (the admission twin of
`dos judge-eval`, and [`90 §2`](90_open-research-areas.md)'s "backtest study"). Point it
at a labelled corpus of concurrent-pair outcomes and get the false-admit rate:

```bash
dos overlap-eval --policy prefix       --cases overlap-cases.jsonl          # baseline (the 1/3 ratio)
dos overlap-eval --policy import-graph --cases overlap-cases.jsonl --json   # your scorer
```

```jsonl
# overlap-cases.jsonl — one labelled pair per line; `collided` is YOUR ground truth
# (did concurrent execution actually corrupt shared state / merge-conflict — from
# artifacts, NEVER from a scorer)
{"tree_a": ["src/featureflags.py"], "tree_b": ["config/flags.yaml"], "collided": true}
{"tree_a": ["src/web/**"], "tree_b": ["src/worker/**"], "collided": false}
```

The headline is **false-admit rate** — of the pairs the scorer *admitted*, the fraction
that actually collided (the dangerous cell, the admission analogue of the judge's
false-clear). The exit code is the verdict on the scorer: `0` if it admitted no real
collision, `1` if the dangerous cell is non-empty — so a CI gate fails on any leak. The
companion `safe-concurrency-forgone rate` is the cost a stricter scorer pays (a
safe-direction quality knob, not a gate). This is what makes the `1/3` constant
*falsifiable*: a number a researcher can **beat on a corpus, with evidence**.

**Design rule:** a policy decides ADMIT/REFUSE for the both-known case only, and is
ALWAYS AND-ed under the prefix floor. It owns no empty-tree handling (the kernel's), and
cannot loosen admission below the floor. The worst a buggy scorer can do is forgo safe
concurrency (lost parallelism — safe), never admit a collision.

---

## The invariant that makes openness safe: `--check`

An open vocabulary is only safe if you can prove it's complete. The completeness
rail (today `dos doctor`; the DOM plan's `man --check`, hardening into CI) is what
turns "anyone can add a name" into "no name goes undefined":

- A `reason_class` **emitted** in a verdict envelope but **not** in the active
  registry → **fail** (this is exactly the `UNCLASSIFIED` drift; it's a bug to
  declare, not tolerate).
- A reason whose `category` the oracle can't verify against → **fail** (the
  `ReasonSpec` constructor already enforces this at declaration time).
- *(roadmap)* a plan-meta field written in a plan body but absent from the schema
  → **fail**; a lane acquired in a lease but absent from the taxonomy → **fail**.

So the deal is: **DOS lets you add anything, and `--check` guarantees the system
can still define everything it uses.** Openness and verifiability are not in
tension here — the registry-as-data design is what lets you have both.

---

## Getting started in 60 seconds

```bash
pip install -e .
cp -r examples/dos_ext my_workspace          # copy the skeleton
cd my_workspace
dos man wedge                                # see your custom reason listed
dos man wedge LANE_PARKED_FOR_BUDGET         # its generated man page
dos doctor                                   # confirm the active workspace + taxonomy
```

Then: edit `dos.toml` to add your reasons/lanes; write a renderer in `renderer.py`
and register it via `entry_points` when you're ready to package. You never touch
the `dos` package.

---

## Status legend

- ✅ **shipped** — works today; tests pin the contract.
- 🔜 **design** — the seam is specified here and the shape is proven by example,
  but the resolver/wiring is not yet in the package. Build order is driven by
  demand; reasons shipped first because it's the kernel's most-exercised syscall.

---

## Where DOS keeps its state — `.dos/` and `~/.dos` (✅ shipped)

DOS no longer scatters its own state into the repo it serves. The generic
default (`default_config`) keeps two homes; `job` (`job_config`) is unaffected and
keeps its inherited `docs/` layout. See `docs/75_state-home-plan.md` for the full
contract.

- **`<workspace>/.dos/`** — DOS's per-project emissions: `runs/` (UTC-named run
  dirs; lineage lives in each `run.json`), `lane-journal.jsonl`, `leases/`,
  `verdicts/`, `soaks/`, and `project.json` (the identity card). It is
  **auto-created on the first *write*** (a `dos lease` / a captured
  `dos arbitrate --force`) and ships a self-ignoring `.gitignore` (`*` +
  `!.gitignore`), so a host repo needs no `.gitignore` edit. **Read-only syscalls
  — `verify` / `man` / `doctor` / `decisions` / `judge` — write nothing**: run
  one in a stranger's repo and no `.dos/` appears. Safe to delete; `dos reindex`
  rebuilds the central view from what survives.
- **`$DOS_HOME`** (`~/.dos`, or `$DISPATCH_HOME` › `$XDG_DATA_HOME/dos` ›
  `%APPDATA%\dos` › `~/.dos`) — a machine-local, **rebuildable projection** over
  every workspace DOS has served: `projects/index.jsonl` (one row per project) +
  `decisions.jsonl` (resolved-decision digests). It is never the source of
  truth — `dos reindex` regenerates it by walking the live `.dos/` dirs.

The home tier adds three read-only verbs (they write nothing, like `man`/`doctor`):

```bash
dos projects                  # the cross-project registry DOS has indexed
dos learn lane-refusals       # which lanes get force-overridden most, across all repos
dos learn wedge-hotspots      # which repos accrue the most decisions
dos learn oracle-calibration  # resolved decisions by reason CATEGORY — the JUDGE/ORACLE
                              # calibration signal (the category comes from the active
                              # ReasonRegistry, so a declared reason lights this up too)
dos reindex [--prune]         # rebuild the projection from the .dos/ dirs
```

`dos learn` is the fifth surface a single reason declaration lights up (after
emit / verify / refuse / `man`): the cross-project aggregate is **data that
informs tuning, never monkeypatching** — the same closed-enums-as-data thesis as
`[reasons]`.
