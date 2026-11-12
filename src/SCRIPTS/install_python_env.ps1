<#
.SYNOPSIS
    Azazael Unified - Create Standalone Python Environment
    
.DESCRIPTION
    Creates a self-sustaining Python environment with all ML frameworks
    for HYDRA cluster operations

.EXAMPLE
    .\install_python_env.ps1
#>

$ErrorActionPreference = "Stop"

$AzazaelRoot = "C:\Users\clint\Desktop\Azazael_UNIFIED"
$EnvDir = "$AzazaelRoot\.venv"
$PythonVersion = "3.11.9"

Write-Host "=" * 70 -ForegroundColor Cyan
Write-Host "  Azazael - Standalone Python Environment" -ForegroundColor Cyan
Write-Host "=" * 70 -ForegroundColor Cyan
Write-Host ""
Write-Host "Target: $EnvDir" -ForegroundColor White
Write-Host "Python: $PythonVersion" -ForegroundColor White
Write-Host ""

# Step 1: Download Python 3.11
$PythonUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
$PythonInstaller = "$EnvTEMP\python311_installer.exe"

Write-Host "[STEP 1] Downloading Python 3.11.9..." -ForegroundColor Cyan
if (-not (Test-Path $PythonInstaller)) {
    try {
        $webClient = New-Object System.Net.WebClient
        $webClient.DownloadFile($PythonUrl, $PythonInstaller)
        Write-Host "  Downloaded: $PythonInstaller" -ForegroundColor Green
    }
    catch {
        Write-Host "  Download failed, trying chocolatey..." -ForegroundColor Yellow
        $PythonInstaller = $null
    }
}
else {
    Write-Host "  Found cached installer" -ForegroundColor Gray
}

# Step 2: Install Python
Write-Host ""
Write-Host "[STEP 2] Installing Python..." -ForegroundColor Cyan

if ($PythonInstaller -and (Test-Path $PythonInstaller)) {
    # Install Python silently
    Write-Host "  Running installer..."
    $proc = Start-Process -FilePath $PythonInstaller -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1" -Wait -PassThru
    Write-Host "  Exit code: $($proc.ExitCode)" -ForegroundColor Gray
}
else {
    # Try winget
    Write-Host "  Trying winget..."
    winget install Python.Python.3.11 --silent 2>&1 | Out-Null
}

# Step 3: Create virtual environment
Write-Host ""
Write-Host "[STEP 3] Creating virtual environment..." -ForegroundColor Cyan

$python311Path = "C:\Users\clint\AppData\Local\Programs\Python\Python311\python.exe"
if (-not (Test-Path $python311Path)) {
    $python311Path = "python311.exe"
}

# Remove old venv if exists
if (Test-Path $EnvDir) {
    Write-Host "  Removing old environment..."
    Remove-Item -Recurse -Force $EnvDir
}

Write-Host "  Creating new environment..."
& $python311Path -m venv $EnvDir

if (Test-Path "$EnvDir\Scripts\python.exe") {
    Write-Host "  Virtual environment created" -ForegroundColor Green
}
else {
    Write-Host "  Failed to create venv" -ForegroundColor Red
    exit 1
}

# Step 4: Upgrade pip
Write-Host ""
Write-Host "[STEP 4] Upgrading pip..." -ForegroundColor Cyan
& "$EnvDir\Scripts\pip.exe" install --upgrade pip 2>&1 | Out-Null
Write-Host "  Pip upgraded" -ForegroundColor Green

# Step 5: Install core packages
Write-Host ""
Write-Host "[STEP 5] Installing core packages..." -ForegroundColor Cyan

$CorePackages = @(
    "numpy>=1.24.0",
    "scipy>=1.10.0",
    "pandas>=2.0.0",
    "scikit-learn>=1.3.0",
    "matplotlib>=3.7.0",
    "seaborn>=0.12.0",
    "jupyter>=1.0.0",
    "ipython>=8.0.0"
)

foreach ($pkg in $CorePackages) {
    Write-Host "  Installing: $pkg" -ForegroundColor Gray
    & "$EnvDir\Scripts\pip.exe" install $pkg 2>&1 | Out-Null
}

Write-Host "  Core packages installed" -ForegroundColor Green

# Step 6: Install ML frameworks
Write-Host ""
Write-Host "[STEP 6] Installing ML frameworks (this may take a while)..." -ForegroundColor Cyan

$MLPackages = @(
    "torch>=2.0.0",
    "torchvision>=0.15.0",
    "torchaudio>=2.0.0",
    "tensorflow>=2.13.0",
    "jax>=0.4.0",
    "jaxlib>=0.4.0",
    "transformers>=4.30.0",
    "datasets>=2.14.0",
    "accelerate>=0.20.0",
    "optuna>=3.3.0",
    "ray[default]>=2.6.0",
    "mlflow>=2.7.0",
    "onnx>=1.14.0",
    "onnxruntime>=1.15.0",
    "xgboost>=2.0.0",
    "lightgbm>=4.0.0",
    "catboost>=1.2.0"
)

