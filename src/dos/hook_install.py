"""hook_install — wire the DOS hooks into a host runtime's OWN config file.

> **The verdict is the kernel; the envelope is a driver (docs/217); the WIRING is
> an installer (docs/221).** `hook_dialect.py` renders a DOS deny/observe verdict
> into the bytes a host honors. This module owns the *other* host fact: WHERE and
> in WHAT FORMAT that host registers a hook command — the config-file path
> (`.cursor/hooks.json` vs `.codex/config.toml` vs …), the file format (JSON vs
> TOML), and the host's event-name vocabulary. So `dos init --hooks <host>` can
> bind the DOS PEP into a team's existing runtime with no hand-authored config.

The kernel/driver split — the SAME line `hook_dialect` draws
============================================================

This module is the install-side sibling of `hook_dialect`, and it obeys the same
litmus (`tests/test_vendor_agnostic_kernel.py`): **no non-driver kernel module names
a vendor as a code identifier**, so no kernel *adjudication* can branch on which
vendor is acting. Therefore:

  * the KERNEL (this module) holds the pure, vendor-blind machinery — the
    `HostHookSpec` TYPE, the merge algorithms (`merge_json` / `merge_toml`),
    and the ONE unshadowable baseline `claude_code_spec()` (the `ClaudeCodeDialect`
    analogue: DOS's own sensors emit a Claude-Code-shaped command, so it is the
    default, not an adjudication branch);
  * every OTHER host's install-facts — the rows that must name `cursor`/`codex`/
    `gemini` as code — live in a DRIVER (`drivers/hook_dialects.py`, co-located with
    the dialect renderers they pair with), discovered by name through the
    `dos.hook_installs` entry-point group, exactly as `resolve_dialect` discovers the
    per-vendor renderers through `dos.hook_dialects`.

A host install-spec legitimately names its vendor — but it is OUTPUT wiring chosen
explicitly by the operator (`--hooks cursor`), strictly downstream of a decision the
kernel already made vendor-blind. That is precisely why it belongs on the driver
side of the line.

The pure core + the I/O boundary
================================

The `HostHookSpec` table and the in-memory merge functions (`merge_json` /
`merge_toml`) are PURE. The file READ/PARSE/WRITE lives at the CLI boundary
(`cli.py:cmd_init`) — the "I/O at the boundary, data to the pure core" rule the
kernel rests on (`git_delta`/`journal_delta` → `liveness.classify`).

A wrong host fails LOUD, not silent
===================================

`host_spec("typo")` RAISES (like `hook_dialect.resolve_dialect`). A host that asked
for `cursor` and silently got the Claude-Code file would wire a no-op deny against
Cursor — the exact failure docs/217/221 exist to prevent.

Facts as of 2026-06-07
======================

Event names and config shapes were web-grounded on each runtime's then-current hook
docs (docs/221 §1a). They churn every minor release; a host moving is a one-line
edit to its `HostHookSpec` row in the driver — never a change to this kernel module.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# The DOS verbs wired per lifecycle moment — the same three SHIPPED hooks the
# Claude-Code installer wires (cli.py:_DOS_HOOK_COMMANDS), named once here so every
# host's spec maps its events onto them. `pretool` DENIES a structurally-refused
# call before it runs; `posttool` re-surfaces a stalled stream (advisory); `stop`
# refuses a stop on an unverified claim.
# ---------------------------------------------------------------------------
PRE_VERB = "dos hook pretool"
POST_VERB = "dos hook posttool"
STOP_VERB = "dos hook stop"

#: The marker every DOS-wired command carries, so a re-run can find its own prior
#: entry and stay idempotent (the generalization of `_is_dos_hook_group`'s rule).
DOS_COMMAND_PREFIX = "dos hook "

#: The fence around the DOS block in a host's TOML config (Codex). A re-run is
#: idempotent on the OPENING marker; the block is appended verbatim, never
#: re-serialized (so the user's comments/keys survive — see merge_toml).
TOML_FENCE_OPEN = "# >>> dos hooks (managed by `dos init --hooks`) >>>"
TOML_FENCE_CLOSE = "# <<< dos hooks <<<"

#: The default host — the one DOS has spoken since the hooks shipped, and the only
#: spec that lives in the kernel (the `DEFAULT_DIALECT` analogue). Its `--dialect` is
#: implicit, so its wired command is byte-identical to today's `--with-hooks`.
DEFAULT_HOST = "claude-code"

#: The installer MODE that detects the host(s) instead of asking the operator to
#: name one (docs/303). NOT a host: `host_spec(AUTO_HOST)` raises like any other
#: non-host name. The CLI resolves it at the boundary — probe each known spec's
#: `detection_probe` dir + `env_markers`, then `choose_auto_hosts` — and wires
#: each detected host through the ordinary per-host path.
AUTO_HOST = "auto"


class ConfigFormat(enum.Enum):
    """How the host's hook-config file is encoded."""

    JSON = "json"  # Claude Code, Cursor, Gemini
    TOML = "toml"  # Codex


