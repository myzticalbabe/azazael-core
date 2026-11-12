<#
.SYNOPSIS
    Azazael Unified - Configure Git Bash (Simple)
    
.DESCRIPTION
    Installs Windows helper functions for Git Bash

.EXAMPLE
    .\setup_bash_simple.ps1
#>

$ErrorActionPreference = "Continue"

$bashrcPath = "$Env:USERPROFILE\.bashrc"
$bashrcD = "$Env:USERPROFILE\.bashrc.d"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Azazael Unified - Git Bash Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[INFO] HOME: $Env:USERPROFILE" -ForegroundColor White
Write-Host ""

# Create .bashrc.d directory
if (-not (Test-Path $bashrcD)) {
    New-Item -ItemType Directory -Path $bashrcD -Force | Out-Null
    Write-Host "[CREATE] Directory: $bashrcD" -ForegroundColor Green
}

# Create helpers file
$helpersPath = "$bashrcD\windows-helpers.sh"
$helpersContent = @"
#!/bin/bash
# Windows File Helpers for Git Bash

to_winpath() {
    cygpath -w "$1" 2>/dev/null || echo "$1"
}

rm-win() {
    [ -z "$1" ] && echo "Usage: rm-win <file>" && return 1
    winpath=`$(cygpath -w "$1" 2>/dev/null)
    [ -z "$winpath" ] && winpath="$1"
    powershell.exe -Command "Remove-Item -Force -Recurse '$winpath'" 2>/dev/null
    echo "Deleted: $1"
}

mv-win() {
    [ -z "$1" ] || [ -z "$2" ] && echo "Usage: mv-win <old> <new>" && return 1
    winpath1=`$(cygpath -w "$1" 2>/dev/null)
    winpath2=`$(cygpath -w "$2" 2>/dev/null)
    [ -z "$winpath1" ] && winpath1="$1"
    [ -z "$winpath2" ] && winpath2="$2"
    powershell.exe -Command "Rename-Item -Path '$winpath1' -NewName '$winpath2'" 2>/dev/null
    echo "Renamed: $1 -> $2"
}

mkdr-win() {
    [ -z "$1" ] && echo "Usage: mkdr-win <dir>" && return 1
    winpath=`$(cygpath -w "$1" 2>/dev/null)
    [ -z "$winpath" ] && winpath="$1"
    powershell.exe -Command "New-Item -ItemType Directory -Force '$winpath'" 2>/dev/null
    echo "Created: $1"
}

code-win() {
    [ -z "$1" ] && echo "Usage: code-win <file>" && return 1
    winpath=`$(cygpath -w "$1" 2>/dev/null)
    [ -z "$winpath" ] && winpath="$1"
    powershell.exe -Command "code '$winpath'" 2>/dev/null
}

alias rm='rm-win'
alias mv='mv-win'
alias mkdir='mkdr-win'
alias del='rm-win'
alias ren='mv-win'
alias code='code-win'
alias azreload='source ~/.bashrc'
alias azpaths='cat ~/.bashrc.d/windows-helpers.sh | grep "^alias"'
"@

$helpersContent | Out-File $helpersPath -Encoding ASCII
Write-Host "[CREATE] $helpersPath" -ForegroundColor Green

# Update .bashrc
if (Test-Path $bashrcPath) {
    $bashrcContent = Get-Content $bashrcPath -Raw
    if ($bashrcContent -notmatch "windows-helpers.sh") {
        @"

# Load Windows helpers for Git Bash
if [ -f "$HOME/.bashrc.d/windows-helpers.sh" ]; then
    source "$HOME/.bashrc.d/windows-helpers.sh"
fi
"@ | Out-File $bashrcPath -Encoding ASCII -Append
        Write-Host "[UPDATE] Added to .bashrc" -ForegroundColor Green
    } else {
        Write-Host "[SKIP] .bashrc already configured" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Setup Complete!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "In Git Bash, run:" -ForegroundColor White
Write-Host "  source ~/.bashrc" -ForegroundColor Yellow
Write-Host "  azpaths" -ForegroundColor Yellow
Write-Host ""
