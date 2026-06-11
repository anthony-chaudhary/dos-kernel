"""The per-vendor hook-dialect renderers — a DRIVER (docs/217, the kernel/driver split).

> **The verdict is the kernel; the envelope is a driver.**

`hook_dialect.py` (the kernel seam) holds the dialect-neutral `HookVerdict`, the
`HookDialect` Protocol, the by-name `resolve_dialect`, and the ONE unshadowable
built-in: `ClaudeCodeDialect` (the default — byte-for-byte what `decide()` already
emits). Every OTHER host renderer — the ones that must name a specific vendor as
code (`CodexDialect`, `GeminiDialect`, `CursorDialect`) — lives HERE, in a driver,
discovered by name through the `dos.hook_dialects` entry-point group.

This is the exact same kernel/driver split as `judges` (the pure `Judge` protocol +
`AbstainJudge` baseline in the kernel; every *ruling* judge in `drivers/llm_judge`)
and `overlap_policy` (the pure scorer seam in the kernel; a model-backed scorer in a
driver). The litmus it satisfies (`tests/test_vendor_agnostic_kernel.py`): **no
non-driver kernel module names a vendor as a code identifier**, so no kernel
*adjudication* can branch on which vendor is acting. A dialect renderer legitimately
names its vendor — but it is OUTPUT formatting chosen explicitly by the operator
(`--dialect codex`), strictly downstream of an already-decided verdict, never a
decision. That is precisely why it belongs on the driver side of the line.

PURE: verdict in, host dict (or None for PASS) out. NO I/O, NO tool-input rewrite
key (the docs/191 §4 byte-author floor — a corrective rides a context/message field
as a fact to read, never a rewritten argument to use).
"""

from __future__ import annotations

from typing import Optional

from dos.hook_dialect import ClaudeCodeDialect, HookAction, HookMoment, HookVerdict

# The default renderer, reused by the CC-identical Codex dialect. Importing the
# kernel from a driver is the allowed direction (layer 4 → layers 1–2).
_CLAUDE_CODE = ClaudeCodeDialect()


class CodexDialect:
    """OpenAI Codex CLI — the cheapest dialect: the envelope is CC-identical.

    Codex's `PreToolUse`/`PostToolUse` hooks honor the same `hookSpecificOutput`
    shape (its field names were copied from CC almost verbatim). The one real
    divergence is host COVERAGE — Codex only fires `PreToolUse` on its
    Bash/apply_patch/unified_exec/mcp handlers — which is a host limit, not a render
    difference: DOS emits the right bytes; Codex simply won't call the hook on every
    tool. So this dialect delegates to the CC renderer (kept as its own class for an
    explicit by-name entry + so a future Codex-specific divergence has a home).
    """

    name = "codex"

    def render(self, verdict: HookVerdict) -> Optional[dict]:
        return _CLAUDE_CODE.render(verdict)


class ClaudeCoworkDialect:
    """Anthropic's **Claude Cowork** — the Claude Code agent harness in a desktop VM.

    Cowork is the agentic desktop app for general knowledge work (docs/298). Under
    the UI it runs the SAME agent harness as Claude Code, inside a Linux VM — so its
    hook output grammar is not "like" CC's, it IS CC's (the nested
    `hookSpecificOutput` envelope, parsed by the same code). This dialect therefore
    delegates to the CC renderer, exactly the Codex precedent: kept as its own class
    for an explicit by-name entry (`--dialect claude-cowork` resolves for an
    Agent-SDK consumer driving the verbs directly) and so a future Cowork-specific
    divergence has a home.

    The host fact that is Cowork's OWN — the product does not FIRE hooks yet
    (anthropics/claude-code#63360, verified 2026-06-10) — is an install-time
    coverage note, not a render difference: DOS emits the right bytes; Cowork's
    harness defines them; the product will start firing them upstream. See
    `claude_cowork_install_spec` below.
    """

    name = "claude-cowork"

    def render(self, verdict: HookVerdict) -> Optional[dict]:
        return _CLAUDE_CODE.render(verdict)


