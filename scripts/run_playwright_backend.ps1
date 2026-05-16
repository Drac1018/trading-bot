param(
    [int]$Port = 18002
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

if (-not $env:DATABASE_URL) {
    $TempDb = Join-Path ([System.IO.Path]::GetTempPath()) "trading_mvp_playwright_smoke.db"
    $env:DATABASE_URL = "sqlite:///$($TempDb.Replace('\', '/'))"
}

$env:TRADING_MVP_ALLOW_SQLITE = "1"
$env:TRADING_MVP_SERVICE_RUNTIME = "0"
$env:TRADING_MVP_ENABLE_BACKGROUND_SCHEDULER = "0"
$env:TRADING_MVP_ENABLE_BACKGROUND_USER_STREAM = "0"
$env:TRADING_MVP_ENABLE_BACKGROUND_MARKET_STREAM = "0"

& ".\.venv\Scripts\python.exe" -m uvicorn trading_mvp.main:app --host 127.0.0.1 --port $Port
