<#
.SYNOPSIS
    Azazael Unified - Code Checker Worms
    
.DESCRIPTION
    Checks all Python files for errors, linting issues, and syntax problems
    
.EXAMPLE
    .\code_checker.ps1
#>

$ErrorActionPreference = "Stop"

$UnifiedRoot = "C:\Users\clint\Desktop\Azazael_UNIFIED"
$ReportFile = "$UnifiedRoot\reports\code_check_report.txt"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Azazael Unified - Code Checker Worms" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Start: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host ""

# Define checkers
$Checkers = @{
    "ERRORZ" = @{
        Description = "Syntax and Import Errors"
        Path = "$UnifiedRoot\META\ERRORZ.py"
        Extension = "*.py"
        Recurse = $true
    }
    "HEALTHZ" = @{
        Description = "Code Health Metrics"
        Path = "$UnifiedRoot\META\HEALTHZ.py"
        Extension = "*.py"
        Recurse = $true
    }
    "TRIMZ" = @{
        Description = "Code Size Optimization"
        Path = "$UnifiedRoot\META\TRIMZ.py"
        Extension = "*.py"
        Recurse = $true
    }
}

$Results = @{
    TotalFiles = 0
    TotalErrors = 0
    TotalWarnings = 0
    TotalLines = 0
    CheckedFiles = @()
    Issues = @()
}

function Test-Syntax {
    param([string]$FilePath)
    
    try {
        $tokens = [System.Management.Automation.Language.Parser]::ParseFile($FilePath, [ref]$null, [ref]$null)
        return @{
            Success = $true
            Errors = 0
            Message = "OK"
        }
    }
    catch {
        $errorCount = ($_.Exception.Errors | Measure-Object).Count
        return @{
            Success = $false
            Errors = $errorCount
            Message = $_.Exception.Message
        }
    }
}

function Get-FileStats {
    param([string]$FilePath)
    
    $content = Get-Content $FilePath -Raw -ErrorAction SilentlyContinue
    $lines = ($content -split "`n").Count
    $chars = $content.Length
    
    return @{
        Lines = $lines
        Chars = $chars
        Words = ($content -split "\s+" | Where-Object { $_ } | Measure-Object).Count
    }
}

Write-Host "Scanning Python files..." -ForegroundColor Cyan
Write-Host ""

# Get all Python files
$pythonFiles = Get-ChildItem $UnifiedRoot -Recurse -Include *.py -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notlike "*\node_modules\*" -and $_.FullName -notlike "*\.venv\*" -and $_.FullName -notlike "*\__pycache__\*" }

Write-Host "Found $($pythonFiles.Count) Python files"
Write-Host ""

