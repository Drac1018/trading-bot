$ErrorActionPreference = "Stop"
$worker = Start-Job -ScriptBlock { Set-Location "C:\my-trading-bot"; .\.venv\Scripts\python.exe workers\worker.py }
$scheduler = Start-Job -ScriptBlock { Set-Location "C:\my-trading-bot"; .\.venv\Scripts\python.exe workers\scheduler.py }
Write-Host "Started worker job id: $($worker.Id)"
Write-Host "Started scheduler job id: $($scheduler.Id)"
Get-Job

