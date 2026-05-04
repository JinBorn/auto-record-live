param(
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

# === Python bootstrap (mirrors windows-agent-loop.ps1:24-33) ===
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

# === venv (single shared .venv across all three windows-*-loop.ps1 launchers) ===
$venvPython = Join-Path $ProjectPath ".venv\Scripts\python.exe"
if (!(Test-Path $venvPython)) {
  & $bootstrapPython @bootstrapArgs -m venv .venv
}
if (!(Test-Path $venvPython)) {
  throw "Failed to create virtual environment at $venvPython"
}

# === pip availability probe with NativeCommandError-resilient try/catch ===
# See .trellis/spec/backend/launcher-conventions.md "Common Mistake:
# $ErrorActionPreference = Stop DOES promote native-exe stderr". Mirrors
# windows-agent-loop.ps1:47-68 verbatim — the try/catch is mandatory because
# `*> $null` does not swallow stderr before Stop promotes it to a terminating
# error.
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

# === install mode + sentinel (mirrors windows-agent-loop.ps1:70-79) ===
# ARL_WIN_INSTALL_MODE=if-missing (default) skips `pip install -e .` when the
# .deps-ready sentinel is present; =always forces reinstall every run.
# Sentinel is touched only after a successful install (LASTEXITCODE == 0) per
# launcher-conventions.md — a failed install MUST NOT leave a sentinel.
$installMode = if ($env:ARL_WIN_INSTALL_MODE) { $env:ARL_WIN_INSTALL_MODE } else { "if-missing" }
$depsReady = Join-Path $ProjectPath ".venv\.deps-ready"

if ($installMode -eq "always" -or -not (Test-Path $depsReady)) {
  & $venvPython -m pip install -e .
  if ($LASTEXITCODE -ne 0) { throw "pip install -e . failed (exit $LASTEXITCODE)" }
  New-Item -ItemType File -Path $depsReady -Force | Out-Null
}

# === Pin CLI-default ARL_RECORDING_ENABLE_FFMPEG before sourcing .env ===
# Mirrors wsl-orchestrator.sh:33-41 semantic: any pre-set ARL_RECORDING_ENABLE_FFMPEG
# (CLI / parent shell) is authoritative even after .env supplies a default.
$enableFfmpeg = if ($env:ARL_RECORDING_ENABLE_FFMPEG) { $env:ARL_RECORDING_ENABLE_FFMPEG } else { "1" }
$env:ARL_RECORDING_ENABLE_FFMPEG = $enableFfmpeg

# === Source .env ===
# Bash uses `set -a; source .env; set +a`. PowerShell has no built-in
# equivalent; parse line-by-line. Read as UTF-8 explicitly because zh-CN
# Windows defaults Get-Content to GBK, which mangles ARL_STREAMER_NAME etc.
$envFile = Join-Path $ProjectPath ".env"
if (Test-Path $envFile) {
  Get-Content $envFile -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if ([string]::IsNullOrEmpty($line) -or $line.StartsWith("#")) { return }
    $eqIdx = $line.IndexOf("=")
    if ($eqIdx -lt 1) { return }
    $key = $line.Substring(0, $eqIdx).Trim()
    $value = $line.Substring($eqIdx + 1).Trim()
    if ($value.Length -ge 2) {
      $first = $value[0]
      $last = $value[$value.Length - 1]
      if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
        $value = $value.Substring(1, $value.Length - 2)
      }
    }
    Set-Item -Path "Env:$key" -Value $value
  }
}

# Re-pin so an externally-set ARL_RECORDING_ENABLE_FFMPEG wins over .env default.
$env:ARL_RECORDING_ENABLE_FFMPEG = $enableFfmpeg

Write-Host "[ARL] orchestrator loop started"
Write-Host "[ARL] project: $ProjectPath"
Write-Host "[ARL] install mode: $installMode"
Write-Host "[ARL] venv: $venvPython"
Write-Host "[ARL] ARL_RECORDING_ENABLE_FFMPEG=$($env:ARL_RECORDING_ENABLE_FFMPEG)"
if ($ProjectPath -like "\\wsl$\*") {
  Write-Warning "Using a UNC WSL path from Windows may be slower; prefer a Windows-local path when possible."
}

# Orchestrator has its own internal poll loop driven by
# ARL_ORCHESTRATOR_POLL_INTERVAL_SECONDS. This script does NOT wrap the call
# in `while ($true)` — wsl-orchestrator.sh:49 uses `exec` for the same reason.
# When the Python process exits, this script exits with the same status.
& $venvPython -m arl.cli orchestrator
exit $LASTEXITCODE
