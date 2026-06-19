"""dos.drivers.notify_slack — the Slack occupant of the `dos.notify` seam (docs/225).

The first transport behind the notification spine. Where the kernel seam
(`dos.notify`) is transport-agnostic and names no vendor, THIS is where "Slack" is
allowed to be code (a `SlackNotifier` is inherently Slack-specific — the
`GeminiDialect` / `LlmJudge` rule). It registers through the `dos.notifiers`
entry-point group, so `resolve_notifier("slack")` finds it by name and no kernel
module imports it.

What it delivers
================

A `Notification` → Slack, in one of two shapes the operator picked (docs/225):

  * **decisions digest** (`source="decisions"`): a fresh Block-Kit POST per run —
    `chat.postMessage`. Cron/event-driven; each push is its own message.
  * **live fleet status** (`source="top"`): ONE message EDITED in place as the
    fleet moves — a `slack_helpers.LiveMessage` keyed on `note.key` so the status
    stream updates a single message instead of spamming the channel (the
    `LiveMessage` reason-to-exist). The kernel `note.key` is the edit handle.

`edit_in_place` overrides the source-based default (None = auto: a `top`
notification edits, a `decisions` digest posts).

Disciplines (inherited from the seam)
=====================================

  * **Fail-soft.** `send` returns a `NotifyResult`, never raises — a missing token,
    an absent `slack_helpers` extra, or a transport error all degrade to
    `delivered=False` with a one-line reason. (The seam's `send_safely` is the
    outer net; this is the inner one, so even a direct `SlackNotifier().send(...)`
    is crash-free.)
  * **Advisory only.** It renders a projection → push. It mutates no DOS state, takes
    no lease, stops no run. A LIVENESS-halt field CARRIES the paste-to-stop command
    (built by the seam); it never enacts it.
  * **Lazy import + optional dep.** `slack_helpers` (which pulls `requests`) is in
    the `[notify-slack]` extra, NOT the core. It is imported INSIDE `send`/the
    client builder; absent → a `NotifyResult` with an install hint, never an
    `ImportError` at module load (the `dos_mcp` posture). So importing this driver —
    which entry-point discovery does — never fails for lack of the extra.

Credentials / routing (the `slack_helpers` convention)
=======================================================

  * **token**: explicit arg › `$SLACK_BOT_TOKEN` › the workspace `.env`
    (`<root>/.env`, the file `slack_helpers` itself reads).
  * **channel**: a logical name resolved through `slack_helpers/slack_config.json`
    (`{"channels": {...}}`), or a raw channel id (`C0…`) passed straight through.

Block Kit is built HERE, locally — a small DOS-shaped builder (the spine's analogue
of `slack_helpers.build_upload_blocks`), so the kernel seam stays Block-Kit-free.
"""

from __future__ import annotations

import os
from pathlib import Path

from dos.notify import Notification, NotifyResult

# The severity → header glyph map. Matches `dispatch_top`'s chip glyphs so the two
# surfaces read the same in a channel (🟢/🟡/🔴), with a neutral bell for a plain
# INFO digest that is not a fleet-status frame.
_SEV_EMOJI = {
    "INFO": ":large_blue_circle:",
    "WARN": ":large_yellow_circle:",
    "URGENT": ":red_circle:",
}

# Slack section `fields` cap at 10; a header/section text caps well under 3000.
_MAX_FIELDS = 10
_MAX_SUMMARY = 2800


def build_blocks(note: Notification) -> list[dict]:
    """A `Notification` → Slack Block Kit blocks (pure; no I/O).

    A `header` (severity emoji + title), a `section` of the TOP `fields` as
    mrkdwn pairs (capped at Slack's 10), the plain-text `summary` in a fenced code
    block (so a notifier with no rich surface still says everything), and a
    `context` line naming the source projection. Pure — the spine's local analogue
    of `slack_helpers.build_upload_blocks`, kept out of the kernel seam.
    """
    emoji = _SEV_EMOJI.get(note.severity.value, "")
    head = f"{emoji} {note.title}".strip()
    blocks: list[dict] = [
        {"type": "header",
         "text": {"type": "plain_text", "text": head[:150], "emoji": True}},
    ]
    if note.fields:
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*{label}*\n{value}"}
                for label, value in note.fields[:_MAX_FIELDS]
            ],
        })
    if note.summary:
        body = note.summary[:_MAX_SUMMARY]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"```\n{body}\n```"},
        })
    # The clickable follow-up (docs/387): a Slack link button when the action
    # carries an explicit URL (a button with no URL has no backend here and the
    # advisory floor forbids one that enacts), so a button only appears when it can
    # actually open something. The open-command is ALSO folded into the context line
    # below so a plain Slack still shows the copy-pasteable `dos …` verb.
    action = getattr(note, "action", None)
    if action is not None and getattr(action, "url", ""):
        blocks.append({
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text",
                         "text": (getattr(action, "label", "") or "open")[:75],
                         "emoji": True},
                "url": action.url,
            }],
        })
    ctx = (f"dos notify · source=`{note.source or '?'}` · "
           f"severity=`{note.severity.value}`")
    if action is not None and getattr(action, "command", ""):
        ctx = f"{ctx} · open: `{action.command}`"
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": ctx}],
    })
    return blocks


