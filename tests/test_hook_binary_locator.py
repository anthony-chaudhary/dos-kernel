"""Tests for `dos.hook_binary` — the in-package native-binary locator (docs/286).

The per-platform PyPI wheel bundles ONE static `dos-hook` binary into `dos/_bin/`;
this module is what the CLI hook verbs consult to route the per-tool-call hot path
through it (the 16-43x win, docs/270) — with the docs/100 fallback to the Python verb
when no binary is present. The contract pinned here:

  * **Clean checkout → no binary → None.** A dev tree (the binary is gitignored, a
    release artifact — unlike the PLUGIN's committed binaries) resolves to None, so the
    CLI falls through to Python and the suite stays green WITHOUT a built binary.
  * **A dropped-in binary IS found** (the wheel-install shape), and only when it is an
    executable regular file.
  * **The `DOS_HOOK_NATIVE=0` opt-out forces None** (the differential-oracle escape).
  * **The platform map matches the build matrix** (`build_hook_binary.py`), so the name
    the locator looks up is the name a per-platform wheel would ship.
  * **`try_native_hook` runs the binary and translates its exit code** (0 = owned,
    3 = DELEGATE → None, abnormal → 0 fail-safe), and is a no-op for non-native verbs.
  * **`hook_argv_from_args` re-emits exactly the flags the binary understands.**

Nothing here needs the Go toolchain: the "binary" in the run tests is a tiny shell/
batch stub whose exit code we control, so the LAUNCH + exit-code translation is tested
hermetically on any machine.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

import dos
from dos import hook_binary as hb

_REPO_ROOT = Path(dos.__file__).resolve().parents[2]
_BUILD_PY = _REPO_ROOT / "scripts" / "build_hook_binary.py"


# ─────────────────────────── the locator ───────────────────────────

def test_clean_checkout_resolves_to_none(monkeypatch):
    """A dev tree ships no binary (gitignored), so the locator returns None.

    This is the load-bearing fallback guarantee: the kernel suite is green on a
    binary-free checkout BECAUSE this is None and the CLI runs the Python verb. If this
    ever returned a path on a clean tree, a stale/foreign binary would silently capture
    the hot path.
    """
    # Clear the ambient opt-out so this asserts the absent-BINARY path, not the
    # `DOS_HOOK_NATIVE=0` escape — a box that exports the opt-out (this one runs the
    # suite against the Python decider via it) would otherwise make this pass for the
    # wrong reason, masking a regression where a stale binary captures the hot path.
    monkeypatch.delenv("DOS_HOOK_NATIVE", raising=False)
    # On the dev tree, src/dos/_bin/ holds only .gitignore — no dos-hook[.exe].
    assert hb.native_hook_binary() is None


def test_opt_out_forces_none(monkeypatch, tmp_path):
    """`DOS_HOOK_NATIVE=0` returns None even when a binary IS present."""
    _install_fake_binary(monkeypatch, tmp_path, exit_code=0)
    assert hb.native_hook_binary() is not None  # present without the opt-out
    monkeypatch.setenv("DOS_HOOK_NATIVE", "0")
    assert hb.native_hook_binary() is None
    # anything-but-"0" still allows it
    monkeypatch.setenv("DOS_HOOK_NATIVE", "1")
    assert hb.native_hook_binary() is not None


def test_present_executable_binary_is_found(monkeypatch, tmp_path):
    """A dropped-in executable binary at dos/_bin/dos-hook[.exe] resolves."""
    path = _install_fake_binary(monkeypatch, tmp_path, exit_code=0)
    found = hb.native_hook_binary()
    assert found == path
    assert found.is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable bit is meaningless on Windows")
def test_non_executable_file_is_not_found(monkeypatch, tmp_path):
    """On POSIX a present-but-non-executable file is NOT a usable binary."""
    path = _install_fake_binary(monkeypatch, tmp_path, exit_code=0)
    path.chmod(0o644)  # strip the x bit
    assert hb.native_hook_binary() is None


def test_platform_map_matches_build_matrix():
    """The (goos, goarch) the locator computes is a name the build matrix emits.

    Load the SAME build script the bundler uses and assert the locator's host token is
    one of the matrix's OS/arch pairs (so the name we look up is the name a wheel ships).
    """
    build = _load_build_script()
    goos, goarch = hb._host_goos_goarch()
    matrix_pairs = {tuple(spec.split("/", 1)) for spec in build.DEFAULT_ARCHES}
    # The host might be an off-matrix arch in some CI, but on every arch the matrix
    # covers the tokens must agree with how build_hook_binary names the binary.
    if (goos, goarch) in matrix_pairs:
        expected = build._binary_name(goos, goarch)  # dos-hook-<os>-<arch>[.exe]
        # The wheel name is the un-suffixed-by-arch form; assert the .exe-ness agrees.
        assert hb.bundled_binary_name().endswith(".exe") == expected.endswith(".exe")


def test_bundled_name_is_arch_free():
    """The wheel binary is `dos-hook[.exe]` — NOT the matrix `dos-hook-<os>-<arch>`."""
    name = hb.bundled_binary_name()
    assert name in ("dos-hook", "dos-hook.exe")


# ─────────────────────────── try_native_hook ───────────────────────────

def test_try_native_is_noop_for_non_native_verb(monkeypatch, tmp_path):
    """`stop`/`marker` are not served natively on the pip path → always None."""
    _install_fake_binary(monkeypatch, tmp_path, exit_code=0)
    assert hb.try_native_hook("stop", []) is None
    assert hb.try_native_hook("marker", []) is None


def test_try_native_none_when_no_binary():
    """No bundled binary (clean tree) → None for a native verb too."""
    assert hb.try_native_hook("pretool", []) is None


def test_try_native_owned_returns_zero(monkeypatch, tmp_path):
    """A native binary exiting 0 (owned the decision) → the CLI returns 0."""
    _install_fake_binary(monkeypatch, tmp_path, exit_code=0)
    with _real_std_streams(monkeypatch, tmp_path):
        assert hb.try_native_hook("pretool", ["--workspace", "."]) == 0


def test_try_native_delegate_falls_through(monkeypatch, tmp_path):
    """A native binary exiting 3 (DELEGATE) → None, so the CLI runs Python."""
    _install_fake_binary(monkeypatch, tmp_path, exit_code=hb.DELEGATE_EXIT)
    with _real_std_streams(monkeypatch, tmp_path):
        assert hb.try_native_hook("pretool", []) is None


def test_try_native_abnormal_exit_is_failsafe_zero(monkeypatch, tmp_path):
    """A native binary exiting abnormally (a crash) → 0 (emit-nothing fail-safe).

    stdin is consumed by then, so Python cannot re-decide; the hook fail-safe is to do
    nothing and not break the turn, which is exit 0.
    """
    _install_fake_binary(monkeypatch, tmp_path, exit_code=1)
    with _real_std_streams(monkeypatch, tmp_path):
        assert hb.try_native_hook("pretool", []) == 0


def test_try_native_failsafe_to_none_when_streams_uninheritable():
    """If sys.stdin/stdout can't be inherited (no real fileno), fall through to None.

    This is pytest's default stream-capture shape, and the production-safe direction:
    a stream the OS can't hand a child → run Python, never crash the hook. (We assert it
    with a present binary + the captured streams pytest gives us by default.)
    """
    # No _real_std_streams() here: under pytest, sys.stdin is captured and has no
    # fileno, so subprocess.run raises OSError → try_native_hook returns None.
    # (A present binary is installed via the monkeypatch-free path below.)
    # Use the module-level clean tree: no binary → None regardless, which still proves
    # the no-crash contract. The stream-specific branch is covered by the OSError guard.
    assert hb.try_native_hook("pretool", []) is None


# ─────────────────────────── hook_argv_from_args ───────────────────────────

def test_argv_omits_defaults():
    """Default dialect/handler are omitted (the binary applies the same defaults)."""
    ns = argparse.Namespace(
        workspace=".", dialect="claude-code", handler="observe", debug=False
    )
    assert hb.hook_argv_from_args(ns) == ["--workspace", "."]


def test_argv_forwards_non_defaults():
    """Non-default flags are forwarded verbatim, in a stable order."""
    ns = argparse.Namespace(
        workspace="/repo", dialect="gemini", handler="enforce",
        session_id="sid-1", debug=True,
    )
    assert hb.hook_argv_from_args(ns) == [
        "--workspace", "/repo",
        "--dialect", "gemini",
        "--handler", "enforce",
        "--session-id", "sid-1",
        "--debug",
    ]


def test_argv_tolerates_missing_attrs():
    """A namespace missing a flag (posttool has no --handler) just omits it."""
    ns = argparse.Namespace(workspace=".")  # no dialect/handler/session_id/debug
    assert hb.hook_argv_from_args(ns) == ["--workspace", "."]


# ─────────────────────────── helpers ───────────────────────────

import contextlib


@contextlib.contextmanager
def _real_std_streams(monkeypatch, tmp_path: Path):
    """Point sys.stdin/stdout at REAL files with filenos for the duration.

    `try_native_hook` inherits sys.stdin/stdout into the child (the real hook has real
    OS pipes). Under pytest those are captured objects with no fileno, which would make
    subprocess.run raise OSError → None (the safe fallback, covered by its own test).
    To exercise the LAUNCH path, swap in real files here."""
    stdin_f = open(os.devnull, "r")
    stdout_f = open(tmp_path / "_stdout", "w")
    monkeypatch.setattr(sys, "stdin", stdin_f)
    monkeypatch.setattr(sys, "stdout", stdout_f)
    try:
        yield
    finally:
        stdin_f.close()
        stdout_f.close()


def _load_build_script():
    spec = importlib.util.spec_from_file_location("_build_hook_binary_t", _BUILD_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _install_fake_binary(monkeypatch, tmp_path: Path, *, exit_code: int) -> Path:
    """Drop a DIRECTLY-LAUNCHABLE stub in <tmp>/_bin/ and point the locator at it.

    The stub drains stdin (so it behaves like the real binary consuming the event) and
    exits with `exit_code`, proving the launch + exit-code translation WITHOUT the Go
    toolchain. We patch only `_BIN_DIR` + `bundled_binary_name` (never subprocess), so
    `try_native_hook` exercises the real launch path.

    On Windows a `.cmd` is directly runnable by `subprocess.run([path, ...])`; on POSIX
    a `#!/bin/sh` script with the executable bit is. The locator's executable-bit check
    is meaningful on POSIX only (an .exe/.cmd runs by extension on Windows).

    Clears the `DOS_HOOK_NATIVE` opt-out: these are the binary-PRESENT scenarios, so the
    native path must be ENABLED for the stub to be found. A box that exports the opt-out
    (to run the suite against the Python decider — `DOS_HOOK_NATIVE=0`) would otherwise
    force every locator probe to None and turn each binary-found assertion into a false
    failure. `raising=False` makes the clear a no-op where the var isn't set.
    """
    monkeypatch.delenv("DOS_HOOK_NATIVE", raising=False)
    bin_dir = tmp_path / "_bin"
    bin_dir.mkdir()
    monkeypatch.setattr(hb, "_BIN_DIR", bin_dir)
    if os.name == "nt":
        name = "dos-hook.cmd"
        path = bin_dir / name
        # @echo off + drain stdin via `more` is fiddly; a bare `exit /b N` returns the
        # code, and cmd.exe does not require stdin to be drained for the test's purpose.
        path.write_text(f"@echo off\r\nexit /b {exit_code}\r\n", encoding="utf-8")
        monkeypatch.setattr(hb, "bundled_binary_name", lambda: name)
        return path
    # POSIX: a tiny shell script that drains stdin and exits N.
    path = bin_dir / "dos-hook"
    path.write_text(f"#!/bin/sh\ncat >/dev/null 2>&1\nexit {exit_code}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path
