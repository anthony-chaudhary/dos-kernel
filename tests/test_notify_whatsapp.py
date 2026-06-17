"""The WhatsApp notifier driver (`dos.drivers.notify_whatsapp`) — fake transport, no network.

Proves the WhatsApp transport's contract WITHOUT touching the network: a fake
transport records the POST; tests assert the Cloud-API body + endpoint, the
titled-alert vs title-less-reply rendering, dry-run sends nothing, no-token /
no-recipient / no-phone-id / non-2xx / a transport raise all degrade to a
`NotifyResult` (never a raise), the credential ladder, and the 4096-char cap.
The real `urllib` path is exercised only by manual dogfood.
"""

from __future__ import annotations

import json

from dos.notify import Notification, Severity, resolve_notifier, send_safely
from dos.drivers.notify_whatsapp import (
    WhatsAppNotifier,
    build_payload,
    build_text,
)


class FakeTransport:
    def __init__(self, code: int = 200, reason: str = "OK"):
        self.posts: list[tuple[str, bytes, dict, float]] = []
        self._code = code
        self._reason = reason

    def post(self, url, body, headers, timeout):
        self.posts.append((url, body, headers, timeout))
        return self._code, self._reason


def _alert(sev=Severity.WARN):
    return Notification(
        severity=sev, title="2 decisions need you", summary="line one\nline two",
        fields=(("ARBITER_REFUSE @ src", "LANE_BUSY"),), key="dos-decisions",
        source="decisions")


def _reply(text="top says: all lanes free"):
    # The bridge sends a title-LESS note so it renders as a clean answer.
    return Notification(severity=Severity.INFO, title="", summary=text, source="chat")


# ---------------------------------------------------------------------------
# build_text — titled alert gets the [SEV] head; a reply is just its summary.
# ---------------------------------------------------------------------------


def test_build_text_titled_alert_has_severity_head():
    body = build_text(_alert(sev=Severity.URGENT))
    assert body.startswith("■ [URGENT] 2 decisions need you")
    assert "line one" in body


def test_build_text_reply_is_just_the_summary():
    body = build_text(_reply("all lanes free"))
    assert body == "all lanes free"  # no severity chrome on a command answer


def test_build_text_caps_at_4096():
    body = build_text(_reply("z" * 6000))
    assert len(body) <= 4096
    assert body.endswith("…")


# ---------------------------------------------------------------------------
# build_payload — the Cloud-API text-message shape.
# ---------------------------------------------------------------------------


def test_build_payload_is_a_whatsapp_text_message():
    p = build_payload(_reply("hi"), to="15551234567")
    assert p["messaging_product"] == "whatsapp"
    assert p["to"] == "15551234567"
    assert p["type"] == "text"
    assert p["text"]["body"] == "hi"
    json.dumps(p)  # must serialize


# ---------------------------------------------------------------------------
# send — posts to the Graph endpoint with the bearer header + JSON body.
# ---------------------------------------------------------------------------


def test_send_posts_to_graph_endpoint():
    ft = FakeTransport()
    nt = WhatsAppNotifier(token="tok", phone_id="PID123", to="15551234567", transport=ft)
    r = nt.send(_reply("all clear"))
    assert r.delivered is True
    assert "sent to 15551234567" in r.detail
    assert len(ft.posts) == 1
    url, body, headers, _t = ft.posts[0]
    assert url == "https://graph.facebook.com/v21.0/PID123/messages"
    assert headers["Authorization"] == "Bearer tok"
    assert headers["Content-Type"] == "application/json"
    sent = json.loads(body.decode("utf-8"))
    assert sent["to"] == "15551234567"
    assert sent["text"]["body"] == "all clear"


def test_url_override_posts_verbatim_without_phone_id():
    ft = FakeTransport()
    nt = WhatsAppNotifier(token="tok", to="15551234567",
                          url="https://gateway.invalid/send", transport=ft)
    r = nt.send(_reply("x"))
    assert r.delivered is True
    url, _b, _h, _t = ft.posts[0]
    assert url == "https://gateway.invalid/send"


def test_api_version_override():
    ft = FakeTransport()
    nt = WhatsAppNotifier(token="tok", phone_id="PID", to="1555",
                          api_version="v22.0", transport=ft)
    nt.send(_reply("x"))
    url, *_ = ft.posts[0]
    assert "/v22.0/PID/messages" in url


# ---------------------------------------------------------------------------
# dry_run — render + report, POST NOTHING.
# ---------------------------------------------------------------------------


def test_dry_run_sends_nothing():
    ft = FakeTransport()
    nt = WhatsAppNotifier(token="tok", phone_id="PID", to="1555",
                          transport=ft, dry_run=True)
    r = nt.send(_alert())
    assert r.delivered is False
    assert "[dry-run]" in r.detail and "1555" in r.detail
    assert ft.posts == []


