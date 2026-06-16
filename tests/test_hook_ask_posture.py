"""Tests for the enforcement POSTURE and the ASK action (docs/370).

DOS shipped assuming the headless / bypass-permissions deployment: the hook
vocabulary was DENY / WARN / PASS, so a refusal could only BLOCK or stay silent —
it could not ROUTE a call into a human's permission prompt. docs/370 adds the
`ASK` action and the `Posture` seam so DOS can *participate* in a host's permission
flow instead of replacing it. This suite pins:

  * **ASK renders to the host's "ask the human" grammar** — Claude Code's
    `permissionDecision: "ask"` (verified `z.enum(['allow','deny','ask'])`), and the
    CC-identical Codex / Claude-Cowork delegate to it. A host with no native ask
    grammar degrades ASK to a turn-preserving WARN (honest coverage, never a
    fabricated ask the host's gate ignores, never a hard block).
  * **The posture is monotone-softening.** `under_posture` only ever re-shapes a
    DENY into a softer surface (ASK or WARN); it never hardens a PASS/WARN/ASK, and
    never invents a refusal. BLOCK (the default) is byte-for-byte today's behavior
    (the parity floor).
  * **GATE escalates at PRE only.** "ask the human" is meaningful before a tool
    runs; a Stop/Post refusal under GATE stays a DENY (no permission-prompt seam to
    escalate into).
"""

from __future__ import annotations

import pytest

from dos import hook_dialect as hd
from dos import pretool_sensor as prt


# ---------------------------------------------------------------------------
# parse_posture — lenient, fail-safe to the BLOCK default.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("observe", hd.Posture.OBSERVE),
    ("block", hd.Posture.BLOCK),
    ("gate", hd.Posture.GATE),
    ("GATE", hd.Posture.GATE),
    ("  Gate  ", hd.Posture.GATE),
])
def test_parse_posture_known_names(name, expected):
    assert hd.parse_posture(name) is expected


@pytest.mark.parametrize("bad", [None, "", "  ", "gat", "yolo", "deny", "allow", "42"])
def test_parse_posture_unknown_falls_back_to_block(bad):
    """Unlike resolve_dialect (fail-LOUD), an unknown posture degrades to the SAFE,
    behavior-preserving BLOCK default — a typo can only make DOS stricter, never
    silently drop a gate."""
    assert hd.parse_posture(bad) is hd.Posture.BLOCK
    assert hd.DEFAULT_POSTURE is hd.Posture.BLOCK


# ---------------------------------------------------------------------------
# under_posture — the pure monotone-softening transform.
# ---------------------------------------------------------------------------
def _deny(moment=hd.HookMoment.PRE, context=""):
    return hd.HookVerdict(moment=moment, action=hd.HookAction.DENY,
                          reason="blocked by DOS", context=context)


def test_block_leaves_a_deny_unchanged():
    v = _deny()
    assert hd.under_posture(v, hd.Posture.BLOCK) == v


def test_gate_turns_a_pre_deny_into_an_ask():
    out = hd.under_posture(_deny(context="read it first"), hd.Posture.GATE)
    assert out.action is hd.HookAction.ASK
    assert out.reason == "blocked by DOS"
    assert out.context == "read it first"  # corrective preserved for the human


@pytest.mark.parametrize("moment", [hd.HookMoment.POST, hd.HookMoment.STOP, hd.HookMoment.SESSION])
def test_gate_leaves_a_non_pre_deny_a_deny(moment):
    """'Ask the human' is a PRE-only seam — a Stop/Post refusal has no permission
    prompt to escalate into, so GATE leaves it a block (never a fabricated ask)."""
    out = hd.under_posture(_deny(moment=moment), hd.Posture.GATE)
    assert out.action is hd.HookAction.DENY


def test_observe_turns_a_deny_into_a_warn_folding_reason_into_context():
    out = hd.under_posture(_deny(context="read it first"), hd.Posture.OBSERVE)
    assert out.action is hd.HookAction.WARN
    # The human still reads WHY — the reason is folded into the surviving context.
    assert "blocked by DOS" in out.context
    assert "read it first" in out.context


@pytest.mark.parametrize("posture", list(hd.Posture))
@pytest.mark.parametrize("action", [hd.HookAction.PASS, hd.HookAction.WARN, hd.HookAction.ASK])
def test_posture_never_hardens_a_non_deny(posture, action):
    """Monotone-softening: a posture only re-shapes a DENY. A PASS/WARN/ASK is
    untouched under every posture — a posture can never invent a refusal."""
    v = hd.HookVerdict(moment=hd.HookMoment.PRE, action=action, context="ctx")
    assert hd.under_posture(v, posture) is v


