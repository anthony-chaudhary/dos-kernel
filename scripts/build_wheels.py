#!/usr/bin/env python3
"""build_wheels.py — build the per-platform `dos-kernel` wheels that ship the native
binary, plus the pure-source sdist (docs/286 Phase 2).

> **Tooling that operates ON the package, never inside it** (CLAUDE.md "Four things
> live OUTSIDE the four layers"). Like `build_dist.py`/`build_hook_binary.py` and the
> release scripts, this consumes the repo and is unaware to the kernel — nothing under
> `src/dos/` imports it, and it is not shipped in any wheel. It anchors on the git
> top-level, not `__file__`'s parent.

The problem it solves
=====================

`pip install dos-kernel` builds a PURE-PYTHON `py3-none-any` wheel today, so a pip user
pays the full ~600 ms Python cold-start on every hook call and never sees the native
fast-path (docs/270's 16-43x win). This script produces, instead, ONE wheel PER
OS/arch, each embedding only its matching static `dos-hook` binary at `dos/_bin/`, so
pip downloads only the one wheel for the installing machine (docs/286's chosen
"per-platform wheels", the cibuildwheel-style best practice for shipping a binary).

Because the binary is a static GO executable (NOT a CPython C-extension), each wheel is
platform-specific but **ABI-independent** — tag `py3-none-<platform>`, so ONE wheel per
OS/arch covers Python 3.11/3.12/3.13 at once. This is materially simpler than full
`cibuildwheel` (no per-interpreter build, no manylinux C-toolchain image for our code).

What it does, per arch
======================

1. `go build` the static no-cgo binary into `src/dos/_bin/dos-hook[.exe]` (the one
   file the package-data glob ships) — the `build_hook_binary.py` invocation, but to
   the IN-PACKAGE `_bin` dir, not the plugin bundle.
2. `python -m build --wheel` → a `dos_kernel-<v>-py3-none-any.whl` carrying that binary.
3. `wheel tags --platform-tag <PLATFORM> --remove` → re-tag it to
   `dos_kernel-<v>-py3-none-<PLATFORM>.whl` so pip selects it only on the matching
   platform. (A `py3-none-any` wheel that secretly held a Windows binary would install
   on Linux and silently ship the wrong binary — the re-tag is what makes it honest.)

The **sdist** is built ONCE, separately, with NO binary in `_bin` — a pure-source
distribution that falls through to the Python verb on install (docs/286 §2: we never
`go build` during `pip install`; an off-matrix arch is un-accelerated, never broken).

The matrix + the per-arch binary naming are imported from `build_hook_binary.py` so
this script and the plugin bundler agree by construction (no second copy of the list).

Usage
=====

    python scripts/build_wheels.py                 # sdist + all default-matrix wheels
    python scripts/build_wheels.py --host           # sdist + only the host arch wheel
    python scripts/build_wheels.py --arches windows/amd64 linux/amd64
    python scripts/build_wheels.py --no-sdist       # wheels only
    python scripts/build_wheels.py --check          # report the plan; build nothing

Exit code is the verdict: 0 = every requested artifact built + re-tagged; non-zero =
a step failed (the failing arch is named).

NOTE on cross-OS: Go cross-compiles every arch from one host (static no-cgo), so this
runs end-to-end on a single machine. CI (docs/286 Phase 3) may instead fan the matrix
across native runners (ubuntu/macos/windows) — that is a CI-topology choice; the
artifact this script emits is identical either way.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

# The PyPI platform tag each GOOS/GOARCH maps to, for an ABI-`none` (pure-binary-data)
# wheel. linux uses the broad manylinux2014 baseline (glibc 2.17, ~2014 — covers every
# still-supported distro); macOS/windows use their conventional tags. These are the
# tags pip's platform compatibility check matches against the installing machine.
_PLATFORM_TAGS = {
    "linux/amd64": "manylinux2014_x86_64",
    "linux/arm64": "manylinux2014_aarch64",
    "darwin/amd64": "macosx_10_9_x86_64",
    "darwin/arm64": "macosx_11_0_arm64",
    "windows/amd64": "win_amd64",
    "windows/arm64": "win_arm64",
}


def _repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(out.stdout.strip())


def _load_hook_builder(root: Path):
    """Load build_hook_binary.py by path (scripts/ is not importable) for the SHARED
    matrix + per-arch naming — the same source the plugin bundler + the bundled-binary
    test use, so all three agree."""
    path = root / "scripts" / "build_hook_binary.py"
    spec = importlib.util.spec_from_file_location("_build_hook_binary", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _host_arch(hook_builder) -> str:
    return hook_builder._host_arch()


def _wheel_binary_name(goos: str) -> str:
    """The IN-PACKAGE binary name (arch-free, one per wheel): dos-hook[.exe]."""
    return "dos-hook.exe" if goos == "windows" else "dos-hook"


def _build_binary_into_package(root: Path, goos: str, goarch: str) -> tuple[bool, str]:
    """go build the static binary straight into src/dos/_bin/ (the package-data dir)."""
    go_dir = root / "go"
    bin_dir = root / "src" / "dos" / "_bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    out = bin_dir / _wheel_binary_name(goos)
    env = dict(os.environ)
    env.update(GOOS=goos, GOARCH=goarch, CGO_ENABLED="0")
    proc = subprocess.run(
        ["go", "build", "-trimpath", "-o", str(out), "./cmd/dos-hook"],
        cwd=str(go_dir), env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return False, f"go build {goos}/{goarch} failed: {proc.stderr.strip()}"
    return True, f"{out.relative_to(root).as_posix()} ({out.stat().st_size // 1024} KB)"


def _clear_package_bin(root: Path) -> None:
    """Remove any dos-hook[.exe] from the package _bin so the NEXT build is clean (one
    binary per wheel — a leftover from a prior arch must not ride along)."""
    bin_dir = root / "src" / "dos" / "_bin"
    for name in ("dos-hook", "dos-hook.exe"):
        p = bin_dir / name
        if p.exists():
            p.unlink()


def _clean_for_build(root: Path) -> None:
    """Reset ALL stale state before a `python -m build`: the package `_bin` AND
    setuptools' `build/` staging dir.

    The `_bin` clear alone is NOT enough: `python -m build` copies the source into
    `build/lib/...`, and setuptools does NOT prune files there that no longer exist in
    the source — so a prior arch's binary lingers in `build/lib/dos/_bin/` and gets
    swept into the NEXT wheel (the win_amd64-shipped-a-Mach-O bug). Wiping `build/`
    before each build is the fix: each wheel is staged from scratch, carrying ONLY the
    one binary the current arch wrote.
    """
    _clear_package_bin(root)
    import shutil
    build_dir = root / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)


def _build_wheel(root: Path) -> Path:
    """python -m build --wheel → the freshly-built py3-none-any wheel path."""
    subprocess.run([sys.executable, "-m", "build", "--wheel"], cwd=str(root),
                   check=True, capture_output=True, text=True)
    wheels = sorted((root / "dist").glob("dos_kernel-*-py3-none-any.whl"),
                    key=lambda p: p.stat().st_mtime)
    if not wheels:
        raise FileNotFoundError("python -m build produced no py3-none-any wheel")
    return wheels[-1]


def _retag(root: Path, wheel: Path, platform_tag: str) -> Path:
    """`wheel tags --platform-tag <T> --remove` → the platform-tagged wheel, dropping
    the `any` original so dist/ holds exactly the per-platform artifact."""
    subprocess.run(
        [sys.executable, "-m", "wheel", "tags",
         "--platform-tag", platform_tag, "--remove", str(wheel)],
        cwd=str(root), check=True, capture_output=True, text=True,
    )
    tagged = sorted((root / "dist").glob(f"dos_kernel-*-py3-none-{platform_tag}.whl"),
                    key=lambda p: p.stat().st_mtime)
    if not tagged:
        raise FileNotFoundError(f"retag to {platform_tag} produced no wheel")
    return tagged[-1]


def _build_sdist(root: Path) -> Path:
    """Build the PURE-source sdist with NO binary in _bin (the fallback floor)."""
    _clean_for_build(root)  # clear _bin AND build/ so no arch binary leaks into the sdist
    subprocess.run([sys.executable, "-m", "build", "--sdist"], cwd=str(root),
                   check=True, capture_output=True, text=True)
    sdists = sorted((root / "dist").glob("dos_kernel-*.tar.gz"),
                    key=lambda p: p.stat().st_mtime)
    if not sdists:
        raise FileNotFoundError("python -m build --sdist produced no tarball")
    return sdists[-1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", action="store_true", help="only the host arch wheel")
    ap.add_argument("--arches", nargs="+", metavar="OS/ARCH",
                    help="explicit arches (default: the build_hook_binary matrix)")
    ap.add_argument("--no-sdist", action="store_true", help="skip the sdist")
    ap.add_argument("--check", action="store_true",
                    help="report the plan (arches → platform tags); build nothing")
    args = ap.parse_args(argv)

    import shutil
    if shutil.which("go") is None:
        print("error: Go toolchain not on PATH — cannot build the native binary "
              "(install Go 1.25+).", file=sys.stderr)
        return 2

    root = _repo_root()
    hook_builder = _load_hook_builder(root)

    if args.host:
        arches = [_host_arch(hook_builder)]
    elif args.arches:
        arches = args.arches
    else:
        arches = list(hook_builder.DEFAULT_ARCHES)

    unknown = [a for a in arches if a not in _PLATFORM_TAGS]
    if unknown:
        print(f"error: no PyPI platform tag for {unknown} — known: "
              f"{sorted(_PLATFORM_TAGS)}", file=sys.stderr)
        return 2

    if args.check:
        print("Would build (per-platform wheels):")
        for a in arches:
            print(f"  {a:16s} -> dos_kernel-<v>-py3-none-{_PLATFORM_TAGS[a]}.whl")
        if not args.no_sdist:
            print("  (sdist) -> dos_kernel-<v>.tar.gz   (pure source, no binary)")
        return 0

    ok_all = True
    built: list[str] = []
    for spec in arches:
        goos, goarch = spec.split("/", 1)
        # Reset _bin AND build/ BEFORE this arch's binary build, so the wheel is staged
        # from scratch and carries ONLY this arch's binary — never a prior arch's
        # leftover from setuptools' build/lib/ staging (the win-shipped-a-Mach-O bug).
        _clean_for_build(root)
        ok, msg = _build_binary_into_package(root, goos, goarch)
        if not ok:
            print(f"  ! {msg}", file=sys.stderr)
            ok_all = False
            continue
        try:
            wheel = _build_wheel(root)
            tagged = _retag(root, wheel, _PLATFORM_TAGS[spec])
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"  ! {spec}: {e}", file=sys.stderr)
            ok_all = False
            continue
        finally:
            _clear_package_bin(root)  # never leave a binary in the source tree
        built.append(tagged.name)
        print(f"  built {spec:16s} -> dist/{tagged.name}")

    if not args.no_sdist:
        try:
            sdist = _build_sdist(root)
            built.append(sdist.name)
            print(f"  built sdist             -> dist/{sdist.name}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"  ! sdist: {e}", file=sys.stderr)
            ok_all = False

    print(f"\n{'OK' if ok_all else 'INCOMPLETE'}: {len(built)} artifact(s) in dist/. "
          f"`twine check dist/*` then publish via the tag-gated workflow "
          f"(owner-only upload; see .github/workflows/publish.yml).")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
