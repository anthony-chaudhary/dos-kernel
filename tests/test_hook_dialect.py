"""Tests for `dos.hook_dialect` — the cross-vendor hook-envelope renderer (docs/217).

The Phase-1 contract, pinned:

  * **The CC dialect is byte-for-byte today.** `render(parse_cc(d))` reproduces the
    Claude-Code dict `decide()`/`warn_payload` already emit, for deny/warn/pass — so
    `--dialect claude-code` (the default) is unchanged behavior. (The 67-test hook
    suite is the other half of this gate; here we pin the round-trip directly.)
  * **Golden bytes per host.** A fixed verdict renders to the EXACT envelope each
    runtime honors — and the envelope is MOMENT-dependent for Gemini: a PRE
    (BeforeTool) deny stops the tool via `{"continue": false, …}` (the field Gemini's
    `shouldStopExecution()` checks; `decision` is ignored there — docs/268), while a
    STOP (AfterAgent) deny blocks via `{"decision":"block"}`. Codex is CC-identical;
    Cursor is `{"permission":"deny"}`.
  * **A wrong dialect fails LOUD** (raises) — never a silent Claude-Code fallback.
  * **No dialect mints a tool-input rewrite** (`updatedInput`/`updated_input`) — the
    docs/191 §4 byte-author floor, enforced across all four hosts.
  * **PASS emits nothing** on every dialect.
"""

from __future__ import annotations

import json

import pytest

from dos import hook_dialect as hd
from dos import pretool_sensor as prt

# The per-vendor renderers (codex/gemini/cursor) live in `dos.drivers.hook_dialects`
# and resolve through the `dos.hook_dialects` entry-point group (docs/217 — "the
# envelope is a driver"). That registration is created by `pip install -e .` and is
# what CI runs against. If the installed metadata is STALE (e.g. the package was not
# reinstalled after the entry points were added), `resolve_dialect("gemini")` would
# raise a bare ValueError — so we verify discovery once here and SKIP with a precise
# remediation rather than failing every dialect test cryptically. (The kernel's own
# `claude-code` default needs no plugin and is always present.)
_DRIVER_DIALECTS = ("codex", "gemini", "cursor", "antigravity", "hermes", "claude-cowork")
if any(name not in hd.available_dialects() for name in _DRIVER_DIALECTS):
    pytest.skip(
        "the dos.hook_dialects driver entry points are not registered "
        "(run `pip install -e .` to register codex/gemini/cursor/antigravity/hermes/"
        "claude-cowork — docs/217/278/298)",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Round-trip floor — the CC dialect reproduces the sensor's own bytes.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cc_dict", [
    prt.deny_payload("a structural refusal"),
    prt.deny_payload("an id was minted", additional_context='{"corrective":"read first"}'),
    prt.warn_payload("scope this to a lane"),
    None,
])
def test_claude_code_dialect_round_trips_to_the_sensor_bytes(cc_dict):
    """`render(parse_cc(d)) == d` for every shape decide()/warn_payload produce.

    This is the docs/217 Phase-1 invariant: routing through the dialect seam with the
    default `claude-code` renderer changes NOTHING about the emitted bytes.
    """
    verdict = hd.parse_cc(cc_dict, moment=hd.HookMoment.PRE)
    back = hd.resolve_dialect("claude-code").render(verdict)
    assert back == cc_dict


def test_claude_code_post_moment_uses_post_event_name():
    """A POST-moment warn renders the PostToolUse event name (not PreToolUse)."""
    v = hd.parse_cc(prt.warn_payload("repeated value"), moment=hd.HookMoment.POST)
    out = hd.resolve_dialect("claude-code").render(v)
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


# ---------------------------------------------------------------------------
# Golden bytes per host — a fixed DENY verdict in each runtime's grammar.
# ---------------------------------------------------------------------------
def _deny_verdict(context: str = "") -> hd.HookVerdict:
    return hd.HookVerdict(
        moment=hd.HookMoment.PRE, action=hd.HookAction.DENY,
        reason="blocked by DOS", context=context,
    )