class GeminiDialect:
    """Google Gemini CLI — `BeforeTool` / `AfterTool` / `AfterAgent` hooks.

    The DENY envelope is MOMENT-DEPENDENT, because Gemini 0.45.x gates the two
    moments on DIFFERENT fields (verified against the CLI 0.45.2 bundle, 2026-06-09):

      * A `BeforeTool` deny — STOP THE TOOL BEFORE IT RUNS — is enforced by
        `shouldStopExecution()`, whose body is literally `return this.continue ===
        false`. So a PRE deny must emit `{"continue": false, "stopReason": …}`. A
        `{"decision": "deny"}` here is IGNORED on the tool-execution path (it only
        feeds `isBlockingDecision()`, which the BeforeTool gate does NOT consult) —
        the tool runs anyway. This was the silent fail-open: DOS emitted
        `{"decision":"deny"}` and a live Gemini wrote the file regardless (docs/268).

      * An `AfterAgent` deny — REFUSE TO STOP — is enforced by `isBlockingDecision()`
        (`decision === "block" || decision === "deny"`). So the STOP moment renders
        `{"decision": "block", "reason": …}` (block, the documented stop refusal).

    A WARN (turn-preserving) injects context via `hookSpecificOutput.additionalContext`
    — Gemini reads it into the model's context for self-correction without blocking.

    `getEffectiveReason()` prefers `stopReason` then `reason`, so the PRE deny carries
    its why on `stopReason` and the corrective fact (if any) on additionalContext.
    """

    name = "gemini"

    def render(self, verdict: HookVerdict) -> Optional[dict]:
        if verdict.action is HookAction.PASS:
            return None
        if verdict.action is HookAction.DENY:
            if verdict.moment is HookMoment.PRE:
                # BeforeTool: stop the tool via `continue: false` (the field
                # `shouldStopExecution()` actually checks). `stopReason` is the why
                # `getEffectiveReason()` surfaces.
                out: dict = {"continue": False, "stopReason": verdict.reason}
                if verdict.context:
                    out["hookSpecificOutput"] = {"additionalContext": verdict.context}
                return out
            # AfterAgent (or any non-PRE) refusal: block the stop via the
            # decision field `isBlockingDecision()` consults.
            out = {"decision": "block", "reason": verdict.reason}
            if verdict.context:
                out["hookSpecificOutput"] = {"additionalContext": verdict.context}
            return out
        return {"hookSpecificOutput": {"additionalContext": verdict.context}}


class AntigravityDialect:
    """Google Antigravity (IDE + CLI) — `PreToolUse`/`PostToolUse`/`Stop` hooks.

    Antigravity is a HYBRID of the two grammars DOS already speaks, which is exactly
    why it earns its own renderer rather than aliasing an existing one:

      * its hook CONFIG file is Claude-Code-SHAPED (group-wrapped `matcher`+`hooks`
        entries under `PreToolUse`/`PostToolUse`/`Stop` — see `antigravity_install_spec`),
        BUT
      * its hook OUTPUT grammar is Gemini-SHAPED: a script writes a JSON object on
        stdout carrying a top-level `decision` key set to `"deny"` or `"allow"`, with
        an optional `reason` (NOT Claude-Code's nested `permissionDecision`).

    So the install spec is group-wrapped like CC, but the bytes a verdict RENDERS to
    are `{"decision": "deny", "reason": …}` like Gemini. Web-grounded 2026-06-09
    (Antigravity hooks docs + the CLI migration guide — "Antigravity hooks receive
    JSON on standard input and read a JSON object on standard output containing a
    decision key set to `allow` or `deny`").

    The corrective FACT (a provenance DENY's `context`) is appended to the operator-
    facing `reason` (Antigravity's documented output vocabulary is `decision`/`reason`;
    it does not document a separate context channel, so re-surfacing the fact through
    `reason` is the lossless, no-extra-key move — the docs/191 §4 byte-author floor:
    a fact to read, never a rewritten argument). A WARN (turn-preserving, do NOT
    block) emits a bare `{"reason": …}` with NO `decision` key — inert to the
    allow/deny gate, so it adds context without withholding the call.
    """

    name = "antigravity"

    def render(self, verdict: HookVerdict) -> Optional[dict]:
        if verdict.action is HookAction.PASS:
            return None
        if verdict.action is HookAction.DENY:
            out = {"decision": "deny"}
            # Join reason + the corrective fact into the one operator-facing field
            # Antigravity reads (it has no separate additionalContext channel). Keep
            # them distinct, space-joined, with neither half left dangling.
            reason = " ".join(p for p in (verdict.reason, verdict.context) if p).strip()
            if reason:
                out["reason"] = reason
            return out
        # WARN → a bare reason with no decision (inert to the allow/deny gate, so it
        # re-surfaces context without blocking — Antigravity's only turn-preserving path).
        return {"reason": verdict.context}


