[CmdletBinding()]
param(
    [switch]$IncludeScheduler,
    [switch]$AllowSqliteDatabase
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$serviceRoot = Join-Path $repoRoot ".services"
$winswRoot = Join-Path $repoRoot ".tools\\winsw"

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

function Test-ServiceDatabaseConfiguration {
    $envPath = Join-Path $repoRoot ".env"
    $databaseUrl = Get-DotEnvValue -Path $envPath -Name "DATABASE_URL"

    if ([string]::IsNullOrWhiteSpace($databaseUrl)) {
        throw "DATABASE_URL is not set in $envPath. Windows services would silently fall back to SQLite via trading_mvp.config. Set a PostgreSQL URL before installing services."
    }

    if ($databaseUrl -like "sqlite*") {
        if (-not $AllowSqliteDatabase) {
            throw "DATABASE_URL in $envPath points to SQLite. Windows service installs must use PostgreSQL for operational use. Re-run with -AllowSqliteDatabase only for explicit local/dev use."
        }

        Write-Warning "DATABASE_URL in $envPath points to SQLite. Continuing only because -AllowSqliteDatabase was supplied."
        return
    }

    if ($databaseUrl -notlike "postgresql*") {
        Write-Warning "DATABASE_URL in $envPath does not look like a PostgreSQL URL: $databaseUrl"
    }
}

function Get-WinSWExecutable {
    New-Item -ItemType Directory -Force -Path $winswRoot | Out-Null
    $target = Join-Path $winswRoot "WinSW-x64.exe"
    if (Test-Path $target) {
        return $target
    }

    $headers = @{ "User-Agent" = "Codex-Trading-MVP" }
    $release = Invoke-RestMethod -Headers $headers -Uri "https://api.github.com/repos/winsw/winsw/releases/latest"
    $asset = $release.assets | Where-Object { $_.name -eq "WinSW-x64.exe" } | Select-Object -First 1
    if (-not $asset) {
        throw "WinSW-x64.exe asset not found from latest release."
    }
    Invoke-WebRequest -Headers $headers -Uri $asset.browser_download_url -OutFile $target
    return $target
}

function Write-ServiceConfig {
    param(
        [hashtable]$Definition,
        [string]$WrapperSource
    )

    New-Item -ItemType Directory -Force -Path $serviceRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $Definition.LogPath | Out-Null

    $exePath = Join-Path $serviceRoot "$($Definition.Id).exe"
    $xmlPath = Join-Path $serviceRoot "$($Definition.Id).xml"
    if (-not (Test-Path $exePath)) {
        Copy-Item $WrapperSource $exePath -Force
    }

    $escapedRepo = $repoRoot -replace "&", "&amp;"
    $escapedLog = $Definition.LogPath -replace "&", "&amp;"
    $escapedScript = (Join-Path $repoRoot $Definition.Script) -replace "&", "&amp;"
    $xml = @"
<service>
  <id>$($Definition.Id)</id>
  <name>$($Definition.Name)</name>
  <description>$($Definition.Description)</description>
  <executable>powershell.exe</executable>
  <arguments>-NoProfile -ExecutionPolicy Bypass -File "$escapedScript"</arguments>
  <workingdirectory>$escapedRepo</workingdirectory>
  <stoptimeout>15 sec</stoptimeout>
  <resetfailure>1 hour</resetfailure>
  <onfailure action="restart" delay="10 sec" />
  <logpath>$escapedLog</logpath>
  <log mode="roll-by-size-time">
    <sizeThreshold>10240</sizeThreshold>
    <pattern>yyyyMMdd</pattern>
    <keepFiles>8</keepFiles>
  </log>
</service>
"@
    Set-Content -Path $xmlPath -Value $xml -Encoding UTF8
    return $exePath
}

function Get-ServiceInstance {
    param(
        [string]$Name
    )

    return Get-CimInstance Win32_Service -Filter "Name='$Name'" -ErrorAction SilentlyContinue
}

function Wait-ForServiceState {
    param(
        [string]$Name,
        [string]$DesiredState,
        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $service = Get-ServiceInstance -Name $Name
        if ($null -eq $service) {
            return $DesiredState -eq "Deleted"
        }
        if ($service.State -eq $DesiredState) {
            return $true
        }
        Start-Sleep -Seconds 1
    }

    return $false
}

function Remove-ServiceIfPresent {
    param(
        [hashtable]$Definition
    )

    $exePath = Join-Path $serviceRoot "$($Definition.Id).exe"
    $service = Get-ServiceInstance -Name $Definition.Id
    if ($null -eq $service) {
        return
    }

    if ($service.State -ne "Stopped") {
        if (Test-Path $exePath) {
            & $exePath stop | Out-Null
        } else {
            Stop-Service -Name $Definition.Id -ErrorAction SilentlyContinue
        }

        if (-not (Wait-ForServiceState -Name $Definition.Id -DesiredState "Stopped")) {
            throw "Timed out waiting for service $($Definition.Id) to stop."
        }
    }

    if (Test-Path $exePath) {
        & $exePath uninstall | Out-Null
    } else {
        sc.exe delete $Definition.Id | Out-Null
    }

    if (-not (Wait-ForServiceState -Name $Definition.Id -DesiredState "Deleted")) {
        throw "Timed out waiting for service $($Definition.Id) to be removed."
    }
}

$definitions = @(
    @{
        Id = "TradingMvpBackend"
        Name = "Trading MVP Backend"
        Description = "FastAPI backend for the trading MVP"
        Script = "scripts\\run_backend.ps1"
        LogPath = (Join-Path $repoRoot ".logs\\services\\backend")
    },
    @{
        Id = "TradingMvpFrontend"
        Name = "Trading MVP Frontend"
        Description = "Next.js frontend for the trading MVP"
        Script = "scripts\\run_frontend_service.ps1"
        LogPath = (Join-Path $repoRoot ".logs\\services\\frontend")
    },
    @{
        Id = "TradingMvpWorker"
        Name = "Trading MVP Worker"
        Description = "RQ worker for trading jobs"
        Script = "scripts\\run_worker.ps1"
        LogPath = (Join-Path $repoRoot ".logs\\services\\worker")
    }
)

$schedulerDefinition = @{
    Id = "TradingMvpScheduler"
    Name = "Trading MVP Scheduler"
    Description = "Scheduler for interval and review jobs"
    Script = "scripts\\run_scheduler.ps1"
    LogPath = (Join-Path $repoRoot ".logs\\services\\scheduler")
}

if ($IncludeScheduler) {
    $definitions += $schedulerDefinition
} else {
    Remove-ServiceIfPresent -Definition $schedulerDefinition
}

Test-ServiceDatabaseConfiguration
$wrapper = Get-WinSWExecutable

foreach ($definition in $definitions) {
    Remove-ServiceIfPresent -Definition $definition

    $exePath = Write-ServiceConfig -Definition $definition -WrapperSource $wrapper
    & $exePath install
    sc.exe config $definition.Id start= auto | Out-Null
    & $exePath start
}

if ($IncludeScheduler) {
    Write-Host "Windows services installed and started, including TradingMvpScheduler."
} else {
    Write-Host "Windows services installed and started. TradingMvpScheduler is omitted by default because backend owns operational cadence in the current service topology."
}
