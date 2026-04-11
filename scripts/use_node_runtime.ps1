$ErrorActionPreference = "Stop"

function Use-ProjectNodeRuntime {
    param(
        [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    )

    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if ($nodeCommand) {
        $commandRoot = Split-Path -Parent $nodeCommand.Source
        return @{
            Source = "PATH"
            NodeExe = $nodeCommand.Source
            NpmCmd = (Join-Path $commandRoot "npm.cmd")
            CorepackCmd = (Join-Path $commandRoot "corepack.cmd")
        }
    }

    $version = "v24.14.1"
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
