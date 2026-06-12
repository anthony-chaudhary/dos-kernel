"""`dos hosts` — the self-describing host-support matrix (docs #93).

The verb's whole point is that it is DERIVED, not hand-kept: every row comes out
of the `dos.hook_installs` registry (`hook_install.host_matrix()` walks
`host_names()` and reads each `host_spec()`), so the matrix can never drift from
what `dos init --hooks` actually wires. These tests pin exactly that contract:

  * the SET of listed hosts equals the registry's contents (the anti-rot pin);
  * the projection reads its facts from the spec, not from a literal table;
  * `--json` carries every fact the text view shows, in a tooling shape;
  * the implementation names no host as a code literal (the `man wedge`/`man lane`
    discipline — the roster is the registry, never a hand-kept list in the verb).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dos import cli, hook_install, hook_dialect


# ---------------------------------------------------------------------------
# The pure projection — listed hosts == registry contents.
# ---------------------------------------------------------------------------
def test_matrix_roster_equals_registry():
    """The set of hosts `host_matrix()` lists is exactly `host_names()` — no host
    invented, none dropped. THE anti-rot pin: a host added to (or removed from) the
    `dos.hook_installs` registry changes the matrix with no edit to the verb."""
    rows = hook_install.host_matrix()
    assert [r.host for r in rows] != []
    assert {r.host for r in rows} == set(hook_install.host_names())


def test_matrix_default_host_sorts_first():
    """The unshadowable baseline host (claude-code) is row 0 and is flagged default;
    every other host follows alphabetically and is not."""
    rows = hook_install.host_matrix()
    assert rows[0].host == hook_install.DEFAULT_HOST
    assert rows[0].is_default is True
    assert all(not r.is_default for r in rows[1:])
    # The tail is alphabetical (host_names() returns sorted, default lifted out).
    tail = [r.host for r in rows[1:]]
    assert tail == sorted(tail)


def test_each_row_is_projected_from_its_spec():
    """Every row's facts equal what the host's OWN `HostHookSpec` carries — the
    projection reads the spec, it does not restate the facts."""
    for row in hook_install.host_matrix():
        spec = hook_install.host_spec(row.host)
        assert row.config_path == "/".join(spec.config_path)
        assert row.note == spec.note
        assert row.wiring == f"dos init --hooks {row.host} ."
        # Dialect: the explicit flag's host, or the default for a baseline-rider.
        expected_dialect = spec.host if spec.dialect_flag else hook_dialect.DEFAULT_DIALECT
        assert row.dialect == expected_dialect
        # Events: exactly the spec's wired event names (de-duplicated, in order).
        spec_events = []
        for ev, _cmd in spec.events_and_commands():
            if ev not in spec_events:
                spec_events.append(ev)
        assert list(row.events) == spec_events
        # Every install-registry host is a hooks-tier (enforcing) host.
        assert row.tier == "hooks"


def test_cursor_has_two_pre_events():
    """A moment that maps to several host events surfaces them all — Cursor's PRE is
    shell + MCP, so its row lists both, not a collapsed single."""
    rows = {r.host: r for r in hook_install.host_matrix()}
    assert "cursor" in rows, "cursor is a registered install host"
    assert "beforeShellExecution" in rows["cursor"].events
    assert "beforeMCPExecution" in rows["cursor"].events


# ---------------------------------------------------------------------------
# The CLI verb — text + JSON, exit code, and the registry-sourced contract end
# to end.
# ---------------------------------------------------------------------------
def test_cmd_hosts_text_lists_every_registered_host(capsys):
    """`dos hosts` (text) names every registry host, exits 0, and points absent
    hosts at the advisory surface (the honest-absence footer)."""
    rc = cli.main(["hosts"])
    out = capsys.readouterr().out
    assert rc == 0
    for name in hook_install.host_names():
        assert name in out, f"{name} missing from `dos hosts` output"
    # The honest-absence footer: a host with no row is advisory-only.
    assert "advisory" in out.lower()
    assert "Trae" in out  # the named advisory-only example (docs/294).


def test_cmd_hosts_json_carries_every_host_and_fact(capsys):
    """`dos hosts --json` emits a parseable record whose host set equals the registry
    and whose every row carries the documented fields."""
    rc = cli.main(["hosts", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert {h["host"] for h in payload["hosts"]} == set(hook_install.host_names())
    assert set(payload["dialects"]) == set(hook_dialect.available_dialects())
    required = {"host", "tier", "default", "config_path", "events", "dialect",
                "wiring", "note"}
    for h in payload["hosts"]:
        assert required <= set(h), f"row missing fields: {required - set(h)}"
        assert isinstance(h["events"], list) and h["events"]


def test_json_renderer_only_dialects_excludes_wired_hosts(capsys):
    """A name that has an install row is NEVER reported renderer-only — even when it
    renders through the default envelope (claude-cowork rides `claude-code` yet is
    fully wired). Only a dialect with a renderer AND no install path is listed
    (hermes today)."""
    cli.main(["hosts", "--json"])
    payload = json.loads(capsys.readouterr().out)
    host_names = set(hook_install.host_names())
    for name in payload["renderer_only_dialects"]:
        assert name not in host_names, (
            f"{name} has an install row — it must not be reported renderer-only"
        )
    # Every renderer-only name IS a real dialect with no install host.
    for name in payload["renderer_only_dialects"]:
        assert name in hook_dialect.available_dialects()


def test_hosts_is_a_registered_verb():
    """`dos hosts` parses to `cmd_hosts` — a real subparser, not a typo'd alias."""
    ns = cli.build_parser().parse_args(["hosts"])
    assert ns.func is cli.cmd_hosts


