#!/usr/bin/env python3
"""Standalone entry-point for `dos hook stop-failure` — StopFailure notifier.

Wired from .claude/settings.json (and claude-plugin/hooks/hooks.json) as:

    python scripts/rate_limit_rewake.py [--workspace PATH] [--debug]

This lets the hook fire without `dos` being on $PATH — the dos-kernel package
just needs to be importable (editable install via `pip install -e .` in this
repo).  The full logic lives in dos.cli.cmd_hook_stop_failure (backed by the
pure dos.breaker + dos.stop_failure_sensor I/O layer); this file is the thin
callable boundary so the settings path stays readable.

NOTE — this is a NOTIFIER, not a resumer. A StopFailure hook's output and exit
code are ignored by the harness, so it can never re-launch the session. The
harness retries transient walls itself (CLAUDE_CODE_MAX_RETRIES; set
CLAUDE_CODE_RETRY_WATCHDOG=1 to retry 429/529 capacity errors indefinitely).
This hook records the wall into the per-session breaker, attributes it to its
seat, and escalates to the operator when the breaker OPENs. It always exits 0.
The legacy `rate_limit_rewake` filename is kept so existing settings keep working.

Exit code: always 0 (the StopFailure contract ignores it regardless).
"""
import argparse
import sys

from dos.cli import cmd_hook_stop_failure


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="StopFailure asyncRewake hook — backoff-retry on API failures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--workspace", default=None,
                   help="workspace root (default: event cwd → cwd)")
    p.add_argument("--session-id", dest="session_id", default=None,
                   help="override the session key read from STDIN")
    p.add_argument("--success", action="store_true",
                   help="record a clean Stop (heals the breaker); wire from Stop hook")
    p.add_argument("--max-consecutive", dest="max_consecutive", type=int, default=None,
                   help="open circuit after N consecutive failures (default 5)")
    p.add_argument("--max-total", dest="max_total", type=int, default=None,
                   help="open circuit after N total failures (default 50)")
    p.add_argument("--debug", action="store_true",
                   help="emit diagnostics to STDERR")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(cmd_hook_stop_failure(_parse()))
