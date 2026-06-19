"""The Slack notifier driver (`dos.drivers.notify_slack`) — fake client, no network.

Proves the driver's contract WITHOUT touching Slack: a fake `SlackClient`
records calls; tests assert post-vs-edit routing, dry-run sends nothing,
no-token / absent-extra degrade to a `NotifyResult` (never a raise), channel
resolution, and the Block-Kit shape. The real transport is exercised only by the
manual dogfood in docs/225.
"""

from __future__ import annotations

from dos.notify import Notification, Severity, send_safely
from dos.drivers.notify_slack import (
    SlackNotifier,
    build_blocks,
    resolve_channel,
    resolve_token,
)


# ---------------------------------------------------------------------------
# A fake SlackClient + LiveMessage — record calls, hit no network.
# ---------------------------------------------------------------------------


class FakeClient:
    def __init__(self):
        self.posts: list[tuple[str, str, list]] = []
        self.updates: list[tuple[str, str, str]] = []
        self._ts = 0

    def post_message(self, channel, text, blocks=None):
        self._ts += 1
        self.posts.append((channel, text, blocks or []))
        return {"ok": True, "ts": f"{self._ts}.000"}

    def update_message(self, channel, ts, text, blocks=None):
        self.updates.append((channel, ts, text))
        return {"ok": True, "ts": ts}


def _note(source="decisions", sev=Severity.WARN, key="dos-decisions"):
    return Notification(
        severity=sev, title="2 decisions need you", summary="line one\nline two",
        fields=(("ARBITER_REFUSE @ src", "LANE_BUSY"),), key=key, source=source,
    )


# ---------------------------------------------------------------------------
# build_blocks — pure Block Kit shape.
# ---------------------------------------------------------------------------


def test_build_blocks_has_header_fields_summary_context():
    blocks = build_blocks(_note())
    types = [b["type"] for b in blocks]
    assert types == ["header", "section", "section", "context"]
    assert "decisions need you" in blocks[0]["text"]["text"]
    # severity emoji present in the header
    assert ":large_yellow_circle:" in blocks[0]["text"]["text"]
    # fields section carries the row
    assert any("LANE_BUSY" in f["text"] for f in blocks[1]["fields"])
    # summary fenced
    assert "```" in blocks[2]["text"]["text"]
    # context names the source
    assert "source=`decisions`" in blocks[3]["elements"][0]["text"]


def test_build_blocks_urgent_emoji_and_no_fields():
    n = Notification(Severity.URGENT, "fleet hung", "screen", fields=(), source="top")
    blocks = build_blocks(n)
    assert ":red_circle:" in blocks[0]["text"]["text"]
    # no fields section when fields empty (and no actions block without an action)
    assert [b["type"] for b in blocks] == ["header", "section", "context"]


def test_build_blocks_action_with_url_adds_a_link_button():
    from dos.notify import NotifyAction
    a = NotifyAction(label="open the live fleet status", command="dos top", url="https://x/ui")
    n = Notification(Severity.INFO, "fleet", "screen", source="top", action=a)
    blocks = build_blocks(n)
    assert "actions" in [b["type"] for b in blocks]
    btn = [b for b in blocks if b["type"] == "actions"][0]["elements"][0]
    assert btn["type"] == "button" and btn["url"] == "https://x/ui"
    # the open-command is folded into the context line too
    assert "open: `dos top`" in blocks[-1]["elements"][0]["text"]


def test_build_blocks_action_without_url_has_no_button_but_shows_command():
    from dos.notify import NotifyAction
    a = NotifyAction(label="review the decisions queue", command="dos decisions")
    n = Notification(Severity.WARN, "3 need you", "body", source="decisions", action=a)
    blocks = build_blocks(n)
    assert "actions" not in [b["type"] for b in blocks]  # no URL → no dead button
    assert "open: `dos decisions`" in blocks[-1]["elements"][0]["text"]


# ---------------------------------------------------------------------------
# Routing — decisions digest POSTS; top status EDITS in place.
# ---------------------------------------------------------------------------


def test_decisions_digest_posts():
    fc = FakeClient()
    nt = SlackNotifier(channel="C0AJ37QHMFB", client=fc)
    r = nt.send(_note(source="decisions"))
    assert r.delivered is True
    assert r.detail.startswith("posted ts=")
    assert len(fc.posts) == 1 and fc.updates == []
    chan, text, blocks = fc.posts[0]
    assert chan == "C0AJ37QHMFB"
    assert blocks  # Block Kit attached


def test_top_status_edits_in_place_one_message():
    fc = FakeClient()
    nt = SlackNotifier(channel="C0AJ37QHMFB", client=fc)
    # The screen CHANGES between ticks (real usage); identical bodies are correctly
    # coalesced by LiveMessage, so vary the summary to force the edit.
    def top(sev, screen):
        return Notification(sev, "fleet status", screen, key="dos-top:/ws", source="top")
    # First send posts (LiveMessage's first update), second EDITS the same message.
    r1 = nt.send(top(Severity.WARN, "tick 1"))
    r2 = nt.send(top(Severity.URGENT, "tick 2"))
    assert r1.delivered is True and r2.delivered is True
    assert len(fc.posts) == 1          # exactly ONE message created
    assert len(fc.updates) == 1        # second send edited it
    assert fc.updates[0][0] == "C0AJ37QHMFB"


