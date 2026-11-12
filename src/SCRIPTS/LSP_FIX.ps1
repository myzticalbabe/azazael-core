#!/usr/bin/env pwsh
# LSP Installation & Permission Fix for Azazael

Write-Host "=== AZAZAEL LSP & PERMISSION FIX ===`n" -ForegroundColor Cyan

# 1. Install LSP tools
Write-Host "[1/3] Installing LSP tools..." -ForegroundColor Yellow
$venvPython = "C:\Users\clint\Desktop\Azazael_UNIFIED\.venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    & $venvPython -m pip install --quiet pyright pylint mypy
    if ($LASTEXITCODE -eq 0) {
        Write-Host "   [OK] LSP tools installed" -ForegroundColor Green
    } else {
        Write-Host "   [ERROR] LSP install failed" -ForegroundColor Red
    }
} else {
    Write-Host "   [ERROR] venv Python not found: $venvPython" -ForegroundColor Red
}

# 2. Fix permissions
Write-Host "`n[2/3] Fixing folder permissions..." -ForegroundColor Yellow
$azazaelPath = "C:\Users\clint\Desktop\Azazael_UNIFIED"
icacls $azazaelPath /grant clint:F /T /Q 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "   [OK] Permissions fixed" -ForegroundColor Green
}

# 3. Generate LSP config
Write-Host "`n[3/3] Generating VS Code settings..." -ForegroundColor Yellow
$vscodeSettings = @{
    "python.defaultInterpreterPath" = $venvPython
    "python.languageServer" = "Pylance"
    "python.analysis.typeCheckingMode" = "basic"
    "python.analysis.diagnosticMode" = "workspace"
    "python.linting.enabled" = $true
    "python.linting.pylintEnabled" = $true
    "python.formatting.provider" = "none"
} | ConvertTo-Json -Depth 5

$settingsPath = "C:\Users\clint\.vscode\settings.json"
try {
    $existing = Get-Content $settingsPath -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
    if ($existing) {
        $existing.psobject.Properties | ForEach-Object { 
            if (-not $vscodeSettings.Contains($_.Name)) { 
                $vscodeSettings | Add-Member -NotePropertyName $_.Name -NotePropertyValue $_.Value 
            }
        }
    }
    $vscodeSettings | ConvertTo-Json -Depth 5 | Set-Content $settingsPath -Encoding UTF8
    Write-Host "   [OK] VS Code settings updated" -ForegroundColor Green
} catch {
    Write-Host "   [ERROR] Could not write settings: $_" -ForegroundColor Red
}

Write-Host "`n=== FIX COMPLETE ===" -ForegroundColor Cyan
Write-Host "Restart VS Code to apply changes" -ForegroundColor Yellow