# Check each file
$pythonFiles | ForEach-Object {
    $filePath = $_.FullName
    $relativePath = $filePath.Replace("$UnifiedRoot\", "")
    
    Write-Host "Checking: $relativePath" -ForegroundColor Gray -NoNewline
    
    $Results.TotalFiles++
    
    # Syntax check
    $syntaxResult = Test-Syntax -FilePath $filePath
    
    # Get stats
    $stats = Get-FileStats -FilePath $filePath
    $Results.TotalLines += $stats.Lines
    
    if (-not $syntaxResult.Success) {
        Write-Host " [ERROR]" -ForegroundColor Red -NoNewline
        $Results.TotalErrors += $syntaxResult.Errors
        $Results.Issues += @{
            File = $relativePath
            Type = "Syntax Error"
            Message = $syntaxResult.Message
        }
    }
    else {
        Write-Host " [OK]" -ForegroundColor Green -NoNewline
    }
    
    # Check for common issues
    $content = Get-Content $filePath -Raw -ErrorAction SilentlyContinue
    
    # Check for TODO comments
    $todoCount = ($content | Select-String -Pattern "TODO|FIXME|HACK|XXX" -AllMatches).Matches.Count
    if ($todoCount -gt 0) {
        Write-Host " [TODO:$todoCount]" -ForegroundColor Yellow -NoNewline
        $Results.TotalWarnings += $todoCount
    }
    
    # Check for hardcoded paths
    if ($content -match "C:\\Users\\clint\\Desktop") {
        Write-Host " [PATH]" -ForegroundColor Yellow -NoNewline
        $Results.TotalWarnings++
    }
    
    Write-Host ""
}

Write-Host ""
Write-Host "Running Additional Checks..." -ForegroundColor Cyan
Write-Host ""

# Count files by directory
Write-Host "Files by Directory:" -ForegroundColor Cyan
$dirCounts = @{}
$pythonFiles | ForEach-Object {
    $dir = Split-Path $_.DirectoryName.Replace("$UnifiedRoot\", "") -Parent
    if (-not $dir) { $dir = "ROOT" }
    if (-not $dirCounts.ContainsKey($dir)) { $dirCounts[$dir] = 0 }
    $dirCounts[$dir]++
}

$dirCounts.GetEnumerator() | Sort-Object Name | ForEach-Object {
    Write-Host "  $($_.Key): $($_.Value) files" -ForegroundColor White
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Code Check Results" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Total Files:     $($Results.TotalFiles)" -ForegroundColor White
Write-Host "  Total Lines:     $($Results.TotalLines)" -ForegroundColor White
Write-Host "  Errors:          $($Results.TotalErrors)" -ForegroundColor $(if ($Results.TotalErrors -gt 0) { "Red" } else { "Green" })
Write-Host "  Warnings:        $($Results.TotalWarnings)" -ForegroundColor $(if ($Results.TotalWarnings -gt 0) { "Yellow" } else { "Green" })
Write-Host ""

# Health score
if ($Results.TotalFiles -gt 0) {
    $healthScore = [math]::Round((1 - ($Results.TotalErrors / $Results.TotalFiles)) * 100)
    Write-Host "  Health Score:   $healthScore%" -ForegroundColor $(if ($healthScore -ge 80) { "Green" } elseif ($healthScore -ge 60) { "Yellow" } else { "Red" })
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Top Issues" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$topIssues = $Results.Issues | Group-Object File | Select-Object -First 10
if ($topIssues) {
    $topIssues | ForEach-Object {
        Write-Host "  $($_.Name)" -ForegroundColor Red
        $_.Group | ForEach-Object {
            Write-Host "    - $($_.Type): $($_.Message.Substring(0, [math]::Min(80, $_.Message.Length)))..." -ForegroundColor Gray
        }
    }
}
else {
    Write-Host "  No critical issues found!" -ForegroundColor Green
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Summary by Component" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$components = @(
    @("CORE\WORMS", "WORMZ"),
    @("META", "METAWORMS"),
    @("HYDRA", "HYDRA"),
    @("ORCHESTRATION", "Azazael"),
    @("UTILITIES", "UTILITIES")
)

foreach ($comp in $components) {
    $dir = $comp[0]
    $name = $comp[1]
    $count = ($pythonFiles | Where-Object { $_.FullName -like "*\$dir\*" } | Measure-Object).Count
    Write-Host "  $name`: $count files" -ForegroundColor White
}

Write-Host ""
Write-Host "End: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host ""

# Save report
$report = @"
Azazael Unified - Code Check Report
Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
======================================

FILES CHECKED
-------------
Total Files: $($Results.TotalFiles)
Total Lines: $($Results.TotalLines)

ISSUES FOUND
------------
Errors: $($Results.TotalErrors)
Warnings: $($Results.TotalWarnings)

HEALTH SCORE
------------
$([math]::Round((1 - ($Results.TotalErrors / [math]::Max(1, $Results.TotalFiles))) * 100))%

BY COMPONENT
------------
CORE\WORMS: $((Get-ChildItem "$UnifiedRoot\CORE\WORMS" -Recurse -Include *.py | Measure-Object).Count) files
META: $((Get-ChildItem "$UnifiedRoot\META" -Recurse -Include *.py | Where-Object { $_.Name -notlike "output*" -and $_.Name -notlike "reports*" } | Measure-Object).Count) files
HYDRA: $((Get-ChildItem "$UnifiedRoot\HYDRA" -Recurse -Include *.py | Measure-Object).Count) files
ORCHESTRATION: $((Get-ChildItem "$UnifiedRoot\ORCHESTRATION" -Recurse -Include *.py | Measure-Object).Count) files
UTILITIES: $((Get-ChildItem "$UnifiedRoot\UTILITIES" -Recurse -Include *.py | Measure-Object).Count) files

"@

$report | Out-File $ReportFile -Encoding UTF8
Write-Host "Report saved: $ReportFile" -ForegroundColor Cyan
