@echo off
REM ============================================================================
REM Generate Api Docs
REM Location: C:\Users\clint\Desktop\SHORTY\generate_api_docs.bat
REM ============================================================================
REM Generated: 2026-02-10
REM ============================================================================

setlocal EnableDelayedExpansion

REM Configuration
set "BASE_DIR=C:\Users\clint\Desktop\Azazael"
set "PYTHON_CMD=python"
set "LOG_DIR=%BASE_DIR%\logs"

REM Create log directory if not exists
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM ============================================================================
REM Main Execution
REM ============================================================================

echo [%TIME%] Starting Generate Api Docs... >> "%LOG_DIR%\bat_execution.log"

cd /d "%BASE_DIR%"

REM Validate environment
if not exist "%BASE_DIR%" (
    echo [%TIME%] ERROR: Base directory not found!
    echo [%TIME%] ERROR: Base directory not found! >> "%LOG_DIR%\bat_execution.log"
    goto :ERROR
)

REM python scripts/validate_docs.py --generate-api ml_
python scripts/validate_docs.py --generate-api ml_platforms/harness.py
REM python scripts/validate_docs.py --generate-api ml_
python scripts/validate_docs.py --generate-api ml_platforms/config.py
REM python scripts/validate_docs.py --generate-api ml_
python scripts/validate_docs.py --generate-api ml_platforms/diagnostics.py

echo [%TIME%] Completed successfully >> "%LOG_DIR%\bat_execution.log"
exit /b 0

:ERROR
echo [%TIME%] Failed with exit code %ERRORLEVEL% >> "%LOG_DIR%\bat_execution.log"
exit /b 1
