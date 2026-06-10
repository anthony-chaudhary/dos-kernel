#!/usr/bin/env sh
# DOS kernel — one-command installer (POSIX: Linux, macOS, WSL, Git Bash).
#
# This is a thin bootstrap over install.py: it finds a usable Python 3.11+,
# then hands every argument straight through. Run it from a clone of this repo:
#
#     ./install.sh                 # venv + editable install + `dos` on PATH
#     ./install.sh --extras mcp    # + the MCP server (dos-mcp)
#     ./install.sh doctor          # read-only health check (resolved path+version)
#     ./install.sh uninstall       # remove the venv + PATH shims
#
# It is NOT a `curl | sh` remote installer — there is nothing to download; you
# already have the source. (DOS is near-stdlib: the core kernel's only runtime
# dependency is PyYAML.) No clone at all? The default no-clone install is
# straight from the public repo:
#   pip install "dos-kernel @ git+https://github.com/anthony-chaudhary/dos-kernel.git"
# If you prefer a package manager, see docs/INSTALL.md — `uv tool install .`
# and `pip install -e .` are first-class alternatives.
#
# SECURITY: this script is committed in the repo you just cloned — read it
# before running. It runs only `python install.py "$@"` against the local tree;
# it fetches nothing from the network.
set -eu

# Resolve the repo dir even when invoked via a symlink or from elsewhere.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
cd "$SCRIPT_DIR"

# Find a Python 3.11+ interpreter. Order: python3, python, then the `py`
# launcher (Git-Bash-on-Windows). We probe the version so a stale Python 3.10
# fails with an actionable message instead of a confusing import error later.
find_python() {
    for cand in python3 python py; do
        if command -v "$cand" >/dev/null 2>&1; then
            if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
                printf '%s\n' "$cand"
                return 0
            fi
        fi
    done
    return 1
}

PY=$(find_python) || {
    echo "Error: no Python 3.11+ found on PATH (looked for python3 / python / py)." >&2
    echo "  Install CPython 3.11+ from https://www.python.org/downloads/ (or your" >&2
    echo "  distro's python3 package) and re-run ./install.sh." >&2
    echo "  Prefer a managed install? 'uv tool install .' brings its own Python —" >&2
    echo "  see docs/INSTALL.md." >&2
    exit 1
}

echo "Using $("$PY" --version 2>&1) ($(command -v "$PY"))"
exec "$PY" install.py "$@"
