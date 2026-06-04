param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$env:TRADING_MVP_SERVICE_RUNTIME = "0"
$env:TRADING_MVP_ENABLE_BACKGROUND_SCHEDULER = "0"
$env:TRADING_MVP_ENABLE_BACKGROUND_USER_STREAM = "0"
$env:TRADING_MVP_ENABLE_BACKGROUND_MARKET_STREAM = "0"

& ".\.venv\Scripts\python.exe" -m uvicorn trading_mvp.main:app --app-dir backend --host 127.0.0.1 --port $Port
