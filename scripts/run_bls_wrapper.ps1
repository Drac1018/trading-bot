param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8091,
    [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw "Python virtual environment not found at .venv\Scripts\python.exe"
}

if ($ConfigPath) {
    $env:TRADING_BLS_WRAPPER_CONFIG = $ConfigPath
}

Write-Host "Starting BLS wrapper on http://$BindHost`:$Port" -ForegroundColor Yellow
if ($env:BLS_API_KEY) {
    Write-Host "BLS_API_KEY detected. Registered mode can be used." -ForegroundColor Green
}
else {
    Write-Host "BLS_API_KEY is not set. Public mode only." -ForegroundColor DarkYellow
}

.\.venv\Scripts\python.exe -m uvicorn trading_mvp.bls_wrapper_app:app --app-dir backend --host $BindHost --port $Port
