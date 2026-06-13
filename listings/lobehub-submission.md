# LobeHub MCP Marketplace — submission packet (DOS)

> **LIVE (2026-06-13):** Listed at <https://lobehub.com/mcp/anthony-chaudhary-dos-kernel>
> — identifier `anthony-chaudhary-dos-kernel`, v0.26.0, status `published`, claimed.
> Imported via `lhm plugin submit` under the `anthony-chaudhary` GitHub identity.
> To enrich the install command + tool list on the page, publish an
> `lhm.plugin.json` with `lhm plugin publish` (see below).

**The real submission path is the `@lobehub/market-cli` (`lhm`) CLI, not a web
modal.** `https://market.lobehub.com/s/publish-mcp` is a *markdown skill doc* for
that CLI. The completing command is one non-interactive call once you're logged in:

```bash
npx -y @lobehub/market-cli plugin submit https://github.com/anthony-chaudhary/dos-kernel
```

Two one-time browser steps gate it: `lhm login` and `lhm github connect` (OAuth).
Then `plugin submit` imports the repo as a new listing (async, ~minutes); poll
`lhm plugin list --output json` for the assigned identifier.

> **Windows gotcha (fixed here):** the CLI crashes with `spawn start ENOENT`
> because it spawns `start` without a shell and node ignores `PATHEXT`. Fix: put a
> real `start.exe` on PATH (copy `explorer.exe` → `%TEMP%\lhm-shim\start.exe`) and
> prepend that dir to PATH before running `lhm login` / `github connect`. The
> browser then opens and the localhost:51234 OAuth-callback listener survives.

To **publish a new version** later, write `lhm.plugin.json` and run
`lhm plugin publish --dir <abs-path>` (see the LobeHub skill doc for the manifest).

The fields below were used for the original listing import and double as a
GitHub-issue fallback (lobehub/lobehub feature request) if the CLI import ever
fails silently — see the bottom of this file.

---

## Step 1 — the one field that matters

**GitHub repository URL** (the modal scrapes README, LICENSE, and the MCP config from this):

```
https://github.com/anthony-chaudhary/dos-kernel
```

## Step 2 — confirm / override the scraped fields

| Field | Value |
|-------|-------|
| **Identifier** | `dos-kernel` (or `anthony-chaudhary-dos-kernel` if it wants owner-prefixed) |
| **Name** | `DOS — the trust substrate for agent fleets` |
| **Author / Publisher** | `Anthony Chaudhary` |
| **Homepage** | `https://pypi.org/project/dos-kernel/` |
| **License** | `MIT` |
| **Description (short)** | Verify what agents actually shipped, arbitrate file collisions, refuse with structured reasons — a domain-free trust substrate for fleets of autonomous coding agents. |
| **Tags** | `agents`, `git`, `verification`, `code-review`, `multi-agent`, `developer-tools`, `ci`, `trust` |

## Step 3 — installation method (transport = stdio, runtime = uvx)

Paste this as the **one-click install / config JSON**:

```json
{
  "mcpServers": {
    "dos": {
      "command": "uvx",
      "args": ["--from", "dos-kernel[mcp]", "dos-kernel"]
    }
  }
}
```

> This matches the official MCP Registry's verified launch
> (`uvx --from "dos-kernel[mcp]" dos-kernel`). The `[mcp]` extra carries the MCP
> server framework; the core install is near-stdlib. Each tool takes an optional
> `workspace` (a repo path); it defaults to the server's working directory. Set
> `DISPATCH_WORKSPACE` to pin one.
>
> If the host runs the server from an already-installed package instead of `uvx`,
> the command is simply `dos-mcp` (or `dos-kernel`) with no args.

## Step 4 — quality checklist (LobeHub asks these; answer honestly)

| Check | Status |
|-------|--------|
| Has README | ✅ <https://github.com/anthony-chaudhary/dos-kernel/blob/master/README.md> |
| Provides ≥1 installation method | ✅ `uvx "dos-kernel[mcp]"` (stdio) + `pip install dos-kernel[mcp]` |
| Has ≥1 Skill (Tool) | ✅ 9 tools (listed below) |
| Has LICENSE | ✅ MIT |
| In the official MCP Registry | ✅ `io.github.anthony-chaudhary/dos-kernel`, status `active` |
| Includes Prompts | ❌ Tools-only server |
| Includes Resources | ❌ Tools-only server |

**Logo (400×400 PNG):** `listings/logo.png` in this repo — upload if the modal
asks for one, or point it at
`https://raw.githubusercontent.com/anthony-chaudhary/dos-kernel/master/listings/logo.png`
(verify that path resolves on the public repo first; otherwise upload the local file).

## The 9 tools (for the "what does it do" field)

- `dos_verify` — did (plan, phase) actually ship? Answered from git ancestry, never the agent's word.
- `dos_commit_audit` — does a commit's message match what its diff actually did?
- `dos_arbitrate` — may two agents edit concurrently, or do their file-trees collide?
- `dos_refuse_reasons` / `dos_check_reason` — refuse with a structured reason from a closed vocabulary.
- `dos_recall` — is this recalled memory still true against git + the working tree?
- `dos_doctor` — machine-readable workspace report (lanes, stamp grammar, paths).
- `dos_status` — one folded, fail-closed status fact for a run.
- `dos_citation_resolve` — does a cited legal case actually exist in a third-party reporter?

---

## Fallback — GitHub issue body (paste into a `lobehub/lobehub` feature request if the modal fails)

> Title: `[Request] Add DOS (dos-kernel) MCP Server to the MCP marketplace`

We'd like to list **DOS — the Dispatch Operating System** on LobeHub's MCP
marketplace (<https://lobehub.com/mcp>). Filing here as the fallback in case the
Submit modal fails after the Turnstile check (per #14133's guidance).

Index our GitHub repository:

- **Repo**: https://github.com/anthony-chaudhary/dos-kernel
- **Name**: DOS — the trust substrate for agent fleets
- **Publisher**: Anthony Chaudhary
- **License**: MIT
- **Official MCP Registry entry**: `io.github.anthony-chaudhary/dos-kernel` — https://registry.modelcontextprotocol.io/v0/servers?search=dos-kernel (status `active`)

**What it does**

A domain-free trust substrate for fleets of autonomous coding agents — the
kernel that doesn't believe the agents. **9 tools**: verify what an agent
actually shipped (from git, not its word), audit a commit's message against its
own diff, arbitrate file-collisions between concurrent agents, refuse with a
structured reason from a closed vocabulary, re-verify a recalled memory against
the working tree, resolve a cited legal case against a third-party reporter, and
report a fail-closed run status.

**Transport & Install**

- Transport: `stdio`
- Runtime: `uvx` (or `pip install dos-kernel[mcp]`)

```json
{
  "mcpServers": {
    "dos": {
      "command": "uvx",
      "args": ["--from", "dos-kernel[mcp]", "dos-kernel"]
    }
  }
}
```

Quality checklist:

| Check | Status |
|-------|--------|
| Has README | ✅ https://github.com/anthony-chaudhary/dos-kernel/blob/master/README.md |
| Provides ≥1 installation method | ✅ uvx + pip |
| Has ≥1 Skill (Tool) | ✅ 9 tools |
| Has LICENSE | ✅ MIT |
| In the official MCP Registry | ✅ `io.github.anthony-chaudhary/dos-kernel` (active) |
| Includes Prompts | ❌ Tools-only |
| Includes Resources | ❌ Tools-only |

Already live on: the official MCP Registry, PyPI (`dos-kernel`), and the Claude
Code plugin marketplace.

Thanks!
