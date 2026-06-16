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


class CopilotCliDialect:
    """GitHub **Copilot CLI** — the FLAT-`permissionDecision` deny grammar.

    The Copilot CLI is a sibling of the VS Code Copilot agent-hooks surface, but its
    deny OUTPUT is subtly different and is why it earns its OWN renderer rather than
    aliasing `claude-code`: it emits the SAME field NAMES as Claude Code
    (`permissionDecision` / `permissionDecisionReason`) but at the TOP LEVEL, NOT nested
    under `hookSpecificOutput` (web-grounded against the GitHub Copilot CLI hooks
    reference, 2026-06-16: `{"permissionDecision":"deny","permissionDecisionReason":…}`
    on stdout from the `preToolUse` command hook). DOS's `claude-code` renderer nests
    these under `hookSpecificOutput`, which the Copilot CLI does NOT read — so wiring it
    with `--dialect claude-code` would be a silent fail-open (the host finds no
    `permissionDecision` at the top level and proceeds). Hence a distinct renderer.

    This is the CONFIG-vs-OUTPUT split once more: the Copilot CLI config is an event-keyed
    array of typed command objects in `.github/hooks/*.json`, but the bytes a verdict
    RENDERS to are the flat top-level `permissionDecision` form. The corrective FACT (a
    provenance DENY's `context`) rides `permissionDecisionReason` appended to the operator
    reason (the Copilot CLI documents no separate context channel — the docs/191 §4
    byte-author floor: a fact to read, never a rewritten argument). A WARN (turn-preserving)
    emits a bare `{"permissionDecision":"allow"}` carrying the context on the reason — inert
    to the block gate, so it adds context without withholding the call.
    """

    name = "copilot-cli"

    def render(self, verdict: HookVerdict) -> Optional[dict]:
        if verdict.action is HookAction.PASS:
            return None
        if verdict.action is HookAction.DENY:
            out = {"permissionDecision": "deny"}
            # Join reason + the corrective fact into the one field the Copilot CLI reads.
            reason = " ".join(p for p in (verdict.reason, verdict.context) if p).strip()
            if reason:
                out["permissionDecisionReason"] = reason
            return out
        # WARN → allow with the context on the reason (turn-preserving — never blocks).
        out = {"permissionDecision": "allow"}
        if verdict.context:
            out["permissionDecisionReason"] = verdict.context
        return out


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