# ---------------------------------------------------------------------------
# Credential / routing resolution — the boundary I/O, kept off the pure builder.
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


def resolve_token(explicit: str | None, *, root: Path | None) -> str:
    """Bot token: explicit arg › `$SLACK_BOT_TOKEN` › `<root>/.env`. "" if none."""
    if explicit:
        return explicit
    env = os.environ.get("SLACK_BOT_TOKEN")
    if env:
        return env
    if root is not None:
        return _read_env_file(root).get("SLACK_BOT_TOKEN", "")
    return ""


def _slack_config_channels() -> dict[str, str]:
    """The `slack_helpers/slack_config.json` name→id map, or {} if unavailable."""
    try:
        import importlib.util
        import json

        spec = importlib.util.find_spec("slack_helpers")
        if spec is None or not spec.origin:
            return {}
        cfg = Path(spec.origin).parent / "slack_config.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        chans = data.get("channels", {})
        return {str(k): str(v) for k, v in chans.items()} if isinstance(chans, dict) else {}
    except Exception:
        return {}


def resolve_channel(channel: str) -> str:
    """A channel NAME → its id via `slack_config.json`; a raw id passes through.

    A value that is already a Slack channel id (starts with `C`/`G`/`D` and is
    upper-case-ish) is returned as-is; otherwise it is looked up as a logical name
    in the config map. An unknown name returns "" (the caller skips, fail-soft).
    """
    channel = (channel or "").strip()
    if not channel:
        return ""
    # Raw id heuristic: Slack ids are like C0AJ37QHMFB / G… / D… — letters+digits,
    # no lowercase. A logical name ("ops", "zip-files") has lowercase or a dash.
    if channel[0] in "CGD" and channel.isupper() and channel.isalnum():
        return channel
    return _slack_config_channels().get(channel, "")


# ---------------------------------------------------------------------------
# A dependency-free edit-in-place fallback — used when slack_helpers is absent but
# a client exists (an injected fake, or a hand-built one). Mirrors the slice of
# `slack_helpers.LiveMessage` the driver uses (`.update(text, force=)` + `.ts`),
# minus the throttle (correctness is "one edited message"; the throttle is a
# rate-limit nicety the real LiveMessage adds). Like LiveMessage, transport errors
# are swallowed so a streaming UI never crashes its producer.
# ---------------------------------------------------------------------------


class _InlineLive:
    """Minimal post-then-update: first `update` posts, later ones edit in place."""

    def __init__(self, client, channel: str):
        self._client = client
        self._channel = channel
        self._ts: str | None = None
        self._sent: str | None = None

    @property
    def ts(self) -> str | None:
        return self._ts

    def update(self, text: str, *, force: bool = False) -> None:  # noqa: ARG002 - parity
        if self._ts is None:
            try:
                resp = self._client.post_message(self._channel, text)
            except Exception:  # noqa: BLE001 - streaming must not crash the producer
                return
            self._ts = str((resp or {}).get("ts") or "")
            self._sent = text
            return
        if text == self._sent:   # nothing changed — skip the round-trip
            return
        try:
            self._client.update_message(self._channel, self._ts, text)
        except Exception:  # noqa: BLE001
            return
        self._sent = text


# ---------------------------------------------------------------------------
# The notifier.
# ---------------------------------------------------------------------------


