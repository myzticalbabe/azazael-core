@echo off
REM Azazael CLI Assistant Launcher
set "PYTHON_PATH=python"
if exist "C:\Azazael\.venvs\py315t\Scripts\python.exe" set "PYTHON_PATH=C:\Azazael\.venvs\py315t\Scripts\python.exe"
if not exist "C:\Azazael\.venvs\py315t\Scripts\python.exe" if exist "C:\Azazael\.venvs\py315\Scripts\python.exe" set "PYTHON_PATH=C:\Azazael\.venvs\py315\Scripts\python.exe"
if not exist "C:\Azazael\.venvs\py315t\Scripts\python.exe" if not exist "C:\Azazael\.venvs\py315\Scripts\python.exe" if exist "C:\Azazael\.venvs\py314m\Scripts\python.exe" set "PYTHON_PATH=C:\Azazael\.venvs\py314m\Scripts\python.exe"
"%PYTHON_PATH%" "C:\Users\clint\Desktop\Azazael\Azazael_INTEGRATION.py" %*
pause
