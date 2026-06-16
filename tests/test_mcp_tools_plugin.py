"""The `dos.mcp_tools` entry-point seam — third-party MCP tools on the DOS server.

The DOS MCP server registers a curated set of built-in syscall tools. This seam lets a
third party ADD a tool/verb from their OWN pip package with no fork (the MCP-surface
analogue of dos.judges / dos.drivers). A plugin registers `name = "pkg:register"` under
[project.entry-points."dos.mcp_tools"]; `register(mcp)` is handed the server and wires its
own tools (getting the same deadline/learn-more wrapping the built-ins do). A bare tool
callable is also accepted. A broken plugin is skipped, never crashing the server.

These tests pin: a register(mcp) plugin's tools land on the server; a bare tool callable
lands; a broken plugin is skipped (server still builds); the registrar-vs-bare-tool
discriminator; and that with NO plugin installed the built-in surface is unchanged.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mcp")  # the server needs the optional [mcp] extra

from dos_mcp import server as _server
from dos_mcp.server import build_server


def _tool_names(mcp) -> set:
    return {t.name for t in mcp._tool_manager.list_tools()}


class _FakeEP:
    def __init__(self, name, obj):
        self.name = name
        self._obj = obj

    def load(self):
        return self._obj


def _patch_eps(monkeypatch, eps):
    """Make importlib.metadata.entry_points(group="dos.mcp_tools") return `eps`."""
    import importlib.metadata as md

    def fake_entry_points(*, group=None):
        return eps if group == "dos.mcp_tools" else []

    monkeypatch.setattr(md, "entry_points", fake_entry_points)


# --------------------------------------------------------------------------- #
# a register(mcp) plugin wires its tools onto the server
# --------------------------------------------------------------------------- #

def test_register_style_plugin_adds_a_tool(monkeypatch):
    def register(mcp):
        @mcp.tool()
        def my_third_party_tool(text: str = "") -> dict:
            """A tool a third party added."""
            return {"ok": True, "echo": text}

    _patch_eps(monkeypatch, [_FakeEP("acme", register)])
    mcp = build_server()
    assert "my_third_party_tool" in _tool_names(mcp)
    # the built-ins are still all present (additive, never replaces)
    assert "dos_verify" in _tool_names(mcp)


def test_bare_tool_callable_is_registered(monkeypatch):
    def acme_echo(text: str = "") -> dict:
        """A bare tool function (not a registrar)."""
        return {"echo": text}

    _patch_eps(monkeypatch, [_FakeEP("acme_echo", acme_echo)])
    mcp = build_server()
    assert "acme_echo" in _tool_names(mcp)


def test_broken_plugin_is_skipped(monkeypatch, capsys):
    def boom(mcp):
        raise RuntimeError("plugin exploded on register")

    _patch_eps(monkeypatch, [_FakeEP("broken", boom)])
    # the server still builds; the built-in surface is intact
    mcp = build_server()
    assert "dos_verify" in _tool_names(mcp)
    err = capsys.readouterr().err
    assert "broken" in err and "skipping" in err


def test_no_plugin_leaves_builtin_surface_unchanged(monkeypatch):
    _patch_eps(monkeypatch, [])
    mcp = build_server()
    # exactly the curated built-in ABI, nothing more
    assert "dos_verify" in _tool_names(mcp)
    assert "my_third_party_tool" not in _tool_names(mcp)


# --------------------------------------------------------------------------- #
# the registrar-vs-bare-tool discriminator
# --------------------------------------------------------------------------- #

def test_looks_like_registrar_by_name():
    def register(mcp):
        pass

    assert _server._looks_like_registrar(register)


def test_looks_like_registrar_by_single_mcp_param():
    def wire(mcp):
        pass

    assert _server._looks_like_registrar(wire)

    def wire_server(server):
        pass

    assert _server._looks_like_registrar(wire_server)


def test_bare_tool_is_not_a_registrar():
    def a_tool(text: str = "", plan: str = "") -> dict:
        return {}

    assert not _server._looks_like_registrar(a_tool)


def test_non_callable_is_not_a_registrar():
    assert not _server._looks_like_registrar("not callable")


# --------------------------------------------------------------------------- #
# the discovery helper returns the registered names
# --------------------------------------------------------------------------- #

def test_register_entry_point_tools_returns_names(monkeypatch):
    def register(mcp):
        @mcp.tool()
        def t1(x: str = "") -> dict:
            return {}

    _patch_eps(monkeypatch, [_FakeEP("acme", register)])
    mcp = build_server()  # build_server calls _register_entry_point_tools internally
    # and a direct call is idempotent-ish in name reporting
    names = _server._register_entry_point_tools(mcp)
    assert "acme" in names
