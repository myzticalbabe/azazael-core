@echo off
REM ============================================================
REM  Refresh Codebase Atlas — one click
REM  1. Runs graphify_refresh_all.sh (patch + refresh all repos
REM     + rebuild Cosmos viewers + refresh Desktop shortcut)
REM  2. Opens the atlas folder when done (unless --no-open)
REM
REM  Usage:
REM    refresh_atlas_and_open.bat                 normal (current user)
REM    refresh_atlas_and_open.bat --admin         elevate via UAC, then run
REM    refresh_atlas_and_open.bat --no-open       run silently, no explorer/pause
REM    refresh_atlas_and_open.bat --admin --no-open   admin + silent (scheduled tasks)
REM ============================================================
setlocal EnableDelayedExpansion

set "WANT_ADMIN="
set "WANT_NOOPEN="
:parse
if "%~1"=="" goto :parsed
if /i "%~1"=="--admin"    set "WANT_ADMIN=1"
if /i "%~1"=="--no-open"  set "WANT_NOOPEN=1"
shift
goto :parse
:parsed

REM --- detect elevation (net session fails for non-admin) ---
net session >nul 2>&1
if errorlevel 1 (set "ELEVATED=") else (set "ELEVATED=1")

REM --- if admin requested but not elevated, re-launch via UAC ---
REM     (original args are passed through so manual --admin still opens the
REM      atlas at the end, while a scheduled task's --admin --no-open stays silent)
if defined WANT_ADMIN (
  if not defined ELEVATED (
    echo  Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    exit /b 0
  )
)

if defined ELEVATED (set "TITLE=Refreshing Codebase Atlas [ADMIN]...") else (set "TITLE=Refreshing Codebase Atlas...")
title %TITLE%
echo.
echo  Refreshing Graphify Codebase Atlas - this can take 10-20 minutes.
if defined WANT_ADMIN echo  Mode: ADMINISTRATOR
if defined WANT_NOOPEN echo  Mode: silent (no window at the end)
echo.

"C:\Program Files\Git\bin\bash.exe" --login -c "bash /c/Azazael/azazael/scripts/graphify_refresh_all.sh"

if not defined WANT_NOOPEN (
  echo.
  echo  ============================================
  echo   Refresh finished. Opening atlas folder...
  echo  ============================================
  explorer "C:\Users\clint\Desktop\Graphify_Codebase_Atlas_2026-08-27"
  echo.
  pause
)
endlocal