foreach ($pkg in $MLPackages) {
    Write-Host "  Installing: $pkg" -ForegroundColor Gray
    try {
        & "$EnvDir\Scripts\pip.exe" install $pkg 2>&1 | Out-Null
        Write-Host "    [OK]" -ForegroundColor Green
    }
    catch {
        Write-Host "    [WARN] Failed to install $pkg" -ForegroundColor Yellow
    }
}

# Step 7: Install Q-Learning and utilities
Write-Host ""
Write-Host "[STEP 7] Installing Azazael ML utilities..." -ForegroundColor Cyan

$AzazaelPackages = @(
    "qlearn>=0.1.0",
    "gym>=0.26.0",
    "gymnasium>=0.29.0",
    "stable-baselines3>=2.0.0",
    "ale-py>=0.8.0"
)

foreach ($pkg in $AzazaelPackages) {
    Write-Host "  Installing: $pkg" -ForegroundColor Gray
    try {
        & "$EnvDir\Scripts\pip.exe" install $pkg 2>&1 | Out-Null
        Write-Host "    [OK]" -ForegroundColor Green
    }
    catch {
        Write-Host "    [SKIP] $pkg not available" -ForegroundColor Yellow
    }
}

# Step 8: Create environment info
Write-Host ""
Write-Host "[STEP 8] Generating environment report..." -ForegroundColor Cyan

$Report = @"
# Azazael Python Environment Report
Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
Python: $PythonVersion
Location: $EnvDir

## Installed Packages
"@

& "$EnvDir\Scripts\pip.exe" freeze | Out-File "$EnvDir\requirements.txt" -Encoding UTF8
Get-Content "$EnvDir\requirements.txt" | ForEach-Object { $Report += "`n$_" }
$Report | Out-File "$EnvDir\environment_report.txt" -Encoding UTF8

Write-Host "  Report saved to: $EnvDir\environment_report.txt" -ForegroundColor Green

# Step 9: Test imports
Write-Host ""
Write-Host "[STEP 9] Testing imports..." -ForegroundColor Cyan

$TestModules = @(
    "numpy",
    "scipy", 
    "pandas",
    "sklearn",
    "torch",
    "tensorflow",
    "jax",
    "transformers"
)

$Passed = 0
$Failed = 0

foreach ($mod in $TestModules) {
    try {
        $version = & "$EnvDir\Scripts\python.exe" -c "import $mod; print($mod.__version__)" 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] ${mod}: $version" -ForegroundColor Green
            $Passed++
        }
        else {
            Write-Host "  [--] ${mod}: not installed" -ForegroundColor Gray
            $Failed++
        }
    }
    catch {
        Write-Host "  [--] ${mod}: error" -ForegroundColor Gray
        $Failed++
    }
}

# Step 10: Update HYDRA harness
Write-Host ""
Write-Host "[STEP 10] Updating HYDRA harness..." -ForegroundColor Cyan

$HarnessContent = @"
# Auto-generated by install_python_env.ps1
# Point to Azazael's standalone Python environment

SCRIPT_DIR=`"`$PSScriptRoot`"
ENV_DIR=`"$EnvDir`"
PYTHON_EXE=`"$ENV_DIR\Scripts\python.exe`"
PIP_EXE=`"$ENV_DIR\Scripts\pip.exe`"

`$Env:PATH = `$ENV_DIR + ";`$Env:PATH
"@

$HarnessContent | Out-File "$AzazaelRoot\HYDRA\python_env.ps1" -Encoding UTF8
Write-Host "  Created: HYDRA\python_env.ps1" -ForegroundColor Green

# Final summary
Write-Host ""
Write-Host "=" * 70 -ForegroundColor Cyan
Write-Host "  Environment Setup Complete!" -ForegroundColor Cyan
Write-Host "=" * 70 -ForegroundColor Cyan
Write-Host ""
Write-Host "  Location: $EnvDir" -ForegroundColor White
Write-Host "  Python:   $PythonVersion" -ForegroundColor White
Write-Host "  Tests:    $Passed passed, $Failed failed" -ForegroundColor $(if ($Failed -eq 0) { "Green" } else { "Yellow" })
Write-Host ""
Write-Host "Usage:" -ForegroundColor Cyan
Write-Host "  Run HYDRA: & '$EnvDir\Scripts\python.exe' harness.py status" -ForegroundColor White
Write-Host "  Activate:  $EnvDir\Scripts\activate.bat" -ForegroundColor White
Write-Host ""
Write-Host "Or run the launcher:" -ForegroundColor Cyan
Write-Host "  HYDRA\run_hydra.bat" -ForegroundColor White
