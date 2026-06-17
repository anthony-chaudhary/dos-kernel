"""docs/370 Phase B — `HookAction.ALLOW` + the `assist` posture (the verified allowlist).

Phase 1 gave DOS the `ASK` action + the `Posture` seam: a refusal could ESCALATE to
the human (gate) or merely record (observe). But in a permission-ON deployment the
human is prompted CONSTANTLY, and DOS holds facts they do not — this read touches
nothing, this command only reads. Phase B adds the positive dual of a refusal: when
DOS can POSITIVELY prove a call carries no effect on shared state, the `assist`
posture auto-ALLOWS it, so the human is prompted only for the genuinely uncertain
ones. This suite pins the contract:

  * **ASSIST = GATE for a refusal.** A PRE deny under `assist` escalates to an `ask`
    identically to `gate`; a non-PRE deny stays a deny (no prompt seam to escalate).
  * **ALLOW is strictly OPT-IN.** `under_posture` honors an auto-allow ONLY under
    `assist`; under block/observe/gate an ALLOW degrades to PASS (DOS does not
    auto-approve — the host's own flow keeps asking the human).
  * **ALLOW never blocks.** It renders to the host's `allow` verb (CC's
    `permissionDecision: "allow"`, verified `z.enum(['allow','deny','ask'])`) and
    carries NO hard-deny signal on any dialect (the fail-open floor's ALLOW dual).
  * **`verified_safe` is refuse-NARROW.** It returns True ONLY for a call DOS can
    certify free of effect (a local read, an effect-free command); a write, an
    egressing command, an Agent spawn, or an unknown tool returns False — an
    auto-allow requires an EXPLICIT proof, never the mere absence of a deny.
  * **End-to-end:** a Read / `git log` under `assist` emits `permissionDecision:
    "allow"`; a Write / `gh issue create` stays a silent passthrough (the host asks);
    a real deny escalates to `ask`.
"""

from __future__ import annotations

import io
import json

import pytest

from dos import hook_dialect as hd
from dos import pretool_sensor as prt


# ---------------------------------------------------------------------------
# parse_posture / the Posture enum — `assist` is a first-class posture.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["assist", "ASSIST", "  Assist  "])
def test_parse_posture_reads_assist(name):
    assert hd.parse_posture(name) is hd.Posture.ASSIST


def test_assist_is_a_distinct_posture_member():
    assert hd.Posture.ASSIST.value == "assist"
    assert hd.Posture.ASSIST not in (hd.Posture.BLOCK, hd.Posture.GATE, hd.Posture.OBSERVE)


# ---------------------------------------------------------------------------
# under_posture — ASSIST escalates a refusal like GATE; ALLOW is opt-in.
# ---------------------------------------------------------------------------
def _deny(moment=hd.HookMoment.PRE, context=""):
    return hd.HookVerdict(moment=moment, action=hd.HookAction.DENY,
                          reason="blocked by DOS", context=context)


def test_assist_turns_a_pre_deny_into_an_ask_like_gate():
    out = hd.under_posture(_deny(context="read it first"), hd.Posture.ASSIST)
    assert out.action is hd.HookAction.ASK
    assert out.reason == "blocked by DOS"
    assert out.context == "read it first"
    # Identical surface to GATE for a refusal — assist only ADDS the auto-allow.
    assert out == hd.under_posture(_deny(context="read it first"), hd.Posture.GATE)


@pytest.mark.parametrize("moment", [hd.HookMoment.POST, hd.HookMoment.STOP, hd.HookMoment.SESSION])
def test_assist_leaves_a_non_pre_deny_a_deny(moment):
    """Like gate: 'ask the human' is a PRE-only seam — a Stop/Post refusal stays a
    block under assist (never a fabricated ask)."""
    assert hd.under_posture(_deny(moment=moment), hd.Posture.ASSIST).action is hd.HookAction.DENY


def _allow(moment=hd.HookMoment.PRE):
    return hd.HookVerdict(moment=moment, action=hd.HookAction.ALLOW, reason="verified safe")


def test_allow_survives_assist_unchanged():
    v = _allow()
    assert hd.under_posture(v, hd.Posture.ASSIST) is v


