# 268 — Gemini integration: the live proof, and the Go fast-path's dialect gap

> **Status:** proof done (2026-06-09); the Go-dialect gap is now CLOSED (2026-06-09).
> The Go transcode seam (`go/internal/hook/dialect_transcode.go`) landed in `af1ba79`
> (alongside docs/269), giving `dos-hook` a `--dialect` renderer for every host. A
> follow-up audit caught that its `renderGemini` still emitted the *superseded*
> `{"decision":"deny"}` PRE shape — the very fail-open this doc identified — so the Go
> bytes had drifted from the corrected Python `GeminiDialect` (which emits
> `{"continue": false, "stopReason": …}`). That drift is now fixed and the
> per-dialect parity test (`parity_dialect_test.go`) re-pins Go == Python for every
> (verdict, dialect) pair. **§4 below records the original gap; the fix landed as
> designed in §"The fix".**
>
> **Phase stamp.** Phase `P1` (the Go fast-path dialect renderer + the Go==Python
> per-dialect parity that closes the `renderGemini` fail-open) shipped as
> `61bd487`; `dos verify --workspace . docs/268 P1` resolves it via this commit's
> `(docs/268 P1)` trailer — the oracle does not read the `Status:` sentence above.

## What was asked

"Prove out integration with Gemini (now logged in)" — and, mid-audit, the sharper
constraint: **the integration should exercise the native Go `dos-hook`, not the
Python fallback.** That second instruction is the load-bearing one: it turns a
green-path demo into an audit, because the moment you point Gemini at the Go binary
the integration *silently stops blocking*. This doc records why.

## The three surfaces (they are not the same integration)

DOS meets a host runtime through three distinct seams, and "Gemini integration"
means something different at each:

| Surface | Runtime | Gemini-aware? | State on this machine |
|---|---|---|---|
| **MCP server** (`dos` tools) | **Python** (`dos_mcp.server`) | yes (JSON/stdio, vendor-neutral) | Connects, but **CallTool STALLS** (Win + py3.13 + mcp 1.27) |
| **Hooks** wired by `dos init --hooks gemini` | **Python** (`dos hook … --dialect gemini`) | yes (`GeminiDialect` renders `{"decision":"deny"}`) | Correct bytes; this is the working path |
| **Go fast-path** (`dos-hook`) | **Go** binary | **NO** — emits Claude-Code dialect only | SELF_MODIFY *detection* works; **dialect is wrong for Gemini** |

The instinct "use the Go thing" is right for *latency* (the Go binary serves a hook
decision in ~10 ms vs ~0.3–0.8 s for a Python spawn — the docs/124/125 parity
contract). But it is wrong for *Gemini correctness today*, and that is the finding.

## What was proven (grounded, not asserted)

### 1. The live Gemini model call works — the prior blocker is cleared

`gemini -p "…"` returns a real model completion through the logged-in
`oauth-personal` account (a personal gmail). The previous
"Dasher-ineligible" block (a Google-Workspace-account eligibility limit) is gone:
a **personal gmail** OAuth login is eligible. This is the unblock the task was
predicated on.

### 2. The MCP server connects but its tool calls stall

`gemini mcp add dos python -m dos_mcp.server` →
`✓ dos: python -m dos_mcp.server (stdio) - Connected`. The handshake (initialize +
tool list) succeeds. But driving the **live** model to actually *call* a `dos`
tool produced **zero output after 90 s** — the documented
`CallTool stdio STALL on Win + py3.13 + mcp 1.27`. So the MCP surface is
**handshake-live, call-dead** on this platform. (In-process / in-memory transport
is unaffected — this is a stdio-pipe interaction bug, not a kernel bug.) There is
**no Go MCP server** — MCP is Python-only by construction (folding a server
framework into the near-stdlib kernel is explicitly rejected, CLAUDE.md).

### 3. The hook path is Python-by-wiring — and that is currently *correct*

`dos init --hooks gemini .` writes `.gemini/settings.json`:

