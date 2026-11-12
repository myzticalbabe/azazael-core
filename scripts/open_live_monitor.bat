@echo off
rem open_live_monitor.bat — persistent live dashboard window for the Azazael learning loop.
rem Double-click this (or run from a shortcut). The window NEVER closes on its own:
rem   - the dashboard loops forever, refreshing every 30s
rem   - if the dashboard ever exits, `pause` holds the window open so you can read errors
rem Press Ctrl+C inside the window to stop the dashboard (window still stays for reading).

title AZAZAEL LIVE MONITOR
color 0A
rem Fixed size so the full wallboard fits ONE screen for snapshots
rem (118 wide keeps the training tail columns + verdict; 34 rows holds
rem all 28 render lines + the header with headroom).
mode con: cols=118 lines=34
cd /d "%~dp0.."

set PY=C:\Users\clint\AppData\Local\Programs\Python\Python315\python.exe
if not exist "%PY%" set PY=python

:loop
echo [%date% %time%] starting live monitor...
"%PY%" scripts\live_monitor.py --refresh 30
echo.
echo ============================================================
echo  Monitor stopped or errored at %date% %time%.
echo  Relaunching in 10s (Ctrl+C to fully close)
echo ============================================================
timeout /t 10 /nobreak >nul
goto loop