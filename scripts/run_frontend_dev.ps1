param(
    [int]$Port = 3000,
    [string]$ApiBaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location (Join-Path $repoRoot "frontend")

$env:API_BASE_URL = $ApiBaseUrl
$env:NEXT_PUBLIC_API_BASE_URL = $ApiBaseUrl

pnpm exec next dev --hostname 127.0.0.1 --port $Port
