@echo off
REM =============================================================================
REM Azazael/PERSEPHONE Admin Activation Script
REM
REM Purpose: Configure maximum command-line capabilities for development
REM
REM Usage: Run as Administrator (Right-click > Run as Administrator)
REM =============================================================================

setlocal EnableDelayedExpansion

echo =============================================================================
echo Admin Activation Script - Azazael/PERSEPHONE
echo =============================================================================
echo.

REM Check for Admin privileges
net session >nul 2>&1
if %errorLevel% == 0 (
    echo [OK] Running with Administrator privileges
    set ADMIN_MODE=1
) else (
    echo [WARN] NOT running as Administrator!
    echo [WARN] Some features may be limited
    echo [WARN] Right-click this file > Run as Administrator
    set ADMIN_MODE=0
)

echo.
echo =============================================================================
echo Configuration
echo =============================================================================
echo.

REM Set environment variables
set "Azazael_ROOT=C:\Users\clint\Desktop\Azazael"
set "PERSEPHONE_ROOT=C:\Users\clint\Desktop\PERSEPHONE"
set "PYTHONPATH=%Azazael_ROOT%\ml_platforms;%PERSEPHONE_ROOT%\lib;%PYTHONPATH%"
set "PATH=%Azazael_ROOT%\scripts;%PERSEPHONE_ROOT%\bin;%PATH%"

echo [OK] Environment variables set
echo.
echo Azazael_ROOT=%Azazael_ROOT%
echo PERSEPHONE_ROOT=%PERSEPHONE_ROOT%
echo PYTHONPATH=%PYTHONPATH%
echo PATH extended with scripts and bin directories

echo.
echo =============================================================================
echo CMD Commands Reference
echo =============================================================================
echo.
echo NETWORKING:
echo   ipconfig /all           - Full network configuration
echo   netstat -ano            - All connections with PID
echo   nslookup google.com     - DNS lookup
echo   ping -t google.com      - Continuous ping
echo   tracert google.com      - Trace route
echo   pathping google.com     - Path analysis
echo.
echo SYSTEM:
echo   systeminfo              - Full system information
echo   tasklist /SVC           - Processes with services
echo   taskkill /PID 1234 /F  - Force kill process
echo   chkdsk /f /r           - Check disk (schedule on reboot)
echo   sfc /scannow            - System file checker
echo   DISM /Online /Cleanup /RestoreHealth - Repair image
echo.
echo USER:
echo   whoami /all            - Full user info with privileges
echo   net user               - List users
echo   net localgroup Administrators - List admins
echo.
echo SERVICES:
echo   net start               - Started services
echo   sc query type= state= all - All services detail
echo   sc start ServiceName    - Start service
echo   sc stop ServiceName     - Stop service
echo.
echo FILES:
echo   icacls file.txt         - View/set permissions
echo   takeown /F file.txt     - Take ownership
echo   robocopy source dest /E /Z /B - Robust file copy
echo   xcopy source dest /E /H /K - Extended copy
echo.
echo REGISTRY:
echo   reg query HKLM\Software - Query registry
echo   reg add "path" /v name /t REG_SZ /d "value" - Add key
echo   reg delete "path" /f - Delete key
echo.
echo PROCESSES:
echo   wmic process get Name,ProcessId,CommandLine - Process details
echo   wmic cpu get Name,NumberOfCores,MaxClockSpeed - CPU info
echo   wmic os get Version,BuildNumber,TotalVisibleMemorySize - OS info
echo.
echo DISK:
echo   diskpart                - Disk utility (interactive)
echo   wmic diskdrive get Model,Size,Status - Disk info
echo   fsutil behavior query DisableDeleteNotify - SSD info
echo.
echo =============================================================================
echo PowerShell Commands Reference
echo =============================================================================
echo.
echo BASICS:
echo   Get-Command             - List all cmdlets
echo   Get-Help Get-Process    - Help for cmdlet
echo   Get-Process             - List processes
echo   Stop-Process -Id 1234    - Kill process
echo.
echo SERVICES:
echo   Get-Service             - List services
echo   Start-Service ServiceName
echo   Stop-Service ServiceName
echo   Set-Service ServiceName -StartupType Automatic
echo.
echo NETWORKING:
echo   Get-NetIPConfiguration  - IP settings
echo   Test-NetConnection google.com - Ping + DNS
echo   Resolve-DnsName google.com - DNS lookup
echo   Get-NetFirewallRule     - Firewall rules
echo.
echo FILES:
echo   Get-ChildItem -Path C:\ -Recurse -Filter *.py | Measure-Object
echo   Copy-Item source dest -Recurse
echo   Move-Item source dest
echo   Remove-Item path -Recurse -Force
echo   Get-ACL path           - View permissions
echo   Set-ACL path -AclObject $aclObject - Set permissions
echo.
echo REGISTRY:
echo   Get-ItemProperty "HKLM:\Software\Key"
echo   Set-ItemProperty "HKLM:\Software\Key" -Name Value -Data "Data"
echo   New-Item "HKLM:\Software\NewKey"
echo   Remove-Item "HKLM:\Software\OldKey"
echo.
echo SYSTEM:
echo   Get-ComputerInfo       - Full system info
echo   Get-WmiObject Win32_ComputerSystem
echo   Get-CimInstance Win32_OperatingSystem
echo   Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine="notepad"}
echo.
echo SCHEDULED TASKS:
echo   Get-ScheduledTask       - List tasks
echo   Start-ScheduledTask TaskName
echo   Stop-ScheduledTask TaskName
echo   Register-ScheduledJob -FilePath script.ps1 -Trigger (New-JobTrigger -AtLogOn)
echo.
echo MODULES:
echo   Import-Module Azazael   - Import Azazael module
echo   Get-Module              - List loaded modules
echo   Install-Module Azazael   - Install from gallery
echo.
echo =============================================================================
echo Creating PowerShell Profile
echo =============================================================================
echo.