def hermes_install_spec() -> HostHookSpec:
    """Nous Research's **Hermes Agent** — `cli-config.yaml` (YAML, flat entries).

    Hermes' shell-hook config is a top-level `hooks:` map keyed by the SHELL-hook
    event name, each a list of flat `{command: …}` entries (which may carry extra
    keys like `timeout: 30` — preserved by `merge_yaml`). This is the exact shape
    docs/278 §"Wiring the Hermes shell hook by hand" records from the real CLI:

        hooks:
          pre_tool_call:
            - command: "dos hook pretool --workspace . --dialect hermes"

    Hermes' shell hook fires `pre_tool_call` and `post_tool_call` (verified against
    the Hermes hooks doc, 2026-06-09 — `hermes-agent.nousresearch.com/docs/user-
    guide/features/hooks`); there is NO documented stop/agent-end shell-hook event,
    so `stop_events` is empty — DOS wires only the two moments Hermes actually fires
    (the honest-coverage discipline: never invent an event name a host won't call).
    The `post_tool_call` hook cannot HALT a finished tool, a host coverage matter the
    `HermesDialect` docstring already records; DOS wires the right bytes regardless.

    The wired command carries `--dialect hermes` (the `HermesDialect` renderer above):
    a DENY emits `{"decision":"block","reason":…}` on stdout, the canonical block
    shape Hermes reads. Format is YAML — `ConfigFormat.YAML` (PyYAML, already the
    kernel's one dep), which is why this spec could only land once the YAML branch of
    `hook_install` existed (the lift docs/278 deferred). Entries are flat
    (`json_entry_has_type=False`, `json_group_wraps=False` — the Cursor-shape flags
    `merge_yaml` honors), no `version` key.
    """
    return HostHookSpec(
        host="hermes",
        config_path=("cli-config.yaml",),
        fmt=ConfigFormat.YAML,
        pre_events=("pre_tool_call",),
        post_events=("post_tool_call",),
        stop_events=(),              # Hermes' shell hook documents no stop/agent-end event.
        dialect_flag="--dialect hermes",
        json_entry_has_type=False,   # Hermes entries are flat {command: …}.
        json_group_wraps=False,
        json_version=None,
        note="Hermes wires the shell hook in cli-config.yaml (pre_tool_call / "
             "post_tool_call). It documents no stop/agent-end shell hook, so DOS wires "
             "only those two moments. A DENY prints {\"decision\":\"block\",\"reason\":…} "
             "on stdout; a WARN degrades to a non-blocking pass (Hermes' shell hook has "
             "no add-context channel). The wired `dos hook` command must be on PATH in "
             "the Hermes session.",
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


def augment_install_spec() -> HostHookSpec:
    """Augment Code's **Auggie CLI** — `.augment/settings.json` (a pure Claude-Code alias).

    Auggie cloned the Claude Code hook contract byte-for-byte (web-grounded against the
    Augment hooks docs, 2026-06-16): BOTH the config grammar (a top-level `hooks` map
    keyed by event -> group-wrapped `{"matcher":…,"hooks":[{"type":"command","command":…}]}`
    entries) AND the deny OUTPUT grammar (`{"hookSpecificOutput":{"permissionDecision":
    "deny","permissionDecisionReason":…}}` on stdout, exit 0) are identical to Claude
    Code's. So this host carries NO `--dialect` (the implicit CC default IS its envelope)
    and `json_group_wraps=True` — the same install facts as `claude_code_spec` with a
    different file. Only `permissionDecision:"deny"` is honored today (allow/ask are
    future), which is exactly the verdict DOS renders. Zero new renderer — the lowest-risk
    host in the addressable set: an install spec over the unshadowable default envelope.
    """
    return HostHookSpec(
        host="augment",
        config_path=(".augment", "settings.json"),
        fmt=ConfigFormat.JSON,
        pre_events=("PreToolUse",),
        post_events=("PostToolUse",),
        stop_events=("Stop",),
        dialect_flag="",          # CC-identical deny output — the default IS the envelope.
        json_entry_has_type=True,
        json_group_wraps=True,    # CC-shaped: entries nest under {"hooks": [...]} groups.
        json_version=None,
        note="Augment's Auggie CLI cloned the Claude Code hook contract — the same "
             ".augment/settings.json group-wrapped shape and the same "
             "hookSpecificOutput/permissionDecision deny output, so the default CC "
             "envelope is wired (no --dialect). Today only permissionDecision:\"deny\" "
             "is honored (allow/ask are future); the wired `dos hook` command must be "
             "on PATH in the Auggie session.",
    )


def devin_install_spec() -> HostHookSpec:
    """Cognition's **Devin for Terminal** CLI — `.devin/hooks.v1.json` (CC config, HERMES output).

    Devin is the config-vs-output split made sharp (web-grounded 2026-06-16): its hook
    CONFIG is Claude-Code-SHAPED — a top-level event map of group-wrapped
    `{"matcher":…,"hooks":[{"type":"command","command":…,"timeout":…}]}` rules (it even
    reads `.claude/settings.json`) — BUT its DENY OUTPUT is a FLAT top-level
    `{"decision":"block","reason":…}` (no `hookSpecificOutput` wrapper, no nested
    `permissionDecision`, no `continue` field). That flat block shape is byte-identical
    to the **hermes** dialect, NOT claude-code — so the wired command carries
    `--dialect hermes` while the config stays `json_group_wraps=True` (CC-shaped). This is
    the Antigravity precedent in mirror image: there a CC-shaped config paired with a
    Gemini-shaped output; here a CC-shaped config pairs with a Hermes-shaped output. The
    `dialect_flag` (data) keeps the wired command pointed at the right renderer without
    `command_for` ever comparing a vendor literal. Exit code 2 also blocks (a host
    fallback, not a render concern).
    """
    return HostHookSpec(
        host="devin",
        config_path=(".devin", "hooks.v1.json"),
        fmt=ConfigFormat.JSON,
        pre_events=("PreToolUse",),
        post_events=("PostToolUse",),
        stop_events=("Stop",),
        dialect_flag="--dialect hermes",   # flat {"decision":"block"} output = the hermes envelope.
        json_entry_has_type=True,
        json_group_wraps=True,             # CC-shaped config (group-wrapped), like Antigravity.
        json_version=None,
        note="Devin for Terminal has a Claude-Code-SHAPED hook config (group-wrapped "
             "matcher+hooks under PreToolUse/PostToolUse/Stop, and it also reads "
             ".claude/settings.json) but a flat top-level {\"decision\":\"block\",\"reason\":…} "
             "deny OUTPUT — so DOS wires the CC-shaped config with --dialect hermes (the "
             "matching flat-block renderer). The wired `dos hook` command must be on PATH "
             "in the Devin session.",
    )


def cursor_cli_install_spec() -> HostHookSpec:
    """Cursor's **cursor-agent** CLI — the SHARED `.cursor/hooks.json` surface.

    The Cursor CLI (cursor-agent) reads the SAME `.cursor/hooks.json` the Cursor IDE
    reads, with the SAME flat `{"command":…,"matcher":…,"timeout":…}` entries under a
    top-level `hooks` map, the SAME `{"version":1}` requirement, and the SAME top-level
    `{"permission":"deny"}` deny output (web-grounded 2026-06-16). So this is the
    claude-cowork precedent (docs/298) on the Cursor axis: a shared config file read by
    two runtimes (the IDE and the CLI), wired once. Every facet equals `cursor_install_spec`
    — file, format, flat entries, version, the `--dialect cursor` renderer — so wiring
    either name wires both, idempotent on the `dos hook ` prefix. Kept as its own host
    name so `dos init --hooks cursor-cli` resolves and `dos hosts` lists the CLI surface
    explicitly, and so a future CLI-only divergence has a home.
    """
    return HostHookSpec(
        host="cursor-cli",
        config_path=(".cursor", "hooks.json"),
        fmt=ConfigFormat.JSON,
        pre_events=("beforeShellExecution", "beforeMCPExecution"),
        post_events=("afterFileEdit",),
        stop_events=("stop",),
        dialect_flag="--dialect cursor",
        json_entry_has_type=False,   # Cursor entries are flat {"command": …}.
        json_group_wraps=False,
        json_version=1,              # hooks.json requires {"version": 1}.
        note="Cursor's cursor-agent CLI reads the SAME .cursor/hooks.json the Cursor IDE "
             "reads, with the same flat entries, {\"version\":1}, and {\"permission\":\"deny\"} "
             "output — so wiring `cursor` or `cursor-cli` wires the same file (one set of "
             "hooks, idempotent). The wired `dos hook` command must be on PATH in the "
             "cursor-agent session.",
    )


def crush_install_spec() -> HostHookSpec:
    """Charmbracelet's **Crush** — `crush.json` (flat event-keyed config, ANTIGRAVITY output).

    Crush is another config-vs-output split (source-verified against
    charmbracelet/crush 2026-06-16: internal/config/config.go, internal/hooks/input.go,
    the bundled crush-config SKILL.md): its hook CONFIG is a top-level `hooks` map keyed
    by event name, each a list of FLAT `{matcher, command, timeout}` entries (NOT Claude
    Code's group-wrapped `{matcher, hooks:[{type,command}]}` nesting). Its NATIVE deny
    OUTPUT is a flat top-level `{"decision":"deny","reason":…}` — byte-identical to what
    `AntigravityDialect` already emits, so it reuses the **antigravity** renderer (zero
    new renderer). (Crush ALSO compat-accepts the CC wrapper and an exit-2 deny, but its
    own documented output is the antigravity shape, so per the output-shape rule it
    aliases antigravity.)

    The config lives INSIDE `crush.json` under a top-level `hooks` object (not a dedicated
    hooks file like `.cursor/hooks.json`), so the merge targets `hooks.<event>` — the same
    flat-JSON shape `merge_json` already handles with `json_group_wraps=False` /
    `json_entry_has_type=False`. Crush implements `PreToolUse` and designs `PostToolUse`;
    it documents no stop/agent-end hook, so DOS wires only the two tool moments (the
    honest-coverage rule, the docs/294 discipline).
    """
    return HostHookSpec(
        host="crush",
        config_path=("crush.json",),
        fmt=ConfigFormat.JSON,
        pre_events=("PreToolUse",),
        post_events=("PostToolUse",),
        stop_events=(),              # Crush documents no stop/agent-end hook.
        dialect_flag="--dialect antigravity",   # native flat {"decision":"deny"} = the antigravity envelope.
        json_entry_has_type=False,   # Crush entries are flat {matcher, command, timeout}.
        json_group_wraps=False,
        json_version=None,
        note="Crush wires hooks inside crush.json under a top-level `hooks` map "
             "(PreToolUse / PostToolUse), flat {matcher,command,timeout} entries. Its "
             "native deny output is a flat top-level {\"decision\":\"deny\",\"reason\":…}, so "
             "DOS wires --dialect antigravity (the matching renderer). It documents no "
             "stop hook, so only the two tool moments are wired. The wired `dos hook` "
             "command must be on PATH in the Crush session.",
    )


def qwen_install_spec() -> HostHookSpec:
    """Alibaba's **Qwen Code** — `.qwen/settings.json` (a pure Claude-Code alias).

    Qwen Code's hook system is structurally identical to Claude Code's (source-verified
    against QwenLM/qwen-code docs/users/features/hooks.md, 2026-06-16): `.qwen/settings.json`
    holds a top-level `hooks` map keyed by event, each a group-wrapped
    `{"matcher":…,"hooks":[{"type":"command","command":…}]}` entry; and its PreToolUse deny
    OUTPUT is the NESTED `{"hookSpecificOutput":{"permissionDecision":"deny",
    "permissionDecisionReason":…}}` — byte-identical to Claude Code. So it carries NO
    `--dialect` (the default CC envelope is its envelope), `json_group_wraps=True`. Zero new
    renderer; an install spec over the unshadowable default, the augment precedent.
    """
    return HostHookSpec(
        host="qwen",
        config_path=(".qwen", "settings.json"),
        fmt=ConfigFormat.JSON,
        pre_events=("PreToolUse",),
        post_events=("PostToolUse",),
        stop_events=("Stop",),
        dialect_flag="",          # CC-identical nested deny output — the default IS the envelope.
        json_entry_has_type=True,
        json_group_wraps=True,    # CC-shaped: entries nest under {"hooks": [...]} groups.
        json_version=None,
        note="Qwen Code's hook system is structurally identical to Claude Code's — the "
             "same .qwen/settings.json group-wrapped shape and the same nested "
             "hookSpecificOutput/permissionDecision deny output, so the default CC "
             "envelope is wired (no --dialect). The wired `dos hook` command must be on "
             "PATH in the Qwen Code session.",
    )


def continue_install_spec() -> HostHookSpec:
    """Continue.dev's **Continue CLI** — `.continue/settings.json` (a Claude-Code alias).

    Continue CLI adopted the Claude Code hook contract (source-verified 2026-06-16): a
    top-level `hooks` map of group-wrapped `{"matcher":…,"hooks":[{"type":"command",
    "command":…}]}` entries, and a NESTED `{"hookSpecificOutput":{"permissionDecision":
    "deny","permissionDecisionReason":…}}` PreToolUse deny OUTPUT — byte-identical to CC.
    Continue ALSO reads `.claude/settings.json` directly (cross-tool reuse); DOS wires its
    OWN `.continue/settings.json` so the binding is explicit and never collides with the
    claude-code host's file. NO `--dialect` (the default CC envelope), `json_group_wraps=True`.
    Zero new renderer — the augment/qwen precedent.
    """
    return HostHookSpec(
        host="continue",
        config_path=(".continue", "settings.json"),
        fmt=ConfigFormat.JSON,
        pre_events=("PreToolUse",),
        post_events=("PostToolUse",),
        stop_events=("Stop",),
        dialect_flag="",          # CC-identical nested deny output — the default IS the envelope.
        json_entry_has_type=True,
        json_group_wraps=True,    # CC-shaped: entries nest under {"hooks": [...]} groups.
        json_version=None,
        note="Continue CLI adopted the Claude Code hook contract — group-wrapped "
             ".continue/settings.json + nested hookSpecificOutput/permissionDecision deny "
             "output, so the default CC envelope is wired (no --dialect). Continue also "
             "reads .claude/settings.json directly; DOS wires its own .continue file so "
             "the binding is explicit. The wired `dos hook` command must be on PATH in "
             "the Continue session.",
    )


def openhands_install_spec() -> HostHookSpec:
    """All-Hands' **OpenHands** (formerly OpenDevin) — `.openhands/hooks.json` (CC config, ANTIGRAVITY output).

    OpenHands is another config-vs-output split (source-verified against
    docs.openhands.dev/openhands/usage/customization/hooks, 2026-06-16): its hook CONFIG is
    a group-wrapped `{"matcher":…,"hooks":[{"command":…,"timeout":…}]}` map (explicitly
    "compatible with Claude Code hooks"), BUT its deny OUTPUT is a FLAT top-level
    `{"decision":"deny","reason":…}` on stdout (exit 2) — byte-identical to what
    `AntigravityDialect` emits, NOT Claude Code's nested form. So it reuses the
    **antigravity** renderer (`--dialect antigravity`) with a `json_group_wraps=True`
    (CC-shaped) config — the Antigravity precedent itself, applied to a third host.
    OpenHands accepts both snake_case (`pre_tool_use`) and PascalCase (`PreToolUse`) event
    keys; DOS writes the PascalCase forms (cross-tool-consistent). Zero new renderer.
    """
    return HostHookSpec(
        host="openhands",
        config_path=(".openhands", "hooks.json"),
        fmt=ConfigFormat.JSON,
        pre_events=("PreToolUse",),
        post_events=("PostToolUse",),
        stop_events=("Stop",),
        dialect_flag="--dialect antigravity",   # flat top-level {"decision":"deny"} output = antigravity.
        json_entry_has_type=False,   # OpenHands entries are flat {"command":…,"timeout":…} inside the group.
        json_group_wraps=True,       # CC-shaped config (group-wrapped), CC-compatible.
        json_version=None,
        note="OpenHands has a Claude-Code-COMPATIBLE hook config (group-wrapped "
             ".openhands/hooks.json) but a flat top-level {\"decision\":\"deny\",\"reason\":…} "
             "deny OUTPUT (exit 2), so DOS wires --dialect antigravity (the matching "
             "flat-top-level renderer). The wired `dos hook` command must be on PATH in "
             "the OpenHands session.",
    )


def tabnine_install_spec() -> HostHookSpec:
    """Tabnine's agent CLI — `.tabnine/agent/settings.json` (CC-style config, ANTIGRAVITY output).

    Tabnine's hook config is a top-level `hooks` map keyed by event, each a group-wrapped
    `{"hooks":[{"type":"command","command":…}]}` entry (CC-style inner array, no `matcher`
    at the group level), and its deny OUTPUT is a top-level `{"decision":"deny","reason":…}`
    on stdout, stderr as the reason (verified against docs.tabnine.com/.../hooks, 2026-06-16).
    The top-level `{"decision":"deny"}` output is byte-identical to `AntigravityDialect`, so
    it reuses the **antigravity** renderer (`--dialect antigravity`) with a
    `json_group_wraps=True` config. Tabnine's event vocabulary: `BeforeTool` / `AfterTool`
    for the tool moments, and `AfterAgent` as the stop analogue (it fires after the agent's
    turn; `{"decision":"deny"}`/`"block"` there refuses the completion). Zero new renderer.
    """
    return HostHookSpec(
        host="tabnine",
        config_path=(".tabnine", "agent", "settings.json"),
        fmt=ConfigFormat.JSON,
        pre_events=("BeforeTool",),
        post_events=("AfterTool",),
        stop_events=("AfterAgent",),
        dialect_flag="--dialect antigravity",   # top-level {"decision":"deny"} output = antigravity.
        json_entry_has_type=True,
        json_group_wraps=True,       # CC-style inner array {"hooks":[{type,command}]}.
        json_version=None,
        note="Tabnine's agent CLI wires hooks in .tabnine/agent/settings.json (BeforeTool / "
             "AfterTool / AfterAgent), CC-style group-wrapped entries. Its deny output is a "
             "top-level {\"decision\":\"deny\",\"reason\":…} on stdout (stderr as the reason), so "
             "DOS wires --dialect antigravity (the matching renderer). The wired `dos hook` "
             "command must be on PATH in the Tabnine session.",
    )


def factory_install_spec() -> HostHookSpec:
    """Factory AI's **Droid** CLI — `.factory/hooks.json` (a pure Claude-Code alias).

    Droid's hook contract is Claude-Code-identical (verified against the Factory docs,
    2026-06-16): `.factory/hooks.json` (project) holds a top-level `hooks` map of
    group-wrapped `{"matcher":…,"hooks":[{"type":"command","command":…}]}` entries, and
    its PreToolUse deny OUTPUT is the NESTED `{"hookSpecificOutput":{"permissionDecision":
    "deny","permissionDecisionReason":…}}` (stdout, exit 0) — byte-identical to Claude
    Code. So NO `--dialect` (the default CC envelope), `json_group_wraps=True`. Zero new
    renderer — the augment/qwen/continue precedent. (Droid also reads
    `~/.factory/hooks.json` user-global and a `hooks` key in `~/.factory/settings.json`;
    DOS wires the project `.factory/hooks.json` — the explicit, workspace-scoped binding.)
    """
    return HostHookSpec(
        host="factory",
        config_path=(".factory", "hooks.json"),
        fmt=ConfigFormat.JSON,
        pre_events=("PreToolUse",),
        post_events=("PostToolUse",),
        stop_events=("Stop",),
        dialect_flag="",          # CC-identical nested deny output — the default IS the envelope.
        json_entry_has_type=True,
        json_group_wraps=True,    # CC-shaped: entries nest under {"hooks": [...]} groups.
        json_version=None,
        note="Factory AI's Droid CLI has a Claude-Code-identical hook contract — "
             ".factory/hooks.json group-wrapped + nested hookSpecificOutput/permissionDecision "
             "deny output — so the default CC envelope is wired (no --dialect). Droid also "
             "reads ~/.factory/hooks.json and ~/.factory/settings.json; DOS wires the project "
             "file. The wired `dos hook` command must be on PATH in the Droid session.",
    )


def copilot_install_spec() -> HostHookSpec:
    """GitHub **Copilot agent mode** (VS Code Agent hooks) — `.github/hooks/dos.json` (claude-code).

    The VS Code Copilot "Agent hooks" surface is a Claude-Code alias (verified against
    code.visualstudio.com/docs/agent-customization/hooks + the microsoft/vscode-copilot-chat
    source, 2026-06-16): PascalCase events (`PreToolUse`/`PostToolUse`/`Stop`), a
    group-wrapped `{"hooks":{"PreToolUse":[{"type":"command","command":…}]}}` config (it even
    reads `.claude/settings.json`), and a NESTED `{"hookSpecificOutput":{"permissionDecision":
    "deny","permissionDecisionReason":…}}` deny OUTPUT — byte-identical to Claude Code, and it
    fails CLOSED (a hook error denies). So NO `--dialect`, `json_group_wraps=True`. DOS wires a
    dedicated `.github/hooks/dos.json` (Copilot loads every `.github/hooks/*.json`) so the
    binding is explicit and never collides with the `claude-code` host's `.claude/settings.json`.
    Zero new renderer.

    (The GitHub Copilot *CLI* is a SIBLING surface with camelCase events and a FLAT top-level
    `{"permissionDecision":"deny"}` output — a NOVEL grammar, NOT this host; it needs its own
    renderer and is tracked separately.)
    """
    return HostHookSpec(
        host="copilot",
        config_path=(".github", "hooks", "dos.json"),
        fmt=ConfigFormat.JSON,
        pre_events=("PreToolUse",),
        post_events=("PostToolUse",),
        stop_events=("Stop",),
        dialect_flag="",          # CC-identical nested deny output — the default IS the envelope.
        json_entry_has_type=True,
        json_group_wraps=True,    # CC-shaped: entries nest under {"hooks": [...]} groups.
        json_version=None,
        note="GitHub Copilot agent mode (VS Code Agent hooks) is a Claude-Code alias — "
             "PascalCase PreToolUse/PostToolUse/Stop, group-wrapped config (it reads "
             ".claude/settings.json too), nested hookSpecificOutput/permissionDecision deny "
             "output, fails CLOSED — so the default CC envelope is wired (no --dialect). DOS "
             "writes a dedicated .github/hooks/dos.json (Copilot loads every "
             ".github/hooks/*.json). The Copilot CLI is a separate host (flat permissionDecision "
             "output — a novel grammar). The wired `dos hook` command must be on PATH.",
    )


def copilot_cli_install_spec() -> HostHookSpec:
    """GitHub **Copilot CLI** — `.github/hooks/dos.json` (flat-with-type config, NOVEL output).

    The Copilot CLI (the standalone `copilot` / `gh copilot` agent, distinct from the VS
    Code agent-hooks surface) loads every `.github/hooks/*.json` (also `~/.copilot/hooks/`).
    The config is a top-level `{"version":1,"hooks":{"<event>":[{...}]}}` where each event
    maps to an ARRAY of typed command objects `{"type":"command","command":…}` — NOT Claude
    Code's group-wrapped `{matcher, hooks:[{type,command}]}` nesting, and NOT a bare flat
    `{command}`. So `json_entry_has_type=True` (the entry carries `type:"command"`) with
    `json_group_wraps=False` (no `{"hooks":[…]}` wrapper). Events are camelCase:
    `preToolUse` / `postToolUse` / `agentStop` (verified against the GitHub Copilot CLI
    hooks reference, 2026-06-16). It requires a top-level `{"version":1}`.

    The deny OUTPUT is a FLAT top-level `{"permissionDecision":"deny","permissionDecisionReason":…}`
    — the field NAMES of Claude Code but un-nested — so it carries `--dialect copilot-cli`
    (the `CopilotCliDialect` renderer above), the only genuinely NEW dialect of the host
    sweep. (`preToolUse` denies via the JSON field; exit-2 is a deny shortcut for the
    separate `permissionRequest` event, not wired here.)
    """
    return HostHookSpec(
        host="copilot-cli",
        config_path=(".github", "hooks", "dos.json"),
        fmt=ConfigFormat.JSON,
        pre_events=("preToolUse",),
        post_events=("postToolUse",),
        stop_events=("agentStop",),
        dialect_flag="--dialect copilot-cli",   # flat top-level permissionDecision — the novel renderer.
        json_entry_has_type=True,    # entry is {"type":"command","command":…}…
        json_group_wraps=False,      # …in a flat array under the event key (no {"hooks":[…]} wrapper).
        json_version=1,              # .github/hooks/*.json requires {"version": 1}.
        note="GitHub Copilot CLI (the standalone copilot agent, NOT the VS Code agent-hooks "
             "surface) loads every .github/hooks/*.json; camelCase events "
             "preToolUse/postToolUse/agentStop. Its deny output is a FLAT top-level "
             "{\"permissionDecision\":\"deny\",…} (CC field names, un-nested), so DOS wires "
             "--dialect copilot-cli (a distinct renderer — the claude-code dialect's NESTED "
             "form would be a silent fail-open here). The wired `dos hook` command must be "
             "on PATH in the Copilot CLI session.",
    )


def kimi_install_spec() -> HostHookSpec:
    """MoonshotAI's **Kimi CLI** — `~/.kimi/config.toml` (flat TOML hooks, claude-code output).

    Kimi's hook config is a FLAT TOML array-of-tables (verified against MoonshotAI/kimi-cli,
    2026-06-16): `[[hooks]]` with `event = "PreToolUse"` (required), `command` (required),
    optional `matcher`/`timeout` — NOT Codex's CC-shaped `[[hooks.EVENT]]` nesting. So this
    spec sets `toml_event_key="event"` (the flat-block path of `_toml_block`). Its deny OUTPUT
    is the NESTED `{"hookSpecificOutput":{"permissionDecision":"deny",…}}` — byte-identical to
    Claude Code — so it carries NO `--dialect` (the default CC envelope). Zero new renderer.

    The path is `~/.kimi/config.toml` (user-global); `dos init --hooks kimi <dir>` writes
    `<dir>/.kimi/config.toml`, so an operator wiring their home runs it against `~` (or hand-
    copies the fenced block). Events PreToolUse / PostToolUse / Stop.
    """
    return HostHookSpec(
        host="kimi",
        config_path=(".kimi", "config.toml"),
        fmt=ConfigFormat.TOML,
        pre_events=("PreToolUse",),
        post_events=("PostToolUse",),
        stop_events=("Stop",),
        dialect_flag="",             # nested permissionDecision deny output = the CC default envelope.
        toml_event_key="event",      # flat [[hooks]] with `event = "<Event>"`.
        note="Kimi CLI's hooks live in a FLAT TOML array-of-tables in .kimi/config.toml "
             "([[hooks]] with event=\"PreToolUse\"/…). Its deny output is the nested "
             "hookSpecificOutput/permissionDecision form (claude-code), so the default CC "
             "envelope is wired (no --dialect). The path is user-global (~/.kimi/config.toml); "
             "the wired `dos hook` command must be on PATH in the Kimi session.",
    )


def vibe_install_spec() -> HostHookSpec:
    """Mistral AI's **Vibe** CLI — `.vibe/hooks.toml` (flat TOML hooks, antigravity output).

    Mistral Vibe's hook config is a FLAT TOML array-of-tables (verified 2026-06-16):
    `[[hooks]]` with `type = "before_tool"` (the event), `match` (tool matcher), `command`,
    `timeout` — NOT Codex's CC-shaped nesting. So this spec sets `toml_event_key="type"` (the
    flat-block path) and names Vibe's own event vocabulary (`before_tool` / `after_tool` /
    `post_agent_turn`). Its deny OUTPUT is a flat top-level `{"decision":"deny","reason":…}`
    on stdout — byte-identical to `AntigravityDialect` — so it carries `--dialect antigravity`.
    Hooks are gated behind `enable_experimental_hooks = true` in Vibe's config.toml (a host
    enablement the operator sets; DOS wires the bytes). Zero new renderer.
    """
    return HostHookSpec(
        host="vibe",
        config_path=(".vibe", "hooks.toml"),
        fmt=ConfigFormat.TOML,
        pre_events=("before_tool",),
        post_events=("after_tool",),
        stop_events=("post_agent_turn",),
        dialect_flag="--dialect antigravity",   # flat top-level {"decision":"deny"} output = antigravity.
        toml_event_key="type",       # flat [[hooks]] with `type = "before_tool"`.
        note="Mistral Vibe's hooks live in a FLAT TOML array-of-tables in .vibe/hooks.toml "
             "([[hooks]] with type=\"before_tool\"/…), gated behind "
             "enable_experimental_hooks=true in config.toml. Its deny output is a flat "
             "top-level {\"decision\":\"deny\",\"reason\":…}, so DOS wires --dialect antigravity. "
             "The wired `dos hook` command must be on PATH in the Vibe session.",
    )