```json
"BeforeTool": [{"type":"command","command":"dos hook pretool --workspace . --dialect gemini"}],
"AfterTool":  [{"type":"command","command":"dos hook posttool --workspace . --dialect gemini"}],
"AfterAgent": [{"type":"command","command":"dos hook stop --workspace . --dialect gemini"}]
```

`dos hook … --dialect gemini` is the **Python** console-script. It renders the
Gemini envelope correctly. Proof — one DENY verdict through every dialect:

```
claude-code  -> {"hookSpecificOutput": {"permissionDecision": "deny", …}}
gemini       -> {"decision": "deny", "reason": …, "hookSpecificOutput": {"additionalContext": …}}
cursor       -> {"permission": "deny", "agent_message": …}
codex        -> {"hookSpecificOutput": {"permissionDecision": "deny", …}}
antigravity  -> {"decision": "deny", "reason": …}
```

Gemini honors a **top-level `decision` key**. Claude Code honors a **nested
`hookSpecificOutput.permissionDecision`**. These are not interchangeable.

### 4. THE GAP — the Go binary detects the refusal but emits the wrong dialect

Feeding a SELF_MODIFY event to the **Go binary** with `--dialect gemini`:

```
$ echo '{"tool_name":"Write","tool_input":{"file_path":"src/dos/arbiter.py","content":"x"}}' \
    | dos-hook-windows-amd64.exe pretool --workspace . --dialect gemini --debug

[dos-hook pretool] rung=admission decision=deny reason_class=SELF_MODIFY      ← detection WORKS
{"hookSpecificOutput": {"hookEventName": "PreToolUse",
                        "permissionDecision": "deny", …}}                      ← but CC bytes
```

Two facts, both load-bearing:

- The Go SELF_MODIFY rung **fires correctly** — the kernel logic is ported and right.
- The Go binary **silently ignores `--dialect gemini`** (`parseFlags` drops unknown
  flags, by design — "a hook must not die on an unexpected argument") and emits the
  **Claude-Code** envelope unconditionally. `go/internal/hook/dialect.go` hardcodes
  `hookSpecificOutput` / `permissionDecision`; there is no other renderer in the Go
  tree, and `main.go` has no `--dialect` flag at all.

**Consequence:** if an operator wired Gemini to the fast Go binary (the natural
"use Go for speed" move), Gemini would receive `hookSpecificOutput` bytes, find no
top-level `decision` key, and **proceed with the call**. The SELF_MODIFY deny — a
correctly-computed refusal — would be **silently dropped**. The agent would rewrite
the kernel that is adjudicating it. This is a fail-*open* on a host the binary was
never taught to speak to.

This is safe *today* only because nothing wires Gemini→Go: the Claude-Code plugin's
`hooks.json` is the **only** caller of `dos-hook`, and it wants CC bytes. The gap is
a latent hazard waiting on the first person who follows the "use Go" instinct for a
non-CC host.

## Why this matches the kernel's own laws

The vendor-agnostic-kernel litmus says *a dialect is OUTPUT chosen by `--dialect`,
strictly downstream of an already-decided verdict* — and the Python side honors it
(the verdict is computed vendor-blind; only the final render branches on the
by-name dialect). The **Go binary violates the spirit** of that contract: it
computes the verdict vendor-blind (good) but then hardcodes the *one* vendor's
output instead of carrying the `--dialect` data through to a renderer. The Go
fast-path is, in effect, a **Claude-Code-only** accelerator that has been handed a
flag it pretends to accept.

## The fix (LANDED — `af1ba79` + the 2026-06-09 parity correction)

Port the dialect seam into Go, mirroring the Python kernel/driver split. All three
steps below shipped; step 2's Gemini transform initially regressed to the old
fail-open shape and was corrected so the parity test (step 3) is honestly green:

