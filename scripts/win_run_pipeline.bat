@echo off
REM win_run_pipeline.bat - run the graphing pipeline with the GIL-enabled
REM Windows Python 3.15 against the live memory DB (native env, no WSL mangling).
REM Usage:  win_run_pipeline.bat  [learning_loop|ecosystem|temporal|all|desktop]
REM   desktop = copy the canonical graphs/ to Desktop\azazael_graphs (no rebuild)
setlocal
set AZAZAEL_MEMORY_DIR=C:\Users\clint\.azazael
set PYW=C:\Users\clint\AppData\Local\Programs\Python\Python315\python.exe
set DESKTOP=C:\Users\clint\Desktop\azazael_graphs
cd /d C:\Azazael\azazael

if "%1"=="learning_loop" goto learning_loop
if "%1"=="ecosystem" goto ecosystem
if "%1"=="temporal" goto temporal
if "%1"=="desktop" goto desktop
if "%1"=="all" goto all
echo usage: win_run_pipeline.bat [learning_loop^|ecosystem^|temporal^|all^|desktop]
exit /b 1

:learning_loop
call :build_learning_loop
if errorlevel 1 exit /b %errorlevel%
goto desktop

:ecosystem
call :build_ecosystem
if errorlevel 1 exit /b %errorlevel%
goto desktop

:temporal
call :build_temporal
if errorlevel 1 exit /b %errorlevel%
goto desktop

:desktop
if exist "%DESKTOP%" rmdir /s /q "%DESKTOP%"
mkdir "%DESKTOP%"
xcopy /e /i /q /y graphs\ecosystem "%DESKTOP%\ecosystem" >nul
xcopy /e /i /q /y graphs\learning_loop "%DESKTOP%\learning_loop" >nul
xcopy /e /i /q /y graphs\learning_loop_temporal "%DESKTOP%\learning_loop_temporal" >nul
xcopy /e /i /q /y graphs\reports "%DESKTOP%\reports" >nul
echo [desktop] copied graphs to %DESKTOP%
exit /b 0

:all
call :build_learning_loop
call :build_ecosystem
call :build_temporal
goto desktop

:build_learning_loop
"%PYW%" scripts\learning_loop_graph.py --out graphs\learning_loop_win
exit /b %errorlevel%

:build_ecosystem
"%PYW%" scripts\ecosystem_graph.py --out graphs\ecosystem_win
exit /b %errorlevel%

:build_temporal
"%PYW%" scripts\learning_loop_temporal.py --hours 24 --out graphs\learning_loop_temporal_win
exit /b %errorlevel%