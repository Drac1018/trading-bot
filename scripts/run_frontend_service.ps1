param(
    [string]$Hostname = "127.0.0.1",
    [int]$Port = 3000,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "use_node_runtime.ps1")
$runtime = Use-ProjectNodeRuntime -RepoRoot $repoRoot

Set-Location (Join-Path $repoRoot "frontend")

function Invoke-WithFrontendBuildLock {
    param([Parameter(Mandatory = $true)][scriptblock]$ScriptBlock)

    $lockPath = Join-Path $repoRoot ".next-build.lock"
    $deadline = (Get-Date).AddMinutes(10)
    $lockStream = $null
    do {
        try {
            $lockStream = [System.IO.File]::Open(
                $lockPath,
                [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None
            )
            break
        } catch {
            if ((Get-Date) -ge $deadline) {
                throw "Productization guard: timed out waiting for frontend build lock at $lockPath."
            }
            Start-Sleep -Seconds 2
        }
    } while ($null -eq $lockStream)

    try {
        $lockStream.SetLength(0)
        $writer = [System.IO.StreamWriter]::new($lockStream, [System.Text.Encoding]::UTF8, 1024, $true)
        try {
            $writer.WriteLine("pid=$PID")
            $writer.WriteLine("started_at=$((Get-Date).ToString('o'))")
            $writer.Flush()
        } finally {
            $writer.Dispose()
        }
        & $ScriptBlock
    } finally {
        if ($null -ne $lockStream) {
            $lockStream.Dispose()
        }
        Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    }
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

function Set-EnvFromDotEnvIfMissing {
    param([Parameter(Mandatory = $true)][string]$Name)

    if ([Environment]::GetEnvironmentVariable($Name, "Process")) {
        return
    }

    $value = Get-DotEnvValue -Name $Name
    if ($null -ne $value -and $value -ne "") {
        [Environment]::SetEnvironmentVariable($Name, $value, "Process")
    }
}

function Test-EnvFlag {
    param([Parameter(Mandatory = $true)][string]$Name)

    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ($null -eq $value) {
        $value = ""
    }
    $value = $value.Trim().ToLowerInvariant()
    return $value -in @("1", "true", "yes", "on")
}

function Test-ProductizedOperatorAuthRequired {
    $appEnv = Get-ConfiguredValue -Name "APP_ENV"
    if ($null -eq $appEnv) {
        $appEnv = ""
    }
    $appEnv = $appEnv.Trim().ToLowerInvariant()
    return $appEnv -in @("prod", "production") -or (Test-EnvFlag -Name "OPERATOR_UI_BEHIND_TLS_PROXY")
}

function Test-LoopbackHostname {
    param([Parameter(Mandatory = $true)][string]$Value)

    $normalized = $Value.Trim().ToLowerInvariant()
    return $normalized -in @("127.0.0.1", "localhost", "::1", "[::1]")
}

function Get-ConfiguredValue {
    param([Parameter(Mandatory = $true)][string]$Name)

    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if (-not $value) {
        $value = Get-DotEnvValue -Name $Name
    }
    return $value
}

function Assert-ProductizedFrontendExposure {
    param([Parameter(Mandatory = $true)][string]$HostName)

    $normalizedHost = $HostName.Trim()
    if (-not (Test-LoopbackHostname -Value $normalizedHost) -and -not (Test-EnvFlag -Name "TRADING_MVP_ALLOW_PUBLIC_FRONTEND_BIND")) {
        throw "Productization guard: frontend public bind is blocked for Hostname=$normalizedHost. Keep the operator UI bound to 127.0.0.1 behind HTTPS reverse proxy and IP allowlist/VPN, or set TRADING_MVP_ALLOW_PUBLIC_FRONTEND_BIND=1 only for a private listener protected from direct public HTTP."
    }

    $appEnv = [Environment]::GetEnvironmentVariable("APP_ENV", "Process")
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
            throw "Productization guard: OPERATOR_UI_TRUSTED_PROXY_SECRET is required when OPERATOR_UI_BEHIND_TLS_PROXY=1 so forged X-Forwarded-* headers are not trusted."
        }
        return
    }

    throw "Productization guard: public HTTP operator UI is blocked for APP_ENV=$appEnv. Bind the frontend to 127.0.0.1 and run behind HTTPS reverse proxy with operator auth and IP allowlist, then set OPERATOR_UI_BEHIND_TLS_PROXY=1."
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
        throw "Productization guard: OPERATOR_UI_PASSWORD or FRONTEND_AUTH_PASSWORD must be at least 16 characters for production/TLS-proxied operator UI."
    }

    $sessionSecret = Get-ConfiguredValue -Name "FRONTEND_AUTH_SECRET"
    if (-not $sessionSecret) {
        $sessionSecret = Get-ConfiguredValue -Name "OPERATOR_AUTH_TOKEN"
    }
    if (-not $sessionSecret -or $sessionSecret.Trim().Length -lt 32) {
        throw "Productization guard: FRONTEND_AUTH_SECRET or OPERATOR_AUTH_TOKEN must be at least 32 characters for production/TLS-proxied operator UI. Do not reuse OPERATOR_API_KEY as the UI session signing secret."
    }
}

