[CmdletBinding()]
param(
    [switch]$PreflightOnly,
    [switch]$PauseOnExit
)

$ErrorActionPreference = "Stop"
$script:RuntimeSwitchFailed = $false
trap {
    $script:RuntimeSwitchFailed = $true
    Write-Host ""
    Write-Host "Service runtime switch failed:"
    Write-Host $_
    try {
        Stop-Transcript | Out-Null
    } catch {
    }
    if ($PauseOnExit) {
        Read-Host "Press Enter to close"
    }
    exit 1
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot
$runtimeSwitchLogDir = Join-Path $repoRoot ".logs\service-runtime-switch"
New-Item -ItemType Directory -Path $runtimeSwitchLogDir -Force | Out-Null
$runtimeSwitchLog = Join-Path $runtimeSwitchLogDir ("runtime-switch-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))
try {
    Start-Transcript -Path $runtimeSwitchLog -Force | Out-Null
    Write-Host "Runtime switch log: $runtimeSwitchLog"
} catch {
    Write-Warning "Failed to start transcript: $($_.Exception.Message)"
}

function Test-IsAdministrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-ServiceGateCheck {
    $raw = & ".\.venv\Scripts\python.exe" "scripts\check_service_gate.py"
    if ($LASTEXITCODE -notin @(0, 2)) {
        throw "service gate check failed with exit code $LASTEXITCODE"
    }
    return $raw | ConvertFrom-Json
}

function Stop-ListeningPorts {
    param(
        [int[]]$Ports,
        [int[]]$ProtectedProcessIds = @()
    )

    $connections = Get-NetTCPConnection -LocalPort $Ports -State Listen -ErrorAction SilentlyContinue
    foreach ($connection in $connections) {
        $owningProcessId = [int]$connection.OwningProcess
        if ($ProtectedProcessIds -contains $owningProcessId) {
            Write-Host "Preserve service-owned listener PID=$owningProcessId port=$($connection.LocalPort)"
            continue
        }
        Write-Host "Stopping listener PID=$owningProcessId port=$($connection.LocalPort)"
        Stop-Process -Id $owningProcessId -Force
    }
}

function Wait-HttpOk {
    param(
        [string]$Uri,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                return $response
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    } while ((Get-Date) -lt $deadline)

    throw "Timed out waiting for $Uri"
}

function Measure-ApiCall {
    param([string]$Uri)
    $result = $null
    $elapsed = Measure-Command {
        $result = Invoke-RestMethod -Uri $Uri -TimeoutSec 30
    }
    [pscustomobject]@{
        uri = $Uri
        elapsed_ms = [math]::Round($elapsed.TotalMilliseconds, 2)
        ok = $null -ne $result
    }
}

$gate = Invoke-ServiceGateCheck
$gate | ConvertTo-Json -Depth 8
if (-not $gate.gate_clear) {
    throw "Service gate is blocked: $($gate.blockers -join ', ')"
}

if ($PreflightOnly) {
    Write-Host "Preflight passed. Re-run this script from an Administrator PowerShell without -PreflightOnly to switch services."
    exit 0
}

if (-not (Test-IsAdministrator)) {
    throw "Administrator PowerShell is required to stop listeners and start Windows services."
}

$serviceNames = @("TradingMvpBackend", "TradingMvpFrontend")
$services = Get-CimInstance Win32_Service | Where-Object { $serviceNames -contains $_.Name }
$servicePids = @($services | Where-Object { $_.ProcessId -gt 0 } | Select-Object -ExpandProperty ProcessId)

foreach ($serviceName in $serviceNames) {
    $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($service -and $service.Status -eq "Running") {
        Write-Host "Stopping service $serviceName"
        Stop-Service -Name $serviceName -Force
        $service.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(30))
    }
}

Stop-ListeningPorts -Ports @(8001, 3001) -ProtectedProcessIds @()
Stop-ListeningPorts -Ports @(8000, 3000) -ProtectedProcessIds $servicePids

Write-Host "Starting TradingMvpBackend"
Start-Service TradingMvpBackend
(Get-Service TradingMvpBackend).WaitForStatus("Running", [TimeSpan]::FromSeconds(60))
Wait-HttpOk -Uri "http://127.0.0.1:8000/health" -TimeoutSeconds 90 | Out-Null

Write-Host "Starting TradingMvpFrontend"
Start-Service TradingMvpFrontend
(Get-Service TradingMvpFrontend).WaitForStatus("Running", [TimeSpan]::FromSeconds(60))
Wait-HttpOk -Uri "http://127.0.0.1:3000/" -TimeoutSeconds 90 | Out-Null

$health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 15
$serviceGate = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/runtime/service-gate" -TimeoutSec 30
$aiUsageFirst = Measure-ApiCall -Uri "http://127.0.0.1:8000/api/settings/ai-usage"
$aiUsageSecond = Measure-ApiCall -Uri "http://127.0.0.1:8000/api/settings/ai-usage"
$listeners = Get-NetTCPConnection -LocalPort 8000,3000,8001,3001 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, OwningProcess, State

[pscustomobject]@{
    services = Get-Service TradingMvpBackend,TradingMvpFrontend | Select-Object Name,Status,StartType
    health = $health
    service_gate_clear = $serviceGate.gate_clear
    service_gate_blockers = $serviceGate.blockers
    ai_usage_first = $aiUsageFirst
    ai_usage_second = $aiUsageSecond
    listeners = $listeners
} | ConvertTo-Json -Depth 8
try {
    Stop-Transcript | Out-Null
} catch {
    Write-Warning "Failed to stop transcript: $($_.Exception.Message)"
}
if ($PauseOnExit) {
    Read-Host "Press Enter to close"
}
