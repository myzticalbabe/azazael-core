@echo off
REM HYDRA ML Cluster - Direct Runner
REM Usage: run_hydra.bat

echo ========================================
echo   Azazael - HYDRA ML Cluster
echo ========================================
echo.

set HYDRA_ROOT=C:\Users\clint\Desktop\Azazael_UNIFIED\HYDRA
set ML_PLATFORMS=%HYDRA_ROOT%\ml_platforms

echo [INFO] HYDRA Root: %HYDRA_ROOT%
echo [INFO] ML Platforms: %ML_PLATFORMS%
echo.

REM Change to ml_platforms directory for imports
cd /d "%ML_PLATFORMS%"

REM Run harness directly (has built-in path fix)
echo [RUN] Starting HYDRA ML Cluster...
echo.

python3.11 harness.py status --json

echo.
echo ========================================
echo   HYDRA Components
echo ========================================
echo.
echo Available commands:
echo   analyze      - Framework analysis
echo   convert      - Model conversion
echo   recommend    - Framework recommendations
echo   monitor      - Health monitoring
echo   status       - System status
echo   models       - List models
echo   verify       - Full verification
echo.

echo ========================================
echo   HYDRA Session Complete
echo ========================================
echo.
pause
