@echo off
rem exmachina_health_check.bat - scheduled probe for the Ex Machina tier.
rem Register once (no admin needed for a per-user task):
rem   schtasks /Create /F /SC MINUTE /MO 15 /TN "Ex Machina Health" ^
rem     /TR "C:\Azazael\azazael\scripts\exmachina_health_check.bat"
rem Inspect / remove:
rem   schtasks /Query /TN "Ex Machina Health" /V /FO LIST
rem   schtasks /Delete /F /TN "Ex Machina Health"
rem Pass --quiet to log without a popup: add it to the /TR line.
cd /d "%~dp0.."

set PY=C:\Users\clint\AppData\Local\Programs\Python\Python315\python.exe
if not exist "%PY%" set PY=python

"%PY%" scripts\exmachina_health_alert.py %*
