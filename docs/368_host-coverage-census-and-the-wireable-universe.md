# 368 — Host coverage: the census, the wireable universe, and what 98% means

> **DOS can only ENFORCE on a host with a real stdout-JSON hook surface. A host
> without one is advisory-by-design (MCP + skills), not a coverage gap. So
> "cover 98% of hosts" is measured against the WIREABLE universe, not against
> every agent product that exists.**

*Status: IN PROGRESS. As of 2026-06-16. 6 → 15 hooks-tier hosts landed.*

## 0. What this is

A census of the industry agent-host landscape (web-grounded, mid-2026) and an
adversarial verification of which hosts have a real hook surface DOS can wire
enforcement into. Adding a host is one `HostHookSpec` row in
`drivers/hook_dialects.py` + a `dos.hook_installs` entry point + (only if the
output grammar is novel) a dialect renderer + Go transcode parity. The
`dos hosts` matrix is DERIVED from the registry, so a data-row add propagates
to the matrix, the `--json`, and the docs footer for free (docs #93).

## 1. The discipline: never ship a fabricated grammar (docs/294)

A wrong hook grammar is the worst failure mode this layer has: config that
*looks* wired but denies nothing — a silent fail-open that converts a real DENY
(SELF_MODIFY, a refused tool) into a proceed. The kernel exists to catch that
class of lie; it must not mint one. So every host is **adversarially verified**
against the vendor's OWN docs/source before wiring: the exact config path, the
exact event names, and the exact stdout JSON that DENIES — quoted, not guessed.
A grammar that is plausible but undocumented is REFUTED.

Two distinctions the verification turns on:

- **CONFIG shape ≠ OUTPUT shape.** A host can have a Claude-Code-*shaped* config
  (group-wrapped `{matcher, hooks:[{type,command}]}`) but a *different* deny
  OUTPUT. Devin and OpenHands have CC-shaped configs but flat
  `{"decision":"block"/"deny"}` output → they alias the hermes/antigravity
  *output* dialect, NOT claude-code. Confusing the two ships a fail-open.
- **Tool-blocking ≠ any hook.** JetBrains Junie has hooks, but only Stop /
  SessionStart — no PreToolUse. It cannot deny a *tool* by stdout, so it is
  REFUTED for tool enforcement (a Stop-only dialect is a separate, weaker thing).
  Amazon Kiro's preToolUse deny is exit-code-2 + stderr, NOT stdout JSON — also
  not wireable through the dialect mechanism.

## 2. The wireable universe (the honest denominator)

A host is **wireable** iff it exposes a user-scriptable lifecycle hook that
BLOCKS a tool (or refuses a stop) by what a hook command prints to stdout as
JSON. Coverage = covered / (covered + addressable-but-unwired). Hosts with NO
such surface are advisory-by-design — they get the kernel syscalls + the MCP
server + the skill pack, which is real support, just not enforcement.

**Advisory-by-design (NO stdout hook surface — not a gap):** Trae (docs/294),
Aider, Zed, Warp, Roo Code, Void, PearAI, and the orchestration FRAMEWORKS
(LangGraph, CrewAI, AutoGen, the Agents SDKs) whose interception is in-process
Python/TS callbacks, not a stdout-command hook. OpenClaw (in-process TS return)
and SwarmClaw (no pre-tool veto) are the docs/278 precedent.

## 3. The hooks-tier roster (15, as of this writing)

| Host | Dialect aliased | Config | Notes |
|---|---|---|---|
| claude-code | claude-code | `.claude/settings.json` | the unshadowable default |
| codex | claude-code | `.codex/config.toml` | CC envelope verbatim |
| cursor | cursor | `.cursor/hooks.json` | flat + version |
| gemini | gemini | `.gemini/settings.json` | moment-dependent deny |
| antigravity | antigravity | `.agents/hooks.json` | CC config, top-level decision output |
| claude-cowork | claude-code | `.claude/settings.json` | shared surface |
| hermes | hermes | `cli-config.yaml` | YAML; flat `{decision:block}` |
| augment | claude-code | `.augment/settings.json` | pure CC clone |
| devin | hermes | `.devin/hooks.v1.json` | CC config, hermes output |
| cursor-cli | cursor | `.cursor/hooks.json` | shared with cursor |
| crush | antigravity | `crush.json` | flat config, antigravity output |
| qwen | claude-code | `.qwen/settings.json` | CC clone |
| continue | claude-code | `.continue/settings.json` | CC clone |
| openhands | antigravity | `.openhands/hooks.json` | CC config, antigravity output |
| tabnine | antigravity | `.tabnine/agent/settings.json` | CC config, antigravity output |

The striking pattern: **most new hosts alias an existing dialect** (claude-code,
hermes, antigravity, cursor). The agent-host field has converged on a small set
of deny-output grammars, so the per-host work is almost always an install spec,
not a new renderer. DOS's dialect seam (docs/217) anticipated exactly this.

## 4. Verified-but-deferred / refuted

- **Cline** — NEEDS_NEW_DIALECT: a novel `{"cancel":true,"errorMessage":…}` deny
  + a file-as-registration installer (an executable named after the event in a
  hooks dir, no JSON table). A real lift, not an alias.
- **Mistral Vibe** — a flat `[[hooks]]` TOML array-of-tables with a `type` field
  (`before_tool`/`after_tool`), NOT the CC-shaped `[[hooks.EVENT]]` that
  `merge_toml` writes. Needs a TOML-merge variant. Antigravity output.
- **Amazon Kiro** — preToolUse deny is exit-code-2 + stderr, not stdout JSON;
  not wireable via a stdout dialect (advisory for tool deny).
- **JetBrains Junie** — REFUTED for tool blocking: no PreToolUse/PostToolUse
  event; only Stop/SessionStart. A Stop-only dialect could be added later.
- **Goose** — hermes-output alias, but its config path is plugin-root-relative;
  the concrete workspace install path needs confirmation before wiring.

## 5. Remaining work toward the goal

GitHub Copilot (Agent Mode + CLI), Grok CLI, Kimi CLI, Droid (Factory) — under
verification. The Vibe TOML-variant and Goose-path questions are small follow-on
lifts. The denominator is still firming up (the full census found ~81 candidate
products; the wireable subset is the live count), but the trajectory is clear:
the field's grammar convergence means most remaining wireable hosts are
install-spec-only adds against the four dialects DOS already renders.
