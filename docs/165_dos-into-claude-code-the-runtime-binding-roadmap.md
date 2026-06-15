# docs/165 — DOS into Claude Code: the runtime-binding roadmap

> **The kernel is the part that doesn't believe the agents — so bind it to the
> moments the agent can't fake.** This plan maps every Claude Code (CC) extension
> seam to a DOS verdict, ranks the bindings, and names the one ~5-line fix that
> unblocks the whole Stop family.

*Status: design. Produced by a 33-agent fan-out (ground → design → adversarial
verify → synthesize) that VERIFIED CC's real hook contracts against
`code.claude.com/docs` + the schemastore settings schema, and reconciled DOS's
shipped surface against the source on disk. The headline finding — the keystone
no-op bug — was confirmed by an independent hand-read of `cli.py:2231-2235`.*

---

## 0. The thesis (what "DOS in Claude Code" actually is)

DOS-built-into-Claude-Code is **the referee installed at the agent runtime's own
non-forgeable moments**: a small set of pure kernel verdicts
(`verify`/`arbitrate`/`liveness`/the byte-clean detectors) bound to CC hook/SDK
seams so that **ground truth** (git ancestry, the lease WAL, env-authored tool
bytes) — not the agent's narration — gates what the agent may **claim, write, and
stop on**. It is a Policy Decision Point (DOS) wired into CC's Policy Enforcement
Points (the hooks).

And the whole product is **distribution**: a referee nobody installs catches no
lies, so the win is a one-move plugin that drops the un-believing verdicts in at
project scope.

The governing constraint, learned the expensive way (docs/143/144 live A/B:
**WARN +4.2pp, BLOCK +0.4pp** because BLOCK's disruption broke more than it
helped): **least-disruptive-that-informs.** Default every actuation to WARN
(`additionalContext`); reserve hard refusal for the rare request-absolute case
(`SELF_MODIFY`) and explicit opt-in.

---

## 1. The two ground truths

### 1a. Claude Code's real extension surface (verified, not recalled)

Sources: `code.claude.com/docs/en/{hooks,settings,sub-agents,mcp,plugins,agent-sdk/*}`
+ the authoritative `schemastore.org/claude-code-settings.json` (29 enumerated
hook-event keys). The installed npm package could NOT be located on this disk —
all findings are docs+schema, flagged in §6 unknowns.

The hook events that matter, with their **real block contracts**:

| Event | Payload (stdin JSON) | How it influences the action |
|---|---|---|
| **PreToolUse** | `tool_name`, `tool_input` (**absolute** `file_path`), `permission_mode`, `agent_id/type` | `{"hookSpecificOutput":{"permissionDecision":"allow\|deny\|ask\|defer","permissionDecisionReason":…,"updatedInput":{…},"additionalContext":…}}` — or exit 2 = block |
| **PostToolUse** | `tool_name`, `tool_input`, `tool_output` | `{"decision":"block"}` stops the loop before the next model call; `additionalContext` feeds context. Cannot un-run the tool. |
| **Stop** | `stop_reason`, `transcript_path` (**no plan/phase, no run-id, no start-SHA**) | `{"decision":"block","reason":…}` forces continuation; `{"hookSpecificOutput":{"additionalContext":…}}` = non-blocking feedback; `{"continue":false}` = hard stop |
| **SubagentStop** | `agent_type`, `agent_id` | same block/additionalContext contract as Stop |
| **UserPromptSubmit** | `prompt` | `{"decision":"block"}` erases the prompt; `additionalContext` annotates; exit-0 stdout becomes context |
| **SessionStart** | `source` (startup\|resume\|clear\|compact), `model` | no block; `additionalContext` injects context before the first prompt; `initialUserMessage`/`reloadSkills`/`watchPaths` |

