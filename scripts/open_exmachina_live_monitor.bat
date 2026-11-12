@echo off
rem open_exmachina_live_monitor.bat - standalone Ex Machina wallboard window.
rem Double-click this (or run from a shortcut). The window NEVER closes on its own:
rem   - the wallboard loops forever, refreshing every 30s
rem   - if it ever exits, `pause` holds the window open so you can read errors
rem Press Ctrl+C inside the window to stop it (window still stays for reading).

title EX MACHINA LIVE MONITOR
color 0B
rem Fixed size so the whole panel fits ONE screen for snapshots
rem (118 wide keeps the two-column organ cells; 40 rows holds the full
rem 16-organ panel plus the progressive-disclosure rows with headroom).
mode con: cols=118 lines=40
cd /d "%~dp0.."

set PY=C:\Users\clint\AppData\Local\Programs\Python\Python315\python.exe
if not exist "%PY%" set PY=python

:loop
echo [%date% %time%] starting Ex Machina live monitor...
"%PY%" scripts\exmachina_live_monitor.py --refresh 30
echo.
echo ============================================================
echo  Ex Machina monitor stopped or errored at %date% %time%.
echo  Relaunching in 10s (Ctrl+C to fully close)
echo ============================================================
timeout /t 10 /nobreak >nul
goto loop