# ---------------------------------------------------------------------------
# ClaudeCodeDialect — native ASK render + parse_cc round-trip.
# ---------------------------------------------------------------------------
def test_claude_code_renders_ask_as_permission_decision_ask():
    v = hd.HookVerdict(moment=hd.HookMoment.PRE, action=hd.HookAction.ASK,
                       reason="escalated to you", context="read it first")
    out = hd.resolve_dialect("claude-code").render(v)
    assert out == {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": "escalated to you",
        "additionalContext": "read it first",
    }}


def test_parse_cc_reads_ask_back_to_the_ask_action():
    cc = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "ask",
        "permissionDecisionReason": "escalated"}}
    v = hd.parse_cc(cc, moment=hd.HookMoment.PRE)
    assert v.action is hd.HookAction.ASK
    assert v.reason == "escalated"


def test_ask_round_trips_through_the_cc_dialect():
    """render(parse_cc(d)) == d for an ask dict — the same Phase-1 round-trip floor
    the deny/warn cases satisfy, extended to the new action."""
    cc = hd.resolve_dialect("claude-code").render(
        hd.HookVerdict(moment=hd.HookMoment.PRE, action=hd.HookAction.ASK,
                       reason="r", context="c"))
    back = hd.resolve_dialect("claude-code").render(hd.parse_cc(cc, moment=hd.HookMoment.PRE))
    assert back == cc


def test_ask_is_not_a_deny_blocking_signal():
    """An ASK must NOT carry a hard-deny signal — it escalates, it does not block.
    (The fail-open floor in test_hook_dialect.py guards DENY; this is its ASK dual:
    an ask is a distinct, non-blocking permission verb.)"""
    out = hd.resolve_dialect("claude-code").render(
        hd.HookVerdict(moment=hd.HookMoment.PRE, action=hd.HookAction.ASK, reason="r"))
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert out["hookSpecificOutput"]["permissionDecision"] != "deny"


# ---------------------------------------------------------------------------
# End-to-end: a real decide() deny, transcoded under each posture.
# ---------------------------------------------------------------------------
def _self_modify_deny_dict(tmp_path, monkeypatch):
    """A real decide() SELF_MODIFY deny dict (the loop-session hard-deny path)."""
    import dataclasses
    from dos import config as _config
    monkeypatch.setenv("DOS_LOOP", "1")  # docs/355 — reach the hard deny, not the operator WARN
    cfg = _config.default_config(tmp_path)
    facts = _config.WorkspaceFacts(
        root=tmp_path,
        kernel_runtime_files=("src/dos/arbiter.py",),
        is_kernel_repo=True,
    )
    cfg = dataclasses.replace(cfg, workspace=facts)
    event = {
        "session_id": "t", "tool_name": "Write",
        "tool_input": {"file_path": "src/dos/arbiter.py", "content": "x"},
        "cwd": str(tmp_path),
    }
    cc_dict, outcome = prt.decide(event, cfg)
    assert outcome["decision"] == "deny" and outcome["reason_class"] == "SELF_MODIFY"
    return cc_dict


def test_real_deny_under_block_stays_a_deny(tmp_path, monkeypatch):
    cc = _self_modify_deny_dict(tmp_path, monkeypatch)
    v = hd.under_posture(hd.parse_cc(cc, moment=hd.HookMoment.PRE), hd.Posture.BLOCK)
    out = hd.resolve_dialect("claude-code").render(v)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_real_deny_under_gate_becomes_an_ask(tmp_path, monkeypatch):
    """The keystone: a hard SELF_MODIFY deny, under the control-oriented GATE posture,
    is handed to the human's permission prompt — same adjudicated reason, no block."""
    cc = _self_modify_deny_dict(tmp_path, monkeypatch)
    v = hd.under_posture(hd.parse_cc(cc, moment=hd.HookMoment.PRE), hd.Posture.GATE)
    out = hd.resolve_dialect("claude-code").render(v)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "SELF_MODIFY" in out["hookSpecificOutput"]["permissionDecisionReason"] \
        or "running" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_real_deny_under_observe_is_context_only_no_permission_decision(tmp_path, monkeypatch):
    cc = _self_modify_deny_dict(tmp_path, monkeypatch)
    v = hd.under_posture(hd.parse_cc(cc, moment=hd.HookMoment.PRE), hd.Posture.OBSERVE)
    out = hd.resolve_dialect("claude-code").render(v)
    hso = out["hookSpecificOutput"]
    assert "permissionDecision" not in hso  # record-only: never blocks, never prompts
    assert hso["additionalContext"]  # but the why is still surfaced


