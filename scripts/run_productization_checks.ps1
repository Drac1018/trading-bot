param(
    [string]$BackendBaseUrl = $(if ($env:BACKEND_BASE_URL) { $env:BACKEND_BASE_URL } else { "http://127.0.0.1:8000" }),
    [string]$FrontendBaseUrl = $(if ($env:FRONTEND_BASE_URL) { $env:FRONTEND_BASE_URL } else { "http://127.0.0.1:3000" }),
    [string]$OperatorApiKey = $(if ($env:OPERATOR_API_KEY) { $env:OPERATOR_API_KEY } else { "" }),
    [string]$OperatorUiUsername = $(if ($env:OPERATOR_UI_USERNAME) { $env:OPERATOR_UI_USERNAME } else { "operator" }),
    [string]$OperatorUiPassword = $(if ($env:OPERATOR_UI_PASSWORD) { $env:OPERATOR_UI_PASSWORD } else { "" }),
    [int]$Samples = 20,
    [int]$ApiP95Ms = 1500,
    [int]$ApiP99Ms = 3000,
    [int]$ApiFirstHitMs = 3000,
    [int]$FrontendP95Ms = 2500,
    [int]$FrontendP99Ms = 5000,
    [int]$FrontendFirstHitMs = 5000,
    [switch]$SkipSlo,
    [switch]$SkipBackendValidation,
    [switch]$SkipFrontendSmoke,
    [switch]$SkipBackupRestore,
    [switch]$SkipDatabaseCredentialPolicy,
    [switch]$SkipAiCostGate,
    [switch]$SkipReadinessGate,
    [int]$RuntimeGateWaitSeconds = 0,
    [int]$RuntimeGatePollSeconds = 60,
    [string]$RuntimeGateEvidencePath = "",
    [string]$BackupDir = ".logs\productization"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backupRoot = Join-Path $repoRoot $BackupDir
$operatorUiUsernameExplicit = $PSBoundParameters.ContainsKey("OperatorUiUsername")

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$WorkingDirectory = $repoRoot
    )

    $previousLocation = Get-Location
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        Set-Location $WorkingDirectory
        $ErrorActionPreference = "Continue"
        $output = & $FilePath @Arguments 2>&1
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorActionPreference
        if ($exitCode -ne 0) {
            if ($output) {
                $output | ForEach-Object { Write-Error $_ }
            }
            throw "$FilePath $($Arguments -join ' ') failed with exit code $exitCode"
        }
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
        Set-Location $previousLocation
    }
}

function Invoke-FrontendValidation {
    . (Join-Path $PSScriptRoot "use_node_runtime.ps1")
    $runtime = Use-ProjectNodeRuntime -RepoRoot $repoRoot
    $frontendDir = Join-Path $repoRoot "frontend"
    $env:COREPACK_ENABLE_DOWNLOAD_PROMPT = "0"

    Invoke-Checked -FilePath $runtime.CorepackCmd -Arguments @("pnpm", "-C", $frontendDir, "run", "lint")
    Invoke-Checked -FilePath $runtime.CorepackCmd -Arguments @("pnpm", "-C", $frontendDir, "run", "build")
    Invoke-Checked -FilePath $runtime.CorepackCmd -Arguments @("pnpm", "-C", $frontendDir, "run", "test:smoke")
}

