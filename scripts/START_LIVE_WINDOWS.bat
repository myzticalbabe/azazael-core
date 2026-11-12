@echo off
setlocal
rem Open visible Azazael live consoles.  This is a manual launcher only;
rem it does not enable or change Windows Task Scheduler.
set "ROOT=%~dp0.."
cd /d "%ROOT%"
python "%ROOT%\scripts\launch_live_windows.py"
endlocal
