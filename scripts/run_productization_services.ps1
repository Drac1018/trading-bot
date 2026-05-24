param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 3000,
    [int]$TimeoutSeconds = 120,
    [switch]$RepairEnvironment
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repoRoot ".logs"
New-Item -ItemType Directory -Force $logDir | Out-Null

function Test-PortListening {
    param([int]$Port)
    $connection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return $null -ne $connection
}

function Wait-HttpOk {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [int]$TimeoutSeconds = 120,
        [hashtable]$Headers = @{}
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = $null
    while ((Get-Date) -lt $deadline) {
        try {
            $request = @{
                Uri = $Uri
                UseBasicParsing = $true
                TimeoutSec = 5
            }
            if ($Headers.Count -gt 0) {
                $request.Headers = $Headers
            }
            $response = Invoke-WebRequest @request
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                return $response
            }
            $lastError = "HTTP $($response.StatusCode)"
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }

    throw "Timed out waiting for $Uri. Last error: $lastError"
}

function Get-DotEnvValue {
    param([Parameter(Mandatory = $true)][string]$Name)

    $dotenvPath = Join-Path $repoRoot ".env"
    if (-not (Test-Path -LiteralPath $dotenvPath)) {
        return $null
    }

    foreach ($line in Get-Content -LiteralPath $dotenvPath -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $parts = $trimmed.Split("=", 2)
        if ($parts[0].Trim() -eq $Name) {
            return $parts[1].Trim().Trim('"').Trim("'")
        }
    }

    return $null
}

function Get-ConfiguredValue {
    param([Parameter(Mandatory = $true)][string]$Name)

    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if (-not $value) {
        $value = Get-DotEnvValue -Name $Name
    }
    return $value
}

function Test-EnvFlag {
    param([Parameter(Mandatory = $true)][string]$Name)

    $value = Get-ConfiguredValue -Name $Name
    if ($null -eq $value) {
        return $false
    }
    return $value.Trim().ToLowerInvariant() -in @("1", "true", "yes", "on")
}

function Test-ProductizedOperatorAuthRequired {
    $appEnv = Get-ConfiguredValue -Name "APP_ENV"
    if ($null -eq $appEnv) {
        $appEnv = ""
    }
    $appEnv = $appEnv.Trim().ToLowerInvariant()
    return $appEnv -in @("prod", "production") -or (Test-EnvFlag -Name "OPERATOR_UI_BEHIND_TLS_PROXY")
}

function Assert-ProductizedFrontendExposure {
    $appEnv = Get-ConfiguredValue -Name "APP_ENV"
    if ($null -eq $appEnv) {
        $appEnv = ""
    }
    $appEnv = $appEnv.Trim().ToLowerInvariant()
    if ($appEnv -notin @("prod", "production")) {
        return
    }

    if (Test-EnvFlag -Name "OPERATOR_UI_BEHIND_TLS_PROXY") {
        $trustedProxySecret = Get-ConfiguredValue -Name "OPERATOR_UI_TRUSTED_PROXY_SECRET"
        if (-not $trustedProxySecret) {
            throw "Productization preflight failed: OPERATOR_UI_TRUSTED_PROXY_SECRET is required when OPERATOR_UI_BEHIND_TLS_PROXY=1 so forged X-Forwarded-* headers are not trusted."
        }
        return
    }

    throw "Productization preflight failed: APP_ENV=$appEnv cannot expose the operator UI over direct HTTP. Put the frontend behind HTTPS reverse proxy and allowlist/VPN first, then set OPERATOR_UI_BEHIND_TLS_PROXY=1."
}

