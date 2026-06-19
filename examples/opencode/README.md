# opencode + DOS

[opencode](https://opencode.ai) is an **MCP** host. The DOS surface that fits it
is the MCP tool surface — `dos_verify`, `dos_arbitrate`, `dos_refuse_reasons`,
`dos_check_reason`, `dos_citation_resolve` — exposed by the `dos_mcp` server.
This directory is the standardized way to wire that server into an opencode
config so any opencode user gets the DOS tools after a restart, with no
hand-edited JSON.

> **Honesty note.** Wiring DOS as an MCP server is **advisory**: the model can
> *call* the verdict tools, but opencode does not hard-DENY a tool call the way a
> hook-enabled host (Claude Code / Cursor / Codex) does. opencode has no
> PreToolUse/Stop stdout-deny surface for `dos init --hooks` to target — that is
> why opencode is **not** in the `dos.hook_installs` registry. Host-level
> enforcement in opencode would need a TS plugin (`tool.execute.before`), which
> is future work.

## Prerequisites

- Python 3.11+
- The DOS server package (the `[mcp]` extra carries the MCP framework; the core
  install is near-stdlib and does not include it):

  ```bash
  pip install 'dos-kernel[mcp]'
  ```

  Verify it imports: `python -c "import dos_mcp"` (prints nothing, exits 0).

## Install (one command)

From anywhere:

```bash
python examples/opencode/init_opencode.py
```

This writes the `mcp.dos` block into your **global** opencode config
(`~/.config/opencode/opencode.json` — the same path on Windows), discovers the
interpreter that has `dos_mcp` installed, and performs a live `initialize`
handshake to prove the server launches before it claims success.

To wire a single project instead (writes `.opencode/opencode.json` in the cwd,
deep-merged with global at runtime):

```bash
python examples/opencode/init_opencode.py --scope project
```

Then **quit and restart opencode** — it loads `opencode.json` once at startup, so
the running session will not see the server until you restart.

What the wired block looks like:

```json
{
  "mcp": {
    "dos": {
      "type": "local",
      "command": ["C:\\Program Files\\Python313\\python.exe", "-m", "dos_mcp.server"],
      "enabled": true
    }
  }
}
```

The interpreter path is whatever the installer discovered — never a hand-typed
literal — so a path containing spaces (`Program Files` on Windows) is fine: it
is one argv element, not a shell string.

## Verify

```bash
python examples/opencode/init_opencode.py --check
```

Read-only: reports whether the block is present and re-runs the launch
handshake. After a restart you can also confirm from opencode itself:

```bash
opencode mcp list
```

`dos` should appear and report healthy.

## Remove

```bash
python examples/opencode/init_opencode.py --remove
```

Deletes only the `mcp.dos` block; every other key in your config is preserved.

## Troubleshooting (the playbook)

**The `dos_*` tools don't appear after I ran the installer.**
opencode loads its config once at startup. You must fully **quit and restart**
opencode — a reload is not enough. Then `opencode mcp list` should show `dos`.

**`[error] no interpreter on this machine can import dos_mcp.`**
The installer could not find a Python that has the server package. Install it:
`pip install 'dos-kernel[mcp]'`, confirm with `python -c "import dos_mcp"`, and
re-run. If `dos_mcp` lives in a specific interpreter (conda, pyenv, pipx), point
at it explicitly: `python init_opencode.py --python /path/to/python`.

**`[verify] FAIL … ModuleNotFoundError: No module named 'dos_mcp'` (or `mcp`).**
The interpreter named in the wired command does not have `dos-kernel[mcp]`. This
happens when you install the package under one Python (e.g. pipx) but the
installer picked another. Re-run with `--python <the-right-one>`, or `--force`
to overwrite the stale entry.

**The server "keeps exiting" when I test it by hand.**
That is expected. A stdio MCP server reads requests until **stdin closes**, then
exits 0. If you launch it manually and it exits, that is just EOF — opencode
keeps stdin open and manages the process lifetime. The installer's `--check`
does the right thing (one `initialize`, then closes).

**opencode won't start: `ConfigInvalidError`.**
Your `opencode.json` is malformed. The installer refuses to write over a broken
file rather than risk clobbering it. Fix the JSON (or remove it and re-run the
installer to regenerate a clean one). To edit from inside opencode while a
project config is broken, start with
`OPENCODE_DISABLE_PROJECT_CONFIG=1 opencode` to skip the project file and load
globals only.

**I want the latest DOS.**
```bash
pip install -U 'dos-kernel[mcp]'
python examples/opencode/init_opencode.py --force
```
The installer is version-agnostic; `--force` rewrites the block in case the
interpreter path moved.

**The wired path has spaces and I want to hand-edit it. Don't.**
`command` is a JSON **array** of strings — opencode spawns it directly, no shell.
Keep it an array. If you collapse it to a single `"command"` string with a space,
opencode will fail to spawn it.

**I want a different server name or module.**
`--name my-dos --module dos_mcp.server`. The default name `dos` matches the
DOS documentation and the MCP-registry manifest (`server.json` at the repo root).

## How this relates to `dos init --hooks`

It doesn't — and deliberately so. `dos init --hooks <host>` writes a
`dos hook pretool/posttool/stop` command into a host that **denies** tool calls
over stdout (Claude Code, Cursor, Codex, Gemini, …). opencode has no such
surface, so it is not in `dos.hook_installs`; forcing it in would claim an
enforcement that does not exist. This MCP wiring is the honest equivalent for an
MCP-only host: the tools are available, the model is trusted to call them, and
the verdicts come from git/environment evidence — never from the agent's
self-report.
