"""dos.chat_control — the generic, transport-agnostic CONTROL SURFACE (docs/382).

A chat line in, a plain-text reply out. That is the whole job. A person on a
phone types ``top`` and gets the fleet screen; types ``decisions`` and gets the
operator queue; types ``help`` and gets the menu. The same router serves any
chat transport — WhatsApp today (``dos.drivers.whatsapp_bridge``), SMS or a
Telegram bot tomorrow — because it names NO transport: it speaks only ``str``
in and ``ChatReply`` out. WhatsApp lives in a driver; this generic surface does
not.

Where it sits
=============

This is a layer-3 **helper** (a policy-free shell, like ``cli`` itself): it
imports the read-only projections (``dispatch_top``, ``decisions``,
``plan_board``) and the config seam, renders them to compact text, and returns.
It takes no lease, mutates no state, names no vendor.

Two disciplines it inherits from the projections it wraps
=========================================================

  * **Read-only — by construction, not by habit.** The command table holds ONLY
    observe verbs (``top``/``decisions``/``plan``/``doctor``/``help``). There is
    no ``arbitrate``/``spawn``/``commit`` here: a stranger who guesses your
    WhatsApp number can READ the fleet, never DRIVE it. Mutating control is a
    later, separately-gated surface (it would need an authenticated allowlist —
    docs/382 §"the mutation gap"); this MVP is the safe floor. The
    ``test_chat_control`` suite pins the verb set so a mutating verb cannot be
    added here by accident.
  * **Fail-soft.** ``handle`` never raises. A broken projection, an unknown
    verb, a torn workspace — all degrade to a friendly ``ChatReply(ok=False)``
    with a one-line reason. A chat surface that 500s on a bad word is useless;
    this one always answers.

Phone-shaped output
===================

The projection renderers are built for a terminal; a phone is narrow and a
WhatsApp text body caps near 4096 chars. ``_compact`` caps the reply to
``max_lines`` and ``max_chars`` (with an honest "… (+N more lines)" marker), so
a giant ``dos top`` screen arrives as a readable digest, never a wall of text
or a rejected over-long message. It does NOT hard-wrap (that mangles the lane
tables); it caps, and tells you it capped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from dos import config as _config

# A WhatsApp text body caps at 4096 chars; SMS segments far shorter. Leave
# headroom below 4096 for a transport that prepends a header — the caller can
# lower it. Lines are capped first (a phone scrolls badly past ~40 rows).
_DEFAULT_MAX_LINES = 40
_DEFAULT_MAX_CHARS = 3500


@dataclass(frozen=True)
class ChatReply:
    """One reply to one chat line — pure data, no wire format.

    ``text`` is the plain-text body a transport sends verbatim. ``command`` is
    the matched verb (``"top"`` / ``"help"`` / ``"unknown"``) for logging and
    tests. ``ok`` is False for an unknown verb or a degraded projection, so a
    caller can route errors differently if it wants (the default transport just
    sends ``text`` either way).
    """

    text: str
    command: str
    ok: bool = True

    def to_dict(self) -> dict:
        return {"text": self.text, "command": self.command, "ok": self.ok}


@dataclass(frozen=True)
class RenderCtx:
    """The phone-shaping budget handed to every command handler."""

    max_lines: int = _DEFAULT_MAX_LINES
    max_chars: int = _DEFAULT_MAX_CHARS


@dataclass(frozen=True)
class CommandSpec:
    """One row of the control vocabulary — the registry IS the surface.

    Adding a verb is one entry here; that is what makes the surface generic. A
    handler is ``(cfg, args, ctx) -> str``: it returns the raw body, and
    ``handle`` does the compacting, so a handler never worries about length.
    """

    name: str
    aliases: tuple[str, ...]
    summary: str
    handler: Callable[..., str]


# ---------------------------------------------------------------------------
# Compacting — cap lines, then chars, with an honest truncation marker.
# ---------------------------------------------------------------------------


def _compact(text: str, *, max_lines: int, max_chars: int) -> str:
    """Cap `text` to a phone-readable size; never raise, never silently lie."""
    text = (text or "").rstrip("\n")
    if not text:
        return "(empty)"
    lines = text.split("\n")
    dropped = 0
    if max_lines > 0 and len(lines) > max_lines:
        dropped = len(lines) - max_lines
        lines = lines[:max_lines]
    out = "\n".join(lines)
    if dropped:
        out += f"\n… (+{dropped} more line{'s' if dropped != 1 else ''}; run the CLI for the full screen)"
    if max_chars > 0 and len(out) > max_chars:
        # Cap on a line boundary when we can, so we never cut a row mid-cell.
        clipped = out[: max_chars - 1]
        nl = clipped.rfind("\n")
        if nl > max_chars // 2:
            clipped = clipped[:nl]
        out = clipped.rstrip() + "\n…"
    return out


# ---------------------------------------------------------------------------
# The read-only command handlers — each returns the raw body; handle() compacts.
# ---------------------------------------------------------------------------


def _cmd_top(cfg, args, ctx) -> str:  # noqa: ARG001 - args unused (no flags yet)
    """The live fleet watchdog: lanes, leases, recent verdicts, commits."""
    from dos import dispatch_top as _dtop

    frame = _dtop.snapshot(cfg)
    return _dtop.render_frame_text(frame)


def _cmd_decisions(cfg, args, ctx) -> str:
    """The operator-decision queue — what needs a human, ranked."""
    from dos import decisions as _decisions

    # `all` widens past HUMAN-only to ORACLE/JUDGE rows too (parity with `--all`).
    resolver = None if (args and args[0].lower() in ("all", "--all")) else "HUMAN"
    rows = _decisions.collect_decisions(cfg, resolver=resolver)
    body = _decisions.render_list_plain(rows)
    return body or "no pending decisions — the queue is clear."


def _cmd_plan(cfg, args, ctx) -> str:  # noqa: ARG001
    """The work-terrain board: every phase, the plan's claim vs the oracle."""
    from dos import plan_board as _pb

    frame = _pb.snapshot(cfg)
    return _pb.render_frame_text(frame)


