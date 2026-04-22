param(
    [switch]$IncludeFrontend,
    [switch]$CheckOnly,
    [string]$WrapperHost = "127.0.0.1",
    [int]$WrapperPort = 8091,
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 3000,
    [int]$StartupTimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$wrapperHealthUrl = "http://$WrapperHost`:$WrapperPort/healthz"
$backendHealthUrl = "http://127.0.0.1:$BackendPort/health"
$settingsApiUrl = "http://127.0.0.1:$BackendPort/api/settings"
$cadencesApiUrl = "http://127.0.0.1:$BackendPort/api/settings/cadences"
$frontendSettingsUrl = "http://127.0.0.1:$FrontendPort/dashboard/settings?view=integration"
$expectedBlsUrl = "http://$WrapperHost`:$WrapperPort/bls/releases"
$healthTimeoutSeconds = if ($CheckOnly) { [Math]::Min($StartupTimeoutSeconds, 15) } else { $StartupTimeoutSeconds }

$script:results = @()
$script:hasFailure = $false

function Add-CheckResult {
    param(
        [ValidateSet("PASS", "WARN", "FAIL")]
        [string]$Level,
        [string]$Name,
        [string]$Detail
    )

    $script:results += [pscustomobject]@{
        Level  = $Level
        Name   = $Name
        Detail = $Detail
    }
    if ($Level -eq "FAIL") {
        $script:hasFailure = $true
    }
}

function Write-CheckResults {
    foreach ($item in $script:results) {
        $color = switch ($item.Level) {
            "PASS" { "Green" }
            "WARN" { "Yellow" }
            "FAIL" { "Red" }
        }
        Write-Host "[$($item.Level)] $($item.Name): $($item.Detail)" -ForegroundColor $color
    }
}

function Get-ListeningProcessId {
    param([int]$Port)

    try {
        $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -First 1
        if ($null -eq $connection) {
            return $null
        }
        return $connection.OwningProcess
    }
    catch {
        return $null
    }
}

function Start-ServiceWindow {
    param(
        [string]$Name,
        [string]$ScriptPath,
        [string[]]$Arguments = @()
    )

    $argumentList = @(
        "-NoExit",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $ScriptPath
    ) + $Arguments

    $process = Start-Process -FilePath "powershell.exe" `
        -WorkingDirectory $repoRoot `
        -ArgumentList $argumentList `
        -PassThru

    Write-Host "$Name 시작 요청 완료 (PID $($process.Id))" -ForegroundColor Cyan
}

function Wait-JsonEndpoint {
    param(
        [string]$Url,
        [string]$Name,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            return Invoke-RestMethod -Uri $Url -Method GET -TimeoutSec 5
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }

    throw "$Name readiness timeout: $Url"
}

function Wait-WebEndpoint {
    param(
        [string]$Url,
        [string]$Name,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            return Invoke-WebRequest -Uri $Url -Method GET -TimeoutSec 5 -UseBasicParsing
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }

    throw "$Name readiness timeout: $Url"
}

function Test-EmptyMap {
    param($Value)

    if ($null -eq $Value) {
        return $true
    }

    if ($Value -is [System.Collections.IDictionary]) {
        return $Value.Count -eq 0
    }

    if ($Value -is [pscustomobject]) {
        return @($Value.PSObject.Properties).Count -eq 0
    }

    return $false
}

function Format-Offenders {
    param(
        [object[]]$Items,
        [string]$PropertyName
    )

    return @(
        $Items | ForEach-Object {
            "$($_.symbol)=$($_.$PropertyName)"
        }
    ) -join ", "
}

Write-Host "발표일 운영 스크립트 시작" -ForegroundColor Yellow
Write-Host "repo: $repoRoot"
Write-Host "wrapper expected URL: $expectedBlsUrl"

if (-not $CheckOnly) {
    $wrapperPid = Get-ListeningProcessId -Port $WrapperPort
    if ($null -eq $wrapperPid) {
        if ($env:BLS_API_KEY) {
            Write-Host "BLS_API_KEY 감지됨. registered mode 사용 가능." -ForegroundColor Green
        }
        else {
            Write-Host "BLS_API_KEY 없음. public mode로 시작합니다." -ForegroundColor DarkYellow
        }
        Start-ServiceWindow -Name "BLS wrapper" `
            -ScriptPath (Join-Path $repoRoot "scripts\\run_bls_wrapper.ps1") `
            -Arguments @("-BindHost", $WrapperHost, "-Port", "$WrapperPort")
    }
    else {
        Write-Host "BLS wrapper 이미 실행 중 (PID $wrapperPid)" -ForegroundColor DarkYellow
    }

    $backendPid = Get-ListeningProcessId -Port $BackendPort
    if ($null -eq $backendPid) {
        Start-ServiceWindow -Name "backend" `
            -ScriptPath (Join-Path $repoRoot "scripts\\run_backend.ps1")
    }
    else {
        Write-Host "backend 이미 실행 중 (PID $backendPid)" -ForegroundColor DarkYellow
    }

    if ($IncludeFrontend) {
        $frontendPid = Get-ListeningProcessId -Port $FrontendPort
        if ($null -eq $frontendPid) {
            Start-ServiceWindow -Name "frontend" `
                -ScriptPath (Join-Path $repoRoot "scripts\\run_frontend.ps1")
        }
        else {
            Write-Host "frontend 이미 실행 중 (PID $frontendPid)" -ForegroundColor DarkYellow
        }
    }
}

$wrapperHealth = $null
$backendHealth = $null
$settings = $null
$cadences = $null

try {
    $wrapperHealth = Wait-JsonEndpoint -Url $wrapperHealthUrl -Name "wrapper" -TimeoutSeconds $healthTimeoutSeconds
    Add-CheckResult -Level "PASS" -Name "wrapper health" -Detail "reachable at $wrapperHealthUrl"
}
catch {
    Add-CheckResult -Level "FAIL" -Name "wrapper health" -Detail $_.Exception.Message
}

try {
    $backendHealth = Wait-JsonEndpoint -Url $backendHealthUrl -Name "backend" -TimeoutSeconds $healthTimeoutSeconds
    Add-CheckResult -Level "PASS" -Name "backend health" -Detail "reachable at $backendHealthUrl"
}
catch {
    Add-CheckResult -Level "FAIL" -Name "backend health" -Detail $_.Exception.Message
}

if ($IncludeFrontend) {
    try {
        $null = Wait-WebEndpoint -Url $frontendSettingsUrl -Name "frontend" -TimeoutSeconds $healthTimeoutSeconds
        Add-CheckResult -Level "PASS" -Name "frontend" -Detail "settings page reachable at $frontendSettingsUrl"
    }
    catch {
        Add-CheckResult -Level "WARN" -Name "frontend" -Detail $_.Exception.Message
    }
}

if (-not $script:hasFailure) {
    try {
        $settings = Invoke-RestMethod -Uri $settingsApiUrl -Method GET -TimeoutSec 10
    }
    catch {
        Add-CheckResult -Level "FAIL" -Name "settings api" -Detail "cannot read $settingsApiUrl"
    }

    try {
        $cadences = Invoke-RestMethod -Uri $cadencesApiUrl -Method GET -TimeoutSec 10
    }
    catch {
        Add-CheckResult -Level "FAIL" -Name "cadences api" -Detail "cannot read $cadencesApiUrl"
    }
}

if ($null -ne $settings) {
    if ($settings.event_source_provider -eq "fred") {
        Add-CheckResult -Level "PASS" -Name "event source provider" -Detail "fred"
    }
    else {
        Add-CheckResult -Level "FAIL" -Name "event source provider" -Detail "expected fred, actual=$($settings.event_source_provider)"
    }

    if ($settings.event_source_api_key_configured) {
        Add-CheckResult -Level "PASS" -Name "FRED key" -Detail "configured"
    }
    else {
        Add-CheckResult -Level "FAIL" -Name "FRED key" -Detail "not configured"
    }

    if ($settings.event_source_bls_enrichment_url -eq $expectedBlsUrl) {
        Add-CheckResult -Level "PASS" -Name "BLS enrichment URL" -Detail $expectedBlsUrl
    }
    elseif ([string]::IsNullOrWhiteSpace([string]$settings.event_source_bls_enrichment_url)) {
        Add-CheckResult -Level "FAIL" -Name "BLS enrichment URL" -Detail "not configured. expected=$expectedBlsUrl"
    }
    else {
        Add-CheckResult -Level "FAIL" -Name "BLS enrichment URL" -Detail "expected=$expectedBlsUrl, actual=$($settings.event_source_bls_enrichment_url)"
    }

    if (Test-EmptyMap -Value $settings.event_source_bls_enrichment_static_params) {
        Add-CheckResult -Level "PASS" -Name "BLS static params" -Detail "empty"
    }
    else {
        Add-CheckResult -Level "FAIL" -Name "BLS static params" -Detail "must stay empty in wrapper mode"
    }

    if (([int]$settings.market_refresh_interval_minutes) -le 1) {
        Add-CheckResult -Level "PASS" -Name "global market refresh" -Detail "$($settings.market_refresh_interval_minutes)m"
    }
    else {
        Add-CheckResult -Level "FAIL" -Name "global market refresh" -Detail "expected 1m, actual=$($settings.market_refresh_interval_minutes)m"
    }

    if (([int]$settings.decision_cycle_interval_minutes) -le 1) {
        Add-CheckResult -Level "PASS" -Name "global decision cycle" -Detail "$($settings.decision_cycle_interval_minutes)m"
    }
    else {
        Add-CheckResult -Level "FAIL" -Name "global decision cycle" -Detail "expected 1m, actual=$($settings.decision_cycle_interval_minutes)m"
    }
}

if ($null -ne $cadences) {
    $enabledItems = @($cadences.items | Where-Object { $_.enabled })
    $slowMarket = @($enabledItems | Where-Object { ([int]$_.market_refresh_interval_minutes) -gt 1 })
    $slowDecision = @($enabledItems | Where-Object { ([int]$_.decision_cycle_interval_minutes) -gt 1 })

    if ($slowMarket.Count -eq 0) {
        Add-CheckResult -Level "PASS" -Name "symbol market cadence" -Detail "all enabled symbols <= 1m"
    }
    else {
        Add-CheckResult -Level "FAIL" -Name "symbol market cadence" -Detail (Format-Offenders -Items $slowMarket -PropertyName "market_refresh_interval_minutes")
    }

    if ($slowDecision.Count -eq 0) {
        Add-CheckResult -Level "PASS" -Name "symbol decision cadence" -Detail "all enabled symbols <= 1m"
    }
    else {
        Add-CheckResult -Level "FAIL" -Name "symbol decision cadence" -Detail (Format-Offenders -Items $slowDecision -PropertyName "decision_cycle_interval_minutes")
    }
}

Write-Host ""
Write-Host "운영 준비 상태" -ForegroundColor Yellow
Write-CheckResults
Write-Host ""

if ($script:hasFailure) {
    Write-Host "현재 상태는 발표 운영 준비 완료가 아닙니다." -ForegroundColor Red
    Write-Host "우선 조치:" -ForegroundColor Red
    Write-Host "1. settings에서 provider/FRED key/BLS URL을 확인합니다."
    Write-Host "2. cadence를 global 및 symbol 기준 모두 1분 수준으로 맞춥니다."
    Write-Host "3. 필요한 경우 settings 저장 후 다시 'scripts\\run_release_day.ps1 -CheckOnly'를 실행합니다."
    Write-Host "4. 정말 급하면 발표 직후 'POST http://127.0.0.1:$BackendPort/api/cycles/run' 1회를 사용합니다."
    exit 1
}

Write-Host "발표 운영 준비 완료입니다." -ForegroundColor Green
Write-Host "재점검 명령: powershell -ExecutionPolicy Bypass -File scripts\\run_release_day.ps1 -CheckOnly"
Write-Host "설정 화면: $frontendSettingsUrl"