function Get-DotEnvValue {
    param([string]$Name)

    $path = Join-Path $repoRoot ".env"
    if (-not (Test-Path -LiteralPath $path)) {
        return $null
    }
    foreach ($line in Get-Content -LiteralPath $path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or $trimmed -notlike "*=*") {
            continue
        }
        $pair = $trimmed -split "=", 2
        if ($pair[0].Trim() -eq $Name) {
            return $pair[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function Get-ConfiguredDatabaseUrl {
    if ($env:DATABASE_URL) {
        return $env:DATABASE_URL.Trim()
    }
    return Get-DotEnvValue -Name "DATABASE_URL"
}

function Get-OperatorApiKey {
    if ($OperatorApiKey) {
        return $OperatorApiKey.Trim()
    }
    $dotenvKey = Get-DotEnvValue -Name "OPERATOR_API_KEY"
    if ($dotenvKey) {
        return $dotenvKey.Trim()
    }
    return ""
}

function Get-BackendAuthHeaders {
    $headers = @{}
    $key = Get-OperatorApiKey
    if ($key) {
        $headers["X-Operator-API-Key"] = $key
    }
    return $headers
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

function Get-FrontendAuthHeaders {
    $username = $OperatorUiUsername
    if (-not $operatorUiUsernameExplicit) {
        $dotenvUsername = Get-DotEnvValue -Name "OPERATOR_UI_USERNAME"
        if ($dotenvUsername) {
            $username = $dotenvUsername.Trim()
        }
    }
    $password = $OperatorUiPassword
    if (-not $password) {
        $password = Get-DotEnvValue -Name "OPERATOR_UI_PASSWORD"
    }
    if (-not $password) {
        $password = Get-DotEnvValue -Name "FRONTEND_AUTH_PASSWORD"
    }
    if (-not $password) {
        $password = Get-DotEnvValue -Name "OPERATOR_AUTH_TOKEN"
    }
    if (-not $password) {
        return @{}
    }
    $raw = "{0}:{1}" -f $username, $password.Trim()
    $token = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($raw))
    $headers = @{ Authorization = "Basic $token" }
    if (Test-EnvFlag -Name "OPERATOR_UI_BEHIND_TLS_PROXY") {
        $frontendUri = [Uri]$FrontendBaseUrl
        $headers["X-Forwarded-Proto"] = "https"
        $headers["X-Forwarded-Host"] = $frontendUri.Authority
        $trustedProxySecret = Get-ConfiguredValue -Name "OPERATOR_UI_TRUSTED_PROXY_SECRET"
        if ($trustedProxySecret) {
            $headers["X-Operator-Proxy-Secret"] = $trustedProxySecret
        }
    }
    return $headers
}

function Get-Percentile {
    param(
        [Parameter(Mandatory = $true)][double[]]$Values,
        [Parameter(Mandatory = $true)][double]$Percentile
    )

    if ($Values.Count -eq 0) {
        return 0.0
    }
    $sorted = @($Values | Sort-Object)
    $index = [Math]::Ceiling(($Percentile / 100.0) * $sorted.Count) - 1
    $index = [Math]::Max(0, [Math]::Min($index, $sorted.Count - 1))
    return [Math]::Round([double]$sorted[$index], 2)
}

function Invoke-HttpSample {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [hashtable]$Headers = @{}
    )

    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    $response = Invoke-WebRequest -Uri $Url -Headers $Headers -UseBasicParsing -MaximumRedirection 5 -TimeoutSec 20
    $stopwatch.Stop()
    if ([int]$response.StatusCode -ge 400) {
        throw "$Url returned HTTP $($response.StatusCode)"
    }
    return $stopwatch.Elapsed.TotalMilliseconds
}

function Invoke-JsonEndpoint {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [hashtable]$Headers = @{}
    )

    return Invoke-RestMethod -Uri $Url -Headers $Headers -MaximumRedirection 5 -TimeoutSec 30
}

function Invoke-ProductizationReadinessGate {
    $headers = Get-BackendAuthHeaders
    $readinessErrors = New-Object System.Collections.Generic.List[string]
    $serviceGate = Invoke-JsonEndpoint -Url "$BackendBaseUrl/api/runtime/service-gate" -Headers $headers
    if (-not $serviceGate.gate_clear) {
        $blockers = @($serviceGate.blockers) -join ","
        $readinessErrors.Add("service gate is not clear. blockers=$blockers")
    }
    if (-not $serviceGate.reconciliation_synced) {
        $readinessErrors.Add("exchange reconciliation is not synced.")
    }
    $counts = $serviceGate.counts
    if ($counts) {
        $activePendingEntryPlans = 0
        if ($null -ne $counts.active_pending_entry_plans) {
            $activePendingEntryPlans = [int]$counts.active_pending_entry_plans
        }
        $unresolvedSubmissionCount = 0
        if ($null -ne $counts.unresolved_submission_count) {
            $unresolvedSubmissionCount = [int]$counts.unresolved_submission_count
        }
        $recentSchedulerNonSuccess = 0
        if ($null -ne $counts.recent_scheduler_non_success) {
            $recentSchedulerNonSuccess = [int]$counts.recent_scheduler_non_success
        }
        if ($activePendingEntryPlans -gt 0) {
            $readinessErrors.Add("active pending entry plans remain.")
        }
        if ($unresolvedSubmissionCount -gt 0) {
            $readinessErrors.Add("unresolved submissions remain.")
        }
        if ($recentSchedulerNonSuccess -gt 0) {
            $readinessErrors.Add("recent scheduler failures remain.")
        }
    }

    $profitability = Invoke-JsonEndpoint -Url "$BackendBaseUrl/api/dashboard/profitability" -Headers $headers
    $readiness = $profitability.limited_live_readiness
    if (-not $readiness) {
        $readinessErrors.Add("profitability readiness payload is missing.")
    } else {
        $readyStatuses = @("limited_live_candidate", "scale_up_candidate")
        if ($readiness.status -notin $readyStatuses) {
            $reasonCodes = @($readiness.reason_codes) -join ","
            $thresholds = $readiness.thresholds
            $minCandidateEvents = if ($thresholds -and $null -ne $thresholds.min_candidate_events) { [int]$thresholds.min_candidate_events } else { "unknown" }
            $minActualEntries = if ($thresholds -and $null -ne $thresholds.min_actual_entries) { [int]$thresholds.min_actual_entries } else { "unknown" }
            $readinessErrors.Add("profitability readiness status=$($readiness.status), reason_codes=$reasonCodes, recent_candidate_events=$($readiness.recent_candidate_events), actual_entries=$($readiness.actual_entries), fills=$($readiness.fills), min_candidate_events=$minCandidateEvents, min_actual_entries=$minActualEntries.")
        }
    }

    if ($readinessErrors.Count -gt 0) {
        throw "Productization readiness check failed: $($readinessErrors -join ' | ')"
    }

    return [ordered]@{
        service_gate = "clear"
        reconciliation = "synced"
        profitability_status = $readiness.status
        actual_entries = [int]$readiness.actual_entries
        fills = [int]$readiness.fills
        reason_codes = @($readiness.reason_codes)
    }
}

function Invoke-ProductizationAiCostGate {
    $headers = Get-BackendAuthHeaders
    $aiUsage = Invoke-JsonEndpoint -Url "$BackendBaseUrl/api/settings/ai-usage" -Headers $headers
    $efficiency = $aiUsage.ai_cost_efficiency_summary
    if (-not $efficiency) {
        throw "Productization AI cost check failed: ai_cost_efficiency_summary payload is missing."
    }

    $wasteAssessment = $efficiency.waste_assessment
    $wasteStatus = if ($wasteAssessment) { [string]$wasteAssessment.status } else { "" }
    if (-not $wasteStatus) {
        throw "Productization AI cost check failed: waste assessment status is missing."
    }

    $focusMetrics = $efficiency.focus_metrics
    $rolling7d = if ($focusMetrics) { $focusMetrics.rolling_7d } else { $null }
    if (-not $rolling7d) {
        throw "Productization AI cost check failed: rolling_7d focus metrics are missing."
    }

    $providerCalls7d = 0
    if ($null -ne $rolling7d.provider_calls) {
        $providerCalls7d = [int]$rolling7d.provider_calls
    }
    $knownCost7d = 0.0
    if ($null -ne $rolling7d.known_ai_cost_usd) {
        $knownCost7d = [double]$rolling7d.known_ai_cost_usd
    }
    $netAfterAiCost7d = $null
    if ($null -ne $rolling7d.net_after_ai_cost_usd) {
        $netAfterAiCost7d = [double]$rolling7d.net_after_ai_cost_usd
    }
    $providerToOrderRate7d = $null
    if ($null -ne $rolling7d.provider_to_order_rate) {
        $providerToOrderRate7d = [double]$rolling7d.provider_to_order_rate
    }

    $signalCodes = @()
    if ($wasteAssessment -and $wasteAssessment.signals) {
        $signalCodes = @($wasteAssessment.signals | ForEach-Object { $_.code })
    }
    $runtimeGuard = if ($wasteAssessment) { $wasteAssessment.runtime_guard } else { $null }
    $runtimeGuardStatus = if ($runtimeGuard) { [string]$runtimeGuard.status } else { "" }
    $runtimeGuardReason = if ($runtimeGuard) { [string]$runtimeGuard.reason } else { "" }
    $runtimeGuardStatusForMessage = if ($runtimeGuardStatus) { $runtimeGuardStatus } else { "missing" }
    $runtimeGuardReasonForMessage = if ($runtimeGuardReason) { $runtimeGuardReason } else { "missing" }
    $runtimeGuardActive = (
        $runtimeGuardStatus -eq "active" -and
        $runtimeGuardReason -eq "low_actionability_cost_guard_active"
    )
    if ($wasteStatus -ne "ok" -and -not $runtimeGuardActive) {
        throw "Productization AI cost check failed: AI runtime guard missing or inactive for waste_assessment.status=$wasteStatus. runtime_guard_status=$runtimeGuardStatusForMessage, runtime_guard_reason=$runtimeGuardReasonForMessage, signals=$($signalCodes -join ","), provider_calls_7d=$providerCalls7d, provider_to_order_rate_7d=$providerToOrderRate7d, net_after_ai_cost_7d=$netAfterAiCost7d. Remediation: pause/disarm live runtime first, then deploy/restart backend and verify /api/settings/ai-usage includes waste_assessment.runtime_guard.status=active."
    }
    if ($null -ne $netAfterAiCost7d -and $netAfterAiCost7d -lt 0 -and -not $runtimeGuardActive) {
        throw "Productization AI cost check failed: 7d net after AI cost is negative. net_after_ai_cost_7d=$netAfterAiCost7d, known_ai_cost_7d=$knownCost7d."
    }

    return [ordered]@{
        status = if ($runtimeGuardActive) { "guard_active" } else { "ok" }
        waste_assessment_status = $wasteStatus
        runtime_guard_status = $runtimeGuardStatus
        runtime_guard_reason = $runtimeGuardReason
        provider_calls_7d = $providerCalls7d
        provider_to_order_rate_7d = $providerToOrderRate7d
        known_ai_cost_7d = $knownCost7d
        net_after_ai_cost_7d = $netAfterAiCost7d
        signal_codes = $signalCodes
    }
}

function Write-ProductizationRuntimeGateEvidence {
    param([Parameter(Mandatory = $true)][object]$Payload)

    $path = $RuntimeGateEvidencePath
    if (-not $path) {
        $path = Join-Path $backupRoot "runtime-gates-latest.json"
    }
    if (-not [IO.Path]::IsPathRooted($path)) {
        $path = Join-Path $repoRoot $path
    }
    $parent = Split-Path -Parent $path
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $Payload | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $path -Encoding UTF8
    return $path
}

function Invoke-ProductizationRuntimeGates {
    param([switch]$NoThrow)

    $gateResults = [ordered]@{}
    $runtimeGateErrors = New-Object System.Collections.Generic.List[string]
    if (-not $SkipAiCostGate) {
        try {
            $gateResults.productization_ai_cost = Invoke-ProductizationAiCostGate
        } catch {
            $runtimeGateErrors.Add($_.Exception.Message)
        }
    }
    if (-not $SkipReadinessGate) {
        try {
            $gateResults.productization_readiness = Invoke-ProductizationReadinessGate
        } catch {
            $runtimeGateErrors.Add($_.Exception.Message)
        }
    }

    $payload = [ordered]@{
        captured_at = (Get-Date).ToUniversalTime().ToString("o")
        status = if ($runtimeGateErrors.Count -gt 0) { "blocked" } else { "ok" }
        wait_seconds = $RuntimeGateWaitSeconds
        poll_seconds = $RuntimeGatePollSeconds
        skipped = [ordered]@{
            ai_cost_gate = [bool]$SkipAiCostGate
            readiness_gate = [bool]$SkipReadinessGate
        }
        results = $gateResults
        errors = @($runtimeGateErrors)
    }
    $evidenceFile = Write-ProductizationRuntimeGateEvidence -Payload $payload
    $payload["evidence_file"] = $evidenceFile
    $payload | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $evidenceFile -Encoding UTF8

    if ($runtimeGateErrors.Count -gt 0 -and -not $NoThrow) {
        throw "Productization runtime gates failed: $($runtimeGateErrors -join ' | ') Evidence: $evidenceFile"
    }

    return $payload
}

function Wait-ProductizationRuntimeGates {
    if ($RuntimeGateWaitSeconds -lt 1) {
        return Invoke-ProductizationRuntimeGates
    }
    if ($RuntimeGatePollSeconds -lt 1) {
        throw "RuntimeGatePollSeconds must be greater than zero."
    }

    $deadline = (Get-Date).AddSeconds($RuntimeGateWaitSeconds)
    $attempt = 0
    $lastPayload = $null
    do {
        $attempt += 1
        $lastPayload = Invoke-ProductizationRuntimeGates -NoThrow
        $lastPayload["attempt"] = $attempt
        Write-ProductizationRuntimeGateEvidence -Payload $lastPayload | Out-Null
        if ($lastPayload["status"] -eq "ok") {
            return $lastPayload
        }

        $remainingSeconds = [int][Math]::Ceiling(($deadline - (Get-Date)).TotalSeconds)
        if ($remainingSeconds -le 0) {
            $errors = @($lastPayload["errors"]) -join " | "
            throw "Productization runtime gates did not clear within $RuntimeGateWaitSeconds seconds: $errors Evidence: $($lastPayload['evidence_file'])"
        }
        Start-Sleep -Seconds ([Math]::Min($RuntimeGatePollSeconds, $remainingSeconds))
    } while ($true)
}

function Test-EndpointSlo {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Urls,
        [hashtable]$Headers = @{},
        [int]$P95BudgetMs,
        [int]$P99BudgetMs,
        [int]$FirstHitBudgetMs
    )

    $durations = New-Object System.Collections.Generic.List[double]
    $urlResults = New-Object System.Collections.Generic.List[object]
    $firstHitViolations = New-Object System.Collections.Generic.List[object]
    foreach ($url in $Urls) {
        $firstHit = Invoke-HttpSample -Url $url -Headers $Headers
        if ($firstHit -gt $FirstHitBudgetMs) {
            $firstHitViolations.Add([ordered]@{
                url = $url
                first_hit_ms = [Math]::Round($firstHit, 2)
                first_hit_budget_ms = $FirstHitBudgetMs
            })
        }
        $urlDurations = New-Object System.Collections.Generic.List[double]
        for ($i = 0; $i -lt $Samples; $i++) {
            $duration = Invoke-HttpSample -Url $url -Headers $Headers
            $durations.Add($duration)
            $urlDurations.Add($duration)
        }
        $urlValues = $urlDurations.ToArray()
        $urlResults.Add([ordered]@{
            url = $url
            first_hit_ms = [Math]::Round($firstHit, 2)
            samples = $urlValues.Count
            p95_ms = Get-Percentile -Values $urlValues -Percentile 95
            p99_ms = Get-Percentile -Values $urlValues -Percentile 99
        })
    }
    $p95 = Get-Percentile -Values $durations.ToArray() -Percentile 95
    $p99 = Get-Percentile -Values $durations.ToArray() -Percentile 99
    $result = [ordered]@{
        name = $Name
        samples = $durations.Count
        p95_ms = $p95
        p99_ms = $p99
        p95_budget_ms = $P95BudgetMs
        p99_budget_ms = $P99BudgetMs
        first_hit_budget_ms = $FirstHitBudgetMs
        urls = $urlResults
    }
    if ($firstHitViolations.Count -gt 0 -or $p95 -gt $P95BudgetMs -or $p99 -gt $P99BudgetMs) {
        $detail = ($urlResults | ConvertTo-Json -Compress -Depth 4)
        $firstHitDetail = ($firstHitViolations | ConvertTo-Json -Compress -Depth 4)
        throw "SLO failed for ${Name}: P95=$p95 ms, P99=$p99 ms, first-hit violations=$firstHitDetail. Urls=$detail"
    }
    return $result
}

