$downloadDir = [System.IO.Path]::GetTempPath()
$jdkUrl = 'https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.12%2B7/OpenJDK17U-jdk_x64_windows_hotspot_17.0.12_7.zip'
$jdkZip = Join-Path $downloadDir 'jdk17.zip'

Write-Host 'Downloading Java JDK 17...' -ForegroundColor Cyan
$client = New-Object System.Net.WebClient
$client.DownloadFile($jdkUrl, $jdkZip)

Write-Host 'Extracting...' -ForegroundColor Cyan
$extractDir = Join-Path $Env:USERPROFILE 'jdk17'
New-Item -ItemType Directory -Path $extractDir -Force | Out-Null
Expand-Archive -Path $jdkZip -DestinationPath $extractDir -Force

$jdkDir = Get-ChildItem $extractDir | Where-Object { $_.Name -match 'jdk' } | Select-Object -First 1

Write-Host '' -ForegroundColor Cyan
Write-Host "JDK installed to: $($jdkDir.FullName)" -ForegroundColor Green
Write-Host '' -ForegroundColor Cyan
Write-Host 'Environment Variables:' -ForegroundColor Cyan
Write-Host "JAVA_HOME=$($jdkDir.FullName)" -ForegroundColor White
Write-Host '' -ForegroundColor Cyan
Write-Host 'Add to PATH:' -ForegroundColor Cyan
Write-Host "$($jdkDir.FullName)\bin" -ForegroundColor White
