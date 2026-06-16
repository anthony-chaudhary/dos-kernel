"""`dos hook stop` — the verify-on-stop hook (docs/134 §2/§2.2, docs/165 §2).

The wiring the pure-extractor tests can't see: stdin event JSON in → extract the
claimed (plan, phase) → verify against git → BLOCK on a NOT_SHIPPED confident
claim, else exit 0 with nothing to block. Every failure mode (no stdin, no
transcript, no claim, an already-active stop) degrades to "let the agent stop" —
the fail-safe direction.

The block is emitted in TWO surfaces, pinned by the two halves below:
  - DEFAULT (no --json): the EXACT Claude-Code Stop dialect
    `{"decision": "block", "reason": …}` — the bytes real CC parses to decline a
    stop. The historical `{"ok": false}` was silently IGNORED by CC (docs/165 §2);
    the default now emits the dialect that actually keeps the agent working.
  - --json: the rich `{"ok": …, "reason"?, "results"}` object — a machine-readable
    surface for tooling / non-CC hosts, NOT the bytes CC reads.

These run against a throwaway git repo so the oracle has a real (no-plan) ground
truth: a phase whose ship-stamp commit exists verifies SHIPPED; a made-up phase
resolves NOT_SHIPPED (via none).
"""

from __future__ import annotations

import io
import json
import subprocess

import pytest