foreach ($name in @(
    "APP_ENV",
    "OPERATOR_AUTH_ENABLED",
    "OPERATOR_UI_USERNAME",
    "OPERATOR_UI_PASSWORD",
    "FRONTEND_AUTH_PASSWORD",
    "FRONTEND_AUTH_SECRET",
    "FRONTEND_AUTH_SESSION_TTL_SECONDS",
    "FRONTEND_AUTH_COOKIE_SECURE",
    "FRONTEND_AUTH_MAX_FAILED_ATTEMPTS",
    "FRONTEND_AUTH_RATE_LIMIT_WINDOW_SECONDS",
    "OPERATOR_AUTH_TOKEN",
    "OPERATOR_API_KEY",
    "OPERATOR_UI_BEHIND_TLS_PROXY",
    "OPERATOR_UI_TRUSTED_PROXY_SECRET",
    "TRADING_MVP_ALLOW_PUBLIC_FRONTEND_BIND"
)) {
    Set-EnvFromDotEnvIfMissing -Name $name
}

Assert-ProductizedFrontendExposure -HostName $Hostname
Assert-ProductizedOperatorAuthSecrets

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Productization guard: command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

function Ensure-FrontendDependencies {
    if (Test-Path "pnpm-lock.yaml") {
        if (-not (Test-Path "node_modules")) {
            Invoke-Checked -FilePath $runtime.CorepackCmd -Arguments @("pnpm", "install", "--frozen-lockfile", "--force")
        }
    }
    elseif (-not (Test-Path "node_modules")) {
        Invoke-Checked -FilePath $runtime.NpmCmd -Arguments @("install")
    }
}

function Ensure-FrontendBuild {
    $nextBuildPath = Join-Path $PWD ".next"
    $buildId = Join-Path $nextBuildPath "BUILD_ID"
    $pagesManifest = Join-Path $nextBuildPath "server\\pages-manifest.json"
    $middlewareManifest = Join-Path $nextBuildPath "server\\middleware-manifest.json"
    $proxyBundle = Join-Path $nextBuildPath "server\\proxy.js"
    $middlewareBundle = Join-Path $nextBuildPath "server\\middleware.js"
    $staticPath = Join-Path $nextBuildPath "static"
    $staticChunksPath = Join-Path $staticPath "chunks"
    $staticCssPath = Join-Path $staticPath "css"
    $clientReferenceManifest = Get-ChildItem -LiteralPath (Join-Path $nextBuildPath "server\\app") -Recurse -Filter "*client-reference-manifest*" -ErrorAction SilentlyContinue | Select-Object -First 1
    $staticChunk = Get-ChildItem -LiteralPath $staticChunksPath -File -Filter "*.js" -ErrorAction SilentlyContinue | Select-Object -First 1
    $staticCss = Get-ChildItem -LiteralPath $staticCssPath -File -Filter "*.css" -ErrorAction SilentlyContinue | Where-Object { $_.Length -gt 0 } | Select-Object -First 1
    if (
        (Test-Path -LiteralPath $buildId) -and
        (Test-Path -LiteralPath $pagesManifest) -and
        (Test-Path -LiteralPath $middlewareManifest) -and
        ((Test-Path -LiteralPath $proxyBundle) -or (Test-Path -LiteralPath $middlewareBundle)) -and
        (Test-Path -LiteralPath $staticPath) -and
        ($null -ne $staticChunk) -and
        ($null -ne $staticCss) -and
        ($null -ne $clientReferenceManifest)
    ) {
        return
    }
    if ($SkipBuild) {
        throw "Productization guard: -SkipBuild requires a complete Next build with .next\\BUILD_ID, server manifests, proxy or middleware bundle, static chunks, static CSS, and app client-reference manifests."
    }
    foreach ($generatedPath in @(
        (Join-Path $nextBuildPath "BUILD_ID"),
        (Join-Path $nextBuildPath "build"),
        (Join-Path $nextBuildPath "diagnostics"),
        (Join-Path $nextBuildPath "lock"),
        (Join-Path $nextBuildPath "package.json"),
        (Join-Path $nextBuildPath "server"),
        (Join-Path $nextBuildPath "standalone"),
        (Join-Path $nextBuildPath "static"),
        (Join-Path $nextBuildPath "trace"),
        (Join-Path $nextBuildPath "types"),
        (Join-Path $nextBuildPath "turbopack")
    )) {
        if (Test-Path -LiteralPath $generatedPath) {
            Remove-Item -LiteralPath $generatedPath -Recurse -Force
        }
    }
    if (Test-Path "pnpm-lock.yaml") {
        Invoke-Checked -FilePath $runtime.CorepackCmd -Arguments @("pnpm", "run", "build")
    }
    else {
        $nextCli = Join-Path (Join-Path $PWD "node_modules\\next\\dist\\bin") "next"
        Invoke-Checked -FilePath $runtime.NodeExe -Arguments @($nextCli, "build", "--webpack")
    }
    if (-not (Test-Path -LiteralPath $buildId)) {
        throw "Productization guard: Next build completed without .next\\BUILD_ID."
    }
    if (-not (Test-Path -LiteralPath $pagesManifest)) {
        throw "Productization guard: Next build completed without .next\\server\\pages-manifest.json."
    }
    if (-not (Test-Path -LiteralPath $middlewareManifest)) {
        throw "Productization guard: Next build completed without .next\\server\\middleware-manifest.json."
    }
    if (-not ((Test-Path -LiteralPath $proxyBundle) -or (Test-Path -LiteralPath $middlewareBundle))) {
        throw "Productization guard: Next build completed without .next\\server\\proxy.js or .next\\server\\middleware.js."
    }
}

Ensure-FrontendDependencies
Invoke-WithFrontendBuildLock -ScriptBlock { Ensure-FrontendBuild }

$nextCli = Join-Path (Join-Path $PWD "node_modules\\next\\dist\\bin") "next"
& $runtime.NodeExe $nextCli start --hostname $Hostname --port $Port