# ---------------------------------------------------------------------------
# The anti-rot discipline: the verb's implementation names no host as code.
# ---------------------------------------------------------------------------
def test_cmd_hosts_names_no_host_as_a_code_literal():
    """The `cmd_hosts` body and the `host_matrix()`/`_row_for()` projection name NO
    vendor as a string literal — the roster is the registry, never a hand-kept list
    in the verb (the `man wedge` / `man lane` self-describing discipline). The sole
    allowance is the default-host reference in `hook_install`, which lives behind the
    `DEFAULT_HOST` constant, not a bare `"claude-code"` literal in the verb.

    A drift here (someone hard-codes `"cursor"` into the printer) is the exact rot
    the verb exists to kill, so it is a test failure, not a style note. Comments and
    docstrings MAY name a vendor as an illustrative example (that does not rot — it
    explains); only the EXECUTABLE code is held to the no-literal rule, so the check
    strips comments and string-literal lines before scanning.
    """
    import ast
    import inspect

    def _code_identifiers(fn) -> str:
        """The vendor-relevant CODE of a function: its AST Name/Attribute identifiers,
        with comments, docstrings, and string literals excluded. A hand-kept table
        would name a vendor as a Name/Constant in control flow — this catches that
        while ignoring an example in prose."""
        tree = ast.parse(inspect.getsource(fn).lstrip())
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
        return " ".join(names).lower()

    code = " ".join(_code_identifiers(fn) for fn in
                    (cli.cmd_hosts, hook_install.host_matrix, hook_install._row_for))
    # The vendor names a hand-kept table would inevitably contain — checked against
    # identifiers only (string literals are how a table WOULD name them, and there
    # are none here; identifiers would be the other smell).
    forbidden = ["cursor", "codex", "gemini", "antigravity", "cowork", "hermes"]
    for vendor in forbidden:
        assert vendor not in code, (
            f"the hosts verb names {vendor!r} as a code identifier — the matrix must "
            f"be sourced from the registry (host_names/host_spec), not a hand-kept table"
        )

    # And no vendor STRING LITERAL in the executable lines either (a table is the
    # `if host == "cursor"` smell). Strip comment lines, then scan the rest.
    raw = "\n".join(
        inspect.getsource(fn) for fn in
        (cli.cmd_hosts, hook_install.host_matrix, hook_install._row_for)
    )
    code_lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Drop the inline comment tail (no vendor literal hides behind a `#`).
        code_lines.append(line.split("#", 1)[0])
    code_text = "\n".join(code_lines).lower()
    for vendor in forbidden:
        assert f'"{vendor}"' not in code_text and f"'{vendor}'" not in code_text, (
            f"the hosts verb has a {vendor!r} string literal in executable code — "
            f"that is the hand-kept-table smell the verb exists to kill"
        )
