<#
.SYNOPSIS
    Azazael Unified - Hash Verification
    
.DESCRIPTION
    Generates and verifies file hashes for integrity check

.EXAMPLE
    .\hash_test.ps1 [-Verify]
#>

param(
    [switch]$Verify
)

$ErrorActionPreference = "Continue"

$UnifiedRoot = "C:\Users\clint\Desktop\Azazael_UNIFIED"
$HashFile = "$UnifiedRoot\.hash_manifest.txt"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Azazael Unified - Hash Verification" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if ($Verify) {
    # Verify existing hashes
    Write-Host "Verifying file integrity..." -ForegroundColor Cyan
    Write-Host ""
    
    if (-not (Test-Path $HashFile)) {
        Write-Error "Hash file not found: $HashFile"
        Write-Host ""
        Write-Host "Run without -Verify to generate hashes first."
        exit 1
    }
    
    $storedHashes = @{}
    Get-Content $HashFile | ForEach-Object {
        if ($_ -match "^(.+?) (.+)$") {
            $storedHashes[$matches[2]] = $matches[1]
        }
    }
    
    $passed = 0
    $failed = 0
    $missing = 0
    
    Get-ChildItem $UnifiedRoot -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
        $relativePath = $_.FullName.Replace("$UnifiedRoot\", "")
        $currentHash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
        
        if ($storedHashes.ContainsKey($relativePath)) {
            $storedHash = $storedHashes[$relativePath]
            if ($currentHash -eq $storedHash) {
                $passed++
            }
            else {
                Write-Host "  [MODIFIED] $relativePath" -ForegroundColor Red
                $failed++
            }
        }
        else {
            Write-Host "  [NEW] $relativePath" -ForegroundColor Yellow
            $missing++
        }
    }
    
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Verification Results" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Passed:   $passed" -ForegroundColor Green
    Write-Host "  Failed:   $failed" -ForegroundColor $(if ($failed -gt 0) { "Red" } else { "Green" })
    Write-Host "  New:      $missing" -ForegroundColor Yellow
    Write-Host ""
    
    if ($failed -eq 0) {
        Write-Host "  [SUCCESS] All verified files match!" -ForegroundColor Green
    }
    else {
        Write-Host "  [FAILURE] $failed files have been modified!" -ForegroundColor Red
    }
}
else {
    # Generate new hashes
    Write-Host "Generating file hashes..." -ForegroundColor Cyan
    Write-Host ""
    
    $hashes = @()
    $total = 0
    
    Get-ChildItem $UnifiedRoot -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
        $relativePath = $_.FullName.Replace("$UnifiedRoot\", "")
        $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
        $hashes += "$hash $relativePath"
        $total++
        
        if ($total % 50 -eq 0) {
            Write-Host "  Processed: $total files..." -ForegroundColor Gray
        }
    }
    
    $hashes | Sort-Object | Out-File $HashFile -Encoding ASCII
    
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Hash Generation Complete" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Total files hashed: $total" -ForegroundColor Green
    Write-Host "  Hash file: $HashFile" -ForegroundColor White
    Write-Host ""
    Write-Host "  Run with -Verify to check integrity later."
}
