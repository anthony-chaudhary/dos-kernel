"""hook_dialect — render a DOS PRE/POST/STOP verdict into the bytes a host honors.

> **The verdict is the kernel; the envelope is a driver (docs/217).** DOS computes
> ONE dialect-neutral hook decision (deny / warn / pass) and renders it into the
> exact JSON the *host runtime* parses — Claude Code today, Gemini CLI / Codex CLI /
> Cursor next. This is the third instance of the kernel's pure-protocol +
> by-name-resolver pattern, after `dos.judges` (the JUDGE rung) and
> `dos.overlap_policy` (the disjointness scorer) — here on the OUTPUT side.

Why this module exists
======================

`pretool_sensor.decide()` / `posttool_sensor.warn_payload()` already emit the exact
**Claude Code** `hookSpecificOutput` envelope (the one real CC honors, verified
against the CC source). But the other agent runtimes ship their OWN deny-capable
pre-tool hook with a DIFFERENT envelope:

  * **Gemini CLI** (`BeforeTool`/`AfterTool`):   `{"decision": "deny", "reason": …}`
  * **Codex CLI** (`PreToolUse`/`PostToolUse`):   CC-identical `hookSpecificOutput`
  * **Cursor** (`beforeShellExecution`/…):        `{"permission": "deny"|"allow", …}`

Point `dos hook pretool` at any of those today and it emits the CC envelope they do
NOT parse — a SILENT no-op (the original `dos hook stop`-vs-CC bug). This module
closes that: a host-selected renderer turns the verdict into the right bytes.

The neutral form is the Claude-Code dict
========================================

`decide()` already returns the CC dict, and that dict is **lossless** for every
target host: a deny carries `permissionDecisionReason` (+ optional
`additionalContext`), a warn carries `additionalContext`, a pass is `None`. So
rather than re-plumb `decide()`'s four return sites (and churn the 67 green hook
tests that assert its CC shape), we treat the CC dict as the canonical internal
form and TRANSCODE it: `parse_cc(cc_dict) -> HookVerdict`, then
`dialect.render(verdict) -> host_dict`. The `ClaudeCodeDialect` round-trips to the
SAME bytes `decide()` already produced (so `--dialect claude-code`, the default, is
byte-for-byte today's behavior — the docs/217 Phase-1 gate).

Discipline (inherited from docs/191 §4, the byte-author floor)
==============================================================

NO dialect ever emits a tool-input REWRITE key (`updatedInput` / `updated_input`;
Cursor's `preToolUse` *can* rewrite input — DOS must NOT). A verdict carries only a
`reason` (operator-facing why) and a `context` (a fact to RE-SURFACE) — never a
corrective ARGUMENT minted for the agent. Rendering is PURE: a verdict in, a dict
out, no I/O — the same line `judges`/`overlap_policy` draw around their seams.

A wrong dialect name fails LOUD, not silent
===========================================

`resolve_dialect("typo")` RAISES. Unlike `judges` (fail-to-abstain) and
`overlap_policy` (fail-to-floor), whose fallbacks are the SAFE direction, a dialect
fallback is NOT safe: a host that asked for `cursor` and silently got `claude-code`
emits a no-op against Cursor — the exact failure this module exists to prevent. So
an unknown dialect is an operator error surfaced immediately, never papered over.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# The dialect-neutral verdict — what the PEP decided, with no envelope grammar.
# ---------------------------------------------------------------------------
class HookMoment(enum.Enum):
    """Which lifecycle seam the verdict is for (selects the host's event name)."""

    PRE = "pre"    # before a tool runs — the only moment a deny is honored
    POST = "post"  # after a tool ran — context-only (cannot block)
    STOP = "stop"  # the agent wants to stop — refuse a false done
    SESSION = "session"  # a session is starting — inject ground-truth context (cannot block)


class HookAction(enum.Enum):
    """The dialect-neutral decision. Maps 1:1 onto every host's grammar."""

    DENY = "deny"  # withhold the call / refuse the stop
    ASK = "ask"    # escalate to the human's permission prompt (do NOT decide for them)
    WARN = "warn"  # add context, do NOT block (turn-preserving)
    PASS = "pass"  # emit nothing


@dataclass(frozen=True)
class HookVerdict:
    """A host-free description of a PRE/POST/STOP decision. PURE data.

    `reason`   — the operator-facing why (carried on DENY and WARN).
    `context`  — a fact to RE-SURFACE to the agent (a WARN's whole payload, or the
                 corrective surfaced alongside a provenance DENY). NEVER a rewritten
                 tool argument (the docs/191 §4 byte-author floor).
    """

    moment: HookMoment
    action: HookAction
    reason: str = ""
    context: str = ""


# ---------------------------------------------------------------------------
# The enforcement POSTURE — how a refusal is SURFACED (docs/370). The kernel
# computes ONE verdict; the operator picks whether a refusal hard-blocks, asks
# the human, or merely records. PURE: a posture only ever RE-SHAPES a refusal
# (DENY) into a softer surface; it never hardens a PASS/WARN into a block, and
# never turns an admit into a deny. So a posture is monotone-softening — the
# refuse-equal-or-softer dual of the overlap policy's refuse-MORE-only floor.
#
# DOS shipped assuming the headless / bypass-permissions deployment, where DOS
# is the ONLY gate, so a refusal must BLOCK. The control-oriented deployment
# keeps a human in the loop and wants DOS to ESCALATE to that human's existing
# permission prompt instead of deciding for them. The posture is that choice.
# ---------------------------------------------------------------------------
class Posture(enum.Enum):
    """How the host surfaces a DOS refusal. Selected by the operator, never by an agent.

      OBSERVE — record only. A refusal degrades to a turn-preserving WARN: DOS
                names the violation but never blocks and never prompts. The
                cautious first-adopter posture (the pre-docs/364 behavior, now an
                explicit choice).
      BLOCK   — a refusal hard-DENYs the call. DOS is the gate. The headless /
                bypass-permissions default — byte-for-byte today's behavior, so an
                operator who sets nothing sees no change (the parity floor).
      GATE    — a refusal ESCALATES to the human's permission prompt (an ASK at the
                PRE moment). DOS does not decide for the human; it hands them an
                adjudicated reason the agent could not have authored. The
                control-oriented / human-in-the-loop posture.
    """

    OBSERVE = "observe"
    BLOCK = "block"
    GATE = "gate"


#: The default — BLOCK, so an operator who declares no posture gets exactly the
#: bytes DOS has emitted since the hooks shipped (the docs/370 parity floor).
DEFAULT_POSTURE = Posture.BLOCK


def parse_posture(name: Optional[str]) -> Posture:
    """Resolve a posture name to a `Posture`. PURE. Lenient → `DEFAULT_POSTURE`.

    Unlike `resolve_dialect` (fail-LOUD), an unknown / empty posture name falls
    back to BLOCK — the SAFE direction (more restrictive than GATE/OBSERVE), and
    the one that preserves today's bytes. A typo can only ever make DOS *stricter*,
    never silently drop a gate; `dos doctor` surfaces the effective posture so the
    typo is visible without breaking the hook hot path.
    """
    if not name:
        return DEFAULT_POSTURE
    try:
        return Posture(str(name).strip().lower())
    except ValueError:
        return DEFAULT_POSTURE


def under_posture(verdict: HookVerdict, posture: Posture) -> HookVerdict:
    """Re-shape a verdict's SURFACE for the operator's posture. PURE, total.

    Only a DENY is ever re-shaped (a PASS/WARN/ASK passes through untouched — a
    posture never hardens). The mapping:

      * BLOCK   → unchanged (a DENY stays a DENY — today's behavior).
      * OBSERVE → a DENY becomes a WARN; the refusal's `reason` is folded into the
                  `context` so the human still reads WHY, but nothing blocks.
      * GATE    → a PRE-moment DENY becomes an ASK (route to the human's permission
                  prompt). A non-PRE DENY (a Stop refusal, a Post context) stays a
                  DENY: "ask the human" is meaningful only BEFORE a tool runs — a
                  stop has no permission-prompt seam to escalate into, so GATE leaves
                  it a block (the honest-coverage direction, never a fabricated ask).

    The transform is the keystone that lets DOS PARTICIPATE in a host's permission
    flow rather than replace it: the same adjudicated verdict, surfaced as the
    operator chose.
    """
    if verdict.action is not HookAction.DENY:
        return verdict
    if posture is Posture.OBSERVE:
        merged = " ".join(p for p in (verdict.reason, verdict.context) if p).strip()
        return HookVerdict(moment=verdict.moment, action=HookAction.WARN, context=merged)
    if posture is Posture.GATE and verdict.moment is HookMoment.PRE:
        return HookVerdict(
            moment=verdict.moment, action=HookAction.ASK,
            reason=verdict.reason, context=verdict.context,
        )
    return verdict


# ---------------------------------------------------------------------------
# Transcode the canonical CC dict (what decide()/warn_payload already return)
# into the neutral verdict. PURE.
# ---------------------------------------------------------------------------
def parse_cc(cc_dict: Optional[dict], *, moment: HookMoment) -> HookVerdict:
    """Read a Claude-Code hook dict (or None) into a `HookVerdict`. PURE, total.

    `None` (or any unparseable shape) → a PASS verdict (emit nothing) — the
    fail-to-passthrough direction the sensors already take. A `permissionDecision:
    deny` → DENY (reason + any `additionalContext` as context). An
    `additionalContext` with NO `permissionDecision` → WARN (context = that text).
    """
    if not isinstance(cc_dict, dict):
        return HookVerdict(moment=moment, action=HookAction.PASS)
    hso = cc_dict.get("hookSpecificOutput")
    if not isinstance(hso, dict):
        return HookVerdict(moment=moment, action=HookAction.PASS)
    decision = hso.get("permissionDecision")
    context = hso.get("additionalContext")
    context = context if isinstance(context, str) else ""
    if decision in ("deny", "ask"):
        reason = hso.get("permissionDecisionReason")
        reason = reason if isinstance(reason, str) else ""
        act = HookAction.DENY if decision == "deny" else HookAction.ASK
        return HookVerdict(moment=moment, action=act, reason=reason, context=context)
    if context:
        # additionalContext present, no deny → a WARN (turn-preserving re-surface).
        return HookVerdict(moment=moment, action=HookAction.WARN, context=context)
    return HookVerdict(moment=moment, action=HookAction.PASS)


# ---------------------------------------------------------------------------
# The dialect Protocol + the four built-in renderers. Each is PURE: verdict in,
# host dict (or None for PASS) out. NO I/O, NO tool-input rewrite key.
# ---------------------------------------------------------------------------
@runtime_checkable
class HookDialect(Protocol):
    name: str

    def render(self, verdict: HookVerdict) -> Optional[dict]:
        """The host's envelope for this verdict, or None to emit nothing (PASS)."""
        ...


_CC_EVENT = {
    HookMoment.PRE: "PreToolUse",
    HookMoment.POST: "PostToolUse",
    HookMoment.STOP: "Stop",
    HookMoment.SESSION: "SessionStart",
}


class ClaudeCodeDialect:
    """The DEFAULT — byte-for-byte what `decide()`/`warn_payload` already emit.

    A round-trip floor: `render(parse_cc(d))` reproduces `d` for the deny/warn/pass
    cases the sensors produce, so `--dialect claude-code` is today's behavior exactly
    (the docs/217 Phase-1 gate, pinned by the existing 67-test hook suite + a
    round-trip test here).
    """

    name = "claude-code"

    def render(self, verdict: HookVerdict) -> Optional[dict]:
        if verdict.action is HookAction.PASS:
            return None
        event = _CC_EVENT[verdict.moment]
        if verdict.action in (HookAction.DENY, HookAction.ASK):
            # `deny` withholds the call; `ask` routes it to the human's permission
            # prompt — both are the `permissionDecision` gate CC honors
            # (`z.enum(['allow','deny','ask'])`, verified against the CC source). The
            # only field that differs is the verb; the why + corrective ride the same
            # `permissionDecisionReason` / `additionalContext` channels.
            hso = {
                "hookEventName": event,
                "permissionDecision": "deny" if verdict.action is HookAction.DENY else "ask",
                "permissionDecisionReason": verdict.reason,
            }
            if verdict.context:
                hso["additionalContext"] = verdict.context
            return {"hookSpecificOutput": hso}
        # WARN — additionalContext only, no permissionDecision (passthrough).
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": verdict.context}}


# The non-default, vendor-NAMED renderers (`codex` / `gemini` / `cursor`) used to
# live here, but a renderer must name its vendor as CODE (a `GeminiDialect` is
# inherently Gemini-specific), which the vendor-blindness litmus forbids in a kernel
# module (`tests/test_vendor_agnostic_kernel.py`). So — per docs/217's own thesis
# ("the envelope is a driver") and the judges/overlap_policy precedent — they moved
# to `dos.drivers.hook_dialects` and register through the `dos.hook_dialects`
# entry-point group. `resolve_dialect("gemini")` discovers them by name at the call
# boundary; the kernel seam imports no vendor renderer. The ONE built-in that stays
# is `ClaudeCodeDialect`: the unshadowable default (byte-for-byte what the sensors
# already emit), the analogue of `AbstainJudge` — a deterministic baseline the kernel
# always has even with zero drivers installed.

# Singleton (stateless).
_CLAUDE_CODE = ClaudeCodeDialect()

#: The built-in dialects, by name — just the unshadowable default. Every other host
#: renderer is a `dos.hook_dialects` plugin (the kernel names no other vendor as code).
BUILTIN_DIALECTS: dict[str, HookDialect] = {
    _CLAUDE_CODE.name: _CLAUDE_CODE,
}

#: The default — the host DOS has spoken since the hooks shipped.
DEFAULT_DIALECT = "claude-code"


def resolve_dialect(name: Optional[str]) -> HookDialect:
    """Resolve a dialect by name. RAISES on an unknown name (fail-LOUD).

    `None`/empty → the default (`claude-code`). A built-in name → that renderer. A
    `dos.hook_dialects` entry-point name → the discovered plugin. An UNKNOWN name →
    `ValueError` (NEVER a silent CC fallback — a wrong dialect against a non-CC host
    is the no-op bug this seam prevents).
    """
    if not name:
        return BUILTIN_DIALECTS[DEFAULT_DIALECT]
    if name in BUILTIN_DIALECTS:
        return BUILTIN_DIALECTS[name]
    plugin = _load_plugin_dialect(name)
    if plugin is not None:
        return plugin
    known = ", ".join(sorted(BUILTIN_DIALECTS))
    raise ValueError(
        f"unknown hook dialect {name!r} — known: {known} "
        f"(or a registered dos.hook_dialects plugin). Refusing to fall back to "
        f"{DEFAULT_DIALECT!r}: a wrong dialect against a non-Claude-Code host is a "
        f"silent no-op."
    )


def available_dialects() -> list[str]:
    """The names a host may pass to `--dialect` — built-ins + discovered plugins."""
    names = set(BUILTIN_DIALECTS)
    try:
        names.update(_plugin_dialect_names())
    except Exception:
        pass
    return sorted(names)


# ---------------------------------------------------------------------------
# Plugin discovery (boundary I/O — at resolve time, never inside render). The
# `dos.hook_dialects` entry-point group, the same mechanism as dos.judges /
# dos.overlap_policies. Kept defensive: a broken plugin never breaks resolution of
# a built-in.
# ---------------------------------------------------------------------------
def _iter_entry_points():
    try:
        from importlib.metadata import entry_points
    except Exception:  # pragma: no cover - very old Python
        return []
    try:
        eps = entry_points()
        # Python 3.10+ selectable API, with a 3.9 dict fallback.
        if hasattr(eps, "select"):
            return list(eps.select(group="dos.hook_dialects"))
        return list(eps.get("dos.hook_dialects", []))  # type: ignore[attr-defined]
    except Exception:
        return []


def _plugin_dialect_names() -> list[str]:
    return [ep.name for ep in _iter_entry_points()]


def _load_plugin_dialect(name: str) -> Optional[HookDialect]:
    for ep in _iter_entry_points():
        if ep.name != name:
            continue
        try:
            obj = ep.load()
            inst = obj() if isinstance(obj, type) else obj
        except Exception:
            return None
        # Duck-typed: must have a callable render + a name (the Protocol surface).
        if hasattr(inst, "render") and callable(inst.render):
            return inst  # type: ignore[return-value]
        return None
    return None
