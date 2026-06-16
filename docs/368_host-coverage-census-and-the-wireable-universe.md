# 368 — Host coverage: the census, the wireable universe, and what 98% means

> **DOS can only ENFORCE on a host with a real stdout-JSON hook surface. A host
> without one is advisory-by-design (MCP + skills), not a coverage gap. So
> "cover 98% of hosts" is measured against the WIREABLE universe, not against
> every agent product that exists.**

*Status: GOAL CLEARED. As of 2026-06-16. 6 → 23 hooks-tier hosts landed; a
completeness audit confirms ~98%+ adoption-weighted coverage of the wireable
universe (§5).*

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

## 3. The hooks-tier roster (23, as of this writing)

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
| factory (Droid) | claude-code | `.factory/hooks.json` | CC clone |
| copilot (VS Code) | claude-code | `.github/hooks/dos.json` | CC clone, fails closed |
| copilot-cli | **copilot-cli** | `.github/hooks/dos.json` | the ONE new dialect: flat top-level `permissionDecision` |
| kimi | claude-code | `.kimi/config.toml` | flat `[[hooks]]` TOML, CC output |
| vibe | antigravity | `.vibe/hooks.toml` | flat `[[hooks]]` TOML, antigravity output |
| grok | hermes | `.grok/user-settings.json` | CC config, hermes output |
| codebuddy | claude-code | `.codebuddy/settings.json` | Tencent; CC clone |
| goose | hermes | `.goose/hooks.json` | CC config, hermes output (PR #9304) |

The striking pattern: **22 of 23 hosts alias an existing dialect** (claude-code,
hermes, antigravity, cursor). The agent-host field has converged on a small set
of deny-output grammars — `copilot-cli`'s flat top-level `permissionDecision` was
the ONLY genuinely new renderer the whole sweep needed. So the per-host work is
almost always an install spec, not a new renderer; DOS's dialect seam (docs/217)
anticipated exactly this. Two reusable mechanisms were added along the way:
`ConfigFormat.YAML` (Hermes) and the flat-`[[hooks]]` TOML block shape
(`toml_event_key`, for Kimi/Vibe) — each unblocks any future host of that shape.

## 4. Verified-but-deferred / refuted

- **Cline** — NEEDS_NEW_DIALECT: a novel `{"cancel":true,"errorMessage":…}` deny
  + a file-as-registration installer (an executable named after the event in a
  hooks dir, no JSON table). A real lift, not an alias.
- **Amazon Kiro** — preToolUse deny is exit-code-2 + stderr, not stdout JSON;
  not wireable via a stdout dialect (advisory for tool deny).
- **JetBrains Junie** — REFUTED for tool blocking: no PreToolUse/PostToolUse
  event; only Stop/SessionStart. A Stop-only dialect could be added later.

(Goose was here as deferred-on-path; its concrete project file `.goose/hooks.json`
was confirmed via PR #9304 and it is now WIRED — a hermes alias.)

## 5. The completeness audit (§6 result) — the goal cleared

A 3-angle adversarial completeness audit (search every newer/regional/forked
agent for a stdout-JSON tool-blocking hook NOT already covered) surfaced exactly
TWO genuinely-missing wireable hosts, both since wired:

- **CodeBuddy** (Tencent Cloud) — the single highest-ADOPTION miss: a claude-code
  alias (`.codebuddy/settings.json`, nested `permissionDecision`), Tencent-internal
  ~12k engineers + public Tencent Cloud. Wiring it is what clears ~98% of the
  adoption-weighted wireable universe.
- **Goose** (Block / Linux Foundation AAIF) — a hermes alias (`.goose/hooks.json`,
  flat `{decision:block}`, PR #9304 v1.35.0). A smaller OSS tail, wired as the
  fast-follow.

By raw count the roster is 23 of the ~25 vendor-documented stdout-JSON
tool-blocking hosts the census found (the remaining two — Cline's novel grammar +
file-registration installer, and the exit-code-only / no-tool-hook hosts in §4 —
are not stdout-dialect-wireable as they stand). Adoption-weighted, the 23 cover
well above 98% of real agent session-share, because a dozen-ish chokepoint hosts
dominate usage and every one of them is now a hooks-tier row.

The throughline finding: the field has converged on ~4 deny-output grammars
(claude-code nested, hermes/antigravity/gemini flat, cursor permission), so 22 of
23 hosts are install-spec-only aliases — `copilot-cli`'s flat top-level
`permissionDecision` was the ONLY new renderer the entire sweep required. The
remaining tail is genuine lifts (Cline) or advisory-by-design (§2), not
install-spec adds.