# ---------------------------------------------------------------------------
# Fail-soft — missing config / non-2xx / a transport raise → NotifyResult.
# ---------------------------------------------------------------------------


def test_no_token_degrades(monkeypatch, tmp_path):
    monkeypatch.delenv("DOS_WHATSAPP_TOKEN", raising=False)
    nt = WhatsAppNotifier(phone_id="PID", to="1555", root=tmp_path)
    r = nt.send(_reply())
    assert r.delivered is False
    assert "no WhatsApp token" in r.detail


def test_no_recipient_degrades(monkeypatch, tmp_path):
    monkeypatch.delenv("DOS_WHATSAPP_TO", raising=False)
    nt = WhatsAppNotifier(token="tok", phone_id="PID", root=tmp_path)
    r = nt.send(_reply())
    assert r.delivered is False
    assert "no WhatsApp recipient" in r.detail


def test_no_phone_id_degrades(monkeypatch, tmp_path):
    monkeypatch.delenv("DOS_WHATSAPP_PHONE_ID", raising=False)
    nt = WhatsAppNotifier(token="tok", to="1555", root=tmp_path)
    r = nt.send(_reply())
    assert r.delivered is False
    assert "phone-number-id" in r.detail


def test_non_2xx_is_not_delivered():
    ft = FakeTransport(code=401, reason="Unauthorized")
    nt = WhatsAppNotifier(token="tok", phone_id="PID", to="1555", transport=ft)
    r = nt.send(_reply())
    assert r.delivered is False
    assert "HTTP 401" in r.detail and "Unauthorized" in r.detail


def test_transport_raise_is_caught():
    class Boom(FakeTransport):
        def post(self, *a, **k):
            raise OSError("connection refused")

    nt = WhatsAppNotifier(token="tok", phone_id="PID", to="1555", transport=Boom())
    r = nt.send(_reply())
    assert r.delivered is False
    assert "error: connection refused" in r.detail


def test_send_safely_wraps_the_driver_too():
    class Boom(FakeTransport):
        def post(self, *a, **k):
            raise RuntimeError("nope")

    nt = WhatsAppNotifier(token="tok", phone_id="PID", to="1555", transport=Boom())
    r = send_safely(nt, _reply())
    assert r.delivered is False


# ---------------------------------------------------------------------------
# Credential ladder — explicit › env › .env (mirrors notify_webhook).
# ---------------------------------------------------------------------------


def test_token_falls_back_to_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DOS_WHATSAPP_TOKEN", "env-tok")
    monkeypatch.setenv("DOS_WHATSAPP_PHONE_ID", "envPID")
    monkeypatch.setenv("DOS_WHATSAPP_TO", "1555")
    ft = FakeTransport()
    nt = WhatsAppNotifier(root=tmp_path, transport=ft)
    r = nt.send(_reply("x"))
    assert r.delivered is True
    _u, _b, headers, _t = ft.posts[0]
    assert headers["Authorization"] == "Bearer env-tok"


def test_config_reads_env_file(monkeypatch, tmp_path):
    for k in ("DOS_WHATSAPP_TOKEN", "DOS_WHATSAPP_PHONE_ID", "DOS_WHATSAPP_TO"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / ".env").write_text(
        'DOS_WHATSAPP_TOKEN="file-tok"\nDOS_WHATSAPP_PHONE_ID=filePID\n'
        "DOS_WHATSAPP_TO=1999\n", encoding="utf-8")
    ft = FakeTransport()
    nt = WhatsAppNotifier(root=tmp_path, transport=ft)
    r = nt.send(_reply("x"))
    assert r.delivered is True
    url, body, headers, _t = ft.posts[0]
    assert "/filePID/messages" in url
    assert headers["Authorization"] == "Bearer file-tok"
    assert json.loads(body.decode("utf-8"))["to"] == "1999"


# ---------------------------------------------------------------------------
# Resolver integration — discovered by name through the dos.notifiers seam.
# ---------------------------------------------------------------------------


def test_resolve_notifier_finds_whatsapp_by_name():
    nt = resolve_notifier("whatsapp", token="t")
    assert nt.name == "whatsapp"
    assert isinstance(nt, WhatsAppNotifier)


def test_resolve_notifier_filters_superset_kwargs():
    # The CLI hands the superset {channel,url,token,dry_run,root}; whatsapp accepts
    # url/token/dry_run/root/channel(ignored) — resolution must not raise on the bag.
    nt = resolve_notifier(
        "whatsapp", channel="#ops", url="https://x.invalid/send",
        token="t", dry_run=True, root=".")
    assert isinstance(nt, WhatsAppNotifier)
    # dry-run + a url override means no phone-id needed, but still no recipient:
    r = nt.send(_reply())
    assert r.delivered is False  # no recipient configured in this bag
