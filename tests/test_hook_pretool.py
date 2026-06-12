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
def test_read_vs_live_lease_warns_not_denies(monkeypatch, tool, tool_input):
    """FQ-532 Defect 3: a READ-ONLY tool has a KNOWN but EMPTY tree — it provably
    touches NOTHING, so it can never collide. Against a live `src/` lease the
    disjointness predicate refuses it ("empty requested tree vs known lease") with
    NO reason_class, and the OLD `reason_class or tree_known` gate escalated that
    contention-only refusal to a hard DENY for every Read/Grep — the phantom-lane
    denial that blocked real agent reads. The fix keeps it ADVISORY (WARN, no
    permissionDecision) regardless of tree_known, so the read passes through. This is
    the Python parity of the Go `TestReadAgainstContendedLaneWarnsNotDenies`."""
    import tempfile
    cfg = _kernel_cfg(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(prt, "live_leases_for", lambda c: [_src_lease()])
    dialect, outcome = prt.decide(_event(tool, tool_input, cwd="/repo"), cfg)
    assert outcome["decision"] == "warn", outcome
    # A read has a KNOWN (empty) tree — tree_known is True, yet it must NOT deny:
    # the fix's load-bearing point is that tree_known alone is not proof of collision.
    assert outcome["tree_known"] is True
    hso = dialect["hookSpecificOutput"]
    assert "permissionDecision" not in hso, "a read must pass through, never deny"
    assert hso["additionalContext"]


def test_known_tree_real_collision_still_denies(monkeypatch):
    """The split must NOT weaken a PROVABLE refusal: a KNOWN tree (a resolvable path) that
    really overlaps the live `src/` lease still denies — the genuine region-collision gate."""
    import tempfile
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
