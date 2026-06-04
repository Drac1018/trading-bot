param(
    [string]$HostName = "1.233.93.187.nip.io"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$caddyExe = Join-Path $repoRoot ".tools\caddy\caddy.exe"
$caddyFile = Join-Path $repoRoot "infra\caddy\operator.Caddyfile"
$storagePath = Join-Path $repoRoot "data\caddy"

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

if (-not (Test-Path -LiteralPath $caddyExe)) {
    throw "Caddy executable not found at $caddyExe. Download it from https://caddyserver.com/download or run the HTTPS setup step first."
}

if (-not (Test-Path -LiteralPath $caddyFile)) {
    throw "Caddyfile not found at $caddyFile."
}

$trustedProxySecret = Get-ConfiguredValue -Name "OPERATOR_UI_TRUSTED_PROXY_SECRET"
if (-not $trustedProxySecret) {
    throw "OPERATOR_UI_TRUSTED_PROXY_SECRET is required so Caddy can mark trusted forwarded headers for the loopback operator UI."
}

New-Item -ItemType Directory -Force -Path $storagePath | Out-Null
[Environment]::SetEnvironmentVariable("OPERATOR_HTTPS_HOST", $HostName, "Process")
[Environment]::SetEnvironmentVariable("CADDY_STORAGE_PATH", $storagePath, "Process")
[Environment]::SetEnvironmentVariable("OPERATOR_UI_TRUSTED_PROXY_SECRET", $trustedProxySecret, "Process")

& $caddyExe run --config $caddyFile --adapter caddyfile
