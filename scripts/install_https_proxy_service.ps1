param(
    [string]$HostName = "1.233.93.187.nip.io"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$serviceRoot = Join-Path $repoRoot ".services"
$logPath = Join-Path $repoRoot ".logs\services\https-proxy"
$serviceId = "TradingMvpHttpsProxy"
$exePath = Join-Path $serviceRoot "$serviceId.exe"
$xmlPath = Join-Path $serviceRoot "$serviceId.xml"
$scriptPath = Join-Path $repoRoot "scripts\run_https_proxy.ps1"
$wrapperSource = Join-Path $serviceRoot "TradingMvpBackend.exe"

if (-not (Test-Path -LiteralPath $wrapperSource)) {
    throw "WinSW wrapper not found at $wrapperSource."
}

New-Item -ItemType Directory -Force -Path $serviceRoot | Out-Null
New-Item -ItemType Directory -Force -Path $logPath | Out-Null

if (-not (Test-Path -LiteralPath $exePath)) {
    Copy-Item -LiteralPath $wrapperSource -Destination $exePath -Force
}

$escapedRepo = $repoRoot -replace "&", "&amp;"
$escapedLog = $logPath -replace "&", "&amp;"
$escapedScript = $scriptPath -replace "&", "&amp;"
$escapedHost = $HostName -replace "&", "&amp;"

$xml = @"
<service>
  <id>$serviceId</id>
  <name>Trading MVP HTTPS Proxy</name>
  <description>Caddy HTTPS reverse proxy for the trading MVP operator UI</description>
  <executable>powershell.exe</executable>
  <arguments>-NoProfile -ExecutionPolicy Bypass -File "$escapedScript" -HostName "$escapedHost"</arguments>
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

Set-Content -LiteralPath $xmlPath -Value $xml -Encoding UTF8

$existing = Get-CimInstance Win32_Service -Filter "Name='$serviceId'" -ErrorAction SilentlyContinue
if ($existing -and $existing.State -ne "Stopped") {
    & $exePath stop | Out-Null
}
if ($existing) {
    & $exePath uninstall | Out-Null
}

& $exePath install | Out-Null
sc.exe config $serviceId start= auto | Out-Null
& $exePath start | Out-Null
Write-Host "Installed and started $serviceId for https://$HostName/"
