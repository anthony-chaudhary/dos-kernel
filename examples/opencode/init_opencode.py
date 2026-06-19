#!/usr/bin/env python3
"""init_opencode.py — wire the DOS MCP server into an opencode config, idempotently.

opencode is an **MCP** host, not a hook-command host: it has no PreToolUse/Stop
stdout-deny surface for `dos init --hooks` to write a `dos hook …` command into.
The DOS surface that fits opencode is the **MCP tool surface** (`dos_mcp.server`),
which exposes `dos_verify` / `dos_arbitrate` / `dos_refuse_reasons` /
`dos_check_reason` / `dos_citation_resolve` as tools the model can call. This
script is the opencode analog of `dos init --hooks <host>`: it injects the
`mcp.dos` block into an opencode config (global or project) so a restart of
opencode picks the server up — no hand-edited JSON.

Honesty note (mirrors the kernel's own MCP-only-host framing): wiring DOS as an
MCP server is ADVISORY. The model can call the verdict tools; opencode does not
hard-DENY a tool call the way a hook-enabled host (Claude Code / Cursor / Codex)
does. Host-level enforcement in opencode would need a TS plugin
(`tool.execute.before`), which is out of scope here.

Idempotent: re-running is a no-op when the wired command already matches. It
never clobbers a malformed or hand-edited config (it refuses loudly instead), it
preserves every other key, and it verifies the launch by performing a live MCP
`initialize` handshake before reporting success.

Usage:
    python init_opencode.py                  # global config (~/.config/opencode/opencode.json)
    python init_opencode.py --scope project  # .opencode/opencode.json in the cwd
    python init_opencode.py --check          # read-only: is it wired + does it launch?
    python init_opencode.py --remove         # delete the mcp.dos block
    python init_opencode.py --python /opt/py  # force the interpreter used in the command
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SERVER_NAME = "dos"
SERVER_MODULE = "dos_mcp.server"
MCP_PROTOCOL_VERSION = "2024-11-05"
VERIFY_TIMEOUT = 12.0

GLOBAL_CONFIG = Path.home() / ".config" / "opencode" / "opencode.json"
PROJECT_CONFIG = Path(".opencode") / "opencode.json"


def _can_import_dos_mcp(python: str) -> bool:
    """True if `python` can import the MCP server module (i.e. dos-kernel[mcp] is installed)."""
    try:
        r = subprocess.run(
            [python, "-c", "import dos_mcp"],
            capture_output=True,
            timeout=25,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def discover_python() -> str | None:
    """The interpreter to put in the wired command.

    Prefers the interpreter this script is running under (you normally run init
    with the same python that has `dos-kernel[mcp]`), then falls back to whatever
    `python`/`python3` on PATH can import `dos_mcp`. Returns None if none can.
    """
    candidates: list[str] = []
    if sys.executable:
        candidates.append(sys.executable)
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)
    for py in candidates:
        if _can_import_dos_mcp(py):
            return py
    return None


def load_config(path: Path) -> tuple[dict, bool]:
    """Read an opencode config. Returns (data, existed). REFUSES malformed JSON."""
    if not path.exists():
        return {}, False
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"REFUSING to write: {path} is not valid JSON ({exc.msg} at "
            f"line {exc.lineno} col {exc.colno}). This installer never clobbers a "
            f"hand-edited config — fix or remove the file, then re-run."
        ) from exc
    if not isinstance(data, dict):
        raise SystemExit(
            f"REFUSING to write: {path} top-level JSON is not an object."
        )
    return data, True


def existing_command(data: dict, name: str) -> list[str] | None:
    mcp = data.get("mcp")
    if not isinstance(mcp, dict):
        return None
    entry = mcp.get(name)
    if not isinstance(entry, dict):
        return None
    cmd = entry.get("command")
    return cmd if isinstance(cmd, list) else None


def install_block(data: dict, name: str, command: list[str]) -> dict:
    mcp = data.get("mcp")
    if not isinstance(mcp, dict):
        mcp = {}
        data["mcp"] = mcp
    mcp[name] = {"type": "local", "command": command, "enabled": True}
    return data


def remove_block(data: dict, name: str) -> bool:
    mcp = data.get("mcp")
    if isinstance(mcp, dict) and name in mcp:
        del mcp[name]
        return True
    return False


def save_config(path: Path, data: dict) -> None:
    """Atomic write (temp file + os.replace) so a crash mid-write never half-truncates the config."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def verify_launch(command: list[str], timeout: float = VERIFY_TIMEOUT) -> tuple[bool, str]:
    """Spawn the server, send one MCP `initialize`, return (ok, detail).

    `subprocess.run(input=…)` writes the request then closes stdin; a well-behaved
    stdio server answers, then exits 0 on EOF — so the response is in `stdout`.
    """
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "dos-init-opencode", "version": "1.0"},
            },
        }
    )
    try:
        proc = subprocess.run(
            command,
            input=request + "\n",
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return False, f"could not launch {command[0]!r}: {exc}"
    except subprocess.TimeoutExpired:
        return False, "server did not answer `initialize` within the timeout (hung)"

    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{") or '"id"' not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("id") != 1:
            continue
        result = obj.get("result", {})
        info = result.get("serverInfo", {}) if isinstance(result, dict) else {}
        name = info.get("name")
        if name == "dos":
            return True, f"dos MCP server v{info.get('version', '?')} answered `initialize`"
        return False, f"server answered but serverInfo.name={name!r} (expected 'dos')"
    err = (proc.stderr or "").strip()
    return False, (
        f"no `initialize` response on stdout (exit {proc.returncode})"
        + (f"; stderr: {err[:300]}" if err else "")
    )


def resolve_config_path(scope: str, config: str | None) -> Path:
    if config:
        return Path(config).expanduser()
    return GLOBAL_CONFIG if scope == "global" else PROJECT_CONFIG.resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Wire the DOS MCP server into an opencode config (idempotent).",
    )
    parser.add_argument("--scope", choices=("global", "project"), default="global")
    parser.add_argument("--config", help="explicit opencode config path (overrides --scope)")
    parser.add_argument("--python", help="interpreter to use in the command (default: discovered)")
    parser.add_argument("--name", default=SERVER_NAME, help=f"MCP server name (default: {SERVER_NAME})")
    parser.add_argument("--module", default=SERVER_MODULE, help=f"server module (default: {SERVER_MODULE})")
    parser.add_argument("--check", action="store_true", help="read-only: report wiring + verify launch, then exit")
    parser.add_argument("--remove", action="store_true", help="remove the mcp.<name> block")
    parser.add_argument("--no-verify", action="store_true", help="skip the live initialize handshake")
    parser.add_argument("--force", action="store_true", help="overwrite an existing different command")
    args = parser.parse_args(argv)

    path = resolve_config_path(args.scope, args.config)

    # --- remove path ---------------------------------------------------------
    if args.remove:
        data, existed = load_config(path) if path.exists() else ({}, False)
        if not existed:
            print(f"[skip] {path} does not exist — nothing to remove.")
            return 0
        if remove_block(data, args.name):
            save_config(path, data)
            print(f"[removed] mcp.{args.name} from {path}")
            print("Restart opencode for the change to take effect.")
        else:
            print(f"[skip] mcp.{args.name} not present in {path}.")
        return 0

    # --- resolve the command (interpreter + -m dos_mcp.server) ---------------
    if args.python:
        python = args.python
        if not _can_import_dos_mcp(python):
            print(
                f"[error] {python} cannot import dos_mcp — install the server there first:\n"
                f"        {python} -m pip install 'dos-kernel[mcp]'",
                file=sys.stderr,
            )
            return 1
    else:
        python = discover_python()
        if python is None:
            print(
                "[error] no interpreter on this machine can import dos_mcp.\n"
                "        Install the server, then re-run:\n"
                "          pip install 'dos-kernel[mcp]'\n"
                "        or pass --python <path-to-the-interpreter-that-has-it>.",
                file=sys.stderr,
            )
            return 1
    command = [python, "-m", args.module]

    # --- read existing config ------------------------------------------------
    data, existed = load_config(path)

    if args.check:
        current = existing_command(data, args.name) if existed else None
        if current is None:
            print(f"[check] mcp.{args.name} is NOT wired in {path}.")
            return 1
        print(f"[check] mcp.{args.name} wired in {path}: {current}")
        if current != command:
            print(f"[warn] wired command differs from the discovered one ({command}).")
        if args.no_verify:
            return 0
        ok, detail = verify_launch(current)
        print(f"[verify] {'OK' if ok else 'FAIL'} — {detail}")
        return 0 if ok else 1

    # --- install path --------------------------------------------------------
    current = existing_command(data, args.name) if existed else None
    if current == command:
        print(f"[ok] mcp.{args.name} already wired in {path} — no change.")
    elif current is not None and not args.force:
        print(
            f"[error] mcp.{args.name} is already wired in {path} with a DIFFERENT command:\n"
            f"        wired:  {current}\n"
            f"        target: {command}\n"
            f"        Re-run with --force to overwrite.",
            file=sys.stderr,
        )
        return 1
    else:
        install_block(data, args.name, command)
        save_config(path, data)
        verb = "updated" if current is not None else "wired"
        print(f"[{verb}] mcp.{args.name} -> {path}")

    if not args.no_verify:
        ok, detail = verify_launch(command)
        print(f"[verify] {'OK' if ok else 'FAIL'} — {detail}")
        if not ok:
            print(
                "[warn] the server did not verify; the config was still written. See the "
                "playbook (examples/opencode/README.md) Troubleshooting section.",
                file=sys.stderr,
            )
            return 1

    print(
        "\nNext: QUIT AND RESTART opencode — it loads opencode.json once at startup, "
        "so the running session will not see the server until you restart."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
