# Playbook 01 — onboard a repo in 10 minutes

> **Goal:** go from `pip install` to your first *verified* ship on a repo you
> already have — no plan documents, no schema, no ceremony. By the end you'll
> have asked the kernel "did this actually ship?" and gotten an
> evidence-backed answer instead of a self-report.

This works on **any git repo**. DOS is stateless about which repo it serves; it
reads ground truth out of git history and answers from that. You do not have to
adopt the dispatch workflow, the lanes, or anything else to get value from
`verify` on day one.

---

## Step 0 — install

```bash
pip install dos-kernel         # dist name is dos-kernel; `import dos` / `dos` cmd unchanged
# or, from a checkout:  pip install -e .
dos --help
```

> The bare PyPI name `dos` is an unrelated package — always install `dos-kernel`.

That's the whole install. The kernel's only hard dependency is PyYAML; the MCP
server and the renderer plugins are opt-in extras.

## Step 1 — point DOS at your repo

Pick the repo you want to ask about. DOS resolves *which workspace it serves* in
this order: an explicit `--workspace`, then the `DISPATCH_WORKSPACE` env var,
then the current directory. For a quickstart, just `cd` in:

```bash
cd ~/code/my-service
dos doctor --workspace .
```

`doctor` reports the **active workspace** — what DOS thinks it's looking at
before you've configured anything:

```text
DOS v0.26.0
workspace root      /home/you/code/my-service
execution-state     /home/you/code/my-service/.dos/execution-state.yaml
plans glob          docs/**/*-plan.md
stamp convention    generic (any/no dir prefix)  [style=grep]
concurrent lanes    main
exclusive lanes     global
autopick ladder     main
admission predicates disjointness, self-modify
judges (JUDGE rung)  abstain, llm, operator-decision, similarity
evidence sources    null, ci_status, citation_resolve, os_acceptance, paste_log  (verify: git-only)
enforce handlers     observe
overlap policy      prefix*  (ratio_max=0.333; prefix floor always on)
stall reader        REPEATING>=3, STALLED>=5  (ignore_tools: (none))
supervisor target   1  (count_spinning_as_alive=yes, reap_stalled=yes, spin_halt_after=off)
is git workspace    yes
runtime hooks       none wired   (run `dos init --hooks <runtime>` to bind)
layout style        dos
environment print   <hash>  (kernel v0.26.0 @ <sha>; py 3.13.7; <os>)
  declared tools    (none declared)
dos home            /home/you/.dos  (0 project(s) indexed)
```

A few things to notice:

- **`stamp convention` defaults to `generic (any/no dir prefix)`.** With no
  `dos.toml`, `verify`'s git rung recognizes a bare `<SERIES>: <PHASE>` /
  `<SERIES><PHASE>:` ship in any commit subject, with no required directory
  prefix — so it works out of the box on a repo it's never seen. If *your* repo
  stamps ships with a stricter shape (e.g. `src/AUTH: AUTH2 ...`), you declare
  that in `[stamp].subject_dirs` to *tighten* the grammar — which is exactly what
  Step 3 shows. (Tightening is the safe direction to move *toward*: a too-strict
  grammar fails visibly with `via none`, whereas a too-loose one risks a *false*
  "shipped." The generic default is the most permissive recognizer, so on your
  own repo you generally only ever narrow it. See
  [HACKING.md](../../docs/HACKING.md) for the full grammar.)
- **No `.dos/` was created.** `doctor`, `verify`, and `man` are read-only — run
  them in a stranger's repo and nothing is written. State appears only on the
  first *persisting* command (a `lease`, a captured `--force`).
- **`admission predicates` shows the two always-on built-ins** (`disjointness`,
  `self-modify`) on a clean install. If you also `pip install` a plugin that
  registers a `dos.predicates` entry point — e.g. the example
  [`examples/dos_ext/`](../dos_ext/) adds `budget-guard` — it appears here too, in
  conjunction order. The blocks in these playbooks pin the clean-install pair.
