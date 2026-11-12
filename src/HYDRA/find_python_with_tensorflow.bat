@echo off
REM Find Python with TensorFlow installed
echo Searching for Python with TensorFlow...

setlocal EnableDelayedExpansion

set PYTHON_PATHS=^
C:\Users\clint\AppData\Local\Programs\Python\Python311\python.exe^
 C:\Python311\python.exe^
 C:\Users\clint\.local\bin\python3.11.exe^
 C:\Program Files\Python311\python.exe

set FOUND_PYTHON=
set FOUND_VERSION=

for %%p in (%PYTHON_PATHS%) do (
    if exist "%%p" (
        echo Checking: %%p
        for /f "delims=" %%v in ('"%%p" -c "import tensorflow; print(tensorflow.__version__)" 2^>nul') do (
            echo [OK] TensorFlow found: %%v
            set FOUND_PYTHON=%%p
            set FOUND_VERSION=%%v
            goto :FOUND
        )
    )
)

:FOUND
if defined FOUND_PYTHON (
    echo.
    echo ========================================
    echo Python with TensorFlow found!
    echo ========================================
    echo Python: !FOUND_PYTHON!
    echo TensorFlow: !FOUND_VERSION!
    echo.
    echo Add to PATH? (Y/N)
    set /p ADD="> "
    if /i "!ADD!"=="Y" (
        for %%i in (!FOUND_PYTHON!) do set PYTHON_DIR=%%~dpi
        setx PATH "!PYTHON_DIR!;!PATH!" >nul
        echo Added !PYTHON_DIR! to PATH
    )
) else (
    echo.
    echo TensorFlow not found in any Python installation.
    echo Check your Python installations.
)

endlocal
pause
