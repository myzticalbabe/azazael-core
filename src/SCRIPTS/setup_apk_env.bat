@echo off
REM Azazael - Complete APK Engineering Setup
REM Run this as Administrator

echo ========================================
echo   Azazael APK Engineering - Setup
echo ========================================
echo.

echo [STEP 1] Checking installed components...

REM Check Java
java -version >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Java found
    java -version 2>&1 | findstr "version"
) else (
    echo [MISSING] Java JDK 17
    echo Installing Java...
    winget install EclipseAdoptium.JDK.17 --silent
)

echo.

REM Check ADB  
adb version >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] ADB found
    adb version 2>&1 | findstr "Android"
) else (
    echo [MISSING] ADB
    echo Installing ADB...
    winget install Google.PlatformTools --silent
)

echo.
echo [STEP 2] Environment paths...

if exist "%USERPROFILE%\jdk17\jdk-17*" (
    echo [OK] JDK found at: %USERPROFILE%\jdk17
    set JAVA_HOME=%USERPROFILE%\jdk17\jdk-17*
    echo JAVA_HOME=%JAVA_HOME%
) else (
    echo [MANUAL] Set JAVA_HOME manually
)

if exist "%USERPROFILE%\android-sdk" (
    echo [OK] Android SDK found
    set ANDROID_HOME=%USERPROFILE%\android-sdk
    echo ANDROID_HOME=%ANDROID_HOME%
) else (
    echo [MANUAL] Set ANDROID_HOME manually
)

echo.
echo ========================================
echo   Installed Locations
echo ========================================
echo.
echo JAVA_HOME: %JAVA_HOME%
echo ANDROID_HOME: %ANDROID_HOME%
echo.
echo ========================================

echo.
echo Next steps:
echo 1. Restart command prompt to refresh paths
echo 2. Run: .\apk_engineering.ps1 -Install
echo.
