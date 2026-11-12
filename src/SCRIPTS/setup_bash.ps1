<#
.SYNOPSIS
    Azazael Unified - Configure Git Bash
    
.DESCRIPTION
    Installs Windows helper functions for Git Bash to fix permission issues

.EXAMPLE
    .\setup_bash.ps1
#>

$ErrorActionPreference = "Continue"

$HOME = $Env:HOME
if (-not $HOME) { $HOME = "$Env:USERPROFILE" }

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Azazael Unified - Git Bash Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[INFO] HOME: $HOME" -ForegroundColor White
Write-Host ""

# Check for Git Bash
$gitBashPath = @(
    "$HOME\AppData\Local\Programs\Git\bin\bash.exe",
    "$Env:ProgramFiles\Git\bin\bash.exe",
    "$Env:ProgramFiles (x86)\Git\bin\bash.exe",
    "$Env:ProgramFiles\Git\usr\bin\bash.exe"
)

$foundBash = $false
foreach ($path in $gitBashPath) {
    if (Test-Path $path) {
        Write-Host "[FOUND] Git Bash: $path" -ForegroundColor Green
        $foundBash = $true
        break
    }
}

if (-not $foundBash) {
    Write-Host "[WARN] Git Bash not found in standard locations." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Please ensure Git for Windows is installed." -ForegroundColor White
    Write-Host "Download: https://git-scm.com/download/win" -ForegroundColor Cyan
}

# Create .bashrc.d directory
$ = "$HOME\.bashrcDbashrc.d"
if (-not (Test-Path $bashrcD)) {
    New-Item -ItemType Directory -Path $bashrcD -Force | Out-Null
    Write-Host "[CREATE] Directory: $bashrcD" -ForegroundColor Green
}
else {
    Write-Host "[EXISTS] Directory: $bashrcD" -ForegroundColor Gray
}

# Create Windows helpers script
$helpersContent = @"
#!/bin/bash
# Windows File Helpers for Git Bash
# Fixes permission issues with Windows files

# Convert path to Windows format
to_winpath() {
    cygpath -w "\$1" 2>/dev/null || echo "\$1"
}

# Delete Windows files
rm-win() {
    if [ -z "\$1" ]; then
        echo "Usage: rm-win <file>"
        return 1
    fi
    winpath=\$(cygpath -w "\$1" 2>/dev/null)
    [ -z "\$winpath" ] && winpath="\$1"
    powershell.exe -Command "Remove-Item -Force -Recurse '\$winpath'" 2>/dev/null
    echo "Deleted: \$1"
}

# Rename Windows files
mv-win() {
    if [ -z "\$1" ] || [ -z "\$2" ]; then
        echo "Usage: mv-win <old> <new>"
        return 1
    fi
    winpath1=\$(cygpath -w "\$1" 2>/dev/null)
    winpath2=\$(cygpath -w "\$2" 2>/dev/null)
    [ -z "\$winpath1" ] && winpath1="\$1"
    [ -z "\$winpath2" ] && winpath2="\$2"
    powershell.exe -Command "Rename-Item -Path '\$winpath1' -NewName '\$winpath2'" 2>/dev/null
    echo "Renamed: \$1 -> \$2"
}

# Create directory on Windows
mkdr-win() {
    if [ -z "\$1" ]; then
        echo "Usage: mkdr-win <dir>"
        return 1
    fi
    winpath=\$(cygpath -w "\$1" 2>/dev/null)
    [ -z "\$winpath" ] && winpath="\$1"
    powershell.exe -Command "New-Item -ItemType Directory -Force '\$winpath'" 2>/dev/null
    echo "Created: \$1"
}

# Open in VS Code
code-win() {
    if [ -z "\$1" ]; then
        echo "Usage: code-win <file>"
        return 1
    fi
    winpath=\$(cygpath -w "\$1" 2>/dev/null)
    [ -z "\$winpath" ] && winpath="\$1"
    powershell.exe -Command "code '\$winpath'" 2>/dev/null
}

# Shortcuts
alias rm='rm-win'
alias mv='mv-win'
alias mkdir='mkdr-win'
alias del='rm-win'
alias ren='mv-win'
alias code='code-win'

# Azazael shortcuts
alias azreload='source ~/.bashrc'
alias azpaths='cat ~/.bashrc.d/windows-helpers.sh | grep "^alias"'
alias azconfig='code ~/.bashrc.d/windows-helpers.sh'
"@

$helpersPath = "$bashrcD\windows-helpers.sh"
$helpersContent | Out-File $helpersPath -Encoding ASCII -NoNewline
Write-Host "[CREATE] $helpersPath" -ForegroundColor Green

# Update .bashrc
$bashrcPath = "$HOME\.bashrc"
$bashrcContent = ""
if (Test-Path $bashrcPath) {
    $bashrcContent = Get-Content $bashrcPath -Raw
}

if ($bashrcContent -notmatch "windows-helpers.sh") {
    $addContent = @"

# Load Windows helpers for Git Bash
if [ -f "\$HOME/.bashrc.d/windows-helpers.sh" ]; then
    source "\$HOME/.bashrc.d/windows-helpers.sh"
fi
"@
    $addContent | Out-File $bashrcPath -Encoding ASCII -Append
    Write-Host "[UPDATE] Added to .bashrc" -ForegroundColor Green
}
else {
    Write-Host "[SKIP] .bashrc already configured" -ForegroundColor Gray
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Setup Complete!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host "  1. Restart Git Bash"
Write-Host "  2. Test: rm-win /c/Users/clint/Desktop/test.txt"
Write-Host ""
Write-Host "Available commands:" -ForegroundColor White
Write-Host "  rm <file>     - Delete file"
Write-Host "  mv <old> <new> - Rename file"
Write-Host "  mkdir <dir>   - Create directory"
Write-Host "  code <file>   - Open in VS Code"
Write-Host "  azreload      - Reload bashrc"
Write-Host "  azpaths       - Show shortcuts"
Write-Host ""