def _cmd_doctor(cfg, args, ctx) -> str:  # noqa: ARG001
    """A one-glance workspace report: version, root, lane taxonomy, git."""
    import dos

    concurrent = tuple(cfg.lanes.concurrent)
    exclusive = tuple(cfg.lanes.exclusive)
    has_git = (cfg.root / ".git").exists()
    lines = [
        f"DOS v{dos.__version__} · {cfg.root.name or cfg.root}",
        f"workspace: {cfg.root}",
        f"git workspace: {'yes' if has_git else 'no'}",
        f"concurrent lanes ({len(concurrent)}): {', '.join(concurrent) or '(none)'}",
        f"exclusive lanes ({len(exclusive)}): {', '.join(exclusive) or '(none)'}",
    ]
    return "\n".join(lines)


def _cmd_help(cfg, args, ctx) -> str:  # noqa: ARG001
    """The menu — every verb this surface answers, one per line."""
    lines = ["DOS control — reply with a command:"]
    for spec in COMMANDS:
        if spec.name == "help":
            continue
        lines.append(f"  {spec.name} — {spec.summary}")
    lines.append("  help — this menu")
    lines.append("(read-only: this surface observes the fleet, it does not drive it)")
    return "\n".join(lines)


# The registry IS the surface. Order is the help-menu order.
COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("top", ("fleet", "status", "t"),
                "live fleet watchdog (lanes, leases, verdicts)", _cmd_top),
    CommandSpec("decisions", ("decide", "queue", "q"),
                "the operator-decision queue", _cmd_decisions),
    CommandSpec("plan", ("board",),
                "the work-terrain board (phases vs the oracle)", _cmd_plan),
    CommandSpec("doctor", ("health", "info", "whereami"),
                "workspace report (version, lanes, git)", _cmd_doctor),
    CommandSpec("help", ("?", "menu", "commands"),
                "this menu", _cmd_help),
)


def commands() -> tuple[CommandSpec, ...]:
    """The command registry — for help text and tests."""
    return COMMANDS


# name/alias → spec, built once.
_BY_NAME: dict[str, CommandSpec] = {}
for _spec in COMMANDS:
    _BY_NAME[_spec.name] = _spec
    for _alias in _spec.aliases:
        _BY_NAME[_alias] = _spec