function Convert-ToPgToolUrl {
    param([Parameter(Mandatory = $true)][string]$DatabaseUrl)

    return ($DatabaseUrl -replace "^postgresql\+[^:]+://", "postgresql://")
}

function Test-DefaultPostgresCredential {
    param([Parameter(Mandatory = $true)][string]$DatabaseUrl)

    if ($DatabaseUrl -notlike "postgresql*") {
        return $false
    }
    try {
        $uri = [Uri](Convert-ToPgToolUrl -DatabaseUrl $DatabaseUrl)
    } catch {
        throw "DATABASE_URL could not be parsed for credential policy validation."
    }
    $userInfo = [Uri]::UnescapeDataString($uri.UserInfo)
    return $userInfo -eq "trading:trading"
}

function Invoke-DatabaseCredentialPolicyCheck {
    $databaseUrl = Get-ConfiguredDatabaseUrl
    if (-not $databaseUrl) {
        throw "DATABASE_URL is required for productization credential policy validation."
    }
    if (Test-DefaultPostgresCredential -DatabaseUrl $databaseUrl) {
        throw "Productization database credential check failed: DATABASE_URL uses default PostgreSQL trading:trading credentials. Rotate the database role/password and update the service .env before productization sign-off."
    }
    return [ordered]@{
        database = $(if ($databaseUrl -like "postgresql*") { "postgresql" } elseif ($databaseUrl -like "sqlite*") { "sqlite" } else { "unknown" })
        default_postgres_credentials = $false
    }
}

