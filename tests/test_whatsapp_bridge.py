"""The WhatsApp inbound bridge (`dos.drivers.whatsapp_bridge`) — pure core, no socket.

Proves the inbound contract WITHOUT binding a port: the GET verification
handshake, parsing a real Cloud-API webhook envelope into typed messages (and
skipping non-text / status callbacks / garbage), routing a message through the
vendor-blind `chat_control` and sending the reply via an injected notifier, and
the always-200 / fail-soft POST response (a bad body is a 400, a per-message
failure is swallowed). `serve` (the only socket-touching function) is exercised
by manual dogfood.
"""

from __future__ import annotations

import json
from pathlib import Path

from dos.config import default_config
from dos.drivers import whatsapp_bridge as WB
from dos.notify import NotifyResult


def _cfg(tmp_path: Path):
    return default_config(tmp_path)


# A realistic Meta Cloud-API inbound webhook body.
def _inbound(text="top", sender="15551234567", phone_id="PID999"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "1800", "phone_number_id": phone_id},
                    "contacts": [{"profile": {"name": "Op"}, "wa_id": sender}],
                    "messages": [{
                        "from": sender,
                        "id": "wamid.ABC",
                        "timestamp": "1700000000",
                        "type": "text",
                        "text": {"body": text},
                    }],
                },
            }],
        }],
    }


class FakeNotifier:
    name = "fake"

    def __init__(self):
        self.sent = []

    def send(self, note):
        self.sent.append(note)
        return NotifyResult(delivered=True, detail="fake-sent", ref="1")


# ---------------------------------------------------------------------------
# verify_challenge — the GET handshake.
# ---------------------------------------------------------------------------


def test_verify_challenge_echoes_on_match():
    params = {"hub.mode": "subscribe", "hub.verify_token": "sekret", "hub.challenge": "12345"}
    assert WB.verify_challenge(params, "sekret") == "12345"


def test_verify_challenge_rejects_wrong_token():
    params = {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "12345"}
    assert WB.verify_challenge(params, "sekret") is None


def test_verify_challenge_rejects_empty_expected():
    params = {"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "12345"}
    assert WB.verify_challenge(params, "") is None


def test_respond_to_get():
    ok = WB.respond_to_get(
        {"hub.mode": "subscribe", "hub.verify_token": "s", "hub.challenge": "c"},
        verify_token="s")
    assert ok == (200, "c")
    bad = WB.respond_to_get({"hub.mode": "subscribe", "hub.verify_token": "x"}, verify_token="s")
    assert bad[0] == 403


# ---------------------------------------------------------------------------
# parse_inbound — lift the text messages out, skip the rest.
# ---------------------------------------------------------------------------


def test_parse_inbound_extracts_a_text_message():
    msgs = WB.parse_inbound(_inbound(text="decisions", sender="1555"))
    assert len(msgs) == 1
    m = msgs[0]
    assert m.sender == "1555"
    assert m.text == "decisions"
    assert m.msg_id == "wamid.ABC"
    assert m.phone_number_id == "PID999"


def test_parse_inbound_skips_non_text():
    payload = _inbound()
    payload["entry"][0]["changes"][0]["value"]["messages"][0] = {
        "from": "1555", "id": "x", "type": "image", "image": {"id": "media"}}
    assert WB.parse_inbound(payload) == []


def test_parse_inbound_skips_status_callbacks():
    # A delivery receipt carries `statuses`, not `messages`.
    payload = {
        "entry": [{"changes": [{"value": {
            "statuses": [{"id": "wamid.X", "status": "delivered"}]}}]}]}
    assert WB.parse_inbound(payload) == []


def test_parse_inbound_tolerates_garbage():
    for junk in (None, {}, [], "nope", {"entry": "x"}, {"entry": [None]},
                 {"entry": [{"changes": [None]}]}):
        assert WB.parse_inbound(junk) == []


# ---------------------------------------------------------------------------
# handle_message — route through chat_control, reply via the injected notifier.
# ---------------------------------------------------------------------------


def test_handle_message_routes_and_replies(tmp_path):
    fake = FakeNotifier()
    msg = WB.InboundMessage(sender="1555", text="doctor", phone_number_id="PID")
    reply, result = WB.handle_message(
        msg, _cfg(tmp_path), notifier_factory=lambda m, c: fake)
    assert reply.command == "doctor"
    assert result.delivered is True
    # the reply was sent as a title-LESS note carrying the control answer
    assert len(fake.sent) == 1
    note = fake.sent[0]
    assert note.title == ""
    assert "DOS v" in note.summary


def test_handle_message_unknown_command_still_replies(tmp_path):
    fake = FakeNotifier()
    msg = WB.InboundMessage(sender="1555", text="xyzzy")
    reply, result = WB.handle_message(
        msg, _cfg(tmp_path), notifier_factory=lambda m, c: fake)
    assert reply.command == "unknown"
    assert result.delivered is True  # an unknown command still gets a (menu) reply


# ---------------------------------------------------------------------------
# respond_to_post — always-200, fail-soft.
# ---------------------------------------------------------------------------


def test_respond_to_post_handles_a_message(tmp_path):
    fake = FakeNotifier()
    raw = json.dumps(_inbound(text="top")).encode("utf-8")
    status, body, handled = WB.respond_to_post(
        raw, _cfg(tmp_path), notifier_factory=lambda m, c: fake)
    assert status == 200
    assert body == "EVENT_RECEIVED"
    assert len(handled) == 1
    assert handled[0][1].command == "top"
    assert len(fake.sent) == 1


def test_respond_to_post_bad_json_is_400(tmp_path):
    status, body, handled = WB.respond_to_post(b"not json{", _cfg(tmp_path))
    assert status == 400
    assert handled == []


def test_respond_to_post_status_callback_is_200_empty(tmp_path):
    raw = json.dumps({"entry": [{"changes": [{"value": {
        "statuses": [{"status": "read"}]}}]}]}).encode("utf-8")
    status, body, handled = WB.respond_to_post(raw, _cfg(tmp_path))
    assert status == 200
    assert handled == []


def test_respond_to_post_swallows_a_failing_message(tmp_path):
    def _boom_factory(m, c):
        raise RuntimeError("boom building notifier")

    raw = json.dumps(_inbound(text="top")).encode("utf-8")
    status, body, handled = WB.respond_to_post(
        raw, _cfg(tmp_path), notifier_factory=_boom_factory)
    assert status == 200  # Meta must not be told to retry
    assert handled == []   # the failed message dropped, no crash


# ---------------------------------------------------------------------------
# serve — refuses to start without a verify token (no socket bound).
# ---------------------------------------------------------------------------


def test_serve_refuses_without_verify_token(tmp_path, monkeypatch):
    monkeypatch.delenv("DOS_WHATSAPP_VERIFY_TOKEN", raising=False)
    logs = []
    rc = WB.serve(_cfg(tmp_path), verify_token="", log=logs.append)
    assert rc == 2
    assert any("verify token" in m for m in logs)


# ---------------------------------------------------------------------------
# Seam integration — the bridge is discovered by name (dos.chat_bridges group).
# ---------------------------------------------------------------------------


def test_resolve_bridge_finds_whatsapp_serve():
    from dos import chat_control as CC

    serve = CC.resolve_bridge("whatsapp")
    assert serve is WB.serve
