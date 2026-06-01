param(
  [ValidateSet("Install", "Uninstall", "Status")]
  [string]$Action = "Status",
  [string]$ProjectPath = "",
  [string]$TaskName = "AutoRecordLive",
  [ValidateSet("AtLogOn", "AtStartup")]
  [string]$TriggerMode = "AtLogOn",
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

$supervisorPath = Join-Path $ProjectPath "scripts\windows-supervisor.ps1"
if (!(Test-Path $supervisorPath)) {
  throw "Supervisor script not found: $supervisorPath"
}

function Get-TaskOrNull {
  param([string]$Name)
  try {
    return Get-ScheduledTask -TaskName $Name -ErrorAction Stop
  } catch {
    return $null
  }
}

if ($Action -eq "Status") {
  $task = Get-TaskOrNull -Name $TaskName
  if ($null -eq $task) {
    Write-Host "[ARL] autostart status: disabled task=$TaskName"
    exit 0
  }
  Write-Host "[ARL] autostart status: enabled task=$TaskName state=$($task.State)"
  exit 0
}

if ($Action -eq "Uninstall") {
  $task = Get-TaskOrNull -Name $TaskName
  if ($null -eq $task) {
    Write-Host "[ARL] autostart already disabled task=$TaskName"
    exit 0
  }
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "[ARL] autostart disabled task=$TaskName"
  exit 0
}

$arguments = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", "`"$supervisorPath`"",
  "-ProjectPath", "`"$ProjectPath`""
)
if ($RestartDelaySeconds -gt 0) {
  $arguments += @("-RestartDelaySeconds", $RestartDelaySeconds)
}

$taskAction = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument ($arguments -join " ") `
  -WorkingDirectory $ProjectPath
if ($TriggerMode -eq "AtStartup") {
  $trigger = New-ScheduledTaskTrigger -AtStartup
} else {
  $trigger = New-ScheduledTaskTrigger -AtLogOn
}
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $taskAction `
  -Trigger $trigger `
  -Settings $settings `
  -Description "Auto Record Live supervisor. Installed only when explicitly enabled." `
  -Force | Out-Null

Write-Host "[ARL] autostart enabled task=$TaskName"
Write-Host "[ARL] trigger: $TriggerMode"
Write-Host "[ARL] supervisor: $supervisorPath"
