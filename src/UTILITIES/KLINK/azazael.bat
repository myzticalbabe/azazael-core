@echo off
REM ============================================================================
REM Azazael ECOSYSTEM - KLINK Command Launcher
REM Location: %USERPROFILE%\Desktop\KLINK\azazael.bat
REM Supports: Windows CMD/PowerShell
REM ============================================================================

setlocal EnableDelayedExpansion

REM Configuration
set "Azazael_ROOT=C:\Users\clint\Desktop\Azazael"
set "KLINK_ROOT=%~dp0"

REM Color support
for /f "delims=#" %%a in ('"prompt #$E# & for %%b in (1) do rem"') do set "ESC=%%a"

set "BLUE=%ESC%[94m"
set "GREEN=%ESC%[92m"
set "YELLOW=%ESC%[93m"
set "RED=%ESC%[91m"
set "NC=%ESC%[0m"

:MAIN
cls
echo %BLUE%============================================================%NC%
echo  Azazael ECOSYSTEM - KLINK Command Launcher
echo %BLUE%============================================================%NC%
echo.
echo %YELLOW%Commands:%NC%
echo  [1]  Status           - Show ecosystem status
echo  [2]  Worms            - Run all WORMZ worms
echo  [3]  Metaworms        - Run all METAWORMS
echo  [4]  HYDRA            - Run full HYDRA cycle
echo  [5]  TERENCE          - Error resolver
echo  [6]  HORUS            - Output formatter
echo  [7]  TARBABY          - Binary builder
echo  [8]  POINDEXTER       - Mathematical indexer
echo  [9]  PERSEPHONE       - Cross-platform utilities
echo  [10] Optimize         - Run ecosystem optimizer
echo  [11] All              - Run full ecosystem
echo  [12] SHORTY           - Run SHORTY shortcuts
echo  [0]  Exit
echo.
set /p choice="Enter command number: "

if "%choice%"=="1" goto STATUS
if "%choice%"=="2" goto WORMS
if "%choice%"=="3" goto METAWORMS
if "%choice%"=="4" goto HYDRA
if "%choice%"=="5" goto TERENCE
if "%choice%"=="6" goto HORUS
if "%choice%"=="7" goto TARBABY
if "%choice%"=="8" goto POINDEXTER
if "%choice%"=="9" goto PERSEPHONE
if "%choice%"=="10" goto OPTIMIZE
if "%choice%"=="11" goto ALL
if "%choice%"=="12" goto SHORTY
if "%choice%"=="0" exit /b 0

goto MAIN

:STATUS
echo %GREEN%[INFO] Showing ecosystem status...%NC%
python "%Azazael_ROOT%\Azazael_INTEGRATION.py" --status
pause
goto MAIN

:WORMS
echo %GREEN%[INFO] Running all WORMZ worms...%NC%
python "%Azazael_ROOT%\WORMZ\CONCURRENCY.py" --scan
pause
goto MAIN

:METAWORMS
echo %GREEN%[INFO] Running METAWORMS...%NC%
python "%Azazael_ROOT%\METAWORMS\HYDRAMETA.py" --all
pause
goto MAIN

:HYDRA
echo %GREEN%[INFO] Running HYDRA cycle...%NC%
python "%Azazael_ROOT%\HYDRA\HYDRA.py" --all
pause
goto MAIN

:TERENCE
echo %GREEN%[INFO] Running TERENCE error resolver...%NC%
python "%Azazael_ROOT%\TERENCE\TERENCE.py" --scan
pause
goto MAIN

:HORUS
echo %GREEN%[INFO] Running HORUS output formatter...%NC%
python "%Azazael_ROOT%\HORUS\HORUS.py"
pause
goto MAIN

:TARBABY
echo %GREEN%[INFO] Running TARBABY binary builder...%NC%
python "%Azazael_ROOT%\TARBABY\TARBABY.py" --test
pause
goto MAIN

:POINDEXTER
echo %GREEN%[INFO] Running POINDEXTER...%NC%
python "%Azazael_ROOT%\POINDEXTER\POINDEXTER.py"
pause
goto MAIN

:PERSEPHONE
echo %GREEN%[INFO] Running PERSEPHONE...%NC%
python "%Azazael_ROOT%\PERSEPHONE\persephone.py"
pause
goto MAIN

:OPTIMIZE
echo %GREEN%[INFO] Running ecosystem optimizer...%NC%
python "%Azazael_ROOT%\ECOSYSTEM_OPTIMIZER.py" -o
pause
goto MAIN

:ALL
echo %GREEN%[INFO] Running full ecosystem...%NC%
python "%Azazael_ROOT%\Azazael_INTEGRATION.py"
pause
goto MAIN

:SHORTY
cls
echo %YELLOW%Available SHORTY shortcuts:%NC%
dir /b "%Azazael_ROOT%\SHORTY\*.bat" 2>nul
echo.
set /p shortcut="Enter shortcut name (without .bat): "
if defined shortcut (
    if exist "%Azazael_ROOT%\SHORTY\%shortcut%.bat" (
        echo %GREEN%[INFO] Running %shortcut%...%NC%
        call "%Azazael_ROOT%\SHORTY\%shortcut%.bat"
    ) else (
        echo %RED%[ERROR] Shortcut not found: %shortcut%%NC%
    )
)
pause
goto MAIN
