@echo off
REM ==========================================================================
REM ONE_CLICK.cmd -- the front door named by the canon block (HED-8 / ONE CLICK).
REM
REM A double-click is one click: this runs tools/one_click.py on the production
REM 3.15t interpreter and stays open so the verdict is readable afterwards.
REM Called with arguments (a shortcut, a scheduled task, a lane script) it
REM returns that exit code and does NOT pause.
REM
REM   0 PASS   1 FAIL   2 BLIND      (BLIND = a step could not be performed)
REM ==========================================================================
setlocal
cd /d "%~dp0"

set "PY=C:\Azazael\.venvs\py315t\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "%~dp0tools\one_click.py" %*
set "RC=%ERRORLEVEL%"

if "%~1"=="" (
    echo.
    echo one_click exit code: %RC%   ^(0 PASS ^| 1 FAIL ^| 2 BLIND^)
    pause
)
exit /b %RC%