Other confirmed surfaces: **MCP** (`.mcp.json` / inline `mcpServers`, tools named
`mcp__dos__verify`, resources `@`-mentionable, `alwaysLoad` escapes ToolSearch
silence); **subagents** (frontmatter, matcher-scoped, tool restrictions); **skills**
(slash-commands merged into skills; `!\`cmd\`` injection + `$1` args);
**plugins** (`plugin.json` bundles skills/commands/agents/**hooks**/mcp/bin +
`marketplace.json`); **Agent SDK** (`canUseTool` callback — the one *mandatory*
permission seam — + programmatic hooks + in-process MCP via `create_sdk_mcp_server`).

### 1b. DOS's shipped surface — already further along than the question implies

11 shipped integration points. The big ones:

- **`dos-mcp` server** — 7 tools (`dos_verify`, `dos_arbitrate`, `dos_refuse_reasons`,
  `dos_check_reason`, `dos_recall`, `dos_doctor`, `dos_status`), browsable
  resources (`dos://reasons`, `dos://lanes`), and host-slash-command prompts.
- **`dos guard`** — launch wrapper injecting `--mcp-config` + a `--settings` Stop
  hook + the claim-prompt (`guard.py`, pure `build_*` builders).
- **`dos hook stop`** — the verify-on-stop verb (`cli.py:2145`): reads the host
  event on stdin → `claim_extract` → `verify` each claim vs git.
- **`dos scope-gate`** — exit-code write-containment verdict (`cli.py:823`).
- The five-screenplay **SKP** skills shipped as package-data.

**10 gaps** where the kernel verdict exists but is *not wired to any CC seam*: live
tool-result stream (`tool_stream`/`dangling_intent`/`precursor_gate` run offline
only), `dos.sdk` (specced not shipped), `dos init --with-hooks` (specced),
`SKILL.md` frontmatter `dos.plan/phase` convention, runtime config reload, and the
arbitrate-as-pre-dispatch-gate.

---

## 2. THE KEYSTONE — fix the Stop contract first (it's a silent no-op today)

**`dos hook stop` emits `{"ok": false, "reason": …}` and exits 0** (`cli.py:2231-2235`).
**Claude Code honors none of that.** CC's Stop hook acts only on
`{"decision":"block"}`, `{"hookSpecificOutput":{"additionalContext":…}}`, exit 2,
or `{"continue":false}`. So the flagship runtime binding — the docs/134 "agent
can't stop on a lie" keystone — **is dead against real CC**: the agent stops
anyway, and the dogfood Stop hook in this repo never caught it because it runs
`async`/exit-0/observational.

This is itself a DOS-shaped lesson, found *inside DOS*: a green test
(`test_hook_stop`) asserted the emitted bytes matched the shape **DOS chose**,
never against a real CC instance honoring it — a **mirror-test** (the agent grading
its own JSON dialect). That mirror passed while the contract was a no-op.

**The fix (~5 lines, the highest-leverage change in the set):** change
`cmd_hook_stop` to CC's real Stop output —

```jsonc
// default: WARN (the live-A/B winner) — non-blocking, agent sees it and can act
{"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "<verdict>"}}
// --emit block (opt-in, high-stakes only): force continuation
{"decision": "block", "reason": "<verdict>"}
// --emit legacy: keep {"ok": false} for non-CC wrappers
```