def test_gemini_pre_deny_stops_the_tool_via_continue_false():
    """A BeforeTool (PRE) deny must emit {"continue": false, …}, NOT {"decision":"deny"}.

    docs/268: Gemini 0.45.x gates the tool-execution path on `shouldStopExecution()`
    (`return this.continue === false`), which does NOT consult `decision`. A
    `{"decision":"deny"}` PRE-render was a silent fail-open — the tool ran anyway. The
    refusal's why rides `stopReason` (what `getEffectiveReason()` surfaces)."""
    out = hd.resolve_dialect("gemini").render(_deny_verdict())
    assert out == {"continue": False, "stopReason": "blocked by DOS"}


def test_gemini_pre_deny_surfaces_context_as_additional_context():
    out = hd.resolve_dialect("gemini").render(_deny_verdict(context="read it first"))
    assert out == {
        "continue": False, "stopReason": "blocked by DOS",
        "hookSpecificOutput": {"additionalContext": "read it first"},
    }


def test_gemini_stop_deny_blocks_via_decision_block():
    """An AfterAgent (non-PRE) refusal — REFUSE TO STOP — IS gated on the decision
    field (`isBlockingDecision()`: decision ∈ {block, deny}), so the stop moment emits
    {"decision": "block", "reason": …} (docs/268). This is the moment where a
    top-level decision key is honored — the PRE moment above is not."""
    v = hd.HookVerdict(
        moment=hd.HookMoment.POST, action=hd.HookAction.DENY, reason="not done yet")
    assert hd.resolve_dialect("gemini").render(v) == {
        "decision": "block", "reason": "not done yet"}


def test_codex_deny_is_claude_code_identical():
    """Codex copied CC's envelope — the deny bytes must match the CC dialect exactly."""
    v = _deny_verdict(context="read it first")
    assert hd.resolve_dialect("codex").render(v) == hd.resolve_dialect("claude-code").render(v)


def test_claude_cowork_render_is_claude_code_identical():
    """Claude Cowork RUNS the Claude Code harness (docs/298) — its envelope is not
    'like' CC's, it IS CC's. The dialect delegates, so every (moment, action) must
    render byte-identical to the CC dialect — deny, warn, and pass alike."""
    cw = hd.resolve_dialect("claude-cowork")
    cc = hd.resolve_dialect("claude-code")
    for moment in (hd.HookMoment.PRE, hd.HookMoment.POST, hd.HookMoment.STOP):
        for v in (
            hd.HookVerdict(moment=moment, action=hd.HookAction.DENY,
                           reason="blocked by DOS", context="read it first"),
            hd.HookVerdict(moment=moment, action=hd.HookAction.WARN, context="scope it"),
            hd.HookVerdict(moment=moment, action=hd.HookAction.PASS),
        ):
            assert cw.render(v) == cc.render(v), (moment, v.action)


def test_antigravity_deny_is_top_level_decision():
    """Antigravity reads a top-level {"decision":"deny"} (Gemini-shaped output), even
    though its hook CONFIG file is Claude-Code-shaped (group-wrapped). The renderer
    side is what this pins; the config side is in test_init_hooks_crossvendor.py."""
    out = hd.resolve_dialect("antigravity").render(_deny_verdict())
    assert out == {"decision": "deny", "reason": "blocked by DOS"}


def test_antigravity_deny_folds_context_into_reason():
    """Antigravity documents only `decision`/`reason` — no separate context channel —
    so the corrective FACT rides the reason field (space-joined), never a new key
    the host wouldn't parse (and never a tool-input rewrite — the byte-author floor)."""
    out = hd.resolve_dialect("antigravity").render(_deny_verdict(context="read it first"))
    assert out == {"decision": "deny", "reason": "blocked by DOS read it first"}


def test_antigravity_warn_is_bare_reason_no_decision():
    """A WARN (turn-preserving) emits a bare {"reason":…} with NO decision key — inert
    to the allow/deny gate, so it re-surfaces context without withholding the call."""
    v = hd.HookVerdict(moment=hd.HookMoment.PRE, action=hd.HookAction.WARN, context="scope it")
    out = hd.resolve_dialect("antigravity").render(v)
    assert out == {"reason": "scope it"}


def test_cursor_deny_is_permission_deny():
    out = hd.resolve_dialect("cursor").render(_deny_verdict())
    assert out == {"permission": "deny", "agent_message": "blocked by DOS"}