1. **`--dialect` flag in `main.go`** — scan it in `parseFlags` (default `claude-code`).
2. **A `renderVerdict(action, reason, context, dialect)` in Go** that switches on the
   dialect string and emits the matching envelope. The built-in set is the
   *unshadowable* `claude-code` (today's `denyPayload`/`warnPayload`, byte-for-byte)
   plus the other four shapes (`gemini`/`cursor`/`codex`/`antigravity`) as pure data
   transforms — they are tiny (`{"decision":"deny","reason":…}` etc.), so this is
   ~40 lines, not a framework.
3. **A parity test** — for every (verdict, dialect) pair, assert the Go bytes ==
   the Python `resolve_dialect(name).render(v)` bytes (the docs/124 parity contract,
   extended from "the CC projection" to "every dialect projection"). This is the
   guardrail that keeps the two implementations from drifting.

Note the Go side does **not** need the entry-point plugin machinery the Python side
has — the four extra dialects are kernel-known closed data (they ship in the binary
anyway), and Go has no third-party driver-registration story here. The split that
matters (verdict computed blind; output chosen by data) is preservable with a plain
`switch`. The "no vendor as a *branch in adjudication*" rule is still honored: the
switch is in the **render** step, downstream of the decided verdict, exactly where
`--dialect` belongs.

Now that it has landed, the operator guidance is simpler: **the Go `dos-hook` speaks
every host's dialect**, so a non-Claude-Code host may be wired to the fast Go binary
*or* the Python `dos hook … --dialect <host>` path and get the same (now correct)
bytes — `--dialect gemini` on the Go binary emits `{"continue": false, …}`, which
Gemini's `shouldStopExecution()` honors. The speed/correctness trade this section
once described is gone; the parity test is the standing guard that keeps it gone.

## Prevention — the standing guards that keep this class from returning

This fail-open shipped *twice* (the BeforeTool PRE deny, then the stop verb at the
STOP moment) because each (dialect, moment) was a hand-written single-case test that
could regress independently, and because the live integration probe wrote to the real
kernel. Three guards now make both halves un-mergeable / harmless:

1. **The fail-open FLOOR** (`tests/test_hook_dialect.py`) — an exhaustive
   5-dialect × 3-moment matrix asserts every DENY renders a signal the host's gate
   *actually honors at that moment*, plus a moment-split pin for Gemini (PRE must be
   `continue:false`, POST/STOP must be `decision:block`). A deny that renders to a
   host-ignored shape, or the wrong shape for the moment, fails LOUD. You cannot merge
   a fail-open.
2. **The Go↔Python parity test** (`parity_dialect_test.go`) — every (verdict, dialect)
   pair, the Go bytes must equal Python's; the two implementations can't drift back
   apart (this is how the Go side regressed to the old shape unnoticed).
3. **The SAFE probe** (`scripts/selfmodify_hook_probe.py`, pinned by
   `tests/test_selfmodify_hook_probe.py`) — proves the SELF_MODIFY deny fires against a
   *sacrificial* throwaway workspace, so a fail-open clobbers a dummy, never the live
   `arbiter.py`. The dangerous live probes (`_gemini_hook*`, `_prove_gemini*`) are
   gitignored so they can't enter history. **Never wire a live agent's
   self-modifying-write probe to this repo's own tree** — that is what truncated
   `arbiter.py` to `x` and broke every concurrent session.

The general law (the fail-open shape + the sacrificial-target rule) is captured for
reuse beyond hooks; see the memory `feedback-fail-open-shape-and-sacrificial-probe-law`.

## Artifacts / repro

- Live model: `gemini -p "…"` → real completion (oauth-personal).
- MCP: `gemini mcp add dos python -m dos_mcp.server`; `gemini mcp list` → Connected;
  a live tool-call drive stalls (no output, killed at 90 s).
- Dialect contrast: the 5-line render table above
  (`dos.hook_dialect.resolve_dialect` + `dos.drivers.hook_dialects`).
- The Go gap: `dos-hook-windows-amd64.exe pretool --dialect gemini --debug` on a
  SELF_MODIFY event → `reason_class=SELF_MODIFY` (right) + `hookSpecificOutput` (wrong
  for Gemini).