class HermesDialect:
    """Nous Research's **Hermes Agent** — the `pre_tool_call` / `post_tool_call`
    SHELL hook (docs/278).

    Hermes is a Python autonomous-agent framework whose hook system fires a
    user-configured shell command *before* a tool runs (inside
    `handle_function_call()`), and the FIRST matching "block" directive short-circuits
    the tool, returning the message to the model as that tool's error. Unlike OpenClaw
    (whose real `before_tool_call` hook is an in-process TypeScript return value, NOT
    stdout bytes — so it has no stdout-renderer consumer and is deliberately NOT given
    a dialect here) and SwarmClaw (no documented pre-tool interception hook at all),
    Hermes' shell hook is a genuine "emit-JSON-on-stdout" surface — exactly the shape
    `dos hook pretool --dialect hermes` produces.

    DENY shape (verified against the Hermes hooks doc, 2026-06-09 —
    `hermes-agent.nousresearch.com/docs/user-guide/features/hooks`): a hook BLOCKS by
    printing `{"decision": "block", "reason": "…"}` on stdout. Hermes ALSO accepts the
    equivalent `{"action": "block", "message": "…"}` and "normalises internally", but
    DOS emits the canonical `decision`/`reason` form (the same field NAMES Gemini's
    AfterAgent and Claude-Code's stop refusal use — one fewer shape for an operator to
    learn). ALLOW is an empty object `{}` (or any non-matching output).

    WARN is the one lossy moment: the Hermes shell-hook grammar documents only
    block-vs-allow — there is NO turn-preserving "add context without blocking"
    channel the way Cursor (`agent_message`), Gemini/CC (`additionalContext`), or
    Antigravity (a bare `reason`) expose. So a DOS WARN renders to the ALLOW object
    `{}` (it MUST NOT block — a WARN is turn-preserving), and the corrective `context`
    is necessarily dropped on this host. That is a Hermes coverage limit, surfaced
    honestly rather than smuggled onto a field Hermes does not read: a WARN through
    `--dialect hermes` is a non-blocking pass, no more. (A Hermes integrator who wants
    the context delivered should use the DENY path with a soft reason, or the Python
    plugin hook, which is out of the stdout-renderer model.)

    Like every dialect this is the docs/191 §4 byte-author floor: a DENY carries a
    `reason` (a fact to read), never a rewritten tool argument. The block bytes do not
    vary by MOMENT (Hermes' `pre_tool_call` and `post_tool_call` read the same
    decision field; `post` cannot actually halt a finished tool, a host coverage
    matter, not a render difference) — so `render` is moment-agnostic, unlike the
    Gemini renderer whose PRE/STOP deny fields genuinely differ.
    """

    name = "hermes"

    def render(self, verdict: HookVerdict) -> Optional[dict]:
        if verdict.action is HookAction.PASS:
            return None
        if verdict.action is HookAction.DENY:
            # Join the operator-facing reason and any corrective fact into the one
            # field Hermes surfaces (`reason`); keep them distinct, space-joined, with
            # neither half left dangling. The canonical block shape.
            reason = " ".join(p for p in (verdict.reason, verdict.context) if p).strip()
            out: dict = {"decision": "block"}
            if reason:
                out["reason"] = reason
            return out
        # WARN → the ALLOW object. Hermes' shell hook has no non-blocking context
        # channel, so a turn-preserving verdict can only PASS here (context dropped).
        return {}


