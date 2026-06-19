# 387 — Clickable notifications: the action affordance

> **A notification that names a problem but no next step is a dead end.** DOS
> already pushes two read-only projections out a transport — `dos decisions`
> ("what needs a human") and `dos top` ("what is running now"), plus the
> `dos pulse` digest (docs/225, docs/267). Each one tells the operator *that*
> something needs them. None of them said *how to look*. This adds the one
> missing field: the **relevant view to open**, carried on every notification and
> rendered as a click target by whatever transport delivered it.

## The shape (decided)

One new pure value type on the notification spine, plus a per-transport renderer.
The kernel says **which** read-only view answers a notification; each transport
decides **how** to make it clickable.

```python
@dataclass(frozen=True)
class NotifyAction:
    label: str        # "review the decisions queue"
    command: str      # "dos decisions --workspace <root>"  (copy-paste runs it)
    url: str = ""     # OPTIONAL explicit deep link a host/driver may set
```

`Notification` gains `action: NotifyAction | None`, threaded through `to_dict()`
(a JSON-null when absent, never a missing key). A closed `action_for_source` map
fills it from the projection name:

| source | opens | command |
|---|---|---|
| `decisions` | the decisions queue | `dos decisions --workspace <root>` |
| `top` | the live fleet status | `dos top --workspace <root>` |
| `pulse` | the fleet pulse | `dos pulse --workspace <root>` |

An unknown source returns `None` — no guessed affordance (the `resolve_*`
fail-quiet posture). The workspace is folded into the command (quoted when it has
spaces) so the copy-pasted line runs from anywhere; the `top` adapter
self-sources the root from `frame.workspace` when the caller passes none.

## Why a field on the kernel, not a transport trick

The same reason `Notification` carries `key` rather than letting each driver
invent its own edit handle: the moment a *second* transport wants the affordance
(the terminal AND Slack AND a webhook dashboard), a per-driver hack is the thing
you tear out. The relevant view is a **DOS fact**, not a Slack detail — so it
lives on the neutral payload, and a new projection adds one row to
`action_for_source`, never a driver edit.

## It stays inside the advisory floor (docs/99)

Every target is a **read-only** projection. Clicking OPENS a view; it never
enacts a stop. A LIVENESS row's paste-to-stop command is still a *field value*
the operator runs by choice — the action affordance does not promote it to a
one-click stop. A notification reports; it does not act on the fleet.

## How each transport renders it

- **Terminal** (`dos notify` / `dos pulse` plain output): a `→ <label>: <command>`
  line. When stdout is an interactive terminal (and not `DOS_NO_HYPERLINKS=1` /
  `TERM=dumb`), the command text is ALSO an **OSC 8 hyperlink** — the
  `ESC ] 8 ; ; URI ST text ESC ] 8 ; ; ST` sequence iTerm2 / WezTerm / kitty /
  Windows Terminal / the VS Code terminal all render as a click target. The link
  target is the action's `url` if set, else a `file://` of the workspace so the
  click still lands somewhere relevant. A pipe / CI log / a terminal that does not
  understand OSC 8 shows the bare copy-pasteable verb — the enhancement degrades,
  it never breaks (the `dispatch_top` `isatty` posture).
- **Slack** (`notify_slack`): a Block Kit **link button** when the action carries
  an explicit `url` (a button with no URL has no backend here, and the advisory
  floor forbids one that enacts — so a button appears only when it can open
  something). The open-command is also folded into the context line so a plain
  Slack still shows the `dos …` verb.
- **Webhook** (`notify_webhook`): the structured `action` object rides in the JSON
  body for free (via `to_dict()`) for a consumer that renders its own button; a
  dumb chat hook that only reads `text` gets `⟶ open: <command>` appended.
- **WhatsApp** (`notify_whatsapp`): unchanged — a phone cannot open a desktop TUI,
  so an open-a-TUI affordance is not *relevant* there (the kept-honest "or
  similar that's relevant" line).

## Litmus tests this keeps

- **Kernel names no transport / vendor.** `NotifyAction` and `action_for_source`
  name only generic `dos` verbs; the OSC 8 escape lives in the CLI presentation
  layer (next to the severity glyphs), never in `notify.py`. Block Kit buttons
  stay in the Slack driver.
- **Advisory floor.** Every action target is a read-only projection; no action
  enacts a lease, a stop, or a mutation.
- **Fail-soft / degrade-clean.** Hyperlinks are an enhancement gated on `isatty`;
  an action-less notification serializes a JSON-null and renders no line.

## Files

| Path | Layer | What |
|---|---|---|
| `src/dos/notify.py` | 1 (kernel) | `NotifyAction`, `action_for_source`, `Notification.action`, adapters take `root` |
| `src/dos/cli.py` | 3 (helper) | `_hyperlinks_enabled` / `_osc8` / `_action_line`; the open line in `cmd_notify` / `cmd_pulse` |
| `src/dos/drivers/notify_slack.py` | 4 (driver) | link button + context-line command |
| `src/dos/drivers/notify_webhook.py` | 4 (driver) | `⟶ open:` folded into the synthesized `text` |
| `tests/test_notify*.py` | — | the action value type, the source map, the per-transport renderers |
