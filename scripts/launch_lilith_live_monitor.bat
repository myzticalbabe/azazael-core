@echo off
rem launch_lilith_live_monitor.bat - opens Lilith live in its own persistent console window.
rem Safe to run from anywhere (double-click, shortcut, or WSL via cmd.exe /c).
start "LILITH LIVE MONITOR" cmd /k "cd /d C:\Azazael\azazael && scripts\open_lilith_live_monitor.bat"
exit /b 0
