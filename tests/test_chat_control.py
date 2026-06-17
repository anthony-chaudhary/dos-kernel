"""Tests for `dos.chat_control` — the generic, transport-agnostic control surface.

The router maps a chat line → a read-only DOS projection → compact text. These
pin the contract that matters: it answers the observe verbs, it is read-only by
CONSTRUCTION (the verb set is closed), it tolerates the prefixes people type, it
fails soft (a broken projection never crashes the surface), and it shapes output
for a phone (line + char caps with an honest marker). No transport here — that
is the point: the surface names no vendor.

Pure throughout: a `default_config(tmp_path)` workspace with no DOS state at all
(the projections all degrade to a renderable empty, the fresh-repo contract).
"""

from __future__ import annotations

from pathlib import Path

from dos import chat_control as CC
from dos.config import default_config


def _cfg(tmp_path: Path):
    return default_config(tmp_path)


# ---------------------------------------------------------------------------
# The verbs answer — each returns a typed reply on a brand-new workspace.
# ---------------------------------------------------------------------------


def test_top_answers_on_a_bare_workspace(tmp_path):
    reply = CC.handle("top", _cfg(tmp_path))
    assert reply.command == "top"
    assert reply.ok is True
    assert reply.text  # a renderable frame even with no DOS state


def test_decisions_answers(tmp_path):
    reply = CC.handle("decisions", _cfg(tmp_path))
    assert reply.command == "decisions"
    assert reply.ok is True
    assert reply.text  # empty queue → the "clear" line, never blank


def test_plan_answers(tmp_path):
    reply = CC.handle("plan", _cfg(tmp_path))
    assert reply.command == "plan"
    assert reply.ok is True


def test_doctor_reports_version_and_lanes(tmp_path):
    reply = CC.handle("doctor", _cfg(tmp_path))
    assert reply.command == "doctor"
    assert reply.ok is True
    assert "DOS v" in reply.text
    assert "lanes" in reply.text


def test_help_lists_the_commands(tmp_path):
    reply = CC.handle("help", _cfg(tmp_path))
    assert reply.command == "help"
    assert reply.ok is True
    for verb in ("top", "decisions", "plan", "doctor"):
        assert verb in reply.text


def test_empty_message_shows_the_menu(tmp_path):
    reply = CC.handle("   ", _cfg(tmp_path))
    assert reply.command == "help"
    assert reply.ok is True


# ---------------------------------------------------------------------------
# Unknown verb → the menu, flagged ok=False.
# ---------------------------------------------------------------------------


def test_unknown_command_returns_menu_not_ok(tmp_path):
    reply = CC.handle("frobnicate", _cfg(tmp_path))
    assert reply.command == "unknown"
    assert reply.ok is False
    assert "unknown command" in reply.text
    assert "top" in reply.text  # the menu rides along


# ---------------------------------------------------------------------------
# Aliases + prefix tolerance — what a real person types.
# ---------------------------------------------------------------------------


def test_aliases_resolve(tmp_path):
    cfg = _cfg(tmp_path)
    assert CC.handle("fleet", cfg).command == "top"
    assert CC.handle("q", cfg).command == "decisions"
    assert CC.handle("?", cfg).command == "help"
    assert CC.handle("health", cfg).command == "doctor"


def test_prefixes_are_tolerated(tmp_path):
    cfg = _cfg(tmp_path)
    assert CC.handle("/top", cfg).command == "top"
    assert CC.handle("!top", cfg).command == "top"
    assert CC.handle("dos top", cfg).command == "top"
    assert CC.handle("  TOP  ", cfg).command == "top"  # case + whitespace


# ---------------------------------------------------------------------------
# Fail-soft — a broken projection degrades to a one-line error, never a crash.
# ---------------------------------------------------------------------------


def test_broken_projection_is_caught(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("torn journal")

    monkeypatch.setattr("dos.dispatch_top.snapshot", _boom)
    reply = CC.handle("top", _cfg(tmp_path))
    assert reply.command == "top"
    assert reply.ok is False
    assert "unavailable" in reply.text
    assert "torn journal" in reply.text


# ---------------------------------------------------------------------------
# Phone-shaping — line cap + char cap with an honest truncation marker.
# ---------------------------------------------------------------------------


def test_compact_caps_lines_with_marker():
    body = "\n".join(f"row {i}" for i in range(100))
    out = CC._compact(body, max_lines=10, max_chars=10_000)
    assert out.count("\n") <= 10  # 10 rows + the marker line
    assert "more line" in out
    assert "+90" in out


def test_compact_caps_chars():
    body = "x" * 5000
    out = CC._compact(body, max_lines=1, max_chars=200)
    assert len(out) <= 201
    assert out.endswith("…")


def test_compact_empty_is_not_blank():
    assert CC._compact("", max_lines=10, max_chars=100) == "(empty)"


def test_handle_respects_max_lines(tmp_path, monkeypatch):
    monkeypatch.setattr("dos.dispatch_top.snapshot", lambda *a, **k: object())
    monkeypatch.setattr(
        "dos.dispatch_top.render_frame_text",
        lambda frame: "\n".join(f"line {i}" for i in range(200)))
    reply = CC.handle("top", _cfg(tmp_path), max_lines=5)
    assert reply.ok is True
    assert reply.text.count("\n") <= 5
    assert "more line" in reply.text


# ---------------------------------------------------------------------------
# The READ-ONLY invariant — the verb set is closed; no mutating verb can sneak in.
# ---------------------------------------------------------------------------


def test_command_set_is_exactly_the_readonly_verbs():
    names = {c.name for c in CC.commands()}
    assert names == {"top", "decisions", "plan", "doctor", "help"}


def test_no_mutating_verb_is_reachable():
    # A stranger who guesses your number must not be able to DRIVE the fleet.
    for mutating in ("arbitrate", "spawn", "reap", "commit", "lease",
                     "refuse", "improve", "reward", "apply", "resume"):
        assert CC._BY_NAME.get(mutating) is None


# ---------------------------------------------------------------------------
# to_dict — the machine-readable shape (`dos chat --json`).
# ---------------------------------------------------------------------------


def test_reply_to_dict_shape(tmp_path):
    d = CC.handle("doctor", _cfg(tmp_path)).to_dict()
    assert set(d) == {"text", "command", "ok"}
    assert d["command"] == "doctor"
    assert d["ok"] is True


# ---------------------------------------------------------------------------
# The chat-bridge seam — resolve an inbound transport by name, loud on unknown.
# ---------------------------------------------------------------------------


def test_resolve_bridge_unknown_raises():
    import pytest

    with pytest.raises(ValueError):
        CC.resolve_bridge("not-a-registered-bridge")
