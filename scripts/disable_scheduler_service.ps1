[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$serviceName = "TradingMvpScheduler"
$backendServiceName = "TradingMvpBackend"
$wrapperPath = Join-Path $repoRoot ".services\$serviceName.exe"
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

if (-not $isAdmin) {
    throw "Run this script from an elevated PowerShell session."
}

function Get-ServiceOrNull {
    param(
        [string]$Name
    )

    return Get-Service -Name $Name -ErrorAction SilentlyContinue
}

function Get-ServiceStartMode {
    param(
        [string]$Name
    )

    $service = Get-CimInstance Win32_Service -Filter "Name='$Name'" -ErrorAction SilentlyContinue
    if ($null -eq $service) {
        return $null
    }
    return $service.StartMode
}

function Stop-SchedulerService {
    param(
        [string]$Name,
        [string]$WinSWPath
    )

    $service = Get-ServiceOrNull -Name $Name
    if ($null -eq $service -or $service.Status -eq "Stopped") {
        return
    }

    if (Test-Path $WinSWPath) {
        if ($PSCmdlet.ShouldProcess($Name, "stop via WinSW wrapper")) {
            & $WinSWPath stop | Out-Null
            Start-Sleep -Seconds 2
        }
        return
    }

    if ($PSCmdlet.ShouldProcess($Name, "stop service")) {
        Stop-Service -Name $Name -ErrorAction Stop
    }
}

$schedulerService = Get-ServiceOrNull -Name $serviceName
if ($null -eq $schedulerService -and -not (Test-Path $wrapperPath)) {
    Write-Host "$serviceName is not installed. Nothing to disable."
    exit 0
}

Stop-SchedulerService -Name $serviceName -WinSWPath $wrapperPath

$schedulerService = Get-ServiceOrNull -Name $serviceName
if ($null -ne $schedulerService -and -not $Remove) {
    if ($PSCmdlet.ShouldProcess($serviceName, "set startup type to Disabled")) {
        Set-Service -Name $serviceName -StartupType Disabled
    }
}

if ($Remove) {
    if (-not (Test-Path $wrapperPath)) {
        throw "Cannot uninstall $serviceName because $wrapperPath was not found."
    }
    if ($PSCmdlet.ShouldProcess($serviceName, "uninstall WinSW service")) {
        & $wrapperPath uninstall | Out-Null
    }
}

$schedulerService = Get-ServiceOrNull -Name $serviceName
$schedulerStartMode = Get-ServiceStartMode -Name $serviceName
$backendService = Get-ServiceOrNull -Name $backendServiceName

if ($Remove) {
    Write-Host "$serviceName uninstall completed."
} elseif ($null -eq $schedulerService) {
    Write-Host "$serviceName is not present after disable step."
} else {
    Write-Host "$serviceName status: $($schedulerService.Status)"
    if ($null -ne $schedulerStartMode) {
        Write-Host "$serviceName startup mode: $schedulerStartMode"
    }
}

if ($null -eq $backendService) {
    Write-Warning "$backendServiceName was not found. Verify that backend background scheduler is running before leaving $serviceName disabled."
} else {
    Write-Host "$backendServiceName status: $($backendService.Status)"
    if ($backendService.Status -ne "Running") {
        Write-Warning "$backendServiceName is not running. SQLite dev mode expects backend background scheduler to remain active when $serviceName is disabled."
    }
}

if (-not $Remove) {
    Write-Host "Note: scripts\\install_windows_services.ps1 now omits $serviceName by default."
    Write-Host "Only reruns with -IncludeScheduler will install and start $serviceName again."
}