@dataclass(frozen=True)
class HostHookSpec:
    """Everything `dos init --hooks <host>` needs to wire one runtime. PURE data.

    `host`         — the `--hooks` value, AND the `--dialect` name the wired command
                     carries (so the verb emits the right envelope). For the default
                     host the dialect is implicit and is OMITTED, keeping the command
                     byte-identical to today's `--with-hooks`.
    `dialect_flag` — the exact `--dialect …` suffix appended to each wired command,
                     or "" for the default host. Carried as DATA so `command_for`
                     never compares against a vendor literal (the vendor-agnostic-
                     kernel litmus): the host's identity rides a data field, not a
                     branch.
    `config_path`  — path PARTS under the workspace root (joined at the boundary):
                     `(".cursor", "hooks.json")`.
    `fmt`          — JSON or TOML.
    `pre/post/stop_events` — the host's event name(s) for each DOS moment. A moment
                     may map to MORE THAN ONE host event (Cursor's PRE is
                     shell+MCP); each event gets the same DOS command.
    `json_entry_has_type` — JSON hosts only: does an entry carry a `"type":
                     "command"` key (CC, Gemini, Codex-shape) or is it a flat
                     `{"command": …}` (Cursor)?
    `json_group_wraps`    — JSON hosts only: is each event a list of GROUPS each
                     `{"hooks": [entry,…]}` (Claude Code) or a flat list of entries
                     (Cursor, Gemini)?
    `json_version`        — JSON hosts only: a top-level `{"version": N}` the file
                     requires (Cursor needs `1`); `None` for none.
    `note`         — a one-line operator hint printed after wiring (e.g. Cursor's
                     `failClosed` option, Codex's handler-coverage limit).
    `env_markers`  — environment-variable NAMES whose presence means the installer
                     is running *inside* this host right now (docs/303: the second
                     `--hooks auto` detection rung, for a fresh repo with no config
                     dir yet). Carried as DATA so the detection machinery never
                     compares against a vendor literal; `()` for a host with no
                     verified marker (the config-dir rung covers it).
    """

    host: str
    config_path: tuple[str, ...]
    fmt: ConfigFormat
    pre_events: tuple[str, ...]
    post_events: tuple[str, ...]
    stop_events: tuple[str, ...]
    dialect_flag: str = ""
    json_entry_has_type: bool = True
    json_group_wraps: bool = False
    json_version: Optional[int] = None
    note: str = ""
    env_markers: tuple[str, ...] = ()

    def command_for(self, verb: str) -> str:
        """The exact `dos hook …` command string this host wires for `verb`.

        Appends `--workspace .` (as the CC installer does) and this host's
        `dialect_flag` (empty for the default host → byte-identical to today's
        `--with-hooks`). Reads the flag as DATA — it never compares `self.host`
        against a vendor literal (the vendor-agnostic-kernel discipline). Every hook
        verb (`pretool`/`posttool`/`stop`) accepts `--dialect`, so the suffix is
        appended uniformly (docs/268: the `stop` verb gained `--dialect` so its
        refusal renders in the host's shape — Cursor's `{"permission":…}`, Gemini's
        `{"decision":…}` — not only CC's `{"decision":"block"}`).
        """
        cmd = f"{verb} --workspace ."
        if self.dialect_flag:
            cmd += f" {self.dialect_flag}"
        return cmd

    def events_and_commands(self) -> list[tuple[str, str]]:
        """Every (host_event, dos_command) pair this host wires, in a stable order."""
        pairs: list[tuple[str, str]] = []
        for ev in self.pre_events:
            pairs.append((ev, self.command_for(PRE_VERB)))
        for ev in self.post_events:
            pairs.append((ev, self.command_for(POST_VERB)))
        for ev in self.stop_events:
            pairs.append((ev, self.command_for(STOP_VERB)))
        return pairs


