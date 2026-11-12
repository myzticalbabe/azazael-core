@echo off
rem open_live_monitor_web.bat — LAN webpage for the Azazael learning loop.
rem Opens its own persistent window running the web server (never auto-closes).
rem Open http://<this-pc>:8765 from any device on the network.
title AZAZAEL LIVE MONITOR (WEB)
color 0D
cd /d "%~dp0.."

set PY=C:\Users\clint\AppData\Local\Programs\Python\Python315\python.exe
if not exist "%PY%" set PY=python

:loop
echo [%date% %time%] starting live monitor web server on port 8765...
"%PY%" scripts\live_monitor_web.py --port 8765 --refresh 30
echo.
echo ============================================================
echo  Web server stopped or errored at %date% %time%.
echo  Window held open so you can read the message.
echo  (Ctrl+C to fully close)
echo ============================================================
pause
goto loop