"""dos.drivers.notify_webhook — the generic HTTP-POST occupant of `dos.notify` (docs/267).

The second transport behind the notification spine, and the *universal* one. Where
the kernel seam (`dos.notify`) is transport-agnostic and names no vendor, THIS is
where "an HTTP endpoint" is allowed to be code — but it names no SPECIFIC vendor: it
renders a `Notification` to a portable JSON body and POSTs it to a configured URL, so
the one driver reaches every chat platform's incoming webhook (Teams / Discord /
Mattermost / Slack-incoming render the top-level `text`), every incident bus
(PagerDuty / Opsgenie / incident.io ingest a POST + routing key), and every automation
hook (Zapier / n8n / a Lambda Function URL). It registers through the `dos.notifiers`
entry-point group, so `resolve_notifier("webhook")` finds it by name and no kernel
module imports it.

Why it ships in the core (no extra)
===================================

Unlike `notify_slack` (which pulls `slack_helpers` → `requests` in the
`[notify-slack]` extra), a webhook needs only `urllib.request` from the standard
library. So this driver adds NO dependency and ships in the core install — a
`pip install dos-kernel` can already deliver notifications to any webhook URL.

Disciplines (inherited from the seam — the `notify_slack` posture, verbatim)
============================================================================

  * **Fail-soft.** `send` returns a `NotifyResult`, never raises — no URL, a non-2xx
    response, a network error, or a malformed body all degrade to `delivered=False`
    with a one-line reason. (The seam's `send_safely` is the outer net; this is the
    inner one, so even a direct `WebhookNotifier().send(...)` is crash-free.)
  * **Advisory only.** It renders a projection → POST. It mutates no DOS state, takes
    no lease, stops no run. A LIVENESS-halt field CARRIES the paste-to-stop command
    (built by the seam); it never enacts it. It does NOT retry or queue — a failed
    POST is reported, and the host decides whether to re-`dos notify` (DOS reports; it
    does not own a delivery SLA).

Credentials / routing (the `notify_slack.resolve_token` ladder, generalized to a URL)
=====================================================================================

  * **url**: explicit arg › `$DOS_WEBHOOK_URL` › the workspace `.env`
    (`<root>/.env`'s `DOS_WEBHOOK_URL`). No URL anywhere → a non-delivered result.
  * **token** (optional): explicit arg › `$DOS_WEBHOOK_TOKEN` › `<root>/.env`. When
    set, sent as `Authorization: Bearer <token>`. Many incoming-webhook URLs carry the
    secret in the PATH (Slack/Teams/Discord style) and need no token; a header-secret
    transport (PagerDuty-style) uses the token. Both are supported.

The JSON body is built HERE, locally — a small DOS-shaped builder (`build_payload`,
the spine's analogue of `notify_slack.build_blocks`), so the kernel seam stays
wire-format-free. It is a FLAT, portable object (not a vendor wire format): a
structured consumer reads `severity`/`fields`/`source`; a dumb chat hook renders the
synthesized top-level `text`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dos.notify import Notification, NotifyResult

# A POST body caps at nothing in particular, but keep the summary bounded so a giant
# `dos top` screen does not produce a multi-megabyte request (the `notify_slack`
# _MAX_SUMMARY instinct; webhooks vary, so this is generous).
_MAX_SUMMARY = 8000

# Severity → a short tag for the synthesized `text` line (chat hooks render `text`).
_SEV_TAG = {"INFO": "·", "WARN": "▲", "URGENT": "■"}


def build_payload(note: Notification) -> dict:
    """A `Notification` → a portable JSON body (pure; no I/O).

    `note.to_dict()` already carries `severity`/`title`/`summary`/`fields`/`key`/
    `source`. We add a synthesized top-level **`text`** (`[SEV] title\\n summary`),
    because most chat webhooks (Teams/Discord/Slack-incoming) render a `text` field
    and ignore the rest — so ONE body serves both a structured consumer (reads
    `fields`/`severity`) and a dumb chat hook (renders `text`). A consumer needing a
    vendor-exact shape (PagerDuty's nested `payload`) is a later payload-shaping
    subclass; this is the 90% generic adapter.
    """
    body = note.to_dict()
    if len(body.get("summary") or "") > _MAX_SUMMARY:
        body["summary"] = body["summary"][:_MAX_SUMMARY]
    tag = _SEV_TAG.get(note.severity.value, "·")
    head = f"{tag} [{note.severity.value}] {note.title}".strip()
    text = head if not body.get("summary") else f"{head}\n{body['summary']}"
    # The clickable follow-up (docs/387): `body['action']` already carries the
    # structured `{label, command, url}` (via `note.to_dict()`) for a consumer that
    # renders a button; a dumb chat hook that only renders `text` gets the open-verb
    # appended so the notification is not a dead end there either.
    action = getattr(note, "action", None)
    if action is not None and getattr(action, "command", ""):
        text = f"{text}\n⟶ open: {action.command}"
    body["text"] = text
    return body


# ---------------------------------------------------------------------------
# Credential / routing resolution — boundary I/O, kept off the pure builder
# (mirrors notify_slack._read_env_file / resolve_token, generalized to URL+token).
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


def resolve_url(explicit: str | None, *, root: Path | None) -> str:
    """Webhook URL: explicit arg › `$DOS_WEBHOOK_URL` › `<root>/.env`. "" if none."""
    if explicit:
        return explicit
    env = os.environ.get("DOS_WEBHOOK_URL")
    if env:
        return env
    if root is not None:
        return _read_env_file(root).get("DOS_WEBHOOK_URL", "")
    return ""


def resolve_token(explicit: str | None, *, root: Path | None) -> str:
    """Optional bearer token: explicit › `$DOS_WEBHOOK_TOKEN` › `<root>/.env`. "" if none."""
    if explicit:
        return explicit
    env = os.environ.get("DOS_WEBHOOK_TOKEN")
    if env:
        return env
    if root is not None:
        return _read_env_file(root).get("DOS_WEBHOOK_TOKEN", "")
    return ""


# ---------------------------------------------------------------------------
# A tiny default transport over urllib — injectable in tests, lazy at call.
# ---------------------------------------------------------------------------


class _UrllibTransport:
    """The stdlib POST. Returns (status_code, reason); raises on network failure.

    Kept behind a method so tests inject a fake with the same `post(...)` shape
    instead of patching urllib (the `notify_slack` injected-client posture).
    """

    def post(self, url: str, body: bytes, headers: dict, timeout: float) -> tuple[int, str]:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - operator-supplied URL
                return int(getattr(resp, "status", 0) or resp.getcode() or 0), "OK"
        except urllib.error.HTTPError as e:
            # A non-2xx with a response body — surface the code + reason, don't raise.
            return int(e.code), str(getattr(e, "reason", "") or "HTTP error")


# ---------------------------------------------------------------------------
# The notifier.
# ---------------------------------------------------------------------------


class WebhookNotifier:
    """Deliver a `Notification` by POSTing a portable JSON body to a configured URL.

    Parameters
    ----------
    url:
        The webhook endpoint; defaults to `$DOS_WEBHOOK_URL` / the workspace `.env`
        (`resolve_url`).
    token:
        Optional bearer secret; defaults to `$DOS_WEBHOOK_TOKEN` / `.env`. Sent as
        `Authorization: Bearer <token>` when present (override via `headers`).
    root:
        Workspace root for `.env` resolution (the `SubstrateConfig.root`).
    dry_run:
        Render + report, POST NOTHING.
    method:
        HTTP method (default POST). Kept for the rare webhook that wants PUT.
    headers:
        Extra/override headers merged over the defaults (`Content-Type:
        application/json` + the bearer header when a token is set).
    timeout:
        Request timeout in seconds (default 10).
    transport:
        Inject a fake with a `post(url, body, headers, timeout) -> (code, reason)`
        method in tests; None uses the stdlib urllib transport.

    `channel` is accepted-and-ignored (a webhook has no channel) so the generic
    `dos notify` kwarg-forwarding can hand the same bag to any transport without the
    caller branching per driver.
    """

    name = "webhook"

    def __init__(self, *, url: str = "", token: str | None = None,
                 root: "os.PathLike[str] | str | None" = None,
                 dry_run: bool = False, method: str = "POST",
                 headers: dict | None = None, timeout: float = 10.0,
                 transport=None, channel: str = ""):  # noqa: ARG002 - channel ignored (parity)
        self._url_arg = url
        self._token = token
        self._root = Path(root) if root is not None else None
        self._dry_run = bool(dry_run)
        self._method = (method or "POST").upper()
        self._extra_headers = dict(headers or {})
        self._timeout = max(0.1, float(timeout))
        self._transport = transport

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "User-Agent": "dos-notify/webhook"}
        token = resolve_token(self._token, root=self._root)
        if token:
            h["Authorization"] = f"Bearer {token}"
        h.update(self._extra_headers)   # operator overrides win
        return h

    def send(self, note: Notification) -> NotifyResult:
        """Deliver `note`. Returns a `NotifyResult`; NEVER raises (fail-soft)."""
        url = resolve_url(self._url_arg, root=self._root)
        if not url:
            return NotifyResult(
                delivered=False,
                detail="no webhook URL (pass --url, set $DOS_WEBHOOK_URL, "
                       "or add DOS_WEBHOOK_URL to the workspace .env)",
            )

        if self._dry_run:
            return NotifyResult(
                delivered=False,
                detail=f"[dry-run] would POST to {url} "
                       f"({note.severity.value}: {note.title})",
            )

        try:
            body = json.dumps(build_payload(note)).encode("utf-8")
        except Exception as e:  # noqa: BLE001 - a non-serializable field must not crash
            return NotifyResult(delivered=False, detail=f"error: payload not serializable: {e}")

        transport = self._transport if self._transport is not None else _UrllibTransport()
        try:
            code, reason = transport.post(url, body, self._headers(), self._timeout)
        except Exception as e:  # noqa: BLE001 - advisory; report, don't crash the producer
            return NotifyResult(delivered=False, detail=f"error: {e}")

        if 200 <= int(code) < 300:
            return NotifyResult(delivered=True, detail=f"posted HTTP {code}", ref=str(code))
        return NotifyResult(delivered=False, detail=f"HTTP {code}: {reason}")
