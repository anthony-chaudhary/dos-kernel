## Give your agent a lie detector (MCP)

The easiest way in doesn't involve writing any Python. Point the agent host you
already use at the bundled MCP server, then ask your agent to `dos_verify` its
own last claim. The first time it comes back `NOT_SHIPPED … (via none)` on work
the agent *swore* it finished, you'll see why this repo exists — in your
terminal, on your fleet.

Installed with the `[mcp]` extra (`pip install -e ".[mcp]"` from your clone — see
[Install](#install)), DOS exposes the syscalls as MCP tools — the truth tools
first (`dos_verify` "did it ship?", `dos_commit_audit` "does this commit's claim
match its diff?", `dos_status` one folded fact about a run), then
`dos_arbitrate` (may two workers run without colliding?), the structured-refusal
pair (`dos_refuse_reasons` / `dos_check_reason`), `dos_recall` (is this recalled
memory still true?), and `dos_doctor` (the workspace report) — so any
MCP-speaking host — Claude Desktop, Cursor, Cline, an Agent-SDK app — can call
the referee over JSON-on-stdio with zero Python coupling. Each verdict comes
back with a one-line interpretation of what it means for the agent's next move.
(See **[the MCP server surface](docs/80_mcp-server-surface.md)**.)

```jsonc
// claude_desktop_config.json — paste, restart, then say:
//   "use dos_verify to confirm you actually shipped that"
{ "mcpServers": { "dos": { "command": "dos-mcp" } } }
```

The MCP server is **advisory**: the agent *calls* the referee when it (or you)
thinks to. The per-host wiring for Cursor / Codex / Gemini is in
**[the MCP README](src/dos_mcp/README.md)** — all four are MCP clients, so this
works on every one of them with zero code.

### …then make the verdict *act* (hooks)

To go from "the agent can ask" to "the host won't let a bad call through", wire
DOS's hooks into the runtime you actually run. One command per host — it writes
that host's own hook-config file, merged into anything already there:

```bash
dos init --hooks claude-code .   # .claude/settings.json
dos init --hooks cursor .        # .cursor/hooks.json
dos init --hooks codex .         # .codex/config.toml
dos init --hooks gemini .        # .gemini/settings.json
dos init --hooks antigravity .   # .agents/hooks.json
```

That binds three shipped hooks: `pretool` denies a structurally-refused call
before it runs, `stop` refuses a stop on an unverified "done," `posttool`
re-surfaces a stalled stream. This is the **enforcement** path (the *host*
denies on a DOS verdict) — the complement to MCP's advisory path. Until
recently this spoke only Claude Code; it now installs across five hosts —
Claude Code, Cursor, Codex, Gemini, and Antigravity
([docs/221](docs/221_the-cross-vendor-hook-installer.md),
[docs/269](docs/269_antigravity-the-fifth-host.md)).
`--with-hooks` is the back-compat alias for `--hooks claude-code`.

Under the installer sits a pluggable dialect seam: the verdict is decided
once, then rendered into whatever JSON shape the host parses
([docs/217](docs/217_the-cross-vendor-hook-dialect-seam.md)) — so a runtime the
installer doesn't cover yet can still consume the same hooks. A sixth shipped
dialect speaks **Hermes**: `dos hook pretool --dialect hermes` emits the
`{"decision": "block", "reason": …}` object Hermes' `pre_tool_call` shell hook
reads (wire it in `cli-config.yaml`). A new host's dialect is a driver, never a
kernel edit.

Because these hooks run on *every* tool call, the core kernel logic on the hot
path is reimplemented in native Go — a `dos-hook` binary that ports the actual
decision predicates (the conjunctive-only lease-admission and
prefix-disjointness floor, the `verify()` grep rung, self-modify, the marker
budget, the WAL) rather than just shelling out to Python. It serves the
per-call verdict in ~10 ms — 16–43× faster than shelling
`python -m dos.cli hook …` (~0.25–0.8 s, dominated by interpreter cold-start) —
and is byte-identical to the Python kernel on the gated decision (the docs/124
parity contract, pinned by Go parity tests). It owns the common fast path and
falls back to the always-available Python verb for anything it doesn't yet
serve, so a machine without the binary degrades cleanly with no wiring change
([docs/125](docs/125_go-hook-fastpath-build-plan.md),
[docs/270](docs/270_go-hook-fastpath-benchmarks.md)). You don't build it
yourself: the per-platform wheels bundle the binary, so a wheel install gets
the native fast path with no Go toolchain — and any platform without a bundled
binary (including a plain source install) just runs the pure-Python path
([docs/286](docs/286_shipping-the-go-binary-through-pypi-per-platform-wheels.md)).
