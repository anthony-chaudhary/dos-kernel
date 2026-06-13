# Installing the DOS MCP server (for Cline and other AI agents)

This file is written for an **AI coding agent** (Cline, Cursor, Claude Desktop,
an Agent-SDK app) to read and act on. It installs the **DOS** Model Context
Protocol server — the deterministic referee that lets an agent check whether a
"done" / "shipped" / "fixed" claim is *actually* backed by ground truth (git
ancestry, the working tree, a commit's own diff) instead of trusting the agent's
own narration.

DOS needs **no API key, no secret, and no LLM** — it is deterministic. The only
optional setting is which git repo it adjudicates (it defaults to the launch
directory, which is correct for one server per repo).

## Step 1 — install the package

The MCP server ships in the `[mcp]` extra of the `dos-kernel` distribution (the
PyPI name is `dos-kernel`, **not** the bare `dos`, which is an unrelated
package):

```bash
pip install "dos-kernel[mcp]"
```

or, to keep it off the project's virtualenv:

```bash
uv tool install "dos-kernel[mcp]"      # puts `dos` and `dos-mcp` on PATH
```

This installs two console scripts: `dos` (the CLI) and `dos-mcp` (the MCP
server). No other system dependency is required (Python 3.10+).

## Step 2 — register the server with the host

Add this entry to the MCP-servers configuration. For **Cline**, this is the
`cline_mcp_settings.json` file (Cline → MCP Servers → Configure / "Edit
Configuration"):

```json
{
  "mcpServers": {
    "dos": {
      "command": "dos-mcp",
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

If launching via `uv` is preferred (no global install), use this form instead —
it needs no prior `pip install`:

```json
{
  "mcpServers": {
    "dos": {
      "command": "uvx",
      "args": ["--from", "dos-kernel[mcp]", "dos-mcp"],
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

The server runs **locally over stdio** on purpose: it reads *your* repo's git
history and working tree to decide whether a claim is real, so it must run next
to the repo rather than in a network sandbox that cannot see your history.

## Step 3 — confirm it is live

After the host reloads its MCP settings, the `dos` server should report as
connected and expose these tools:

- `dos_verify` — did a claimed (plan, phase) actually ship? (git ancestry, never self-report)
- `dos_commit_audit` — does a commit's subject match what its diff actually did?
- `dos_arbitrate` — may two workers edit concurrently without colliding?
- `dos_refuse_reasons` / `dos_check_reason` — refuse with a structured, verifiable reason
- `dos_status` — one folded status fact for a run (liveness · progress · region · resume)
- `dos_recall` — is this recalled memory still true against git now?
- `dos_citation_resolve` — does a cited legal case actually exist in a reporter? (the *Mata v. Avianca* witness)
- `dos_doctor` — the machine-readable workspace report

To verify from a terminal without the host, the server answers a standard MCP
`initialize` handshake on stdio:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.0.0"}}}' | dos-mcp
```

A JSON response naming `"serverInfo":{"name":"dos",...}` means the server is
installed and speaking MCP.

## Step 4 — use it

Once connected, ask the agent to check its own claims, e.g.:

> "Use `dos_commit_audit` to confirm your last commit actually did what its
> message says."

> "Before you tell me that's done, call `dos_verify` to confirm the phase
> actually shipped from git."

That is the whole point: the agent stops being the sole witness to its own work.
