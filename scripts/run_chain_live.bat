@echo off
REM run_chain_live.bat - scheduler wrapper for the chain controller.
REM
REM FORENSIC_INVESTIGATION_2026-09-24.md PART 8, Tier-5 fix: the task used to
REM run python.exe directly, so the controller's stdout/stderr went nowhere and
REM a process death left only "Last Result 0xC000013A". This wrapper tees the
REM output into logs\chain_controller.live.log so the death has a trace.
REM
REM The interpreter path is deliberately hardcoded: it must match the account's
REM pinned production interpreter, now the 3.15t venv (3.14t retired 2026-10-19;
REM same literal the task action uses, since PATH lookups are the Error #226
REM class). Repo paths come from %~dp0.
setlocal
REM PROTEUS_MORPH ON (2026-09-26): forward Phase-E extra genes to the trainer
REM so the search grows past the 4-gene ceiling. Revert to 0 to restore the
REM frozen 4-gene landscape. Watched by benchmark_evolution.py (13 checks) +
REM EvolutionIntegrityWorm (6h).
set "AZAZAEL_PROTEUS_MORPH=1"
REM Speed (2026-09-26): 14 eval workers on the 16-thread box. Bit-identical
REM results at any count. Episodes deliberately stay at default 320 for one
REM clean morph era; drop via AZAZAEL_EVAL_EPISODES later if cycles are slow.
set "AZAZAEL_EVO_WORKERS=14"
set "PY=C:\Azazael\.venvs\py315t\Scripts\python.exe"
"%PY%" "%~dp0chain_controller.py" --live --interval 30 --allow-mutations >> "%~dp0..\logs\chain_controller.live.log" 2>&1
endlocal
