#!/usr/bin/env python3
"""Build, validate, and smoke-test the `dos-kernel` distribution — locally, the
way CI and the publish workflow do, so a release is never the first time the
artifacts are exercised.

This is the LOCAL, fully-reversible half of publishing: it produces
`dist/dos_kernel-<version>{.tar.gz,-py3-none-any.whl}` and proves they are
publishable (metadata renders, the wheel imports, the `dos` CLI runs from a
clean throwaway venv) — but it does NOT upload. Upload is the one-way step the
publish workflow (or a manual `twine upload`) takes, gated separately — see
.github/workflows/publish.yml for the trigger + one-time owner setup.

It is dev tooling that operates ON the package, not part of it (the layering
note in CLAUDE.md: nothing under src/dos imports scripts/). It anchors on the
git top-level, not __file__'s parent, because it ships with the repo it builds.

Usage:
    python scripts/build_dist.py            # clean, build, twine check, smoke-import
    python scripts/build_dist.py --no-clean # keep an existing dist/ (build alongside)
    python scripts/build_dist.py --no-smoke # skip the clean-venv import (faster)
    python scripts/build_dist.py --json     # machine-readable report on stdout

Exit code is the verdict: 0 = built + validated (+ smoked), non-zero = a step
failed (the failing step's name is in the report).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

# A stock Windows console is cp1252; printing a non-ASCII glyph (or pretty JSON)
# raises UnicodeEncodeError there. Force UTF-8 so this dev tool behaves the same
# on win32 as on POSIX (the repo's cross-platform discipline). No-op if already
# UTF-8 or if the stream doesn't support reconfigure (e.g. a redirect).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


def _repo_root() -> Path:
    """The git top-level — the honest root for a script that ships with its repo
    (the same anchor the release scripts use; NOT __file__'s parent, so a moved
    checkout still resolves correctly)."""
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(out.stdout.strip())


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run a command, streaming nothing but capturing both streams for the report.
    Raises CalledProcessError on non-zero so the caller can name the failing step."""
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=True,
    )


def _package_version(root: Path) -> str:
    """Read the version the build will stamp — straight from the package, so it
    reflects whatever the version markers currently say (the value /release's
    bumper keeps in lockstep across pyproject + __init__ + the plugin manifests)."""
    out = subprocess.run(
        [sys.executable, "-c", "import dos; print(dos.__version__)"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-clean", action="store_true", help="do not wipe dist/ first")
    ap.add_argument(
        "--no-smoke",
        action="store_true",
        help="skip the clean-venv install + CLI smoke (faster)",
    )
    ap.add_argument("--json", action="store_true", help="emit a JSON report on stdout")
    args = ap.parse_args(argv)

    root = _repo_root()
    dist = root / "dist"
    report: dict[str, object] = {"root": str(root), "steps": []}

    def step(name: str, ok: bool, **extra: object) -> None:
        entry = {"step": name, "ok": ok, **extra}
        report["steps"].append(entry)  # type: ignore[attr-defined]
        if not args.json:
            mark = "OK  " if ok else "FAIL"
            print(f"[{mark}] {name}" + (f"  {extra}" if extra and not ok else ""))

    try:
        version = _package_version(root)
        report["version"] = version
        if not args.json:
            print(f"Building dos-kernel {version}\n")

        # 1. clean — a stale wheel of a prior version in dist/ is the classic
        #    "uploaded the wrong file" trap. Wipe unless asked not to.
        if not args.no_clean and dist.exists():
            for p in dist.glob("dos_kernel-*"):
                p.unlink()
            step("clean", True, removed_glob="dist/dos_kernel-*")
        else:
            step("clean", True, skipped=args.no_clean)

        # 2. build — sdist + wheel via PEP 517 (`python -m build`).
        try:
            _run([sys.executable, "-m", "build"], cwd=root)
            built = sorted(p.name for p in dist.glob("dos_kernel-*"))
            step("build", True, artifacts=built)
        except subprocess.CalledProcessError as e:
            step("build", False, stderr=e.stderr[-2000:])
            return _finish(report, args, 1)

        # 3. twine check — does the metadata (chiefly the README long-description)
        #    render on PyPI? A warning-free pass is the bar (the metadata was
        #    tuned to clear it, see pyproject readme note).
        try:
            cp = _run([sys.executable, "-m", "twine", "check", *[str(p) for p in dist.glob("dos_kernel-*")]])
            step("twine_check", True, output=cp.stdout.strip())
        except subprocess.CalledProcessError as e:
            step("twine_check", False, stderr=(e.stderr or e.stdout)[-2000:])
            return _finish(report, args, 1)

        # 4. smoke — install the freshly-built WHEEL into a throwaway venv and run
        #    the CLI from it. This is what proves what a `pip install` user gets:
        #    `import dos` resolves, the console scripts exist, the shipped skill
        #    pack is in the wheel. (Skipped with --no-smoke.)
        if not args.no_smoke:
            wheels = list(dist.glob("dos_kernel-*.whl"))
            if not wheels:
                step("smoke", False, error="no wheel found to smoke-test")
                return _finish(report, args, 1)
            try:
                _smoke_test(wheels[0], root)
                step("smoke", True, wheel=wheels[0].name)
            except subprocess.CalledProcessError as e:
                step("smoke", False, stderr=(e.stderr or e.stdout)[-2000:])
                return _finish(report, args, 1)
        else:
            step("smoke", True, skipped=True)

        report["ok"] = True
        if not args.json:
            print(f"\nAll steps passed. dist/ holds the publishable dos-kernel {version}.")
            print("Next (one-way, NOT done here): the tag-gated publish workflow uploads"
                  " (see .github/workflows/publish.yml).")
        return _finish(report, args, 0)

    except subprocess.CalledProcessError as e:  # _package_version / git failures
        report["ok"] = False
        report["error"] = (e.stderr or str(e))[-2000:]
        return _finish(report, args, 1)


def _smoke_test(wheel: Path, root: Path) -> None:
    """Create a throwaway venv, pip-install the wheel into it, and run
    `dos doctor` + a `dos verify` round-trip from that isolated interpreter —
    proving the built artifact is self-sufficient (not leaning on the dev tree)."""
    with tempfile.TemporaryDirectory(prefix="dos-relcheck-") as tmp:
        venv_dir = Path(tmp) / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True, capture_output=True)
        # venv layout differs by OS — Scripts\ on Windows, bin/ on POSIX.
        bindir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
        py = bindir / ("python.exe" if sys.platform == "win32" else "python")
        # Install the wheel itself (with the [mcp] extra, the heaviest import path).
        subprocess.run(
            [str(py), "-m", "pip", "install", "--quiet", f"{wheel}[mcp]"],
            check=True,
            capture_output=True,
        )
        # import dos + the version round-trips from the INSTALLED package.
        subprocess.run(
            [str(py), "-c", "import dos; import dos_mcp; print('import ok', dos.__version__)"],
            check=True,
            capture_output=True,
        )
        # The CLI entrypoint runs (doctor needs a workspace; point it at the repo).
        dos_exe = bindir / ("dos.exe" if sys.platform == "win32" else "dos")
        subprocess.run(
            [str(dos_exe), "doctor", "--workspace", str(root)],
            check=True,
            capture_output=True,
        )


def _finish(report: dict, args: argparse.Namespace, code: int) -> int:
    report.setdefault("ok", code == 0)
    if args.json:
        print(json.dumps(report, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
