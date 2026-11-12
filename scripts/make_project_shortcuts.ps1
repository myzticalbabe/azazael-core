$ErrorActionPreference = 'Continue'
$ws = New-Object -ComObject WScript.Shell
$py = 'C:\Users\clint\AppData\Local\Programs\Python\Python315\python.exe'
$iconDir = 'C:\Azazael\azazael\scripts\icons'
$groupDir = 'C:\Users\clint\Desktop\Project Launchers'

$projects = @(
  @{ name = 'EveUnleashed_Selector'; script = 'C:\Users\clint\Desktop\EveUnleashed_Selector\run.py';  workdir = 'C:\Users\clint\Desktop\EveUnleashed_Selector' },
  @{ name = 'LlamaServer';           script = 'C:\Users\clint\Desktop\LlamaServer\run.py';             workdir = 'C:\Users\clint\Desktop\LlamaServer' },
  @{ name = 'PAMS';                  script = 'C:\Users\clint\Desktop\PAMS\run.py';                    workdir = 'C:\Users\clint\Desktop\PAMS' },
  @{ name = 'PerformanceMonitor';    script = 'C:\Users\clint\Desktop\PerformanceMonitor\run.py';      workdir = 'C:\Users\clint\Desktop\PerformanceMonitor' },
  @{ name = 'Ultimate_Agent_Selector'; script = 'C:\Users\clint\Desktop\Ultimate_Agent_Selector\run.py'; workdir = 'C:\Users\clint\Desktop\Ultimate_Agent_Selector' },
  @{ name = 'WSL_CrossExplorer';     script = 'C:\Users\clint\Desktop\WSL_CrossExplorer\run.py';       workdir = 'C:\Users\clint\Desktop\WSL_CrossExplorer' },
  @{ name = 'Azazael_MetaCluster';   script = 'C:\Azazael\azazael\METACLUSTER.py';                 workdir = 'C:\Azazael\azazael' },
  @{ name = 'Tigger_CLI';            script = 'C:\Users\clint\tigger\cli.py';                          workdir = 'C:\Users\clint\tigger' },
  @{ name = 'G_EveUnleashed';        script = 'G:\AZAZAEL\EveUnleashed\run.py';                       workdir = 'G:\AZAZAEL\EveUnleashed' },
  @{ name = 'AOA_Selector';          script = 'C:\Users\clint\Desktop\AOA\cli\aoa_cli.py';            workdir = 'C:\Users\clint\Desktop\AOA' }
)

if (-not (Test-Path -LiteralPath $groupDir)) { New-Item -ItemType Directory -Path $groupDir | Out-Null }

foreach ($p in $projects) {
  try {
    $lnkPath = Join-Path $groupDir ($p.name + '.lnk')
    $lnk = $ws.CreateShortcut($lnkPath)
    $lnk.TargetPath = $py
    $lnk.Arguments = '""' + $p.script + '""'
    $lnk.WorkingDirectory = $p.workdir
    $lnk.Description = 'Launcher: ' + $p.script
    $icon = Join-Path $iconDir ($p.name + '.ico')
    if (Test-Path $icon) { $lnk.IconLocation = "`"$icon`",0" }
    $lnk.Save()
    $exists = Test-Path $lnkPath
    Write-Output ("OK  {0}  exists={1}" -f $p.name, $exists)
  } catch {
    Write-Output ("ERR {0}  {1}" -f $p.name, $_.Exception.Message)
  }
}
