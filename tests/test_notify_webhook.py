"""The webhook notifier driver (`dos.drivers.notify_webhook`) — fake transport, no network.

Proves the generic HTTP-POST transport's contract WITHOUT touching the network: a
fake transport records the POST; tests assert the portable JSON body, dry-run sends
nothing, no-URL / non-2xx / a transport raise all degrade to a `NotifyResult` (never
a raise), URL+token resolution, and the bearer header. The real `urllib` path is
exercised only by manual dogfood (docs/267).
"""

from __future__ import annotations

import json

from dos.notify import Notification, Severity, resolve_notifier, send_safely
from dos.drivers.notify_webhook import (
    WebhookNotifier,
    build_payload,
    resolve_token,
    resolve_url,
)


# ---------------------------------------------------------------------------
# A fake transport — records the POST, hits no network.
# ---------------------------------------------------------------------------


class FakeTransport:
    def __init__(self, code: int = 200, reason: str = "OK"):
        self.posts: list[tuple[str, bytes, dict, float]] = []
        self._code = code
        self._reason = reason

    def post(self, url, body, headers, timeout):
        self.posts.append((url, body, headers, timeout))
        return self._code, self._reason


def _note(source="decisions", sev=Severity.WARN, key="dos-decisions"):
    return Notification(
        severity=sev, title="2 decisions need you", summary="line one\nline two",
        fields=(("ARBITER_REFUSE @ src", "LANE_BUSY"),), key=key, source=source,
    )


# ---------------------------------------------------------------------------
# build_payload — pure, portable JSON body.
# ---------------------------------------------------------------------------


def test_build_payload_carries_structured_fields_and_synth_text():
    body = build_payload(_note(sev=Severity.URGENT))
    assert body["severity"] == "URGENT"
    assert body["title"] == "2 decisions need you"
    assert body["fields"] == [["ARBITER_REFUSE @ src", "LANE_BUSY"]]
    assert body["source"] == "decisions"
    # the synthesized top-level text (what a dumb chat hook renders)
    assert body["text"].startswith("■ [URGENT] 2 decisions need you")
    assert "line one" in body["text"]


def test_build_payload_text_without_summary_is_just_the_head():
    n = Notification(Severity.INFO, "fleet clear", "", fields=(), source="decisions")
    body = build_payload(n)
    assert body["text"] == "· [INFO] fleet clear"


def test_build_payload_is_json_serializable():
    # The driver json.dumps()es this; a frozen field tuple must round-trip.
    body = build_payload(_note())
    json.dumps(body)  # must not raise


def test_build_payload_carries_the_action_and_folds_open_into_text():
    from dos.notify import notification_for_decisions
    # A note built through the adapter carries the clickable follow-up (docs/387).
    n = notification_for_decisions([], summary="(none)", root="/ws")
    body = build_payload(n)
    # structured consumers read the action object…
    assert body["action"]["command"] == "dos decisions --workspace /ws"
    # …and a dumb chat hook gets the open-verb appended to text.
    assert "⟶ open: dos decisions --workspace /ws" in body["text"]


def test_build_payload_no_action_leaves_text_unchanged():
    # A directly-built, action-less note must not grow an open line (the == below
    # pins that the fold is guarded on an actual action).
    n = Notification(Severity.INFO, "fleet clear", "", fields=(), source="decisions")
    body = build_payload(n)
    assert body["text"] == "· [INFO] fleet clear"
    assert body["action"] is None


# ---------------------------------------------------------------------------
# Routing — a POST happens to the resolved URL with the JSON body + headers.
# ---------------------------------------------------------------------------


def test_send_posts_body_to_url():
    ft = FakeTransport()
    nt = WebhookNotifier(url="https://example.invalid/hook", transport=ft)
    r = nt.send(_note())
    assert r.delivered is True
    assert r.detail == "posted HTTP 200"
    assert len(ft.posts) == 1
    url, body, headers, _timeout = ft.posts[0]
    assert url == "https://example.invalid/hook"
    assert headers["Content-Type"] == "application/json"
    # the body is the portable payload, serialized
    sent = json.loads(body.decode("utf-8"))
    assert sent["title"] == "2 decisions need you"
    assert sent["text"].startswith("▲ [WARN]")


def test_token_sets_bearer_header():
    ft = FakeTransport()
    nt = WebhookNotifier(url="https://example.invalid/hook", token="sek-ret", transport=ft)
    nt.send(_note())
    _url, _body, headers, _t = ft.posts[0]
    assert headers["Authorization"] == "Bearer sek-ret"


def test_no_token_no_auth_header():
    ft = FakeTransport()
    nt = WebhookNotifier(url="https://example.invalid/hook", transport=ft)
    nt.send(_note())
    _url, _body, headers, _t = ft.posts[0]
    assert "Authorization" not in headers


def test_custom_headers_override():
    ft = FakeTransport()
    nt = WebhookNotifier(url="https://example.invalid/hook", transport=ft,
                         headers={"X-Routing-Key": "abc", "Content-Type": "application/json+pd"})
    nt.send(_note())
    _url, _body, headers, _t = ft.posts[0]
    assert headers["X-Routing-Key"] == "abc"
    assert headers["Content-Type"] == "application/json+pd"  # operator override wins


