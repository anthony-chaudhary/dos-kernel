<#
.SYNOPSIS
    DOS kernel — one-command installer for Windows (PowerShell).

.DESCRIPTION
    A thin bootstrap over install.py: it finds a usable Python 3.11+, then hands
    every argument straight through. Run it from a clone of this repo:

        .\install.ps1                  # venv + editable install + `dos` on PATH
        .\install.ps1 --extras mcp     # + the MCP server (dos-mcp)
        .\install.ps1 doctor           # read-only health check (resolved path+version)
        .\install.ps1 uninstall        # remove the venv + PATH shims

    This is NOT a remote `irm | iex` installer — there is nothing to download;
    you already have the source. (DOS is near-stdlib: the core kernel's only
    runtime dependency is PyYAML.) No clone at all? The default no-clone install
    is straight from the public repo:
        pip install "dos-kernel @ git+https://github.com/anthony-chaudhary/dos-kernel.git"
    For a package-manager path, see docs/INSTALL.md — `uv tool install .` and
    `pip install -e .` are first-class.

    SECURITY: this script is committed in the repo you just cloned — read it
    before running. It runs only `python install.py @args` against the local
    tree; it fetches nothing from the network.

.EXAMPLE
    .\install.ps1 --extras mcp
    Install the kernel plus the MCP server, and put `dos`/`dos-mcp` on your PATH.

.NOTES
    If PowerShell refuses to run the script ("running scripts is disabled"),
    launch it once with an unrestricted process scope (this does NOT change any
    machine policy):
        powershell -ExecutionPolicy Bypass -File .\install.ps1
#>
[CmdletBinding()]
param(
    # Everything after the script name is forwarded verbatim to install.py. We
    # name it $ForwardArgs (NOT $Args) deliberately: $Args is a PowerShell
    # automatic variable, and a param that shadows it is a documented footgun.
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ForwardArgs
)

$ErrorActionPreference = 'Stop'

# Run from the repo dir even if invoked from elsewhere.
Set-Location -LiteralPath $PSScriptRoot

# Find a Python 3.11+ interpreter. The `py` launcher is preferred on Windows
# (it resolves the newest install and dodges the Microsoft Store stub that ships
# as a no-op `python.exe`); fall back to `python` / `python3` on PATH. We probe
# the version so a stale 3.10 fails with an actionable message, not a later
# import error.
function Find-Python {
    foreach ($cand in @('py', 'python', 'python3')) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        # `py` takes -3 to force a Python 3 launcher; the others ignore it via -c.
        $verArgs = if ($cand -eq 'py') { @('-3', '-c') } else { @('-c') }
        $probe = 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
        & $cand @verArgs $probe 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @{ Exe = $cand; PreArgs = ($verArgs[0..($verArgs.Count - 2)]) }
        }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Error @"
No Python 3.11+ found on PATH (looked for py / python / python3).
  Install CPython 3.11+ from https://www.python.org/downloads/ (a full install,
  NOT the Microsoft Store stub) and re-run .\install.ps1.
  Prefer a managed install? 'uv tool install .' brings its own Python — see
  docs\INSTALL.md.
"@
    exit 1
}

# `py -3` has a one-element PreArgs; `python`/`python3` have none.
$preArgs = @($py.PreArgs | Where-Object { $_ })
$verLine = & $py.Exe @preArgs --version 2>&1
Write-Host "Using $verLine ($((Get-Command $py.Exe).Source))"

& $py.Exe @preArgs install.py @ForwardArgs
exit $LASTEXITCODE
