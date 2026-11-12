@echo off
REM SHORTY - Azazael Ecosystem Shortcut Manager
REM Centralized shortcut execution for Azazael ecosystem

set "Azazael_ROOT=%~dp0.."
set "SHORTY_PATH=%~dp0"
set "PYTHON_PATH=python"

if exist "C:\Azazael\.venvs\py315t\Scripts\python.exe" set "PYTHON_PATH=C:\Azazael\.venvs\py315t\Scripts\python.exe"
if not exist "C:\Azazael\.venvs\py315t\Scripts\python.exe" if exist "C:\Azazael\.venvs\py315\Scripts\python.exe" set "PYTHON_PATH=C:\Azazael\.venvs\py315\Scripts\python.exe"
if not exist "C:\Azazael\.venvs\py315t\Scripts\python.exe" if not exist "C:\Azazael\.venvs\py315\Scripts\python.exe" if exist "C:\Azazael\.venvs\py314m\Scripts\python.exe" set "PYTHON_PATH=C:\Azazael\.venvs\py314m\Scripts\python.exe"
REM AI Playground is a last resort only: it must not override a real Azazael venv.
if not exist "C:\Azazael\.venvs\py315t\Scripts\python.exe" if not exist "C:\Azazael\.venvs\py315\Scripts\python.exe" if not exist "C:\Azazael\.venvs\py314m\Scripts\python.exe" if exist "%USERPROFILE%\AppData\Local\Programs\AI Playground\python.exe" set "PYTHON_PATH=%USERPROFILE%\AppData\Local\Programs\AI Playground\python.exe"

echo ========================================
echo          Azazael SHORTY SHORTCUTS
echo ========================================
echo.
echo Available shortcuts:
echo.
echo [CORE]
echo   azazael        - Run Azazael CLI Assistant
echo   wormz          - Run WORMZ crawler
echo   systemz        - Run SYSTEMZ monitoring
echo.
echo [METAWORMS]
echo   metaworms      - Run METAWORMS orchestrator
echo   errorz         - Run ERRORZ crawler
echo   hydrometa      - Run HYDRAMETA optimizer
echo   metricz        - Run METRICZ telemetry
echo   telemetryz     - Run TELEMETRYZ tracer
echo.
echo [MANAGEMENT]
echo   update         - Update Azazael ecosystem
echo   health         - System health check
echo   status         - Full ecosystem status
echo   cleanup        - Clean temporary files
echo.
echo [UTILITIES]
echo   diag           - Run diagnostics
echo   install         - Auto-install dependencies
echo   backup         - Backup configuration
echo.
echo ========================================

if "%1"=="" goto :eof

set "COMMAND=%1"
shift

goto :run_%COMMAND%

:run_azazael
%PYTHON_PATH% "%Azazael_ROOT%\Azazael\Azazael_INTEGRATION.py" %*
goto :eof

:run_wormz
%PYTHON_PATH% "%Azazael_ROOT%\WORMZ\WORMZ.py" %*
goto :eof

:run_systemz
%PYTHON_PATH% "%Azazael_ROOT%\WORMZ\SYSTEMZ.py" %*
goto :eof

:run_metaworms
%PYTHON_PATH% "%Azazael_ROOT%\METAWORMS\METAWORMS.py" %*
goto :eof

:run_errorz
%PYTHON_PATH% "%Azazael_ROOT%\METAWORMS\ERRORZ.py" %*
goto :eof

:run_hydrometa
%PYTHON_PATH% "%Azazael_ROOT%\METAWORMS\HYDRAMETA.py" %*
goto :eof

:run_metricz
%PYTHON_PATH% "%Azazael_ROOT%\METAWORMS\METRICZ.py" %*
goto :eof

:run_telemetryz
%PYTHON_PATH% "%Azazael_ROOT%\METAWORMS\TELEMETRYZ.py" %*
goto :eof

:run_uvz
%PYTHON_PATH% "%Azazael_ROOT%\METAWORMS\UVZ.py" %*
goto :eof

:run_update
cd /d "%Azazael_ROOT%"
%PYTHON_PATH% -m pip install --upgrade -r requirements.txt
goto :eof

:run_health
%PYTHON_PATH% "%Azazael_ROOT%\WORMZ\SYSTEMZ.py" --status
%PYTHON_PATH% "%Azazael_ROOT%\METAWORMS\HEALTHZ.py"
goto :eof

:run_status
echo Gathering ecosystem status...
%PYTHON_PATH% "%Azazael_ROOT%\Azazael\Azazael_INTEGRATION.py" --status
goto :eof

:run_cleanup
echo Cleaning temporary files...
del /q "%Azazael_ROOT%\METAWORMS\output\*.tmp" 2>nul
del /q "%Azazael_ROOT%\WORMZ\__pycache__\*.pyc" 2>nul
del /q "%Azazael_ROOT%\METAWORMS\__pycache__\*.pyc" 2>nul
echo Cleanup complete.
goto :eof

:run_diag
%PYTHON_PATH% "%Azazael_ROOT%\Azazael\ECOSYSTEM_TEST.py"
goto :eof

:run_install
echo Installing dependencies...
%PYTHON_PATH% -m pip install psutil requests
echo Done.
goto :eof

:run_backup
echo Backing up configuration...
mkdir "%USERPROFILE%\Desktop\azazael_backup_%date:~-4,4%%date:~-10,2%%date:~-7,2%" 2>nul
xcopy "%Azazael_ROOT%\*.json" "%USERPROFILE%\Desktop\azazael_backup_" /y /q
echo Backup complete.
goto :eof

:run_wormz_ml
%PYTHON_PATH% "%Azazael_ROOT%\WORMZ\WORMZ_ML.py" %*
goto :eof

:run_systemz_ml
%PYTHON_PATH% "%Azazael_ROOT%\WORMZ\SYSTEMZ_ML.py" %*
goto :eof

:run_smartdisk
%PYTHON_PATH% "%Azazael_ROOT%\WORMZ\SMARTDISK.py" %*
goto :eof

:run_hydrometa_uv
%PYTHON_PATH% "%Azazael_ROOT%\METAWORMS\HYDRAMETA_UV.py" %*
goto :eof

:unknown
echo Unknown shortcut: %COMMAND%
echo Run shorty.bat without arguments to see available shortcuts.
goto :eof

:eof
