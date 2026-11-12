<#
.SYNOPSIS
    Azazael Unified - Local APK Engineering Environment
    
.DESCRIPTION
    Sets up and manages local Android APK engineering environment
    
.EXAMPLE
    .\apk_engineering.ps1 -Install
#>

param(
    [switch]$Install,
    [switch]$Build,
    [switch]$Sign,
    [switch]$Decompile,
    [string]$APKPath,
    [string]$ProjectPath
)

$ErrorActionPreference = "Stop"

$UnifiedRoot = "C:\Users\clint\Desktop\Azazael_UNIFIED"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Azazael - Local APK Engineering" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Configuration
$Config = @{
    JavaHome = "C:\Program Files\Eclipse Adoptium\jdk-17.0.12.7-hotspot"
    AndroidSDK = "C:\Users\clint\AppData\Local\Android\Sdk"
    GradleHome = "C:\Gradle\gradle-8.5"
    Apktool = "$UnifiedRoot\tools\apktool\apktool.jar"
    SignAPK = "$UnifiedRoot\tools\signapk\signapk.jar"
    Keystore = "$UnifiedRoot\config\azazael.keystore"
}

function Test-Dependencies {
    Write-Host "[CHECK] Testing dependencies..." -ForegroundColor Cyan
    Write-Host ""
    
    $checks = @(
        @{
            Name = "Java JDK"
            Path = "$($Config.JavaHome)\bin\java.exe"
            Required = $true
        },
        @{
            Name = "Android SDK"
            Path = "$($Config.AndroidSDK)\platform-tools\adb.exe"
            Required = $true
        },
        @{
            Name = "Gradle"
            Path = "$($Config.GradleHome)\bin\gradle.bat"
            Required = $false
        },
        @{
            Name = "Apktool"
            Path = $Config.Apktool
            Required = $false
        },
        @{
            Name = "APKSigner"
            Path = $Config.SignAPK
            Required = $false
        }
    )
    
    $allPassed = $true
    
    foreach ($check in $checks) {
        $exists = Test-Path $check.Path
        $status = if ($exists) { "OK" } else { if ($check.Required) { "MISSING" } else { "OPTIONAL" } }
        $color = if ($exists) { "Green" } elseif ($check.Required) { "Red" } else { "Yellow" }
        
        Write-Host "  [$status] $($check.Name)" -ForegroundColor $color
        
        if (-not $exists -and $check.Required) {
            $allPassed = $false
        }
    }
    
    Write-Host ""
    return $allPassed
}