@pytest.mark.parametrize("posture", [hd.Posture.BLOCK, hd.Posture.OBSERVE, hd.Posture.GATE])
def test_allow_degrades_to_pass_outside_assist(posture):
    """Strictly opt-in: ONLY `assist` may auto-approve. Under every other posture an
    ALLOW degrades to PASS (emit nothing) so the host's own flow asks the human — no
    posture but `assist` can ever make DOS an approver."""
    out = hd.under_posture(_allow(), posture)
    assert out.action is hd.HookAction.PASS


@pytest.mark.parametrize("posture", list(hd.Posture))
@pytest.mark.parametrize("action", [hd.HookAction.PASS, hd.HookAction.WARN, hd.HookAction.ASK])
def test_posture_never_hardens_a_pass_warn_or_ask(posture, action):
    """A posture only re-shapes a DENY (softer) or gates an ALLOW (to assist). A
    PASS/WARN/ASK is untouched under every posture — a posture never invents a refusal."""
    v = hd.HookVerdict(moment=hd.HookMoment.PRE, action=action, context="ctx")
    assert hd.under_posture(v, posture) is v


# ---------------------------------------------------------------------------
# ClaudeCodeDialect — native ALLOW render + parse_cc round-trip + fail-open.
# ---------------------------------------------------------------------------
def test_claude_code_renders_allow_as_permission_decision_allow():
    v = hd.HookVerdict(moment=hd.HookMoment.PRE, action=hd.HookAction.ALLOW,
                       reason="verified safe", context="")
    out = hd.resolve_dialect("claude-code").render(v)
    assert out == {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": "verified safe",
    }}


def test_parse_cc_reads_allow_back_to_the_allow_action():
    cc = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow",
        "permissionDecisionReason": "verified safe"}}
    v = hd.parse_cc(cc, moment=hd.HookMoment.PRE)
    assert v.action is hd.HookAction.ALLOW
    assert v.reason == "verified safe"


def test_allow_round_trips_through_the_cc_dialect():
    cc = hd.resolve_dialect("claude-code").render(
        hd.HookVerdict(moment=hd.HookMoment.PRE, action=hd.HookAction.ALLOW,
                       reason="r", context="c"))
    back = hd.resolve_dialect("claude-code").render(hd.parse_cc(cc, moment=hd.HookMoment.PRE))
    assert back == cc


def test_allow_is_not_a_deny_blocking_signal():
    """An ALLOW must NOT carry a hard-deny signal — it approves, it never blocks
    (the fail-open floor's ALLOW dual, mirroring the ASK one)."""
    out = hd.resolve_dialect("claude-code").render(
        hd.HookVerdict(moment=hd.HookMoment.PRE, action=hd.HookAction.ALLOW, reason="r"))
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert out["hookSpecificOutput"]["permissionDecision"] != "deny"


# ---------------------------------------------------------------------------
# verified_safe — the refuse-NARROW positive-safety predicate.
# ---------------------------------------------------------------------------
def _ev(tool_name, **tool_input):
    return {"tool_name": tool_name, "tool_input": tool_input}


@pytest.mark.parametrize("tool", ["Read", "Grep", "Glob", "LS", "NotebookRead"])
def test_verified_safe_true_for_local_read_tools(tool):
    assert prt.verified_safe(_ev(tool, file_path="src/x.py")) is True


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
def test_verified_safe_false_for_write_tools(tool):
    assert prt.verified_safe(_ev(tool, file_path="src/x.py", content="x")) is False


@pytest.mark.parametrize("tool", ["WebFetch", "WebSearch"])
def test_verified_safe_false_for_network_reads(tool):
    """A read that reaches the network is NOT effect-free — it can egress. So even a
    read-only tool is auto-allowed only when it touches no network (refuse-narrow)."""
    assert prt.verified_safe(_ev(tool, url="http://example.com")) is False


@pytest.mark.parametrize("tool", ["Agent", "Task", "TaskCreate", "TaskUpdate", "ToolSearch"])
def test_verified_safe_false_for_spawn_and_coordination_tools(tool):
    """No-FILE-footprint orchestration tools still carry a SPAWN/COORDINATION effect
    the file model can't see (#202) — never auto-allowed."""
    assert prt.verified_safe(_ev(tool, prompt="go")) is False


def test_verified_safe_false_for_unknown_tool_and_malformed_event():
    assert prt.verified_safe(_ev("SomeMcpWriteTool", x=1)) is False
    assert prt.verified_safe({"tool_name": ""}) is False
    assert prt.verified_safe({}) is False
    assert prt.verified_safe(None) is False  # type: ignore[arg-type]