function Invoke-OperatorAuthSecretPolicyCheck {
    if (-not (Test-ProductizedOperatorAuthRequired)) {
        return [ordered]@{
            status = "not_applicable"
            app_env = $(if ($null -eq (Get-ConfiguredValue -Name "APP_ENV")) { "" } else { (Get-ConfiguredValue -Name "APP_ENV").Trim().ToLowerInvariant() })
            behind_tls_proxy = (Test-EnvFlag -Name "OPERATOR_UI_BEHIND_TLS_PROXY")
        }
    }

    $operatorPassword = Get-ConfiguredValue -Name "OPERATOR_UI_PASSWORD"
    if (-not $operatorPassword) {
        $operatorPassword = Get-ConfiguredValue -Name "FRONTEND_AUTH_PASSWORD"
    }
    if (-not $operatorPassword -or $operatorPassword.Trim().Length -lt 16) {
        throw "Productization operator auth check failed: OPERATOR_UI_PASSWORD or FRONTEND_AUTH_PASSWORD must be at least 16 characters for production/TLS-proxied operator UI."
    }

    $sessionSecret = Get-ConfiguredValue -Name "FRONTEND_AUTH_SECRET"
    if (-not $sessionSecret) {
        $sessionSecret = Get-ConfiguredValue -Name "OPERATOR_AUTH_TOKEN"
    }
    if (-not $sessionSecret -or $sessionSecret.Trim().Length -lt 32) {
        throw "Productization operator auth check failed: FRONTEND_AUTH_SECRET or OPERATOR_AUTH_TOKEN must be at least 32 characters for production/TLS-proxied operator UI. Do not reuse OPERATOR_API_KEY as the UI session signing secret."
    }

    return [ordered]@{
        status = "ok"
        password_policy = "operator_ui_password_min_16"
        session_secret_policy = "dedicated_session_secret_min_32"
    }
}

