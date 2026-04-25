param(
    [string]$SourceSqlitePath = "data/trading_mvp.db",
    [string]$TargetDatabaseUrl,
    [int]$BatchSize = 500,
    [switch]$Apply,
    [switch]$PrepareTargetSchema,
    [switch]$SnapshotSource,
    [switch]$SourceOnlyPreflight,
    [switch]$AllowUnpausedSource,
    [switch]$AllowNonemptyTarget,
    [switch]$AllowColumnMismatch
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"

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

function Resolve-TargetDatabaseUrl {
    param([string]$ExplicitUrl)

    if (-not [string]::IsNullOrWhiteSpace($ExplicitUrl)) {
        return $ExplicitUrl.Trim()
    }

    if (-not [string]::IsNullOrWhiteSpace($env:DATABASE_URL)) {
        return $env:DATABASE_URL.Trim()
    }

    return Get-DotEnvValue -Path (Join-Path $repoRoot ".env") -Name "DATABASE_URL"
}

function Get-SourceSafetyState {
    param([string]$Path)

    $env:TRADING_MVP_SOURCE_SQLITE_PATH = $Path
    try {
        $output = @'
import json
import os
import sqlite3

SYSTEM_TABLES = {"alembic_version"}
PRIORITY_TABLES = (
    "users",
    "settings",
    "market_snapshots",
    "feature_snapshots",
    "agent_runs",
    "risk_checks",
    "pending_entry_plans",
    "positions",
    "orders",
    "executions",
    "pnl_snapshots",
    "account_ledger_entries",
    "skipped_trade_events",
    "alerts",
    "scheduler_runs",
    "competitor_notes",
    "ui_feedback",
    "system_health_events",
    "audit_events",
)

path = os.environ["TRADING_MVP_SOURCE_SQLITE_PATH"]
connection = sqlite3.connect(path)
try:
    cursor = connection.cursor()
    source_tables = {
        row[0]
        for row in cursor.execute("select name from sqlite_master where type='table'").fetchall()
    } - SYSTEM_TABLES
    ordered_tables = [table for table in PRIORITY_TABLES if table in source_tables]
    ordered_tables.extend(sorted(source_tables - set(ordered_tables)))
    table_counts = []
    for table_name in ordered_tables:
        quoted_table_name = '"' + table_name.replace('"', '""') + '"'
        row_count = cursor.execute(f"select count(*) from {quoted_table_name}").fetchone()[0]
        table_counts.append({"table": table_name, "rows": int(row_count)})
    has_settings = cursor.execute(
        "select 1 from sqlite_master where type='table' and name='settings'"
    ).fetchone() is not None
    payload = {
        "has_settings_table": has_settings,
        "has_settings_row": False,
        "trading_paused": None,
        "live_execution_armed": None,
        "table_counts": table_counts,
        "total_rows": sum(item["rows"] for item in table_counts),
    }
    if has_settings:
        row = cursor.execute(
            "select trading_paused, live_execution_armed from settings limit 1"
        ).fetchone()
        if row is not None:
            payload["has_settings_row"] = True
            payload["trading_paused"] = bool(row[0])
            payload["live_execution_armed"] = bool(row[1])
    print(json.dumps(payload))
finally:
    connection.close()
'@ | & $pythonExe -
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to inspect source SQLite safety state."
        }
        return $output | ConvertFrom-Json
    }
    finally {
        Remove-Item Env:TRADING_MVP_SOURCE_SQLITE_PATH -ErrorAction SilentlyContinue
    }
}

function Test-TargetDatabaseConnectivity {
    param([string]$DatabaseUrl)

    $env:TRADING_MVP_TARGET_DATABASE_URL = $DatabaseUrl
    try {
        @'
import os
import socket

from sqlalchemy.engine.url import make_url

database_url = os.environ["TRADING_MVP_TARGET_DATABASE_URL"]
url = make_url(database_url)
if not url.drivername.startswith("postgresql"):
    raise RuntimeError("Target database URL must point to PostgreSQL.")

host = url.host or "127.0.0.1"
port = url.port or 5432
with socket.create_connection((host, port), timeout=5):
    pass
'@ | & $pythonExe -
        if ($LASTEXITCODE -ne 0) {
            throw "Target PostgreSQL connectivity check failed."
        }
    }
    finally {
        Remove-Item Env:TRADING_MVP_TARGET_DATABASE_URL -ErrorAction SilentlyContinue
    }
}

function New-SourceSnapshot {
    param(
        [string]$SourcePath,
        [string]$DestinationDirectory
    )

    New-Item -ItemType Directory -Path $DestinationDirectory -Force | Out-Null
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $snapshotPath = Join-Path $DestinationDirectory ("trading_mvp_{0}.db" -f $timestamp)

    $env:TRADING_MVP_SOURCE_SQLITE_PATH = $SourcePath
    $env:TRADING_MVP_SNAPSHOT_SQLITE_PATH = $snapshotPath
    try {
        @'
import os
import sqlite3

source_path = os.environ["TRADING_MVP_SOURCE_SQLITE_PATH"]
snapshot_path = os.environ["TRADING_MVP_SNAPSHOT_SQLITE_PATH"]

source_connection = sqlite3.connect(source_path)
snapshot_connection = sqlite3.connect(snapshot_path)
try:
    source_connection.backup(snapshot_connection)
finally:
    snapshot_connection.close()
    source_connection.close()
'@ | & $pythonExe -
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create SQLite source snapshot."
        }
    }
    finally {
        Remove-Item Env:TRADING_MVP_SOURCE_SQLITE_PATH -ErrorAction SilentlyContinue
        Remove-Item Env:TRADING_MVP_SNAPSHOT_SQLITE_PATH -ErrorAction SilentlyContinue
    }

    return $snapshotPath
}

