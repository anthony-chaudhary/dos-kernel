"""docs/296 — the operator-armed SELF_MODIFY override window.

The verdict is NOT under test here — a tool call hitting the kernel's runtime
files still classifies SELF_MODIFY, armed window or not. What these tests pin
is the ENFORCEMENT disposition (PDP decides; PEP disposes) and its fail-closed
perimeter:

  * `read_override` can only fail toward None (missing/garbled/incomplete arm
    file → the deny stands, byte-identical);
  * `dispose` converts ONLY a SELF_MODIFY refusal, only inside the window,
    and — when scoped — only for provably in-scope targets;
  * the hook emits the admit as ALLOW-with-note (`additionalContext`, never a
    silent pass) and journals a distinct `override-admit` decision;
  * the arm path itself is write-DENIED for agents even inside an armed
    window (a window must not extend itself);
  * docs/328 Phase 2: an `arm` verb exists, but it is INERT headless — it
    writes the window only behind an interactivity gate (both streams ttys +
    an explicit y/N) a non-interactive agent shell cannot satisfy, so the
    docs/296 asymmetry holds (anyone may disarm, only an interactive human
    arms). `suggest` remains print-only.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
from pathlib import Path

from dos import config as _config
from dos import override_facts as ovr
from dos import pretool_sensor as prt


NOW = dt.datetime(2026, 6, 10, 12, 0, 0, tzinfo=dt.timezone.utc)
LATER = "2026-06-10T13:00:00Z"     # one hour inside the window
EARLIER = "2026-06-10T11:00:00Z"   # already expired at NOW


def _arm(root: Path, *, until: str = LATER, reason: str = "test window",
         scope: list[str] | None = None, raw: str | None = None) -> Path:
    p = ovr.arm_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        p.write_text(raw, encoding="utf-8")
        return p
    lines = [f"until = {until}", f'reason = "{reason}"']
    if scope is not None:
        # TOML literal strings (single quotes): no escape processing, so a
        # Windows backslash in a scope entry survives verbatim.
        entries = ", ".join(f"'{s}'" for s in scope)
        lines.append(f"scope = [{entries}]")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _kernel_cfg(tmp_path: Path):
    """The test_hook_pretool fixture: workspace facts that declare the runtime
    set, so SELF_MODIFY fires deterministically under a tmp root."""
    cfg = _config.default_config(tmp_path)
    facts = _config.WorkspaceFacts(
        root=tmp_path,
        kernel_runtime_files=("src/dos/arbiter.py", "src/dos/admission.py",
                              "src/dos/self_modify.py"),
        is_kernel_repo=True,
    )
    return dataclasses.replace(cfg, workspace=facts)


def _event(tool_name="Write", tool_input=None, **extra):
    e = {"tool_name": tool_name, "session_id": "S1",
         "tool_input": tool_input if tool_input is not None else {}}
    e.update(extra)
    return e


# ==========================================================================
# read_override — every malformed branch folds to None (fail-closed).
# ==========================================================================
def test_reader_missing_file_is_none(tmp_path):
    assert ovr.read_override(tmp_path) is None


def test_reader_garbled_toml_is_none(tmp_path):
    _arm(tmp_path, raw="this is { not toml\n")
    assert ovr.read_override(tmp_path) is None


def test_reader_non_utf8_arm_file_is_none_not_a_crash(tmp_path):
    """#147: a non-UTF-8 arm file (e.g. PowerShell 5.1's `>` redirect writes UTF-16
    + BOM) must fail CLOSED to None — not raise UnicodeDecodeError and crash
    `dos override status` / the hook's disposition read. The TOML content is VALID;
    only the encoding is wrong, which is still a malformed override → deny stands."""
    p = ovr.arm_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # A perfectly valid arm file, written as UTF-16LE — the 0xff/0xfe BOM that
    # `utf-8-sig` cannot decode (the exact bytes the bug crashed on).
    p.write_text(f'until = {LATER}\nreason = "wrong encoding"\n', encoding="utf-16")
    assert ovr.read_override(tmp_path) is None  # fail-closed, no exception


def test_reader_missing_until_or_reason_is_none(tmp_path):
    _arm(tmp_path, raw='reason = "no deadline"\n')
    assert ovr.read_override(tmp_path) is None
    _arm(tmp_path, raw=f"until = {LATER}\n")  # no reason
    assert ovr.read_override(tmp_path) is None
    _arm(tmp_path, raw=f'until = {LATER}\nreason = "   "\n')  # blank reason
    assert ovr.read_override(tmp_path) is None


def test_reader_bad_scope_type_is_none(tmp_path):
    _arm(tmp_path, raw=f'until = {LATER}\nreason = "x"\nscope = "not-a-list"\n')
    assert ovr.read_override(tmp_path) is None


def test_reader_good_file_parses_aware_until_and_normalized_scope(tmp_path):
    _arm(tmp_path, scope=["src/dos/Arbiter.py", "./tests\\"])
    facts = ovr.read_override(tmp_path)
    assert facts is not None
    assert facts.until.tzinfo is not None
    assert facts.reason == "test window"
    assert facts.scope == ("src/dos/arbiter.py", "tests")


# ==========================================================================
# dispose — the pure truth table.
# ==========================================================================
def _facts(until: str = LATER, scope: tuple[str, ...] = ()) -> ovr.OverrideFacts:
    return ovr.OverrideFacts(
        until=dt.datetime.fromisoformat(until), reason="r", scope=scope)


def test_dispose_none_facts_is_none():
    assert ovr.dispose("SELF_MODIFY", ("src/dos/arbiter.py",), None, now=NOW) is None


def test_dispose_only_converts_self_modify():
    f = _facts()
    assert ovr.dispose("SELF_MODIFY", ("src/dos/arbiter.py",), f, now=NOW)
    # A collision/budget/any-other refusal is never waved through.
    assert ovr.dispose("", ("src/dos/arbiter.py",), f, now=NOW) is None
    assert ovr.dispose("CLASS_BUDGET_EXHAUSTED", ("x",), f, now=NOW) is None


def test_dispose_expired_window_is_none():
    assert ovr.dispose("SELF_MODIFY", ("src/dos/arbiter.py",),
                       _facts(until=EARLIER), now=NOW) is None


def test_dispose_scope_gates_targets():
    scoped = _facts(scope=("src/dos/arbiter.py",))
    assert ovr.dispose("SELF_MODIFY", ("src/dos/arbiter.py",), scoped, now=NOW)
    # Out-of-scope target → the deny stands.
    assert ovr.dispose("SELF_MODIFY", ("src/dos/reasons.py",), scoped, now=NOW) is None
    # Mixed targets: ALL must be in scope.
    assert ovr.dispose("SELF_MODIFY",
                       ("src/dos/arbiter.py", "src/dos/reasons.py"),
                       scoped, now=NOW) is None
    # A scoped window with an unparseable footprint stays denied (cannot prove
    # the targets are inside the scope) — but an UNSCOPED window admits it.
    assert ovr.dispose("SELF_MODIFY", (), scoped, now=NOW) is None
    assert ovr.dispose("SELF_MODIFY", (), _facts(), now=NOW)


def test_dispose_scope_directory_entry_covers_children():
    scoped = _facts(scope=("src/dos",))
    assert ovr.dispose("SELF_MODIFY", ("src/dos/arbiter.py",), scoped, now=NOW)
    assert ovr.dispose("SELF_MODIFY", ("srcX/other.py",), scoped, now=NOW) is None


def test_dispose_note_names_deadline_reason_and_disarm():
    note = ovr.dispose("SELF_MODIFY", ("src/dos/arbiter.py",), _facts(), now=NOW)
    assert "operator override" in note
    assert "r" in note and "dos override disarm" in note


# ==========================================================================
# decide() — the hook disposition end to end (the PEP side).
# ==========================================================================
def test_unarmed_self_modify_still_denies(tmp_path):
    cfg = _kernel_cfg(tmp_path)
    dialect, outcome = prt.decide(
        _event("Write", {"file_path": "src/dos/arbiter.py"}), cfg)
    assert outcome["decision"] == "deny"
    assert outcome["reason_class"] == "SELF_MODIFY"
    assert dialect["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_armed_window_converts_deny_to_allow_with_note(tmp_path):
    cfg = _kernel_cfg(tmp_path)
    _arm(tmp_path, until="2099-01-01T00:00:00Z", reason="docs/296 e2e")
    dialect, outcome = prt.decide(
        _event("Write", {"file_path": "src/dos/arbiter.py"}), cfg)
    # The admit is distinct and on the record — never a silent passthrough.
    assert outcome["decision"] == "override-admit"
    assert outcome["reason_class"] == "SELF_MODIFY"  # the verdict is unchanged
    hso = dialect["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in hso  # ALLOW = no deny key, CC proceeds
    assert "operator override" in hso["additionalContext"]
    assert "docs/296 e2e" in hso["additionalContext"]


def test_expired_window_restores_the_deny(tmp_path):
    cfg = _kernel_cfg(tmp_path)
    _arm(tmp_path, until="2001-01-01T00:00:00Z", reason="long gone")
    dialect, outcome = prt.decide(
        _event("Write", {"file_path": "src/dos/arbiter.py"}), cfg)
    assert outcome["decision"] == "deny"
    assert dialect["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_scoped_window_denies_out_of_scope_target(tmp_path):
    cfg = _kernel_cfg(tmp_path)
    _arm(tmp_path, until="2099-01-01T00:00:00Z", reason="scoped",
         scope=["src/dos/admission.py"])
    dialect, outcome = prt.decide(
        _event("Write", {"file_path": "src/dos/arbiter.py"}), cfg)
    assert outcome["decision"] == "deny"


def test_arm_path_write_is_denied_even_while_armed(tmp_path):
    """The perimeter: a window must not be able to extend itself."""
    cfg = _kernel_cfg(tmp_path)
    _arm(tmp_path, until="2099-01-01T00:00:00Z", reason="open window")
    dialect, outcome = prt.decide(
        _event("Write", {"file_path": ".dos/override/self-modify.toml"}), cfg)
    assert outcome["decision"] == "deny"
    assert outcome["reason_class"] == "SELF_MODIFY"
    assert "only the operator arms" in outcome["reason"]
    assert dialect["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_arm_path_deny_fires_in_foreign_repos_too(tmp_path):
    """The perimeter is request-absolute: it guards the arm file even where
    SELF_MODIFY itself cannot fire (no kernel runtime files under the root)."""
    cfg = _config.default_config(tmp_path)
    dialect, outcome = prt.decide(
        _event("Write", {"file_path": ".dos/override/self-modify.toml"}), cfg)
    assert outcome["decision"] == "deny"


# ==========================================================================
# the CLI verb — status / disarm only (no arm verb, by design).
# ==========================================================================
def _cli_override(tmp_path: Path, *argv: str):
    import io
    import sys as _sys
    from dos import cli
    # Drive through main() so the parser wiring (subcommand, flags) is pinned.
    # `--workspace` sits on the `override` parent parser, so it goes BEFORE the
    # status/disarm subcommand token (the lease-lane flag-placement rule).
    out = io.StringIO()
    old = _sys.stdout
    _sys.stdout = out
    try:
        rc = cli.main(["override", "--workspace", str(tmp_path), *argv])
    except SystemExit as e:  # argparse exits
        rc = int(e.code or 0)
    finally:
        _sys.stdout = old
    return out.getvalue(), rc


def test_cli_status_disarmed_then_armed_then_disarm(tmp_path):
    out, rc = _cli_override(tmp_path, "status")
    assert rc == 1
    assert json.loads(out)["armed"] is False

    _arm(tmp_path, until="2099-01-01T00:00:00Z", reason="window for tests")
    out, rc = _cli_override(tmp_path, "status")
    assert rc == 0
    body = json.loads(out)
    assert body["armed"] is True and body["reason"] == "window for tests"

    out, rc = _cli_override(tmp_path, "disarm")
    assert rc == 0
    assert json.loads(out)["disarmed"] is True

    out, rc = _cli_override(tmp_path, "status")
    assert rc == 1


def test_cli_arm_exists_but_refuses_headless_LITMUS(tmp_path, monkeypatch):
    """docs/328 Phase 2: the `arm` verb now EXISTS, but the asymmetry is unchanged
    — only an INTERACTIVE human arms. Driven through main() with captured (non-tty)
    streams — exactly an agent's shell — `arm` must REFUSE (exit 2, refused
    'not-a-tty'), write NOTHING, and leave the window disarmed. This is the
    load-bearing security property: no headless arm path exists."""
    # main() drives through _cli_override_streams, whose StringIO streams are not
    # ttys; belt-and-suspenders, force the gate False so the test does not depend
    # on the capture's isatty behavior.
    from dos import cli
    monkeypatch.setattr(cli, "_require_interactive_operator", lambda: False)
    stdout, stderr, rc = _cli_override_streams(
        tmp_path, "arm", "--reason", "agent tried to self-arm", "--minutes", "20")
    assert rc == 2
    body = json.loads(stderr)  # the refusal is on stderr
    assert body["armed"] is False and body["refused"] == "not-a-tty"
    assert not ovr.arm_path(tmp_path).exists()  # wrote nothing
    out, rc2 = _cli_override(tmp_path, "status")
    assert rc2 == 1 and json.loads(out)["armed"] is False


def test_cli_arm_interactive_writes_a_reader_acceptable_window(tmp_path, monkeypatch):
    """The positive path: an interactive operator (gate True + confirm 'y') arms
    the window. The file round-trips through read_override and status reports it
    armed. The bytes are UTF-8 with NO BOM — the #148 win: written from this
    process, never via a PowerShell `>` redirect."""
    from dos import cli
    monkeypatch.setattr(cli, "_require_interactive_operator", lambda: True)
    monkeypatch.setattr(cli, "_confirm_interactive", lambda prompt: True)
    stdout, stderr, rc = _cli_override_streams(
        tmp_path, "arm", "src/dos/arbiter.py", "--reason", "fix #118",
        "--minutes", "25")
    assert rc == 0
    body = json.loads(stdout)
    assert body["armed"] is True and body["reason"] == "fix #118"
    assert body["scope"] == ["src/dos/arbiter.py"]
    # The window is real: read_override parses it, status reports armed.
    facts = ovr.read_override(tmp_path)
    assert facts is not None and facts.reason == "fix #118"
    out, rc2 = _cli_override(tmp_path, "status")
    assert rc2 == 0 and json.loads(out)["armed"] is True
    # #148 win: the on-disk bytes are NOT a UTF-8 or UTF-16 BOM.
    raw = ovr.arm_path(tmp_path).read_bytes()
    assert raw[:3] != b"\xef\xbb\xbf"        # not UTF-8 BOM
    assert raw[:2] not in (b"\xff\xfe", b"\xfe\xff")  # not UTF-16 BOM


def test_cli_arm_declined_confirm_writes_nothing(tmp_path, monkeypatch):
    """Gate True but the operator answers 'n' at the confirm — no window, exit 1."""
    from dos import cli
    monkeypatch.setattr(cli, "_require_interactive_operator", lambda: True)
    monkeypatch.setattr(cli, "_confirm_interactive", lambda prompt: False)
    stdout, stderr, rc = _cli_override_streams(
        tmp_path, "arm", "--reason", "changed my mind", "--minutes", "10")
    assert rc == 1
    assert json.loads(stderr)["declined"] is True
    assert not ovr.arm_path(tmp_path).exists()


def test_cli_arm_offers_no_yes_or_force_bypass(tmp_path):
    """No agent-reachable bypass: argparse must reject --yes and --force on arm
    (either would be the non-interactive arm path docs/296 forbids)."""
    import pytest
    from dos import cli
    for bypass in ("--yes", "--force"):
        with pytest.raises(SystemExit) as exc:
            cli.main(["override", "--workspace", str(tmp_path), "arm",
                      "--reason", "x", bypass])
        assert exc.value.code != 0


def test_cli_arm_requires_a_reason(tmp_path, monkeypatch):
    """--reason is required even for an interactive arm (it lands in the audit note)."""
    import pytest
    from dos import cli
    monkeypatch.setattr(cli, "_require_interactive_operator", lambda: True)
    with pytest.raises(SystemExit) as exc:
        cli.main(["override", "--workspace", str(tmp_path), "arm"])
    assert exc.value.code != 0


def test_cli_arm_does_not_touch_the_hook_perimeter(tmp_path):
    """The perimeter deny on agent TOOL writes to .dos/override/ is a DIFFERENT
    guard and stays intact: even after the arm verb exists, a Write tool call
    targeting the arm file is still denied (a window must not extend itself)."""
    cfg = _kernel_cfg(tmp_path)
    _arm(tmp_path, until="2099-01-01T00:00:00Z", reason="open window")
    dialect, outcome = prt.decide(
        _event("Write", {"file_path": ".dos/override/self-modify.toml"}), cfg)
    assert outcome["decision"] == "deny"
    assert outcome["reason_class"] == "SELF_MODIFY"


# ==========================================================================
# render_arm_toml — the PURE arm-line emitter (issue #145). Text in, text out,
# no disk, no clock; the file it renders round-trips through read_override.
# ==========================================================================
def test_render_arm_toml_round_trips_through_reader(tmp_path):
    until = dt.datetime(2026, 6, 13, 23, 30, 0, tzinfo=dt.timezone.utc)
    toml = ovr.render_arm_toml('issue #145 — a "quoted" reason', until=until,
                               scope=("pkg/widget.py", "PKG/Foo.py"))
    # Write the rendered text to a real arm file and read it back: the emitter
    # must produce something the reader accepts (the contract that matters).
    p = ovr.arm_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(toml, encoding="utf-8")
    facts = ovr.read_override(tmp_path)
    assert facts is not None
    assert facts.until == until
    assert facts.reason == 'issue #145 — a "quoted" reason'
    # scope is normalized to the reader's one spelling (posix, casefolded).
    assert facts.scope == ("pkg/widget.py", "pkg/foo.py")


def test_render_arm_toml_no_scope_omits_the_line(tmp_path):
    until = dt.datetime(2026, 6, 13, 23, 30, 0, tzinfo=dt.timezone.utc)
    toml = ovr.render_arm_toml("whole T1 set", until=until)
    assert "scope" not in toml
    p = ovr.arm_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(toml, encoding="utf-8")
    facts = ovr.read_override(tmp_path)
    assert facts is not None and facts.scope == ()  # absent scope = the whole T1 set


def test_render_arm_toml_naive_until_is_made_aware(tmp_path):
    # A naive datetime must not crash and must round-trip as an aware UTC stamp.
    toml = ovr.render_arm_toml("r", until=dt.datetime(2026, 6, 13, 23, 30, 0))
    assert "+00:00" in toml  # rendered UTC
    p = ovr.arm_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(toml, encoding="utf-8")
    assert ovr.read_override(tmp_path) is not None


# ==========================================================================
# `dos override suggest` — PRINTS the arm line, NEVER writes (issue #145).
# The security litmus: a headless invocation leaves the window DISARMED.
# ==========================================================================
def _cli_override_streams(tmp_path: Path, *argv: str):
    """Drive `dos override …` capturing BOTH stdout and stderr (suggest splits
    the pasteable TOML onto stdout, the human how-to onto stderr)."""
    import io
    import sys as _sys
    from dos import cli
    out, err = io.StringIO(), io.StringIO()
    old_out, old_err = _sys.stdout, _sys.stderr
    _sys.stdout, _sys.stderr = out, err
    try:
        rc = cli.main(["override", "--workspace", str(tmp_path), *argv])
    except SystemExit as e:
        rc = int(e.code or 0)
    finally:
        _sys.stdout, _sys.stderr = old_out, old_err
    return out.getvalue(), err.getvalue(), rc


def test_cli_suggest_prints_pasteable_toml_and_writes_nothing(tmp_path):
    stdout, stderr, rc = _cli_override_streams(
        tmp_path, "suggest", "pkg/widget.py", "--reason", "issue #145 demo",
        "--minutes", "20")
    assert rc == 0
    # The pasteable TOML is on STDOUT (clean for `> arm-file`); the how-to is on
    # STDERR so a redirect captures only the file content.
    assert "until" in stdout and 'reason = "issue #145 demo"' in stdout
    assert "pkg/widget.py" in stdout
    assert "paste" in stderr.lower()
    # #148: the how-to must offer the BOM-safe PowerShell form, not only the POSIX
    # `>` (which on PowerShell 5.1 writes UTF-16+BOM the arm reader rejects). Both
    # shells are shown, and the `>`-in-PowerShell trap is called out.
    assert "Set-Content" in stderr and "-Encoding ascii" in stderr, (
        "suggest must print the BOM-safe PowerShell redirect form (#148)")
    assert "PowerShell" in stderr and "POSIX" in stderr
    # IT WROTE NOTHING — the arm file does not exist.
    assert not ovr.arm_path(tmp_path).exists()
    # And the stdout is a real, reader-acceptable arm file.
    ovr.arm_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    ovr.arm_path(tmp_path).write_text(stdout, encoding="utf-8")
    facts = ovr.read_override(tmp_path)
    assert facts is not None and facts.reason == "issue #145 demo"
    assert facts.scope == ("pkg/widget.py",)


def test_cli_suggest_leaves_status_disarmed_LITMUS_no_agent_arm_path(tmp_path):
    """The load-bearing security property (issue #145, docs/296): the new verb
    is NOT an arm path. A headless invocation prints text and the window stays
    DISARMED — no agent-callable surface ever armed it."""
    # Before: disarmed.
    out, rc = _cli_override(tmp_path, "status")
    assert rc == 1 and json.loads(out)["armed"] is False
    # An agent (headless, no tty) calls suggest as many times as it likes.
    for _ in range(3):
        _cli_override_streams(tmp_path, "suggest", "--reason", "agent tried")
    # After: STILL disarmed — suggest never wrote the guarded file.
    out, rc = _cli_override(tmp_path, "status")
    assert rc == 1 and json.loads(out)["armed"] is False
    assert not ovr.arm_path(tmp_path).exists()


def test_cli_suggest_json_form(tmp_path):
    stdout, _stderr, rc = _cli_override_streams(
        tmp_path, "suggest", "pkg/a.py", "pkg/b.py", "--reason", "json test",
        "--minutes", "5", "--json")
    assert rc == 0
    body = json.loads(stdout)
    assert body["minutes"] == 5 and body["reason"] == "json test"
    assert body["scope"] == ["pkg/a.py", "pkg/b.py"]
    assert "until" in body and body["toml"].startswith("#")
    # Even the JSON form wrote nothing.
    assert not ovr.arm_path(tmp_path).exists()


def test_cli_suggest_requires_a_reason(tmp_path):
    import pytest
    with pytest.raises(SystemExit) as exc:
        from dos import cli
        cli.main(["override", "--workspace", str(tmp_path), "suggest"])
    assert exc.value.code != 0  # --reason is required
