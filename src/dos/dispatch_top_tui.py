"""The live `dos top` screen — a `rich.live` poll loop over `dispatch_top.snapshot`.

The rendering layer for `dos top`'s interactive mode, the live-ops sibling of
`decisions_tui`. It re-`snapshot()`s the workspace on a cadence and redraws — a
read-only watchdog the operator leaves open during a fleet run. It mutates
nothing: no lease, no launch, no write path; the only effect is drawing.

**Graceful degradation (the floor that always works).** `rich` is an OPTIONAL
dependency (the `[tui]` extra) — the kernel core stays PyYAML-only. So this module
is import-light at module scope (no top-level `import rich`), and `run_top`
imports rich lazily; on ImportError (rich not installed) or a non-interactive
stdout (a pipe / CI), it falls straight through to a single
`dispatch_top.render_frame_text` frame and returns. `dos top` therefore ALWAYS
works — `dos top --once` and a piped `dos top` print the plain frame everywhere;
the live redraw is the enhancement where rich is present and stdout is a tty. This
is exactly the lazy-import + plain-floor split `decisions_tui` uses for curses.
"""

from __future__ import annotations

import sys
import time

from dos import config as _config
from dos import dispatch_top as _top


def _render_once(cfg) -> str:
    """One plain-text frame — the floor and the `--once` body."""
    return _top.render_frame_text(_top.snapshot(cfg))


def run_top(
    config=None, *, once: bool = False, interval: float = 5.0,
) -> int:
    """Run the live `dos top` screen, or print one frame and return.

    Returns a process exit code (always 0 — a read-only viewer has nothing to
    fail). `once=True`, a non-interactive stdout, or a missing `rich` all collapse
    to a single plain-text frame. Otherwise a `rich.live` loop redraws every
    `interval` seconds until the operator interrupts (Ctrl-C / q-via-SIGINT),
    which exits cleanly — there is no state to unwind.
    """
    cfg = _config.ensure(config)

    # The floor: one frame, no live loop. Taken for --once, a piped/CI stdout, or
    # a box without rich. `isatty` guards the pipe case so `dos top | less` and
    # CI capture get clean text, never escape codes.
    interactive = bool(getattr(sys.stdout, "isatty", lambda: False)())
    if once or not interactive:
        print(_render_once(cfg))
        return 0

    try:
        from rich.console import Console
        from rich.live import Live
    except ImportError:
        # rich not installed (the [tui] extra is absent) — print one frame and
        # tell the operator how to get the live screen, then return cleanly.
        print(_render_once(cfg))
        print("\n(install `dos-kernel[tui]` for the live auto-refreshing screen)")
        return 0

    console = Console()
    interval = max(0.5, float(interval))
    try:
        with Live(_renderable(cfg), console=console, screen=True,
                  auto_refresh=False, transient=True) as live:
            while True:
                live.update(_renderable(cfg), refresh=True)
                time.sleep(interval)
    except KeyboardInterrupt:
        # Read-only: nothing to undo. Print a final static frame so the operator
        # is left with the last state on the restored terminal, not a blank.
        print(_render_once(cfg))
        return 0


def _renderable(cfg):
    """Build the rich renderable for one frame, or a plain string if rich is gone.

    Kept separate so the live loop body is trivial. Uses a `rich.panel.Panel`
    per section when rich is importable (it is, inside `run_top`'s live branch);
    falls back to the plain text frame defensively.
    """
    frame = _top.snapshot(cfg)
    try:
        from rich.console import Group
        from rich.panel import Panel
        from rich.text import Text
    except Exception:  # pragma: no cover - rich present in the live branch
        return _top.render_frame_text(frame)

    def _panel(title: str, body: str, style: str) -> Panel:
        return Panel(Text(body), title=f"[bold]{title}[/]", border_style=style,
                     title_align="left")

    # Reuse the pure plain-text section renderers as the panel bodies — one source
    # of truth for content; rich only adds the frame/colour. (Strip each section's
    # own header line since the panel title carries it.)
    def _body(text: str) -> str:
        lines = text.splitlines()
        return "\n".join(lines[1:]) if len(lines) > 1 else ""

    header = Text(
        f"dos top · {frame.workspace} · {frame.now_iso}"
        + ("" if frame.initialized else "   (no dos.toml — generic main/global)"),
        style="bold cyan",
    )
    # The SELF_MODIFY banner rides above the lanes — but ONLY when there is a
    # fresh block (issue #145), so a quiet screen is unchanged. Reuse the pure
    # plain-text renderer; an armed window reads green (allowed), a block red.
    sections: list = [header]
    banner = _top.render_self_modify_banner(frame.self_modify_block)
    if banner:
        armed = bool(frame.self_modify_block and frame.self_modify_block.armed)
        sections.append(_panel(
            "self-modify override" if armed else "kernel edit blocked",
            banner, "green" if armed else "red"))
    sections.extend([
        _panel("lanes", _body(_top.render_lanes_text(frame.lanes)), "cyan"),
        _panel("recent verdicts", _body(_top.render_verdicts_text(frame.verdicts)), "magenta"),
        _panel("recent commits", _body(_top.render_activity_text(frame.activity)), "green"),
        Text("read-only · Ctrl-C to quit · this screen mutates nothing", style="dim"),
    ])
    return Group(*sections)