@pytest.mark.parametrize("cmd", [
    "git log",
    "git diff HEAD~1",
    "grep -n foo src/dos/cli.py",
    "cat README.md | grep dos",
    "ls -la && git status",
    "rg pattern && wc -l file",
])
def test_verified_safe_true_for_effect_free_bash(cmd):
    assert prt.verified_safe(_ev("Bash", command=cmd)) is True


@pytest.mark.parametrize("cmd", [
    "gh issue create --title x",       # network egress (mutates GitHub)
    "git ls-remote origin",            # network egress (queries a remote)
    "curl http://example.com",         # network egress
    "echo hi > src/dos/arbiter.py",    # a shell write metacharacter
    "git log > out.txt",               # redirect writes a file
    "rm -rf build",                    # an unrecognized (unsafe) program
    "git push origin main",            # not in the effect-free set
    "cat a && rm b",                   # one segment is unsafe → the whole command is
])
def test_verified_safe_false_for_effectful_or_unknown_bash(cmd):
    assert prt.verified_safe(_ev("Bash", command=cmd)) is False


def test_verified_safe_false_for_bash_with_no_command():
    assert prt.verified_safe(_ev("Bash")) is False
    assert prt.verified_safe({"tool_name": "Bash", "tool_input": {"command": "  "}}) is False


# ---------------------------------------------------------------------------
# End-to-end through cmd_hook_pretool — the boundary auto-allow under `assist`.
# ---------------------------------------------------------------------------
def _run_pretool(monkeypatch, tmp_path, event, *, posture=None):
    from dos import cli
    # Pin the Python path: no bundled native binary in a dev checkout, but be hermetic.
    monkeypatch.setattr("dos.hook_binary.try_native_hook", lambda *a, **k: None)
    monkeypatch.setenv("DISPATCH_WORKSPACE", str(tmp_path))
    if posture is None:
        monkeypatch.delenv("DOS_HOOK_POSTURE", raising=False)
    else:
        monkeypatch.setenv("DOS_HOOK_POSTURE", posture)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    args = cli.argparse.Namespace(
        workspace=None, driver=None, job=False, handler="observe", debug=False)
    rc = cli.cmd_hook_pretool(args)
    return buf.getvalue(), rc


def test_e2e_read_under_assist_is_auto_allowed(monkeypatch, tmp_path):
    event = {"tool_name": "Read", "session_id": "S1",
             "tool_input": {"file_path": "README.md"}, "cwd": str(tmp_path)}
    out, rc = _run_pretool(monkeypatch, tmp_path, event, posture="assist")
    assert rc == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_e2e_effect_free_bash_under_assist_is_auto_allowed(monkeypatch, tmp_path):
    event = {"tool_name": "Bash", "session_id": "S1",
             "tool_input": {"command": "git log --oneline -5"}, "cwd": str(tmp_path)}
    out, rc = _run_pretool(monkeypatch, tmp_path, event, posture="assist")
    assert rc == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_e2e_write_under_assist_stays_a_silent_passthrough(monkeypatch, tmp_path):
    """Refuse-narrow: a write is not provably effect-free, so DOS emits NOTHING and the
    host's own permission flow asks the human (it is not auto-allowed)."""
    event = {"tool_name": "Write", "session_id": "S1",
             "tool_input": {"file_path": "notes.txt", "content": "x"}, "cwd": str(tmp_path)}
    out, rc = _run_pretool(monkeypatch, tmp_path, event, posture="assist")
    assert rc == 0
    assert out.strip() == ""  # no permissionDecision — the host decides


def test_e2e_egressing_bash_under_assist_stays_a_silent_passthrough(monkeypatch, tmp_path):
    event = {"tool_name": "Bash", "session_id": "S1",
             "tool_input": {"command": "gh issue list"}, "cwd": str(tmp_path)}
    out, rc = _run_pretool(monkeypatch, tmp_path, event, posture="assist")
    assert rc == 0
    assert out.strip() == ""