# ---------------------------------------------------------------------------
# The ONE built-in spec: the default host (the `ClaudeCodeDialect` analogue). It is
# the kernel's unshadowable baseline — DOS's own sensors emit its command shape — so
# it names the default as DATA in a frozen literal, never as a code identifier or a
# branch. Every OTHER host's spec lives in the driver (see module docstring).
# ---------------------------------------------------------------------------
def claude_code_spec() -> HostHookSpec:
    """The default host's install-facts: `.claude/settings.json`, group-wrapped,
    NO `--dialect` (the default) → byte-identical to today's `--with-hooks`."""
    return HostHookSpec(
        host=DEFAULT_HOST,
        config_path=(".claude", "settings.json"),
        fmt=ConfigFormat.JSON,
        pre_events=("PreToolUse",),
        post_events=("PostToolUse",),
        stop_events=("Stop",),
        dialect_flag="",          # the default — implicit, so the command matches today.
        json_entry_has_type=True,
        json_group_wraps=True,    # CC nests entries under {"hooks": [...]} matcher-groups.
        json_version=None,
        note="",
        # The variable this host exports into every shell it spawns — presence
        # means `dos init` is being run from inside it (the docs/303 env rung).
        env_markers=("CLAUDECODE",),
    )


def host_names() -> list[str]:
    """The names a host may pass to `--hooks` — the default + discovered driver specs."""
    names = {DEFAULT_HOST}
    try:
        names.update(_plugin_spec_names())
    except Exception:
        pass
    return sorted(names)


def host_spec(name: Optional[str]) -> HostHookSpec:
    """Resolve a host spec by name. RAISES on an unknown name (fail-LOUD).

    `None`/the default name → the built-in baseline. A name registered under the
    `dos.hook_installs` entry-point group → that driver's spec. An unknown name →
    `ValueError` (NEVER a silent default fallback: a wrong host would write the wrong
    file in the wrong format, a no-op deny against the real runtime — the
    `hook_dialect.resolve_dialect` discipline).
    """
    if name in (None, DEFAULT_HOST):
        return claude_code_spec()
    plugin = _load_plugin_spec(name)
    if plugin is not None:
        return plugin
    known = ", ".join(host_names())
    raise ValueError(
        f"unknown hook host {name!r} — known: {known}. Refusing to guess: a wrong "
        f"host would wire a no-op deny against your real runtime."
    )


# ---------------------------------------------------------------------------
# `--hooks auto` detection (docs/303). Both helpers are PURE — the existence
# checks and the environ read live at the CLI boundary (`cli.py:cmd_init`), the
# same split as the merges above. The detection signal is spec DATA
# (`config_path`, `env_markers`); no vendor name is ever compared against.
# ---------------------------------------------------------------------------
def detection_probe(spec: HostHookSpec) -> tuple[str, ...]:
    """The path parts whose presence under a workspace root signals this host is
    in use — the config file's parent directory (`.cursor`, `.claude`, …), or the
    file itself for a root-level config. PURE data; the caller does the I/O."""
    return spec.config_path[:-1] or spec.config_path


def choose_auto_hosts(
    detected: "list[tuple[str, tuple[str, ...]]]",
) -> "list[tuple[str, tuple[str, ...]]]":
    """Pick the hosts `--hooks auto` wires from the detected `(name, config_path)`
    pairs. PURE.

    Hosts that share one config file are wired ONCE (claude-code / claude-cowork
    both write `.claude/settings.json` — one set of hooks covers both, docs/298):
    the first detected name per config path becomes the OWNER, with `DEFAULT_HOST`
    always winning a file it shares (its wired command is the unshadowable
    baseline). Returns `(owner, also_covered_names)` pairs — owners in detection
    order (default first), the covered siblings carried so the operator message
    can say "also covers …" instead of staying silent.
    """
    pairs = list(detected)
    # Stable: the default host first, everything else keeps its given order.
    pairs.sort(key=lambda p: p[0] != DEFAULT_HOST)
    owner_by_path: dict[tuple[str, ...], str] = {}
    covers: dict[str, list[str]] = {}
    order: list[str] = []
    for name, path in pairs:
        owner = owner_by_path.get(path)
        if owner is None:
            owner_by_path[path] = name
            covers[name] = []
            order.append(name)
        elif name != owner:
            covers[owner].append(name)
    return [(name, tuple(covers[name])) for name in order]


