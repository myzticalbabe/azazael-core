<#
.SYNOPSIS
    Azazael Unified - PERSEPHONE WSL Sync
    
.DESCRIPTION
    Syncs files between Windows and WSL for PERSEPHONE cross-platform development

.EXAMPLE
    .\sync_wsl.ps1 [-ToWSL] [-FromWSL] [-Bidirectional]
#>

param(
    [switch]$ToWSL,
    [switch]$FromWSL,
    [switch]$Bidirectional
)

$ErrorActionPreference = "Stop"

$UnifiedRoot = "C:\Users\clint\Desktop\Azazael_UNIFIED"
$WSLRoot = "/mnt/c/Users/clint/Desktop/Azazael_UNIFIED"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Azazael - PERSEPHONE WSL Sync" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Define sync targets
$SyncTargets = @(
    @{
        Windows = "$UnifiedRoot\UTILITIES\PERSEPHONE"
        WSL = "$WSLRoot/UTILITIES/PERSEPHONE"
        Name = "PERSEPHONE"
    },
    @{
        Windows = "$UnifiedRoot\ORCHESTRATION\scripts"
        WSL = "$WSL_ROOT/ORCHESTRATION/scripts"
        Name = "Scripts"
    },
    @{
        Windows = "$UnifiedRoot\SCRIPTS"
        WSL = "$WSL_ROOT/SCRIPTS"
        Name = "Core Scripts"
    }
)

function Sync-ToWSL {
    param([string]$WindowsPath, [string]$WSLPath, [string]$Name)
    
    Write-Host "[$Name] Windows -> WSL..." -ForegroundColor Cyan
    
    # Convert paths
    $wslConverted = $WSLPath -replace '/mnt/c/', '/c/'
    
    # Use wsl.exe to copy
    $cmd = "cp -rf `"$wslConverted`" `"$WSLPath`" 2>&1"
    $result = wsl.exe $cmd
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] $Name synced to WSL" -ForegroundColor Green
    }
    else {
        Write-Host "  [WARN] $name sync had issues: $result" -ForegroundColor Yellow
    }
}

function Sync-FromWSL {
    param([string]$WindowsPath, [string]$WSLPath, [string]$Name)
    
    Write-Host "[$Name] WSL -> Windows..." -ForegroundColor Cyan
    
    # Convert WSL path to Windows
    $winPath = $WSLPath -replace '/mnt/c/', 'C:/'
    
    # Use wsl.exe to copy from WSL to Windows
    $cmd = "cp -rf `"$WSLPath`" `"$winPath`" 2>&1"
    $result = wsl.exe $cmd
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] $Name synced to Windows" -ForegroundColor Green
    }
    else {
        Write-Host "  [WARN] $name sync had issues: $result" -ForegroundColor Yellow
    }
}

function Sync-Bidirectional {
    param([string]$WindowsPath, [string]$WSLPath, [string]$Name)
    
    Write-Host "[$Name] Bidirectional sync..." -ForegroundColor Cyan
    
    # Get file counts
    $winFiles = Get-ChildItem $WindowsPath -Recurse -File -ErrorAction SilentlyContinue | Measure-Object | Select-Object -ExpandProperty Count
    $wslFiles = (wsl.exe "find `"$WSLPath`" -type f 2>/dev/null | wc -l").Trim()
    
    Write-Host "  Windows: $winFiles files" -ForegroundColor White
    Write-Host "  WSL: $wslFiles files" -ForegroundColor White
    
    # Sync newer files
    Sync-ToWSL -WindowsPath $WindowsPath -WSLPath $WSLPath -Name $Name
    Sync-FromWSL -WindowsPath $WindowsPath -WSLPath $WSLPath -Name $Name
}

# Determine sync direction
if ($ToWSL -or (-not $FromWSL -and -not $Bidirectional)) {
    foreach ($target in $SyncTargets) {
        Sync-ToWSL -WindowsPath $target.Windows -WSLPath $target.WSL -Name $target.Name
    }
}

if ($FromWSL) {
    foreach ($target in $SyncTargets) {
        Sync-FromWSL -WindowsPath $target.Windows -WSLPath $target.WSL -Name $target.Name
    }
}

if ($Bidirectional) {
    foreach ($target in $SyncTargets) {
        Sync-Bidirectional -WindowsPath $target.Windows -WSLPath $target.WSL -Name $target.Name
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Sync Complete" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Show next steps
Write-Host "WSL Commands:" -ForegroundColor Cyan
Write-Host "  wsl cd /mnt/c/Users/clint/Desktop/Azazael_UNIFIED" -ForegroundColor White
Write-Host "  python3 SCRIPTS/environ_updater.py" -ForegroundColor White
Write-Host ""
