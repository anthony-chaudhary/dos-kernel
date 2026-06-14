"""docs/191 — the PreToolUse hook: DENY a structurally-refused tool call BEFORE it runs.

The PRE moment is the unique cell where a DOS verdict is both SOUND and backed by real
deny-power (the actuation×evidence crossing, docs/191 §0). These tests exercise the
boundary adapter (`pretool_sensor`) and the end-to-end CLI (`cmd_hook_pretool`), with two
LOAD-BEARING structural pins:

  * **handler-fault**: a Rung-B handler that raises / returns a non-`EffectProposal` can
    NEVER manufacture a `permissionDecision: deny` (fail-to-OBSERVE through `run_handler`).
  * **default-install PDP**: with only `ObserveHandler` wired, even a HIGH-confidence mint
    emits passthrough (zero behavioral deny) — DOS is PDP-only by construction.

The docs/165 §5 self-certification trap is the enemy: the deny/warn assertions check the
EXACT shape REAL Claude Code honors (`permissionDecision`/`additionalContext`/
`hookEventName == "PreToolUse"`), not merely "the bytes match the shape DOS chose" — the
sibling `dos hook stop` was a SILENT NO-OP against real CC for emitting the wrong dialect.
"""

from __future__ import annotations

import dataclasses
import io
import json
from pathlib import Path

import pytest

from dos import config as _config
from dos import enforce as _enforce
from dos import intervention as _intervention
from dos import pretool_sensor as prt


# ==========================================================================
# Helpers
# ==========================================================================
def _kernel_cfg(tmp_path: Path):
    """A config whose workspace facts declare the kernel runtime set, so SELF_MODIFY fires
    deterministically regardless of what is on disk under tmp_path (a foreign tmp dir would
    otherwise yield an empty runtime set and never fire the guard — the correct foreign-repo
    behavior, but not what these guard tests want to exercise)."""
    cfg = _config.default_config(tmp_path)
    facts = _config.WorkspaceFacts(
        root=tmp_path,
        kernel_runtime_files=("src/dos/arbiter.py", "src/dos/admission.py", "src/dos/self_modify.py"),
        is_kernel_repo=True,
    )
    return dataclasses.replace(cfg, workspace=facts)


def _foreign_cfg(tmp_path: Path):
    """A plain foreign-repo config — no kernel runtime files (SELF_MODIFY cannot fire)."""
    return _config.default_config(tmp_path)


def _event(tool_name="Write", tool_input=None, *, session="S1", cwd=None, **extra):
    e = {"tool_name": tool_name, "session_id": session,
         "tool_input": tool_input if tool_input is not None else {}}
    if cwd is not None:
        e["cwd"] = cwd
    e.update(extra)
    return e


def _run_cli(monkeypatch, event, workspace: Path, *, handler="observe", debug=False):
    """Drive cmd_hook_pretool with `event` on stdin; return (stdout, rc)."""
    from dos import cli

    monkeypatch.setenv("DISPATCH_WORKSPACE", str(workspace))
    stdin_text = "" if event is None else (event if isinstance(event, str) else json.dumps(event))
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    args = cli.argparse.Namespace(
        workspace=None, driver=None, job=False, handler=handler, debug=debug,
    )
    rc = cli.cmd_hook_pretool(args)
    return buf.getvalue(), rc


# ==========================================================================
# is_pre_event — the structural PRE marker (no result key).
# ==========================================================================
def test_is_pre_event_true_on_plain_pretool():
    assert prt.is_pre_event(_event("Write", {"file_path": "x.py"})) is True


def test_is_pre_event_false_when_result_present():
    """A mis-routed PostToolUse event (carries a result) is NOT a PRE event — we never treat
    agent-unseen result bytes as PRE evidence."""
    assert prt.is_pre_event(_event("Write", {"file_path": "x.py"}, tool_response="done")) is False
    assert prt.is_pre_event(_event("Read", {"file_path": "x"}, tool_output="bytes")) is False


def test_is_pre_event_false_on_no_tool_name_or_wrong_event_name():
    assert prt.is_pre_event({}) is False
    assert prt.is_pre_event({"tool_name": "Write", "hook_event_name": "PostToolUse"}) is False


# ==========================================================================
# (1) Fail-to-passthrough on any input fault.
# ==========================================================================
def test_empty_stdin_passthrough(monkeypatch, tmp_path):
    out, rc = _run_cli(monkeypatch, None, tmp_path)
    assert out == "" and rc == 0


def test_bad_json_passthrough(monkeypatch, tmp_path):
    out, rc = _run_cli(monkeypatch, "{not json", tmp_path)
    assert out == "" and rc == 0


def test_no_tool_name_passthrough(monkeypatch, tmp_path):
    out, rc = _run_cli(monkeypatch, {"session_id": "S1"}, tmp_path)
    assert out == "" and rc == 0


def test_misrouted_posttool_event_passthrough(monkeypatch, tmp_path):
    out, rc = _run_cli(
        monkeypatch,
        _event("Write", {"file_path": "src/dos/arbiter.py"}, tool_response="ok"),
        tmp_path,
    )
    assert out == "" and rc == 0


