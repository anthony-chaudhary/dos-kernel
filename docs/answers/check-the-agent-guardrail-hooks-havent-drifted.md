# How do I check my agent's trust-gate hooks haven't silently stopped enforcing?

> Run `dos doctor --wiring` — it re-reads each runtime's config and reports `WIRED` / `DRIFTED` / `NOT_WIRED` with the event count, so a guardrail that quietly un-bound shows up as a verdict instead of staying invisible. `pip install dos-kernel`; a `DRIFTED` line means re-run `dos init --hooks <runtime>` to repair. The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter; never install that.

## The short answer

A guardrail that silently un-wired is worse than none: it reads as enforced, so nobody re-checks it, while every commit it was supposed to gate sails through. The dangerous failure isn't "the hook denied something it shouldn't" — it's "the hook stopped firing and said nothing." You can't catch that by asking the agent ("are your hooks on?" is a self-report), and you can't catch it by reading the agent's transcript. You catch it by re-reading the artifact the agent didn't author: the runtime's own config file, on disk, right now.

`dos doctor --wiring` does exactly that. It is a READ-ONLY probe — it writes nothing, creates no config — that opens each known runtime's config file under your workspace and asks which of the DOS hook events are actually bound there. `WIRED` means all events are present; `NOT_WIRED` means the runtime was never set up; `DRIFTED` means it was wired once and some events have since gone missing (an edited `settings.json`, a host upgrade, a merge that dropped a block). The output names the config path and the count, e.g. `claude-code DRIFTED .claude/settings.json (1/3 events)` — so you see at a glance both which host drifted and how far. The fix is one command: `dos init --hooks <runtime>` re-binds the missing events, and a re-run of the probe confirms it's back to `WIRED`.

## The evidence

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| `doctor --wiring` re-reads each runtime's config and reports which DOS hook events are bound | per-host status `WIRED` / `DRIFTED` / `NOT_WIRED` with the event count (e.g. `1/3 events`) and the config path it inspected | the runtime's config file on disk, which the host/operator authored — not the agent claiming its guardrails are on | [`docs/CLI.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI.md) |
| The probe is READ-ONLY — a drift check never writes or repairs, it only reports | a `doctor` probe creates no config; a malformed/unreadable host config contributes an empty (NOT_WIRED) result, never a crash | the file read happens at the CLI boundary; the verdict is a pure function of the bytes found there | [`docs/CLI.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI.md) |
| The wiring is `dos init --hooks`; the re-check confirms it's still bound | a `DRIFTED` host is repaired by re-running `dos init --hooks <runtime>`, then re-probed to confirm `WIRED` | `dos init` merges into the host's own config (it doesn't clobber the user's other hooks) — the binding lives in the config, not in the agent's word | [`AGENTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/AGENTS.md) |

No benchmark J here — this is a wiring-integrity probe, not a count of caught lies. The number that matters is the event ratio (`1/3`), and it's read straight off the config file, not asserted.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos doctor --wiring --workspace .
```

```text
runtime hooks:
  claude-code   DRIFTED   .claude/settings.json   (1/3 events)
  cursor        WIRED     .cursor/...             (3/3 events)
  codex         NOT_WIRED  —                      (0/3 events)

claude-code drifted — re-run:  dos init --hooks claude-code .
```

The `DRIFTED` line is the catch: the runtime was wired once, but two of its three DOS hook events have since gone missing from `.claude/settings.json` — so the guardrail looked installed and was only one-third firing. Repair and re-confirm:

```bash
dos init --hooks claude-code .     # re-binds the missing events (merges, doesn't clobber)
dos doctor --wiring --workspace .  # now reports: claude-code  WIRED  (3/3 events)
```

## What this does — and does not — certify

It certifies **binding, not behavior**. `dos doctor --wiring` confirms the DOS hook events are present in the runtime's config — that the host *will* invoke the gate on the events it covers. It does not run the gate, replay a tool call, or prove the hook would deny a specific bad action; that is what the per-verb verdicts (`dos commit-audit`, `dos verify`) are for once the hooks are firing. It also reads only the runtimes DOS knows about: a `NOT_WIRED` means "this host has no DOS events bound here," which is the honest answer for a host you never wired — not a claim that the host is misconfigured. And it is read-only by design: it tells you a host drifted, it never silently re-wires one (that is `dos init`'s job, which you run knowingly).

## Sources / reproduce

- [`docs/CLI.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI.md) — the runtime-hook-status probe behind `dos doctor --wiring`: READ-ONLY, per-host, reports the bound DOS events and the config path; a malformed host config degrades to an empty result, never a crash.
- [`AGENTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/AGENTS.md) — `dos init --hooks <runtime>` is the wiring (and the repair); it merges into the host's own config without clobbering the user's other hooks.
- [How to add a guardrail to a coding agent that has no plugin or hook system](how-to-add-a-guardrail-to-a-coding-agent-with-no-plugin-system.md) — when a host has no hook seam at all, ride the exit-code contract instead.
- [Why does my agent ignore the rules in CLAUDE.md?](why-does-my-agent-ignore-the-rules-in-claude-md.md) — the deeper reason a written instruction isn't enforcement, and why a bound hook is.
- [FAQ: Does DOS work with Claude Code, Cursor, Codex, Gemini CLI, or other agent runtimes?](../FAQ.md#does-dos-work-with-claude-code-cursor-codex-gemini-cli-or-other-agent-runtimes) — the three integration surfaces (MCP, hooks, exit-code).

## Also asked as

- How do I know my agent's hooks are still actually enforcing?
- My guardrail hooks look installed — how do I check they didn't silently break?
- Did my `.claude/settings.json` lose its DOS hook entries?
- How do I detect hook drift in Claude Code / Cursor / Codex?
- Is there a way to verify trust-gate hooks are still bound after a config edit?
- My agent says its guardrails are on — how do I confirm without trusting it?
- How do I re-check and repair agent hook wiring?
- Why did my enforcement hooks stop firing without any error?

> The kernel is the part that doesn't believe the agents.
