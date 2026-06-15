# r.ps1 — the single-key roll-up. Press `r`, see the most important DOS SOTA items.
#
# The DOS SOTA scanner (scripts/sota_scan.py) tracks the field for DOS-relevant
# work. This is its clean user view: the most important items, ranked, with the
# long tail collapsed to a count. It runs `sota_scan.py --rollup`.
#
# NOTE: `r` is a built-in PowerShell alias for Invoke-History, and an alias wins
# over a function of the same name. So this script REMOVES that alias (for the
# session) before defining `function r`, so pressing `r` runs the roll-up.
#
# Two ways to use it:
#
#   1. Run it directly (prints the roll-up and exits):
#        powershell -File scripts\r.ps1        # Windows PowerShell 5.1
#        pwsh -File scripts/r.ps1              # PowerShell 7+
#
#   2. Make `r` a single-key command in your shell — dot-source it once so the
#      function `r` is defined (and the Invoke-History alias removed) for the
#      session, then just type `r`:
#        . .\scripts\r.ps1        # defines `r` (note the leading dot + space)
#        r                        # the single key
#
#      To get `r` in every new shell, add that dot-source line to your profile
#      ($PROFILE), pointing at this file's full path, e.g.:
#        . <path-to-repo>\scripts\r.ps1
#
# `r` accepts the same knobs as --rollup, e.g.  r -Top 12   or   r -All.

# Drop the built-in `r` → Invoke-History alias so the function below wins.
# Scope Global so it also takes effect when this file is dot-sourced.
if (Get-Alias r -ErrorAction SilentlyContinue) {
    Remove-Item Alias:\r -Force -ErrorAction SilentlyContinue
}

function global:r {
    [CmdletBinding()]
    param(
        [int]$Top = 8,           # how many top items before the tail count
        [switch]$All,            # rank the whole ledger, not just the latest scan
        [switch]$Json            # machine JSON instead of the report
    )
    # Resolve the repo's sota_scan.py relative to THIS script, so `r` works from
    # any working directory once it's defined.
    $scanner = Join-Path $PSScriptRoot 'sota_scan.py'
    $py = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'py' }
    $cliArgs = @($scanner, '--rollup', '--top', $Top)
    if ($All)  { $cliArgs += '--all' }
    if ($Json) { $cliArgs += '--json' }
    & $py @cliArgs
}

# When this file is RUN (not dot-sourced), $MyInvocation.InvocationName is the
# path to the script; when dot-sourced it is '.'. Run the roll-up only in the
# run case, so dot-sourcing just defines `r` (and removes the alias) silently.
if ($MyInvocation.InvocationName -ne '.') {
    r @args
}
