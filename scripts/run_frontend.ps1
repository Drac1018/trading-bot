$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "use_node_runtime.ps1")
$runtime = Use-ProjectNodeRuntime -RepoRoot $repoRoot

Set-Location (Join-Path $repoRoot "frontend")

if (Test-Path "pnpm-lock.yaml") {
    & $runtime.CorepackCmd pnpm install --frozen-lockfile --force
    & $runtime.CorepackCmd pnpm build
    & $runtime.CorepackCmd pnpm exec next start --hostname 0.0.0.0 --port 3000
}
else {
    & $runtime.NpmCmd install
    & $runtime.NpmCmd run build
    & $runtime.NpmCmd exec next start -- --hostname 0.0.0.0 --port 3000
}
