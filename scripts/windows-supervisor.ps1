param(
  [string]$ProjectPath = "",
  [int]$RestartDelaySeconds = 0
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
  $ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

if (!(Test-Path $ProjectPath)) {
  throw "Project path not found: $ProjectPath"
}

if ($RestartDelaySeconds -le 0) {
  if ($env:ARL_SUPERVISOR_RESTART_DELAY_SECONDS) {
    $RestartDelaySeconds = [int]$env:ARL_SUPERVISOR_RESTART_DELAY_SECONDS
  } else {
    $RestartDelaySeconds = 10
  }
}

$logDir = Join-Path $ProjectPath "data\tmp\launcher-logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

function New-LauncherSpec {
  param(
    [string]$Name,
    [string]$Script,
    [string[]]$ExtraArgs
  )
  return [pscustomobject]@{
    Name = $Name
    Script = Join-Path $ProjectPath $Script
    ExtraArgs = $ExtraArgs
    Process = $null
  }
}

function Start-Launcher {
  param([pscustomobject]$Spec)

  if (!(Test-Path $Spec.Script)) {
    throw "Launcher script not found: $($Spec.Script)"
  }

  $stdout = Join-Path $logDir "$($Spec.Name).out.log"
  $stderr = Join-Path $logDir "$($Spec.Name).err.log"
  $arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $Spec.Script,
    "-ProjectPath", $ProjectPath
  ) + $Spec.ExtraArgs

  Write-Host "[ARL] supervisor starting name=$($Spec.Name)"
  $Spec.Process = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList $arguments `
    -WorkingDirectory $ProjectPath `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru
}

$agentArgs = @()
if ($env:ARL_SUPERVISOR_AGENT_INTERVAL_SECONDS) {
  $agentArgs += @("-IntervalSeconds", $env:ARL_SUPERVISOR_AGENT_INTERVAL_SECONDS)
}
$recorderArgs = @()
if ($env:ARL_RECORDER_INTERVAL_SECONDS) {
  $recorderArgs += @("-IntervalSeconds", $env:ARL_RECORDER_INTERVAL_SECONDS)
}
$postprocessArgs = @()
if ($env:ARL_POSTPROCESS_INTERVAL_SECONDS) {
  $postprocessArgs += @("-IntervalSeconds", $env:ARL_POSTPROCESS_INTERVAL_SECONDS)
}
$recoveryArgs = @()
if ($env:ARL_RECOVERY_INTERVAL_SECONDS) {
  $recoveryArgs += @("-IntervalSeconds", $env:ARL_RECOVERY_INTERVAL_SECONDS)
}

$specs = @(
  (New-LauncherSpec -Name "agent" -Script "scripts\windows-agent-loop.ps1" -ExtraArgs $agentArgs),
  (New-LauncherSpec -Name "orchestrator" -Script "scripts\windows-orchestrator-loop.ps1" -ExtraArgs @()),
  (New-LauncherSpec -Name "recorder" -Script "scripts\windows-recorder-loop.ps1" -ExtraArgs $recorderArgs),
  (New-LauncherSpec -Name "postprocess" -Script "scripts\windows-postprocess-loop.ps1" -ExtraArgs $postprocessArgs),
  (New-LauncherSpec -Name "recovery" -Script "scripts\windows-recovery-loop.ps1" -ExtraArgs $recoveryArgs)
)

Write-Host "[ARL] supervisor started"
Write-Host "[ARL] project: $ProjectPath"
Write-Host "[ARL] restart_delay: ${RestartDelaySeconds}s"
Write-Host "[ARL] logs: $logDir"

foreach ($spec in $specs) {
  Start-Launcher -Spec $spec
}

while ($true) {
  foreach ($spec in $specs) {
    if ($null -eq $spec.Process -or $spec.Process.HasExited) {
      $exitCode = if ($null -eq $spec.Process) { "none" } else { $spec.Process.ExitCode }
      Write-Warning "[ARL] supervisor restarting name=$($spec.Name) exit=$exitCode"
      Start-Sleep -Seconds $RestartDelaySeconds
      Start-Launcher -Spec $spec
    }
  }
  Start-Sleep -Seconds 5
}
