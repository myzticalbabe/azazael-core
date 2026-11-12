@echo off
rem technique_heartbeat.bat - scheduler wrapper for the technique-runtime heartbeat.
rem Tier-5 pattern (see run_chain_live.bat): never run python.exe directly from the
rem task — tee stdout/stderr into logs\ so a death leaves a trace. Task:
rem   Azazael-TechniqueHeartbeat, hourly. Interpreter ladder matches the monitors.
setlocal
set "PY=C:\Azazael\.venvs\py315t\Scripts\python.exe"
if not exist "%PY%" set PY=C:\Users\clint\AppData\Local\Programs\Python\Python315\python3.15t.exe
if not exist "%PY%" set PY=python3.15t
if not exist "%PY%" set PY=python
if not exist "%~dp0..\logs" mkdir "%~dp0..\logs"
"%PY%" "%~dp0technique_heartbeat.py" >> "%~dp0..\logs\technique_heartbeat.live.log" 2>&1
endlocal