def _parse(message: str) -> tuple[str, list[str]]:
    """A raw chat line → (verb, args). Tolerant of `/top`, `dos top`, `!top`."""
    text = (message or "").strip()
    # Strip a leading prefix people reflexively type.
    for prefix in ("/", "!"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    tokens = text.split()
    if tokens and tokens[0].lower() == "dos":
        tokens = tokens[1:]
    if not tokens:
        return "", []
    return tokens[0].lower(), tokens[1:]


def handle(
    message: str,
    config=None,
    *,
    max_lines: int = _DEFAULT_MAX_LINES,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> ChatReply:
    """Route one chat line to a read-only DOS verb; return a compact reply.

    `config` defaults to the process-active config (the `dos chat` CLI path,
    which `_apply_workspace`-es first); a long-running bridge passes the
    workspace explicitly per request (the MCP per-call posture). NEVER raises:
    an unknown verb returns the menu (`ok=False`), and any projection failure
    degrades to a one-line error (`ok=False`) — a chat surface always answers.
    """
    cfg = config if config is not None else _config.active()
    ctx = RenderCtx(max_lines=max_lines, max_chars=max_chars)
    verb, args = _parse(message)

    if not verb:
        body = _cmd_help(cfg, args, ctx)
        return ChatReply(text=_compact(body, max_lines=max_lines, max_chars=max_chars),
                         command="help", ok=True)

    spec = _BY_NAME.get(verb)
    if spec is None:
        body = (f"unknown command: {verb!r}\n\n" + _cmd_help(cfg, args, ctx))
        return ChatReply(text=_compact(body, max_lines=max_lines, max_chars=max_chars),
                         command="unknown", ok=False)

    try:
        body = spec.handler(cfg, args, ctx)
    except Exception as e:  # noqa: BLE001 - fail-soft: a bad projection must not crash the surface
        return ChatReply(
            text=f"⚠ {spec.name} is unavailable right now: {e}",
            command=spec.name, ok=False)

    return ChatReply(
        text=_compact(body, max_lines=max_lines, max_chars=max_chars),
        command=spec.name, ok=True)


# ---------------------------------------------------------------------------
# Chat-bridge seam — resolve an INBOUND transport binding by NAME.
#
# A bridge is the inbound side of a chat transport (WhatsApp today): it owns a
# vendor wire shape, so it lives in a driver and is discovered through the
# `dos.chat_bridges` entry-point group — the CLI names it by string, never by a
# static import (the `resolve_notifier` discipline + the no-kernel-imports-a-
# driver litmus). Adding a transport (Telegram, SMS) is one more entry point;
# this generic surface and the CLI never change.
# ---------------------------------------------------------------------------

BRIDGE_ENTRY_POINT_GROUP = "dos.chat_bridges"


def discover_bridges(*, _stderr=None) -> dict[str, object]:
    """Every registered chat bridge as ``{name: serve_callable}``. Never raises.

    A plugin that fails to load is SKIPPED with a one-line stderr note rather than
    crashing — the `resolve_notifier` posture. Entry-point I/O, so this is a
    call-boundary helper.
    """
    import sys

    stderr = _stderr if _stderr is not None else sys.stderr
    out: dict[str, object] = {}
    try:
        from importlib.metadata import entry_points
    except Exception:  # pragma: no cover - importlib.metadata always present py3.11+
        return out
    try:
        eps = entry_points(group=BRIDGE_ENTRY_POINT_GROUP)
    except TypeError:  # pragma: no cover - py<3.10 selectable-API fallback
        eps = entry_points().get(BRIDGE_ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - defensive: never let discovery crash a call
        return out
    for ep in sorted(eps, key=lambda e: e.name):
        try:
            out[ep.name] = ep.load()
        except Exception as e:  # pragma: no cover - depends on third-party plugin
            print(f"warning: chat bridge {ep.name!r} failed to load ({e}); skipping",
                  file=stderr)
            continue
    return out


def resolve_bridge(name: str, *, _stderr=None):
    """Resolve a chat bridge's ``serve`` callable by name; loud on unknown.

    Returns the loaded ``serve(cfg, *, host, port, verify_token, …) -> int``
    callable. Raises ValueError naming the known bridges on an unknown name (an
    operator typo is a loud error, the `resolve_notifier` discipline).
    """
    found = discover_bridges(_stderr=_stderr)
    serve = found.get(name)
    if serve is None:
        known = ", ".join(sorted(found)) or "(none registered)"
        raise ValueError(f"unknown chat bridge {name!r}; known: {known}")
    return serve
