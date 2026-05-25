param(
    [string[]]$Target = @(
        "tests\test_settings_and_connectivity.py",
        "tests\test_risk_engine.py",
        "tests\test_pipeline.py",
        "tests\test_pending_entry_plan_watcher.py"
    ),
    [int]$PerTargetTimeoutSeconds = 180,
    [switch]$ContinueOnFailure
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python virtual environment not found: $python"
}

$failures = New-Object System.Collections.Generic.List[string]

foreach ($item in $Target) {
    $targetPath = Join-Path $repoRoot $item
    if (-not (Test-Path -LiteralPath $targetPath)) {
        throw "Pytest target not found: $item"
    }

    Write-Host "==> pytest $item"
    $job = Start-Job -ScriptBlock {
        param(
            [string]$RepoRoot,
            [string]$Python,
            [string]$PytestTarget
        )

        Set-Location $RepoRoot
        & $Python -m pytest -q $PytestTarget --tb=short
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "pytest $PytestTarget failed with exit code $exitCode"
        }
    } -ArgumentList $repoRoot, $python, $item

    $completed = Wait-Job -Job $job -Timeout $PerTargetTimeoutSeconds
    if (-not $completed) {
        Stop-Job -Job $job
        Remove-Job -Job $job -Force
        $message = "pytest $item exceeded ${PerTargetTimeoutSeconds}s"
        $failures.Add($message)
        Write-Warning $message
        if (-not $ContinueOnFailure) {
            throw $message
        }
        continue
    }

    Receive-Job -Job $job -ErrorAction Continue
    $state = $job.State
    Remove-Job -Job $job
    if ($state -ne "Completed") {
        $message = "pytest $item ended with job state $state"
        $failures.Add($message)
        if (-not $ContinueOnFailure) {
            throw $message
        }
    }
}

if ($failures.Count -gt 0) {
    throw "Focused backend safety tests failed: $($failures -join '; ')"
}

Write-Host "Focused backend safety tests passed."
