@echo off
REM Generate all 11 template styles
REM Usage: double-click or run from command prompt

set SCRIPT_DIR=%~dp0
set DATA_DIR=%SCRIPT_DIR%..\data

echo ========================================
echo  AZAZAEL Template Generator - All Styles
echo ========================================
echo.

for %%s in (ultimate neon cosmic glass report mystical occult ethereal grimoire firehorse cain) do (
    echo [%%s] Generating...
    python "%SCRIPT_DIR%unified_generator.py" %%s "%DATA_DIR%\session_data.json" && (
        echo   OK - output_%%s.html
    ) || (
        echo   FAILED - %%s
    )
)

echo.
echo ========================================
echo  All styles generated in: %DATA_DIR%
echo  Open template_viewer.html to browse
echo ========================================
pause
