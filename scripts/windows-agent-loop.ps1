param(
  [Parameter(Mandatory=$true)]
  [string]$RoomUrl,

  [Parameter(Mandatory=$true)]
  [string]$StreamerName,

  [int]$IntervalSeconds = 15,
  [string]$ProjectPath = "D:\auto-record-live"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (!(Test-Path $ProjectPath)) {
  throw "Project path not found: $ProjectPath"
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

& $venvPython -m pip install -e .
if (!(Test-Path "node_modules")) {
  npm install
}

$env:ARL_DOUYIN_ROOM_URL = $RoomUrl
$env:ARL_STREAMER_NAME = $StreamerName

Write-Host "[ARL] windows-agent loop started"
Write-Host "[ARL] room: $RoomUrl"
Write-Host "[ARL] streamer: $StreamerName"
Write-Host "[ARL] interval: ${IntervalSeconds}s"

while ($true) {
  try {
    & $venvPython -m arl.cli windows-agent --once
  } catch {
    Write-Warning "windows-agent failed: $($_.Exception.Message)"
  }
  Start-Sleep -Seconds $IntervalSeconds
}