def test_cursor_warn_is_allow_with_message():
    """Cursor has no pass-but-add-context that isn't an allow → allow + agent_message."""
    v = hd.HookVerdict(moment=hd.HookMoment.PRE, action=hd.HookAction.WARN, context="scope it")
    out = hd.resolve_dialect("cursor").render(v)
    assert out == {"permission": "allow", "agent_message": "scope it"}


# ---------------------------------------------------------------------------
# Hermes Agent (Nous Research) — the `pre_tool_call` SHELL hook (docs/278).
# Verified against the Hermes hooks doc 2026-06-09: a hook BLOCKS by printing
# {"decision":"block","reason":…} on stdout; ALLOW is {} (or non-matching output).
# ---------------------------------------------------------------------------
def test_hermes_deny_is_decision_block():
    """A Hermes DENY renders the canonical block shape its shell hook honors."""
    out = hd.resolve_dialect("hermes").render(_deny_verdict())
    assert out == {"decision": "block", "reason": "blocked by DOS"}


def test_hermes_deny_appends_context_to_reason():
    """The corrective fact rides Hermes' one operator field (`reason`) — a fact to
    read, never a rewritten arg (docs/191 §4) — space-joined after the reason."""
    out = hd.resolve_dialect("hermes").render(_deny_verdict(context="read it first"))
    assert out == {"decision": "block", "reason": "blocked by DOS read it first"}


def test_hermes_warn_is_the_allow_object_context_dropped():
    """Hermes' shell hook has NO non-blocking context channel, so a turn-preserving
    WARN can only PASS — it renders the ALLOW object {} and the context is dropped.
    This is a Hermes coverage limit surfaced honestly (not smuggled onto an unread
    field), and it MUST NOT carry any blocking signal."""
    v = hd.HookVerdict(moment=hd.HookMoment.PRE, action=hd.HookAction.WARN, context="scope it")
    out = hd.resolve_dialect("hermes").render(v)
    assert out == {}
    assert _blocking_signal(out) == "", "a WARN must not block"


def test_hermes_pass_emits_nothing():
    v = hd.HookVerdict(moment=hd.HookMoment.PRE, action=hd.HookAction.PASS)
    assert hd.resolve_dialect("hermes").render(v) is None


def test_hermes_deny_is_moment_agnostic():
    """Unlike Gemini (whose PRE/STOP deny fields differ), Hermes reads the same
    `decision` field at every moment — so the block bytes do not vary by moment."""
    pre = hd.resolve_dialect("hermes").render(
        hd.HookVerdict(moment=hd.HookMoment.PRE, action=hd.HookAction.DENY, reason="x"))
    stop = hd.resolve_dialect("hermes").render(
        hd.HookVerdict(moment=hd.HookMoment.STOP, action=hd.HookAction.DENY, reason="x"))
    assert pre == stop == {"decision": "block", "reason": "x"}


# ---------------------------------------------------------------------------
# THE FAIL-OPEN FLOOR — the structural guard (docs/268 lesson).
#
# A correctly-computed DENY is worthless if it renders to a shape the host's gate
# IGNORES. That fail-open shipped TWICE for Gemini: a PRE deny rendered
# {"decision":"deny"} (the tool gate reads `continue`, not `decision` → tool ran), and
# the stop verb rendered the PRE shape at the STOP moment (AfterAgent reads `decision`
# → ignored `continue`). Each was a hand-written single-case test that could slip
# independently. This is the EXHAUSTIVE matrix: for EVERY (dialect, moment), a DENY
# must carry a blocking signal the host honors AT THAT MOMENT. A new dialect, or a
# regression to a host-ignored shape, fails here — you cannot merge a fail-open.
# See feedback-fail-open-shape-and-sacrificial-probe-law (the law this enforces).
# ---------------------------------------------------------------------------

