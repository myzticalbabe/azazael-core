@echo off
rem refresh_oa_data.bat - keep the AOA data artifacts matching the live system.
rem
rem WHY: `AOA_MILESTONE.json` and `benchmark_data.json` had NO producer at all.
rem They were hand-authored (2026-06-30 and 2026-08-27) and read by two
rem monitors, so by 2026-09-14 every descriptive claim in them was wrong:
rem 8 arms where the registry had 9, module line counts drifted, `aoa_cli.py`
rem recorded at a path it had since moved out of, Python 3.14.6 where the
rem machine then ran 3.14.7. The panels were accurately reporting stale files.
rem
rem Register once (no admin needed for a per-user task):
rem   schtasks /Create /F /SC HOURLY /MO 6 /TN "AOA Data Refresh" ^
rem     /TR "C:\Azazael\azazael\scripts\refresh_oa_data.bat"
rem Inspect / remove:
rem   schtasks /Query /TN "AOA Data Refresh" /V /FO LIST
rem   schtasks /Delete /F /TN "AOA Data Refresh"
rem Report drift only, change nothing (useful before a commit):
rem   refresh_oa_data.bat --check
rem
rem It is safe to run repeatedly: descriptive fields are recomputed from the
rem live registry, baselines are never rewritten (only stamped with the date
rem they were taken), and recorded milestone history is preserved verbatim.
cd /d "%~dp0.."

rem 3.15t is the project's only production target (3.14t retired 2026-10-19), so
rem this runs on the free-threaded interpreter. Fall back to the system 3.15t
rem install if the venv is absent, then to `python3.15t`.
set PY=C:\Azazael\.venvs\py315t\Scripts\python.exe
if not exist "%PY%" set PY=C:\Users\clint\AppData\Local\Programs\Python\Python315\python3.15t.exe
if not exist "%PY%" set PY=python3.15t
if not exist "%PY%" set PY=python

rem Log every run, so a silent failure is visible rather than assumed.
set LOG=%~dp0..\logs\refresh_oa_data.log
echo [%date% %time%] refresh_oa_data %* >> "%LOG%"
"%PY%" "%~dp0..\src\AOA\refresh_oa_data.py" %* >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
if not "%RC%"=="0" echo [%date% %time%] FAILED rc=%RC% >> "%LOG%"
exit /b %RC%
