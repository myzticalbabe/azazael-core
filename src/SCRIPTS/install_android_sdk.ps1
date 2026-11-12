$tempDir = [System.IO.Path]::GetTempPath()
$sdkZip = $tempDir + "android_cmdline.zip"
$sdkDir = $Env:USERPROFILE + "\android-sdk"

Write-Host "Downloading Android SDK Command Line Tools..." -ForegroundColor Cyan
$webClient = New-Object System.Net.WebClient
$webClient.DownloadFile("https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip", $sdkZip)

Write-Host "Extracting..." -ForegroundColor Cyan
New-Item -ItemType Directory -Path $sdkDir -Force | Out-Null
Expand-Archive -Path $sdkZip -DestinationPath $sdkDir -Force

Write-Host "" -ForegroundColor Cyan
Write-Host "Android SDK installed to: $sdkDir" -ForegroundColor Green
Write-Host "" -ForegroundColor Cyan
Write-Host "Environment Variables:" -ForegroundColor Cyan
Write-Host "ANDROID_HOME=$sdkDir" -ForegroundColor White
Write-Host "" -ForegroundColor Cyan
Write-Host "Add to PATH:" -ForegroundColor Cyan
Write-Host "$sdkDir\cmdline-tools\latest\bin" -ForegroundColor White
