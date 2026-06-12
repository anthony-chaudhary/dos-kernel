# DOS demos — runnable in a throwaway directory

Three self-contained proofs. Each spins up a **fresh `mktemp` git repo**, runs the
real thing, and cleans up after itself — no agents, no fleet, nothing left behind.

> **The AUTH story is the canonical example.** The tokens (`AUTH`/`AUTH1`/`AUTH2`),
> the named features (the login endpoint / the password reset), and the one real
> commit subject are declared once in `src/dos/_demo_story.py`; every surface that
> retells it (these scripts, the figures, the README parts, the QUICKSTART, the
> plan-doc example) is pinned to the same strings by
> `tests/test_canonical_example_lockstep.py`. Retell the story in any genre you
> like — but copy the canonical strings, or the lockstep test will name the drift.

| Demo | What it proves | Run |
|---|---|---|
| [`verify_demo.sh`](verify_demo.sh) · [`.ps1`](verify_demo.ps1) | the **truth syscall** — an agent claims a phase shipped; DOS asks git, not the agent. | `bash examples/demo/verify_demo.sh` |
| [`clobber_demo.sh`](clobber_demo.sh) · [`.ps1`](clobber_demo.ps1) | the **arbiter** — two agents, one file: the silent lost update with no referee, then the same moment replayed as a typed refusal (and a re-admission once the lane frees). | `bash examples/demo/clobber_demo.sh` |
| [`plugin_smoke.sh`](plugin_smoke.sh) · [`.ps1`](plugin_smoke.ps1) | the **Claude Code plugin** works installed into an **isolated, non-DOS repo** — all three runtime surfaces (hooks · MCP server · skill pack). | `bash examples/demo/plugin_smoke.sh` |

## `plugin_smoke.sh` — the plugin works for a stranger

The plugin ([`claude-plugin/`](../../claude-plugin/)) ships **JSON + markdown
only**; the brains ship as the `dos-kernel` pip package. So the honest question is
not "does it pass its own tests in the source tree?" but **"does it work when a
stranger installs it into a repo DOS has never seen?"** This script answers that:

1. **Stands up a non-DOS repo** — a throwaway git repo with three top-level dirs
   and **no `dos.toml`, no `src/dos/`**. The plugin must adjudicate it by resolving
   the workspace through `cwd` / `--workspace`, never `__file__`.
2. **Installs the plugin standalone** — copies `claude-plugin/` the way
   `/plugin install` clones it, so a component path that escapes the plugin root
   (`../src/dos/skills`) is unreachable and the skills must be **real files**
   physically inside the bundle (a symlink would be dropped on install).
3. **Exercises all three surfaces** exactly as Claude Code would (`python -m`,
   `cwd` = the project):
   - **Hooks** — the three lifecycle verbs (`pretool` / `posttool` / `stop`). Benign
     inputs → observe → exit 0; **garbage stdin still exits 0** (the fail-safe that
     guarantees a hook never breaks a turn).
   - **MCP server** — the `dos_*` tools answer correctly against the stranger repo
     (`dos_doctor` falls back to the **generic default** lanes, `dos_verify` returns
     an honest no-evidence verdict, `dos_arbitrate` admits disjoint trees and
     refuses a collision).
   - **Skill pack** — every installed `SKILL.md` parses, dir-names match frontmatter,
     and the generic skills name no host.

### Two MCP depths — and when each runs

The MCP surface is checked at two depths, because they prove different things:

| Mode | What runs | When to use it |
|---|---|---|
| **default** | `build_server()` + call the registered tools | **always** — fast (no stdio round-trips), needs only the `[mcp]` extra the plugin already requires. Proves the tools **answer correctly** for a repo with no `dos.toml`. |
| `--full` | additionally spawn `python -m dos_mcp.server` over **real stdio** (cwd = the isolated repo — the exact `.mcp.json` contract): initialize → `list_tools` → `call_tool` | when you want to prove the **transport** Claude Code actually launches, not just the tool logic. Slower; needs an MCP client. If `--full` is asked for but no client is importable, the script **says it skipped** rather than fake transport coverage. |

```bash
bash examples/demo/plugin_smoke.sh          # fast: tool logic
bash examples/demo/plugin_smoke.sh --full   # also the live stdio transport
# Windows:
pwsh examples/demo/plugin_smoke.ps1
pwsh examples/demo/plugin_smoke.ps1 -Full
```

**Requires:** the `dos-kernel` package importable (`pip install -e '.[mcp]'` from
this repo) and `git`. The static form of these checks — manifest validity, the
hooks naming real verbs, version lockstep, skills-in-sync — is also pinned as a
test in [`tests/test_plugin_manifest.py`](../../tests/test_plugin_manifest.py);
this script is the **runnable, behavioral** companion to that static gate.
