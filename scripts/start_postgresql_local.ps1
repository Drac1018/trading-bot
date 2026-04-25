param(
    [int]$TimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pgBin = Join-Path $repoRoot ".tools\postgresql\pgsql-16.13\pgsql\bin"
$pgCtl = Join-Path $pgBin "pg_ctl.exe"
$pgIsReady = Join-Path $pgBin "pg_isready.exe"
$pgData = Join-Path $repoRoot "data\postgres16"
$pgLogDir = Join-Path $repoRoot ".logs\postgresql"
$pgLog = Join-Path $pgLogDir "postgres16.log"

function Test-PostgresqlReady {
    & $pgIsReady -h "127.0.0.1" -p "5432" -U "trading" *> $null
    return $LASTEXITCODE -eq 0
}

if (-not (Test-Path $pgIsReady)) {
    throw "Local PostgreSQL binary is missing: $pgIsReady"
}

if (Test-PostgresqlReady) {
    Write-Host "Local PostgreSQL is already accepting connections on 127.0.0.1:5432."
    exit 0
}

if (-not (Test-Path $pgCtl)) {
    throw "Local PostgreSQL control binary is missing: $pgCtl"
}

if (-not (Test-Path $pgData)) {
    throw "Local PostgreSQL data directory is missing: $pgData"
}

New-Item -ItemType Directory -Path $pgLogDir -Force | Out-Null

Write-Host "Starting local PostgreSQL from $pgData."
& $pgCtl -D $pgData -l $pgLog -o "-h 127.0.0.1 -p 5432" start
if ($LASTEXITCODE -ne 0) {
    throw "pg_ctl failed to start local PostgreSQL. See log: $pgLog"
}

$deadline = (Get-Date).AddSeconds([Math]::Max($TimeoutSeconds, 1))
while ((Get-Date) -lt $deadline) {
    if (Test-PostgresqlReady) {
        Write-Host "Local PostgreSQL is accepting connections on 127.0.0.1:5432."
        exit 0
    }
    Start-Sleep -Seconds 1
}

throw "Local PostgreSQL did not become ready within $TimeoutSeconds seconds. See log: $pgLog"
