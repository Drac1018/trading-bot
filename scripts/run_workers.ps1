[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$allowed = $env:TRADING_MVP_ALLOW_LEGACY_WORKERS -in @("1", "true", "TRUE", "True", "yes", "YES", "Yes", "on", "ON", "On")
if (-not $Force -and -not $allowed) {
    throw "scripts\run_workers.ps1 is a legacy helper that starts an additional scheduler loop. Use scripts\run_backend.ps1 for the official runtime, scripts\run_scheduler.ps1 for an explicit standalone scheduler, or rerun with -Force / TRADING_MVP_ALLOW_LEGACY_WORKERS=1."
}

$worker = Start-Job -ScriptBlock { param($Root) Set-Location $Root; .\.venv\Scripts\python.exe workers\worker.py } -ArgumentList $repoRoot
$scheduler = Start-Job -ScriptBlock { param($Root) Set-Location $Root; .\.venv\Scripts\python.exe workers\scheduler.py } -ArgumentList $repoRoot
Write-Host "Started worker job id: $($worker.Id)"
Write-Host "Started scheduler job id: $($scheduler.Id)"
Get-Job