# ---------------------------------------------------------------------------
# JSON merge (Claude Code, Cursor, Gemini). PURE: a parsed dict in, a parsed dict
# out, plus the (wired, already) event lists. The file read/write is the caller's.
# ---------------------------------------------------------------------------
def _json_entry(spec: HostHookSpec, command: str) -> dict:
    """One host hook entry for `command`, in this host's JSON shape."""
    entry: dict = {}
    if spec.json_entry_has_type:
        entry["type"] = "command"
    entry["command"] = command
    if spec.json_group_wraps:
        # Claude Code nests the entry under a matcher-group {"hooks": [entry]}.
        return {"hooks": [entry]}
    return entry


def _group_has_dos_command(group: object) -> bool:
    """True if a Claude-Code matcher-group already runs a `dos hook …` command."""
    if not isinstance(group, dict):
        return False
    for h in group.get("hooks", []):
        cmd = h.get("command", "") if isinstance(h, dict) else ""
        if isinstance(cmd, str) and cmd.strip().startswith(DOS_COMMAND_PREFIX):
            return True
    return False


def _entry_is_dos_command(entry: object) -> bool:
    """True if a flat entry (Cursor/Gemini) already runs a `dos hook …` command."""
    if not isinstance(entry, dict):
        return False
    cmd = entry.get("command", "")
    return isinstance(cmd, str) and cmd.strip().startswith(DOS_COMMAND_PREFIX)


def merge_json(
    existing: dict, spec: HostHookSpec, *, force: bool = False
) -> tuple[dict, list[str], list[str]]:
    """Add the DOS hooks to a parsed JSON hook-config. PURE.

    Returns `(merged, wired, already)` — the new config object, the events newly
    wired, and the events that already had a DOS entry (skipped — the idempotent
    path). Every non-DOS key/hook the user has is preserved. `force` drops an
    existing DOS entry and re-adds the canonical one (the repair path), mirroring
    `cli.py:_install_hooks`.
    """
    settings: dict = dict(existing) if isinstance(existing, dict) else {}

    if spec.json_version is not None and "version" not in settings:
        settings["version"] = spec.json_version

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    else:
        hooks = dict(hooks)
    settings["hooks"] = hooks

    wired: list[str] = []
    already: list[str] = []
    has_dos = _group_has_dos_command if spec.json_group_wraps else _entry_is_dos_command

    for event, command in spec.events_and_commands():
        groups = hooks.get(event)
        groups = list(groups) if isinstance(groups, list) else []
        present = any(has_dos(g) for g in groups)
        if present and not force:
            already.append(event)
            hooks[event] = groups
            continue
        if present and force:
            groups = [g for g in groups if not has_dos(g)]
        groups.append(_json_entry(spec, command))
        hooks[event] = groups
        wired.append(event)

    return settings, wired, already


# ---------------------------------------------------------------------------
# Codex TOML merge. Codex's config.toml is hand-edited and comment-rich, and the
# stdlib has no comment-preserving TOML writer (`tomllib` is read-only; `tomlkit`
# would break the PyYAML-only kernel dependency floor). So we APPEND a fenced block
# of `[[hooks.EVENT]]` tables rather than re-serialize the file — idempotent on the
# opening fence marker. PURE: text in, text out.
# ---------------------------------------------------------------------------
def _toml_block(spec: HostHookSpec) -> str:
    """The fenced `[[hooks.EVENT]]` block for a TOML host, as TOML text."""
    lines = [TOML_FENCE_OPEN]
    for event, command in spec.events_and_commands():
        # TOML basic strings need backslashes/quotes escaped; the DOS commands use
        # neither, but escape defensively so a future command stays valid TOML.
        esc = command.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f"[[hooks.{event}]]")
        lines.append("[[hooks.%s.hooks]]" % event)
        lines.append('type = "command"')
        lines.append(f'command = "{esc}"')
        lines.append("")  # blank line between tables for readability.
    lines.append(TOML_FENCE_CLOSE)
    return "\n".join(lines)