# ---------------------------------------------------------------------------
# _hook_posture — the boundary read (env precedence over config, default BLOCK).
# ---------------------------------------------------------------------------
def test_hook_posture_reads_env(tmp_path, monkeypatch):
    from dos import cli as _cli
    from dos import config as _config
    cfg = _config.default_config(tmp_path)
    monkeypatch.setenv("DOS_HOOK_POSTURE", "gate")
    assert _cli._hook_posture(cfg) is hd.Posture.GATE
    monkeypatch.setenv("DOS_HOOK_POSTURE", "observe")
    assert _cli._hook_posture(cfg) is hd.Posture.OBSERVE


def test_hook_posture_defaults_to_block_when_unset(tmp_path, monkeypatch):
    from dos import cli as _cli
    from dos import config as _config
    monkeypatch.delenv("DOS_HOOK_POSTURE", raising=False)
    cfg = _config.default_config(tmp_path)
    assert _cli._hook_posture(cfg) is hd.Posture.BLOCK


def test_hook_posture_reads_dos_toml_enforcement_section(tmp_path, monkeypatch):
    from dos import cli as _cli
    from dos import config as _config
    monkeypatch.delenv("DOS_HOOK_POSTURE", raising=False)
    (tmp_path / "dos.toml").write_text("[enforcement]\nposture = \"gate\"\n", encoding="utf-8")
    cfg = _config.default_config(tmp_path)
    assert _cli._hook_posture(cfg) is hd.Posture.GATE


def test_env_posture_overrides_config(tmp_path, monkeypatch):
    from dos import cli as _cli
    from dos import config as _config
    (tmp_path / "dos.toml").write_text("[enforcement]\nposture = \"observe\"\n", encoding="utf-8")
    monkeypatch.setenv("DOS_HOOK_POSTURE", "block")
    cfg = _config.default_config(tmp_path)
    assert _cli._hook_posture(cfg) is hd.Posture.BLOCK


# ---------------------------------------------------------------------------
# Per-host ASK rendering — native on the CC family, honest WARN-degrade elsewhere.
# (Skips if the dos.hook_dialects driver entry points aren't registered, mirroring
# test_hook_dialect.py — `pip install -e .` registers them.)
# ---------------------------------------------------------------------------
_DRIVER_DIALECTS = ("codex", "gemini", "cursor", "antigravity", "hermes", "claude-cowork")
_drivers_present = all(name in hd.available_dialects() for name in _DRIVER_DIALECTS)
_needs_drivers = pytest.mark.skipif(
    not _drivers_present,
    reason="dos.hook_dialects driver entry points not registered (run `pip install -e .`)",
)


def _blocking_signal(out) -> str:
    """The host-honored HARD-DENY signal in a rendered envelope, or "" if none.
    (Copied from test_hook_dialect.py — an ASK must carry NONE of these.)"""
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


def _ask_verdict():
    return hd.HookVerdict(moment=hd.HookMoment.PRE, action=hd.HookAction.ASK,
                          reason="escalated to you", context="")


@_needs_drivers
@pytest.mark.parametrize("name", ["codex", "claude-cowork"])
def test_cc_family_renders_ask_natively(name):
    """Codex and Claude-Cowork run/copy the CC harness, so an ASK renders identical
    to the CC dialect's native `permissionDecision: "ask"`."""
    v = _ask_verdict()
    assert hd.resolve_dialect(name).render(v) == hd.resolve_dialect("claude-code").render(v)
    assert hd.resolve_dialect(name).render(v)["hookSpecificOutput"]["permissionDecision"] == "ask"


@_needs_drivers
@pytest.mark.parametrize("name", ["gemini", "cursor", "antigravity", "hermes"])
def test_non_cc_hosts_degrade_ask_to_a_non_blocking_surface(name):
    """A host with no verified native ask grammar degrades ASK to a turn-preserving
    WARN surface: it MUST NOT hard-block (that would contradict the operator's GATE
    choice) and MUST NOT fabricate an ask the host's gate ignores. Native per-host
    ASK is the docs/370 roadmap follow-up; until each host's ask grammar is verified,
    the honest degrade is a non-blocking surface."""
    out = hd.resolve_dialect(name).render(_ask_verdict())
    assert _blocking_signal(out) == "", (
        f"ASK on {name!r} rendered a HARD-DENY signal {out!r} — GATE must not block")
