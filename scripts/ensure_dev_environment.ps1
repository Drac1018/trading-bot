param(
    [switch]$Repair,
    [switch]$SkipPython,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$frontendDir = Join-Path $repoRoot "frontend"

function Test-AccessiblePath {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [string]$Description = "path"
    )

    try {
        return Test-Path -LiteralPath $LiteralPath -ErrorAction Stop
    } catch {
        Write-Warning "Could not access $Description '$LiteralPath': $($_.Exception.Message)"
        return $false
    }
}

function Resolve-AccessiblePath {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [string]$Description = "path"
    )

    try {
        if (-not (Test-Path -LiteralPath $LiteralPath -ErrorAction Stop)) {
            return $null
        }
        return (Resolve-Path -LiteralPath $LiteralPath -ErrorAction Stop).Path
    } catch {
        Write-Warning "Could not resolve $Description '$LiteralPath': $($_.Exception.Message)"
        return $null
    }
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$WorkingDirectory = $repoRoot
    )

    $previousLocation = Get-Location
    try {
        Set-Location $WorkingDirectory
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$FilePath $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
        }
    } finally {
        Set-Location $previousLocation
    }
}

function Get-BasePython {
    $envPython = [Environment]::GetEnvironmentVariable("PYTHON_EXE", "Process")
    if ($envPython) {
        $resolvedEnvPython = Resolve-AccessiblePath -LiteralPath $envPython -Description "PYTHON_EXE"
        if ($resolvedEnvPython) {
            return $resolvedEnvPython
        }
    }

    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        try {
            $resolved = & $pyLauncher.Source -3 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $resolved) {
                $resolvedPy = Resolve-AccessiblePath -LiteralPath $resolved.Trim() -Description "py.exe Python 3 executable"
                if ($resolvedPy) {
                    return $resolvedPy
                }
            }
        } catch {
            Write-Warning "py.exe is present but could not resolve a Python 3 executable: $($_.Exception.Message)"
        }
    }

    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python -and $python.Source -notlike "*\WindowsApps\python.exe") {
        return $python.Source
    }

    $candidates = @(
        "C:\Users\DRAC\AppData\Local\Python\bin\python.exe",
        "C:\Users\DRAC\AppData\Local\Python\pythoncore-3.14-64\python.exe",
        "C:\Users\DRAC\AppData\Local\Programs\Python\Python315\python.exe"
    )

    foreach ($candidate in $candidates) {
        if (Test-AccessiblePath -LiteralPath $candidate -Description "Python candidate") {
            return $candidate
        }
    }

    throw "No usable Python executable found. Set PYTHON_EXE to python.exe, install Python 3.12+, or restore C:\Users\DRAC\AppData\Local\Python."
}

function Test-VenvBaseExecutable {
    $venvConfig = Join-Path $repoRoot ".venv\pyvenv.cfg"
    if (-not (Test-AccessiblePath -LiteralPath $venvConfig -Description ".venv config")) {
        return $true
    }

    foreach ($line in Get-Content -LiteralPath $venvConfig -Encoding UTF8) {
        if ($line -notmatch "^(home|executable)\s*=\s*(.+)$") {
            continue
        }
        $path = $Matches[2].Trim()
        if ($Matches[1] -eq "home") {
            $path = Join-Path $path "python.exe"
        }
        if ($path -and -not (Test-AccessiblePath -LiteralPath $path -Description ".venv base $($Matches[1])")) {
            Write-Warning "Broken .venv detected: pyvenv.cfg points to missing or inaccessible $($Matches[1]) path '$path'. Set PYTHON_EXE to a usable Python 3.12+ executable, then run: powershell -ExecutionPolicy Bypass -File scripts\ensure_dev_environment.ps1 -Repair"
            return $false
        }
    }

    return $true
}

function Move-BrokenVenvAside {
    $venvDir = Join-Path $repoRoot ".venv"
    if (-not (Test-AccessiblePath -LiteralPath $venvDir -Description ".venv directory")) {
        return
    }

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backup = Join-Path $repoRoot ".venv.broken-$stamp"
    Write-Warning "Moving broken .venv to $backup before repair."
    Move-Item -LiteralPath $venvDir -Destination $backup
}

