# dos-hook-codex.ps1 — native Windows adapter for Codex tool and Stop-family hooks.
#
# Adapter contract v1:
#   * pass the native Codex JSON envelope to the backend without mapping or
#     normalization;
#   * prefer the bundled native binary, then one installed Python interpreter;
#   * translate a structured PreToolUse permissionDecision=deny into Codex's
#     blocking exit 2, with the backend reason on stderr, before effect;
#   * forward valid protocol JSON only; empty success remains empty;
#   * preserve a structured Stop block so Codex continues the session;
#   * fail open on adapter/backend errors, with one typed, secret-free diagnostic
#     on stderr. PostToolUse is therefore always non-blocking.

$ErrorActionPreference = 'SilentlyContinue'
$adapterVersion = 1
$hookArgs = @($args)

function Write-AdapterDiagnostic {
  param(
    [string]$Hook,
    [string]$Stage,
    [AllowNull()]$Backend,
    [int]$ExitCode
  )

  $diagnostic = [ordered]@{
    schema = 'dos.codex-hook-diagnostic.v1'
    adapter_version = $adapterVersion
    hook = $Hook
    stage = $Stage
    backend = $Backend
    exit_code = $ExitCode
    posture = 'fail_open'
  }
  [Console]::Error.WriteLine(($diagnostic | ConvertTo-Json -Compress))
}

if ($hookArgs.Count -eq 0) {
  Write-AdapterDiagnostic -Hook '' -Stage 'backend_policy' -Backend $null -ExitCode 64
  exit 0
}

$hook = [string]$hookArgs[0]
$backendHook = $hook
if ($hook -notin @('pretool', 'posttool', 'stop', 'stop-transaction', 'stop-failure', 'live-rotate')) {
  Write-AdapterDiagnostic -Hook $backendHook -Stage 'backend_policy' -Backend $null -ExitCode 64
  exit 0
}

$transactionalStop = $hook -eq 'stop-transaction'
if ($transactionalStop) {
  $backendHook = 'stop'
  $hookArgs[0] = 'stop'
}

$selfDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$arch = $env:PROCESSOR_ARCHITECTURE
if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
switch ($arch) {
  'ARM64' { $goarch = 'arm64' }
  default { $goarch = 'amd64' }
}

$native = Join-Path $selfDir "dos-hook-windows-$goarch.exe"
$backend = $null
$backendArgs = @()

if ($hook -notin @('stop-failure', 'live-rotate') -and (Test-Path -LiteralPath $native -PathType Leaf)) {
  $backend = $native
  $backendName = "native-windows-$goarch"
  $backendArgs = @($hookArgs)
} else {
  $python = Get-Command python -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($python) {
    $backend = $python.Source
    $backendName = 'python'
    $backendArgs = @('-m', 'dos.cli', 'hook') + $hookArgs
  } else {
    $py = Get-Command py -CommandType Application -ErrorAction SilentlyContinue |
      Select-Object -First 1
    if ($py) {
      $backend = $py.Source
      $backendName = 'py-3'
      $backendArgs = @('-3', '-m', 'dos.cli', 'hook') + $hookArgs
    }
  }
}

if (-not $backend) {
  Write-AdapterDiagnostic -Hook $hook -Stage 'executable_selection' -Backend $null -ExitCode 127
  exit 0
}

$stdinPayload = [Console]::In.ReadToEnd()
$stderrPath = [IO.Path]::GetTempFileName()
try {
  $stdoutLines = @($stdinPayload | & $backend @backendArgs 2> $stderrPath)
  $backendExit = $LASTEXITCODE
  $backendStderr = [IO.File]::ReadAllText($stderrPath)
} finally {
  Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
}

if ($backendStderr) {
  [Console]::Error.Write($backendStderr)
}

if ($null -eq $backendExit) { $backendExit = 1 }
if ($backendExit -ne 0) {
  Write-AdapterDiagnostic -Hook $backendHook -Stage 'backend_policy' -Backend $backendName -ExitCode $backendExit
  exit 0
}

if ($transactionalStop) {
  # Codex runs sibling hooks concurrently. Keep policy as the sole host-visible
  # decision, then sequence best-effort bookkeeping behind that verdict.
  foreach ($notificationArgs in @(
    @('-m', 'dos.cli', 'hook', 'stop-failure', '--success', '--workspace', '.'),
    @('-m', 'dos.cli', 'hook', 'live-rotate', '--workspace', '.')
  )) {
    try {
      $stdinPayload | & python @notificationArgs 1> $null 2> $null
      if ($LASTEXITCODE -ne 0) {
        Write-AdapterDiagnostic -Hook $backendHook -Stage 'notification_nonzero' -Backend 'python' -ExitCode $LASTEXITCODE
      }
    } catch {
      Write-AdapterDiagnostic -Hook $backendHook -Stage 'notification_unavailable' -Backend 'python' -ExitCode 127
    }
  }
}

$backendStdout = [string]::Join([Environment]::NewLine, [string[]]$stdoutLines)
if ($backendHook -in @('stop-failure', 'live-rotate')) {
  # StopFailure is a notification seam, not a decision protocol. Never allow a
  # backend status message to become host JSON.
  exit 0
}

if ($backendStdout) {
  $decision = $backendStdout | ConvertFrom-Json -ErrorAction SilentlyContinue
  if ($null -eq $decision) {
    Write-AdapterDiagnostic -Hook $hook -Stage 'backend_output' -Backend $backendName -ExitCode 65
    exit 0
  }

  if ($hook -eq 'pretool') {
    $hookOutput = $decision.hookSpecificOutput
    if ($hookOutput.permissionDecision -eq 'deny') {
      $reason = [string]$hookOutput.permissionDecisionReason
      if ($reason) { [Console]::Error.WriteLine($reason) }
      exit 2
    }
  }

  [Console]::Out.Write(($decision | ConvertTo-Json -Compress -Depth 32))
}
exit 0