def test_edit_in_place_works_without_slack_helpers(monkeypatch):
    # Simulate the [notify-slack] extra being ABSENT: the edit path must still work
    # with an injected client, via the _InlineLive fallback (no hard dep on the dep).
    import builtins
    real_import = builtins.__import__

    def _no_slack_helpers(name, *a, **k):
        if name == "slack_helpers" or name.startswith("slack_helpers."):
            raise ImportError("simulated missing extra")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_slack_helpers)
    fc = FakeClient()
    nt = SlackNotifier(channel="C0AJ37QHMFB", client=fc)
    nt.send(Notification(Severity.INFO, "fleet", "tick 1", key="dos-top:/ws", source="top"))
    nt.send(Notification(Severity.WARN, "fleet", "tick 2", key="dos-top:/ws", source="top"))
    assert len(fc.posts) == 1 and len(fc.updates) == 1  # one message, edited once


def test_edit_in_place_override_forces_post():
    fc = FakeClient()
    nt = SlackNotifier(channel="C0AJ37QHMFB", client=fc, edit_in_place=False)
    nt.send(_note(source="top", key="dos-top:/ws"))
    assert len(fc.posts) == 1 and fc.updates == []  # forced post despite source=top


# ---------------------------------------------------------------------------
# dry_run — render + report, send NOTHING.
# ---------------------------------------------------------------------------


def test_dry_run_sends_nothing():
    fc = FakeClient()
    nt = SlackNotifier(channel="C0AJ37QHMFB", client=fc, dry_run=True)
    r = nt.send(_note())
    assert r.delivered is False
    assert "[dry-run]" in r.detail and "post" in r.detail
    assert fc.posts == [] and fc.updates == []


def test_dry_run_top_says_edit():
    fc = FakeClient()
    nt = SlackNotifier(channel="C0AJ37QHMFB", client=fc, dry_run=True)
    r = nt.send(_note(source="top", key="dos-top:/ws"))
    assert "[dry-run]" in r.detail and "edit-in-place" in r.detail


# ---------------------------------------------------------------------------
# Fail-soft — no token, no channel, transport raise → NotifyResult, never a raise.
# ---------------------------------------------------------------------------


def test_no_token_degrades_to_skip(monkeypatch, tmp_path):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    # No injected client, no token anywhere → ensure_client returns the reason.
    nt = SlackNotifier(channel="C0AJ37QHMFB", root=tmp_path)
    r = nt.send(_note())
    assert r.delivered is False
    assert "SLACK_BOT_TOKEN" in r.detail


def test_unknown_channel_name_degrades_to_skip():
    fc = FakeClient()
    nt = SlackNotifier(channel="not-a-known-name", client=fc)
    r = nt.send(_note())
    assert r.delivered is False
    assert "no channel" in r.detail
    assert fc.posts == []


def test_transport_raise_is_caught():
    class Boom(FakeClient):
        def post_message(self, *a, **k):
            raise RuntimeError("429 forever")

    nt = SlackNotifier(channel="C0AJ37QHMFB", client=Boom())
    r = nt.send(_note())
    assert r.delivered is False
    assert "error: 429 forever" in r.detail


def test_send_safely_wraps_the_driver_too():
    # Belt-and-suspenders: the seam's outer net over the driver's inner net.
    class Boom(FakeClient):
        def post_message(self, *a, **k):
            raise RuntimeError("nope")

    nt = SlackNotifier(channel="C0AJ37QHMFB", client=Boom())
    r = send_safely(nt, _note())
    assert r.delivered is False


# ---------------------------------------------------------------------------
# Credential / routing helpers.
# ---------------------------------------------------------------------------


def test_resolve_token_prefers_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env")
    assert resolve_token("xoxb-explicit", root=tmp_path) == "xoxb-explicit"


def test_resolve_token_falls_back_to_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env")
    assert resolve_token(None, root=tmp_path) == "xoxb-env"


def test_resolve_token_reads_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    (tmp_path / ".env").write_text('SLACK_BOT_TOKEN="xoxb-fromfile"\n', encoding="utf-8")
    assert resolve_token(None, root=tmp_path) == "xoxb-fromfile"


def test_resolve_channel_raw_id_passthrough():
    assert resolve_channel("C0AJ37QHMFB") == "C0AJ37QHMFB"


def test_resolve_channel_unknown_name_is_empty():
    # A logical name not in the config map → "" (the caller skips, fail-soft).
    assert resolve_channel("definitely-not-configured-xyz") == ""


def test_resolve_channel_blank_is_empty():
    assert resolve_channel("") == ""
    assert resolve_channel("   ") == ""