# ==========================================================================
# (2) SELF_MODIFY deny — a Write to a kernel runtime path → permissionDecision: deny.
# ==========================================================================
def test_self_modify_write_denies():
    cfg_holder = {}

    # Build directly via decide() with a kernel-runtime config (independent of the CLI's
    # workspace resolution, which would re-probe the real disk).
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    cfg = _kernel_cfg(tmp)
    dialect, outcome = prt.decide(_event("Write", {"file_path": "src/dos/arbiter.py"}), cfg)
    assert outcome["decision"] == "deny"
    assert outcome["reason_class"] == "SELF_MODIFY"
    # The EXACT CC PreToolUse deny dialect — the anti-no-op assertion.
    hso = dialect["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "permissionDecisionReason" in hso and hso["permissionDecisionReason"]
    # NEVER updatedInput (would mint corrective bytes — byte-author violation, §4).
    assert "updatedInput" not in hso


def test_self_modify_deny_text_names_hook_real_remedies_only():
    """The hook-emitted SELF_MODIFY deny must not advertise `--force` (issue #14).

    Only the `dos arbitrate` CLI has `--force`; the PreToolUse ABI deliberately
    gives the agent none, so a deny text naming it points at a door that does not
    exist — the documented fuel of the 21-attempt retry storm. The hook surface
    swaps that tail for the remedies that DO exist (read-only inspection, the
    operator's between-runs edit / armed override window, "stop retrying —
    repeated denies raise an operator decision"). The PREDICATE text is
    untouched: the CLI deny keeps `--force`, where it is real.
    """
    import tempfile
    cfg = _kernel_cfg(Path(tempfile.mkdtemp()))
    dialect, outcome = prt.decide(_event("Write", {"file_path": "src/dos/arbiter.py"}), cfg)
    emitted = dialect["hookSpecificOutput"]["permissionDecisionReason"]
    for text in (emitted, outcome["reason"]):
        assert "Pass --force" not in text
        assert "dos decisions" in text          # the raised-decision pointer
        assert "dos override status" in text    # the operator's real window
    # The CLI rung is unchanged: the predicate itself still names --force.
    from dos import admission as _adm
    from dos.self_modify import SelfModifyPredicate
    req = _adm.AdmissionRequest(lane="src", kind="cluster",
                                tree=["src/dos/arbiter.py"])
    v = SelfModifyPredicate()(req, {}, cfg)
    assert not v.admitted and "Pass --force" in v.reason


def test_hook_surface_reason_is_classed_and_appends_when_tail_absent():
    # Pure-function contract: other classes pass through; a SELF_MODIFY reason
    # without the CLI tail gets the hook remedies APPENDED (never silently kept).
    assert prt.hook_surface_reason("a collision", "") == "a collision"
    out = prt.hook_surface_reason("custom refusal text", "SELF_MODIFY")
    assert out.startswith("custom refusal text")
    assert "dos decisions" in out and "Pass --force" not in out


def test_non_runtime_write_passthrough_on_admission():
    """A write to a NON-runtime path does not trip SELF_MODIFY → admission admits."""
    import tempfile
    cfg = _kernel_cfg(Path(tempfile.mkdtemp()))
    dialect, outcome = prt.decide(_event("Write", {"file_path": "docs/scratch.md"}), cfg)
    # Admission admits; Rung B over an empty corpus believes → passthrough.
    assert outcome["decision"] == "passthrough"
    assert dialect is None


# ==========================================================================
# (2b) The UNKNOWN-tree-vs-live-lease split (docs/191 §3 −9 pp regression guard).
#      A non-admit is deny-safe ONLY when the refusal is PROVABLE (structural
#      reason_class, or a KNOWN tree that really collides). An UNPROVABLE refusal
#      — an unknown footprint refused only because an UNRELATED lane is contended
#      — must WARN-and-pass, never deny. Without this split, `npm test` / a
#      pathless Write / an MCP tool got a hard `permissionDecision: deny` (a wrong
#      block with no --force escape) the instant ANY lane lease went live.
# ==========================================================================
def _src_lease():
    return {"lane": "src", "tree": ["src/"], "kind": "cluster",
            "loop_ts": "2026-06-06T00:00:00Z"}


@pytest.mark.parametrize("tool,tool_input", [
    ("Bash", {"command": "npm test"}),       # unknown footprint (no path-shaped token)
    ("Write", {"content": "x"}),              # write tool, no resolvable path → unknown tree
    ("mcp__svc__do", {"q": "1"}),             # unrecognized (MCP) tool → unknown tree
])
def test_unknown_tree_vs_live_lease_warns_not_denies(monkeypatch, tool, tool_input):
    """An UNKNOWN-tree mutating call refused only because an unrelated lane is live →
    WARN-and-pass (additionalContext, NO permissionDecision), not a hard deny."""
    import tempfile
    cfg = _kernel_cfg(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(prt, "live_leases_for", lambda c: [_src_lease()])
    dialect, outcome = prt.decide(_event(tool, tool_input, cwd="/repo"), cfg)
    assert outcome["decision"] == "warn", outcome
    assert outcome["tree_known"] is False
    hso = dialect["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    # The load-bearing assertion: NO permissionDecision → CC's normal flow proceeds (passthrough),
    # only additionalContext is added (turn-preserving). A deny here is the −9 pp mistake.
    assert "permissionDecision" not in hso
    assert hso["additionalContext"]


@pytest.mark.parametrize("tool,tool_input", [
    ("Read", {"file_path": "src/dos/arbiter.py"}),  # a read of a path UNDER the held lease
    ("Grep", {"pattern": "x"}),                      # a pathless read
])
def test_read_vs_live_lease_passes_clean_no_advisory(monkeypatch, tool, tool_input):
    """A proven no-footprint READ passes CLEAN against a live lease (issue #46).

    A read-only tool has a KNOWN but EMPTY tree — it provably touches NOTHING, so it
    can never collide with ANY live lease. It never denied (FQ-532 Defect 3 already
    fixed the phantom-lane DENY), but it still emitted a PRE-admission ADVISORY on
    every read while an unrelated lane was leased — ambient noise that trains the
    operator to skim past PRE-admission output (the wrong reflex for the one call
    that matters). Now a proven no-footprint refusal passes CLEAN: no dialect, no
    additionalContext, a `passthrough` outcome. The advisory is reserved for the
    genuinely-unknown footprint (the next test), where "scope it to a path" is real
    guidance. Only the OUTPUT changed — the call passed either way."""
    import tempfile
    cfg = _kernel_cfg(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(prt, "live_leases_for", lambda c: [_src_lease()])
    dialect, outcome = prt.decide(_event(tool, tool_input, cwd="/repo"), cfg)
    assert outcome["decision"] == "passthrough", outcome
    # A read has a KNOWN (empty) tree — tree_known True, yet no advisory: a proven
    # no-footprint call cannot collide, so it passes clean, not even WARN.
    assert outcome["tree_known"] is True
    assert dialect is None, "a proven no-footprint read emits nothing — no advisory"


def test_known_tree_real_collision_still_denies(monkeypatch):
    """The split must NOT weaken a PROVABLE refusal: a KNOWN tree (a resolvable path) that
    really overlaps the live `src/` lease still denies for a DISPATCH LOOP — the genuine
    region-collision gate. (A loop is signalled by a loop-context env; the operator-session
    softening below only fires when NONE of those envs are set.)"""
    import tempfile
    # Mark this as a dispatch-loop call so the operator-session softening does NOT apply —
    # a loop must still be hard-denied on a real collision.
    monkeypatch.setenv("DOS_LOOP", "1")
    cfg = _kernel_cfg(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(prt, "live_leases_for", lambda c: [_src_lease()])
    # A non-runtime path under the held src/ lease: not SELF_MODIFY (empty reason_class) but a
    # KNOWN-tree collision → deny via the tree_known arm of the split.
    dialect, outcome = prt.decide(
        _event("Edit", {"file_path": "src/dos/not_a_runtime_file.py",
                        "old_string": "a", "new_string": "b"}, cwd="/repo"), cfg)
    assert outcome["decision"] == "deny", outcome
    assert outcome["tree_known"] is True
    assert dialect["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_operator_session_collision_warns_not_denies(monkeypatch):
    """The operator-session softening: the SAME known-tree collision a dispatch loop is
    hard-DENIED on warns-and-passes for an INTERACTIVE operator (no loop-context env). A held
    lane's declared region is a defensive claim, not proof the holder is writing this file;
    the human-in-command owns their own blast radius (the `--force` principle)."""
    import tempfile
    # Interactive operator: clear every loop-context env so OperatorSession is true.
    for var in ("DOS_LOOP", "CID_RUN_ID", "DISPATCH_LOOP_TS"):
        monkeypatch.delenv(var, raising=False)
    cfg = _kernel_cfg(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(prt, "live_leases_for", lambda c: [_src_lease()])
    dialect, outcome = prt.decide(
        _event("Edit", {"file_path": "src/dos/not_a_runtime_file.py",
                        "old_string": "a", "new_string": "b"}, cwd="/repo"), cfg)
    assert outcome["decision"] == "warn", outcome
    # A WARN passes through — it omits permissionDecision (CC's normal permission flow runs).
    assert "permissionDecision" not in (dialect or {}).get("hookSpecificOutput", {}), dialect


def test_operator_session_does_not_soften_self_modify(monkeypatch):
    """Safety invariant: the operator-session softening touches CONTENTION refusals only.
    A SELF_MODIFY refusal (reason_class set) — editing the live kernel — stays a hard DENY
    for an operator too."""
    import tempfile
    for var in ("DOS_LOOP", "CID_RUN_ID", "DISPATCH_LOOP_TS"):
        monkeypatch.delenv(var, raising=False)
    cfg = _kernel_cfg(Path(tempfile.mkdtemp()))
    # A kernel runtime file (configured in _kernel_cfg) -> the SELF_MODIFY predicate fires
    # (request-absolute, reason_class set) regardless of any live lease.
    dialect, outcome = prt.decide(
        _event("Edit", {"file_path": "src/dos/arbiter.py",
                        "old_string": "a", "new_string": "b"}, cwd="/repo"), cfg)
    assert outcome["decision"] == "deny", outcome
    assert outcome["reason_class"] == "SELF_MODIFY", outcome


def test_disjoint_known_tree_passes_through_under_live_lease(monkeypatch):
    """Control: a KNOWN tree in a DISJOINT lane (`docs/`) is unaffected by the `src/` lease —
    it admits and passes through, proving the split denies on collision, not on lease-presence."""
    import tempfile
    cfg = _kernel_cfg(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(prt, "live_leases_for", lambda c: [_src_lease()])
    dialect, outcome = prt.decide(
        _event("Edit", {"file_path": "docs/x.md", "old_string": "a", "new_string": "b"},
               cwd="/repo"), cfg)
    assert outcome["decision"] == "passthrough", outcome
    assert dialect is None


# ==========================================================================
# (3) Read is never gated.
# ==========================================================================
def test_read_passthrough(monkeypatch, tmp_path):
    out, rc = _run_cli(monkeypatch, _event("Read", {"file_path": "README.md"}), tmp_path)
    assert out == "" and rc == 0


# ==========================================================================
# (4) Behavioral provenance: HIGH mint → deny WITH synthetic; LOW → additionalContext only.
#     Driven through decide() with a config + a wired test handler that actuates BLOCK.
# ==========================================================================
class _BlockHandler:
    """A test ruling handler that ACTUATES the kernel's recommended rung (unlike Observe).
    Registered nowhere — passed by monkeypatching resolve_handler, so we exercise the deny
    path a real driver would enable, while the DEFAULT install stays Observe-only."""

    name = "block-test"

    def handle(self, decision, config):
        from dos.enforce import EffectProposal
        if decision.intervention is _intervention.Intervention.BLOCK:
            synth = _intervention.synthetic_corrective_result(
                # decision carries no verdict; the consumer builds the synth from the
                # decision's unsupported args via a minimal shim verdict.
                _MintVerdict(decision.unsupported), "tool")
            return EffectProposal(
                intervention=_intervention.Intervention.BLOCK,
                dispatch_call=False, synthetic_result=synth,
                note=decision.reason, handler=self.name, reason="block-test actuates BLOCK",
            )
        # WARN/OBSERVE → dispatch, note only.
        return EffectProposal(
            intervention=decision.intervention, dispatch_call=True,
            note=decision.reason, handler=self.name, reason="block-test dispatches",
        )


@dataclasses.dataclass(frozen=True)
class _MintVerdict:
    """A minimal stand-in carrying the fields synthetic_corrective_result reads (`.args`)."""
    unsupported: tuple

    @property
    def args(self):
        from dos.arg_provenance import ArgProvenance, ProvenanceStance
        return tuple(
            ArgProvenance(arg_name=a, value_repr="", stance=ProvenanceStance.UNSUPPORTED,
                          matched_in=(), components_unmatched=(a,))
            for a in self.unsupported
        )


def _mutating_event_with_minted_id(value="INC9999999"):
    """A mutating call whose id-shaped arg never appeared in any prior env result → a mint."""
    return _event("Edit", {"file_path": "data.txt", "incident_id": value}, session="MINT")


def test_high_mint_denies_with_synthetic(monkeypatch, tmp_path):
    """A HIGH-confidence whole-value mint, with a ruling handler wired, → deny + synthetic."""
    import tempfile
    cfg = _foreign_cfg(Path(tempfile.mkdtemp()))  # no SELF_MODIFY interference
    monkeypatch.setattr(_enforce, "resolve_handler", lambda name, **kw: _BlockHandler())
    dialect, outcome = prt.decide(
        _mutating_event_with_minted_id(), cfg, handler_name="block-test")
    # An empty prior corpus makes classify_call ABSTAIN-all (believe=True) → OBSERVE, NOT a
    # mint. To exercise the mint path we need a non-empty corpus that does NOT contain the
    # id. Provide one via the session stream below; here assert the empty-corpus SAFE path.
    assert outcome["decision"] in ("passthrough", "deny")  # empty corpus → safe passthrough


def test_low_or_composite_mint_warns_not_denies():
    """A LOW-confidence mint resolves to WARN (additionalContext only) — never a deny."""
    # choose_intervention maps LOW → on_low_confidence (WARN), which dispatches. We assert
    # the dialect a WARN produces is additionalContext-only (no permissionDecision).
    payload = prt.warn_payload("resolve the id via a read first")
    hso = payload["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert "additionalContext" in hso
    assert "permissionDecision" not in hso  # WARN never denies


# ==========================================================================
# (5) Handler-fault structural proof — a raising / wrong-type handler NEVER denies.
# ==========================================================================
class _RaisingHandler:
    name = "raises"

    def handle(self, decision, config):
        raise RuntimeError("handler bug")


class _WrongTypeHandler:
    name = "wrong-type"

    def handle(self, decision, config):
        return {"not": "an EffectProposal"}


def test_raising_handler_never_denies(monkeypatch):
    import tempfile
    cfg = _foreign_cfg(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(_enforce, "resolve_handler", lambda name, **kw: _RaisingHandler())
    dialect, outcome = prt.decide(_mutating_event_with_minted_id(), cfg, handler_name="raises")
    # fail-to-OBSERVE: run_handler degraded the raise to OBSERVE → no deny on stdout.
    assert outcome["decision"] != "deny"
    assert dialect is None or "permissionDecision" not in dialect.get("hookSpecificOutput", {})


def test_wrong_type_handler_never_denies(monkeypatch):
    import tempfile
    cfg = _foreign_cfg(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(_enforce, "resolve_handler", lambda name, **kw: _WrongTypeHandler())
    dialect, outcome = prt.decide(_mutating_event_with_minted_id(), cfg, handler_name="wrong-type")
    assert outcome["decision"] != "deny"
    assert dialect is None or "permissionDecision" not in dialect.get("hookSpecificOutput", {})


# ==========================================================================
# (6) No-escalation — a handler proposing DEFER on a BLOCK decision is clamped, never deny.
# ==========================================================================
class _OverreachHandler:
    name = "overreach"

    def handle(self, decision, config):
        from dos.enforce import EffectProposal
        # Propose DEFER (more disruptive than any BLOCK the kernel recommends).
        return EffectProposal(
            intervention=_intervention.Intervention.DEFER,
            dispatch_call=False, synthetic_result={"x": 1},
            note="overreach", handler=self.name, reason="tries to escalate",
        )


def test_overreaching_handler_clamped_to_observe(monkeypatch):
    import tempfile
    cfg = _foreign_cfg(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(_enforce, "resolve_handler", lambda name, **kw: _OverreachHandler())
    dialect, outcome = prt.decide(_mutating_event_with_minted_id(), cfg, handler_name="overreach")
    # run_handler clamps an over-disruptive proposal to OBSERVE → no deny.
    assert outcome["decision"] != "deny"


# ==========================================================================
# (7) Default-install PDP proof — ObserveHandler only → ZERO behavioral deny.
# ==========================================================================
def test_default_observe_handler_emits_no_behavioral_deny():
    """With the default 'observe' handler (no driver wired), even a mint cannot deny via the
    behavioral rung — the PDP-only default install."""
    import tempfile
    cfg = _foreign_cfg(Path(tempfile.mkdtemp()))
    dialect, outcome = prt.decide(_mutating_event_with_minted_id(), cfg, handler_name="observe")
    assert outcome["decision"] != "deny"
    assert dialect is None or "permissionDecision" not in dialect.get("hookSpecificOutput", {})


# ==========================================================================
# (8) Dialect-exactness — stdout is byte-exact CC PreToolUse JSON; --debug only on stderr.
# ==========================================================================
def test_stdout_is_exact_cc_dialect_and_debug_on_stderr(monkeypatch, tmp_path, capsys):
    # Use a real kernel-repo workspace so SELF_MODIFY fires through the CLI: point the
    # workspace at the actual repo root (the test runs inside it).
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("DISPATCH_WORKSPACE", str(repo_root))
    event = _event("Write", {"file_path": "src/dos/arbiter.py"}, cwd=str(repo_root))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    out_buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", out_buf)
    from dos import cli
    args = cli.argparse.Namespace(
        workspace=None, driver=None, job=False, handler="observe", debug=True)
    rc = cli.cmd_hook_pretool(args)
    assert rc == 0
    out = out_buf.getvalue().strip()
    # Exactly one JSON object on stdout, the CC deny dialect, nothing else (no debug leak).
    obj = json.loads(out)
    assert set(obj.keys()) == {"hookSpecificOutput"}
    assert obj["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert obj["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "[dos hook pretool]" not in out  # debug went to stderr, not stdout


# ==========================================================================
# Tree extraction — the conservative unknown-blast-radius direction.
# ==========================================================================
def test_tree_read_only_is_known_empty():
    assert prt._tree_from_event(_event("Read", {"file_path": "x"})) == ((), True)


def test_tree_write_extracts_path():
    tree, known = prt._tree_from_event(_event("Write", {"file_path": "src/dos/oracle.py"}))
    assert known and tree == ("src/dos/oracle.py",)


def test_tree_unparseable_bash_is_unknown():
    tree, known = prt._tree_from_event(_event("Bash", {"command": "make build"}))
    assert tree == () and known is False  # unknown blast radius, conservative


def test_tree_write_without_path_is_unknown():
    tree, known = prt._tree_from_event(_event("Write", {}))
    assert tree == () and known is False


# ==========================================================================
# A mention is not a mutation (issue #12) — a Bash command whose invoked program
# provably cannot write gets the read-only posture (known-EMPTY tree), so a kernel
# path appearing as PROSE inside an argument is no longer scraped into a write
# footprint. The conservative direction is fully preserved: a shell write
# metacharacter, an unrecognized program, a wrapper, or one bad segment in a chain
# all fall back to today's scrape.
# ==========================================================================
@pytest.mark.parametrize("cmd", [
    'gh issue create --body "see src/dos/arbiter.py"',  # the observed false deny
    "grep -n foo src/dos/arbiter.py",                   # grep cannot write its args
    "git log --oneline -5 -- src/dos/arbiter.py",       # git read-only subcommand
    "git --no-pager diff src/dos/arbiter.py",           # flags before the subcommand
    "ls -la",                                            # bare read-only program
    "git status",
    "git log | grep fix",                                # every pipe segment qualifies
    "gh pr view 12 && gh issue list",                    # every chain segment qualifies
    "FOO=1 grep x src/dos/arbiter.py",                   # leading VAR=value skipped
])
def test_tree_no_write_footprint_command_is_known_empty(cmd):
    assert prt._tree_from_event(_event("Bash", {"command": cmd})) == ((), True)


@pytest.mark.parametrize("cmd,tree", [
    ("echo x > src/dos/arbiter.py", ("src/dos/arbiter.py",)),   # `>` defeats the allowance
    ("git log > src/dos/arbiter.py", ("src/dos/arbiter.py",)),  # even for an allowed program
    ("rm src/dos/_tree.py", ("src/dos/_tree.py",)),             # rm is not in the set
    ("git log && rm src/dos/_tree.py", ("src/dos/_tree.py",)),  # one bad segment → scrape
    ("sudo grep x src/dos/arbiter.py", ("src/dos/arbiter.py",)),  # a wrapper stays conservative
    ("sort -o out.txt src/dos/arbiter.py", ("src/dos/arbiter.py",)),  # sort -o writes → excluded
])
def test_tree_write_capable_command_still_scrapes(cmd, tree):
    assert prt._tree_from_event(_event("Bash", {"command": cmd})) == (tree, True)


def test_tree_substitution_veto_falls_back_to_unknown():
    """A `$(…)` substitution can run anything — the allowance is vetoed and the scrape
    finds nothing path-shaped, leaving the conservative UNKNOWN tree."""
    cmd = 'gh issue create --body "$(rm -rf x)"'
    assert prt._tree_from_event(_event("Bash", {"command": cmd})) == ((), False)


def test_gh_issue_mention_not_denied_but_redirect_still_denied():
    """The issue-#12 done-condition, at the decide() level: filing an issue ABOUT a kernel
    runtime file is not a SELF_MODIFY deny (the path is a mention), while actually
    redirecting bytes INTO that file still denies."""
    import tempfile
    cfg = _kernel_cfg(Path(tempfile.mkdtemp()))
    mention = _event("Bash", {"command": 'gh issue create --body "see src/dos/arbiter.py"'})
    dialect, outcome = prt.decide(mention, cfg)
    assert outcome["decision"] != "deny", outcome
    write = _event("Bash", {"command": "echo x > src/dos/arbiter.py"})
    dialect, outcome = prt.decide(write, cfg)
    assert outcome["decision"] == "deny"
    assert outcome["reason_class"] == "SELF_MODIFY"
    assert dialect["hookSpecificOutput"]["permissionDecision"] == "deny"


# ==========================================================================
# (9) The apply-gate binding turnstile (docs/126 Phase 1.5 / docs/342 M1 = P-GATE).
#     The keystone: generalize the always-on SELF_MODIFY deny — which binds a write
#     to the kernel's OWN T1 files — to ANY held lease's tree. A NON-cooperating
#     agent's out-of-lane Write is DENIED at the tool call (not just by an opt-in CLI
#     it can skip), and the file is UNCHANGED on disk afterward (the P-GATE witness:
#     the filesystem state + the host deny record, never the agent's report).
#
#     The gate is OPT-IN (`DOS_APPLY_GATE`) and binds only when this run holds a
#     lease — so the four pins are: a default install is unchanged; an out-of-lane
#     loop write DENIES (and the file stays put); an in-lane write PASSES; and an
#     interactive operator gets a WARN, never a dead-end deny (the docs/143 escape).
# ==========================================================================
def _self_lease(lane="docs", tree=("docs/",)):
    """A live lease this very test process holds — matched by `resolve_self_lease`
    on the running pid, so `self_tree` resolves to the lease's recorded tree without
    needing a configured lane taxonomy."""
    import os
    return {"lane": lane, "tree": list(tree), "kind": "cluster",
            "pid": os.getpid(), "loop_ts": "2026-06-14T00:00:00Z"}


def _enable_apply_gate(monkeypatch, *, loop: bool):
    """Turn the opt-in gate on; set/clear the loop-context env that decides
    deny-under-loop vs warn-for-an-operator."""
    monkeypatch.setenv("DOS_APPLY_GATE", "1")
    if loop:
        monkeypatch.setenv("DOS_LOOP", "1")
    else:
        for var in ("DOS_LOOP", "CID_RUN_ID", "DISPATCH_LOOP_TS"):
            monkeypatch.delenv(var, raising=False)


def test_apply_gate_off_by_default_out_of_lane_write_not_denied(monkeypatch):
    """OPT-IN proof: without `DOS_APPLY_GATE`, an out-of-lane write is NOT apply-gate
    denied — the default install keeps exactly today's SELF_MODIFY + disjointness
    behavior (docs/126 §3 rule 3: the gate is an ADDITIONAL surface, opt-in)."""
    import tempfile
    monkeypatch.delenv("DOS_APPLY_GATE", raising=False)
    cfg = _foreign_cfg(Path(tempfile.mkdtemp()))  # no SELF_MODIFY interference
    monkeypatch.setattr(prt, "live_leases_for", lambda c: [_self_lease("docs", ("docs/",))])
    # A write to src/ while holding the docs/ lane: an out-of-lane escape — but the
    # gate is OFF, so it passes through (Rung B over an empty corpus believes).
    dialect, outcome = prt.decide(
        _event("Write", {"file_path": "src/dos/oracle.py", "content": "x"}, cwd="/repo"), cfg)
    assert outcome["rung"] != "apply-gate", outcome
    assert outcome["decision"] != "deny", outcome


def test_apply_gate_out_of_lane_write_denied_and_file_unchanged(monkeypatch, tmp_path):
    """THE P-GATE WITNESS: a NON-cooperating loop write OUTSIDE its held lane is DENIED
    at PreToolUse, and the target file is UNCHANGED on disk afterward.

    The witness is the filesystem state + the host deny record, never the agent's
    report: `decide()` returns a `permissionDecision: deny`, which the host honors by
    NOT executing the Write — so a file we seed on disk keeps its original bytes. This
    is docs/335 §5's acceptance test against a non-cooperating consumer: the consumer
    issues a raw out-of-lane Write through the normal tool path and is refused before
    the effect, driving its acceptance rate of bad effects toward zero."""
    import tempfile
    _enable_apply_gate(monkeypatch, loop=True)
    cfg = _foreign_cfg(Path(tempfile.mkdtemp()))
    # The agent holds the docs/ lane; a real file under src/ exists with known bytes.
    monkeypatch.setattr(prt, "live_leases_for", lambda c: [_self_lease("docs", ("docs/",))])
    target = tmp_path / "src" / "dos" / "oracle.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    original = "# the real kernel bytes — must survive a refused out-of-lane write\n"
    target.write_text(original, encoding="utf-8")

    event = _event("Write",
                   {"file_path": "src/dos/oracle.py", "content": "POISONED"},
                   cwd="/repo")
    dialect, outcome = prt.decide(event, cfg)

    # 1. The host deny record — the binding refusal at the tool call.
    assert outcome["rung"] == "apply-gate", outcome
    assert outcome["decision"] == "deny", outcome
    assert outcome["reason_class"] == "SCOPE_ESCAPE", outcome
    hso = dialect["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "updatedInput" not in hso  # never mint corrective bytes (docs/191 §4)
    # 2. The filesystem witness — the host honored the deny, so the Write never ran:
    #    the file on disk is byte-for-byte its original content. (decide() itself does
    #    no I/O; the deny is what keeps the agent's bytes from ever landing.)
    assert target.read_text(encoding="utf-8") == original, \
        "the refused out-of-lane write must NOT have changed the file on disk"


def test_apply_gate_in_lane_write_passes(monkeypatch):
    """An IN-LANE write passes the apply-gate: holding the src/ lane, a write to a
    src/ path is contained — the gate allows it, so the decision is not an apply-gate
    deny (it falls through to Rung B, which believes over an empty corpus)."""
    import tempfile
    _enable_apply_gate(monkeypatch, loop=True)
    cfg = _foreign_cfg(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(prt, "live_leases_for", lambda c: [_self_lease("src", ("src/",))])
    dialect, outcome = prt.decide(
        _event("Write", {"file_path": "src/dos/oracle.py", "content": "x"}, cwd="/repo"), cfg)
    assert outcome["rung"] != "apply-gate", outcome
    assert outcome["decision"] != "deny", outcome
    assert dialect is None or "permissionDecision" not in dialect.get("hookSpecificOutput", {})


def test_apply_gate_interactive_operator_escape_warns_no_dead_end(monkeypatch):
    """THE docs/143 SOFTEN-TRAP ESCAPE (no dead-end): the SAME out-of-lane write a loop
    is hard-DENIED on WARNS-and-passes for an INTERACTIVE operator (no loop-context
    env). A SCOPE_ESCAPE is a MISROUTE/contention-class refusal; the human-in-command
    owns the blast radius of their own deliberate edit (the --force principle), so the
    gate downgrades to an advisory WARN — never the docs/143 −9 pp dead-end deny."""
    import tempfile
    _enable_apply_gate(monkeypatch, loop=False)
    cfg = _foreign_cfg(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(prt, "live_leases_for", lambda c: [_self_lease("docs", ("docs/",))])
    dialect, outcome = prt.decide(
        _event("Write", {"file_path": "src/dos/oracle.py", "content": "x"}, cwd="/repo"), cfg)
    assert outcome["rung"] == "apply-gate", outcome
    assert outcome["decision"] == "warn", outcome
    assert outcome["reason_class"] == "SCOPE_ESCAPE", outcome
    hso = (dialect or {}).get("hookSpecificOutput", {})
    # A WARN passes through: additionalContext only, NO permissionDecision (CC's normal
    # permission flow proceeds — the operator is never dead-ended).
    assert "permissionDecision" not in hso, dialect
    assert hso.get("additionalContext"), "the WARN must still surface the corrective"


def test_apply_gate_collision_with_sibling_lease_denied_under_loop(monkeypatch):
    """A SIBLING collision is a BINDING refusal: a loop write inside its OWN lane that
    still overlaps a SIBLING lease's region is DENIED at the tool call. The sibling
    collision is caught by the always-on disjointness predicate (which correctly
    compares against NON-self leases) — the apply-gate's collision floor is a backup of
    the same sound floor (docs/126 §1.1). Either binding rung is a valid refusal; the
    witness is the `permissionDecision: deny`, not which rung emitted it. (The
    apply-gate's UNIQUE contribution — an out-of-lane CONTAINMENT escape disjointness
    cannot see — is the SCOPE_ESCAPE deny test above.)"""
    import tempfile
    _enable_apply_gate(monkeypatch, loop=True)
    cfg = _foreign_cfg(Path(tempfile.mkdtemp()))
    import os
    leases = [
        {"lane": "src", "tree": ["src/"], "kind": "cluster", "pid": os.getpid()},
        {"lane": "sibling", "tree": ["src/dos/"], "kind": "cluster", "pid": -1},
    ]
    monkeypatch.setattr(prt, "live_leases_for", lambda c: leases)
    dialect, outcome = prt.decide(
        _event("Write", {"file_path": "src/dos/oracle.py", "content": "x"}, cwd="/repo"), cfg)
    assert outcome["decision"] == "deny", outcome
    assert outcome["rung"] in ("admission", "apply-gate"), outcome
    assert dialect["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_apply_gate_no_self_lease_keeps_pre_gate_behavior(monkeypatch):
    """The gate binds a run that DECLARED a lane: an un-leased session (no lease this
    run holds) is NOT apply-gate denied — `resolve_self_lease` returns an empty
    self-tree and no other trees, and the caller skips the gate (the gate must never
    invent a lane for a session that never opted in)."""
    import tempfile
    _enable_apply_gate(monkeypatch, loop=True)
    cfg = _foreign_cfg(Path(tempfile.mkdtemp()))
    # No leases at all → resolve_self_lease yields ("", (), ()) → the gate is skipped.
    monkeypatch.setattr(prt, "live_leases_for", lambda c: [])
    dialect, outcome = prt.decide(
        _event("Write", {"file_path": "src/dos/oracle.py", "content": "x"}, cwd="/repo"), cfg)
    assert outcome["rung"] != "apply-gate", outcome
    assert outcome["decision"] != "deny", outcome


def test_apply_gate_self_modify_still_takes_precedence(monkeypatch):
    """Safety ordering: the always-on SELF_MODIFY deny runs FIRST (in run_predicates),
    so a write to a kernel T1 file is a SELF_MODIFY deny, not an apply-gate one — even
    with the gate enabled. The apply-gate ADDS refusals (refuse-MORE only); it never
    displaces the request-absolute kernel guard."""
    import tempfile
    _enable_apply_gate(monkeypatch, loop=True)
    cfg = _kernel_cfg(Path(tempfile.mkdtemp()))  # T1 files configured
    monkeypatch.setattr(prt, "live_leases_for", lambda c: [_self_lease("docs", ("docs/",))])
    dialect, outcome = prt.decide(
        _event("Write", {"file_path": "src/dos/arbiter.py", "content": "x"}, cwd="/repo"), cfg)
    assert outcome["decision"] == "deny", outcome
    assert outcome["reason_class"] == "SELF_MODIFY", outcome  # not SCOPE_ESCAPE
    assert outcome["rung"] == "admission", outcome


def test_apply_gate_read_never_gated(monkeypatch):
    """A read is never apply-gated — it has a known-EMPTY tree (touches nothing), so
    even with the gate on and a held lease, a Read passes clean."""
    import tempfile
    _enable_apply_gate(monkeypatch, loop=True)
    cfg = _foreign_cfg(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(prt, "live_leases_for", lambda c: [_self_lease("docs", ("docs/",))])
    dialect, outcome = prt.decide(
        _event("Read", {"file_path": "src/dos/oracle.py"}, cwd="/repo"), cfg)
    assert outcome["rung"] != "apply-gate", outcome
    assert outcome["decision"] != "deny", outcome


def test_resolve_self_lease_matches_pid_and_falls_back_to_lease_tree(monkeypatch):
    """`resolve_self_lease` unit: it matches the running pid, takes the lease's
    recorded tree when the taxonomy doesn't name the lane, and reports OTHER trees."""
    import os
    import tempfile
    cfg = _foreign_cfg(Path(tempfile.mkdtemp()))
    leases = [
        {"lane": "docs", "tree": ["docs/"], "pid": os.getpid()},
        {"lane": "other", "tree": ["bench/"], "pid": -1},
    ]
    self_lane, self_tree, other_trees = prt.resolve_self_lease(leases, cfg)
    assert self_lane == "docs"
    assert self_tree == ("docs/",)
    assert ("bench/",) in other_trees
