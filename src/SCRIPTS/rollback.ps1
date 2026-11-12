<#
.SYNOPSIS
    Rollback Azazael Unified Migration
    
.DESCRIPTION
    Restores ecosystem folders from backup and removes unified structure
    WARNING: This will delete the Azazael_UNIFIED folder!

.EXAMPLE
    .\rollback.ps1
#>

$ErrorActionPreference = "Stop"

$backupRoot = Get-ChildItem -Directory "C:\Users\clint\Desktop\Azazael_BACKUP_*" | Sort-Object Name -Descending | Select-Object -First 1

if (-not $backupRoot) {
    Write-Error "No backup found!"
    exit 1
}

Write-Host "Using backup: $backupRoot" -ForegroundColor Yellow
Write-Host ""
Read-Host "Press ENTER to continue (Ctrl+C to cancel)"

# Move folders back
$folders = @("WORMZ", "METAWORMS", "HYDRA", "TERENCE", "PERSEPHONE", "POINDEXTER", "TARBABY", "KLINK", "BIBL", "SHORTY", "CODEX")

foreach ($folder in $folders) {
    $source = "$backupRoot\C:\Users\clint\Desktop\Azazael_UNIFIED\CORE\WORMS"
    $dest = "C:\Users\clint\Desktop\C:\Users\clint\Desktop\Azazael_UNIFIED\CORE\WORMS"
    
    if (Test-Path $source) {
        Write-Host "Restoring: $folder..." -NoNewline
        if (Test-Path $dest) {
            Remove-Item $dest -Recurse -Force
        }
        Copy-Item $source $dest -Recurse
        Write-Host " Done" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Rollback complete!" -ForegroundColor Green
Write-Host "Backup location: $backupRoot"
