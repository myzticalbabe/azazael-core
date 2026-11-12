$ErrorActionPreference = 'Stop'
$desktop = 'C:\Users\clint\Desktop'
$group = Join-Path $desktop 'Project Launchers'
$names = @(
  'EveUnleashed_Selector','LlamaServer','PAMS','PerformanceMonitor',
  'Ultimate_Agent_Selector','WSL_CrossExplorer','Azazael_MetaCluster',
  'Tigger_CLI','G_EveUnleashed','AOA_Selector'
)

if (-not (Test-Path -LiteralPath $group)) {
  New-Item -ItemType Directory -Path $group | Out-Null
  Write-Output "created $group"
}

$moved = 0
foreach ($n in $names) {
  $src = Join-Path $desktop ($n + '.lnk')
  $dst = Join-Path $group ($n + '.lnk')
  if (Test-Path -LiteralPath $src) {
    if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force }
    Move-Item -LiteralPath $src -Destination $dst
    $moved++
    Write-Output "moved $n.lnk -> Project Launchers\"
  } else {
    Write-Output "skip  $n.lnk (not on Desktop root)"
  }
}
Write-Output ("moved {0} shortcuts" -f $moved)

# verify every shortcut in the folder still resolves
$ws = New-Object -ComObject WScript.Shell
$ok = 0; $bad = 0
foreach ($n in $names) {
  $lnk = $ws.CreateShortcut((Join-Path $group ($n + '.lnk')))
  $t = -not [string]::IsNullOrEmpty($lnk.TargetPath) -and (Test-Path -LiteralPath $lnk.TargetPath)
  $script = $lnk.Arguments.Trim('"').Trim()
  $s = -not [string]::IsNullOrEmpty($script) -and (Test-Path -LiteralPath $script)
  $w = -not [string]::IsNullOrEmpty($lnk.WorkingDirectory) -and (Test-Path -LiteralPath $lnk.WorkingDirectory)
  if ($t -and $s -and $w) { $ok++ } else { $bad++ }
  Write-Output ("{0,-26} target={1} script={2} workdir={3}" -f $n, $t, $s, $w)
}
Write-Output ("=== {0} OK, {1} PROBLEMS in {2} ===" -f $ok, $bad, $group)
