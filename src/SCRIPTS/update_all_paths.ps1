<#
.SYNOPSIS
    Update all internal paths after moving to unified structure
#>

$ErrorActionPreference = "Stop"

$UnifiedRoot = "C:\Users\clint\Desktop\Azazael_UNIFIED"

$oldPaths = @{
    "C:/Users/clint/Desktop/WORMZ"         = "C:/Users/clint/Desktop/Azazael_UNIFIED/CORE/WORMS"
    "C:/Users/clint/Desktop/METAWORMS"    = "C:/Users/clint/Desktop/Azazael_UNIFIED/META"
    "C:/Users/clint/Desktop/HYDRA"        = "C:/Users/clint/Desktop/Azazael_UNIFIED/HYDRA"
    "C:/Users/clint/Desktop/TERENCE"      = "C:/Users/clint/Desktop/Azazael_UNIFIED/UTILITIES/TERENCE"
    "C:/Users/clint/Desktop/PERSEPHONE"   = "C:/Users/clint/Desktop/Azazael_UNIFIED/UTILITIES/PERSEPHONE"
    "C:/Users/clint/Desktop/POINDEXTER"   = "C:/Users/clint/Desktop/Azazael_UNIFIED/UTILITIES/POINDEXTER"
    "C:/Users/clint/Desktop/TARBABY"      = "C:/Users/clint/Desktop/Azazael_UNIFIED/UTILITIES/TARBABY"
    "C:/Users/clint/Desktop/KLINK"        = "C:/Users/clint/Desktop/Azazael_UNIFIED/UTILITIES/KLINK"
    "C:/Users/clint/Desktop/BIBL"         = "C:/Users/clint/Desktop/Azazael_UNIFIED/UTILITIES/BIBL"
    "C:/Users/clint/Desktop/Azazael"      = "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION"
}

$filesUpdated = 0
$filesChecked = 0

Write-Host "Azazael Unified - Path Updater" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

Get-ChildItem $UnifiedRoot -Recurse -Include *.py, *.md, *.bat, *.json, *.yaml -ErrorAction SilentlyContinue | ForEach-Object {
    $filesChecked++
    $filePath = $_.FullName
    $content = Get-Content -Path $filePath -Raw -ErrorAction SilentlyContinue
    
    if ($content) {
        $original = $content
        foreach ($oldPath in $oldPaths.Keys) {
            $content = $content -replace [regex]::Escape($oldPath), $oldPaths[$oldPath]
        }
        if ($content -ne $original) {
            $content | Set-Content -Path $filePath -Encoding UTF8
            $filesUpdated++
            Write-Host "Updated: $($_.Name)" -ForegroundColor Cyan
        }
    }
}

Write-Host ""
Write-Host "Path update complete!" -ForegroundColor Green
Write-Host "Files checked: $filesChecked" -ForegroundColor White
Write-Host "Files updated: $filesUpdated" -ForegroundColor Green