class SlackNotifier:
    """Deliver a `Notification` to Slack — post (digest) or edit-in-place (status).

    Parameters
    ----------
    channel:
        A logical name (resolved via `slack_config.json`) or a raw channel id.
    token:
        Bot token; defaults to `$SLACK_BOT_TOKEN` / the workspace `.env`
        (`resolve_token`).
    root:
        Workspace root for `.env` resolution (the `SubstrateConfig.root`).
    dry_run:
        Render + report, send NOTHING (no `post_message`/`update_message` call).
    edit_in_place:
        None = auto (a `source="top"` notification edits one message; everything
        else posts). True/False forces.
    client:
        Inject a fake `SlackClient` in tests; None builds a real one lazily from the
        token at first `send`.
    min_interval:
        The `LiveMessage` throttle (seconds) for the edit-in-place surface.
    """

    name = "slack"

    def __init__(self, *, channel: str = "", token: str | None = None,
                 root: "os.PathLike[str] | str | None" = None,
                 dry_run: bool = False, edit_in_place: bool | None = None,
                 client=None, min_interval: float = 0.0):
        self._channel_arg = channel
        self._token = token
        self._root = Path(root) if root is not None else None
        self._dry_run = bool(dry_run)
        self._edit_in_place = edit_in_place
        self._client = client          # injected fake, or built lazily
        self._client_built = client is not None
        self._min_interval = max(0.0, float(min_interval))
        # Per-key LiveMessage cache for the edit-in-place surface (one re-edited
        # message per note.key for the lifetime of this notifier instance).
        self._live: dict[str, object] = {}

    # -- transport construction (lazy; the only place slack_helpers loads) ------

    def _ensure_client(self) -> tuple[object | None, str]:
        """(client, "") on success, or (None, reason) — never raises (fail-soft)."""
        if self._client is not None:
            return self._client, ""
        if self._client_built:  # we already tried and failed
            return None, "no slack client"
        token = resolve_token(self._token, root=self._root)
        if not token:
            self._client_built = True
            return None, "no SLACK_BOT_TOKEN (set it in env or the workspace .env)"
        try:
            from slack_helpers import SlackClient
        except Exception:
            self._client_built = True
            return None, ("slack_helpers not installed — "
                          "`pip install dos-kernel[notify-slack]`")
        try:
            self._client = SlackClient(token)
        except Exception as e:  # pragma: no cover - defensive
            self._client_built = True
            return None, f"slack client init failed: {e}"
        self._client_built = True
        return self._client, ""

    def _live_message(self, channel: str, key: str):
        """A per-key edit-in-place handle. None if no client (fail-soft).

        Prefers `slack_helpers.LiveMessage` (it adds the throttle that keeps a
        high-frequency status stream under Slack's `chat.update` rate limit). When
        the extra is absent but a client EXISTS (e.g. an injected fake, or a
        hand-built client), it falls back to `_InlineLive` — a minimal
        post-then-update with the same surface — so the edit path never
        hard-depends on the optional dependency once a transport is in hand.
        """
        if key in self._live:
            return self._live[key]
        client, _ = self._ensure_client()
        if client is None:
            return None
        try:
            from slack_helpers import LiveMessage
            lm: object = LiveMessage(client, channel, min_interval=self._min_interval)
        except Exception:
            lm = _InlineLive(client, channel)
        self._live[key] = lm
        return lm

    # -- delivery ---------------------------------------------------------------

    def _wants_edit(self, note: Notification) -> bool:
        if self._edit_in_place is not None:
            return self._edit_in_place
        return note.source == "top"   # the live-status surface edits by default

    def send(self, note: Notification) -> NotifyResult:
        """Deliver `note`. Returns a `NotifyResult`; NEVER raises (fail-soft)."""
        channel = resolve_channel(self._channel_arg)
        if not channel:
            return NotifyResult(
                delivered=False,
                detail=f"no channel (got {self._channel_arg!r}; "
                       f"name not in slack_config.json or not a raw id)",
            )

        edit = self._wants_edit(note)

        if self._dry_run:
            how = "edit-in-place" if edit else "post"
            return NotifyResult(
                delivered=False,
                detail=f"[dry-run] would {how} to {channel} "
                       f"({note.severity.value}: {note.title})",
            )

        if edit:
            return self._send_edit(channel, note)
        return self._send_post(channel, note)

    def _send_post(self, channel: str, note: Notification) -> NotifyResult:
        client, reason = self._ensure_client()
        if client is None:
            return NotifyResult(delivered=False, detail=reason)
        blocks = build_blocks(note)
        try:
            resp = client.post_message(channel, note.title, blocks=blocks)
        except Exception as e:  # noqa: BLE001 - advisory; report, don't crash
            return NotifyResult(delivered=False, detail=f"error: {e}")
        ts = str((resp or {}).get("ts") or "")
        return NotifyResult(delivered=True, detail=f"posted ts={ts}", ref=ts)

    def _send_edit(self, channel: str, note: Notification) -> NotifyResult:
        lm = self._live_message(channel, note.key or "dos-notify")
        if lm is None:
            client, reason = self._ensure_client()
            return NotifyResult(delivered=False, detail=reason or "no live message")
        # LiveMessage streams TEXT (its body is the running log); we feed the
        # title + summary, which is the at-a-glance line plus the full screen.
        text = note.title if not note.summary else f"{note.title}\n{note.summary}"
        try:
            lm.update(text, force=True)
        except Exception as e:  # noqa: BLE001 - LiveMessage already swallows, double-net
            return NotifyResult(delivered=False, detail=f"error: {e}")
        ts = str(getattr(lm, "ts", "") or "")
        return NotifyResult(delivered=True, detail="edited" if ts else "edit queued", ref=ts)
