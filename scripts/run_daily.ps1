# Runs the daily pipeline in Docker. Windows Task Scheduler calls this script.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$log = Join-Path $root ("logs\scheduler_{0:yyyy-MM-dd}.log" -f (Get-Date))

function Write-Log([string]$message) {
    "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $message | Add-Content -Path $log -Encoding utf8
}

function Test-Docker {
    docker info *> $null
    return $LASTEXITCODE -eq 0
}

Write-Log "Starting daily run"

if (-not (Test-Docker)) {
    Write-Log "Docker is not running, starting Docker Desktop"
    Start-Process "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    $deadline = (Get-Date).AddMinutes(5)
    while (-not (Test-Docker)) {
        if ((Get-Date) -gt $deadline) {
            Write-Log "Docker did not start within 5 minutes"
            exit 1
        }
        Start-Sleep -Seconds 5
    }
}

# cmd redirection keeps the log in one encoding; PowerShell 5.1 appends UTF-16.
cmd /c "docker compose up -d --wait >> `"$log`" 2>&1"
if ($LASTEXITCODE -ne 0) {
    Write-Log "Failed to start services"
    exit $LASTEXITCODE
}

cmd /c "docker compose --profile jobs run --rm pipeline >> `"$log`" 2>&1"
$code = $LASTEXITCODE
Write-Log "Pipeline finished with exit code $code"
exit $code
