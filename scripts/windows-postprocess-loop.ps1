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

if ($IntervalSeconds -le 0) {
  if ($env:ARL_POSTPROCESS_INTERVAL_SECONDS) {
    $IntervalSeconds = [int]$env:ARL_POSTPROCESS_INTERVAL_SECONDS
  } else {
    $IntervalSeconds = 30
  }
}

# Bootstrap mirrors scripts/windows-recorder-loop.ps1 so all long-running
# launchers share the same venv, install mode, and dependency sentinel.
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

$installMode = if ($env:ARL_WIN_INSTALL_MODE) { $env:ARL_WIN_INSTALL_MODE } else { "if-missing" }
$depsReady = Join-Path $ProjectPath ".venv\.deps-ready"
$installSpec = "pip install -e .[subtitles]"
$depsReadySpec = if (Test-Path $depsReady) { (Get-Content $depsReady -Raw -Encoding UTF8).Trim() } else { "" }

if ($installMode -eq "always" -or $depsReadySpec -ne $installSpec) {
  & $venvPython -m pip install -e ".[subtitles]"
  if ($LASTEXITCODE -ne 0) { throw "$installSpec failed (exit $LASTEXITCODE)" }
  Set-Content -Path $depsReady -Value $installSpec -Encoding UTF8
}

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

Write-Host "[ARL] postprocess loop started"
Write-Host "[ARL] project: $ProjectPath"
Write-Host "[ARL] interval: ${IntervalSeconds}s"
Write-Host "[ARL] install mode: $installMode"
Write-Host "[ARL] venv: $venvPython"
if ($ProjectPath -like "\\wsl$\*") {
  Write-Warning "Using a UNC WSL path from Windows may be slower; prefer a Windows-local path when possible."
}

while ($true) {
  try {
    & $venvPython -m arl.cli postprocess --once
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "[ARL][warn] postprocess run failed (exit $LASTEXITCODE); continue after sleep"
    }
  } catch {
    Write-Warning "[ARL][warn] postprocess threw: $($_.Exception.Message); continue after sleep"
  }
  Start-Sleep -Seconds $IntervalSeconds
}

