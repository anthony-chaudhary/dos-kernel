"""dos.drivers.notify_whatsapp — the WhatsApp occupant of `dos.notify` (docs/382).

A `dos.notifiers` transport that delivers a `Notification` as a WhatsApp text
message through the official **Meta WhatsApp Cloud API** (the Graph API
`/<phone_number_id>/messages` endpoint). It registers through the entry-point
group, so `resolve_notifier("whatsapp")` finds it by name and no kernel module
imports it. It is the OUTBOUND half of the WhatsApp control surface; the INBOUND
half (a chat line in → a read-only verb) is `dos.drivers.whatsapp_bridge`, which
routes through the vendor-blind `dos.chat_control` surface.

Why it ships in the core (no extra)
===================================

Like `notify_webhook` and unlike `notify_slack`, this needs only
`urllib.request` from the standard library — the Cloud API is plain HTTPS + JSON.
So it adds NO dependency: a bare `pip install dos-kernel` can already deliver to
WhatsApp once the token + phone-number-id are wired.

Disciplines (the notify-seam posture, verbatim from `notify_webhook`)
=====================================================================

  * **Fail-soft.** `send` returns a `NotifyResult`, never raises — no token, no
    phone-number-id, no recipient, a non-2xx, or a network error all degrade to
    `delivered=False` with a one-line reason.
  * **Advisory only.** It renders a projection → POST. It mutates no DOS state,
    takes no lease, stops no run. It does NOT retry or queue.

Credentials / routing (the `notify_webhook` ladder: explicit › `$ENV` › `.env`)
===============================================================================

  * **token** (Cloud API access token): explicit › `$DOS_WHATSAPP_TOKEN` ›
    `<root>/.env`. Sent as `Authorization: Bearer <token>`. No token → not sent.
  * **phone_id** (the sender's phone-number-id, NOT a phone number): explicit ›
    `$DOS_WHATSAPP_PHONE_ID` › `.env`. Names the WhatsApp business number that
    sends. No phone_id (and no `url` override) → not sent.
  * **to** (the recipient's E.164 number, e.g. `15551234567`): explicit ›
    `$DOS_WHATSAPP_TO` › `.env`. The bridge passes `to=<sender>` per reply, so
    a default recipient is only needed for unsolicited `dos notify` pushes.
  * **api_base** / **api_version**: default `https://graph.facebook.com` / a
    pinned Graph version; override via `$DOS_WHATSAPP_API_BASE` /
    `$DOS_WHATSAPP_API_VERSION` to point at a gateway or bump the version.
  * **url**: a full endpoint override (accepted from the generic `dos notify`
    kwarg bag). When set, the POST goes there verbatim and `phone_id` is not
    needed — for a proxy / a Twilio-style gateway that wants the same JSON body.

The JSON body is built HERE (`build_payload`, the spine's analogue of
`notify_webhook.build_payload`), so the kernel seam stays wire-format-free.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dos.notify import Notification, NotifyResult

# A WhatsApp text message body caps at 4096 chars; keep the rendered summary
# under that so a giant `dos top` screen does not get rejected by the API.
_MAX_BODY = 4096

# Severity → a short tag for the message head (a phone has no colour chips).
_SEV_TAG = {"INFO": "·", "WARN": "▲", "URGENT": "■"}

# A recent, stable Graph API version. Overridable via $DOS_WHATSAPP_API_VERSION
# so an operator can bump it without a code change when Meta rolls a new one.
_DEFAULT_API_VERSION = "v21.0"
_DEFAULT_API_BASE = "https://graph.facebook.com"


def build_text(note: Notification) -> str:
    """A `Notification` → the plain-text WhatsApp body (pure; no I/O).

    A titled alert (a `dos notify` push) gets a `[SEV] title` head with the
    projection's plain summary below — so the phone says everything the terminal
    would. A title-LESS note (a chat-control REPLY routed back by the bridge) is
    sent as just its summary, with no severity chrome — a command answer should
    read like an answer, not an alert. Both cap at the API's 4096-char limit
    with an honest truncation marker.
    """
    summary = note.summary or ""
    if not note.title:
        body = summary or note.severity.value
    else:
        tag = _SEV_TAG.get(note.severity.value, "·")
        head = f"{tag} [{note.severity.value}] {note.title}"
        body = head if not summary else f"{head}\n{summary}"
    if len(body) > _MAX_BODY:
        body = body[: _MAX_BODY - 1].rstrip() + "…"
    return body


def build_payload(note: Notification, *, to: str) -> dict:
    """The Cloud API send body for a text message (pure; no I/O)."""
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": build_text(note)},
    }


# ---------------------------------------------------------------------------
# Credential / routing resolution — boundary I/O off the pure builder
# (mirrors notify_webhook._read_env_file / resolve_*).
# ---------------------------------------------------------------------------


def _read_env_file(root: Path) -> dict[str, str]:
    """Best-effort parse of `<root>/.env` → {KEY: value}. Never raises."""
    out: dict[str, str] = {}
    try:
        text = (root / ".env").read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _resolve(explicit: str | None, env_key: str, *, root: Path | None, default: str = "") -> str:
    """One credential/route: explicit arg › `$ENV` › `<root>/.env` › default."""
    if explicit:
        return explicit
    env = os.environ.get(env_key)
    if env:
        return env
    if root is not None:
        from_file = _read_env_file(root).get(env_key, "")
        if from_file:
            return from_file
    return default


# ---------------------------------------------------------------------------
# A tiny default transport over urllib — injectable in tests, lazy at call.
# ---------------------------------------------------------------------------


class _UrllibTransport:
    """The stdlib POST. Returns (status_code, reason); raises on network failure.

    The same shape `notify_webhook._UrllibTransport` exposes, so a test injects a
    fake with a `post(url, body, headers, timeout) -> (code, reason)` method.
    """

    def post(self, url: str, body: bytes, headers: dict, timeout: float) -> tuple[int, str]:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - operator-supplied host
                return int(getattr(resp, "status", 0) or resp.getcode() or 0), "OK"
        except urllib.error.HTTPError as e:
            return int(e.code), str(getattr(e, "reason", "") or "HTTP error")


# ---------------------------------------------------------------------------
# The notifier.
# ---------------------------------------------------------------------------


class WhatsAppNotifier:
    """Deliver a `Notification` as a WhatsApp text via the Meta Cloud API.

    Parameters
    ----------
    token:
        Cloud API access token; defaults to `$DOS_WHATSAPP_TOKEN` / `.env`.
    phone_id:
        The sender's phone-number-id; defaults to `$DOS_WHATSAPP_PHONE_ID` /
        `.env`. Not needed when `url` (a full endpoint) is given.
    to:
        Default recipient (E.164, no `+`); defaults to `$DOS_WHATSAPP_TO` /
        `.env`. The bridge passes the message's sender per reply.
    root:
        Workspace root for `.env` resolution (the `SubstrateConfig.root`).
    dry_run:
        Render + report, POST NOTHING.
    api_base / api_version:
        Graph host + version; default to `$DOS_WHATSAPP_API_BASE` /
        `$DOS_WHATSAPP_API_VERSION` then the pinned defaults.
    url:
        A full endpoint override (from the generic `dos notify` kwarg bag); when
        set the POST goes there and `phone_id` is unused.
    timeout:
        Request timeout in seconds (default 10).
    transport:
        Inject a fake `post(...) -> (code, reason)` in tests; None → stdlib.

    `channel` is accepted-and-ignored (WhatsApp has no channel) so the generic
    `dos notify` kwarg-forwarding can hand the same bag to any transport.
    """

    name = "whatsapp"

    def __init__(self, *, token: str | None = None, phone_id: str = "", to: str = "",
                 root: "os.PathLike[str] | str | None" = None,
                 dry_run: bool = False, api_base: str = "", api_version: str = "",
                 url: str = "", timeout: float = 10.0, transport=None,
                 channel: str = ""):  # noqa: ARG002 - channel ignored (parity)
        self._token = token
        self._phone_id_arg = phone_id
        self._to_arg = to
        self._root = Path(root) if root is not None else None
        self._dry_run = bool(dry_run)
        self._api_base_arg = api_base
        self._api_version_arg = api_version
        self._url_arg = url
        self._timeout = max(0.1, float(timeout))
        self._transport = transport

    def _endpoint(self, phone_id: str) -> str:
        if self._url_arg:
            return self._url_arg
        base = _resolve(self._api_base_arg, "DOS_WHATSAPP_API_BASE",
                        root=self._root, default=_DEFAULT_API_BASE).rstrip("/")
        version = _resolve(self._api_version_arg, "DOS_WHATSAPP_API_VERSION",
                           root=self._root, default=_DEFAULT_API_VERSION)
        return f"{base}/{version}/{phone_id}/messages"

    def send(self, note: Notification) -> NotifyResult:
        """Deliver `note`. Returns a `NotifyResult`; NEVER raises (fail-soft)."""
        token = _resolve(self._token, "DOS_WHATSAPP_TOKEN", root=self._root)
        if not token:
            return NotifyResult(
                delivered=False,
                detail="no WhatsApp token (pass token=, set $DOS_WHATSAPP_TOKEN, "
                       "or add DOS_WHATSAPP_TOKEN to the workspace .env)")

        to = _resolve(self._to_arg, "DOS_WHATSAPP_TO", root=self._root)
        if not to:
            return NotifyResult(
                delivered=False,
                detail="no WhatsApp recipient (pass to=, set $DOS_WHATSAPP_TO, "
                       "or add DOS_WHATSAPP_TO to the workspace .env)")

        phone_id = _resolve(self._phone_id_arg, "DOS_WHATSAPP_PHONE_ID", root=self._root)
        if not phone_id and not self._url_arg:
            return NotifyResult(
                delivered=False,
                detail="no WhatsApp phone-number-id (pass phone_id=, set "
                       "$DOS_WHATSAPP_PHONE_ID, or add it to the workspace .env)")

        endpoint = self._endpoint(phone_id)

        if self._dry_run:
            return NotifyResult(
                delivered=False,
                detail=f"[dry-run] would message {to} via {endpoint} "
                       f"({note.severity.value}: {note.title})")

        try:
            body = json.dumps(build_payload(note, to=to)).encode("utf-8")
        except Exception as e:  # noqa: BLE001 - a non-serializable field must not crash
            return NotifyResult(delivered=False, detail=f"error: payload not serializable: {e}")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "dos-notify/whatsapp",
        }
        transport = self._transport if self._transport is not None else _UrllibTransport()
        try:
            code, reason = transport.post(endpoint, body, headers, self._timeout)
        except Exception as e:  # noqa: BLE001 - advisory; report, don't crash the producer
            return NotifyResult(delivered=False, detail=f"error: {e}")

        if 200 <= int(code) < 300:
            return NotifyResult(delivered=True, detail=f"sent to {to} (HTTP {code})", ref=str(code))
        return NotifyResult(delivered=False, detail=f"HTTP {code}: {reason}")
