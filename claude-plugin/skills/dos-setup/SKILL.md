---
name: dos-setup
description: "One-time check that the DOS kernel plugin is ready to use — confirm the `dos-kernel` Python package is importable (the hooks and MCP server need it), report what the plugin bundled (hooks, the `dos` MCP tools, the generic skill pack), and point at the next skill to run. Use right after installing the dos-kernel plugin, or when `/mcp` shows the `dos` server failing to start or a `dos hook` command erroring. Read-only: it runs `dos doctor` to confirm wiring; it installs nothing and changes no config."
---

# dos-setup — confirm the plugin is wired, then point at the next move

> **The plugin ships JSON + markdown; the brains ship as a pip package.** The
> bundled hooks shell `python -m dos.cli …` and the bundled MCP server runs
> `python -m dos_mcp.server` — both need the `dos-kernel` package importable in the
> SAME Python that Claude Code launches. This skill verifies that one prerequisite
> and shows you what the plugin gave you. It is the only DOS skill that names the
> pip package; the rest are domain-free.

## Step 1 — Is the kernel importable? (the one prerequisite)

```bash
python -c "import dos, dos_mcp; print('dos', dos.__version__)"
```

- **Prints a version** → the package is installed and importable. Go to Step 2.
- **`ModuleNotFoundError: dos`** → the plugin's hooks/MCP cannot work yet. Install
  the package (the `[mcp]` extra pulls the MCP server framework the bundled server
  needs):

  ```bash
  pip install "dos-kernel[mcp]"
  ```

  Install it into the SAME interpreter Claude Code runs (the one `python` resolves
  to here). Then re-run Step 1. If `python` is the wrong interpreter, the hooks will
  silently no-op (they fail safe, by design) and the MCP server will print an
  install hint in `/mcp`.
- **`ModuleNotFoundError: dos_mcp`** → the core installed but not the MCP extra;
  re-run the `pip install "dos-kernel[mcp]"` line above.

## Step 2 — Does the kernel see this workspace? (confirm wiring)

```bash
python -m dos.cli doctor --workspace . --json
```

A JSON object with `lanes`, `paths`, and `stamp` means the kernel is reading THIS
repo. Skim it once — those values are the layout the bundled skills and the MCP
`dos_arbitrate` / `dos_verify` tools read instead of hardcoding anything. If this
repo has no `dos.toml`, the lanes are auto-derived from your top-level directories
and everything still works (the generic default).

## Step 3 — See it work end to end (60 seconds, throwaway repo)

Before reading the skills, watch the kernel make a real call. This runs in a
disposable git repo and cleans up after itself:

```bash
python -m dos.cli quickstart --driver workshop
```

It scaffolds a repo under the **`workshop` reference driver** (a worked
host-policy pack with two concurrent lanes + one exclusive release lane), then
shows BOTH halves of DOS against real artifacts:

- **the truth syscall** — `AUTH1` was committed → SHIPPED (exit 0); `AUTH2` was
  only claimed → NOT_SHIPPED (exit 1). The verdict comes from git, not the agent.
- **the admission kernel** — `frontend` and `backend` acquire *concurrently*
  (their file trees are disjoint), while `release` *refuses* (it is exclusive and
  runs alone). This is the concurrency policy your own lanes will get.

`workshop` is the **copy-me template** for declaring your own lanes — see Step 5.
(The plain `dos quickstart`, no `--driver`, shows just the truth-syscall half.)

## Step 4 — What the plugin bundled (so you know what you have)

- **Hooks** (active on every Claude Code launch in this project, all fail-safe —
  they emit nothing and exit 0 if anything goes wrong, so they never break a turn):
  - **PreToolUse** → `dos hook pretool`: can DENY a structurally-refused call before
    it runs (e.g. a self-modify of the kernel's own path). Advisory by default —
    a behavioral deny needs a ruling handler wired; out of the box it only observes.
  - **PostToolUse** → `dos hook posttool`: re-surfaces a stalled tool stream
    (a read-loop spinning on the same file) as advisory context.
  - **Stop** → `dos hook stop`: refuses to stop on an unverified claim, so the loop
    does not close on "I'm done" the git history does not back.

  Every one of those calls is **counted and logged**: the bundled native `dos-hook`
  binary appends one record per hook call to `.dos/metrics/observations.jsonl`, so the
  kernel emits evidence about its OWN behavior (docs/276). Read it back any time with
  `/dos-kernel:dos-stats` (or `"${CLAUDE_PLUGIN_ROOT}/bin/dos-hook" stats --workspace .`)
  — counts by verb/outcome, the delegate + stop-block rates, and per-verb latency.
- **MCP tools** (the `dos` server — check `/mcp` shows it connected): `dos_verify`
  (did a claim actually ship, from git not self-report), `dos_arbitrate` (may two
  workers run concurrently without colliding), `dos_commit_audit`, `dos_refuse_reasons`
  / `dos_check_reason`, `dos_status`, `dos_recall`, `dos_doctor`.
- **The generic skill pack** — the domain-free dispatch screenplays, namespaced
  under this plugin. The usual first one to read is `/dos-kernel:dos-next-up` (snapshot
  the portfolio) or `/dos-kernel:dos-dispatch` (plan-and-ship one lane safely). And
  `/dos-kernel:dos-stats` shows the kernel's own activity on this project.

## Step 5 — Adopt DOS in this repo (optional, makes it yours)

The plugin works against any git repo with zero config. To make the layout explicit
and editable (a `dos.toml` with your lanes, and the skills as local files you can
tune), scaffold it:

```bash
python -m dos.cli init .                    # lanes auto-derived from your top-level dirs
python -m dos.cli init . --example workshop  # OR start from the worked reference taxonomy
```

The bare form derives one concurrent lane per top-level source dir. The
`--example workshop` form scaffolds the same lanes the Step-3 demo used (two
concurrent clusters + an exclusive release lane) for you to rename to your real
concurrency topics. Both are optional — the bundled surfaces already work without
any `dos.toml`.

## What this skill deliberately does NOT do

- **It installs nothing.** It only TELLS you the pip command if the import fails;
  it never runs `pip` for you (that would mutate your environment without asking,
  and the target interpreter is ambiguous). You run the install.
- **It wires nothing.** The hooks and MCP server are already wired BY the plugin;
  this skill confirms them, it does not re-install or edit any config file.
- **It decides no ground truth.** Like every DOS skill, it shells `dos` verbs and
  reads the verdict; the kernel decides, the skill reports.