REM Create PowerShell profile
set "PROFILE_PATH=%USERPROFILE%\Documents\PowerShell\Microsoft.PowerShell_profile.ps1"
set "PROFILE_CONTENT="
set "PROFILE_CONTENT=!PROFILE_CONTENT!# Azazael/PERSEPHONE PowerShell Profile\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!# Generated by Admin Activation Script\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!# Environment Variables\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!$env:Azazael_ROOT = \"C:\Users\clint\Desktop\Azazael\"\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!$env:PERSEPHONE_ROOT = \"C:\Users\clint\Desktop\PERSEPHONE\"\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!$env:PYTHONPATH = \"$env:Azazael_ROOT\ml_platforms;$env:PERSEPHONE_ROOT\lib;$env:PYTHONPATH\"\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!$env:PATH = \"$env:Azazael_ROOT\scripts;$env:PERSEPHONE_ROOT\bin;$env:PATH\"\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!# Aliases\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!Set-Alias -Name azazael -Value \"python $env:Azazael_ROOT\scripts\azazael_cli.py\"\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!Set-Alias -Name persephone -Value \"python $env:PERSEPHONE_ROOT\bin\persephone.py\"\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!Set-Alias -Name sync -Value \"bash $env:PERSEPHONE_ROOT\sync_azazael_wsl.sh\"\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!# Admin functions\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!function Restart-ServiceAll { Get-Service | Where-Object {$_.Status -eq 'Running'} | Restart-Service }\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!function Kill-All-Python { Get-Process python* | Stop-Process -Force }\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!function Show-ProcessTree { Get-Process | Select-Object Id,ProcessName,@{N='ParentId';E={$_.Parent.Id}} }\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!function Get-DiskInfo { Get-WmiObject Win32_DiskDrive | Select-Object Model,Size,Status }\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!function Find-Process-Port { param([string]$Port) Get-NetTCPConnection -LocalPort $Port | Select-Object OwningProcess,LocalPort }\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!# Load Azazael module if available\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!$AzazaelModule = \"$env:Azazael_ROOT\scripts\azazael.psm1\"\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!if (Test-Path $AzazaelModule) { Import-Module $AzazaelModule }\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!\n"
set "PROFILE_CONTENT=!PROFILE_CONTENT!Write-Host \"Azazael/PERSEPHONE Profile Loaded\" -ForegroundColor Green\n"

