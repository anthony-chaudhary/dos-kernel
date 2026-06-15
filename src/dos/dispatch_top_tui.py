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


def _poll_keypress(timeout: float):
    """One pressed key (lowercased str) within ``timeout`` seconds, else None.

    Non-blocking, stdlib-only, cross-platform. Windows: ``msvcrt.kbhit`` +
    ``getwch``. POSIX: ``select`` on a cbreak'd stdin. Returns None where raw
    single-key input is unavailable (no tty, or the platform shim is absent) —
    the live loop then behaves exactly as a plain ``time.sleep`` did, so the
    keypress is an ENHANCEMENT, never a requirement. Never blocks past
    ``timeout``; never raises (any odd stream/permission folds to a sleep+None).

    This is the second structural gate (after ``run_top``'s ``isatty`` guard)
    that keeps the `a`-to-arm key unreachable headless: a non-interactive stdin
    yields no key here, so an agent's piped `dos top` can never trigger the arm.
    """
    timeout = max(0.0, float(timeout))
    fd_in = getattr(sys.stdin, "fileno", None)
    # Windows: msvcrt reads the console directly, no fd dance.
    try:
        import msvcrt  # type: ignore
    except ImportError:
        msvcrt = None
    if msvcrt is not None:
        deadline = time.monotonic() + timeout
        while True:
            if msvcrt.kbhit():
                try:
                    ch = msvcrt.getwch()
                except Exception:  # pragma: no cover - console quirk
                    return None
                return ch.lower() if ch else None
            if time.monotonic() >= deadline:
                return None
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    # POSIX: select on a cbreak stdin. If stdin is not a real tty fd, fall back
    # to a plain sleep + None (the headless / piped case).
    try:
        import select
        import termios
        import tty
    except ImportError:  # pragma: no cover - non-posix without msvcrt
        time.sleep(timeout)
        return None
    if fd_in is None or not getattr(sys.stdin, "isatty", lambda: False)():
        time.sleep(timeout)
        return None
    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except Exception:  # pragma: no cover - not a real terminal
        time.sleep(timeout)
        return None
    try:
        tty.setcbreak(fd)
        r, _, _ = select.select([fd], [], [], timeout)
        if r:
            ch = sys.stdin.read(1)
            return ch.lower() if ch else None
        return None
    except Exception:  # pragma: no cover - defensive
        return None
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:  # pragma: no cover
            pass


def _tui_arm(cfg, frame, live) -> bool:
    """Arm the surfaced SELF_MODIFY window from the `dos top` approve key.

    The TUI half of docs/328 Phase 2. A no-op (returns False) unless there is a
    fresh, NOT-yet-armed block to approve. Drops out of the live alternate
    screen, re-asserts the interactivity gate (belt-and-suspenders over
    ``run_top``'s isatty guard + ``_poll_keypress``), takes one explicit y/N,
    and on yes WRITES the window via the SAME ``cli._arm_write`` the CLI `arm`
    verb uses (so the two surfaces never diverge). Scoped to the blocked target
    when the record named one (the ergonomic, least-privilege default). Returns
    True iff a window was armed."""
    block = getattr(frame, "self_modify_block", None)
    if block is None or getattr(block, "armed", False):
        return False
    import datetime as _dt
    from dos import cli as _cli
    if not _cli._require_interactive_operator():
        return False
    target = getattr(block, "target", "") or ""
    scope = (target,) if target else ()
    live.stop()  # leave the alternate screen so the prompt is visible
    try:
        _scope_show = target if target else "the whole T1 kernel set"
        if not _cli._confirm_interactive(
                f"Arm a 30-min SELF_MODIFY override over {_scope_show}?"):
            return False
        now = _dt.datetime.now(_dt.timezone.utc)
        until = now + _dt.timedelta(minutes=30)
        reason = (f"dos top approve: {target}" if target
                  else "dos top approve: supervised kernel edit")
        _cli._arm_write(cfg.paths.root, reason, until=until, scope=scope)
        return True
    finally:
        live.start(refresh=True)


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
                frame = _top.snapshot(cfg)
                live.update(_renderable_for(frame), refresh=True)
                # The cadence wait is also the keypress window (docs/328 Phase 2):
                # `q` quits, `a` approves a surfaced SELF_MODIFY block. Where raw
                # input is unavailable (headless) `_poll_keypress` just sleeps and
                # returns None, so the loop is byte-behaviour-identical to the old
                # `time.sleep(interval)` there.
                key = _poll_keypress(interval)
                if key == "q":
                    break
                if key == "a":
                    _tui_arm(cfg, frame, live)
    except KeyboardInterrupt:
        # Read-only: nothing to undo. Print a final static frame so the operator
        # is left with the last state on the restored terminal, not a blank.
        print(_render_once(cfg))
        return 0


def _renderable(cfg):
    """Build the rich renderable for one fresh snapshot (back-compat entry)."""
    return _renderable_for(_top.snapshot(cfg))


def _renderable_for(frame):
    """Build the rich renderable for an already-taken frame, or a plain string.

    Split from the snapshot so the live loop can snapshot ONCE and hand the same
    frame to both this renderer and `_tui_arm` (the approve key acts on exactly
    what is on screen). Uses a `rich.panel.Panel` per section when rich is
    importable (it is, inside `run_top`'s live branch); falls back to the plain
    text frame defensively.
    """
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
    # `a` arms only a fresh, not-yet-armed block (docs/328 Phase 2) — surface the
    # key right on the red panel so the operator sees the one-keystroke approve.
    armable = bool(frame.self_modify_block
                   and not getattr(frame.self_modify_block, "armed", False))
    if banner:
        armed = bool(frame.self_modify_block and frame.self_modify_block.armed)
        body = banner + ("\n\n[press 'a' to arm a 30-min override]" if armable else "")
        sections.append(_panel(
            "self-modify override" if armed else "kernel edit blocked",
            body, "green" if armed else "red"))
    _keys = ("read-only · 'a' arm · 'q'/Ctrl-C quit" if armable
             else "read-only · 'q'/Ctrl-C to quit")
    sections.extend([
        _panel("lanes", _body(_top.render_lanes_text(frame.lanes)), "cyan"),
        _panel("recent verdicts", _body(_top.render_verdicts_text(frame.verdicts)), "magenta"),
        _panel("recent commits", _body(_top.render_activity_text(frame.activity)), "green"),
        Text(f"{_keys} · arming is the only write this screen can make", style="dim"),
    ])
    return Group(*sections)
