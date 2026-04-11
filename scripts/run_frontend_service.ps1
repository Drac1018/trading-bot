$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "use_node_runtime.ps1")
$runtime = Use-ProjectNodeRuntime -RepoRoot $repoRoot

Set-Location (Join-Path $repoRoot "frontend")

function Ensure-FrontendDependencies {
    if (Test-Path "pnpm-lock.yaml") {
        if (-not (Test-Path "node_modules")) {
            & $runtime.CorepackCmd pnpm install --frozen-lockfile --force
        }
    }
    elseif (-not (Test-Path "node_modules")) {
        & $runtime.NpmCmd install
    }
}

function Ensure-FrontendBuild {
    $nextCli = Join-Path (Join-Path $PWD "node_modules\\next\\dist\\bin") "next"
    & $runtime.NodeExe $nextCli build
}

Ensure-FrontendDependencies
Ensure-FrontendBuild

$nextCli = Join-Path (Join-Path $PWD "node_modules\\next\\dist\\bin") "next"
& $runtime.NodeExe $nextCli start --hostname 0.0.0.0 --port 3000