REM Check if profile exists
if exist "%PROFILE_PATH%" (
    echo [INFO] PowerShell profile already exists
    echo [INFO] Location: %PROFILE_PATH%
    echo.
    echo [ASK] Overwrite? (Y/N)
    set /p OVERWRITE="Choice: "
    if not "!OVERWRITE!"=="Y" (
        echo Skipping profile creation
        goto SKIP_PROFILE
    )
)

REM Create directory if needed
mkdir "%USERPROFILE%\Documents\PowerShell" 2>nul
mkdir "%USERPROFILE%\Documents\PowerShell" >nul 2>&1

REM Write profile (handle special characters)
echo # Azazael/PERSEPHONE PowerShell Profile> "%PROFILE_PATH%"
echo # Generated by Admin Activation Script>> "%PROFILE_PATH%"
echo.>> "%PROFILE_PATH%"
echo # Environment Variables>> "%PROFILE_PATH%"
echo $env:Azazael_ROOT = "C:\Users\clint\Desktop\Azazael">> "%PROFILE_PATH%"
echo $env:PERSEPHONE_ROOT = "C:\Users\clint\Desktop\PERSEPHONE">> "%PROFILE_PATH%"
echo $env:PYTHONPATH = "$env:Azazael_ROOT\ml_platforms;$env:PERSEPHONE_ROOT\lib;$env:PYTHONPATH">> "%PROFILE_PATH%"
echo $env:PATH = "$env:Azazael_ROOT\scripts;$env:PERSEPHONE_ROOT\bin;$env:PATH">> "%PROFILE_PATH%"
echo.>> "%PROFILE_PATH%"
echo # Aliases>> "%PROFILE_PATH%"
echo Set-Alias -Name azazael -Value "python $env:Azazael_ROOT\scripts\azazael_cli.py">> "%PROFILE_PATH%"
echo Set-Alias -Name persephone -Value "python $env:PERSEPHONE_ROOT\bin\persephone.py">> "%PROFILE_PATH%"
echo Set-Alias -Name sync -Value "bash $env:PERSEPHONE_ROOT\sync_azazael_wsl.sh">> "%PROFILE_PATH%"
echo.>> "%PROFILE_PATH%"
echo # Admin functions>> "%PROFILE_PATH%"
echo function Restart-ServiceAll { Get-Service | Where-Object {$_.Status -eq 'Running'} | Restart-Service }>> "%PROFILE_PATH%"
echo function Kill-All-Python { Get-Process python* | Stop-Process -Force }>> "%PROFILE_PATH%"
echo function Show-ProcessTree { Get-Process | Select-Object Id,ProcessName,@{N='ParentId';E={$_.Parent.Id}} }>> "%PROFILE_PATH%"
echo function Get-DiskInfo { Get-WmiObject Win32_DiskDrive | Select-Object Model,Size,Status }>> "%PROFILE_PATH%"
echo function Find-Process-Port { param([string]$Port) Get-NetTCPConnection -LocalPort $Port | Select-Object OwningProcess,LocalPort }>> "%PROFILE_PATH%"
echo.>> "%PROFILE_PATH%"
echo # Load Azazael module>> "%PROFILE_PATH%"
echo $AzazaelModule = "$env:Azazael_ROOT\scripts\azazael.psm1">> "%PROFILE_PATH%"
echo if (Test-Path $AzazaelModule) { Import-Module $AzazaelModule }>> "%PROFILE_PATH%"
echo.>> "%PROFILE_PATH%"
echo Write-Host "Azazael/PERSEPHONE Profile Loaded" -ForegroundColor Green>> "%PROFILE_PATH%"