def test_e2e_read_under_block_default_is_not_auto_allowed(monkeypatch, tmp_path):
    """The parity floor: a read under the BLOCK default emits nothing — auto-allow is
    strictly opt-in to `assist`, so the default deployment is byte-for-byte unchanged."""
    event = {"tool_name": "Read", "session_id": "S1",
             "tool_input": {"file_path": "README.md"}, "cwd": str(tmp_path)}
    out, rc = _run_pretool(monkeypatch, tmp_path, event, posture=None)
    assert rc == 0
    assert out.strip() == ""


def test_e2e_real_deny_under_assist_escalates_to_ask(monkeypatch, tmp_path):
    """A real refusal under `assist` escalates to the human (an `ask`) exactly like
    `gate` — assist only ADDS the auto-allow for the proven-safe calls."""
    cc = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": "blocked by DOS (SELF_MODIFY)"}}
    outcome = {"decision": "deny", "rung": "admission",
               "reason_class": "SELF_MODIFY", "tree_known": True}
    monkeypatch.setattr("dos.pretool_sensor.decide",
                        lambda event, cfg, handler_name="observe": (cc, outcome))
    event = {"tool_name": "Write", "session_id": "S1",
             "tool_input": {"file_path": "src/dos/arbiter.py", "content": "x"}, "cwd": str(tmp_path)}
    out, rc = _run_pretool(monkeypatch, tmp_path, event, posture="assist")
    assert rc == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_e2e_auto_allow_records_posture_and_surface(monkeypatch, tmp_path):
    """Phase C consistency: an auto-allow's kernel verdict is still a passthrough (no
    refusal), but the operator's SURFACE is `allow` — recorded so a fold can count how
    often DOS cleared a call for the human."""
    from dos import config as _config
    from dos import hook_observation as hobs
    event = {"tool_name": "Read", "session_id": "S1",
             "tool_input": {"file_path": "README.md"}, "cwd": str(tmp_path)}
    _run_pretool(monkeypatch, tmp_path, event, posture="assist")
    recs = [r for r in hobs.read_observations(cfg=_config.default_config(tmp_path))
            if r.get("verb") == "pretool"]
    assert recs, "no pretool observation recorded"
    rec = recs[-1]
    assert rec["outcome"] == "passthrough"   # the kernel found no objection
    assert rec["posture"] == "assist"
    assert rec["surface"] == "allow"          # but the host was told to allow


# ---------------------------------------------------------------------------
# Per-host ALLOW degrade — never a hard block on any dialect (the fail-open floor).
# (Skips if the dos.hook_dialects driver entry points aren't registered.)
# ---------------------------------------------------------------------------
_DRIVER_DIALECTS = ("codex", "gemini", "cursor", "antigravity", "hermes", "claude-cowork")
_drivers_present = all(name in hd.available_dialects() for name in _DRIVER_DIALECTS)
_needs_drivers = pytest.mark.skipif(
    not _drivers_present,
    reason="dos.hook_dialects driver entry points not registered (run `pip install -e .`)",
)


def _blocking_signal(out) -> str:
    """The host-honored HARD-DENY signal in a rendered envelope, or "" if none."""
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


@_needs_drivers
@pytest.mark.parametrize("name", ["codex", "claude-cowork"])
def test_cc_family_renders_allow_natively(name):
    """Codex / Claude-Cowork run the CC harness, so an ALLOW renders identically to
    the CC dialect's native `permissionDecision: "allow"`."""
    v = hd.HookVerdict(moment=hd.HookMoment.PRE, action=hd.HookAction.ALLOW, reason="ok")
    assert hd.resolve_dialect(name).render(v) == hd.resolve_dialect("claude-code").render(v)
    assert hd.resolve_dialect(name).render(v)["hookSpecificOutput"]["permissionDecision"] == "allow"


@_needs_drivers
@pytest.mark.parametrize("name", _DRIVER_DIALECTS)
def test_allow_never_hard_blocks_on_any_dialect(name):
    """An ALLOW must carry NO hard-deny signal on any host — worst case it degrades to
    a non-blocking surface and the host asks the human, never a block (the GATE/assist
    invariant: surfacing a proven-safe call must never withhold it)."""
    v = hd.HookVerdict(moment=hd.HookMoment.PRE, action=hd.HookAction.ALLOW, reason="ok")
    out = hd.resolve_dialect(name).render(v)
    assert _blocking_signal(out) == "", f"ALLOW on {name!r} rendered a HARD-DENY {out!r}"
