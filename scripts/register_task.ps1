# Registers the daily Windows Task Scheduler job for the current user.
param(
    [string]$TaskName = "finalPricing-daily",
    [string]$At = "07:00"
)

$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot "run_daily.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $At
# StartWhenAvailable runs a missed job as soon as the laptop is back on.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
# Docker Desktop runs in the user session, so the task needs an interactive logon.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Output "Registered $TaskName, daily at $At"
