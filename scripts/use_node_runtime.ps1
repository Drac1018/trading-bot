$ErrorActionPreference = "Stop"

function Use-ProjectNodeRuntime {
    param(
        [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    )

    $version = "v22.21.1"
    $packageName = "node-$version-win-x64"
    $toolsDir = Join-Path $RepoRoot ".tools"
    $installDir = Join-Path $toolsDir $packageName
    $nodeExe = Join-Path $installDir "node.exe"
    $npmCmd = Join-Path $installDir "npm.cmd"
    $corepackCmd = Join-Path $installDir "corepack.cmd"

    if (-not (Test-Path $nodeExe)) {
        New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
        $zipPath = Join-Path $toolsDir "$packageName.zip"
        $downloadUrl = "https://nodejs.org/dist/$version/$packageName.zip"
        Write-Host "Node.js runtime not found on PATH. Downloading portable runtime from $downloadUrl"
        Invoke-WebRequest -Uri $downloadUrl -OutFile $zipPath
        Expand-Archive -LiteralPath $zipPath -DestinationPath $toolsDir -Force
    }

    $env:PATH = "$installDir;$env:PATH"
    return @{
        Source = "portable"
        NodeExe = $nodeExe
        NpmCmd = $npmCmd
        CorepackCmd = $corepackCmd
    }
}