from dos import cli


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A plain git repo with one shipped phase (FOO1) and no plan/registry."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.py").write_text("def f(): ...\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "FOO1: ship the foo")
    return tmp_path


def _transcript(tmp_path, text, name="t.jsonl"):
    p = tmp_path / name
    rec = {"type": "assistant", "message": {"role": "assistant",
           "content": [{"type": "text", "text": text}]}}
    p.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    return str(p)


def _run_hook(monkeypatch, capsys, event, *extra_args):
    """Drive `dos hook stop` with `event` on stdin; return (rc, parsed_stdout)."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    rc = cli.main(["hook", "stop", "--json", *extra_args])
    out = capsys.readouterr().out.strip()
    return rc, (json.loads(out) if out else None)


# ---------------------------------------------------------------------------
# The two headline paths: a true claim passes, a false claim blocks.
# ---------------------------------------------------------------------------
def test_true_claim_does_not_block(repo, monkeypatch, capsys):
    tx = _transcript(repo, "All done.\nDOS-CLAIM: FOO FOO1")
    event = {"transcript_path": tx, "cwd": str(repo)}
    rc, out = _run_hook(monkeypatch, capsys, event, "--workspace", str(repo))
    assert rc == 0
    assert out["ok"] is True
    assert out["checked"] == 1
    assert out["results"][0]["shipped"] is True


def test_false_claim_blocks_with_reason(repo, monkeypatch, capsys):
    tx = _transcript(repo, "Finished.\nDOS-CLAIM: NOPE PHASE9")
    event = {"transcript_path": tx, "cwd": str(repo)}
    rc, out = _run_hook(monkeypatch, capsys, event, "--workspace", str(repo))
    # exit 0 + a block is the host's "keep working" signal (NOT exit 2); the --json
    # surface reports it as {"ok": false}. (The DEFAULT surface emits CC's
    # {"decision":"block"} dialect — pinned by the default-surface tests below.)
    assert rc == 0
    assert out["ok"] is False
    assert "NOPE PHASE9" in out["reason"]
    assert "via none" in out["reason"]
    assert out["results"][0]["shipped"] is False


def test_mixed_claims_block_on_the_unshipped_one(repo, monkeypatch, capsys):
    tx = _transcript(repo, "DOS-CLAIM: FOO FOO1\nDOS-CLAIM: NOPE PHASE9")
    event = {"transcript_path": tx, "cwd": str(repo)}
    rc, out = _run_hook(monkeypatch, capsys, event, "--workspace", str(repo))
    assert out["ok"] is False
    assert "NOPE PHASE9" in out["reason"]
    assert "FOO FOO1" not in out["reason"]  # the shipped one is not in the failure list


# ---------------------------------------------------------------------------
# The frontmatter rung (explicit flags, no transcript needed).
# ---------------------------------------------------------------------------
def test_frontmatter_flags_block_an_unshipped_phase(repo, monkeypatch, capsys):
    rc, out = _run_hook(monkeypatch, capsys, {},
                        "--workspace", str(repo), "--plan", "NOPE", "--phase", "X9")
    assert out["ok"] is False
    assert "NOPE X9" in out["reason"]


def test_frontmatter_flags_pass_a_shipped_phase(repo, monkeypatch, capsys):
    rc, out = _run_hook(monkeypatch, capsys, {},
                        "--workspace", str(repo), "--plan", "FOO", "--phase", "FOO1")
    assert out["ok"] is True


# ---------------------------------------------------------------------------
# The abstain / fail-safe behaviors — all must "let the agent stop".
# ---------------------------------------------------------------------------
def test_no_claim_lets_agent_stop(repo, monkeypatch, capsys):
    tx = _transcript(repo, "All done! Everything works.")  # pure prose, no marker
    event = {"transcript_path": tx, "cwd": str(repo)}
    rc, out = _run_hook(monkeypatch, capsys, event, "--workspace", str(repo))
    assert rc == 0
    assert out["ok"] is True
    assert out["checked"] == 0


def test_missing_transcript_lets_agent_stop(repo, monkeypatch, capsys):
    event = {"transcript_path": str(repo / "does-not-exist.jsonl"), "cwd": str(repo)}
    rc, out = _run_hook(monkeypatch, capsys, event, "--workspace", str(repo))
    assert rc == 0
    assert out["ok"] is True


def test_empty_stdin_lets_agent_stop(repo, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    rc = cli.main(["hook", "stop", "--json", "--workspace", str(repo)])
    out = json.loads(capsys.readouterr().out.strip())
    assert rc == 0 and out["ok"] is True


def test_heuristic_claim_is_advisory_unless_strict(repo, monkeypatch, capsys):
    # a heuristic-only NOT_SHIPPED must NOT block by default (low confidence),
    # but MUST block under --strict
    tx = _transcript(repo, "I shipped NOPE9 just now")  # NOPE9 = ID-shaped, unshipped
    event = {"transcript_path": tx, "cwd": str(repo)}

    rc, out = _run_hook(monkeypatch, capsys, event, "--workspace", str(repo))
    # default: heuristic rung not even extracted (allow_heuristic=False) → no claim
    assert out["ok"] is True and out["checked"] == 0

    rc, out = _run_hook(monkeypatch, capsys, event, "--workspace", str(repo), "--strict")
    # strict: the heuristic claim is extracted AND actionable → blocks
    assert out["ok"] is False
    assert "NOPE9" in out["reason"]


def test_workspace_falls_back_to_event_cwd(repo, monkeypatch, capsys):
    # no --workspace flag → resolve from the event's cwd
    tx = _transcript(repo, "DOS-CLAIM: NOPE PHASE9")
    event = {"transcript_path": tx, "cwd": str(repo)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    cli.main(["hook", "stop", "--json"])
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is False  # it found the repo via cwd and verified the claim


def test_torn_config_load_lets_agent_stop_without_crashing(repo, monkeypatch, capsys):
    # issue #206: a SIBLING loop mid-editing a kernel leaf (config.py) leaves a torn
    # write on disk that raises AttributeError out of `load_workspace_config` (the
    # documented `cfg.vcs_backend` crash). A kernel reader — the Stop hook is itself
    # part of the trust substrate — must NOT hard-crash the host's stop on that: a
    # SystemExit traceback in the operator's terminal is strictly worse than letting
    # the agent stop. Fail-to-abstain (judges.py's safe-failure direction) applied to
    # the substrate's own config load. Fails-unpatched (the AttributeError used to
    # propagate straight up cmd_hook_stop); passes-patched (degrades to exit 0 + a
    # one-line stderr note). The error class stands in for any torn-edit failure
    # (AttributeError / ImportError / ValueError) out of the config load.
    def boom(*a, **k):
        raise AttributeError(
            "'SubstrateConfig' object has no attribute 'vcs_backend'")
    monkeypatch.setattr("dos.config.load_workspace_config", boom)

    tx = _transcript(repo, "DOS-CLAIM: NOPE PHASE9")  # a claim that WOULD block
    event = {"transcript_path": tx, "cwd": str(repo)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    # No exception escapes; the agent is allowed to stop (the abstain shape).
    rc = cli.main(["hook", "stop", "--json", "--workspace", str(repo)])
    captured = capsys.readouterr()  # one drain — both streams
    out = json.loads(captured.out.strip())
    assert rc == 0
    assert out["ok"] is True       # abstain, not a block
    assert out["checked"] == 0     # nothing was checked — we couldn't read the workspace
    # The one-line note went to stderr (a diagnostic, not the host-parsed stdout).
    assert "could not load the workspace config" in captured.err


def test_torn_config_load_default_surface_emits_nothing(repo, monkeypatch, capsys):
    # The DEFAULT (CC-dialect) surface on the same torn-config abstain: a non-block
    # emits NOTHING to stdout (an empty stdout is CC's "let it stop"), and never a
    # traceback. The companion to the --json witness above on the bytes CC parses.
    def boom(*a, **k):
        raise ImportError("partially initialized module 'dos.config'")
    monkeypatch.setattr("dos.config.load_workspace_config", boom)

    tx = _transcript(repo, "DOS-CLAIM: NOPE PHASE9")
    event = {"transcript_path": tx, "cwd": str(repo)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    rc = cli.main(["hook", "stop", "--workspace", str(repo)])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.strip() == ""  # no block object → the agent stops
    assert "could not load the workspace config" in captured.err


# ---------------------------------------------------------------------------
# The DEFAULT surface (no --json): the EXACT Claude-Code Stop dialect. This is
# the load-bearing fix (docs/165 §2) — the bytes real CC parses to block a stop.
# ---------------------------------------------------------------------------
def _run_hook_default(monkeypatch, capsys, event, *extra_args):
    """Drive `dos hook stop` WITHOUT --json; return (rc, parsed_stdout_or_None)."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    rc = cli.main(["hook", "stop", *extra_args])
    out = capsys.readouterr().out.strip()
    return rc, (json.loads(out) if out else None)


def test_default_surface_emits_cc_block_dialect_on_a_false_claim(repo, monkeypatch, capsys):
    tx = _transcript(repo, "Finished.\nDOS-CLAIM: NOPE PHASE9")
    event = {"transcript_path": tx, "cwd": str(repo)}
    rc, out = _run_hook_default(monkeypatch, capsys, event, "--workspace", str(repo))
    assert rc == 0
    # This is the contract CC honors: top-level decision=block + reason (NOT an
    # `ok` field, NOT a `hookSpecificOutput` object — CC's Stop schema is strict).
    assert out == {"decision": "block", "reason": out["reason"]}
    assert out["decision"] == "block"
    assert "NOPE PHASE9" in out["reason"] and "via none" in out["reason"]
    assert "ok" not in out
    assert "hookSpecificOutput" not in out


def test_default_surface_emits_nothing_on_a_true_claim(repo, monkeypatch, capsys):
    tx = _transcript(repo, "All done.\nDOS-CLAIM: FOO FOO1")
    event = {"transcript_path": tx, "cwd": str(repo)}
    rc, out = _run_hook_default(monkeypatch, capsys, event, "--workspace", str(repo))
    # CC reads an empty Stop output as "allow the stop" — so a verified claim
    # prints NOTHING (no block dict, no {"ok": true}).
    assert rc == 0
    assert out is None


# ---------------------------------------------------------------------------
# The --dialect surface: a stop refusal must render in the HOST's stop-blocking
# grammar, not the pre-tool grammar. This is the fail-open the stop verb shipped
# with — it built the verdict at moment=PRE, so `--dialect gemini` emitted
# {"continue":false} (the BeforeTool gate) which Gemini's AfterAgent IGNORES, so the
# agent stopped despite the refusal. The fix uses HookMoment.STOP (docs/268).
# ---------------------------------------------------------------------------
def test_dialect_gemini_stop_block_is_decision_block_not_continue_false(repo, monkeypatch, capsys):
    """`dos hook stop --dialect gemini` on a false claim must emit the AfterAgent
    blocking shape {"decision":"block"}, NEVER the BeforeTool {"continue":false}
    (which AfterAgent does not consult — a silent fail-open)."""
    tx = _transcript(repo, "Finished.\nDOS-CLAIM: NOPE PHASE9")
    event = {"transcript_path": tx, "cwd": str(repo)}
    rc, out = _run_hook_default(monkeypatch, capsys, event,
                                "--workspace", str(repo), "--dialect", "gemini")
    assert rc == 0
    assert out is not None, "a false-claim stop refusal must emit SOMETHING (not fail-open)"
    assert out.get("decision") == "block", f"expected decision:block, got {out}"
    assert "continue" not in out, "continue:false is the BeforeTool gate — wrong for a stop"
    assert "NOPE PHASE9" in out.get("reason", "")


def test_dialect_cursor_stop_block_is_permission_deny(repo, monkeypatch, capsys):
    """Cursor's stop refusal rides {"permission":"deny"} — pin it so the stop verb's
    dialect render is exercised for a second non-CC host, not just gemini."""
    tx = _transcript(repo, "Finished.\nDOS-CLAIM: NOPE PHASE9")
    event = {"transcript_path": tx, "cwd": str(repo)}
    rc, out = _run_hook_default(monkeypatch, capsys, event,
                                "--workspace", str(repo), "--dialect", "cursor")
    assert rc == 0
    assert out is not None and out.get("permission") == "deny", out


def test_dialect_hermes_stop_block_is_decision_block(repo, monkeypatch, capsys):
    """`dos hook stop --dialect hermes` on a false claim must emit Hermes' block shape
    {"decision":"block","reason":…} — proving the --dialect flag reaches the
    HermesDialect renderer end-to-end through the CLI (docs/278). Hermes' stop-block
    shape coincides with Claude-Code's, but routing it through the dialect seam is what
    a real Hermes `pre_tool_call`/session hook consumes, and pins it against a drift."""
    if "hermes" not in __import__("dos.hook_dialect", fromlist=["x"]).available_dialects():
        import pytest
        pytest.skip("the dos.hook_dialects:hermes entry point is not registered "
                    "(run `pip install -e .` — docs/278)")
    tx = _transcript(repo, "Finished.\nDOS-CLAIM: NOPE PHASE9")
    event = {"transcript_path": tx, "cwd": str(repo)}
    rc, out = _run_hook_default(monkeypatch, capsys, event,
                                "--workspace", str(repo), "--dialect", "hermes")
    assert rc == 0
    assert out is not None, "a false-claim stop refusal must emit SOMETHING (not fail-open)"
    assert out.get("decision") == "block", f"expected decision:block, got {out}"
    assert "continue" not in out, "continue:false is the BeforeTool gate — wrong for a stop"
    assert "NOPE PHASE9" in out.get("reason", "")


def test_default_surface_emits_nothing_when_no_claim(repo, monkeypatch, capsys):
    tx = _transcript(repo, "All done! Everything works.")  # pure prose, no marker
    event = {"transcript_path": tx, "cwd": str(repo)}
    rc, out = _run_hook_default(monkeypatch, capsys, event, "--workspace", str(repo))
    assert rc == 0 and out is None


# ---------------------------------------------------------------------------
# The anti-loop guard: stop_hook_active means CC is ALREADY in a forced
# continuation from a prior block — one push-back per work stretch, then let it
# stop, or we trap the agent in an infinite no-stop loop.
# ---------------------------------------------------------------------------
def test_stop_hook_active_lets_agent_stop_even_on_a_false_claim(repo, monkeypatch, capsys):
    tx = _transcript(repo, "Finished.\nDOS-CLAIM: NOPE PHASE9")
    # Same false claim that WOULD block — but the event says we already blocked once.
    event = {"transcript_path": tx, "cwd": str(repo), "stop_hook_active": True}
    # default surface: nothing (let it stop)
    rc, out = _run_hook_default(monkeypatch, capsys, event, "--workspace", str(repo))
    assert rc == 0 and out is None
    # --json surface: an explicit ok=true (did not block) so a tool can see why
    rc, out = _run_hook(monkeypatch, capsys, event, "--workspace", str(repo))
    assert rc == 0 and out["ok"] is True and out["checked"] == 0


def test_force_overrides_the_anti_loop_guard(repo, monkeypatch, capsys):
    tx = _transcript(repo, "Finished.\nDOS-CLAIM: NOPE PHASE9")
    event = {"transcript_path": tx, "cwd": str(repo), "stop_hook_active": True}
    # --force ignores stop_hook_active and blocks anyway (host owns the loop).
    rc, out = _run_hook_default(monkeypatch, capsys, event,
                                "--workspace", str(repo), "--force")
    assert rc == 0
    assert out["decision"] == "block"
    assert "NOPE PHASE9" in out["reason"]
