# PR draft — pascalporedda/awesome-claude-code (hooks hub)

**Target:** <https://github.com/pascalporedda/awesome-claude-code>
**Type:** a hook entry (this repo's stated vision is "a central hub for Claude Code
hooks" and it explicitly invites "open an issue or a PR and add your own").
**Before filing:** confirm the current entry format (it organizes by hook event —
PreToolUse / PostToolUse / Stop / SubagentStop). DOS spans three events, so it may fit
as one cross-event entry or one row per event; match the repo's convention.

---

## Suggested entry

> ### dos-kernel — deterministic done-/deny-check (PreToolUse + Stop + PostToolUse)
>
> [dos-kernel](https://github.com/anthony-chaudhary/dos-kernel) wires a small,
> model-free referee into all three tool-lifecycle hooks with one command:
>
> ```bash
> pip install dos-kernel
> dos init --hooks claude-code .   # writes .claude/settings.json
> ```
>
> - **PreToolUse** — denies a *structurally refused* call before it runs (a typed
>   reason from a closed vocabulary, not a substring blocklist).
> - **Stop** — refuses a stop on an unverified "done": it checks the claim against
>   **git ancestry**, not the agent's self-report, so a "shipped it" that never
>   landed is caught.
> - **PostToolUse** — re-surfaces a stalled stream (no git/world progress).
>
> Deterministic (~0 model calls; a native ~10 ms fast path), advisory and
> fail-to-abstain (a crash → "no opinion", never a false clear), vendor-neutral,
> Apache-2.0. There's also an MCP (advisory) and an exit-code (hook-less) tier.

## PR title

`Add dos-kernel — a deterministic referee spanning PreToolUse / Stop / PostToolUse`

## PR body

Adds **dos-kernel** to the hub. It's the *decision* behind the hooks this list
collects: where most PreToolUse examples hand-write a per-case rule, DOS supplies a
deterministic, model-free verdict (`verify` from git ancestry, `refuse` from a closed
reason vocabulary), and it's the one example that also fills the usually-empty `Stop`
slot with a real done-check.

One command wires all three events into `.claude/settings.json`. Preview without
writing: `dos init --hooks claude-code --dry-run .`. The live host matrix is `dos
hosts`.

**Why it fits the hub's vision:** you're aiming at a hook manager / curated set; DOS
is a hook *and* an installer for it (`dos init --hooks`), so it slots in as both a
listed hook and a reference for the "one command to wire it" pattern.

**Done-condition:** `pip install dos-kernel && dos init --hooks claude-code --dry-run .`
shows the exact merge; `python -m pytest -q tests/test_hook_dialect.py` from a
checkout proves the emitted envelope.

---

### Filing notes (for the human)

- This repo is the *friendliest* target (it asks for PRs by name) — good first file,
  and a merged entry here is a reference for the larger `hesreallyhim` list.
- If the repo organizes strictly one-entry-per-event, file three short rows pointing
  at the same project rather than one wide entry.