function Get-PgTool {
    param([Parameter(Mandatory = $true)][string]$Name)

    $command = Get-Command "$Name.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $bundled = Join-Path $repoRoot ".tools\postgresql\pgsql-16.13\pgsql\bin\$Name.exe"
    if (Test-Path -LiteralPath $bundled) {
        return $bundled
    }
    return $null
}

function Invoke-BackupRestoreCheck {
    $databaseUrl = Get-ConfiguredDatabaseUrl
    if (-not $databaseUrl) {
        throw "DATABASE_URL is required for backup/restore validation."
    }
    if ($databaseUrl -notlike "postgresql*") {
        throw "Backup/restore validation requires PostgreSQL DATABASE_URL; refusing to validate SQLite for productized runtime."
    }

    $pgDump = Get-PgTool -Name "pg_dump"
    $pgRestore = Get-PgTool -Name "pg_restore"
    if (-not $pgDump -or -not $pgRestore) {
        throw "pg_dump and pg_restore are required for backup/restore validation."
    }

    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backupFile = Join-Path $backupRoot "trading-mvp-$stamp.dump"
    $restoreListFile = Join-Path $backupRoot "trading-mvp-$stamp.restore-list.txt"
    $pgUrl = Convert-ToPgToolUrl -DatabaseUrl $databaseUrl

    Invoke-Checked -FilePath $pgDump -Arguments @("--format=custom", "--file", $backupFile, $pgUrl)
    Invoke-Checked -FilePath $pgRestore -Arguments @("--list", $backupFile)
    & $pgRestore --list $backupFile | Set-Content -LiteralPath $restoreListFile -Encoding UTF8

    return [ordered]@{
        backup_file = $backupFile
        restore_list_file = $restoreListFile
    }
}

