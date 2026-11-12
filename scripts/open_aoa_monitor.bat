@echo off
rem open_aoa_monitor.bat - persistent live wallboard for the Armorie-of-Anubis.
rem Double-click this (or run from a shortcut). The window NEVER closes on its own:
rem   - the panel loops forever, refreshing every 15s (slow probe every 300s)
rem   - if it ever exits, the relaunch hold keeps the window open so you can read errors
rem Press Ctrl+C inside the window to stop it (window still stays for reading).
rem
rem This launcher was missing until 2026-09-14: the other three wallboards
rem (Azazael, Lilith, Ex Machina) each had one, so the AOA panel could only be
rem run by hand and was therefore never actually on screen.

title AOA ARMORIE-OF-ANUBIS LIVE MONITOR
color 0E
rem Fixed size so the whole panel fits ONE screen for snapshots.
rem 118 cols keeps the routing/provenance tail; 42 rows is what unlocks the
rem capability matrix (the panel height-gates sections at 30 / 34 / 42).
mode con: cols=118 lines=42
cd /d "%~dp0.."

rem Prefer the free-threaded interpreter: this project mandates 3.15t since
rem 3.14t was retired (2026-10-19), and the scientific stack (scipy, torch)
rem publishes cp315t-only wheels, so the GIL build cannot import them. Fall
rem back to the system 3.15t install only if the venv is absent, then to
rem `python3.15t`. Same ladder as refresh_oa_data.bat.
set PY=C:\Azazael\.venvs\py315t\Scripts\python.exe
if not exist "%PY%" set PY=C:\Users\clint\AppData\Local\Programs\Python\Python315\python3.15t.exe
if not exist "%PY%" set PY=python3.15t
if not exist "%PY%" set PY=python

:loop
echo [%date% %time%] starting AOA Armorie-of-Anubis panel...
"%PY%" src\AOA\aoa_monitor.py --refresh 15
echo.
echo ============================================================
echo  AOA panel stopped or errored at %date% %time%.
echo  Relaunching in 10s (Ctrl+C to fully close)
echo ============================================================
timeout /t 10 /nobreak >nul
goto loop
