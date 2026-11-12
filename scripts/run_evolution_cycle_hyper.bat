@echo off
rem run_evolution_cycle_hyper.bat — HYPEROPTIMIZED EvolutionCycle (all phases ON).
rem
rem The stock launcher (run_evolution_cycle_314t.bat) runs every PROTEUS phase
rem with its DEFAULT, which is OFF, so the scheduled task has been running the
rem baseline search this whole time. This launcher turns on every phase that is
rem wired, and each one is a measured, separately-verdict'd change:
rem
rem   ORACLE      Phase C  imagined-era robustness pre-gate (torch-free JSON)
rem   CRANIUM     Phase D  cross-era pooled decision at the SPRT site
rem   CIRCULATION Phase D  Thompson allocator replaces the fixed 2x margin gate
rem   MORPH       Phase E  one extra structural gene, round-robin trialled
rem   TENSOR      deliverable  genes as tensors declaring their own usefulness
rem
rem Every flag is set for THIS PROCESS ONLY, so the scheduled task and any
rem manual GIL-side run stay reproducible from the batch file alone.
rem
rem 3.14t was RETIRED 2026-10-19. This launcher now executes the 3.15t
rem production venv directly; the old cp314t-only PYTHONPATH injection was
rem removed, because injecting a cp314t site dir into a 3.15 process breaks it.
set AZAZAEL_PROTEUS_ORACLE=1
set AZAZAEL_PROTEUS_TRAIN=1
set AZAZAEL_PROTEUS_CRANIUM=1
set AZAZAEL_PROTEUS_CIRCULATION=1
set AZAZAEL_PROTEUS_MORPH=1
set AZAZAEL_TENSOR_GENOME=1

"C:\Azazael\.venvs\py315t\Scripts\python.exe" "%~dp0evolution_cycle.py"
exit /b %ERRORLEVEL%