def merge_toml(
    existing_text: str, spec: HostHookSpec, *, force: bool = False
) -> tuple[str, list[str], list[str]]:
    """Append the DOS hooks to a TOML hook-config (Codex). PURE: text in, text out.

    Returns `(text, wired, already)`. Idempotent on the opening fence marker: if a
    DOS block is already present, the file is returned unchanged and every event is
    reported `already` (unless `force`, which strips the old fenced block and
    re-appends a fresh one — the repair path). The user's existing TOML (keys,
    comments, other `[[hooks.*]]` tables) is never re-serialized, only appended to.
    """
    text = existing_text if isinstance(existing_text, str) else ""
    events = [ev for ev, _ in spec.events_and_commands()]

    if TOML_FENCE_OPEN in text:
        if not force:
            return text, [], events
        # Repair: excise the old fenced block (open..close inclusive), then re-append.
        start = text.index(TOML_FENCE_OPEN)
        close_at = text.find(TOML_FENCE_CLOSE, start)
        if close_at != -1:
            end = close_at + len(TOML_FENCE_CLOSE)
            text = text[:start].rstrip() + text[end:]
        # (A truncated block with no close marker: leave it; the append below adds a
        # well-formed one and the operator can clean the stray opener.)

    block = _toml_block(spec)
    sep = "" if (text == "" or text.endswith("\n\n")) else ("\n" if text.endswith("\n") else "\n\n")
    new_text = (text + sep + block + "\n") if text else (block + "\n")
    return new_text, events, []


# ---------------------------------------------------------------------------
# Read-only DETECTION — the inverse of the merges, for `dos doctor`. Given a host's
# already-parsed config (a dict for JSON, the raw text for TOML), report which of
# this host's DOS events are wired. PURE: no I/O (the file read is the caller's), so
# doctor stays read-only. Used to answer "did my `dos init --hooks` take effect?".
# ---------------------------------------------------------------------------
def wired_events_json(existing: dict, spec: HostHookSpec) -> list[str]:
    """Which of `spec`'s events already run a `dos hook …` command in a JSON config."""
    if not isinstance(existing, dict):
        return []
    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        return []
    has_dos = _group_has_dos_command if spec.json_group_wraps else _entry_is_dos_command
    found: list[str] = []
    for event in [ev for ev, _ in spec.events_and_commands()]:
        groups = hooks.get(event)
        if isinstance(groups, list) and any(has_dos(g) for g in groups):
            found.append(event)
    return found


def wired_events_toml(existing_text: str, spec: HostHookSpec) -> list[str]:
    """Which of `spec`'s events are wired in a TOML config (the DOS fence is present)."""
    if not isinstance(existing_text, str) or TOML_FENCE_OPEN not in existing_text:
        return []
    # The DOS block wires ALL of the host's events at once (it is written as a unit),
    # so the presence of the fence means every event is wired.
    return [ev for ev, _ in spec.events_and_commands()]


# ---------------------------------------------------------------------------
# Driver-spec discovery (boundary I/O — at resolve time, never inside a merge). The
# `dos.hook_installs` entry-point group, the same mechanism `hook_dialect` uses for
# `dos.hook_dialects`. Kept defensive: a broken plugin never breaks the default.
# ---------------------------------------------------------------------------
def _iter_entry_points():
    try:
        from importlib.metadata import entry_points
    except Exception:  # pragma: no cover - very old Python
        return []
    try:
        eps = entry_points()
        if hasattr(eps, "select"):
            return list(eps.select(group="dos.hook_installs"))
        return list(eps.get("dos.hook_installs", []))  # type: ignore[attr-defined]
    except Exception:
        return []


def _plugin_spec_names() -> list[str]:
    return [ep.name for ep in _iter_entry_points()]


def _coerce_spec(obj: object) -> Optional[HostHookSpec]:
    """A registered target may be a HostHookSpec, or a zero-arg factory returning one."""
    if isinstance(obj, HostHookSpec):
        return obj
    if callable(obj):
        try:
            built = obj()
        except Exception:
            return None
        if isinstance(built, HostHookSpec):
            return built
    return None


def _load_plugin_spec(name: str) -> Optional[HostHookSpec]:
    for ep in _iter_entry_points():
        if ep.name != name:
            continue
        try:
            obj = ep.load()
        except Exception:
            return None
        return _coerce_spec(obj)
    return None
