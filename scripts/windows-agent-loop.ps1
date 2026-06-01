param(
  [Parameter(Mandatory=$true)]
  [string]$RoomUrl,

  [Parameter(Mandatory=$true)]
  [string]$StreamerName,

  [int]$IntervalSeconds = 15,
  [string]$ProjectPath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
  $ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

if (!(Test-Path $ProjectPath)) {
  throw "Project path not found: $ProjectPath`nHint: pass -ProjectPath explicitly, e.g. C:\auto-record-live"
}
Set-Location $ProjectPath

$bootstrapPython = $null
$bootstrapArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
  $bootstrapPython = "py"
  $bootstrapArgs = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
  $bootstrapPython = "python"
} else {
  throw "Python not found. Install Python 3 and ensure 'py' or 'python' is available in PATH."
}

$venvPython = Join-Path $ProjectPath ".venv\Scripts\python.exe"
if (!(Test-Path $venvPython)) {
  & $bootstrapPython @bootstrapArgs -m venv .venv
}
if (!(Test-Path $venvPython)) {
  throw "Failed to create virtual environment at $venvPython"
}

if (!(Get-Command npm -ErrorAction SilentlyContinue)) {
  throw "npm not found. Install Node.js and ensure npm is available in PATH."
}

# Mirrors the pip-probe + ensurepip recovery in scripts/windows-orchestrator-loop.ps1
# and scripts/windows-recorder-loop.ps1: a venv created without working
# pip (some Windows Python distributions ship ensurepip data that fails to
# bootstrap on first venv creation) is recovered via `ensurepip --upgrade`
# before any pip-driven install runs.
#
# The probe is wrapped in try/catch because $ErrorActionPreference = "Stop"
# promotes native-exe stderr ("No module named pip") into a terminating
# NativeCommandError BEFORE `*> $null` redirection takes effect, so a plain
# `& ... *> $null; if ($LASTEXITCODE) { ... }` would abort the script
# instead of falling through to the recovery branch.
$pipOk = $false
try {
  & $venvPython -m pip --version *> $null
  if ($LASTEXITCODE -eq 0) { $pipOk = $true }
} catch {
  $pipOk = $false
}
if (-not $pipOk) {
  Write-Host "[ARL] ensuring pip in venv"
  & $venvPython -m ensurepip --upgrade
  if ($LASTEXITCODE -ne 0) { throw "python -m ensurepip --upgrade failed (exit $LASTEXITCODE)" }
}

# Mirrors ARL_WIN_INSTALL_MODE in scripts/windows-orchestrator-loop.ps1 and
# scripts/windows-recorder-loop.ps1: if-missing skips
# `pip install -e .` when the .deps-ready sentinel is present; always forces it.
$installMode = if ($env:ARL_WIN_INSTALL_MODE) { $env:ARL_WIN_INSTALL_MODE } else { "if-missing" }
$depsReady = Join-Path $ProjectPath ".venv\.deps-ready"

if ($installMode -eq "always" -or -not (Test-Path $depsReady)) {
  & $venvPython -m pip install -e .
  if ($LASTEXITCODE -ne 0) { throw "pip install -e . failed (exit $LASTEXITCODE)" }
  New-Item -ItemType File -Path $depsReady -Force | Out-Null
}
if (!(Test-Path "node_modules")) {
  npm install
}

$env:ARL_DOUYIN_ROOM_URL = $RoomUrl
$env:ARL_STREAMER_NAME = $StreamerName

$cookieGate = if ($env:ARL_COOKIE_HEALTH_GATE) { $env:ARL_COOKIE_HEALTH_GATE } else { "warning" }
if ($cookieGate -ne "skip") {
  Write-Host "[ARL] running cookie-health gate (mode=$cookieGate)"
  $cookieExit = 0
  try {
    & $venvPython -m arl.cli cookie-health
    $cookieExit = $LASTEXITCODE
  } catch {
    $cookieExit = if ($LASTEXITCODE -ne 0) { $LASTEXITCODE } else { 1 }
  }
  if ($cookieExit -ne 0) {
    if ($cookieGate -eq "fatal") {
      throw "[ARL] cookie expired (exit=$cookieExit). Refresh ARL_DOUYIN_COOKIE / ARL_BILIBILI_SESSDATA, or set ARL_COOKIE_HEALTH_GATE=warning to continue anyway."
    }
    Write-Warning "[ARL] cookie expired (exit=$cookieExit) - launcher continuing; recordings may be degraded. Refresh cookie env vars or set ARL_COOKIE_HEALTH_GATE=fatal to abort on expired."
  }
}

Write-Host "[ARL] windows-agent loop started"
Write-Host "[ARL] project: $ProjectPath"
Write-Host "[ARL] venv: $venvPython"
Write-Host "[ARL] install mode: $installMode"
Write-Host "[ARL] room: $RoomUrl"
Write-Host "[ARL] streamer: $StreamerName"
Write-Host "[ARL] interval: ${IntervalSeconds}s"
if ($ProjectPath -like "\\wsl$\*") {
  Write-Warning "Using a UNC WSL path from Windows may be slower; prefer a Windows-local path when possible."
}

while ($true) {
  try {
    & $venvPython -m arl.cli windows-agent --once
  } catch {
    Write-Warning "windows-agent failed: $($_.Exception.Message)"
  }
  Start-Sleep -Seconds $IntervalSeconds
}
