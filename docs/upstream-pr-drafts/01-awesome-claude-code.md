# PR draft — hesreallyhim/awesome-claude-code

**Target:** <https://github.com/hesreallyhim/awesome-claude-code>
**Type:** list entry (single line + short description), under the Hooks (and/or MCP)
section.
**Before filing:** read the repo's `CONTRIBUTING` / entry format — this list uses a
scripted entry format (often a CSV/template + a generated README). Match whatever the
current template is rather than hand-editing the README if the repo regenerates it.
Confirm the Hooks section still exists and accepts third-party tools.

---

## Suggested entry (Hooks section)

> **[dos-kernel](https://github.com/anthony-chaudhary/dos-kernel)** — A deterministic,
> model-free referee you wire into Claude Code's hooks in one command
> (`dos init --hooks claude-code .`). It denies a structurally-refused tool call at
> `PreToolUse`, refuses a `Stop` on an unverified "done" (checked against git
> ancestry, not the agent's self-report), and re-surfaces a stalled stream at
> `PostToolUse`. Advisory MCP and exit-code tiers too. Vendor-neutral; ~0 model calls
> on the hot path; fails to "no opinion," never to a false clear.

## Suggested entry (MCP / tooling section, if a separate listing fits)

> **[dos-kernel (MCP)](https://github.com/anthony-chaudhary/dos-kernel)** — Exposes a
> lie-detector for agent work as MCP tools: `dos_verify` ("did it actually ship?",
> from git), `dos_commit_audit` ("does this commit's claim match its diff?"),
> `dos_arbitrate` ("can two agents run without colliding?"), and a fabricated-legal-
> citation check (`dos_citation_resolve`). Zero Python coupling, no API key
> (deterministic). `{ "mcpServers": { "dos": { "command": "dos-mcp" } } }`.

## PR title

`Add dos-kernel (deterministic done-/deny-check via hooks + MCP)`

## PR body

Adds **dos-kernel**, a small vendor-neutral referee that fills the decision behind
Claude Code's `PreToolUse → deny` slot and the empty `Stop`-hook slot.

- **Hooks (enforcement):** `dos init --hooks claude-code .` writes `.claude/settings.json`
  binding three hooks — deny a refused call before it runs, refuse a `Stop` on an
  unverified done, re-surface a stalled stream.
- **MCP (advisory):** the bundled server exposes `dos_verify` / `dos_commit_audit` /
  `dos_arbitrate` / `dos_citation_resolve` over stdio, no key.
- **Exit-code (hook-less):** any runner can read a `dos` verb's exit code.

It does the one thing the agent structurally can't do for itself: check "did it ship"
against the artifact (git ancestry) instead of the narration. Deterministic (~0 model
calls), advisory + fail-to-abstain (a crash degrades to "no opinion," never a false
clear), Apache-2.0, on PyPI as `dos-kernel`.

**Done-condition a reviewer can check:**
`pip install dos-kernel && dos init --hooks claude-code --dry-run .` previews the
exact `.claude/settings.json` merge it would write; `dos hosts` prints the live
support matrix. The byte-identical `PreToolUse` deny envelope is provable from the
repo (`tests/test_hook_dialect.py`).

---

### Filing notes (for the human)

- This list has historically regenerated its README from a structured source —
  **don't hand-edit the README** if so; add the row to the source the maintainers
  point to and let their tooling rebuild.
- Pick the *one* most-fitting section if the list discourages multiple entries per
  project (Hooks is the primary fit for Flip A; MCP is secondary).
- Keep the entry to the list's house length; the longer text above is the PR body,
  not the entry.