def _blocking_signal(out: dict | None) -> str:
    """The host-honored blocking signal in a rendered envelope, or "" if NONE (the
    fail-open). Recognizes every gate DOS speaks: CC/Codex nested
    `permissionDecision:deny`; Gemini's BeforeTool `continue:false`; the
    `decision` gate (`deny`|`block`) Gemini-stop/Antigravity use; Cursor's
    `permission:deny`. "" means the host would find no refusal and PROCEED."""
    if not isinstance(out, dict):
        return ""
    if out.get("continue") is False:
        return "continue:false"
    inner = out.get("hookSpecificOutput")
    if isinstance(inner, dict) and inner.get("permissionDecision") == "deny":
        return "permissionDecision:deny"
    if out.get("decision") in ("deny", "block"):
        return f"decision:{out['decision']}"
    if out.get("permission") == "deny":
        return "permission:deny"
    return ""


@pytest.mark.parametrize("name", ["claude-code", "codex", "gemini", "cursor", "antigravity", "hermes", "claude-cowork"])
@pytest.mark.parametrize("moment", [hd.HookMoment.PRE, hd.HookMoment.POST, hd.HookMoment.STOP])
def test_deny_carries_a_blocking_signal_for_every_dialect_and_moment(name, moment):
    """No (dialect, moment) DENY may render to a shape with no blocking signal — that
    is the silent fail-open. This is the floor that makes the class un-mergeable."""
    v = hd.HookVerdict(moment=moment, action=hd.HookAction.DENY, reason="blocked by DOS")
    out = hd.resolve_dialect(name).render(v)
    sig = _blocking_signal(out)
    assert sig, (
        f"FAIL-OPEN: dialect {name!r} at moment {moment.name} rendered {out!r}, which "
        f"carries NO signal the host's gate honors — the agent would PROCEED despite "
        f"the deny. Render a blocking shape the host acts on at this moment.")


def test_gemini_deny_moment_split_is_the_documented_one():
    """Pin the load-bearing Gemini specifics (the docs/268 fix), since 'has SOME
    blocking signal' above would accept the wrong one: BeforeTool (PRE) MUST be
    `continue:false` (the tool gate `shouldStopExecution()` reads), and the stop
    moments MUST be `decision:block` (the AfterAgent `isBlockingDecision()` gate).
    A PRE that emits `decision:*` or a STOP that emits `continue:false` is the exact
    fail-open that shipped — caught here even though both have *a* signal."""
    g = hd.resolve_dialect("gemini")
    pre = g.render(hd.HookVerdict(moment=hd.HookMoment.PRE, action=hd.HookAction.DENY, reason="r"))
    assert _blocking_signal(pre) == "continue:false", pre
    assert "decision" not in pre, f"PRE must NOT use the decision gate (AfterAgent-only): {pre}"
    for m in (hd.HookMoment.POST, hd.HookMoment.STOP):
        stop = g.render(hd.HookVerdict(moment=m, action=hd.HookAction.DENY, reason="r"))
        assert _blocking_signal(stop) == "decision:block", (m.name, stop)
        assert "continue" not in stop, f"{m.name} must NOT use the tool gate `continue`: {stop}"


# ---------------------------------------------------------------------------
# PASS emits nothing — on EVERY dialect.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["claude-code", "codex", "gemini", "cursor", "antigravity", "claude-cowork"])
def test_pass_renders_none_on_every_dialect(name):
    v = hd.HookVerdict(moment=hd.HookMoment.PRE, action=hd.HookAction.PASS)
    assert hd.resolve_dialect(name).render(v) is None


def test_parse_cc_none_and_garbage_are_pass():
    for bad in (None, {}, {"nope": 1}, {"hookSpecificOutput": "not a dict"}, 42, "x"):
        v = hd.parse_cc(bad, moment=hd.HookMoment.PRE)
        assert v.action is hd.HookAction.PASS


# ---------------------------------------------------------------------------
# Fail LOUD on an unknown dialect — never a silent Claude-Code fallback.
# ---------------------------------------------------------------------------
def test_unknown_dialect_raises_not_falls_back():
    with pytest.raises(ValueError) as ei:
        hd.resolve_dialect("claude")  # close but wrong
    msg = str(ei.value)
    assert "unknown hook dialect" in msg
    assert "claude-code" in msg  # the known list is surfaced
    assert "silent no-op" in msg  # explains WHY it refuses to guess


def test_empty_or_none_dialect_is_the_default():
    for empty in (None, ""):
        assert hd.resolve_dialect(empty) is hd.BUILTIN_DIALECTS[hd.DEFAULT_DIALECT]


