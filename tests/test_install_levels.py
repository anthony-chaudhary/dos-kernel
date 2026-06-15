"""Prove the install actually works — at many levels — and let DOS be the witness.

The drift gate (`test_install_drift`) and the wrapper-contract properties
(`test_prop_install_wrappers`) check what the install surface *says*. This file
checks what it *does*: it runs a REAL install at each level DOS documents, then
asks DOS itself whether the install succeeded — `dos doctor` / `dos verify` on the
freshly-installed CLI, not the installer's own "done". That is the dogfood: the
kernel adjudicates its own install the same way it adjudicates an agent's claim —
from an independently-authored witness (the installed binary's reported version +
resolved path), never the installer's say-so.

The levels (each SKIPS cleanly when its tool is absent, so a CI box without uv or
WSL still goes green — the suite never *fails* for a missing optional tool, it
just reports the level as skipped):

  * uvx ephemeral         — `uvx --from <repo> dos doctor`            (needs uv)
  * uv tool install       — `uv tool install <repo>` + uninstall      (needs uv)
  * pip into a fresh venv — `python -m venv` + `pip install -e .`      (always, gated on venv)
  * install.sh wrapper    — `./install.sh doctor` delegates to install.py (POSIX/Git-Bash)
  * install.ps1 wrapper   — `install.ps1 doctor` delegates             (Windows)
  * REAL WSL              — extract tracked tree on the Linux FS, venv + pip -e,
                            witness with the WSL `dos`                  (needs wsl.exe)

Safety (lessons baked in): every subprocess gets `stdin=DEVNULL` + a timeout and
NEVER runs sudo, so no level can wedge on an interactive prompt (the WSL-sudo-hang
trap, docs note). Each level installs into a throwaway dir/venv and cleans up.
These are integration tests — slower than the pure suite — but they are the only
proof that "it installs" is true rather than asserted.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

import dos

# Heavy (real pip/uv/uvx installs into fresh venvs, ~3-7s/test) — excluded from
# the `dev.py fast` inner loop, still run in full CI. See pyproject [tool.pytest].
pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION = dos.__version__

# A generous default ceiling; a real `pip install -e` of a near-stdlib package is
# seconds, but a cold uv/venv build on a loaded CI box can spike.
_TIMEOUT = 300


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


def _run(cmd, *, cwd=None, timeout=_TIMEOUT, env=None):
    """Run a command safely: no stdin (can't hang on a prompt), captured, timed."""
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _assert_doctor_reports_version(doctor_output: str, *, where: str) -> None:
    """The dogfood assertion: the installed `dos doctor` names THIS version.

    `dos doctor`'s first line is `DOS vX.Y.Z`; the install succeeded iff that
    equals the package version. We deliberately read the BINARY's self-report
    (authored by the freshly-installed code), not the installer's exit message.
    """
    head = doctor_output.strip().splitlines()[0] if doctor_output.strip() else ""
    assert f"v{VERSION}" in head, (
        f"[{where}] freshly-installed `dos doctor` reported {head!r}, expected a "
        f"`DOS v{VERSION}` banner. Output:\n{doctor_output[:500]}"
    )


# ---------------------------------------------------------------------------
# uv levels (uvx ephemeral + uv tool install)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _have("uv"), reason="uv not installed")
def test_uvx_ephemeral_install_and_dogfood(tmp_path) -> None:
    """`uvx --from <repo> dos doctor` builds + runs the CLI, ephemerally.

    We point uv at an ISOLATED cache + tool dir via env so a sibling test's
    `uv tool uninstall` (in this same module) can never invalidate this run's
    ephemeral build mid-flight — the tests stay order-independent under the
    shared global uv cache they'd otherwise contend on.
    """
    env = dict(os.environ)
    env["UV_CACHE_DIR"] = str(tmp_path / "uv-cache")
    env["UV_TOOL_DIR"] = str(tmp_path / "uv-tools")
    env["UV_TOOL_BIN_DIR"] = str(tmp_path / "uv-bin")
    r = _run(["uv", "tool", "run", "--from", str(REPO_ROOT),
              "dos", "doctor", "--workspace", str(REPO_ROOT)], env=env)
    assert r.returncode == 0, f"uvx run failed:\n{r.stderr[:800]}"
    _assert_doctor_reports_version(r.stdout, where="uvx")


@pytest.mark.skipif(not _have("uv"), reason="uv not installed")
def test_uv_tool_install_then_uninstall(tmp_path) -> None:
    """`uv tool install <repo>` installs `dos`/`dos-mcp`, then uninstalls cleanly.

    We isolate uv's tool dirs into tmp via env so this never touches the
    developer's real `uv tool` state, and always uninstall in a finally.
    """
    env = dict(os.environ)
    env["UV_TOOL_DIR"] = str(tmp_path / "uv-tools")
    env["UV_TOOL_BIN_DIR"] = str(tmp_path / "uv-bin")
    try:
        r = _run(["uv", "tool", "install", "--force", str(REPO_ROOT)], env=env)
        assert r.returncode == 0, f"uv tool install failed:\n{r.stderr[:800]}"
        # Both console scripts must have been installed.
        out = (r.stdout + r.stderr).lower()
        assert "dos" in out and "dos-mcp" in out, (
            f"uv tool install did not report the dos/dos-mcp executables:\n{out[:500]}"
        )
        # Dogfood: run the installed binary from the isolated bin dir.
        bin_dir = Path(env["UV_TOOL_BIN_DIR"])
        dos_exe = bin_dir / ("dos.exe" if sys.platform == "win32" else "dos")
        assert dos_exe.exists(), f"installed dos not found at {dos_exe}"
        d = _run([str(dos_exe), "doctor", "--workspace", str(REPO_ROOT)], env=env)
        assert d.returncode == 0, f"installed dos doctor failed:\n{d.stderr[:500]}"
        _assert_doctor_reports_version(d.stdout, where="uv tool")
    finally:
        _run(["uv", "tool", "uninstall", "dos-kernel"], env=env)


# ---------------------------------------------------------------------------
# pip into a throwaway venv — the always-available level
# ---------------------------------------------------------------------------

def test_pip_editable_into_fresh_venv_then_dogfood(tmp_path) -> None:
    """`python -m venv` + `pip install -e <repo>` yields a working `dos` CLI.

    The path every machine can run (no uv needed). We build a clean venv, do an
    editable install of the repo, then witness with the venv's own `dos doctor`.
    """
    venv_dir = tmp_path / "venv"
    venv.create(venv_dir, with_pip=True)
    if sys.platform == "win32":
        vpy = venv_dir / "Scripts" / "python.exe"
        vdos = venv_dir / "Scripts" / "dos.exe"
    else:
        vpy = venv_dir / "bin" / "python"
        vdos = venv_dir / "bin" / "dos"

    inst = _run([str(vpy), "-m", "pip", "install", "-q", "-e", str(REPO_ROOT)])
    assert inst.returncode == 0, f"pip install -e failed:\n{inst.stderr[:800]}"
    assert vdos.exists(), f"`dos` console script not installed at {vdos}"

    d = _run([str(vdos), "doctor", "--workspace", str(REPO_ROOT)])
    assert d.returncode == 0, f"installed dos doctor failed:\n{d.stderr[:500]}"
    _assert_doctor_reports_version(d.stdout, where="pip -e venv")

    # Second witness: the truth syscall answers from git in the real repo.
    v = _run([str(vdos), "verify", "--workspace", str(REPO_ROOT),
              "docs/82_liveness-oracle-plan", "liveness"])
    assert v.returncode in (0, 1), f"dos verify crashed:\n{v.stderr[:500]}"
    assert "via" in v.stdout, (
        f"dos verify gave no rung verdict (expected `(via …)`):\n{v.stdout[:300]}"
    )


# ---------------------------------------------------------------------------
# The repo-local wrappers delegate to install.py (doctor is read-only + safe)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell wrapper")
def test_install_sh_delegates_to_install_py() -> None:
    """`./install.sh doctor` finds a Python and forwards to install.py doctor.

    `doctor` is read-only (it builds no venv, touches no PATH), so this exercises
    the wrapper's Python-discovery + verbatim-forward without side effects.
    """
    sh = shutil.which("sh") or "/bin/sh"
    r = _run([sh, str(REPO_ROOT / "install.sh"), "doctor"], cwd=REPO_ROOT)
    # install.py doctor exits non-zero when the .venv is missing; that's the
    # delegation working, not a wrapper bug. We assert it REACHED install.py.
    combined = r.stdout + r.stderr
    assert "install.py doctor" in combined or "health check" in combined, (
        f"install.sh did not reach install.py doctor:\n{combined[:600]}"
    )
    assert "Using Python" in combined, (
        f"install.sh did not report the Python it found:\n{combined[:400]}"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell wrapper")
def test_install_ps1_delegates_to_install_py() -> None:
    """`install.ps1 doctor` finds a Python and forwards to install.py doctor."""
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        pytest.skip("no PowerShell on PATH")
    r = _run([ps, "-ExecutionPolicy", "Bypass", "-File",
              str(REPO_ROOT / "install.ps1"), "doctor"], cwd=REPO_ROOT)
    combined = r.stdout + r.stderr
    assert "install.py doctor" in combined or "health check" in combined, (
        f"install.ps1 did not reach install.py doctor:\n{combined[:600]}"
    )
    assert "Using Python" in combined, (
        f"install.ps1 did not report the Python it found:\n{combined[:400]}"
    )


# ---------------------------------------------------------------------------
# REAL WSL — extract the tracked tree on the Linux FS, venv + pip -e, dogfood.
# ---------------------------------------------------------------------------

def _wsl_available() -> bool:
    if not _have("wsl.exe") and sys.platform != "win32":
        return False
    exe = shutil.which("wsl.exe") or shutil.which("wsl")
    if not exe:
        return False
    try:
        r = subprocess.run([exe, "--", "bash", "-lc", "echo ok"],
                           stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=30)
        return r.returncode == 0 and "ok" in r.stdout
    except (subprocess.SubprocessError, OSError):
        return False


@pytest.mark.skipif(not _wsl_available(), reason="WSL not available")
def test_real_wsl_pip_install_and_dogfood(tmp_path) -> None:
    """A genuine pip-editable install INSIDE WSL, witnessed by the WSL `dos`.

    We copy the tracked source tree (git archive — tracked files only, the
    'verify against the tracked tree' discipline) PLUS the new wrappers into a
    staging dir the WSL side reads via a Windows path, extract it onto the WSL
    Linux home (NOT /mnt/c — the venv-on-DrvFs speed trap), build a venv, pip
    install -e, and ask the resulting WSL `dos doctor` for its version.

    No sudo anywhere (so it cannot hang on a password prompt); stdin is closed and
    a timeout bounds it; the WSL work dir is removed at the end.
    """
    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    assert wsl

    # Stage a tracked-only tarball + the (possibly-uncommitted) wrappers under a
    # Windows path WSL can reach. git archive excludes untracked files, so we add
    # the wrappers explicitly — they may not be committed yet.
    stage = tmp_path / "stage"
    stage.mkdir()
    tar = stage / "src.tar"
    arch = _run(["git", "archive", "--format=tar", "HEAD", "-o", str(tar)],
                cwd=REPO_ROOT)
    assert arch.returncode == 0 and tar.exists(), f"git archive failed:\n{arch.stderr}"
    for extra in ("install.sh", "install.ps1", "install.py"):
        src = REPO_ROOT / extra
        if src.exists():
            shutil.copy2(src, stage / extra)

    # Convert the Windows staging path to a WSL path. We pass it through
    # `bash -lc 'wslpath ...'` with the path FORWARD-SLASHED and single-quoted —
    # a raw backslash path handed straight to wsl.exe gets its backslashes eaten
    # as escapes (`C:\dir` arrives as `C:dir`), so wslpath fails.
    fwd = str(stage).replace("\\", "/")
    wp = _run([wsl, "--", "bash", "-lc", f"wslpath -u '{fwd}'"])
    assert wp.returncode == 0, f"wslpath failed:\n{wp.stderr}"
    wsl_stage = wp.stdout.strip()

    # The install+witness script, run on the Linux FS. We WRITE it to a file in
    # the staging dir and run `bash <file>` rather than `bash -lc '<script>'`:
    # passing a multi-line script with `$HOME`/`"$@"` through Python → wsl.exe →
    # bash mangles the quoting (the `$HOME` arrives quoted-literal and expands to
    # empty). A script file is read verbatim by bash, so its own quoting holds.
    # Use LF line endings explicitly (a CRLF script breaks bash on `\r`).
    script = (
        "set -e\n"
        'WORK=$(mktemp -d "${HOME}/dos-wsl-itest.XXXXXX")\n'
        'mkdir -p "${WORK}/repo"\n'
        f'tar -xf "{wsl_stage}/src.tar" -C "${{WORK}}/repo"\n'
        f'cp -f "{wsl_stage}"/install.* "${{WORK}}/repo/" 2>/dev/null || true\n'
        'cd "${WORK}/repo"\n'
        'python3 -m venv "${WORK}/venv"\n'
        '"${WORK}/venv/bin/python" -m pip install -q -e . </dev/null\n'
        'echo "DOCTOR_BEGIN"\n'
        '"${WORK}/venv/bin/dos" doctor --workspace . 2>&1 | head -1\n'
        'echo "DOCTOR_END"\n'
        'rm -rf "${WORK}"\n'
    )
    script_file = stage / "wsl_install.sh"
    script_file.write_bytes(script.encode("utf-8"))  # LF-only (no \r)
    r = _run([wsl, "--", "bash", f"{wsl_stage}/wsl_install.sh"], timeout=_TIMEOUT)
    assert r.returncode == 0, (
        f"WSL install failed (rc={r.returncode}):\nSTDOUT:\n{r.stdout[:800]}\n"
        f"STDERR:\n{r.stderr[:800]}"
    )
    # Extract the doctor banner between the markers and dogfood it.
    out = r.stdout
    banner = ""
    if "DOCTOR_BEGIN" in out and "DOCTOR_END" in out:
        banner = out.split("DOCTOR_BEGIN", 1)[1].split("DOCTOR_END", 1)[0].strip()
    assert f"v{VERSION}" in banner, (
        f"WSL `dos doctor` reported {banner!r}, expected `DOS v{VERSION}`.\n"
        f"Full output:\n{out[:800]}"
    )