function Test-PythonEnvironment {
    if (-not (Test-AccessiblePath -LiteralPath $venvPython -Description ".venv Python executable")) {
        Write-Warning ".venv Python executable is missing at $venvPython. Set PYTHON_EXE if Python is not on PATH, then run: powershell -ExecutionPolicy Bypass -File scripts\ensure_dev_environment.ps1 -Repair"
        return $false
    }

    if (-not (Test-VenvBaseExecutable)) {
        return $false
    }

    try {
        & $venvPython -c "import sys; import fastapi; import sqlalchemy; print(sys.version)"
        return $LASTEXITCODE -eq 0
    } catch {
        Write-Warning ".venv Python validation failed: $($_.Exception.Message). Set PYTHON_EXE if Python is not on PATH, then run: powershell -ExecutionPolicy Bypass -File scripts\ensure_dev_environment.ps1 -Repair"
        return $false
    }
}

function Repair-PythonEnvironment {
    $basePython = Get-BasePython
    Write-Host "Repairing .venv with $basePython"

    $venvBaseOk = Test-VenvBaseExecutable
    if ((Test-AccessiblePath -LiteralPath $venvPython -Description ".venv Python executable") -and $venvBaseOk) {
        Invoke-Checked -FilePath $basePython -Arguments @("-m", "venv", "--upgrade", ".venv")
    } else {
        if (-not $venvBaseOk) {
            Move-BrokenVenvAside
        }
        Invoke-Checked -FilePath $basePython -Arguments @("-m", "venv", ".venv")
    }

    Invoke-Checked -FilePath $venvPython -Arguments @("-m", "pip", "install", "--upgrade", "pip")
    Invoke-Checked -FilePath $venvPython -Arguments @("-m", "pip", "install", "-e", ".[dev]")
}

function Get-Corepack {
    $corepack = Get-Command corepack.cmd -ErrorAction SilentlyContinue
    if ($corepack) {
        return $corepack.Source
    }

    $defaultCorepack = "C:\Program Files\nodejs\corepack.cmd"
    if (Test-AccessiblePath -LiteralPath $defaultCorepack -Description "corepack.cmd") {
        return $defaultCorepack
    }

    throw "corepack.cmd not found. Install Node.js 20.9+ or add corepack to PATH."
}

function Invoke-Pnpm {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $env:COREPACK_ENABLE_DOWNLOAD_PROMPT = "0"
    $pnpm = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
    if ($pnpm) {
        Invoke-Checked -FilePath $pnpm.Source -Arguments $Arguments -WorkingDirectory $frontendDir
        return
    }

    $corepack = Get-Corepack
    Invoke-Checked -FilePath $corepack -Arguments (@("pnpm") + $Arguments) -WorkingDirectory $frontendDir
}

function Invoke-FrontendBin {
    param(
        [Parameter(Mandatory = $true)][string]$CommandName,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $cmdPath = Join-Path $frontendDir "node_modules\.bin\$CommandName.cmd"
    if (-not (Test-AccessiblePath -LiteralPath $cmdPath -Description "frontend command $CommandName")) {
        throw "Frontend command '$CommandName' is missing at $cmdPath. Re-run with -Repair to install node_modules."
    }
    Invoke-Checked -FilePath $cmdPath -Arguments $Arguments -WorkingDirectory $frontendDir
}

function Test-FrontendEnvironment {
    try {
        Invoke-FrontendBin -CommandName "tsc" -Arguments @("--version")
        Invoke-FrontendBin -CommandName "next" -Arguments @("--version")
        Invoke-FrontendBin -CommandName "playwright" -Arguments @("--version")
        return $true
    } catch {
        Write-Warning $_.Exception.Message
        return $false
    }
}

function Repair-FrontendEnvironment {
    Write-Host "Repairing frontend node_modules with pnpm package-import-method=copy"
    Invoke-Pnpm -Arguments @(
        "install",
        "--frozen-lockfile",
        "--force",
        "--package-import-method=copy"
    )
}

$result = [ordered]@{}

if (-not $SkipPython) {
    $pythonOk = Test-PythonEnvironment
    if (-not $pythonOk -and $Repair) {
        Repair-PythonEnvironment
        $pythonOk = Test-PythonEnvironment
    }
    $result.python = if ($pythonOk) { "ok" } else { "failed" }
}

if (-not $SkipFrontend) {
    $frontendOk = Test-FrontendEnvironment
    if (-not $frontendOk -and $Repair) {
        Repair-FrontendEnvironment
        $frontendOk = Test-FrontendEnvironment
    }
    $result.frontend = if ($frontendOk) { "ok" } else { "failed" }
}

$result | ConvertTo-Json -Compress

if ($result.Values -contains "failed") {
    if (-not $Repair) {
        Write-Error "Development environment check failed. Re-run with: powershell -ExecutionPolicy Bypass -File scripts\ensure_dev_environment.ps1 -Repair. If .venv points to a removed Python install, set PYTHON_EXE to a usable Python 3.12+ executable first."
    }
    exit 1
}
