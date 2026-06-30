"""The native dos-hook fast-path binaries are BUNDLED in git — the direct-install pin.

A Claude Code plugin is distributed as its git tree: `/plugin marketplace add` clones
this repo, and `marketplace.json` points `source: ./claude-plugin`. So a binary that
is gitignored is a binary the user never receives — the plugin would silently fall
back to Python on every tool call, defeating the docs/125 native fast-path (the 16–43×
win measured in docs/270). For the install to be DIRECT, the per-arch binaries the
launcher dispatches to must be PRESENT on disk AND TRACKED in git.

This file pins exactly that, the binary analogue of `test_plugin_manifest.py`'s
"skills are in sync with source" — it shares ONE definition of the build matrix with
`scripts/build_hook_binary.py` (loaded by path, no second copy of the list), so the
test and the build agree by construction:

  * **Every default-matrix arch is present** under `claude-plugin/bin/` with the name
    the launcher computes (`dos-hook-<os>-<arch>[.exe]`).
  * **Every one is tracked in git** (`git ls-files`), not merely sitting in a dirty
    working tree — the thing a fresh clone actually ships.
  * **The launchers themselves are tracked** (the POSIX `dos-hook` + `dos-hook.ps1`).
  * **`bin/.gitignore` ignores none of the binaries** — the regression guard for the
    old `dos-hook-*` / `*.exe` ignore lines that caused the gap.
  * **The amd/intel (amd64) + arm64 grid covers linux/macOS/windows** — the
    cross-platform compatibility the bundle promises.

It does NOT rebuild anything (that needs the Go toolchain, which CI's test legs may
lack); it asserts the committed state. The build is exercised separately by running
`scripts/build_hook_binary.py`. Like the plugin, this lives OUTSIDE the kernel — it
checks a distribution surface, nothing under `src/dos/` imports the binaries.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import dos
import pytest

_REPO_ROOT = Path(dos.__file__).resolve().parents[2]
_BUILD_PY = _REPO_ROOT / "scripts" / "build_hook_binary.py"
_BIN_DIR = _REPO_ROOT / "claude-plugin" / "bin"

# Load the build script by path (scripts/ is not an importable package) so the matrix
# + the per-arch naming come from the SAME source the build uses — no drift between
# "what we build" and "what we assert is committed".
_spec = importlib.util.spec_from_file_location("_build_hook_binary", _BUILD_PY)
assert _spec and _spec.loader, f"cannot load {_BUILD_PY}"
build_hook_binary = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_hook_binary)


def _tracked_files(rel_dir: str) -> set[str]:
    """The git-tracked paths under rel_dir, as posix strings relative to the repo root."""
    out = subprocess.run(
        ["git", "ls-files", rel_dir],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, check=True,
    )
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def _tracked_modes(rel_dir: str) -> dict[str, str]:
    """Git index modes for tracked paths under rel_dir."""
    out = subprocess.run(
        ["git", "ls-files", "--stage", rel_dir],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, check=True,
    )
    modes: dict[str, str] = {}
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        mode, _, _, path = line.split(maxsplit=3)
        modes[path] = mode
    return modes


def _expected_binary_names() -> list[str]:
    return [
        build_hook_binary._binary_name(*spec.split("/", 1))
        for spec in build_hook_binary.DEFAULT_ARCHES
    ]


def test_every_matrix_binary_is_present_on_disk():
    """Each default-matrix arch has its launcher-dispatched binary on disk."""
    missing = [n for n in _expected_binary_names() if not (_BIN_DIR / n).is_file()]
    assert not missing, (
        "native hook binaries missing from claude-plugin/bin/ "
        f"(run: python scripts/build_hook_binary.py): {missing}"
    )


def test_every_matrix_binary_is_tracked_in_git():
    """Present-on-disk is not enough — a fresh clone only ships TRACKED files.

    This is the direct-install guarantee: the binaries must be in git, not just in a
    dirty working tree. It is the regression test for the gap where bin/.gitignore
    ignored `dos-hook-*` / `*.exe`, so a marketplace install (a clone) got none.
    """
    tracked = _tracked_files("claude-plugin/bin")
    expected = {f"claude-plugin/bin/{n}" for n in _expected_binary_names()}
    untracked = sorted(expected - tracked)
    assert not untracked, (
        "native hook binaries are NOT tracked in git, so a `/plugin marketplace add` "
        "clone would not ship them (git add them): " + ", ".join(untracked)
    )


def test_launchers_are_tracked():
    """The per-arch launchers the hooks.json calls must ship too (POSIX + PowerShell)."""
    tracked = _tracked_files("claude-plugin/bin")
    for launcher in ("claude-plugin/bin/dos-hook", "claude-plugin/bin/dos-hook.ps1"):
        assert launcher in tracked, f"launcher not tracked in git: {launcher}"


def test_posix_launchers_and_binaries_are_executable_in_git():
    """Fresh plugin clones must preserve +x for the POSIX launcher and binaries."""
    modes = _tracked_modes("claude-plugin/bin")
    expected = {"claude-plugin/bin/dos-hook"}
    expected.update(
        f"claude-plugin/bin/{name}"
        for name in _expected_binary_names()
        if not name.endswith(".exe")
    )
    not_executable = [
        path for path in sorted(expected)
        if modes.get(path) != "100755"
    ]
    assert not not_executable, (
        "POSIX plugin hook launchers/binaries must be tracked with mode 100755 "
        "so a marketplace clone ships executable hooks: " + ", ".join(not_executable)
    )


def test_posix_launcher_uses_lf_line_endings():
    """`sh` rejects CRLF in the launcher on POSIX plugin installs."""
    data = (_BIN_DIR / "dos-hook").read_bytes()
    assert b"\r\n" not in data, "claude-plugin/bin/dos-hook must use LF line endings"


@pytest.mark.skipif(os.name == "nt", reason="POSIX execute-bit repair is not meaningful on Windows")
def test_posix_launcher_repairs_stripped_native_execute_bit(tmp_path):
    """The launcher self-heals an installer that preserves bytes but strips +x."""
    if shutil.which("sh") is None:
        pytest.skip("sh not available")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launcher = bin_dir / "dos-hook"
    shutil.copy2(_BIN_DIR / "dos-hook", launcher)

    goos, goarch = build_hook_binary._host_arch().split("/", 1)
    if goos == "windows":
        pytest.skip("host arch maps to Windows")
    native = bin_dir / build_hook_binary._binary_name(goos, goarch)
    native.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    native.chmod(0o644)

    proc = subprocess.run(
        ["sh", str(launcher), "pretool", "--workspace", "."],
        cwd=tmp_path,
        input="{}",
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert os.access(native, os.X_OK), "launcher did not chmod the stripped native binary"


def test_gitignore_does_not_ignore_the_binaries():
    """Guard the reversal: bin/.gitignore must not re-ignore the bundled binaries.

    A stray `dos-hook-*` or `*.exe` line here would silently un-bundle them on the
    next `git add` even though they are tracked today — so assert git itself does not
    consider any matrix binary ignored (`git check-ignore` exits 0 only on a match).
    """
    for name in _expected_binary_names():
        proc = subprocess.run(
            ["git", "check-ignore", f"claude-plugin/bin/{name}"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True,
        )
        # exit 0 == the path IS ignored == regression. exit 1 == not ignored == good.
        assert proc.returncode != 0, (
            f"claude-plugin/bin/{name} is gitignored — it would not ship in a clone. "
            f"matched rule: {proc.stdout.strip()!r}"
        )


def test_matrix_covers_amd64_and_arm64_across_os():
    """The advertised cross-platform grid: amd/intel (amd64) + arm64 on lin/mac/win."""
    arches = set(build_hook_binary.DEFAULT_ARCHES)
    for required in (
        "linux/amd64", "linux/arm64",
        "darwin/amd64", "darwin/arm64",
        "windows/amd64", "windows/arm64",
    ):
        assert required in arches, f"default build matrix is missing {required}"
