$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$serviceRoot = Join-Path $repoRoot ".services"
$winswRoot = Join-Path $repoRoot ".tools\\winsw"

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
    },
    @{
        Id = "TradingMvpScheduler"
        Name = "Trading MVP Scheduler"
        Description = "Scheduler for interval and review jobs"
        Script = "scripts\\run_scheduler.ps1"
        LogPath = (Join-Path $repoRoot ".logs\\services\\scheduler")
    }
)

$wrapper = Get-WinSWExecutable

foreach ($definition in $definitions) {
    $exePath = Join-Path $serviceRoot "$($definition.Id).exe"
    $service = Get-Service -Name $definition.Id -ErrorAction SilentlyContinue
    if ($service) {
        & $exePath stop | Out-Null
        Start-Sleep -Seconds 2
        & $exePath uninstall | Out-Null
    }

    $exePath = Write-ServiceConfig -Definition $definition -WrapperSource $wrapper
    & $exePath install
    sc.exe config $definition.Id start= auto | Out-Null
    & $exePath start
}

Write-Host "Windows services installed and started."
