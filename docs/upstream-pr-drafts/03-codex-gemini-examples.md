# PR draft — Codex / Gemini hook-example repos

**Targets (pick by what's live when you file):**
- A community Codex starter-config repo (e.g. a `codex-setup` / `config.example.toml`
  collection) — add a DOS hook example to its sample `config.toml`.
- A Gemini CLI extensions/examples collection — DOS already ships a
  `gemini-extension.json` manifest, so the cleanest Gemini move is the **extensions
  gallery listing** (auto-crawled), and a hook example is the secondary.
- OpenAI's own Codex docs accept issues/PRs against the hooks reference if a worked
  third-party example is in scope — check `developers.openai.com/codex` repo policy
  before assuming.

**Before filing:** confirm the target's `config.toml` / `settings.json` example
format and that it welcomes a third-party tool example. The prior sweep's lesson
applies hardest here — read the actual sample file, don't assume one exists.

---

## Codex — sample `config.toml` block to contribute

```toml
# DOS — deterministic done-/deny-check. Install: pip install dos-kernel
# Wire automatically with:  dos init --hooks codex .
# (Note: Codex fires PreToolUse only on Bash / apply_patch / unified_exec / mcp
#  handlers today — DOS emits the right bytes; Codex calls the hook on those tools.)
[[hooks.PreToolUse]]
[[hooks.PreToolUse.hooks]]
type = "command"
command = "dos hook pretool --workspace . --dialect codex"

[[hooks.Stop]]
[[hooks.Stop.hooks]]
type = "command"
command = "dos hook stop --workspace . --dialect codex"
```

The deny envelope DOS emits here is **byte-identical to Claude Code's**
(`{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision":
"deny", "permissionDecisionReason": "…"}}`), which is exactly why Codex honors it with
no adapter. Prefer letting `dos init --hooks codex .` write this block (it merges into
an existing `config.toml`); the literal above is for a docs/example file.

## Gemini — the one-liner (extension) + the hook caveat

Gemini's cleanest path is the extension (no clone, no config edit):

```bash
pip install 'dos-kernel[mcp]'
gemini extensions install https://github.com/anthony-chaudhary/dos-kernel
```

For a **hook** example, carry the caveat or the example is silently wrong: Gemini's
config is Claude-Code-shaped (group-wrapped) but its **output** diverges — a
`BeforeTool` deny must be `{"decision":"deny"}`, not CC's nested `permissionDecision`.
`dos init --hooks gemini .` writes the correct shape; an example that copies the CC
envelope verbatim will fail open on Gemini 0.45.x. (This exact bug is documented in
the DOS repo, docs/268.)

## PR title (per target)

`Add a DOS (deterministic done-/deny-check) example`

## PR body

Adds a worked example of wiring **dos-kernel** — a deterministic, model-free referee —
into <Codex/Gemini>'s hooks. It denies a refused tool call before it runs and refuses
a premature "done" by checking git ancestry instead of the agent's self-report. ~0
model calls, vendor-neutral, Apache-2.0.

The example is grounded against <host>'s real hook contract (the byte-identical deny
envelope for Codex; the `{"decision":"deny"}` output divergence for Gemini), and the
one-command installer `dos init --hooks <host> .` writes it for a user automatically.

**Done-condition:** `pip install dos-kernel && dos init --hooks <host> --dry-run .`
previews the exact merge; `dos hosts` shows the live matrix and each host's caveat.

---

### Filing notes (for the human)

- Gemini: lead with the **extension listing** (auto-crawled gallery) — it's the
  highest-leverage Gemini surface and needs no PR; the hook example is secondary.
- Codex: a community `config.example.toml` repo is a friendlier first target than the
  official docs repo. Confirm the official docs repo accepts third-party tool
  examples before filing there.
- Carry the per-host caveat verbatim — an example missing it is the silent-fail-open
  failure DOS exists to prevent, which would discredit the entry.
