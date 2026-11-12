<#
.SYNOPSIS
    Azazael Unified - WSA Integration
    
.DESCRIPTION
    Interoperability with Windows Subsystem for Android

.EXAMPLE
    .\wsa_integration.ps1 -Status
#>

param(
    [switch]$Status,
    [switch]$ListApps,
    [switch]$Connect,
    [string]$InstallAPK,
    [string]$RunApp
)

$ErrorActionPreference = "Continue"

$UnifiedRoot = "C:\Users\clint\Desktop\Azazael_UNIFIED"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Azazael - WSA Integration" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# WSA Configuration
$WSA_IP = "127.0.0.1"
$WSA_Port = 58526
$ADB_PATH = "adb"

function Test-WSAConnection {
    Write-Host "[CHECK] Testing WSA connection..." -ForegroundColor Cyan
    
    $test = & $ADB_PATH connect "$WSA_IP`:$WSA_Port" 2>&1
    
    if ($test -match "connected" -or $test -match "already connected") {
        Write-Host "  [OK] WSA connected" -ForegroundColor Green
        return $true
    }
    else {
        Write-Host "  [WARN] WSA not connected: $test" -ForegroundColor Yellow
        Write-Host "  Make sure WSA is running and Developer Mode is enabled" -ForegroundColor White
        return $false
    }
}

function Get-WSAStatus {
    Write-Host "WSA Status:" -ForegroundColor Cyan
    Write-Host ""
    
    # Check WSA process
    $wsaProc = Get-Process "WsaClient" -ErrorAction SilentlyContinue
    if ($wsaProc) {
        Write-Host "  [RUNNING] WsaClient process active" -ForegroundColor Green
    }
    else {
        Write-Host "  [STOPPED] WsaClient not running" -ForegroundColor Yellow
    }
    
    # Check ADB connection
    $connected = Test-WSAConnection
    
    Write-Host ""
    Write-Host "Configuration:" -ForegroundColor Cyan
    Write-Host "  IP: $WSA_IP" -ForegroundColor White
    Write-Host "  Port: $WSA_Port" -ForegroundColor White
    Write-Host "  ADB Path: $ADB_PATH" -ForegroundColor White
    
    return $connected
}

function List-WSAApps {
    Write-Host "Installed Apps:" -ForegroundColor Cyan
    Write-Host ""
    
    $apps = & $ADB_PATH shell pm list packages 2>&1
    
    if ($LASTEXITCODE -eq 0) {
        $apps | ForEach-Object {
            $pkg = $_ -replace "package:", ""
            Write-Host "  $pkg" -ForegroundColor White
        }
    }
    else {
        Write-Host "  [ERROR] Could not list apps" -ForegroundColor Red
    }
}

function Install-APK {
    param([string]$APKPath)
    
    if (-not (Test-Path $APKPath)) {
        Write-Host "[ERROR] APK not found: $APKPath" -ForegroundColor Red
        return
    }
    
    Write-Host "[INSTALL] Installing APK..." -ForegroundColor Cyan
    Write-Host "  File: $APKPath" -ForegroundColor White
    
    $result = & $ADB_PATH install $APKPath 2>&1
    
    if ($result -match "Success") {
        Write-Host "  [OK] APK installed successfully" -ForegroundColor Green
    }
    else {
        Write-Host "  [ERROR] Installation failed: $result" -ForegroundColor Red
    }
}

function Run-WSAApp {
    param([string]$PackageName)
    
    Write-Host "[RUN] Starting app..." -ForegroundColor Cyan
    Write-Host "  Package: $PackageName" -ForegroundColor White
    
    # Get main activity
    $activity = & $ADB_PATH shell cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER $PackageName 2>&1 | Select-Object -First 1
    
    if ($activity) {
        & $ADB_PATH shell am start -n $activity
        Write-Host "  [OK] App started" -ForegroundColor Green
    }
    else {
        Write-Host "  [WARN] Could not find main activity" -ForegroundColor Yellow
    }
}

function Sync-ToWSA {
    param([string]$SourcePath)
    
    Write-Host "[SYNC] Syncing to WSA..." -ForegroundColor Cyan
    
    if (-not (Test-Path $SourcePath)) {
        Write-Host "  [ERROR] Source not found: $SourcePath" -ForegroundColor Red
        return
    }
    
    # Push files to WSA storage
    $files = Get-ChildItem $SourcePath -Recurse -File
    foreach ($file in $files) {
        $relative = $file.FullName.Replace($SourcePath, "")
        $wsaPath = "/sdcard/Download/Azazael$relative"
        
        & $ADB_PATH push $file.FullName $wsaPath | Out-Null
    }
    
    Write-Host "  [OK] $($files.Count) files synced" -ForegroundColor Green
}

# Execute commands
if ($Status -or (-not $ListApps -and -not $Connect -and -not $InstallAPK -and -not $RunApp)) {
    Get-WSAStatus
}

if ($ListApps) {
    List-WSAApps
}

if ($InstallAPK) {
    Install-APK -APKPath $InstallAPK
}

if ($RunApp) {
    Run-WSAApp -PackageName $RunApp
}

Write-Host ""
Write-Host "ADB Commands:" -ForegroundColor Cyan
Write-Host "  adb connect 127.0.0.1:58526  # Connect to WSA" -ForegroundColor White
Write-Host "  adb devices                  # List devices" -ForegroundColor White
Write-Host "  adb shell                    # Shell into WSA" -ForegroundColor White
Write-Host "  adb install <apk>            # Install APK" -ForegroundColor White
Write-Host ""
