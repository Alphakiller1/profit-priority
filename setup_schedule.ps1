<#
  Register the profit-priority learning loop with Windows Task Scheduler.

  Replaces the retired SharpDesk-* tasks, which pointed at sharp-money-tracker.

  Cadence: every 3 hours. The reasoning is the grading lag, not a round number --
  learning.py refuses to grade a candidate younger than 30 minutes, and abandons
  one older than 72 hours. A 3-hour beat puts every candidate comfortably inside
  that window while it is still quoted, so candidates get judged rather than
  abandoned. Running hourly would mostly re-record an unchanged board; running
  daily would let quotes vanish before they could be graded, and an abandoned
  candidate teaches nothing.

  Every endpoint used is free (Kalshi + Polymarket public APIs). No metered call
  is in this loop by design: a loop with a per-run cost gets throttled to save
  money and then stops producing the evidence it exists to gather.

  Run from an elevated PowerShell:
      .\setup_schedule.ps1
      .\setup_schedule.ps1 -Remove
#>

param(
  [switch]$Remove,
  [string]$Repo = "C:\Users\chase\profit-priority"
)

$ErrorActionPreference = "Stop"
$taskName = "ProfitPriority-Learning"
$legacy   = @("SharpDesk-Capture-Free", "SharpDesk-Capture-Odds")

function Remove-IfExists($name) {
  if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Output "removed $name"
  }
}

# The old tasks point at a repo that no longer exists on GitHub. Leaving them
# would keep writing to a dead tree and look like the loop was running.
foreach ($n in $legacy) { Remove-IfExists $n }

if ($Remove) {
  Remove-IfExists $taskName
  Write-Output "schedule removed."
  return
}

$python = (Get-Command python).Source
if (-not (Test-Path $Repo)) { throw "repo not found: $Repo" }
Write-Output "python: $python"
Write-Output "repo:   $Repo"

Remove-IfExists $taskName

$action = New-ScheduledTaskAction -Execute $python `
  -Argument "-m profit_priority.capture" -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours(1) `
  -RepetitionInterval (New-TimeSpan -Hours 3)
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
  -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action `
  -Trigger $trigger -Settings $settings `
  -Description "profit-priority: grade, mark positions, record, export dashboard, publish docs/ (free endpoints only)" | Out-Null

Write-Output "registered $taskName  (every 3h)"
Write-Output ""
Write-Output "verify : Get-ScheduledTask ProfitPriority-*"
Write-Output "run now: Start-ScheduledTask -TaskName $taskName"
Write-Output "report : python -m profit_priority.learning report"
Write-Output "log    : $Repo\data\capture.log"