def test_available_dialects_lists_every_host():
    names = hd.available_dialects()
    assert {"claude-code", "codex", "gemini", "cursor", "antigravity", "claude-cowork"} <= set(names)


def test_trae_dialect_is_a_deliberate_absence():
    """Trae is NOT a dialect, on purpose — there is no grammar to render (docs/294).

    Proved out 2026-06-10: Trae (ByteDance) has no hook system — no events, no
    deny/allow stdout JSON, no exit codes — so any TraeDialect envelope would be
    bytes nothing parses: the silent fail-open this seam's fail-LOUD resolver
    exists to prevent (the docs/278 OpenClaw/SwarmClaw precedent, one step
    further out). Trae's support is the advisory surface (MCP / rules / skills,
    docs/294 §3). If Trae ships hooks, follow the docs/269 playbook and delete
    this pin consciously — after re-running the docs/294 §1 probe."""
    assert "trae" not in hd.available_dialects()
    with pytest.raises(ValueError, match="unknown hook dialect"):
        hd.resolve_dialect("trae")


# ---------------------------------------------------------------------------
# The byte-author floor — NO dialect ever emits a tool-input rewrite key.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["claude-code", "codex", "gemini", "cursor", "claude-cowork"])
@pytest.mark.parametrize("action", [hd.HookAction.DENY, hd.HookAction.WARN])
def test_no_dialect_emits_a_rewrite_key(name, action):
    """docs/191 §4: a corrective is a fact to re-surface, never a rewritten argument.

    Cursor's preToolUse *can* return `updated_input`; DOS must NOT use it. Assert no
    rendered envelope (deny or warn, with a context payload) carries any input-rewrite
    key on any host.
    """
    v = hd.HookVerdict(
        moment=hd.HookMoment.PRE, action=action,
        reason="blocked", context='{"corrective":"resolve the id via a read"}',
    )
    out = hd.resolve_dialect(name).render(v)
    blob = json.dumps(out)
    assert "updatedInput" not in blob
    assert "updated_input" not in blob


# ---------------------------------------------------------------------------
# Provenance: a real SELF_MODIFY deny from decide() transcodes to each host.
# ---------------------------------------------------------------------------
def test_self_modify_deny_transcodes_to_every_host(tmp_path):
    """End-to-end: a real decide() SELF_MODIFY deny → the right bytes per host.

    Proves the seam consumes what `decide()` actually returns (not a hand-built dict),
    and that the deny crosses to all four runtimes.
    """
    import dataclasses
    from dos import config as _config

    # A kernel-repo config whose runtime-file LIST includes the target path, so
    # SELF_MODIFY fires deterministically regardless of what's under tmp_path (the
    # same construction the test_hook_pretool guard tests use — the is_kernel_repo
    # boolean alone is NOT enough; the guard matches the write path against this list).
    cfg = _config.default_config(tmp_path)
    facts = _config.WorkspaceFacts(
        root=tmp_path,
        kernel_runtime_files=("src/dos/arbiter.py", "src/dos/admission.py"),
        is_kernel_repo=True,
    )
    cfg = dataclasses.replace(cfg, workspace=facts)
    event = {
        "session_id": "t", "tool_name": "Write",
        "tool_input": {"file_path": "src/dos/arbiter.py", "content": "x"},
        "cwd": str(tmp_path),
    }
    cc_dict, outcome = prt.decide(event, cfg)
    assert outcome["decision"] == "deny"
    assert outcome["reason_class"] == "SELF_MODIFY"

    verdict = hd.parse_cc(cc_dict, moment=hd.HookMoment.PRE)
    assert verdict.action is hd.HookAction.DENY

    # Each host's deny grammar carries the refusal. A PRE (BeforeTool) deny stops the
    # tool via Gemini's `continue: false` gate, NOT a top-level `decision` (docs/268 —
    # the decision key is consulted only at the AfterAgent/stop moment).
    assert hd.resolve_dialect("gemini").render(verdict)["continue"] is False
    assert hd.resolve_dialect("cursor").render(verdict)["permission"] == "deny"
    cc = hd.resolve_dialect("claude-code").render(verdict)
    assert cc["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert hd.resolve_dialect("codex").render(verdict) == cc  # codex == cc bytes
