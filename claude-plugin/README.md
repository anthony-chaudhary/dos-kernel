# DOS — the Claude Code plugin

> **One install for the three runtime surfaces.** This plugin bundles the DOS
> **hooks**, the DOS **MCP server**, and the **generic skill pack** so a Claude Code
> user binds the trust substrate to their fleet in a single step — instead of
> hand-editing `settings.json`, registering an MCP server, and copying skills out of
> the wheel separately.

The plugin ships **JSON + markdown only**. The brains — the `verify` / `arbitrate` /
`refuse` syscalls the hooks and MCP server call — ship as the **`dos-kernel` Python
package**. So the one prerequisite is:

```bash
pip install "dos-kernel[mcp] @ git+https://github.com/anthony-chaudhary/dos-kernel.git"
```

(straight from the public repo — `dos-kernel` is pre-PyPI; once it publishes this
shrinks to `pip install "dos-kernel[mcp]"`), installed into the **same interpreter
Claude Code launches** (`python` on its PATH).
The `[mcp]` extra is what the bundled MCP server needs; the core hooks need only the
base package. If the package isn't importable, the hooks **fail safe** (emit nothing,
exit 0 — they never break a turn) and the MCP server prints an install hint in `/mcp`.

## Install

```bash
# 1. the prerequisite (see above) — straight from the public repo while pre-PyPI
pip install "dos-kernel[mcp] @ git+https://github.com/anthony-chaudhary/dos-kernel.git"

# 2. inside Claude Code:
/plugin marketplace add anthony-chaudhary/dos-kernel
/plugin install dos-kernel@dos

# 3. confirm + orient (a read-only check skill):
/dos-kernel:dos-setup
```

To test a local clone before publishing, point Claude Code at this directory:

```bash
claude --plugin-dir ./claude-plugin
```

## What's in the bundle

| Surface | File | What it does |
|---|---|---|
| **Hooks** | [`hooks/hooks.json`](hooks/hooks.json) | `PreToolUse` → `dos hook pretool` (DENY a structurally-refused call before it runs) · `PostToolUse` → `dos hook posttool` (re-surface a stalled tool stream, advisory) · `Stop` → `dos hook stop` (refuse to stop on an unverified claim). Served by the bundled native `dos-hook` binary (`bin/`) in ~10 ms, with a Python fallback. |
| **Observability** | [`bin/dos-hook`](bin/) | the native binary counts and logs every hook call to `.dos/metrics/observations.jsonl`. Fold it into a report — counts by verb/outcome, the delegate + stop-block rates, per-verb latency — with `"${CLAUDE_PLUGIN_ROOT}/bin/dos-hook" stats` (or the `/dos-kernel:dos-stats` skill). Read-only; no scrape endpoint (a hook is one-shot — the durable log is the surface). |
| **MCP server** | [`.mcp.json`](.mcp.json) | launches `python -m dos_mcp.server` — exposes `dos_verify`, `dos_arbitrate`, `dos_commit_audit`, `dos_refuse_reasons` / `dos_check_reason`, `dos_status`, `dos_recall`, `dos_doctor` as tools. |
| **Skills** | [`skills/`](skills/) | the generic skill pack (`dos-next-up`, `dos-dispatch`, `dos-witness-claim`, …) + the plugin-only `dos-setup` (onboarding) and `dos-stats` (observability) skills. Namespaced as `/dos-kernel:<skill>`. |
| **Catalog** | [`../.claude-plugin/marketplace.json`](../.claude-plugin/marketplace.json) | the repo-root marketplace that `/plugin marketplace add` reads; its one plugin entry points back here (`source: ./claude-plugin`). |

### Why `python -m`, not the `dos` / `dos-mcp` scripts

Both the hooks and the MCP server invoke the package via `python -m dos.cli …` /
`python -m dos_mcp.server`, **not** the `dos` / `dos-mcp` console scripts. pip puts
those scripts in the interpreter's `Scripts`/`bin` dir, which is **not guaranteed to
be on the PATH** of the subprocess Claude Code spawns for a hook or an MCP server.
`python -m` resolves the module through the interpreter directly, so it works
wherever the package is importable — the robust choice for a bundle a stranger
installs.

### Fail-safe by design

The three hook verbs are the **shipped** DOS sensors, and every one degrades to a
no-op on any failure (no stdin, bad JSON, an I/O error, the package not importable):
it prints nothing and exits 0. The `PreToolUse` deny is **advisory by default** — a
behavioral deny needs a ruling handler wired; out of the box the plugin only
observes and re-surfaces, never silently blocks your work. (DOS is a PDP, not a PEP:
it reports and proposes; the runtime acts.)

## Maintenance — the skills are generated, not hand-edited

The bundled `skills/` are a **faithful copy** of the single source under
[`../src/dos/skills/`](../src/dos/skills/) (which also ships as wheel package-data),
plus the one plugin-only `dos-setup` skill authored by the build. A Claude Code
plugin must physically contain its skills — a component path can't escape the plugin
root, and a symlink outside the marketplace is dropped — so the copy is regenerated
by a script rather than maintained by hand:

```bash
python scripts/build_plugin.py          # regenerate claude-plugin/skills/ from source
python scripts/build_plugin.py --check  # verify in sync (exit 1 if drifted); writes nothing
```

**Do not edit `claude-plugin/skills/*/SKILL.md` directly** (except `dos-setup`,
whose content lives in `scripts/build_plugin.py`). Edit the source under
`src/dos/skills/`, then re-run the build. The lockstep is pinned by
[`tests/test_plugin_manifest.py`](../tests/test_plugin_manifest.py), which fails if
the copy drifts, if a hook stops naming a real verb, if the MCP server doesn't build,
or if the plugin version falls out of step with the package.

## Where this sits in the layering

The plugin **operates on the package, never inside it** — the same one-way arrow as
the release scripts and the `.claude/` dev tooling (see the repo's
[CLAUDE.md](../CLAUDE.md), "Four things live OUTSIDE the four layers"). It `import`s
nothing; it shells `dos` verbs and launches `dos_mcp`. Nothing under `src/dos/`
depends on it. It is a **distribution surface** for the kernel, not part of the
kernel.
