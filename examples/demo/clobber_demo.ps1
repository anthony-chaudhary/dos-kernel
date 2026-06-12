# Two agents, one file — the lost update, and the referee that refuses it (PowerShell).
#
# Two concurrent loops both "improve" the same file. Without a referee the
# second save silently destroys the first (the classic lost update) — and both
# agents report success. With the arbiter, the second loop is REFUSED before it
# starts, with a typed reason it can act on, and admitted the moment the lane
# frees. The contrast — refused-with-a-reason vs. silently-lost-work — is the
# whole demo.
#
#   pwsh examples/demo/clobber_demo.ps1
#
# Requires: `dos` on PATH (pip install dos-kernel) and git.
$ErrorActionPreference = 'Stop'
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("dos-demo-" + [System.IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $work -Force | Out-Null
try {
    Set-Location $work

    Write-Output '$ dos init . && git init -q'
    dos init . | ForEach-Object { "  $_" }
    git init -q
    git config user.email demo@example.com
    git config user.name  "Demo"
    git config commit.gpgsign false
    git config core.autocrlf false
    Set-Content -Path service.md -Value 'retry budget: 3' -Encoding ascii
    git add -A
    git commit -q -m "seed: the shared service notes"
    Write-Output ''

    Write-Output '# ROUND 1 — no referee. Agents A and B both read service.md, work, then save.'
    $base = Get-Content service.md -Raw
    Set-Content -Path service.md -Value ($base + "A: raised the rate limit") -Encoding ascii
    Write-Output '  agent A saved its line — and reports success.'
    Set-Content -Path service.md -Value ($base + "B: added the retry loop") -Encoding ascii
    Write-Output '  agent B saved its line — and reports success.'
    Write-Output ''
    Write-Output '# Both narrated success. What does the file actually hold?'
    Write-Output '$ cat service.md'
    Get-Content service.md | ForEach-Object { "  $_" }
    if (-not (Select-String -Path service.md -Pattern '^A:' -Quiet)) {
        Write-Output "  -> agent A's work is GONE (the lost update) — and no narration said so."
    }
    Write-Output ''

    Write-Output '# ROUND 2 — the same two agents, but each asks the arbiter before starting.'
    Write-Output '$ dos lease-lane acquire --lane main --owner agent-a'
    dos lease-lane acquire --lane main --owner agent-a
    Write-Output ''
    Write-Output '# Agent B asks at the same moment it clobbered in round 1:'
    Write-Output '$ dos arbitrate --lane main --explain'
    dos arbitrate --lane main --explain
    Write-Output "  exit=$LASTEXITCODE  (1 = REFUSE — typed, checkable, BEFORE any work was lost)"
    Write-Output ''
    Write-Output '# Agent A finishes and releases; B is admitted the moment the lane frees:'
    Write-Output '$ dos lease-lane release --lane main --owner agent-a'
    dos lease-lane release --lane main --owner agent-a
    Write-Output '$ dos arbitrate --lane main --explain'
    dos arbitrate --lane main --explain
    Write-Output ''
    Write-Output '# Boundary: the arbiter is ADVISORY — it returns the verdict; the loop''s'
    Write-Output '# compliance (B waiting) is what prevents the write. Wire the call into'
    Write-Output '# your loop''s admission step, exactly as round 2 did.'
}
finally {
    Set-Location ([System.IO.Path]::GetTempPath())
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