- **`runtime hooks` shows `none wired`** until you bind the verdict to an agent
  runtime. After [Step 5](#step-5--bind-the-verdict-to-the-agent-you-already-run)
  (`dos init --hooks cursor .`) this reads `cursor (4)` — your confirmation that the
  enforcement hooks actually took, not just that the command exited 0.

## Step 2 — ask the truth syscall a question

Here's the payoff. Suppose someone (a teammate, an agent, you three weeks ago)
claims that phase **`AUTH2`** of the **`AUTH`** workstream shipped. Don't take
their word for it — ask:

```bash
dos verify --workspace . AUTH AUTH2
```

`verify` does **not** read a status file an agent wrote. It looks, in order, at:

1. a run **registry** (if your repo has one — most don't, that's fine),
2. **git history** — does a commit subject actually attribute this phase as
   shipped, and is it an ancestor of `HEAD`?

If nothing attributes it, you get the honest answer:

```text
NOT_SHIPPED AUTH AUTH9 (via none)
```
```text
exit code: 1
```

The `(via none)` is the kernel saying *"I found no evidence at all"* — not
"someone said no." If a commit **does** attribute it, you get:

```text
SHIPPED AUTH AUTH2 9f70e39 (via grep-subject)
```
```text
exit code: 0
```

`(via grep-subject)` = "I found the phase token in a commit **subject** in git
history," and `9f70e39` is the commit that proves it. **The exit code is the
verdict**, so this drops straight into a script or a CI gate (see
[the CI cookbook](cookbook-ci-integration.md)).

> **What counts as "attributes this phase"?** A commit subject like
> `AUTH2: ship token refresh endpoint` (the phase id, then a colon) or
> `AUTH: AUTH2 — ...` (series, then the phase). That's the *generic* grammar.
> Your repo may stamp ships differently — that's Step 3.

## Step 3 — teach DOS your repo's conventions

`dos init` scaffolds a `dos.toml` — the **one** policy file that crosses into
your tree. Everything else (the oracle, the arbiter, the refusal vocabulary)
stays in the installed package.

```bash
dos init .
```

Open the generated `dos.toml`. The two tables that matter on day one:

```toml
# dos.toml

# Where DOS looks for plan documents (optional — verify works without any).
[paths]
plans_glob = "docs/**/*-plan.md"

# How YOUR repo stamps a shipped phase in commit subjects.
# With no [stamp] table, the grammar is generic (any/no dir prefix). Declare your
# own here to tighten it to your convention:
[stamp]
style        = "grep"
subject_dirs = []          # [] = the generic "<SERIES><PHASE>:" / "<SERIES>: <PHASE>" shape
# subject_dirs = ["src", "lib"]   # ...or: ships are scoped under these dirs, e.g. "src/AUTH: AUTH2"
```

- **`subject_dirs = []`** is the right choice for most repos: it recognizes a
  bare `AUTH2: ...` or `AUTH: AUTH2 ...` commit subject with no directory prefix.
- **`subject_dirs = ["src", "lib"]`** is for repos that scope ship commits under
  a path, e.g. `src/AUTH: AUTH2 — ...`.

Re-run `doctor` to confirm DOS now reads your grammar:

```bash
dos doctor --workspace .
#   stamp convention    generic (any/no dir prefix)  [style=grep]
```

And **prove the grammar matches reality** with the completeness check:

```bash
dos doctor --workspace . --check
```

If you declared a `[stamp]` grammar that recognizes **none** of your repo's
own recent ship-shaped commits, `--check` fails loud (exit 1) with a finding —
because that means `verify` would silently answer `via none` for real ships.
A clean repo prints nothing and exits 0. This is the
"openness is only safe if you can prove completeness" rail.

## Step 4 — (optional) describe your lanes

If you only want `verify`, you're done — skip this. If you intend to run a
*fleet* (several agents in parallel), declare your **lanes**: the units of
concurrency the arbiter keeps from colliding.

```toml
[lanes]
concurrent = ["api", "web"]      # may run in parallel — iff their trees are disjoint
exclusive  = ["infra"]           # runs alone (touches blast-radius state)
autopick   = ["api", "web"]      # the order the arbiter walks when picking a free lane

[lanes.trees]
api   = ["src/api/**"]
web   = ["web/**"]
infra = ["deploy/**", "terraform/**"]
```

Now the arbiter can answer "may this loop start on the `web` lane?" — see your
archetype playbook for the full fleet flow. The
[`acme-store`](../workspaces/acme-store/) workspace is a worked example of
exactly this taxonomy.

## Step 5 — bind the verdict to the agent you already run

Everything so far is something *you* invoke. To make DOS act **inside a live agent
loop** — denying a refused tool-call before it runs, refusing a stop on an
unverified claim — wire its hooks into your runtime. One command, on whichever agent
you use:

```bash
dos init --hooks claude-code .   # writes/merges .claude/settings.json
dos init --hooks cursor .        # writes/merges .cursor/hooks.json
dos init --hooks codex .         # writes/merges .codex/config.toml
dos init --hooks gemini .        # writes/merges .gemini/settings.json
```

This wires three SHIPPED hooks into that host's own config file, each rendered into
the bytes that host honors:

| DOS hook | what it does | fires on |
|---|---|---|
| `dos hook pretool` | **denies** a structurally-refused call (e.g. a `SELF_MODIFY` of the kernel's own path) before it runs | `PreToolUse` / `beforeShellExecution`+`beforeMCPExecution` / `BeforeTool` |
| `dos hook posttool` | re-surfaces a **stalled tool stream** (advisory — never blocks) | `PostToolUse` / `afterFileEdit` / `AfterTool` |
| `dos hook stop` | **refuses to stop** on an unverified "done" claim (the verify-on-stop gate) | `Stop` / `stop` / `AfterAgent` |

The block is **merged** into any existing config — your other hooks and keys survive
— and re-running is idempotent (`--force` repairs an existing DOS block). `--with-hooks`
is the back-compat alias for `--hooks claude-code`.

> **Hooks vs. MCP — enforce vs. advise.** This step is the **enforcement** path: the
> *host* denies a call on a DOS verdict. There is also an **advisory** path — the
> agent *calls* `dos_verify` / `dos_status` itself — which is the MCP server, wired
> separately by adding `dos-mcp` to the host config (see
> [the MCP README](../../src/dos_mcp/README.md), which has the per-host snippets for
> all four runtimes). They compose: hooks stop a bad action, MCP lets the agent
> check its own work. Full design of the installer: [docs/221](../../docs/221_the-cross-vendor-hook-installer.md).

> **What it does NOT do.** Wiring the hooks does not make DOS deny *everything* — the
> default pre-tool posture only refuses *structural* hazards (the `SELF_MODIFY` /
> typed-refusal set); a broader behavioral deny is an opt-in handler. And no hook ever
> rewrites your tool's arguments — it surfaces a *fact* to re-read, never a corrected
> value (the byte-author floor).

## Step 6 — when data isn't enough: a driver

`dos.toml` is a *list* — a fixed set of lanes you typed out. That covers most
repos. Two cases outgrow it: your lanes must be **computed** at startup (derived
from an env var, a service registry, a monorepo manifest — anything a static TOML
list can't express), or you want a **model-backed JUDGE** to rule on the residue
the deterministic oracle abstains on. For those you graduate from data to a
**driver**: a small `dos/drivers/<host>.py` that builds the same two things the
TOML declared — a `LaneTaxonomy` constant and a `<host>_config(workspace)` factory
— but in code. The kernel never imports it; the CLI loads it by name:

```bash
dos arbitrate --driver myhost --lane api --kind cluster --leases '[]'
```

`--driver myhost` resolves `dos.drivers.myhost.myhost_config(workspace)` by
convention (`--job` is just the back-compat spelling of `--driver job`). Copy
[`src/dos/drivers/workshop.py`](../../src/dos/drivers/workshop.py) — the
reference template — and the full driver/plugin map in
[HACKING.md](../../docs/HACKING.md) ("Host policy-pack driver").

## What you have now

- `verify` answers "did it ship?" from evidence, on demand, for any phase.
- `dos.toml` teaches DOS your ship grammar — checked for completeness.
- (optional) lanes are declared, so a fleet can be arbitrated.
- (optional) the verdict is **bound to your agent runtime** — a refused call is
  denied and a false "done" is refused, inside the live loop, on the host you run.

## Where to go next

- **Run a fleet on this shape** → your archetype playbook
  ([web service](02_polyglot-web-service.md) /
  [library](03_oss-library-release.md) / [pipeline](04_data-ml-pipeline.md) /
  [infra](05_infra-monorepo.md)).
- **Put `verify` in CI** → [`cookbook-ci-integration.md`](cookbook-ci-integration.md).
- **Call it from code, not the CLI** → [`cookbook-python-api.md`](cookbook-python-api.md).
- **It's behaving strangely** → [`06_debug-a-stuck-fleet.md`](06_debug-a-stuck-fleet.md).

---

### Recap — the commands

```bash
pip install dos-kernel                   # NOT `pip install dos` (that PyPI name is unrelated)
dos doctor --workspace .                 # what does DOS see?
dos verify --workspace . AUTH AUTH2      # did it ship?  (exit 0=yes, 1=no)
dos init .                               # scaffold dos.toml
#   edit [stamp].subject_dirs to match your commit convention
dos doctor --workspace . --check         # prove the grammar matches real ships (exit 1 on mismatch)
dos init --hooks cursor .                # bind the verdict to your runtime (or claude-code/codex/gemini)
```
