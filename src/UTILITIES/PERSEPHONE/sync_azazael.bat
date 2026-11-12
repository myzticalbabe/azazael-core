@echo off
REM =============================================================================
REM AZAzael <-> PERSEPHONE Synchronization Script
REM
REM Sync files between Azazael and PERSEPHONE for cross-platform development
REM
REM Usage:
REM   sync_to_persephone.bat    - Sync Azazael -> PERSEPHONE
REM   sync_from_persephone.bat - Sync PERSEPHONE -> Azazael
REM =============================================================================

setlocal EnableDelayedExpansion

REM Configuration
set "Azazael_ROOT=C:\Users\clint\Desktop\Azazael"
set "PERSEPHONE_ROOT=C:\Users\clint\Desktop\PERSEPHONE"
set "SYNC_LOG=%TEMP%\sync_%date:~-4,4%%date:~-10,2%%date:~-7,2%_%time:~0,2%%time:~3,2%.log"

echo =============================================================================
echo Azazael <-> PERSEPHONE Sync
echo =============================================================================
echo.
echo Azazael:  %Azazael_ROOT%
echo PERSEPHONE: %PERSEPHONE_ROOT%
echo.
echo Options:
echo   [1] Sync Azazael -> PERSEPHONE
echo   [2] Sync PERSEPHONE -> Azazael
echo   [3] Bidirectional Sync
echo   [4] Compare Only (Dry Run)
echo   [5] Exit
echo.
set /p CHOICE="Select option [1-5]: "

if "%CHOICE%"=="1" goto SYNC_TO_PERS
if "%CHOICE%"=="2" goto SYNC_FROM_PERS
if "%CHOICE%"=="3" goto SYNC_BOTH
if "%CHOICE%"=="4" goto DRY_RUN
if "%CHOICE%"=="5" goto EXIT

echo Invalid choice.
goto END

:SYNC_TO_PERS
echo.
echo Syncing Azazael -> PERSEPHONE...
echo Log: %SYNC_LOG%
echo.
robocopy "%Azazael_ROOT%" "%PERSEPHONE_ROOT%" /E /Z /B /MT:8 /XF "*.pyc" "__pycache__" "*.log" "node_modules" /LOG+:%SYNC_LOG%
echo.
echo Sync complete. Check log for details.
goto END

:SYNC_FROM_PERS
echo.
echo Syncing PERSEPHONE -> Azazael...
echo Log: %SYNC_LOG%
echo.
robocopy "%PERSEPHONE_ROOT%" "%Azazael_ROOT%" /E /Z /B /MT:8 /XF "*.pyc" "__pycache__" "*.log" "node_modules" /LOG+:%SYNC_LOG%
echo.
echo Sync complete. Check log for details.
goto END

:SYNC_BOTH
echo.
echo Performing bidirectional sync...
echo.
echo Step 1: Azazael -> PERSEPHONE
robocopy "%Azazael_ROOT%" "%PERSEPHONE_ROOT%" /E /Z /B /MT:8 /XF "*.pyc" "__pycache__" "*.log" "node_modules" /LOG+:%SYNC_LOG%
echo.
echo Step 2: PERSEPHONE -> Azazael
robocopy "%PERSEPHONE_ROOT%" "%Azazael_ROOT%" /E /Z /B /MT:8 /XF "*.pyc" "__pycache__" "*.log" "node_modules" /LOG+:%SYNC_LOG%
echo.
echo Bidirectional sync complete.
goto END

:DRY_RUN
echo.
echo Dry run - comparing directories only...
echo.
echo Azazael -> PERSEPHONE comparison:
robocopy "%Azazael_ROOT%" "%PERSEPHONE_ROOT%" /E /Z /B /L /XJ /XF "*.pyc" "__pycache__" "*.log" "node_modules"
echo.
echo PERSEPHONE -> Azazael comparison:
robocopy "%PERSEPHONE_ROOT%" "%Azazael_ROOT%" /E /Z /B /L /XJ /XF "*.pyc" "__pycache__" "*.log" "node_modules"
echo.
echo Dry run complete. No files were copied.
goto END

:EXIT
echo Exiting...
goto END

:END
echo.
echo Done.
endlocal
pause
