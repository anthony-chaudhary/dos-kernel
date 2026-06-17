# 382 — The WhatsApp control surface, on a generic chat bridge

> Control DOS from your phone. Type `top`, see the fleet. The phone is just one
> mouth on a surface that names no phone.

## The want

An operator away from a terminal still wants to ask DOS what is going on: which
lanes are live, what is stalled, what needs a human. The first ask is WhatsApp —
text `top`, get the fleet screen back. But the want underneath is bigger than one
chat app: a **generic surface for control** that any messaging transport can
drive, with `dos top` and the other read-only utilities as the first useful thing
on it.

## The shape

Three parts, each in the layer the architecture contract assigns it. The split is
the whole point: the control LOGIC names no vendor; only the WhatsApp wire shape
does, and that lives in drivers.

```
   phone ──WhatsApp──>  whatsapp_bridge (driver)  ──text──>  chat_control (helper)
                              │  inbound webhook                 │  generic router
                              │  (vendor wire shape)             │  (names no vendor)
                              ▼                                  ▼
                        notify_whatsapp (driver) <──reply──  read-only projections
                              │  outbound Cloud API              (top / decisions /
                              ▼                                   plan / doctor)
                           phone
```

1. **`dos.chat_control`** (layer-3 helper) — the generic control surface. A chat
   line in, a compact plain-text reply out. It maps a small, CLOSED vocabulary of
   **read-only** verbs (`top`, `decisions`, `plan`, `doctor`, `help`) to the
   kernel's own read-only projections, renders them phone-shaped, and returns. It
   knows nothing about WhatsApp. Swap the transport and this does not change.

2. **`dos.drivers.notify_whatsapp`** (layer-4 driver) — the OUTBOUND transport: a
   `dos.notifiers` occupant that renders a `Notification` to a WhatsApp text and
   POSTs it through the official Meta Cloud API. Stdlib-only (urllib), so it ships
   in the core like `notify_webhook`. `dos notify --notifier whatsapp` pushes any
   projection; the bridge reuses it to send replies.

3. **`dos.drivers.whatsapp_bridge`** (layer-4 driver) — the INBOUND half: the
   webhook receiver. It answers Meta's GET verification handshake, parses an
   inbound POST into typed messages, routes each message's text through
   `chat_control`, and sends the reply back through `notify_whatsapp`. The
   WhatsApp wire shape (the `entry[].changes[].value.messages[]` envelope, the
   `hub.challenge` handshake) lives here and only here.

## Why these layers, and the litmus it has to clear

- **A transport is a driver** (CLAUDE.md). WhatsApp is a vendor; its name and wire
  format belong in `drivers/`, never in the kernel. `chat_control` is a helper
  because it is a policy-free shell over the read-only projections — the same
  class as `cli` itself.
- **The kernel imports no driver — even the CLI.** The vendor-agnosticism litmus
  (`tests/test_vendor_agnostic_kernel.py`) AST-checks that no `src/dos/*.py`
  statically `import dos.drivers.*`. So `dos whatsapp serve` cannot just import the
  bridge. It resolves it **by name** through a new entry-point group,
  `dos.chat_bridges`, exactly the way `resolve_notifier` finds a transport: the
  CLI names the string `"whatsapp"`, a helper (`chat_control.resolve_bridge`) does
  the dynamic `entry_points` lookup + `ep.load()`. The static import never exists.
- **The surface is extensible by registration, not by editing.** A Telegram or SMS
  bridge is one more `dos.chat_bridges` entry point + one more driver. Neither
  `chat_control` nor the CLI changes. That is what makes it a *generic* surface.

## The read-only floor (a deliberate constraint, not an omission)

The command vocabulary holds **observe verbs only**. There is no `arbitrate`,
`spawn`, `commit`, or `lease` reachable from a chat line. A stranger who guesses
your WhatsApp number can READ the fleet; they cannot DRIVE it. The
`test_chat_control` suite pins the exact verb set so a mutating verb cannot be
added here by accident.

This is the safe MVP and the right default. A mutating control surface is a real
future want, but it needs more than this MVP has:

### The mutation gap (future work)

- **Sender authentication.** This MVP authenticates the WEBHOOK (the
  `hub.verify_token` handshake proves the endpoint is yours) but not the SENDER.
  Before any verb can change state, the bridge needs a sender allowlist
  (`$DOS_WHATSAPP_ALLOW`, a set of E.164 numbers) and ideally the Cloud API's
  per-POST app-secret HMAC (`X-Hub-Signature-256`) verified.
- **Then, gated mutation.** A mutating verb would route through the same admission
  the rest of DOS uses (`arbitrate`/`refuse`), so a phone-driven action is
  adjudicated identically to a CLI one — the chat surface is a transport, never a
  bypass.

## Phone-shaping

The projection renderers target a terminal; a phone is narrow and a WhatsApp text
body caps near 4096 chars. `chat_control._compact` caps the reply to `max_lines`
then `max_chars`, with an honest `… (+N more lines)` marker — it does NOT
hard-wrap (that mangles the lane tables); it caps and says so. `notify_whatsapp`
caps again at the API's hard 4096 limit as a backstop.

## Surfaces added

- `dos chat <command…>` — run a control command, print the reply. Transport-free,
  so an operator (or a test) sees exactly what a phone would get. `--json` for the
  `{text, command, ok}` shape; `--max-lines` / `--max-chars` for the phone budget.
- `dos whatsapp serve [--host --port --verify-token]` — run the inbound webhook
  receiver. Front it with HTTPS at your edge; Meta requires a public HTTPS
  callback. Refuses to start without a verify token (an unverifiable webhook is a
  misconfiguration, not a default).
- `dos notify --notifier whatsapp` — push a projection outbound (the existing
  notify verb, now with a WhatsApp transport).

## Credentials (the `notify_webhook` ladder: explicit › `$ENV` › `<root>/.env`)

| key | meaning |
|---|---|
| `DOS_WHATSAPP_TOKEN` | Cloud API access token (bearer) |
| `DOS_WHATSAPP_PHONE_ID` | the sender's phone-number-id (the business number) |
| `DOS_WHATSAPP_TO` | default recipient, E.164 — only for unsolicited pushes |
| `DOS_WHATSAPP_VERIFY_TOKEN` | the webhook verification token |
| `DOS_WHATSAPP_API_BASE` / `_API_VERSION` | Graph host / version overrides |

## What is proven

- `tests/test_chat_control.py` — the router: every verb answers on a bare
  workspace, aliases + prefixes (`/top`, `dos top`), fail-soft on a broken
  projection, the line/char caps, the closed read-only verb set, the bridge
  resolver.
- `tests/test_notify_whatsapp.py` — the transport against a fake (no network): the
  Cloud-API body + endpoint, titled-alert vs title-less-reply rendering, dry-run,
  the credential ladder, every degrade path, resolution by name.
- `tests/test_whatsapp_bridge.py` — the inbound core: the handshake, parsing
  (skipping non-text / status callbacks / garbage), routing through
  `chat_control`, the always-200 fail-soft POST, the no-token refusal, resolution
  through the `dos.chat_bridges` seam.

The live socket (the `serve` HTTP shell) is exercised by manual dogfood; the unit
tests cover the pure request handlers it delegates to.
