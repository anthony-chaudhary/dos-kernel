# DOS — the Claude Code plugin

> **One install for the three runtime surfaces.** This plugin bundles the DOS
> **hooks**, the DOS **MCP server**, and the **generic skill pack** so a Claude Code
> user binds the trust substrate to their fleet in a single step — instead of
> hand-editing `settings.json`, registering an MCP server, and copying skills out of
> the wheel separately.

The plugin ships **JSON + markdown, plus the prebuilt native `dos-hook` binary**
(`bin/`, per-platform) that serves the hooks fast. The brains — the `verify` /
`arbitrate` / `refuse` syscalls the hooks and MCP server call — ship as the
**`dos-kernel` Python package**. So the one prerequisite is:

```bash
pip install "dos-kernel[mcp]"
```

(from PyPI; tracking unreleased `master` is
`pip install "dos-kernel[mcp] @ git+https://github.com/anthony-chaudhary/dos-kernel.git"`),
installed into the **same interpreter Claude Code launches** (`python` on its PATH).
The `[mcp]` extra is what the bundled MCP server needs; the core hooks need only the
base package. If the package isn't importable, the hooks **fail safe** (emit nothing,
exit 0 — they never break a turn) and the MCP server prints an install hint in `/mcp`.

## Install

```bash
# 1. the prerequisite (see above)
pip install "dos-kernel[mcp]"

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

### Private / company marketplace

Distributing DOS through your **own** internal marketplace — a private repo with
a pinned plugin source — instead of the public one above? The end-to-end
playbook (the four private `source` shapes, shipping the `dos-kernel` pip
prerequisite to the fleet, `strictKnownMarketplaces` lockdown, air-gapped
seeding, and the private-repo auth gotcha) is
[docs/PRIVATE-MARKETPLACE.md](../docs/PRIVATE-MARKETPLACE.md).

### Claude Cowork

Cowork runs the same Claude Code agent harness, and plugins (skills + MCP) work
there too — so this bundle's **MCP server and skills** serve a Cowork session as
they do a Claude Code one. The **hooks half is wired but dormant**: Cowork does not
fire hooks yet (anthropics/claude-code#63360), so until that closes the plugin's
working surfaces in Cowork are advisory. Details and the per-surface state:
[docs/298](../docs/298_claude-cowork-the-sixth-host-shared-surface.md).

## What's in the bundle

| Surface | File | What it does |
|---|---|---|
| **Hooks** | [`hooks/hooks.json`](hooks/hooks.json) | `PreToolUse` → `dos hook pretool` (DENY a structurally-refused call before it runs) · `PostToolUse` → `dos hook posttool` (re-surface a stalled tool stream, advisory) · `Stop` → `dos hook stop` (refuse to stop on an unverified claim). Served by the bundled native `dos-hook` binary (`bin/`) in ~10 ms, with a Python fallback. |
| **Observability** | [`bin/dos-hook`](bin/) | the native binary counts and logs every hook call to `.dos/metrics/observations.jsonl`. Fold it into a report — counts by verb/outcome, the delegate + stop-block rates, per-verb latency — with `"${CLAUDE_PLUGIN_ROOT}/bin/dos-hook" stats` (or the `/dos-kernel:dos-stats` skill). Read-only; no scrape endpoint (a hook is one-shot — the durable log is the surface). |
| **MCP server** | [`.mcp.json`](.mcp.json) | launches `python -m dos_mcp.server` — exposes `dos_verify`, `dos_arbitrate`, `dos_commit_audit`, `dos_refuse_reasons` / `dos_check_reason`, `dos_status`, `dos_recall`, `dos_citation_resolve`, `dos_doctor` as tools. |
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


### Multi-account/headless dispatch: prefer the project-skill fallback

A namespaced invocation such as `/dos-kernel:dos-dispatch-loop` is resolved from
the active account's plugin cache. When a fleet rotates `CLAUDE_CONFIG_DIR`, a
fresh account can have the plugin registration but not its cached skill files;
Claude Code currently reports `Unknown command` and may still exit 0. Do not use
that exit code as evidence that a worker started.

For unattended multi-account launchers, install the generic skills into the
workspace (`dos init --skills`) and invoke the git-tracked project skill instead:

```text
/dos-dispatch-loop --lane <LANE>
```

This bare form resolves from `.claude/skills/dos-dispatch-loop/SKILL.md` in the
repo, independent of each account's plugin cache. Before spawning a fleet, verify
that file exists; treat `Unknown command` or a run with zero kernel decisions as
a failed launch, even when the host process exits 0. Interactive single-account
sessions with a healthy plugin cache can continue using the namespaced form.
## Maintenance — the skills are generated, not hand-edited

The bundled `skills/` are a **faithful copy** of the single source under
[`../src/dos/skills/`](../src/dos/skills/) (which also ships as wheel package-data),
plus the plugin-only `dos-setup` and `dos-stats` skills authored by the build. A
Claude Code plugin must physically contain its skills — a component path can't
escape the plugin root, and a symlink outside the marketplace is dropped — so the
copy is regenerated by a script rather than maintained by hand:

```bash
python scripts/build_plugin.py          # regenerate claude-plugin/skills/ from source
python scripts/build_plugin.py --check  # verify in sync (exit 1 if drifted); writes nothing
```

**Do not edit `claude-plugin/skills/*/SKILL.md` directly.** For generic skills,
edit the source under `src/dos/skills/`; for the plugin-only `dos-setup` and
`dos-stats`, edit their authored content in `scripts/build_plugin.py`. Then
re-run the build. The lockstep is pinned by
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