function Assert-ProductizedOperatorAuthSecrets {
    if (-not (Test-ProductizedOperatorAuthRequired)) {
        return
    }

    $operatorPassword = Get-ConfiguredValue -Name "OPERATOR_UI_PASSWORD"
    if (-not $operatorPassword) {
        $operatorPassword = Get-ConfiguredValue -Name "FRONTEND_AUTH_PASSWORD"
    }
    if (-not $operatorPassword -or $operatorPassword.Trim().Length -lt 16) {
        throw "Productization preflight failed: OPERATOR_UI_PASSWORD or FRONTEND_AUTH_PASSWORD must be at least 16 characters for production/TLS-proxied operator UI."
    }

    $sessionSecret = Get-ConfiguredValue -Name "FRONTEND_AUTH_SECRET"
    if (-not $sessionSecret) {
        $sessionSecret = Get-ConfiguredValue -Name "OPERATOR_AUTH_TOKEN"
    }
    if (-not $sessionSecret -or $sessionSecret.Trim().Length -lt 32) {
        throw "Productization preflight failed: FRONTEND_AUTH_SECRET or OPERATOR_AUTH_TOKEN must be at least 32 characters for production/TLS-proxied operator UI. Do not reuse OPERATOR_API_KEY as the UI session signing secret."
    }
}

function Get-OperatorSecret {
    foreach ($name in @("OPERATOR_UI_PASSWORD", "FRONTEND_AUTH_PASSWORD", "OPERATOR_AUTH_TOKEN", "OPERATOR_API_KEY")) {
        $value = Get-ConfiguredValue -Name $name
        if ($value) {
            return $value
        }
    }
    return $null
}

function Get-FrontendAuthHeaders {
    $secret = Get-OperatorSecret
    if (-not $secret) {
        return @{}
    }

    $user = Get-ConfiguredValue -Name "OPERATOR_UI_USERNAME"
    if (-not $user) {
        $user = "operator"
    }

    $token = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(("{0}:{1}" -f $user, $secret)))
    return @{ Authorization = "Basic $token" }
}

function Get-FrontendRequestHeaders {
    param([int]$Port)

    $headers = Get-FrontendAuthHeaders
    if (Test-EnvFlag -Name "OPERATOR_UI_BEHIND_TLS_PROXY") {
        $headers["X-Forwarded-Proto"] = "https"
        $headers["X-Forwarded-Host"] = "127.0.0.1:$Port"
        $trustedProxySecret = Get-ConfiguredValue -Name "OPERATOR_UI_TRUSTED_PROXY_SECRET"
        if ($trustedProxySecret) {
            $headers["X-Operator-Proxy-Secret"] = $trustedProxySecret
        }
    }
    return $headers
}

function Get-OperatorApiHeaders {
    $operatorApiKey = Get-ConfiguredValue -Name "OPERATOR_API_KEY"
    if (-not $operatorApiKey) {
        return @{}
    }
    return @{ "X-Operator-API-Key" = $operatorApiKey }
}

function Assert-FrontendRuntimeCurrent {
    param(
        [Parameter(Mandatory = $true)][string]$FrontendUrl,
        [Parameter(Mandatory = $true)][hashtable]$Headers,
        [int]$TimeoutSeconds = 60
    )

    Wait-HttpOk -Uri "$FrontendUrl/api/operator/csrf" -TimeoutSeconds $TimeoutSeconds -Headers $Headers | Out-Null
    Wait-HttpOk -Uri "$FrontendUrl/api/dashboard/operator?view=home" -TimeoutSeconds $TimeoutSeconds -Headers $Headers | Out-Null
}

function Wait-SchedulerFresh {
    param(
        [Parameter(Mandatory = $true)][string]$BackendUrl,
        [Parameter(Mandatory = $true)][hashtable]$Headers,
        [int]$TimeoutSeconds = 120
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastSummary = $null
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Wait-HttpOk -Uri "$BackendUrl/api/dashboard/operator?view=scheduler" -TimeoutSeconds 10 -Headers $Headers
            $payload = $response.Content | ConvertFrom-Json
            $lastSummary = $payload.control.scheduler_freshness_summary
            if ($lastSummary -and -not $lastSummary.stale) {
                return
            }
        } catch {
            $lastSummary = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }

    $detail = if ($lastSummary) { $lastSummary | ConvertTo-Json -Compress -Depth 5 } else { "no scheduler freshness payload" }
    throw "Productization preflight failed: scheduler freshness is stale or unavailable. Details: $detail"
}

