param(
    [int]$Port = 3000,
    [string]$ApiBaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "use_node_runtime.ps1")
$runtime = Use-ProjectNodeRuntime -RepoRoot $repoRoot
Set-Location (Join-Path $repoRoot "frontend")

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

$env:API_BASE_URL = $ApiBaseUrl
foreach ($name in @(
    "APP_ENV",
    "OPERATOR_AUTH_ENABLED",
    "OPERATOR_UI_USERNAME",
    "OPERATOR_UI_PASSWORD",
    "OPERATOR_AUTH_TOKEN",
    "OPERATOR_API_KEY"
)) {
    Set-EnvFromDotEnvIfMissing -Name $name
}

$nextCli = Join-Path (Join-Path $PWD "node_modules\next\dist\bin") "next"
if (-not (Test-Path -LiteralPath $nextCli)) {
    throw "Next.js CLI not found at $nextCli. Re-run scripts\ensure_dev_environment.ps1 -Repair first."
}

& $runtime.NodeExe $nextCli dev --hostname 127.0.0.1 --port $Port
