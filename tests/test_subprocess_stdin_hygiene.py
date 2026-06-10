"""No shipped subprocess may inherit its caller's stdin — the docs/295 pin.

The defect class (docs/295, found 2026-06-10 while proving out the Trae MCP
binding, docs/294): a `subprocess.run(..., capture_output=True)` that does not
redirect stdin hands the child the PARENT's stdin. In a short-lived CLI that is
the operator's console — harmless. Inside a long-lived stdio server (`dos-mcp`)
it is the LIVE MCP TRANSPORT PIPE, and on Windows a git child holding that pipe
wedges without exiting; the 10s evidence-gather timeout then makes it worse
(CPython's `run()` kills the child and re-`communicate()`s with NO timeout to
drain the pipes — which blocks until the client hangs up). Net effect: every
git-backed MCP tool (`dos_verify`, `dos_commit_audit`, `dos_recall`, …) answered
STALLED on every spawned server, on every MCP host.

The rule this pins: **an evidence subprocess reads no stdin, so it must say
so** — `stdin=subprocess.DEVNULL` (or an explicit `input=`/`stdin=` when the
child genuinely consumes bytes, e.g. the LLM-judge prompt or the hook binary's
deliberate stdin relay). AST-walked over every shipped module so a new spawn
site cannot silently reintroduce the wedge.
"""
from __future__ import annotations

import ast
from pathlib import Path

import dos

_SRC_ROOTS = [
    Path(dos.__file__).parent,                      # src/dos (incl. drivers/)
    Path(dos.__file__).parents[1] / "dos_mcp",      # the MCP server package
]

#: subprocess callables that spawn a child whose std handles default to inherit.
_SPAWN_FUNCS = {"run", "Popen", "check_output", "check_call", "call"}


def _spawn_calls(tree: ast.AST):
    """Every `subprocess.<spawn>(...)` Call node in the module."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr in _SPAWN_FUNCS
                and isinstance(f.value, ast.Name) and f.value.id == "subprocess"):
            yield node


def _kwarg_names(call: ast.Call) -> set[str]:
    return {k.arg for k in call.keywords if k.arg is not None}


def test_every_shipped_subprocess_redirects_stdin():
    """Every shipped `subprocess.*` spawn passes `stdin=` or `input=`.

    `input=` implies a pipe Python itself writes and closes; an explicit
    `stdin=` is the author saying where the child's stdin comes from (DEVNULL
    for an evidence reader, `sys.stdin` for the hook binary's deliberate
    relay). What is FORBIDDEN is silence — the inherit-the-caller default that
    leaked the dos-mcp transport pipe into git (docs/295)."""
    offenders: list[str] = []
    for root in _SRC_ROOTS:
        if not root.is_dir():
            continue
        for py in sorted(root.rglob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            for call in _spawn_calls(tree):
                names = _kwarg_names(call)
                if "stdin" not in names and "input" not in names:
                    offenders.append(f"{py.relative_to(root.parent)}:{call.lineno}")
    assert offenders == [], (
        "shipped subprocess spawns inherit the caller's stdin (the docs/295 "
        "transport-pipe wedge): pass stdin=subprocess.DEVNULL (evidence "
        f"readers) or an explicit stdin=/input=: {offenders}"
    )