class CursorDialect:
    """Cursor — `beforeShellExecution`/`beforeMCPExecution`/`preToolUse` hooks.

    Cursor's deny grammar is a top-level `{"permission": "deny"}`; the human/agent
    messages ride `user_message`/`agent_message`. A DOS WARN (turn-preserving, do
    NOT block) maps to `{"permission": "allow", "agent_message": <context>}` —
    Cursor has no "pass-but-add-context" that is not an allow, so we allow-with-message.
    We NEVER emit Cursor's `updated_input` rewrite key (the docs/191 §4 byte-author
    floor — minting a corrective argument for the agent is forbidden); the corrective
    rides `agent_message` as a fact to read, not a value to use.
    """

    name = "cursor"

    def render(self, verdict: HookVerdict) -> Optional[dict]:
        if verdict.action is HookAction.PASS:
            return None
        if verdict.action is HookAction.DENY:
            out = {"permission": "deny"}
            if verdict.reason:
                out["agent_message"] = verdict.reason
            if verdict.context:
                # Append the corrective fact to the agent-facing message (a fact, not
                # a rewritten arg). Keep reason + context distinct, joined by a space.
                out["agent_message"] = (out.get("agent_message", "") + " " + verdict.context).strip()
            return out
        # WARN → allow + a message (Cursor's only turn-preserving "add context" path).
        return {"permission": "allow", "agent_message": verdict.context}


# ===========================================================================
# Per-vendor INSTALL specs (docs/221) — where/how `dos init --hooks <host>` wires
# the DOS hooks into each runtime's OWN config file. These are the install-side
# sibling of the dialect renderers above, and they belong HERE for the SAME reason:
# a spec must name its vendor (`cursor`/`codex`/`gemini`) and its config-file path
# as code, which the vendor-agnostic-kernel litmus forbids in a non-driver kernel
# module. The kernel (`hook_install.py`) holds only the pure machinery + the
# `claude-code` default; it discovers these by name through the `dos.hook_installs`
# entry-point group (see pyproject.toml). Facts web-grounded 2026-06-07 (docs/221
# §1a); a vendor moving is a one-line edit to its row here, never a kernel change.
# ===========================================================================
from dos.hook_install import ConfigFormat, HostHookSpec  # noqa: E402  (driver→kernel, allowed)


def cursor_install_spec() -> HostHookSpec:
    """Cursor — `.cursor/hooks.json` (JSON, requires `{"version": 1}`).

    PRE is TWO events (`beforeShellExecution` + `beforeMCPExecution`) so a refused
    call is caught whether it is a shell command or an MCP tool. Entries are FLAT
    `{"command": …}` (no `type`, no group wrapper). The `stop` event fires when the
    agent loop ends.
    """
    return HostHookSpec(
        host="cursor",
        config_path=(".cursor", "hooks.json"),
        fmt=ConfigFormat.JSON,
        pre_events=("beforeShellExecution", "beforeMCPExecution"),
        post_events=("afterFileEdit",),
        stop_events=("stop",),
        dialect_flag="--dialect cursor",
        json_entry_has_type=False,   # Cursor entries are flat {"command": …}.
        json_group_wraps=False,
        json_version=1,              # hooks.json requires {"version": 1}.
        note='Cursor honors "failClosed": true on the PRE deny — add it per-hook if '
             "you want a DOS crash to BLOCK the call (DOS itself fails to PASS; the "
             "host's fail-on-crash direction is your call).",
    )


def codex_install_spec() -> HostHookSpec:
    """OpenAI Codex CLI — `.codex/config.toml` (TOML, CC-shaped tables).

    `[[hooks.PreToolUse]]` → `[[hooks.PreToolUse.hooks]]` with `type="command"`.
    Codex fires `PreToolUse` only on its Bash/apply_patch/unified_exec/mcp handlers
    (a host coverage limit, tracked upstream) — DOS wires the right bytes; Codex
    simply won't call the hook on every tool.
    """
    return HostHookSpec(
        host="codex",
        config_path=(".codex", "config.toml"),
        fmt=ConfigFormat.TOML,
        pre_events=("PreToolUse",),
        post_events=("PostToolUse",),
        stop_events=("Stop",),
        dialect_flag="--dialect codex",
        note="Codex fires PreToolUse only on its Bash / apply_patch / unified_exec / "
             "mcp handlers (a host coverage limit, tracked upstream) — DOS wires the "
             "right bytes; Codex simply won't call the hook on every tool.",
    )


