param(
  [Parameter(Mandatory=$true)]
  [string]$RoomUrl,

  [Parameter(Mandatory=$true)]
  [string]$StreamerName,

  [int]$IntervalSeconds = 15,
  [string]$ProjectPath = "D:\auto-record-live"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectPath

if (!(Test-Path ".venv\Scripts\python.exe")) {
  python -m venv .venv
}

& .\.venv\Scripts\python -m pip install -e .
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
    & .\.venv\Scripts\python -m arl.cli windows-agent --once
  } catch {
    Write-Warning "windows-agent failed: $($_.Exception.Message)"
  }
  Start-Sleep -Seconds $IntervalSeconds
}