function Invoke-TargetSchemaPreparation {
    param([string]$DatabaseUrl)

    $previousDatabaseUrl = $env:DATABASE_URL
    $hadDatabaseUrl = Test-Path Env:DATABASE_URL
    try {
        $env:DATABASE_URL = $DatabaseUrl
        & $pythonExe -m trading_mvp.migrate
        if ($LASTEXITCODE -ne 0) {
            throw "Target schema preparation failed."
        }
    }
    finally {
        if ($hadDatabaseUrl) {
            $env:DATABASE_URL = $previousDatabaseUrl
        }
        else {
            Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
        }
    }
}

function Invoke-OneShotCopy {
    param(
        [string]$SourceUrl,
        [string]$TargetUrl
    )

    $commandArgs = @(
        "-m", "trading_mvp.sqlite_to_postgresql_copy",
        "--source-url", $SourceUrl,
        "--target-url", $TargetUrl,
        "--batch-size", $BatchSize
    )

    if ($Apply) {
        $commandArgs += "--apply"
    }
    if ($AllowUnpausedSource) {
        $commandArgs += "--allow-unpaused-source"
    }
    if ($AllowNonemptyTarget) {
        $commandArgs += "--allow-nonempty-target"
    }
    if ($AllowColumnMismatch) {
        $commandArgs += "--allow-column-mismatch"
    }

    & $pythonExe @commandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "SQLite -> PostgreSQL one-shot copy command failed."
    }
}

if (-not (Test-Path $pythonExe)) {
    throw ".venv Python runtime is required: $pythonExe"
}

if ($Apply -and ($AllowUnpausedSource -or $AllowNonemptyTarget -or $AllowColumnMismatch)) {
    throw "-Apply requires a paused source, empty target, and aligned columns. Do not combine -Apply with override flags."
}

$resolvedSourcePath = (Resolve-Path $SourceSqlitePath).Path
$sourceState = Get-SourceSafetyState -Path $resolvedSourcePath
if (-not $sourceState.has_settings_table) {
    throw "Source SQLite database is missing the settings table: $resolvedSourcePath"
}
if (-not $sourceState.has_settings_row) {
    throw "Source SQLite database has no settings row: $resolvedSourcePath"
}

Write-Host ("Source settings safety state: trading_paused={0} live_execution_armed={1}" -f $sourceState.trading_paused, $sourceState.live_execution_armed)
$sourceTableCounts = @($sourceState.table_counts)
Write-Host ("Source table row counts: tables={0} total_rows={1}" -f $sourceTableCounts.Count, $sourceState.total_rows)
foreach ($entry in $sourceTableCounts) {
    Write-Host ("  - {0}: {1}" -f $entry.table, $entry.rows)
}

if (-not $AllowUnpausedSource) {
    if (-not $sourceState.trading_paused) {
        throw "Source SQLite is not paused. Pause trading before one-shot copy, or re-run with -AllowUnpausedSource only for a manual rehearsal."
    }
    if ($sourceState.live_execution_armed) {
        throw "Source SQLite still has live execution armed. Disarm live execution before one-shot copy, or re-run with -AllowUnpausedSource only for a manual rehearsal."
    }
}

if ($SourceOnlyPreflight) {
    Write-Host "Source-only preflight complete."
    return
}

$resolvedTargetDatabaseUrl = Resolve-TargetDatabaseUrl -ExplicitUrl $TargetDatabaseUrl
if ([string]::IsNullOrWhiteSpace($resolvedTargetDatabaseUrl)) {
    throw "Target PostgreSQL URL is required. Pass -TargetDatabaseUrl or set DATABASE_URL in the environment/.env."
}

Write-Host "Testing target PostgreSQL connectivity."
Test-TargetDatabaseConnectivity -DatabaseUrl $resolvedTargetDatabaseUrl

if ($PrepareTargetSchema) {
    Write-Host "Preparing target schema with trading_mvp.migrate."
    Invoke-TargetSchemaPreparation -DatabaseUrl $resolvedTargetDatabaseUrl
}

$effectiveSourcePath = $resolvedSourcePath
if ($SnapshotSource -or $Apply) {
    $snapshotDirectory = Join-Path $repoRoot "data\migration_snapshots"
    $effectiveSourcePath = New-SourceSnapshot -SourcePath $resolvedSourcePath -DestinationDirectory $snapshotDirectory
    Write-Host "Created source snapshot: $effectiveSourcePath"
}

$sourceUrl = "sqlite:///$($effectiveSourcePath.Replace('\', '/'))"
Write-Host ("Running SQLite -> PostgreSQL copy ({0})." -f $(if ($Apply) { "apply" } else { "dry-run" }))
Invoke-OneShotCopy -SourceUrl $sourceUrl -TargetUrl $resolvedTargetDatabaseUrl