def gemini_install_spec() -> HostHookSpec:
    """Google Gemini CLI — `.gemini/settings.json` (JSON).

    Gemini's own event vocabulary: `BeforeTool` / `AfterTool` / `AfterAgent`.
    `AfterAgent` fires "once per turn after the model generates its final response"
    — the Stop analogue where `dos hook stop` refuses a premature done.

    CONFIG SHAPE — group-wrapped, byte-identical to Claude Code (verified against the
    Gemini CLI 0.45.2 bundle, 2026-06-09). Each event maps to a list of
    `{"hooks": [{"type": "command", "command": …}]}` matcher-GROUPS, NOT a flat
    `{"type", "command"}` entry: the loader's `processHookDefinition` discards any
    definition where `Array.isArray(definition.hooks)` is false (it logs
    "Discarding invalid hook definition for BeforeTool …" and drops it). Gemini
    adopted Claude-Code's hook-config format — that is why `gemini hooks migrate`
    (from Claude Code) exists — so the install shape is CC's, the same
    `json_group_wraps=True` as `claude_code_spec`. The inner hook is validated by
    `validateHookConfig`: `type` ∈ {command, plugin, runtime} and a non-empty
    `command` when `type == "command"`.

    OUTPUT SHAPE — the renderers still diverge from CC. `BeforeTool` honors a
    top-level `{"decision": "deny"}` (Gemini's tool gate throws "denied by policy"
    on `decision === "deny"`), which is what `--dialect gemini` produces via
    `GeminiDialect`. `AfterAgent` blocks the stop on `isBlockingDecision()`, which is
    true for BOTH `"block"` AND `"deny"` — so a stop refusal rendered through
    `--dialect gemini` (a `{"decision": "deny", "reason": …}`) is honored just as the
    CC-native `{"decision": "block"}` would be.

    Earlier this spec wrote flat entries (`json_group_wraps=False`) — that matched a
    pre-0.45 Gemini shape and made 0.45.2 discard EVERY DOS hook at load time. The
    group-wrap fix lands the hooks; giving the `stop` verb a `--dialect` flag lands
    the AfterAgent hook (it previously exited 2 on the unrecognized flag) — docs/268.
    """
    return HostHookSpec(
        host="gemini",
        config_path=(".gemini", "settings.json"),
        fmt=ConfigFormat.JSON,
        pre_events=("BeforeTool",),
        post_events=("AfterTool",),
        stop_events=("AfterAgent",),
        dialect_flag="--dialect gemini",
        json_entry_has_type=True,
        json_group_wraps=True,       # CC-shaped: entries nest under {"hooks": [...]} groups.
        json_version=None,
        note="Gemini 0.45.x adopted Claude-Code's group-wrapped hook-config shape "
             "(hence `gemini hooks migrate`). BeforeTool honors {\"decision\":\"deny\"}, "
             "AfterAgent honors both {\"decision\":\"block\"} and \"deny\" — all rendered "
             "via --dialect gemini.",
    )


