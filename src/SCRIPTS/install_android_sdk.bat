@echo off
REM Install Android SDK - Run as Administrator

echo ========================================
echo   Installing Android SDK
echo ========================================
echo.

REM Download SDK
echo [DOWNLOAD] Android SDK Command Line Tools...
powershell -Command "$tempDir = [System.IO.Path]::GetTempPath(); $sdkZip = $tempDir + 'android_cmdline.zip'; $webClient = New-Object System.Net.WebClient; $webClient.DownloadFile('https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip', $sdkZip); Write-Host 'Download complete'"

REM Extract SDK
echo [EXTRACT] Extracting SDK...
powershell -Command "$sdkDir = $Env:USERPROFILE + '\android-sdk'; New-Item -ItemType Directory -Force -Path $sdkDir | Out-Null; Expand-Archive -Path ([System.IO.Path]::GetTempPath() + 'android_cmdline.zip') -DestinationPath $sdkDir -Force; Write-Host 'SDK installed to: ' + $sdkDir"

echo.
echo [DONE] Android SDK installed!
echo.
echo Environment variables needed:
echo   ANDROID_HOME=%USERPROFILE%\android-sdk
echo.
echo Add to PATH:
echo   %USERPROFILE%\android-sdk\cmdline-tools\latest\bin
echo   %USERPROFILE%\android-sdk\platform-tools
echo.

pause
