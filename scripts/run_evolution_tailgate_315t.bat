@echo off
rem run_evolution_tailgate_315t.bat — NEW runner for the tail-gate evolution trigger.
rem Built 2026-10-17 per RULE 0Z-J: never reuse ripped-out cycles. Replaces the
rem disabled Azazael-EvolutionCycle task + the misnamed run_evolution_cycle_314t.bat
rem (314t in name, runs 3.15t — do not trust cycle filenames).
rem Tier-5 pattern (see technique_heartbeat.bat): never run python.exe directly
rem from the task — tee stdout/stderr into logs\ so a death leaves a trace. Task:
rem   Azazael-EvolutionTailgate, hourly. Fail-open: the cycle's own __main__
rem   guard already routes fatals to the log AND the exit code.
setlocal
set "PY=C:\Azazael\.venvs\py315t\Scripts\python.exe"
if not exist "%PY%" set PY=python3.15t
if not exist "%PY%" set PY=python
if not exist "%~dp0..\logs" mkdir "%~dp0..\logs"
"%PY%" "%~dp0evolution_cycle.py" >> "%~dp0..\logs\evolution_tailgate.live.log" 2>&1
endlocal
