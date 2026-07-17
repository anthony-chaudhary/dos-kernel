"""Cadence wrapper for `dos notify decisions`.

The kernel has no daemon. A host cron, scheduled workflow, or loop tick owns the
recurrence and calls this script. The script builds one `dos notify decisions`
command from environment variables plus optional CLI overrides, then executes it.

Default behavior is safe for credential-less workspaces:

    dos notify decisions --workspace . --notifier null --dry-run

Set DOS_NOTIFY_DECISIONS_NOTIFIER to a real transport name to deliver instead.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from collections.abc import Mapping, Sequence


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _env(env: Mapping[str, str], name: str, default: str = "") -> str:
    value = env.get(name, default)
    return value.strip() if isinstance(value, str) else default


def _env_bool(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = _env(env, name, "")
    if raw == "":
        return default
    lowered = raw.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ValueError(f"{name} must be one of: 1/0, true/false, yes/no, on/off")


def _env_int(env: Mapping[str, str], name: str, *, default: int) -> int:
    raw = _env(env, name, "")
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the scheduled `dos notify decisions` delivery tick."
    )
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--notifier", default=None)
    parser.add_argument("--channel", default=None)
    parser.add_argument("--url", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--top", type=int, default=None)
    parser.add_argument("--all", dest="all_rows", action="store_true", default=None)
    parser.add_argument("--human-only", dest="all_rows", action="store_false")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=None)
    parser.add_argument("--send", dest="dry_run", action="store_false")
    parser.add_argument("--json", dest="json", action="store_true", default=None)
    parser.add_argument("--plain", dest="json", action="store_false")
    parser.add_argument(
        "--dos-bin",
        default=None,
        help="DOS executable to invoke; defaults to $DOS_NOTIFY_DECISIONS_BIN or `dos`.",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return make_parser().parse_args(argv)


def build_command(args: argparse.Namespace, env: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if env is None else env

    notifier = args.notifier or _env(env, "DOS_NOTIFY_DECISIONS_NOTIFIER", "null") or "null"
    workspace = args.workspace or _env(env, "DOS_NOTIFY_DECISIONS_WORKSPACE", ".") or "."
    dos_bin = args.dos_bin or _env(env, "DOS_NOTIFY_DECISIONS_BIN", "dos") or "dos"
    top = args.top if args.top is not None else _env_int(env, "DOS_NOTIFY_DECISIONS_TOP", default=5)
    if top < 0:
        raise ValueError("--top must be >= 0")

    all_rows = (
        args.all_rows
        if args.all_rows is not None
        else _env_bool(env, "DOS_NOTIFY_DECISIONS_ALL", default=False)
    )
    json_output = (
        args.json
        if args.json is not None
        else _env_bool(env, "DOS_NOTIFY_DECISIONS_JSON", default=False)
    )
    dry_run = (
        args.dry_run
        if args.dry_run is not None
        else _env_bool(env, "DOS_NOTIFY_DECISIONS_DRY_RUN", default=(notifier == "null"))
    )

    command = [
        dos_bin,
        "notify",
        "decisions",
        "--workspace",
        workspace,
        "--notifier",
        notifier,
        "--top",
        str(top),
    ]
    if all_rows:
        command.append("--all")
    if dry_run:
        command.append("--dry-run")
    if json_output:
        command.append("--json")

    # Keep the null canary free of transport-only args so a default workflow log
    # cannot accidentally echo a secret-bearing endpoint.
    if notifier != "null":
        for flag, attr, env_name in (
            ("--channel", "channel", "DOS_NOTIFY_DECISIONS_CHANNEL"),
            ("--url", "url", "DOS_NOTIFY_DECISIONS_URL"),
            ("--token", "token", "DOS_NOTIFY_DECISIONS_TOKEN"),
        ):
            value = getattr(args, attr) or _env(env, env_name, "")
            if value:
                command.extend([flag, value])

    return command


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = parse_args(argv)
    try:
        command = build_command(args, env)
    except ValueError as exc:
        print(f"notify-decisions-cadence: {exc}", file=os.sys.stderr)
        return 2
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":  # pragma: no cover - exercised through main() tests
    raise SystemExit(main())
