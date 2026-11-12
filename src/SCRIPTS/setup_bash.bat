@echo off
REM Azazael Unified - Git Bash Setup (Windows)
REM 
REM Usage:
REM   1. Right-click and "Run as Administrator"
REM   2. OR open Git Bash as Administrator and run: bash setup_bash.sh

echo ========================================
echo   Azazael Unified - Git Bash Setup
echo ========================================
echo.
echo This script will:
echo   - Create .bashrc.d directory
echo   - Install Windows helper functions
echo   - Configure Git Bash to use PowerShell for file operations
echo.
echo.

REM Check if Git Bash is installed
if not exist "%USERPROFILE%\AppData\Local\Programs\Git" (
    echo [ERROR] Git Bash not found!
    echo Please install Git for Windows first.
    echo Download: https://git-scm.com/download/win
    pause
    exit /b 1
)

REM Find Git Bash
set GIT_BASH="%USERPROFILE%\AppData\Local\Programs\Git\bin\bash.exe"
if not exist %GIT_BASH% (
    set GIT_BASH="%ProgramFiles%\Git\bin\bash.exe"
)
if not exist %GIT_BASH% (
    set GIT_BASH="%ProgramFiles (x86)%\Git\bin\bash.exe"
)

echo [INFO] Using Git Bash: %GIT_BASH%
echo.

REM Run the setup script in Git Bash
echo [RUN] Running setup in Git Bash...
echo.
%GIT_BASH% -l -c "cd /c/Users/clint/Desktop/Azazael_UNIFIED && bash setup_bash.sh"
set EXIT_CODE=%errorlevel%

echo.
if %EXIT_CODE% equ 0 (
    echo [SUCCESS] Git Bash configured!
    echo.
    echo Next steps:
    echo   1. Close this window
    echo   2. Open Git Bash
    echo   3. Test: rm-win /c/Users/clint/Desktop/test.txt
) else (
    echo [WARNING] Setup completed with warnings
    echo Exit code: %EXIT_CODE%
)

echo.
pause
