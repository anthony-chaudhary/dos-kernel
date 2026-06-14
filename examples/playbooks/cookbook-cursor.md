# Cookbook — Cursor: wire DOS into the editor in one command

> **In plain words:** if you write code with the AI inside Cursor, this page
> hooks a small honesty-checker into Cursor for you, with one command. After
> that, the editor's AI can't quietly tell you a feature is "done" when it never
> landed — Cursor checks the AI's claim against your code's history, instead of
> taking the AI's word. It's for vibe coders already living in Cursor.

That one command wires three small checks into Cursor's own `.cursor/hooks.json`
file, so the editor's AI can't run an action the checker has already refused,
can't sit quietly on a stream that has stalled, and can't tell you a feature
shipped when your code's history doesn't back it. The checker (DOS) does the
checking, not the AI grading its own homework.

This is the **Cursor on-ramp**. The adapter ships today; this page just shows
you the one command and what it buys you. If you have never run a did-it-ship
check before, read the front-door quickstart first —
[`00b_did-my-ai-do-it.md`](00b_did-my-ai-do-it.md) ("Did my AI actually do
it?") — then come back here to bind the check to the editor you already use.

There are other ways to plug the check in (one the AI calls itself, one that's
just a command's exit code) — see
[`cookbook-exit-code-tier.md`](cookbook-exit-code-tier.md). For other editors
that take the same wiring (Codex, Gemini, Antigravity, Claude Code) the install
is the same one command with a different `--hooks` value.

---

## 1. Wire it — one command, verbatim output

Install the kernel, then point `dos init --hooks cursor` at your repo. This is
the **real output** of the command run against a fresh workspace:

```text
$ pip install dos-kernel        # the dist name; the bare `dos` on PyPI is unrelated
$ dos init --hooks cursor .
wrote /your/repo/dos.toml
no source dirs detected — scaffolded a single-writer 'main' lane
wired 4 DOS hook(s) into /your/repo/.cursor/hooks.json: beforeShellExecution, beforeMCPExecution, afterFileEdit, stop
  bound to cursor: a refused call is DENIED (pretool), a stalled stream is re-surfaced (posttool), a stop on an unverified claim is refused (stop).
  note: Cursor honors "failClosed": true on the PRE deny — add it per-hook if you want a DOS crash to BLOCK the call (DOS itself fails to PASS; the host's fail-on-crash direction is your call).
DOS workspace initialised. Try:  dos doctor --workspace .
```

> Run it in a repo that already has a `.cursor/` directory and the DOS block is
> **merged** into your existing `hooks.json` — your other hooks and keys are
> preserved (re-running is idempotent; pass `--force` only to repair a
> DOS-wired entry). Prefer a dress rehearsal? `dos init --hooks cursor --dry-run .`
> prints the merge and writes nothing.

### The file it writes — `.cursor/hooks.json`, verbatim

```json
{
  "hooks": {
    "afterFileEdit": [
      {
        "command": "dos hook posttool --workspace . --dialect cursor"
      }
    ],
    "beforeMCPExecution": [
      {
        "command": "dos hook pretool --workspace . --dialect cursor"
      }
    ],
    "beforeShellExecution": [
      {
        "command": "dos hook pretool --workspace . --dialect cursor"
      }
    ],
    "stop": [
      {
        "command": "dos hook stop --workspace . --dialect cursor"
      }
    ]
  },
  "version": 1
}
```

Two Cursor-specific facts to notice in that file:

- **`"version": 1`** — Cursor's `hooks.json` requires it; `dos init` adds it.
- **The PRE moment is TWO events** — `beforeShellExecution` *and*
  `beforeMCPExecution` — so a refused call is caught whether it is a shell
  command or an MCP tool. Both run the same `dos hook pretool` command, rendered
  with `--dialect cursor` so Cursor parses the verdict it honors (a top-level
  `{"permission": "deny"}`).

This is exactly what `dos init --help` describes as the cross-vendor wiring
(quoting the shipped help text):

> `--hooks <runtime>` (docs/221) is CROSS-VENDOR: it wires three SHIPPED hooks
> (the verdict bound to the runtime …) into the host you actually run — Claude
> Code, Cursor, Codex CLI, Gemini CLI, Google Antigravity, or Claude Cowork —
> each in its own file/format, with the matching `--dialect` so the host parses
> the verdict it honors.

---

## 2. What the three hooks do in Cursor's lifecycle

The three DOS verbs map onto three seams in Cursor's agent loop. Each is a
SHIPPED hook — the kernel renders the verdict; Cursor enforces it:

| Cursor event(s) | DOS verb | What it does |
|---|---|---|
| `beforeShellExecution`, `beforeMCPExecution` | `dos hook pretool` | **Deny a refused call before it runs.** A structurally-refused action (a write that escapes its lane, a call the kernel refuses) is stopped at the gate — Cursor receives `{"permission": "deny"}` with the reason on `agent_message`. |
| `afterFileEdit` | `dos hook posttool` | **Re-surface a stalled stream (advisory).** After an edit lands, the kernel can re-inject the fact that a stream is stalled — a fact to read, never a rewritten argument. |
| `stop` (the agent loop ends) | `dos hook stop` | **Refuse to stop on an unverified "done".** When the agent tries to end the turn claiming a feature shipped, `dos hook stop` refuses the stop until the claim is witnessed by git ancestry — the agent cannot self-certify "done". |

The deny is delivered as a **fact, not a rewritten argument**: DOS never emits
Cursor's `updated_input` key. A refused call carries its reason on
`agent_message` for the agent to read and correct — it never mints a corrective
argument and hands it back. (Cursor also honors `"failClosed": true` per-hook on
the PRE deny — add it if you want a DOS *crash* to block the call too; DOS itself
fails to PASS, so the fail-on-crash direction is your call.)

---

## 3. Tell the in-editor agent to verify before it claims "done"

The hooks ENFORCE at the lifecycle seams. To also STEER the agent — so it runs
`dos verify` itself before announcing a feature is finished — drop a Cursor
**Project Rule** into your repo. Cursor reads `.cursor/rules/*.mdc` files as
always-applied rules for the in-editor agent. This one is copy-pasteable:

```mdc
---
description: Verify with DOS before claiming a feature is done
alwaysApply: true
---

# Prove it shipped — don't just say it shipped

Before you tell the user a feature, phase, or fix is "done", run the DOS truth
syscall and show its result:

    dos verify --workspace . <PLAN> <PHASE>

- Exit `0` = the phase is attributed in git history → you may say it shipped.
- Exit `1` = NOT shipped yet → do NOT claim done; keep working or report the gap.

`dos verify` reads git ancestry, not your own narration — it is the witness the
`stop` hook also checks. If you have no plan/phase to verify, at minimum run
`dos commit-audit --workspace . HEAD` so your commit's subject is checked
against its own diff before you call the work complete.
```

Save it as `.cursor/rules/dos-verify-before-done.mdc`. Now the agent is told to
self-check with `dos verify` (exit `0` = shipped, `1` = not), and the `stop`
hook from step 1 backstops it — if the agent forgets the rule and tries to stop
on an unverified claim, the kernel refuses the stop anyway. Rule plus hook:
advice the agent *should* follow, and enforcement it *cannot* skip.

---

## 4. Confirm it took

```bash
dos doctor --workspace .        # shows the active lanes + that the cursor hooks are wired
```

That is the whole on-ramp: one `dos init --hooks cursor`, one Project Rule, and
the editor you already vibe-code in now refuses to lie to you about what it
built. For the "is my AI actually doing it?" framing and your first verdict, see
[`00b_did-my-ai-do-it.md`](00b_did-my-ai-do-it.md).
