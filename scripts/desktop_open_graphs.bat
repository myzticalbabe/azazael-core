@echo off
REM desktop_open_graphs.bat - open the key graph visuals from Desktop\azazael_graphs.
REM Usage:  desktop_open_graphs.bat  [all|png|timeline|report]
setlocal
set D=C:\Users\clint\Desktop\azazael_graphs

if "%1"=="png" goto png
if "%1"=="timeline" goto timeline
if "%1"=="report" goto report
if "%1"=="all" goto all
echo usage: desktop_open_graphs.bat [all^|png^|timeline^|report]
exit /b 1

:png
start "" "%D%\ecosystem\ecosystem_gephi.png"
start "" "%D%\learning_loop\learning_loop_gephi.png"
exit /b 0

:timeline
start "" "%D%\learning_loop_temporal\timeline.html"
exit /b 0

:report
start "" "%D%\reports\training_day_trend.md"
exit /b 0

:all
start "" "%D%\ecosystem\ecosystem_gephi.png"
start "" "%D%\learning_loop\learning_loop_gephi.png"
start "" "%D%\learning_loop_temporal\timeline.html"
start "" "%D%\ecosystem\ecosystem.html"
exit /b 0