def antigravity_install_spec() -> HostHookSpec:
    """Google Antigravity (IDE + CLI) — `.agents/hooks.json` (JSON, CC-shaped groups).

    Antigravity adopted Claude-Code's hook-CONFIG shape: each event maps to a list of
    matcher-GROUPS, each `{"hooks": [{"type": "command", "command": …}]}` (a group
    with no `matcher` matches every tool — the right default for a DOS hook that must
    adjudicate ALL tools, not one). The event names are the CC vocabulary too:
    `PreToolUse` / `PostToolUse` / `Stop` (Antigravity also fires `BeforeModel` /
    `AfterModel` / `SessionStart` / `SubAgentStop`, but DOS's three lifecycle moments
    map onto the tool + stop seams). So this spec is `json_group_wraps=True` exactly
    like `claude_code_spec`.

    What it does NOT share with CC is the hook OUTPUT grammar — Antigravity reads a
    top-level `{"decision": "deny"}` (Gemini-shaped), which is why it carries
    `--dialect antigravity` (the `AntigravityDialect` renderer), NOT the implicit CC
    default. Group-wrapped config + Gemini-shaped output is a combination no other
    host has; the `dialect_flag` (data) keeps the wired command pointed at the right
    renderer without `command_for` ever comparing a vendor literal.

    Config-file facts web-grounded 2026-06-09 (Antigravity hooks docs + the
    `Migrating to Antigravity CLI` guide: `.agents/hooks.json`, `PreToolUse` groups
    with `matcher`+`hooks`+`type/command`, `{"decision":"deny","reason":…}` output).
    """
    return HostHookSpec(
        host="antigravity",
        config_path=(".agents", "hooks.json"),
        fmt=ConfigFormat.JSON,
        pre_events=("PreToolUse",),
        post_events=("PostToolUse",),
        stop_events=("Stop",),
        dialect_flag="--dialect antigravity",
        json_entry_has_type=True,
        json_group_wraps=True,       # CC-shaped: entries nest under {"hooks": [...]} groups.
        json_version=None,
        note="Antigravity also fires BeforeModel / AfterModel / SessionStart / "
             "SubAgentStop; DOS wires the tool + stop seams (PreToolUse / PostToolUse "
             "/ Stop). A workspace .agents/hooks.json takes precedence over the global "
             "one. The hook OUTPUT is top-level {\"decision\":\"deny\"} (Gemini-shaped, "
             "via --dialect antigravity), even though the CONFIG is Claude-Code-shaped.",
    )


def claude_cowork_install_spec() -> HostHookSpec:
    """Claude Cowork — the SHARED surface: the same `.claude/settings.json` Claude
    Code reads, because Cowork runs the same agent harness (docs/298).

    Every facet equals `claude_code_spec()` — file, format, shape, events — and the
    wired command carries NO `--dialect`, deliberately: the shared file is read by
    BOTH runtimes, so the command must emit bytes both honor, and both run the CC
    harness, so the one universally-correct envelope is the default one. (A
    per-runtime divergence could never ride a shared file anyway; an explicit flag
    would add a resolution step and buy nothing.) Wiring either host name wires
    both — the merge is idempotent on the `dos hook ` prefix — and `dos doctor`
    truthfully reports both bindings.

    What is Cowork's OWN is the `note`: as of 2026-06-10 the Cowork desktop app
    does not FIRE hooks (anthropics/claude-code#63360 — user-scope hooks verified
    not firing 2026-05-28; the config/scripts live on the host while the session
    runs in a Linux VM). That is the Codex precedent — a host coverage limit
    carried as data, printed at wiring time — NOT the Trae one (docs/294): nothing
    here is invented; the grammar, events, and envelope are the CC harness's own,
    Claude Code enforces them on this workspace today, and Cowork starts enforcing
    them when the upstream issue closes, with zero DOS change.
    """
    return HostHookSpec(
        host="claude-cowork",
        config_path=(".claude", "settings.json"),
        fmt=ConfigFormat.JSON,
        pre_events=("PreToolUse",),
        post_events=("PostToolUse",),
        stop_events=("Stop",),
        dialect_flag="",          # shared file, shared harness — the default IS the envelope.
        json_entry_has_type=True,
        json_group_wraps=True,    # CC-shaped: entries nest under {"hooks": [...]} groups.
        json_version=None,
        note="Claude Cowork runs the Claude Code harness, so these hooks wire the "
             "SAME .claude/settings.json Claude Code enforces on this workspace. "
             "Cowork itself does not fire hooks yet (anthropics/claude-code#63360, "
             "as of 2026-06-10) — until that closes, Cowork's working DOS surface "
             "is advisory (MCP + skills; see src/dos_mcp/README.md). The wired "
             "`dos hook` command must be on PATH inside the session that fires it "
             "(in Cowork's VM: pip install dos-kernel there).",
    )
