#!/usr/bin/env python
"""dev.py — the inner-loop runner for contributors (test / fast / lint / verify-self).

> **Tooling that operates ON the package, never inside it** (CLAUDE.md "Four things
> live OUTSIDE the four layers"). Like `build_readme.py`, this consumes the repo and
> the kernel is unaware it exists — nothing under `src/dos/` imports it.

Why this exists
===============

The full suite is ~4,900 tests / ~4-5 min — the right gate before a commit, the
wrong feedback loop while editing one module. There was no documented fast path
and no single place that remembers the four commands CI actually runs, so a
contributor either ran the whole suite for every one-line change or hand-rolled a
`pytest -k` they had to keep in their head.

`dev.py` is that place. Each subcommand MIRRORS a CI step (see
`.github/workflows/ci.yml`) so green-local implies green-CI:

  python scripts/dev.py fast          # pytest -m "not slow"  — the inner loop
  python scripts/dev.py test [-k X]   # pytest -q             — the full gate (+ optional filter)
  python scripts/dev.py lint          # ruff check src/dos src/dos_mcp
  python scripts/dev.py verify-self   # dos doctor --check + a real verify round-trip
  python scripts/dev.py all           # lint + test + verify-self (the pre-push gate)

Stdlib only, cross-platform (Windows-developed, Linux-CI): every command is a
`subprocess` over the current interpreter. `fast` depends on the `slow` pytest
marker registered in `pyproject.toml` — mark the heavy modules `@pytest.mark.slow`
and the inner loop skips them.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], *, cwd: Path | None = None) -> int:
    """Echo + run a command, returning its exit code (never raising)."""
    print(f"$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=str(cwd or _ROOT)).returncode


def cmd_test(args: argparse.Namespace) -> int:
    cmd = [sys.executable, "-m", "pytest", "-q"]
    if args.k:
        cmd += ["-k", args.k]
    cmd += args.pytest_args
    return _run(cmd)


def cmd_fast(args: argparse.Namespace) -> int:
    """The inner loop: skip @pytest.mark.slow modules."""
    cmd = [sys.executable, "-m", "pytest", "-q", "-m", "not slow"]
    if args.k:
        cmd += ["-k", args.k]
    cmd += args.pytest_args
    return _run(cmd)


def cmd_lint(args: argparse.Namespace) -> int:
    return _run(["ruff", "check", "src/dos", "src/dos_mcp"])


def cmd_verify_self(args: argparse.Namespace) -> int:
    """Mirror the CI CLI smoke: doctor --check, then a real SHIPPED/NOT_SHIPPED
    round-trip in a throwaway git repo (the verdict-IS-the-exit-code contract)."""
    rc = _run([sys.executable, "-m", "dos.cli", "doctor", "--check", "--workspace", "."])
    if rc != 0:
        print("dev.py: `dos doctor --check` found a config issue.", file=sys.stderr)
        return rc
    with tempfile.TemporaryDirectory(prefix="dos-dev-verify-") as tmp:
        repo = Path(tmp)

        def git(*a: str) -> int:
            return subprocess.run(["git", "-C", str(repo), *a],
                                  capture_output=True, text=True).returncode

        def dos(*a: str) -> int:
            return subprocess.run([sys.executable, "-m", "dos.cli", *a],
                                  cwd=str(repo)).returncode

        if git("init", "-q") != 0:
            print("dev.py: git not available — skipping the verify round-trip.",
                  file=sys.stderr)
            return 0
        git("config", "user.email", "dev@example.com")
        git("config", "user.name", "dev")
        git("config", "commit.gpgsign", "false")
        dos("init", ".")
        (repo / "login.py").write_text("def login(): pass\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "AUTH1: ship the login endpoint")
        shipped = dos("verify", "--workspace", ".", "AUTH", "AUTH1")
        not_shipped = dos("verify", "--workspace", ".", "AUTH", "AUTH2")
        if shipped != 0:
            print(f"dev.py: expected SHIPPED (0) for AUTH1, got {shipped}", file=sys.stderr)
            return 1
        if not_shipped == 0:
            print("dev.py: expected NOT_SHIPPED (non-zero) for AUTH2, got 0", file=sys.stderr)
            return 1
        print("verify-self passed: SHIPPED=0, NOT_SHIPPED!=0, doctor clean.")
        return 0


def cmd_all(args: argparse.Namespace) -> int:
    """The pre-push gate: lint, full suite, verify-self — stop at the first failure."""
    for step in (cmd_lint, cmd_test, cmd_verify_self):
        rc = step(args)
        if rc != 0:
            return rc
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dev.py",
        description="The contributor inner-loop runner — mirrors the CI steps.")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")

    pt = sub.add_parser("test", help="the full kernel suite (pytest -q)")
    pt.add_argument("-k", default=None, help="pass a pytest -k filter expression")
    pt.add_argument("pytest_args", nargs="*", help="extra args passed to pytest")
    pt.set_defaults(func=cmd_test)

    pf = sub.add_parser("fast", help="the inner loop: skip @pytest.mark.slow")
    pf.add_argument("-k", default=None, help="pass a pytest -k filter expression")
    pf.add_argument("pytest_args", nargs="*", help="extra args passed to pytest")
    pf.set_defaults(func=cmd_fast)

    pl = sub.add_parser("lint", help="ruff check src/dos src/dos_mcp")
    pl.set_defaults(func=cmd_lint)

    pv = sub.add_parser("verify-self", help="doctor --check + a real verify round-trip")
    pv.set_defaults(func=cmd_verify_self)

    pa = sub.add_parser("all", help="lint + test + verify-self (the pre-push gate)")
    pa.add_argument("-k", default=None, help=argparse.SUPPRESS)
    pa.add_argument("pytest_args", nargs="*", help=argparse.SUPPRESS)
    pa.set_defaults(func=cmd_all)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