function Invoke-RollbackValidation {
    Invoke-Checked -FilePath "git" -Arguments @("rev-parse", "--verify", "HEAD")
    Invoke-Checked -FilePath "git" -Arguments @("diff", "--check")
    return [ordered]@{
        head = (git rev-parse --short HEAD)
        diff_check_command = "git diff --check"
        diff_check = "ok"
    }
}

$results = [ordered]@{}

if (-not $SkipDatabaseCredentialPolicy) {
    $results.database_credential_policy = Invoke-DatabaseCredentialPolicyCheck
}

$results.operator_auth_secret_policy = Invoke-OperatorAuthSecretPolicyCheck

if (-not $SkipAiCostGate -or -not $SkipReadinessGate) {
    $runtimeGatePayload = Wait-ProductizationRuntimeGates
    foreach ($key in $runtimeGatePayload["results"].Keys) {
        $results[$key] = $runtimeGatePayload["results"][$key]
    }
    $results.productization_runtime_gate_evidence = $runtimeGatePayload["evidence_file"]
}

if (-not $SkipBackendValidation) {
    Invoke-Checked -FilePath ".\.venv\Scripts\python.exe" -Arguments @("-m", "compileall", "-q", "backend\trading_mvp")
    Invoke-Checked -FilePath ".\.venv\Scripts\python.exe" -Arguments @("-m", "ruff", "check", "backend", "tests", "workers")
    Invoke-Checked -FilePath ".\.venv\Scripts\python.exe" -Arguments @("-m", "pytest")
    $results.backend_validation = "ok"
}