function Start-HiddenPowerShell {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$StdoutPath,
        [Parameter(Mandatory = $true)][string]$StderrPath
    )

    Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList $Arguments `
        -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $StdoutPath `
        -RedirectStandardError $StderrPath `
        -WindowStyle Hidden `
        -PassThru
}

$ensureArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "ensure_dev_environment.ps1"))
if ($RepairEnvironment) {
    $ensureArgs += "-Repair"
}
& powershell.exe @ensureArgs
if ($LASTEXITCODE -ne 0) {
    throw "Development environment check failed."
}

Assert-ProductizedFrontendExposure
Assert-ProductizedOperatorAuthSecrets

$backendUrl = "http://127.0.0.1:$BackendPort"
$frontendUrl = "http://127.0.0.1:$FrontendPort"
$frontendAuthHeaders = Get-FrontendRequestHeaders -Port $FrontendPort
$operatorApiHeaders = Get-OperatorApiHeaders
$started = @()

if (-not (Test-PortListening -Port $BackendPort)) {
    if ($BackendPort -ne 8000) {
        throw "Productization backend startup currently uses scripts\run_backend.ps1, which binds port 8000. Start backend port $BackendPort explicitly before running this preflight."
    }
    $backend = Start-HiddenPowerShell `
        -Arguments @(
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            (Join-Path $PSScriptRoot "run_backend.ps1")
        ) `
        -StdoutPath (Join-Path $logDir "productization-backend-$BackendPort.out.log") `
        -StderrPath (Join-Path $logDir "productization-backend-$BackendPort.err.log")
    $started += [ordered]@{ service = "backend"; port = $BackendPort; pid = $backend.Id }
}

Wait-HttpOk -Uri "$backendUrl/health" -TimeoutSeconds $TimeoutSeconds | Out-Null
$serviceGateResponse = Wait-HttpOk -Uri "$backendUrl/api/runtime/service-gate" -TimeoutSeconds $TimeoutSeconds -Headers $operatorApiHeaders
$serviceGate = $serviceGateResponse.Content | ConvertFrom-Json
if (-not $serviceGate.gate_clear) {
    $blockers = @($serviceGate.blockers) -join ", "
    throw "Productization preflight failed: service gate is not clear. Blockers: $blockers"
}
Wait-SchedulerFresh -BackendUrl $backendUrl -Headers $operatorApiHeaders -TimeoutSeconds $TimeoutSeconds

if (-not (Test-PortListening -Port $FrontendPort)) {
    $frontend = Start-HiddenPowerShell `
        -Arguments @(
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            (Join-Path $PSScriptRoot "run_frontend_service.ps1"),
            "-Port",
            "$FrontendPort"
        ) `
        -StdoutPath (Join-Path $logDir "productization-frontend-$FrontendPort.out.log") `
        -StderrPath (Join-Path $logDir "productization-frontend-$FrontendPort.err.log")
    $started += [ordered]@{ service = "frontend"; port = $FrontendPort; pid = $frontend.Id }
}

Wait-HttpOk -Uri "$frontendUrl/" -TimeoutSeconds $TimeoutSeconds -Headers $frontendAuthHeaders | Out-Null
Assert-FrontendRuntimeCurrent -FrontendUrl $frontendUrl -Headers $frontendAuthHeaders -TimeoutSeconds $TimeoutSeconds
Wait-HttpOk -Uri "$frontendUrl/dashboard/operations" -TimeoutSeconds $TimeoutSeconds -Headers $frontendAuthHeaders | Out-Null
Wait-HttpOk -Uri "$frontendUrl/dashboard/backtest" -TimeoutSeconds $TimeoutSeconds -Headers $frontendAuthHeaders | Out-Null

[ordered]@{
    status = "ok"
    backend = "$backendUrl/health"
    frontend = "$frontendUrl/dashboard/operations"
    frontend_backtest = "$frontendUrl/dashboard/backtest"
    started = $started
    logs = $logDir
} | ConvertTo-Json -Depth 5
