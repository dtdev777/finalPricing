# Registers the daily Windows Task Scheduler job for the current user.
param(
    [string]$TaskName = "finalPricing-daily",
    [string]$VietnamTime = "07:00",
    # Last day the job runs (yyyy-MM-dd). The task expires after that day.
    [string]$EndDate = "2026-12-08"
)

$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot "run_daily.ps1"

# Task Scheduler uses the machine's clock, so convert from Vietnam time.
# Re-run this script if the machine's time zone or daylight saving changes.
$vietnam = [TimeZoneInfo]::FindSystemTimeZoneById("SE Asia Standard Time")
$timeOfDay = [datetime]::ParseExact($VietnamTime, "HH:mm", $null).TimeOfDay
$vietnamStart = [datetime]::SpecifyKind((Get-Date).Date.Add($timeOfDay), "Unspecified")
$localStart = [TimeZoneInfo]::ConvertTime($vietnamStart, $vietnam, [TimeZoneInfo]::Local)

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $localStart
$lastDay = [datetime]::ParseExact($EndDate, "yyyy-MM-dd", $null)
$trigger.EndBoundary = $lastDay.AddDays(1).AddSeconds(-1).ToString("s")
# StartWhenAvailable runs a missed job as soon as the laptop is back on.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
# Docker Desktop runs in the user session, so the task needs an interactive logon.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Output ("Registered {0}: daily at {1} Vietnam time ({2:HH:mm} on this machine), last run on {3}" -f $TaskName, $VietnamTime, $localStart, $EndDate)
