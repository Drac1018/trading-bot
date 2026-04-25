$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Get-DotEnvValue {
    param(
        [string]$Path,
        [string]$Name
    )

    if (-not (Test-Path $Path)) {
        return $null
    }

    foreach ($line in Get-Content -Path $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }

        $pair = $trimmed -split "=", 2
        if ($pair.Count -ne 2) {
            continue
        }
        if ($pair[0].Trim() -ne $Name) {
            continue
        }

        return $pair[1].Trim()
    }

    return $null
}

function Get-ConfiguredDatabaseUrl {
    if (-not [string]::IsNullOrWhiteSpace($env:DATABASE_URL)) {
        return $env:DATABASE_URL.Trim()
    }

    return Get-DotEnvValue -Path (Join-Path $repoRoot ".env") -Name "DATABASE_URL"
}

function Test-ExplicitDatabaseConfiguration {
    $databaseUrl = Get-ConfiguredDatabaseUrl
    if ([string]::IsNullOrWhiteSpace($databaseUrl)) {
        throw "DATABASE_URL is required for worker startup. This script no longer falls back to SQLite. Set a PostgreSQL URL, or set DATABASE_URL to sqlite://... together with TRADING_MVP_ALLOW_SQLITE=1 for explicit local/dev use."
    }

    if ($databaseUrl -like "sqlite*") {
        $allowSqlite = $env:TRADING_MVP_ALLOW_SQLITE
        if ([string]::IsNullOrWhiteSpace($allowSqlite)) {
            $allowSqlite = Get-DotEnvValue -Path (Join-Path $repoRoot ".env") -Name "TRADING_MVP_ALLOW_SQLITE"
        }
        if ($allowSqlite -notin @("1", "true", "TRUE", "True", "yes", "YES", "Yes", "on", "ON", "On")) {
            throw "DATABASE_URL points to SQLite. Worker startup requires explicit opt-in for SQLite. Re-run with TRADING_MVP_ALLOW_SQLITE=1 only for local/dev use."
        }

        Write-Warning "DATABASE_URL points to SQLite. Continuing only because TRADING_MVP_ALLOW_SQLITE is set."
    }

    return $databaseUrl
}

function Start-LocalPostgresqlIfConfigured {
    param([string]$DatabaseUrl)

    if ($DatabaseUrl -notlike "postgresql*") {
        return
    }
    if ($DatabaseUrl -notmatch "(127\.0\.0\.1|localhost)(:5432)?") {
        return
    }

    $startScript = Join-Path $repoRoot "scripts\start_postgresql_local.ps1"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startScript
    if ($LASTEXITCODE -ne 0) {
        throw "Local PostgreSQL startup check failed."
    }
}

function Invoke-CheckedPython {
    param([string[]]$Arguments)

    & .\.venv\Scripts\python.exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed: $($Arguments -join ' ')"
    }
}

$databaseUrl = Test-ExplicitDatabaseConfiguration
Start-LocalPostgresqlIfConfigured -DatabaseUrl $databaseUrl
Invoke-CheckedPython -Arguments @("workers\worker.py")
