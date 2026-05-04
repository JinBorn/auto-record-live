param(
  [string]$ProjectPath = "",
  [int]$IntervalSeconds = 0
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

# === Resolve interval: param > env > default 5 ===
if ($IntervalSeconds -le 0) {
  if ($env:ARL_RECORDER_INTERVAL_SECONDS) {
    $IntervalSeconds = [int]$env:ARL_RECORDER_INTERVAL_SECONDS
  } else {
    $IntervalSeconds = 5
  }
}

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
# windows-agent-loop.ps1:47-68.
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
$installMode = if ($env:ARL_WIN_INSTALL_MODE) { $env:ARL_WIN_INSTALL_MODE } else { "if-missing" }
$depsReady = Join-Path $ProjectPath ".venv\.deps-ready"

if ($installMode -eq "always" -or -not (Test-Path $depsReady)) {
  & $venvPython -m pip install -e .
  if ($LASTEXITCODE -ne 0) { throw "pip install -e . failed (exit $LASTEXITCODE)" }
  New-Item -ItemType File -Path $depsReady -Force | Out-Null
}

# === Pin CLI-default ARL_RECORDING_ENABLE_FFMPEG before sourcing .env ===
$enableFfmpeg = if ($env:ARL_RECORDING_ENABLE_FFMPEG) { $env:ARL_RECORDING_ENABLE_FFMPEG } else { "1" }
$env:ARL_RECORDING_ENABLE_FFMPEG = $enableFfmpeg

# === Source .env ===
# Bash uses `set -a; source .env; set +a`. UTF-8 explicit because zh-CN
# Windows Get-Content default codepage mangles non-ASCII values.
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

Write-Host "[ARL] recorder loop started"
Write-Host "[ARL] project: $ProjectPath"
Write-Host "[ARL] interval: ${IntervalSeconds}s"
Write-Host "[ARL] install mode: $installMode"
Write-Host "[ARL] venv: $venvPython"
Write-Host "[ARL] ARL_RECORDING_ENABLE_FFMPEG=$($env:ARL_RECORDING_ENABLE_FFMPEG)"
if ($ProjectPath -like "\\wsl$\*") {
  Write-Warning "Using a UNC WSL path from Windows may be slower; prefer a Windows-local path when possible."
}

# Recorder is single-pass per call: RecorderService.run() processes existing
# recording jobs once and exits (see src/arl/recorder/service.py — no internal
# while loop, by design and matching README "执行一次录制" semantics). This
# launcher loop drives the polling cadence and supervises restart on crash.
# Try/catch covers the NativeCommandError-promotion case (recorder writes
# to stderr on failure).
while ($true) {
  try {
    & $venvPython -m arl.cli recorder
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "[ARL][warn] recorder run failed (exit $LASTEXITCODE); continue after sleep"
    }
  } catch {
    Write-Warning "[ARL][warn] recorder threw: $($_.Exception.Message); continue after sleep"
  }
  Start-Sleep -Seconds $IntervalSeconds
}
