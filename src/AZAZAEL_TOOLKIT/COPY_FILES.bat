@echo off
REM AZAZAEL TOOLKIT - COPY SCRIPT
REM Copies all essential files from Azazael_UNIFIED to AZAZAEL_TOOLKIT
REM Run as Administrator for best results

set SOURCE=C:\Users\clint\Desktop\Azazael_UNIFIED
set DEST=C:\Users\clint\Desktop\AZAZAEL_TOOLKIT

echo ================================================
echo AZAZAEL TOOLKIT - FILE COPY
echo ================================================
echo.
echo Copying essential files...
echo.

REM Documentation
copy "%SOURCE%\KNOWLEDGE_BASE.txt" "%DEST%\" 2>nul && echo [OK] KNOWLEDGE_BASE.txt || echo [FAIL] KNOWLEDGE_BASE.txt
copy "%SOURCE%\INSTALLATION_LOG.txt" "%DEST%\" 2>nul && echo [OK] INSTALLATION_LOG.txt || echo [FAIL] INSTALLATION_LOG.txt
copy "%SOURCE%\TRANSPARENCY_MANIFESTO.txt" "%DEST%\" 2>nul && echo [OK] TRANSPARENCY_MANIFESTO.txt || echo [FAIL] TRANSPARENCY_MANIFESTO.txt
copy "%SOURCE%\QL_ANALYSIS_REPORT.txt" "%DEST%\" 2>nul && echo [OK] QL_ANALYSIS_REPORT.txt || echo [FAIL] QL_ANALYSIS_REPORT.txt

REM Python Scripts
copy "%SOURCE%\COMPREHENSIVE_FIX.py" "%DEST%\" 2>nul && echo [OK] COMPREHENSIVE_FIX.py || echo [FAIL] COMPREHENSIVE_FIX.py
copy "%SOURCE%\environment_manager.py" "%DEST%\" 2>nul && echo [OK] environment_manager.py || echo [FAIL] environment_manager.py
copy "%SOURCE%\QL_ANALYSIS.py" "%DEST%\" 2>nul && echo [OK] QL_ANALYSIS.py || echo [FAIL] QL_ANALYSIS.py
copy "%SOURCE%\REAL_FRAMEWORK_TEST.py" "%DEST%\" 2>nul && echo [OK] REAL_FRAMEWORK_TEST.py || echo [FAIL] REAL_FRAMEWORK_TEST.py

REM Logger
copy "%SOURCE%\UTILITIES\AzazaelDynamicLogger.py" "%DEST%\" 2>nul && echo [OK] AzazaelDynamicLogger.py || echo [FAIL] AzazaelDynamicLogger.py

echo.
echo ================================================
echo COPY COMPLETE
echo ================================================
echo.
echo Files in AZAZAEL_TOOLKIT:
dir /b "%DEST%\*.py" "%DEST%\*.txt" 2>nul
echo.
pause
