param(
    [string]$Atlas = "C:\Users\clint\Desktop\Graphify_Codebase_Atlas_2026-08-27",
    [string]$DesktopRoot = "C:\Users\clint\Desktop"
)

$ws = New-Object -ComObject WScript.Shell

# Atlas folder shortcut
$lnkPath = Join-Path $DesktopRoot "Graphify_Codebase_Atlas.lnk"
$s = $ws.CreateShortcut($lnkPath)
$s.TargetPath = $Atlas
$s.Description = "Graphify Codebase Atlas - all repos"
$s.Save()
Write-Host "shortcut -> $Atlas"

# Per-repo viewer shortcuts (azazael)
$azViewer = Join-Path $DesktopRoot "AZAZAEL_COSMOS_VIEWER\cosmos-viewer.html"
if (-not (Test-Path $azViewer)) {
    $azViewer = Join-Path $Atlas "azazael\cosmos-viewer.html"
}
$lnk = Join-Path $DesktopRoot "AZAZAEL_VIEWER.lnk"
$s = $ws.CreateShortcut($lnk)
$s.TargetPath = $azViewer
$s.Description = "AZAZAEL_VIEWER - Cosmos viewer for azazael"
$s.Save()
Write-Host "shortcut AZAZAEL_VIEWER -> $azViewer"

# Project launcher shortcuts (Python-based)
$py = "C:\Users\clint\AppData\Local\Programs\Python\Python315\python.exe"
$projectShortcuts = @(
    @( "Azazael_MetaCluster", "C:\Azazael\azazael\METACLUSTER.py", "C:\Azazael\azazael" ),
    @( "Tigger_CLI", "C:\Users\clint\tigger\cli.py", "C:\Users\clint\tigger" )
)

foreach ($entry in $projectShortcuts) {
    $name = $entry[0]
    $script = $entry[1]
    $workdir = $entry[2]
    if (-not (Test-Path $script)) {
        Write-Host "SKIP $name (script not found: $script)" -ForegroundColor Yellow
        continue
    }
    $lnk = Join-Path $DesktopRoot "Project Launchers\$name.lnk"
    $s = $ws.CreateShortcut($lnk)
    $s.TargetPath = $py
    $s.Arguments = "`"$script`""
    $s.WorkingDirectory = $workdir
    $s.Description = "Launcher: $script"
    $s.Save()
    Write-Host "shortcut $name -> $script"
}

Write-Host "All shortcuts refreshed."
