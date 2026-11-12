@echo off
REM Azazael APK Engineering - Dependency Installation
REM Run as Administrator

echo ========================================
echo   Azazael - APK Engineering Setup
echo ========================================
echo.

REM Check for Java
echo [CHECK] Checking for Java...
java -version >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Java found
    java -version 2>&1 | findstr /i "version" >nul
) else (
    echo [MISSING] Java JDK 17+ not found
    echo.
    echo Download Java JDK 17 from:
    echo   https://adoptium.net/
    echo.
    echo Or run:
    echo   winget install EclipseAdoptium.JDK.17
    echo.
)

REM Check for ADB
echo [CHECK] Checking for ADB...
adb version >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] ADB found
    adb version 2>&1 | head -1
) else (
    echo [MISSING] ADB not found
    echo.
    echo Installing ADB Platform Tools...
    winget install Google.PlatformTools -e
)

echo.
echo ========================================
echo   Environment Setup
echo ========================================
echo.

REM Set environment variables
if exist "C:\Program Files\Eclipse Adoptium\jdk-17*" (
    set JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-17*
    echo [AUTO] JAVA_HOME set to: %JAVA_HOME%
) else if exist "%USERPROFILE%\jdk17\jdk-17*" (
    set JAVA_HOME=%USERPROFILE%\jdk17\jdk-17*
    echo [AUTO] JAVA_HOME set to: %JAVA_HOME%
) else (
    echo [MANUAL] Set JAVA_HOME manually
)

if exist "C:\Users\clint\AppData\Local\Android\Sdk" (
    set ANDROID_HOME=C:\Users\clint\AppData\Local\Android\Sdk
    echo [AUTO] ANDROID_HOME set to: %ANDROID_HOME%
) else if exist "C:\Program Files (x86)\Android\android-sdk" (
    set ANDROID_HOME=C:\Program Files (x86)\Android\android-sdk
    echo [AUTO] ANDROID_HOME set to: %ANDROID_HOME%
) else if exist "C:\Android\Sdk" (
    set ANDROID_HOME=C:\Android\Sdk
    echo [AUTO] ANDROID_HOME set to: %ANDROID_HOME%
) else (
    echo [MANUAL] Set ANDROID_HOME manually
)

echo.
echo ========================================
echo   Next Steps
echo ========================================
echo.
echo 1. Install Java JDK 17:
echo    winget install EclipseAdoptium.JDK.17
echo.
echo 2. Install Android SDK:
echo    Download Android Studio from:
echo    https://developer.android.com/studio
echo.
echo 3. Set environment variables:
echo    JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-17.x.x
echo    ANDROID_HOME=C:\Users\clint\AppData\Local\Android\Sdk
echo.
echo 4. Run Azazael APK Engineering:
echo    .\apk_engineering.ps1 -Install
echo.
echo ========================================

pause
