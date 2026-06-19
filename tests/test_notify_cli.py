"""`dos notify` CLI wiring (docs/225) — exit codes + null-default safety.

Drives `cmd_notify` directly with a fake argparse namespace and a monkeypatched
notifier resolver, so NOTHING touches Slack. Pins the safe-by-default contract:
the default `null` notifier renders + sends nothing (exit 0), an unknown notifier
fails loud (exit 2), and a REAL transport that fails to deliver exits 1 (so a cron
notices) while a dry-run never does.
"""

from __future__ import annotations

import argparse
import io

from dos import cli
from dos.notify import NotifyResult


def _ns(**kw):
    base = dict(
        workspace=".", notify_cmd="decisions", notifier="null", channel="",
        dry_run=False, json=False, all=False, top=5,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_null_default_sends_nothing_exit_0(capsys, monkeypatch):
    # Avoid touching a real workspace's journal: stub the readers to empty.
    import dos.decisions as d
    monkeypatch.setattr(d, "collect_decisions", lambda *a, **k: [])
    monkeypatch.setattr(d, "render_list_plain", lambda rows: "(none)")
    rc = cli.cmd_notify(_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert "not sent (notifier=null" in out


def test_unknown_notifier_exits_2(capsys, monkeypatch):
    import dos.decisions as d
    monkeypatch.setattr(d, "collect_decisions", lambda *a, **k: [])
    monkeypatch.setattr(d, "render_list_plain", lambda rows: "(none)")
    rc = cli.cmd_notify(_ns(notifier="nope"))
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown notifier 'nope'" in err


class _FakeNotifier:
    name = "fake"

    def __init__(self, delivered):
        self._delivered = delivered

    def send(self, note):
        return NotifyResult(delivered=self._delivered, detail="fake")


def test_real_transport_failure_exits_1(monkeypatch):
    import dos.decisions as d
    import dos.notify as n
    monkeypatch.setattr(d, "collect_decisions", lambda *a, **k: [])
    monkeypatch.setattr(d, "render_list_plain", lambda rows: "(none)")
    monkeypatch.setattr(n, "resolve_notifier",
                        lambda name, **kw: _FakeNotifier(delivered=False))
    rc = cli.cmd_notify(_ns(notifier="fake", channel="C0AAA"))
    assert rc == 1  # a real send that did NOT land → non-zero (cron-alertable)


def test_real_transport_success_exits_0(monkeypatch):
    import dos.decisions as d
    import dos.notify as n
    monkeypatch.setattr(d, "collect_decisions", lambda *a, **k: [])
    monkeypatch.setattr(d, "render_list_plain", lambda rows: "(none)")
    monkeypatch.setattr(n, "resolve_notifier",
                        lambda name, **kw: _FakeNotifier(delivered=True))
    rc = cli.cmd_notify(_ns(notifier="fake", channel="C0AAA"))
    assert rc == 0


def test_dry_run_real_transport_exits_0(monkeypatch):
    # A dry-run never delivers, but must NOT exit non-zero (it's a success no-op).
    import dos.decisions as d
    import dos.notify as n
    monkeypatch.setattr(d, "collect_decisions", lambda *a, **k: [])
    monkeypatch.setattr(d, "render_list_plain", lambda rows: "(none)")
    monkeypatch.setattr(n, "resolve_notifier",
                        lambda name, **kw: _FakeNotifier(delivered=False))
    rc = cli.cmd_notify(_ns(notifier="fake", channel="C0AAA", dry_run=True))
    assert rc == 0


def test_json_output_shape(capsys, monkeypatch):
    import dos.decisions as d
    monkeypatch.setattr(d, "collect_decisions", lambda *a, **k: [])
    monkeypatch.setattr(d, "render_list_plain", lambda rows: "(none)")
    rc = cli.cmd_notify(_ns(json=True))
    out = capsys.readouterr().out
    assert rc == 0
    import json as _json
    obj = _json.loads(out)
    assert set(obj) == {"notification", "result", "notifier"}
    assert obj["notifier"] == "null"
    assert obj["notification"]["source"] == "decisions"


def test_top_surface_builds_from_snapshot(capsys, monkeypatch):
    import dos.dispatch_top as dt
    from dos.dispatch_top import Frame

    fake_frame = Frame(workspace="/ws", now_iso="2026-06-07T00:00:00+00:00")
    monkeypatch.setattr(dt, "snapshot", lambda *a, **k: fake_frame)
    monkeypatch.setattr(dt, "render_frame_text", lambda f: "SCREEN")
    rc = cli.cmd_notify(_ns(notify_cmd="top"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "fleet:" in out


# ---------------------------------------------------------------------------
# The clickable follow-up (docs/387) — the open line in plain + JSON output.
# ---------------------------------------------------------------------------


def test_plain_output_prints_the_open_line(capsys, monkeypatch):
    import dos.decisions as d
    monkeypatch.setattr(d, "collect_decisions", lambda *a, **k: [])
    monkeypatch.setattr(d, "render_list_plain", lambda rows: "(none)")
    rc = cli.cmd_notify(_ns())
    out = capsys.readouterr().out
    assert rc == 0
    # capsys is not a tty → no escape codes, just the copy-pasteable verb.
    assert "→ review the decisions queue: dos decisions" in out
    assert "\x1b]8" not in out


def test_json_output_carries_the_action(capsys, monkeypatch):
    import dos.decisions as d
    monkeypatch.setattr(d, "collect_decisions", lambda *a, **k: [])
    monkeypatch.setattr(d, "render_list_plain", lambda rows: "(none)")
    rc = cli.cmd_notify(_ns(json=True))
    out = capsys.readouterr().out
    import json as _json
    obj = _json.loads(out)
    assert rc == 0
    action = obj["notification"]["action"]
    assert action is not None
    assert action["command"].startswith("dos decisions")


# ---------------------------------------------------------------------------
# The OSC 8 hyperlink helpers — pure, opt-out, never raise.
# ---------------------------------------------------------------------------


def test_osc8_wraps_text_in_a_link():
    s = cli._osc8("file:///ws", "dos top")
    assert s == "\x1b]8;;file:///ws\x1b\\dos top\x1b]8;;\x1b\\"


class _Tty:
    def isatty(self):
        return True


def test_hyperlinks_disabled_off_a_non_tty():
    assert cli._hyperlinks_enabled(io.StringIO()) is False


def test_hyperlinks_enabled_on_a_tty(monkeypatch):
    monkeypatch.delenv("DOS_NO_HYPERLINKS", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert cli._hyperlinks_enabled(_Tty()) is True


def test_hyperlinks_opt_out_env(monkeypatch):
    monkeypatch.setenv("DOS_NO_HYPERLINKS", "1")
    assert cli._hyperlinks_enabled(_Tty()) is False


def test_action_line_links_when_enabled_else_plain():
    from dos.notify import NotifyAction
    a = NotifyAction(label="open", command="dos top", url="https://x/ui")
    linked = cli._action_line(a, hyperlinks=True)
    assert "\x1b]8;;https://x/ui\x1b\\dos top" in linked
    plain = cli._action_line(a, hyperlinks=False)
    assert plain == "  → open: dos top"


def test_action_line_falls_back_to_workspace_uri(tmp_path):
    from dos.notify import NotifyAction
    a = NotifyAction(label="open", command="dos decisions")  # no explicit url
    linked = cli._action_line(a, root=str(tmp_path), hyperlinks=True)
    assert "\x1b]8;;file://" in linked  # the workspace file:// became the target