echo [OK] PowerShell profile created
:SKIP_PROFILE

echo.
echo =============================================================================
echo Enabling Administrative Features
echo =============================================================================
echo.

if %ADMIN_MODE%==1 (
    echo [OK] Enabling all administrative features...
    echo.
    
    echo [INFO] Enabling Remote Desktop...
    reg add "HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Terminal Server" /v fDenyTSConnections /t REG_DWORD /d 0 /f >nul
    
    echo [INFO] Enabling File and Printer Sharing...
    netsh advfirewall firewall set rule group="File and Printer Sharing" new enable=Yes >nul
    
    echo [INFO] Setting power plan to High Performance...
    powercfg /s 8c5e7fda-e8bf-4a96-9a85-a6e23a324c9c >nul
    
    echo [INFO] Disabling Windows Defender SmartScreen for development...
    reg add "HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer" /v SmartScreenEnabled /t REG_SZ /d Off /f >nul
    
    echo [OK] Administrative features enabled
) else (
    echo [WARN] Run as Administrator to enable all features
)

echo.
echo =============================================================================
echo Creating Quick Access Shortcuts
echo =============================================================================
echo.

echo Creating CMD Admin shortcut on Desktop...
echo Set oWS = WScript.CreateObject("WScript.Shell")> "%TEMP%\admin_cmd.vbs"
echo sLinkFile = "%USERPROFILE%\Desktop\CMD Admin.lnk">> "%TEMP%\admin_cmd.vbs"
echo Set oLink = oWS.CreateShortcut(sLinkFile)>> "%TEMP%\admin_cmd.vbs"
echo oLink.TargetPath = "cmd.exe">> "%TEMP%\admin_cmd.vbs"
echo oLink.Arguments = "/k mode con: cols=120 lines=50">> "%TEMP%\admin_cmd.vbs"
echo oLink.Description = "CMD with full admin privileges">> "%TEMP%\admin_cmd.vbs"
echo oLink.Save>> "%TEMP%\admin_cmd.vbs"
cscript //nologo "%TEMP%\admin_cmd.vbs" >nul
del "%TEMP%\admin_cmd.vbs" >nul

echo Creating PowerShell Admin shortcut on Desktop...
echo Set oWS = WScript.CreateObject("WScript.Shell")> "%TEMP%\admin_ps.vbs"
echo sLinkFile = "%USERPROFILE%\Desktop\PowerShell Admin.lnk">> "%TEMP%\admin_ps.vbs"
echo Set oLink = oWS.CreateShortcut(sLinkFile)>> "%TEMP%\admin_ps.vbs"
echo oLink.TargetPath = "powershell.exe">> "%TEMP%\admin_ps.vbs"
echo oLink.Arguments = "-NoExit -ExecutionPolicy Bypass">> "%TEMP%\admin_ps.vbs"
echo oLink.Description = "PowerShell with full admin privileges">> "%TEMP%\admin_ps.vbs"
echo oLink.Save>> "%TEMP%\admin_ps.vbs"
cscript //nologo "%TEMP%\admin_ps.vbs" >nul
del "%TEMP%\admin_ps.vbs" >nul

echo [OK] Shortcuts created on Desktop

echo.
echo =============================================================================
echo Summary
echo =============================================================================
echo.
echo [OK] Admin Activation Complete
echo.
echo Quick Access Shortcuts:
echo   - CMD Admin.lnk (Desktop)
echo   - PowerShell Admin.lnk (Desktop)
echo.
echo PowerShell Profile:
echo   - %PROFILE_PATH%
echo.
echo To activate changes in current CMD:
echo   - Close and reopen CMD as Administrator
echo.
echo To activate PowerShell changes:
echo   - Restart PowerShell or run: & $PROFILE
echo.
echo =============================================================================
echo All commands are now available with full scripting capabilities!
echo =============================================================================

endlocal
pause
