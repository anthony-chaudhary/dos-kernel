# How do I wire a trust gate into Claude Code, Cursor, or Codex with one command?

> `pip install dos-kernel`, then `dos init --hooks auto .` — it detects the runtime your repo already uses (Claude Code, Cursor, Codex, Gemini, Antigravity, Claude Cowork) and binds the DOS verdict to that runtime's hook events, so a false "done" is refused at the hook with no `settings.json` hand-editing. The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter; never install that.

## The short answer

The trust gate is one command: `dos init --hooks auto .`. A self-report is not evidence — when an agent says "done," that sentence is bytes the agent itself authored, so the kernel reads the artifact the agent did *not* author (git ancestry, the commit's own diff) and hands back a verdict. The one move that wires that verdict into your runtime is binding it to the runtime's **hook events**. `dos init --hooks auto .` probes the config the host already keeps (Claude Code's `.claude/settings.json`, Cursor's / Codex's / Gemini's own file and format) and merges in the three shipped DOS hooks — `PreToolUse` → `dos hook pretool`, `PostToolUse` → `dos hook posttool`, `Stop` → `dos hook stop`. After that, when the agent tries to *Stop* on an unverified claim, the Stop hook refuses; you never hand-edited a config file.

You don't have to let it guess. `--hooks auto` detects and wires every runtime the repo uses; or name one explicitly — `--hooks claude-code`, `--hooks cursor`, `--hooks codex`, `--hooks gemini`. The merge is idempotent and preserves your other hooks, so re-running adds nothing already present and never clobbers your own entries.

## The evidence

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| One command detects the runtime(s) and wires the hooks | `dos init --hooks auto <repo>` — `auto` resolves to the host(s) the repo already uses; or name one (`claude-code`, `cursor`, `codex`, `gemini`, `antigravity`, `claude-cowork`). Hooks enforce; MCP advises | the consumer-moves table is checked against the shipped CLI, not narrated | [`AGENTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/AGENTS.md) |
| The bind is to the runtime's hook events, not a manual config edit | three shipped hooks: `Stop` → `dos hook stop` (refuse to stop on an unverified claim), `PreToolUse` → `dos hook pretool`, `PostToolUse` → `dos hook posttool` — merged into the host's own config file, idempotent | the hook event is fired by the runtime, and the verdict is read from git, not from the agent's "done" line | [`docs/CLI.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI.md) |
| The Claude Code plugin bundles the same hooks + MCP + skills | one `/plugin install` binds the three runtime surfaces; the hooks fail safe (emit nothing, exit 0) if the package isn't importable | the bundled native `dos-hook` binary serves the hook; the kernel verdict it calls is a pure function of git | [`claude-plugin/README.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/claude-plugin/README.md) |

The mechanism is qualitative on purpose here — this page describes *where the verdict binds*, not a measured block-rate. The hook events come from the runtime; the verdict they carry (`dos verify` / `dos hook stop`) is read from ground truth the judged agent could not author.

## The one command

```bash
pip install dos-kernel      # the PyPI name is dos-kernel, never bare `dos`
dos init --hooks auto .     # detect the runtime(s) this repo uses and wire the DOS hooks
```

```text
$ dos init --hooks auto .
wired claude-code → .claude/settings.json
  Stop        → dos hook stop      (refuse to stop on an unverified claim)
  PreToolUse  → dos hook pretool
  PostToolUse → dos hook posttool
already present: (none)
```

Inside Claude Code specifically, the bundled plugin wires the same three surfaces (hooks + MCP + skills) in one install — `pip install "dos-kernel[mcp]"`, then `/plugin install dos-kernel@dos`. Either path binds the same `Stop` hook; the plugin just also ships the MCP tools and the skill pack.

## What this does — and does not — certify

It certifies that the verdict is *bound* to the runtime: once the `Stop` hook is wired, a stop on an unverified claim is refused, and the refusal rests on git ancestry, not on the agent's narration. It does **not** make the agent correct — `dos hook pretool`'s deny is advisory by default (a ruling handler must be wired for a behavioral block; out of the box the plugin observes and re-surfaces). It does not judge whether the code is *good*, only whether a claimed ship is *witnessed*. And Claude Cowork is the partial case: it shares Claude Code's config surface, but the app doesn't fire hooks yet, so its working surface is MCP + skills until that closes.

## Sources / reproduce

- [`AGENTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/AGENTS.md) — the consumer-moves table: `dos init --hooks auto <repo>` and the supported runtimes.
- [`docs/CLI.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI.md) — the `dos init` / `dos doctor` verb reference, the three hook events, and the cross-vendor `--hooks <host>` form.
- [`claude-plugin/README.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/claude-plugin/README.md) — the Claude Code plugin path (hooks + MCP + skills in one install).
- [How to add a guardrail to a coding agent that has no plugin or hook system](how-to-add-a-guardrail-to-a-coding-agent-with-no-plugin-system.md) — the hook-less fallback: the exit code is the verdict.
- [Deterministic hook vs agent skill — which enforces?](deterministic-hook-vs-agent-skill-which-enforces.md) — why a hook enforces where a skill only advises.
- [FAQ: Does DOS work with Claude Code, Cursor, Codex, Gemini CLI, or other agent runtimes?](../FAQ.md#does-dos-work-with-claude-code-cursor-codex-gemini-cli-or-other-agent-runtimes) — the three surfaces (hooks, MCP, exit-code).

## Also asked as

- How do I wire a trust gate into Claude Code with one command?
- One command to add a verify hook to Cursor or Codex?
- How do I bind DOS to my agent runtime's hooks?
- Set up a hook gate for Claude Code / Cursor / Codex / Gemini automatically?
- How do I install the DOS hooks without editing settings.json by hand?
- What's the one command that makes an agent's "done" get checked at the Stop hook?
- Auto-detect my coding agent and wire the trust hooks?

> The kernel is the part that doesn't believe the agents.