# ---------------------------------------------------------------------------
# dry_run — render + report, POST NOTHING.
# ---------------------------------------------------------------------------


def test_dry_run_sends_nothing():
    ft = FakeTransport()
    nt = WebhookNotifier(url="https://example.invalid/hook", transport=ft, dry_run=True)
    r = nt.send(_note())
    assert r.delivered is False
    assert "[dry-run]" in r.detail and "example.invalid" in r.detail
    assert ft.posts == []


# ---------------------------------------------------------------------------
# Fail-soft — no URL, non-2xx, transport raise → NotifyResult, never a raise.
# ---------------------------------------------------------------------------


def test_no_url_degrades_to_skip(monkeypatch, tmp_path):
    monkeypatch.delenv("DOS_WEBHOOK_URL", raising=False)
    nt = WebhookNotifier(root=tmp_path)  # no url arg, no env, no .env
    r = nt.send(_note())
    assert r.delivered is False
    assert "no webhook URL" in r.detail


def test_non_2xx_is_not_delivered():
    ft = FakeTransport(code=500, reason="Internal Server Error")
    nt = WebhookNotifier(url="https://example.invalid/hook", transport=ft)
    r = nt.send(_note())
    assert r.delivered is False
    assert "HTTP 500" in r.detail and "Internal Server Error" in r.detail


def test_transport_raise_is_caught():
    class Boom(FakeTransport):
        def post(self, *a, **k):
            raise OSError("connection refused")

    nt = WebhookNotifier(url="https://example.invalid/hook", transport=Boom())
    r = nt.send(_note())
    assert r.delivered is False
    assert "error: connection refused" in r.detail


def test_send_safely_wraps_the_driver_too():
    class Boom(FakeTransport):
        def post(self, *a, **k):
            raise RuntimeError("nope")

    nt = WebhookNotifier(url="https://example.invalid/hook", transport=Boom())
    r = send_safely(nt, _note())
    assert r.delivered is False


def test_unserializable_field_degrades_to_skip():
    # A Notification whose field value cannot be JSON-encoded → a soft failure, not a
    # crash. (Defensive: the typed Notification normally holds only strings.)
    class Bad:
        def __repr__(self):
            return "bad"

    ft = FakeTransport()
    n = Notification(Severity.WARN, "t", "s", fields=(("k", Bad()),), source="decisions")  # type: ignore[arg-type]
    nt = WebhookNotifier(url="https://example.invalid/hook", transport=ft)
    r = nt.send(n)
    assert r.delivered is False
    assert "not serializable" in r.detail
    assert ft.posts == []


# ---------------------------------------------------------------------------
# URL / token resolution ladder — explicit › env › .env (mirrors notify_slack).
# ---------------------------------------------------------------------------


def test_resolve_url_prefers_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("DOS_WEBHOOK_URL", "https://env.invalid")
    assert resolve_url("https://explicit.invalid", root=tmp_path) == "https://explicit.invalid"


def test_resolve_url_falls_back_to_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DOS_WEBHOOK_URL", "https://env.invalid")
    assert resolve_url("", root=tmp_path) == "https://env.invalid"


def test_resolve_url_reads_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("DOS_WEBHOOK_URL", raising=False)
    (tmp_path / ".env").write_text('DOS_WEBHOOK_URL="https://file.invalid/h"\n', encoding="utf-8")
    assert resolve_url("", root=tmp_path) == "https://file.invalid/h"


def test_resolve_token_reads_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("DOS_WEBHOOK_TOKEN", raising=False)
    (tmp_path / ".env").write_text("DOS_WEBHOOK_TOKEN=tok-fromfile\n", encoding="utf-8")
    assert resolve_token(None, root=tmp_path) == "tok-fromfile"


# ---------------------------------------------------------------------------
# Resolver integration — discovered by name through the dos.notifiers seam, and the
# CLI's superset kwargs are filtered to the constructor (channel ignored, url kept).
# ---------------------------------------------------------------------------


def test_resolve_notifier_finds_webhook_by_name():
    nt = resolve_notifier("webhook", url="https://example.invalid/hook")
    assert nt.name == "webhook"
    assert isinstance(nt, WebhookNotifier)


def test_resolve_notifier_filters_superset_kwargs_for_webhook():
    # The CLI hands the superset {channel,url,token,dry_run,root}. `webhook` accepts
    # url/token/dry_run/root/channel(ignored) — resolution must not raise on the bag.
    nt = resolve_notifier(
        "webhook", channel="#ops", url="https://example.invalid/hook",
        token="t", dry_run=True, root=".")
    assert isinstance(nt, WebhookNotifier)
    r = nt.send(_note())
    assert r.delivered is False and "[dry-run]" in r.detail


def test_resolve_notifier_filters_superset_kwargs_for_slack():
    # The SAME superset must resolve `slack` (which does NOT take url/token) without a
    # TypeError — the signature filter drops the kwargs slack's __init__ lacks.
    from dos.drivers.notify_slack import SlackNotifier

    nt = resolve_notifier(
        "slack", channel="C0AJ37QHMFB", url="https://x.invalid", token="t",
        dry_run=True, root=".")
    assert isinstance(nt, SlackNotifier)
