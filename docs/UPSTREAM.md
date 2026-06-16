# UPSTREAM.md — wiring DOS into a runtime's recommended examples

> **Who this page is for.** A maintainer of an agent runtime, or a curator of an
> `awesome-*` list, who wants the one-line, copy-paste recipe for "turn on a
> deterministic done-/deny-check" — already grounded against your runtime's real
> hook contract, so adopting it costs you no research. Everything below is the
> *proven* form: the bytes DOS emits, the file it writes, and the one honest caveat
> per host. Nothing here asks you to take a new dependency into your runtime — DOS
> runs as a hook command the user installs, never as code you ship.

DOS is a small, deterministic, vendor-neutral referee for agent work: it answers
*"did it ship?"* (`verify`, from git ancestry — never the agent's self-report),
*"may this call run?"* (`arbitrate`/`refuse`, pure admission control), and *"is it
still moving?"* (`liveness`). It binds to a runtime through the hook seam the runtime
already has — and the one fact that makes this a *recipe* rather than an integration
project is below.

## The one fact this page rests on

The coding-agent ecosystem **converged on Claude Code's `PreToolUse → deny`
envelope.** DOS emits exactly those bytes, and you can prove it in one command from a
DOS checkout:

```bash
python -c "
from dos.hook_dialect import ClaudeCodeDialect, HookVerdict, HookAction, HookMoment
from dos.drivers.hook_dialects import CodexDialect
import json
v = HookVerdict(action=HookAction.DENY, moment=HookMoment.PRE, reason='refused', context='')
print('byte-identical:', json.dumps(ClaudeCodeDialect().render(v)) == json.dumps(CodexDialect().render(v)))
"
# -> byte-identical: True
```

The deny payload both runtimes honor:

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "<why>"}}
```

So for a runtime that speaks this dialect, adopting DOS is a **config recipe against
a slot you already built**, not new code. The runtime already standardized the
enforcement *envelope*; DOS supplies the *verdict* the slot was waiting for.

## The recipe — one command per host

The user installs the kernel and points one command at their repo; it writes that
host's own hook-config file, merged into anything already there.

```bash
pip install dos-kernel          # the dist name; the bare `dos` on PyPI is unrelated

dos init --hooks auto .          # detect the runtime(s) this repo uses, wire them all
dos init --hooks claude-code .   # -> .claude/settings.json
dos init --hooks cursor .        # -> .cursor/hooks.json
dos init --hooks codex .         # -> .codex/config.toml
dos init --hooks gemini .        # -> .gemini/settings.json
dos init --hooks antigravity .   # -> .agents/hooks.json
dos init --hooks claude-cowork . # the SAME .claude/settings.json Claude Code reads
```

The authoritative, never-stale matrix is a verb, not this table: `dos hosts`
(`dos hosts --json` for tooling) prints every host DOS can wire — its tier, the
events it binds, its dialect, its config path, the exact command, and the host's own
caveat. If you are scripting a recommended-examples page, generate it from
`dos hosts --json` so it tracks the installer.

That binds three shipped hooks: `pretool` denies a structurally-refused call before
it runs, `stop` refuses a stop on an unverified "done," `posttool` re-surfaces a
stalled stream. This is the **enforcement** path (your host denies on a DOS verdict).
There is also a zero-config **advisory** path — the bundled MCP server, which any
MCP-speaking host calls over stdio with no hook wiring — and an **exit-code** path
for hook-less runners (`dos commit-audit … ; echo $?`). Pick the tier your runtime
supports; the recipe above is the enforcement tier.

## Per-host facts (each verified against the named version)

Each row is what an external example needs to be *correct*, including the one caveat
that, if omitted, would make the example silently wrong.

| Host | Config file | Dialect | The one caveat an example must carry |
|---|---|---|---|
| **Claude Code** | `.claude/settings.json` | `claude-code` (the default envelope) | none — this is the reference dialect |
| **OpenAI Codex CLI** | `.codex/config.toml` | `codex` (byte-identical to CC) | PreToolUse fires only on Bash / apply_patch / unified_exec / mcp handlers — DOS wires the right bytes; Codex won't call the hook on every tool (confirmed against the OpenAI Codex hooks docs) |
| **Google Gemini CLI** | `.gemini/settings.json` | `gemini` | config is CC-shaped (group-wrapped — that's why `gemini hooks migrate` exists), but the **output** diverges: a `BeforeTool` deny must be `{"decision":"deny"}`, not CC's nested `permissionDecision` (verified against Gemini CLI 0.45.2) |
| **Cursor** | `.cursor/hooks.json` | `cursor` | deny is top-level `{"permission":"deny"}`; entries are flat (no group wrapper); file requires `{"version": 1}` |
| **Google Antigravity** | `.agents/hooks.json` | `antigravity` | config is CC-shaped groups, but output is Gemini-shaped `{"decision":"deny"}` — a combination unique to this host |
| **Claude Cowork** | `.claude/settings.json` (shared with Claude Code) | `claude-code` | runs the CC harness, so wiring binds both; but the Cowork app does not *fire* hooks yet (anthropics/claude-code#63360) — its working surface is advisory until that closes |

A host with **no** row and no `dos hosts` entry has no user-scriptable hook seam
(e.g. ByteDance Trae today). DOS binds those **advisory-only** and `dos init --hooks
<that-host>` fails loud rather than writing a fake envelope the host would never read
— inventing fake enforcement is the exact failure this seam prevents. If your runtime
*has* a hook seam DOS doesn't cover yet, it is a new **driver** (a dialect renderer +
an install spec), never a kernel change — see [docs/217](217_the-cross-vendor-hook-dialect-seam.md)
and [docs/221](221_the-cross-vendor-hook-installer.md), and please open an issue with
the host's real deny grammar.

## Why a runtime would want this in its examples

- **It fills the slot you left empty.** Your `PreToolUse → deny` seam carries a
  decision; today every user hand-writes that decision inline, capability by
  capability. DOS is a deterministic, model-free decision they can drop in.
- **It is cheap and fail-safe on the hot path.** Every verdict is a near-stdlib
  computation (a git-ancestry read, a pure admission check) — ~0 model calls, and a
  native `dos-hook` fast path (~10 ms). It is advisory and **fail-to-abstain**: a
  crashing or slow referee degrades to "no opinion," never to a false clear and never
  to a wedged host. Its worst case is silence.
- **It is neutral.** DOS is nobody's runtime. A user mixing your agent with another
  vendor's, contending for one prod deploy, needs a referee that is not a contestant.
  That is the one job a runtime can't credibly ship for itself.

## Verify the claim before you adopt it

Don't take this page's word for it — that would be the exact anti-pattern DOS exists
to prevent. From a checkout (`pip install -e ".[dev,mcp]"`):

```bash
python -m pytest -q tests/test_hook_dialect.py tests/test_vendor_agnostic_kernel.py
dos hosts                       # the live support matrix
dos init --hooks <your-host> --dry-run .   # preview the exact merge, write nothing
```

The repo: <https://github.com/anthony-chaudhary/dos-kernel>. Contributions that add a
host or correct a dialect fact are welcome — read [CONTRIBUTING.md](../CONTRIBUTING.md)
and [AGENTS.md](../AGENTS.md) first.