function Install-Dependencies {
    Write-Host "[INSTALL] Setting up APK engineering environment..." -ForegroundColor Cyan
    Write-Host ""
    
    $toolsDir = "$UnifiedRoot\tools"
    if (-not (Test-Path $toolsDir)) {
        New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
    }
    
    Write-Host "Required installations:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "1. Java JDK 17+" -ForegroundColor White
    Write-Host "   Download: https://adoptium.net/" -ForegroundColor Gray
    Write-Host "   Install to: C:\Program Files\Eclipse Adoptium\" -ForegroundColor Gray
    Write-Host ""
    Write-Host "2. Android SDK" -ForegroundColor White
    Write-Host "   Download: https://developer.android.com/studio" -ForegroundColor Gray
    Write-Host "   Or: winget install Google.AndroidSDK" -ForegroundColor Gray
    Write-Host ""
    Write-Host "3. Gradle 8.x" -ForegroundColor White
    Write-Host "   Download: https://gradle.org/releases/" -ForegroundColor Gray
    Write-Host "   Install to: C:\Gradle\" -ForegroundColor Gray
    Write-Host ""
    Write-Host "4. Apktool (automatic)" -ForegroundColor White
    
    # Download Apktool
    $apktoolDir = "$toolsDir\apktool"
    if (-not (Test-Path $apktoolDir)) {
        New-Item -ItemType Directory -Path $apktoolDir -Force | Out-Null
    }
    
    $apktoolUrl = "https://bitbucket.org/iBotPeaches/apktool/downloads/apktool_2.9.3.jar"
    $apktoolDest = "$apktoolDir\apktool.jar"
    
    try {
        Invoke-WebRequest -Uri $apktoolUrl -OutFile $apktoolDest -ErrorAction Stop
        Write-Host "  [OK] Apktool downloaded" -ForegroundColor Green
    }
    catch {
        Write-Host "  [WARN] Could not download Apktool: $_" -ForegroundColor Yellow
    }
    
    Write-Host ""
    Write-Host "5. APKSigner (automatic)" -ForegroundColor White
    
    # Create signapk directory
    $signDir = "$toolsDir\signapk"
    if (-not (Test-Path $signDir)) {
        New-Item -ItemType Directory -Path $signDir -Force | Out-Null
    }
    
    Write-Host ""
    Write-Host "6. Generate keystore (automatic)" -ForegroundColor White
    
    # Generate keystore if not exists
    if (-not (Test-Path $Config.Keystore)) {
        Write-Host "  Generating debug keystore..." -ForegroundColor Cyan
        $keytool = "$($Config.JavaHome)\bin\keytool.exe"
        if (Test-Path $keytool) {
            & $keytool -genkeypair -v -keystore $Config.Keystore `
                -storepass azazael -alias azazael `
                -keypass azazael -keyalg RSA -keysize 2048 `
                -validity 10000 -storetype JKS 2>&1 | Out-Null
            Write-Host "  [OK] Keystore created" -ForegroundColor Green
        }
        else {
            Write-Host "  [WARN] keytool not found, generate keystore manually" -ForegroundColor Yellow
        }
    }
    
    Write-Host ""
    Write-Host "Environment variables to set:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  JAVA_HOME=$($Config.JavaHome)" -ForegroundColor White
    Write-Host "  ANDROID_HOME=$($Config.AndroidSDK)" -ForegroundColor White
    Write-Host "  GRADLE_HOME=$($Config.GradleHome)" -ForegroundColor White
    Write-Host ""
    Write-Host "  Add to PATH:" -ForegroundColor Cyan
    Write-Host "    %JAVA_HOME%\bin" -ForegroundColor White
    Write-Host "    %ANDROID_HOME%\platform-tools" -ForegroundColor White
    Write-Host "    %ANDROID_HOME%\cmdline-tools\latest\bin" -ForegroundColor White
    Write-Host "    %GRADLE_HOME%\bin" -ForegroundColor White
}

function Decompile-APK {
    param([string]$APK)
    
    if (-not (Test-Path $APK)) {
        Write-Host "[ERROR] APK not found: $APK" -ForegroundColor Red
        return
    }
    
    Write-Host "[DECOMPILE] Decompiling APK..." -ForegroundColor Cyan
    Write-Host "  File: $APK" -ForegroundColor White
    
    $java = "$($Config.JavaHome)\bin\java.exe"
    $apktool = $Config.Apktool
    $outputDir = $APK.Replace(".apk", "_decompiled")
    
    if (Test-Path $java -and Test-Path $apktool) {
        & $java -jar $apktool d -f $APK -o $outputDir 2>&1
        Write-Host "  [OK] Decompiled to: $outputDir" -ForegroundColor Green
    }
    else {
        Write-Host "  [ERROR] Missing Java or Apktool" -ForegroundColor Red
    }
}

function Build-APK {
    param([string]$Project)
    
    if (-not (Test-Path $Project)) {
        Write-Host "[ERROR] Project not found: $Project" -ForegroundColor Red
        return
    }
    
    Write-Host "[BUILD] Building APK..." -ForegroundColor Cyan
    Write-Host "  Project: $Project" -ForegroundColor White
    
    $gradle = "$($Config.GradleHome)\bin\gradle.bat"
    
    if (Test-Path $gradle) {
        Push-Location $Project
        & $gradle assembleDebug 2>&1
        Pop-Location
        Write-Host "  [OK] APK built" -ForegroundColor Green
    }
    else {
        Write-Host "  [ERROR] Gradle not found" -ForegroundColor Red
        Write-Host "  Falling back to aapt2..." -ForegroundColor Yellow
        
        # Basic aapt2 build
        $aapt2 = "$($Config.AndroidSDK)\build-tools\latest\aapt2.exe"
        if (Test-Path $aapt2) {
            Write-Host "  Using aapt2 for basic build" -ForegroundColor Cyan
        }
    }
}

function Sign-APK {
    param([string]$APK)
    
    if (-not (Test-Path $APK)) {
        Write-Host "[ERROR] APK not found: $APK" -ForegroundColor Red
        return
    }
    
    Write-Host "[SIGN] Signing APK..." -ForegroundColor Cyan
    Write-Host "  File: $APK" -ForegroundColor White
    
    $java = "$($Config.JavaHome)\bin\java.exe"
    $signer = $Config.SignAPK
    
    if (Test-Path $java -and (Test-Path $signer -or $true)) {
        # Use apksigner if available
        $apksigner = "$($Config.AndroidSDK)\build-tools\latest\apksigner.bat"
        
        if (Test-Path $apksigner) {
            & $apksigner sign --ks $Config.Keystore --ks-pass pass:azazael --ks-key-alias azazael --key-pass pass:azazael $APK
            Write-Host "  [OK] APK signed with apksigner" -ForegroundColor Green
        }
        else {
            Write-Host "  [WARN] apksigner not found" -ForegroundColor Yellow
            Write-Host "  Install Android SDK build-tools" -ForegroundColor White
        }
    }
    else {
        Write-Host "  [ERROR] Missing Java" -ForegroundColor Red
    }
}

function Align-APK {
    param([string]$APK)
    
    if (-not (Test-Path $APK)) {
        Write-Host "[ERROR] APK not found: $APK" -ForegroundColor Red
        return
    }
    
    Write-Host "[ALIGN] Aligning APK..." -ForegroundColor Cyan
    
    $zipalign = "$($Config.AndroidSDK)\build-tools\latest\zipalign.exe"
    
    if (Test-Path $zipalign) {
        $alignedAPK = $APK.Replace(".apk", "_aligned.apk")
        & $zipalign -f 4 $APK $alignedAPK
        Write-Host "  [OK] Aligned: $alignedAPK" -ForegroundColor Green
    }
    else {
        Write-Host "  [WARN] zipalign not found" -ForegroundColor Yellow
    }
}

function New-AndroidProject {
    param([string]$Name, [string]$Package)
    
    Write-Host "[CREATE] Creating Android project..." -ForegroundColor Cyan
    Write-Host "  Name: $Name" -ForegroundColor White
    Write-Host "  Package: $Package" -ForegroundColor White
    
    $projectDir = "$UnifiedRoot\projects\$Name"
    New-Item -ItemType Directory -Path $projectDir -Force | Out-Null
    
    # Create build.gradle
    $buildGradle = @"
plugins {
    id 'com.android.application' version '8.2.0' apply false
    id 'org.jetbrains.kotlin.android' version '1.9.20' apply false
}

allprojects {
    repositories {
        google()
        mavenCentral()
    }
}

tasks.register('clean', Delete) {
    delete rootProject.buildDir
}
"@
    
    # Create app/build.gradle
    $appBuildGradle = @"
plugins {
    id 'com.android.application'
    id 'org.jetbrains.kotlin.android'
}

android {
    namespace '$Package'
    compileSdk 34

    defaultConfig {
        applicationId "$Package"
        minSdk 26
        targetSdk 34
        versionCode 1
        versionName "1.0.0"
    }

    buildTypes {
        release {
            minifyEnabled true
            proguardFiles getDefaultProguardFile('proguard-android-optimize.txt'), 'proguard-rules.pro'
        }
        debug {
            minifyEnabled false
        }
    }
    
    compileOptions {
        sourceCompatibility JavaVersion.VERSION_17
        targetCompatibility JavaVersion.VERSION.VERSION_17
    }
    
    kotlinOptions {
        jvmTarget = '17'
    }
}

dependencies {
    implementation 'androidx.core:core-ktx:1.12.0'
    implementation 'androidx.appcompat:appcompat:1.6.1'
    implementation 'com.google.android.material:material:1.10.0'
    implementation 'org.jetbrains.kotlin:kotlin-stdlib:1.9.20'
}
"@
    
    # Create settings.gradle
    $settingsGradle = @"
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "$Name"
include ':app'
"@
    
    $buildGradle | Out-File "$projectDir\build.gradle" -Encoding UTF8
    $appBuildGradle | Out-File "$projectDir\app\build.gradle" -Encoding UTF8
    $settingsGradle | Out-File "$projectDir\settings.gradle" -Encoding UTF8
    
    Write-Host "  [OK] Project created: $projectDir" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Next steps:" -ForegroundColor Cyan
    Write-Host "    cd $projectDir" -ForegroundColor White
    Write-Host "    gradle build" -ForegroundColor White
}

# Main execution
if ($Install) {
    Install-Dependencies
}

if ($Decompile -and $APKPath) {
    Decompile-APK -APK $APKPath
}

if ($Build -and $ProjectPath) {
    Build-APK -Project $ProjectPath
}

if ($Sign -and $APKPath) {
    Sign-APK -APK $APKPath
    Align-APK -APK $APKPath
}

if (-not $Install -and -not $Build -and -not $Sign -and -not $Decompile) {
    Test-Dependencies
    Write-Host ""
    Write-Host "Commands:" -ForegroundColor Cyan
    Write-Host "  .\apk_engineering.ps1 -Install              # Install dependencies" -ForegroundColor White
    Write-Host "  .\apk_engineering.ps1 -Decompile -APKPath <file>   # Decompile APK" -ForegroundColor White
    Write-Host "  .\apk_engineering.ps1 -Build -ProjectPath <dir>   # Build APK" -ForegroundColor White
    Write-Host "  .\apk_engineering.ps1 -Sign -APKPath <file>       # Sign APK" -ForegroundColor White
    Write-Host ""
}