Keep the fail-safe (no/error/true claim → silent exit 0). Add a **strike-cap** so
an opted-in block degrades to context after N identical `NOT_SHIPPED` re-prompts
(don't trap a confused agent in a loop). **Restrict any block to the
file-path/registry non-forgeable rung — never block on `grep-subject`** (the
agent authors its own commit subjects; it's forgeable, fired live on docs/120).

**Why this is the keystone by the non-forgeable-moment principle:** the Stop event
is the exact instant the agent converts "I'm done" into a terminal fact, and the
verdict that gates it (`oracle.is_shipped` vs git ancestry) is authored by **git —
a byte-author that is not the judged agent**. Every other Stop-family design
(SubagentStop fleet-gate, PreCompact ledger-pin, the plugin verifier,
`dos init --with-hooks`) inherits this same `{"ok":false}` no-op byte-for-byte and
is **DEAD until this lands**.

---

## 3. The ranked roadmap

Enforcement stance per item; default WARN/OBSERVE (the −9pp/+0.4pp lesson).

### Tier 0 — shipped-or-trivial (unblock the family + the one sound deny)

| Design | Surface | Stance | Effort |
|---|---|---|---|
| **Keystone Stop-contract fix** (§2) | Stop | WARN | S |
| **`dos hook pretool selfmodify`** — relativize CC's absolute `tool_input.file_path` against the resolved repo root (walk to `src/dos/`/`.git`, *not* cwd), POSIX-ify, run `self_modify._tree_touches_runtime`; emit `permissionDecision:"ask"` on a hit (maps the human `--force` override onto CC's user-escalation). Pair with a PreToolUse rule on `.claude/settings*.json` + a Bash-matcher scan for `sed -i`/`tee`/redirections at the runtime set. | PreToolUse | WARN/`ask` | S |

The selfmodify guard is **the one PreToolUse verdict genuinely sound to refuse**:
request-absolute (it ignores leases), byte-clean, ~0 false-positive against
ordinary work, `self_modify` reused unchanged. The killer gap is a coordinate
mismatch (CC sends **absolute** paths; `_tree.norm_tree_prefix` expects
repo-relative) — a boundary fix, not a kernel change.

### Tier 1 — near-term (distribution + WARN sensors + read-only surfaces)

| Design | Surface | Stance | Effort |
|---|---|---|---|
| **`dos-kernel` plugin bundle** — `.claude-plugin/plugin.json` (inline `mcpServers`) + `hooks/hooks.json` (corrected Stop + SubagentStop + SessionStart + PostTool sensor + selfmodify guard) + re-homed SKP + `/dos:*` commands + `bin/`. `marketplace.json` catalog. | plugin | WARN | M |
| **`dos init --with-hooks`** — scaffold `.claude/settings.json` Stop fragment + `.mcp.json` from `guard.build_settings`/`build_mcp_config` **verbatim** (parity-tested, merge-safe, idempotent, `--print`). Resolve hook workspace to `CLAUDE_PROJECT_DIR`, not `.`. | plugin/CLI | WARN | S |
| **PostTool `tool_stream` sensor** — boundary digest (sorted-key `tool_input`, hashed `tool_output` via the `trajectory.py` normalizer) → `session_id`-scoped `.dos/streams/<id>.jsonl` accumulator → `tool_stream.classify_stream` → re-surface REPEATING as `additionalContext`. The one lever that moves success **up** (re-surface the held value → the agent finishes). | PostToolUse | WARN | M |
| **SessionStart memory-recall + doctor injector** — fan `recall_one` over the project memory store; inject FRESH verbatim / STALE as a one-line hedge / UNVERIFIABLE by name; plus `dos doctor` facts. Aims the kernel inward at its own 52KB of frozen self-reports (live sweep: 8 stale / 39 fresh / 95 unverifiable). | SessionStart | WARN | M |
| **`/dos-status` (run_id-required), `/dos-trace`, `/dos-decisions`, `/dos-board`** — read-only PDP projections via `!\`cmd\`` injection. No lease, no block → OBSERVE by construction, dodges the −9pp trap. | skills | OBSERVE | S |
| **`dos-verify`/`dos-arbitrate` skills** — inline the **RUNG not the bare verdict** (render `grep-subject` as NOT-PROOF); pin a test that no bare SHIPPED is shown without its source. | skills | OBSERVE | S |

The plugin correctly routes Stop wiring to **plugin-root `hooks/hooks.json`** —
hooks in plugin *agent* frontmatter are ignored for security. Dependency arrow
stays one-way (plugin DATA references the `dos` CLI; nothing under `src/dos`
imports the plugin), the same litmus as the shipped SKP.

### Tier 2 — mid-term (the fleet/parent-child trust boundary + SDK referee)

| Design | Surface | Stance | Effort |
|---|---|---|---|
| **SubagentStop fleet-gate** — bind the corrected `dos hook stop` to SubagentStop (matcher = git-landing agent types only; read-only Explore/Plan abstain). Realizes agent-**to-substrate** adjudication at the parent/child boundary (the blackboard-disease cure, docs/116). | SubagentStop | WARN | S |
| **`dos hook pretool provenance`** — PostTool corpus accumulator → PreToolUse on mutating MCP tools runs `arg_provenance.classify_call` + `precursor_gate.classify_call`, **both arms WARN-only** (the corpus is structurally env-authored — no `AGENT_AUTHORED` member). | PreToolUse | WARN | M |
| **code-reviewer subagent via `dos.judges` (LlmJudge)** — new `dos judge` verb supplying an explicit `oracle_fn` (`oracle.is_shipped` over the reviewer's own DOS-CLAIM claims) composed via `judge_eval.compose_deterministic_first`. The literal "model verifying a model", made safe by the four shipped disciplines. | subagent | WARN | M |
| **`dos.sdk`** (docs/134 §5, `[sdk]` extra) — `build_can_use_tool` + `build_stop_hook`. Map OBSERVE/WARN → allow-with-`updatedInput`/annotate; reserve `canUseTool` **deny** SOLELY for SELF_MODIFY + a real lane double-book. `canUseTool` is the one *mandatory* (non-skippable) permission seam. | SDK | WARN/deny | L |
| **`dos_lease`/`dos_release` MCP tools** + a PreToolUse hook that stamps the lease, WARN-only — the only design that **prevents** rather than detects (the O_APPEND-under-mutex WAL write is the irreversible commit point). | MCP | WARN | M |
| **`alwaysLoad` + SessionStart-injected CALL PROTOCOL** — `alwaysLoad:true` on verify+lease+recall only; deliver the protocol via SessionStart `additionalContext` (the attested, version-safe channel — *not* an unverified `appendSystemPrompt` key). The honest answer to "MCP is weaker than a hook because the agent can decline to call." | MCP | OBSERVE | S |

### Tier 3 — long-horizon (needs a real `session_id → run_id` identity seam first)

Three designs all pivot on a mapping that **does not exist today**: CC's
`session_id` ↔ a DOS `run_id`/start-SHA/lease. (`run_id` is minted from
epoch/pid/monotonic — it has no `session_id` link.) Build the **SessionStart
run-identity seam** (a sidecar in `CLAUDE_PROJECT_DIR` keyed by `session_id`) as
the prerequisite, then:

- **Stop-vs-stall liveness arbiter** — consult `liveness.classify` ONLY when real
  identity is present (else SKIP); STALLED → `additionalContext`, `continue:false`
  behind opt-in. *(Marked BROKEN as originally drawn: the Stop payload has no
  start-SHA/(loop_ts,lane), so `classify` degrades to a CONSTANT STALLED — hence
  the identity-seam gate.)*
- **PreCompact ledger-pin** — treat compaction as a voluntary partial-crash: append
  an INTENT entry, `verify_step` each claimed SHA into `STEP_VERIFIED` (non-forgeable
  file-path rung) BEFORE the summary (a generation-#2 self-report) becomes the
  record-of-progress.
- **SessionEnd post-mortem** — `dangling_intent.classify_stop` (the one self-report
  DOS believes: against-interest "I still need to X", corroborated by env-authored
  result absence) → route a fire to the persistent decisions surface. WARN-only.
- **`dos_trace` MCP tool** + **`dos-dispatch` SKP fan-out** (invert enforcement to a
  per-Edit/Write PreToolUse hook mapping the *real* `file_path` against sibling
  leases).

---

## 3.5. SHIPPED addendum — the keep-alive wait-marker budget (Stop family, native)

*Added 2026-06-09. The ranked roadmap above (§3) mapped the Stop family around the
verify-on-stop keystone but did NOT anticipate a SECOND Stop-family binding: the
loop-cost lever. It is now shipped — native Go + Python + parity-tested — so it is
recorded here rather than as a future tier.*

**The binding:** `dos hook marker` — a CC **Stop** hook that bounds keep-alive
polling. A `/loop`-style dispatch loop holds its turn open by emitting `claude -p`
keep-alive markers, and each marker is a FULL assistant turn that replays the whole
context out of prompt cache for ZERO forward work (session 4b4ff97c burned 252
markers / ~$7.80, 91% of that run's cache-read). The hook reads the session's durable
marker tally, asks the pure `loop_decide.wait_marker_budget`, and either holds the
turn open one more marker (`{"decision":"block"}`, budget unspent) or emits nothing
(allow stop, budget spent → the loop waits on the real Bash `<task-notification>`).
It is the PRE-HOC runtime sibling of the POST-HOC `keepalive_poll` telemetry flag
(`trajectory_audit.py`, fires at ≥5 markers); the default cap (4) refuses one marker
BEFORE that flag would fire.

**Why it is in the Stop family but is the INVERSE of the keystone:** both bind to
the Stop event, but on opposite triggers. `dos hook stop` blocks a *false done*
(claimed-ship vs git ancestry — the keystone). `dos hook marker` blocks a *premature
stop ONLY while the marker budget is unspent*, then gets out of the way. The two
COMPOSE — the plugin wires both in the same `Stop` array; a `{"decision":"block"}`
from either means "keep working," and the turn ends only when NEITHER blocks (the
wait budget is spent AND no false-done is detected). This is the one Stop-family
binding that moves loop COST down rather than gating a claim, and it is WARN-by-
construction: it never blocks *work*, only idle *polling*, so it dodges the
−9pp/+0.4pp BLOCK-disruption trap entirely (the §0 governing constraint).

**State (all on disk, all green):**

| Piece | Module | Status |
|---|---|---|
| Pure verdict | `loop_decide.wait_marker_budget` → `WaitMarkerDecision` | shipped + tested |
| Durable tally | `marker_sensor` (`.dos/markers/<sid>.jsonl`, fsync'd, schema-gated, torn-tail-tolerant) | shipped |
| Python runtime verb | `cli.cmd_hook_marker` (the two-polarity Stop dialect) | shipped |
| **Native Go fast-path** | `dos-hook marker` (`go/internal/hook/marker.go` + `main.go` dispatch) | **shipped (this addendum)** |
| Go↔Python parity | `parity_marker_test.go` + `parity/corpus_marker.jsonl` (byte-exact verdict + dialect) | shipped |
| **Plugin wiring** | second `Stop` entry in `claude-plugin/hooks/hooks.json` | **shipped (this addendum)** |

**The native-path discipline (why `marker` OWNS its outcome, unlike `stop`'s earlier
delegate phase):** the Go `marker` decider reads the session's durable tally to
decide, so it must NEVER delegate to the Python `||` fallback after that read —
delegating would let Python ALSO run and append a SECOND marker record, double-
counting the budget. So like `pretool`/`posttool`, the native `marker` path reads
stdin, owns the outcome (emit the block dialect or nothing), writes the marker record
itself (the boundary I/O lives inside `DecideMarker`), and always exits 0. A machine
with no prebuilt binary falls through the `|| python -m dos.cli hook marker` to the
Python verb (the docs/100 fallback) — byte-identical decision, just slower; and since
`marker` fires once per keep-alive wait (seconds-to-minutes apart), not per tool call,
the Python-speed fallback is entirely acceptable (the latency argument that forced
`pretool`/`posttool` native does not bind here — this went native for parity and
double-count-safety, not speed).

## 3.6. The re-injection hook — a UserPromptSubmit primitive against salience decay

*Added 2026-06-14. §3 mapped every Stop-family seam but left **UserPromptSubmit**
unused for anything but the rejected self-cert trap (§5.1). This records the one
sound, general use of that seam: re-printing a standing directive every turn.
Source pattern: docs/339 §5.1 — the `opus-fable-mode` re-injection hook
(`reinject.sh`), an outsider's independently-built fix for the same problem DOS hits
in its own CLAUDE.md.*

**The problem it fixes.** A standing directive — a line in `CLAUDE.md`, a governor
rule, a working-discipline note — is read once at session start, then **fades in
salience as the session grows**. The system prompt is fixed at the top, but the
agent's attention sits at the live end of a long, growing transcript; by turn 40 a
turn-0 directive is far away and weakly weighted. The agent drifts back to its
default texture (for Opus: padding medium-length prose, narrating itself, hedging) —
not because it was told not to, but because the telling decayed. This is the
salience-decay problem docs/336 names from the legibility side and docs/339 §5.1
measures from the behavioral side.

**The mechanism.** A **UserPromptSubmit** hook re-prints the standing directives on
**every** turn. Its exit-0 stdout becomes context the model sees right before it
acts (§1a: "exit-0 stdout becomes context"), so the directive lands at the *live end*
of the transcript every time — fresh, not faded. It is a **thermostat**: the
directive is the setpoint, and the hook re-asserts it each turn so drift never
accumulates. It blocks nothing (`{"decision":"block"}` would erase the prompt — never
use it here); it only injects. So it is OBSERVE/WARN by construction and dodges the
−9pp BLOCK-disruption trap (§0).

**The worked snippet.** Same shell-command shape as the bundled hooks
(`claude-plugin/hooks/hooks.json`): a `UserPromptSubmit` entry whose command prints
the directive block to stdout and exits 0. The directives live in one file the hook
`cat`s, so editing the setpoint is a one-file edit, not a hook edit:

```jsonc
// claude-plugin/hooks/hooks.json — a UserPromptSubmit entry
"UserPromptSubmit": [
  {
    "hooks": [
      {
        "type": "command",
        "shell": "bash",
        // print the standing directives, then exit 0 so stdout becomes context.
        // Fail-safe: a missing file prints nothing and still exits 0 (|| true) —
        // the hook never breaks a turn, the same discipline as the other DOS hooks.
        "command": "cat \"${CLAUDE_PROJECT_DIR}/.dos/governor.md\" 2>/dev/null || true"
      }
    ]
  }
]
```

`.dos/governor.md` holds the few lines worth re-asserting — short, because every
turn pays their token cost. A minimal example:

```text
STANDING DIRECTIVES (re-asserted every turn — these decay otherwise):
- Prefer tool calls over prose: when a call settles the question, make it — don't
  narrate what you're about to do. Act, then report the result.
- Write plainly: short common words, short sentences, one idea at a time.
- In tool work, lead with the result; act, don't narrate.
```

For a non-plugin host the identical entry goes in `.claude/settings.json` under
`hooks.UserPromptSubmit` — `dos init --with-hooks` (§3 Tier 1) is the natural place
to scaffold it, byte-identical to the plugin form.

**Where DOS's OWN directives need this.** DOS already carries standing governor
directives that are exactly the decay-prone kind — they are setpoints with no
thermostat today:

| Standing directive | Where it lives | Why it decays |
|---|---|---|
| "Prefer tool calls over prose" | `CLAUDE.md` (HIGH-PRIORITY block) | added to fight Opus's narrate-don't-act texture; the longer the session, the more the agent reverts to prose |
| "Write plainly — short words, short sentences" | `CLAUDE.md` + `AGENTS.md` (operator directive) | a style setpoint; drift back to dense prose is gradual and unflagged |
| "Commit only the lane you worked / never `git add -A`" | `CLAUDE.md` working discipline | a safety rule the agent must re-apply at *every* commit, late in a session when the rule is most distant |

Each is a line read once and then left to fade. The re-injection hook is the cheap,
general fix: put the two or three load-bearing directives in `.dos/governor.md`,
wire the `UserPromptSubmit` entry above, and they arrive fresh on every turn instead
of decaying to a turn-0 memory.

**The honest caveat — this steers the *narration*, not the disposition.** Re-asserting
"prefer tool calls over prose" makes the directive present; it does not make the
agent's underlying behavior better, only more-likely-aligned-to-the-text. That is the
docs/339 §3 forgeable-sensor lesson applied to the *actuator*: the re-injection hook
is a stronger setpoint, but the loop is only trustworthy if its **sensor** reads a
non-forgeable effect (a landed commit, a verified claim — the §3 PostTool / Stop
verdicts), not the agent's own re-aligned words. Use this primitive to keep the
setpoint fresh; close the loop with an author-disjoint sensor, not by trusting that
the freshly-injected directive was obeyed.

---

## 4. The one-install story

```
claude plugin install dos@dos-marketplace
```

drops the whole referee in one move: `plugin.json` declares `dos-mcp` inline
(`mcp__dos__*`, `alwaysLoad:true`); `hooks/hooks.json` wires the **corrected** Stop
+ SubagentStop verifier (WARN), a SessionStart recall+doctor injector, a PostTool
`tool_stream` sensor, and the SELF_MODIFY guard (`ask`); `skills/` re-homes the SKP
+ `/dos:*` commands; `bin/` puts `dos`/`dos-mcp` on PATH while enabled.

For non-plugin hosts, **`dos init --with-hooks`** writes the *byte-identical*
`.claude/settings.json` fragment + `.mcp.json` from the **same pure builders**
(parity-tested, merge-safe, idempotent). **The default install is all-WARN/OBSERVE;
BLOCK is never wired by default.**

---

## 5. Self-certification traps explicitly REJECTED

These are the designs the synthesis threw out because they let the agent grade
itself (the DOS cardinal sin, inward):

1. **"Agent grades its own task done" satisfaction predicates** (UserPromptSubmit/
   SessionStart) — replaced by `verify` vs git ancestry. The kernel never asks "is
   the agent succeeding?", only "did these bytes appear in a byte-author that is not
   the agent?"
2. **`tool_stream` as a "progress/succeeding" predicate** — it keys ONLY on
   env-authored `result_digest` identity (provenance-of-repeated-output, §5a), never
   a satisfaction verdict.
3. **The LlmJudge ruling on its OWN findings text / its own `dos verify` summary** —
   a mirror-verifier (AGENT_AUTHORED below the floor). The `judges.Claim.evidence`
   must be THIRD-PARTY bytes; an AGREE can never clear, only DISAGREE→WARN or ABSTAIN.
4. **Trusting the CC compaction SUMMARY as progress-of-record** — PreCompact mints
   `STEP_VERIFIED` only on the non-forgeable file-path rung; every un-verified claimed
   step is fail-closed not-done.
5. **DOS grading its own Stop JSON shape in `test_hook_stop`** — the subtlest
   self-cert, and the reason the keystone was a no-op: a green test that emitted bytes
   matched the shape DOS chose, never run against a real CC honoring it.
6. **Letting the agent disable the referee by editing `.claude/settings.json`** — the
   control-plane self-defeat; pair any write-guard with a PreToolUse rule on
   `.claude/settings*.json` and document managed `strictPluginOnlyCustomization`.
7. **The verify-skill inlining the BARE verdict** — `grep-subject` is agent-authored;
   the skill must inline the RUNG and demote `grep-subject` to NOT-PROOF.

---

## 6. Open questions / unknowns (do not build on these unverified)

- The installed CC npm package could not be inspected on this disk — all contracts
  are from docs + the schemastore schema (the schema enumerates **29** hook keys; the
  hooks doc prose showed **32** incl. a doc-only `MessageDisplay`). The exact set is
  version-dependent.
- Stdin payload field names (`effort.level` shape, `stop_reason` enum, `agent_goal`)
  are from doc prose, not a published stdin JSON schema — fields may differ by version.
- Whether `{"continue":false,"stopReason"}` is honored uniformly across **all**
  blocking events (vs only Stop/SubagentStop/UserPromptSubmit) is not fully
  enumerated.
- The SDK's complete set of programmatically-registerable hook events, the
  `marketplace.json` field schema, and the `settingSources` enum were referenced but
  not exhaustively fetched.

**Action implied:** before shipping any *blocking* binding, probe the contract
against a live CC instance (the §2 mirror-test lesson). WARN/`additionalContext`
bindings are safe to ship on the doc contract; BLOCK bindings are not.
