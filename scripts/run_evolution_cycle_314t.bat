@echo off
rem run_evolution_cycle_314t.bat — EvolutionCycle on the free-threaded interpreter.
rem
rem 3.14t was RETIRED 2026-10-19: this file keeps its historical name because
rem the scheduler task and run_evolution_tailgate_315t.bat reference it, but it
rem now executes the 3.15t production venv. The old cp314t PYTHONPATH injection
rem was removed with the retirement — a cp314t site dir must never be injected
rem into a 3.15 process.
"C:\Azazael\.venvs\py315t\Scripts\python.exe" "%~dp0evolution_cycle.py"
exit /b %ERRORLEVEL%