if (-not $SkipFrontendSmoke) {
    Invoke-FrontendValidation
    $results.frontend_smoke = "ok"
}

if (-not $SkipSlo) {
    $headers = @{}
    $key = Get-OperatorApiKey
    if ($key) {
        $headers["X-Operator-API-Key"] = $key
    }
    $results.api_slo = Test-EndpointSlo -Name "api" -Urls @(
        "$BackendBaseUrl/health",
        "$BackendBaseUrl/api/settings",
        "$BackendBaseUrl/api/settings/ai-usage",
        "$BackendBaseUrl/api/dashboard/operator?view=home",
        "$BackendBaseUrl/api/dashboard/profitability",
        "$BackendBaseUrl/api/analytics/cost-breakdown",
        "$BackendBaseUrl/api/analytics/opportunity-attribution/summary"
    ) -Headers $headers -P95BudgetMs $ApiP95Ms -P99BudgetMs $ApiP99Ms -FirstHitBudgetMs $ApiFirstHitMs
    $results.frontend_login_slo = Test-EndpointSlo -Name "frontend_login" -Urls @(
        "$FrontendBaseUrl/login"
    ) -Headers @{} -P95BudgetMs $FrontendP95Ms -P99BudgetMs $FrontendP99Ms -FirstHitBudgetMs $FrontendFirstHitMs
    $results.frontend_slo = Test-EndpointSlo -Name "frontend" -Urls @(
        "$FrontendBaseUrl/",
        "$FrontendBaseUrl/dashboard/operations",
        "$FrontendBaseUrl/dashboard/trading",
        "$FrontendBaseUrl/dashboard/backtest",
        "$FrontendBaseUrl/dashboard/cost-breakdown",
        "$FrontendBaseUrl/dashboard/account"
    ) -Headers (Get-FrontendAuthHeaders) -P95BudgetMs $FrontendP95Ms -P99BudgetMs $FrontendP99Ms -FirstHitBudgetMs $FrontendFirstHitMs
}

if (-not $SkipBackupRestore) {
    $results.backup_restore = Invoke-BackupRestoreCheck
}

$results.rollback_validation = Invoke-RollbackValidation
$results | ConvertTo-Json -Depth 8
