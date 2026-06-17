"""dos.drivers.whatsapp_bridge — the INBOUND half of the WhatsApp surface (docs/382).

The bridge that turns a WhatsApp message into a DOS answer. Meta's Cloud API
delivers inbound messages by POSTing to a callback URL you host; this driver is
that callback. It does three things, and only these:

  1. Answers Meta's GET verification handshake (`verify_challenge`).
  2. Parses an inbound webhook POST into typed messages (`parse_inbound`).
  3. Routes each message's text through the vendor-blind control surface
     (`dos.chat_control`) and sends the reply back to the sender through the
     outbound transport (`dos.drivers.notify_whatsapp`).

Why a driver, and why this thin
===============================

WhatsApp's wire shape (the `entry[].changes[].value.messages[]` envelope, the
`hub.challenge` handshake) is vendor-specific, so it lives in a DRIVER — the
control LOGIC does not. The bridge knows nothing about what `top` means; it hands
the raw text to `chat_control.handle` and sends back whatever text comes out. Swap
WhatsApp for Telegram and only this file changes; the control surface is reused
whole. That split is the point of docs/382.

Disciplines
===========

  * **Read-only, transitively.** The bridge can only reach what `chat_control`
    exposes, and that surface holds observe verbs only. A WhatsApp message cannot
    take a lease, spawn a worker, or commit — by construction.
  * **Fail-soft + always-200.** Meta RETRIES a webhook that does not return 2xx,
    so a parse error or a per-message failure must not 500 (that causes a retry
    storm). `respond_to_post` swallows per-message errors and still returns 200;
    only malformed JSON returns 400 (Meta will not retry its own bad body).
  * **Pure core, thin shell.** The handshake, the parse, and the per-POST
    response are pure functions (`verify_challenge` / `parse_inbound` /
    `respond_to_get` / `respond_to_post`), unit-tested without a socket. `serve`
    is the only function that touches the network.

Security note (docs/382 §"who can talk to the bridge")
======================================================

This MVP authenticates the WEBHOOK (the `hub.verify_token` handshake proves the
endpoint is yours) but not the SENDER — anyone who messages your business number
gets the read-only fleet view. That is acceptable for an observe-only surface on
a number you control; a sender allowlist (`$DOS_WHATSAPP_ALLOW`, a set of E.164
numbers) is the obvious next gate and the prerequisite for ever exposing a
mutating verb. The Cloud API also signs each POST with an app-secret HMAC
(`X-Hub-Signature-256`); verifying it is the production hardening step noted in
the design doc.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from dos import chat_control as _chat
from dos import config as _config
from dos.notify import Notification, NotifyResult, Severity, send_safely


@dataclass(frozen=True)
class InboundMessage:
    """One inbound WhatsApp text message, lifted out of the webhook envelope."""

    sender: str            # the sender's E.164 number (the `from` field)
    text: str              # the message body
    msg_id: str = ""       # the WhatsApp message id (wamid…)
    phone_number_id: str = ""  # the business number that received it (for the reply)


# ---------------------------------------------------------------------------
# Pure handshake + parsing — no I/O, unit-tested without a socket.
# ---------------------------------------------------------------------------


def verify_challenge(params: dict, expected_token: str) -> str | None:
    """Meta's GET handshake: echo `hub.challenge` iff the verify token matches.

    Returns the challenge string to echo back (200), or None to reject (403). An
    empty `expected_token` never verifies — an unconfigured bridge refuses rather
    than accepting any caller.
    """
    if not expected_token:
        return None
    if params.get("hub.mode") != "subscribe":
        return None
    if params.get("hub.verify_token") != expected_token:
        return None
    return params.get("hub.challenge")


def parse_inbound(payload) -> list[InboundMessage]:
    """A Cloud-API webhook body → the text messages in it. Never raises.

    Skips non-text messages and status callbacks (delivery receipts carry
    `statuses`, not `messages`), so a delivery-receipt POST parses to []. Walks
    defensively: a torn/foreign payload yields [] rather than an error.
    """
    out: list[InboundMessage] = []
    if not isinstance(payload, dict):
        return out
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value") or {}
            if not isinstance(value, dict):
                continue
            meta = value.get("metadata") or {}
            phone_id = str(meta.get("phone_number_id") or "") if isinstance(meta, dict) else ""
            for m in value.get("messages") or []:
                if not isinstance(m, dict) or m.get("type") != "text":
                    continue
                text_obj = m.get("text") or {}
                text = (text_obj.get("body") or "").strip() if isinstance(text_obj, dict) else ""
                sender = str(m.get("from") or "")
                mid = str(m.get("id") or "")
                if sender and text:
                    out.append(InboundMessage(sender=sender, text=text,
                                              msg_id=mid, phone_number_id=phone_id))
    return out


# ---------------------------------------------------------------------------
# Routing — one message → a control reply → the outbound transport.
# ---------------------------------------------------------------------------


def default_notifier(msg: InboundMessage, cfg):
    """Build the per-sender WhatsApp notifier for a reply.

    Each reply goes to the message's OWN sender (`to=msg.sender`) from the OWN
    receiving business number (`phone_number_id` from the payload, else the
    workspace `.env`), so the bridge serves many contacts from one process.
    """
    from dos.drivers.notify_whatsapp import WhatsAppNotifier

    return WhatsAppNotifier(to=msg.sender, phone_id=msg.phone_number_id, root=str(cfg.root))


def handle_message(
    msg: InboundMessage,
    cfg,
    *,
    notifier_factory: Callable[[InboundMessage, object], object] = default_notifier,
    max_lines: int = _chat._DEFAULT_MAX_LINES,
    max_chars: int = _chat._DEFAULT_MAX_CHARS,
) -> tuple["_chat.ChatReply", NotifyResult]:
    """Route one message through `chat_control` and send the reply back.

    Returns `(reply, result)`: the control reply (for logging) and the delivery
    result. NEVER raises — `send_safely` wraps the transport. The reply is a
    title-LESS `Notification` so `notify_whatsapp` sends it as a clean answer,
    not as a severity-tagged alert.
    """
    reply = _chat.handle(msg.text, cfg, max_lines=max_lines, max_chars=max_chars)
    note = Notification(
        severity=Severity.INFO, title="", summary=reply.text,
        key="", source="chat")
    notifier = notifier_factory(msg, cfg)
    result = send_safely(notifier, note)
    return reply, result


# ---------------------------------------------------------------------------
# Pure per-request responses — what the HTTP shell returns. Socket-free.
# ---------------------------------------------------------------------------


def respond_to_get(params: dict, *, verify_token: str) -> tuple[int, str]:
    """The GET handshake response: (status, body)."""
    challenge = verify_challenge(params, verify_token)
    if challenge is not None:
        return 200, challenge
    return 403, "verification failed"


def respond_to_post(
    raw: bytes,
    cfg,
    *,
    notifier_factory: Callable[[InboundMessage, object], object] = default_notifier,
    max_lines: int = _chat._DEFAULT_MAX_LINES,
    max_chars: int = _chat._DEFAULT_MAX_CHARS,
) -> tuple[int, str, list]:
    """The POST response: (status, body, handled).

    `handled` is a list of `(InboundMessage, ChatReply, NotifyResult)` for
    logging/tests. Malformed JSON → 400 (Meta will not retry its own bad body);
    everything else → 200 even with zero messages or a per-message failure, so
    Meta does not retry-storm.
    """
    import json

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001 - a bad body is a 400, not a crash
        return 400, "bad json", []

    handled: list = []
    for msg in parse_inbound(payload):
        try:
            reply, result = handle_message(
                msg, cfg, notifier_factory=notifier_factory,
                max_lines=max_lines, max_chars=max_chars)
            handled.append((msg, reply, result))
        except Exception:  # noqa: BLE001 - one bad message must not fail the batch
            continue
    return 200, "EVENT_RECEIVED", handled


# ---------------------------------------------------------------------------
# The HTTP shell — the only function that touches the network.
# ---------------------------------------------------------------------------


def serve(
    cfg=None,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    verify_token: str = "",
    notifier_factory: Callable[[InboundMessage, object], object] = default_notifier,
    max_lines: int = _chat._DEFAULT_MAX_LINES,
    max_chars: int = _chat._DEFAULT_MAX_CHARS,
    log=None,
) -> int:
    """Run the inbound webhook receiver until interrupted. Returns an exit code.

    `verify_token` defaults to `$DOS_WHATSAPP_VERIFY_TOKEN` / the workspace
    `.env`. Binds `host:port` (front it with HTTPS at your edge — Meta requires a
    public HTTPS callback). Ctrl-C / SIGTERM stops it cleanly with exit 0.
    """
    import os
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    cfg = _config.ensure(cfg)
    emit = log if callable(log) else (lambda m: print(m))

    token = verify_token or os.environ.get("DOS_WHATSAPP_VERIFY_TOKEN", "")
    if not token:
        from dos.drivers.notify_whatsapp import _read_env_file

        token = _read_env_file(cfg.root).get("DOS_WHATSAPP_VERIFY_TOKEN", "")
    if not token:
        emit("error: no verify token (set $DOS_WHATSAPP_VERIFY_TOKEN or pass "
             "--verify-token); refusing to start an unverifiable webhook")
        return 2

    class _Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            params = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            status, body = respond_to_get(params, verify_token=token)
            self._send(status, body)

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            status, body, handled = respond_to_post(
                raw, cfg, notifier_factory=notifier_factory,
                max_lines=max_lines, max_chars=max_chars)
            for msg, reply, result in handled:
                emit(f"[whatsapp] {msg.sender} → {reply.command} "
                     f"({'sent' if result.delivered else 'not sent'}: {result.detail})")
            self._send(status, body)

        def log_message(self, *args) -> None:  # noqa: ARG002 - silence default stderr spam
            pass

    server = ThreadingHTTPServer((host, port), _Handler)
    emit(f"dos whatsapp bridge listening on {host}:{port} "
         f"(workspace {cfg.root}) — Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        emit("dos whatsapp bridge stopped")
    finally:
        server.server_close()
    return 0
