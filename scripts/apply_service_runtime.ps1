[CmdletBinding()]
param(
    [switch]$PreflightOnly,
    [switch]$IncludeWorker,
    [switch]$PauseOnExit,
    [switch]$NoOpenDashboard,
    [string]$DashboardUrl = "http://127.0.0.1:3000/dashboard/operations"
)

$ErrorActionPreference = "Stop"
$script:RuntimeSwitchFailed = $false

function Test-LocalPortReachable {
    param([Parameter(Mandatory = $true)][int]$Port)

    $client = [Net.Sockets.TcpClient]::new()
    try {
        $asyncResult = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $asyncResult.AsyncWaitHandle.WaitOne(1000, $false)) {
            return $false
        }
        $client.EndConnect($asyncResult)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Open-OperatorDashboard {
    param([Parameter(Mandatory = $true)][string]$Context)

    if ($NoOpenDashboard) {
        Write-Host "Operator dashboard auto-open skipped by -NoOpenDashboard."
        return
    }
    if (-not (Test-LocalPortReachable -Port 3000)) {
        Write-Warning "Operator dashboard was not opened because http://127.0.0.1:3000 is not reachable."
        return
    }

    try {
        Write-Host "Opening operator dashboard ($Context): $DashboardUrl"
        Start-Process $DashboardUrl | Out-Null
    } catch {
        Write-Warning "Failed to open operator dashboard: $($_.Exception.Message)"
    }
}

trap {
    $script:RuntimeSwitchFailed = $true
    Write-Host ""
    Write-Host "Service runtime switch failed:"
    Write-Host $_
    Open-OperatorDashboard -Context "restart blocked or failed; opening current reachable UI"
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

function Invoke-WinSWServiceCommand {
    param(
        [string]$ServiceName,
        [string]$Command
    )

    $wrapperPath = Join-Path $repoRoot ".services\$ServiceName.exe"
    if (-not (Test-Path $wrapperPath)) {
        throw "WinSW wrapper not found for $ServiceName at $wrapperPath"
    }
    $maxAttempts = 2
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        Write-Host "Running $ServiceName wrapper command: $Command (attempt $attempt/$maxAttempts)"
        & $wrapperPath $Command
        if ($LASTEXITCODE -eq 0) {
            return
        }
        $exitCode = $LASTEXITCODE
        if ($attempt -lt $maxAttempts) {
            Write-Warning "$ServiceName wrapper command '$Command' failed with exit code $exitCode; retrying."
            Start-Sleep -Seconds 5
            continue
        }
        throw "$ServiceName wrapper command '$Command' failed with exit code $exitCode"
    }
}

function Stop-WorkerServiceIfPresent {
    param([switch]$Disable)

    $serviceName = "TradingMvpWorker"
    $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($null -eq $service) {
        return
    }

    Write-Host "Stopping unused $serviceName"
    if (Test-IsAdministrator) {
        if ($service.Status -ne "Stopped") {
            Stop-Service -Name $serviceName -Force
            $service.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(30))
        }
        if ($Disable) {
            Set-Service -Name $serviceName -StartupType Disabled
        }
        return
    }

    try {
        Invoke-WinSWServiceCommand -ServiceName $serviceName -Command "stop"
    } catch {
        Write-Warning "Could not stop $serviceName without Administrator rights: $($_.Exception.Message)"
    }
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

$serviceNames = @("TradingMvpBackend", "TradingMvpFrontend")
if ($IncludeWorker) {
    $serviceNames += "TradingMvpWorker"
} else {
    Stop-WorkerServiceIfPresent -Disable
}

if (-not (Test-IsAdministrator)) {
    Write-Host "Administrator PowerShell is not available; using WinSW wrapper restart fallback."
    foreach ($serviceName in $serviceNames) {
        Invoke-WinSWServiceCommand -ServiceName $serviceName -Command "restart"
    }
} else {
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
}
Wait-HttpOk -Uri "http://127.0.0.1:8000/health" -TimeoutSeconds 90 | Out-Null

if (Test-IsAdministrator) {
    Write-Host "Starting TradingMvpFrontend"
    Start-Service TradingMvpFrontend
    (Get-Service TradingMvpFrontend).WaitForStatus("Running", [TimeSpan]::FromSeconds(60))
    if ($IncludeWorker) {
        Write-Host "Starting TradingMvpWorker"
        Start-Service TradingMvpWorker
        (Get-Service TradingMvpWorker).WaitForStatus("Running", [TimeSpan]::FromSeconds(60))
    }
}
Wait-HttpOk -Uri "http://127.0.0.1:3000/" -TimeoutSeconds 90 | Out-Null

$health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 15
$serviceGate = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/runtime/service-gate" -TimeoutSec 30
$aiUsageFirst = Measure-ApiCall -Uri "http://127.0.0.1:8000/api/settings/ai-usage"
$aiUsageSecond = Measure-ApiCall -Uri "http://127.0.0.1:8000/api/settings/ai-usage"
$listeners = Get-NetTCPConnection -LocalPort 8000,3000,8001,3001 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, OwningProcess, State

[pscustomobject]@{
    services = Get-Service TradingMvpBackend,TradingMvpFrontend,TradingMvpWorker -ErrorAction SilentlyContinue | Select-Object Name,Status,StartType
    health = $health
    service_gate_clear = $serviceGate.gate_clear
    service_gate_blockers = $serviceGate.blockers
    ai_usage_first = $aiUsageFirst
    ai_usage_second = $aiUsageSecond
    listeners = $listeners
} | ConvertTo-Json -Depth 8
Open-OperatorDashboard -Context "runtime switch completed"
try {
    Stop-Transcript | Out-Null
} catch {
    Write-Warning "Failed to stop transcript: $($_.Exception.Message)"
}
if ($PauseOnExit) {
    Read-Host "Press Enter to close"
}
