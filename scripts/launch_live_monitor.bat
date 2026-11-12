@echo off
rem launch_live_monitor.bat — opens the live monitor in its own persistent console window.
rem Safe to run from anywhere (double-click, shortcut, or WSL via cmd.exe /c).
start "AZAZAEL LIVE MONITOR" cmd /k "cd /d C:\Azazael\azazael && scripts\open_live_monitor.bat"
exit /b 0
