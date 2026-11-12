@echo off
rem refresh_aoa_data.bat - keep the AOA data artifacts matching the live system.
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
rem     /TR "C:\Azazael\azazael\scripts\refresh_aoa_data.bat"
rem Inspect / remove:
rem   schtasks /Query /TN "AOA Data Refresh" /V /FO LIST
rem   schtasks /Delete /F /TN "AOA Data Refresh"
rem Report drift only, change nothing (useful before a commit):
rem   refresh_aoa_data.bat --check
rem
rem It is safe to run repeatedly: descriptive fields are recomputed from the
rem live registry, baselines are never rewritten (only stamped with the date
rem they were taken), and recorded milestone history is preserved verbatim.
cd /d "%~dp0.."

rem The 3.15t build is the project's dominant target and the hybrid site-packages
rem serves both ABIs, so this runs on the free-threaded interpreter. Fall back to
rem the GIL build if 3.15t is absent, then to whatever `python` resolves to.
set PY=C:\Users\clint\AppData\Local\Programs\Python\Python315\python3.15t.exe
if not exist "%PY%" set PY=C:\Users\clint\AppData\Local\Programs\Python\Python315\python.exe
if not exist "%PY%" set PY=python

rem Log every run, so a silent failure is visible rather than assumed.
set LOG=%~dp0..\logs\refresh_aoa_data.log
echo [%date% %time%] refresh_oa_data %* >> "%LOG%"
"%PY%" "%~dp0..\src\AOA\refresh_aoa_data.py" %* >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
if not "%RC%"=="0" echo [%date% %time%] FAILED rc=%RC% >> "%LOG%"
exit /b %RC%
