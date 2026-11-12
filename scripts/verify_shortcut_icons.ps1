$ws = New-Object -ComObject WScript.Shell
$dir = if ($args.Count -gt 0) { $args[0] } else { "$env:USERPROFILE\Desktop\Project Launchers" }
$names = @(
  'EveUnleashed_Selector','LlamaServer','PAMS','PerformanceMonitor',
  'Ultimate_Agent_Selector','WSL_CrossExplorer','Azazael_MetaCluster',
  'Tigger_CLI','G_EveUnleashed','AOA_Selector'
)
$ok = 0; $bad = 0
foreach ($n in $names) {
  $lnk = $ws.CreateShortcut((Join-Path $dir ($n + '.lnk')))
  $icon = $lnk.IconLocation
  # WScript returns IconLocation as "path,index" - split to check the file
  $iconPath = $icon.Split(',')[0].Trim('"')
  $exists = -not [string]::IsNullOrEmpty($iconPath) -and (Test-Path -LiteralPath $iconPath)
  if ($exists) { $ok++ } else { $bad++ }
  Write-Output ("{0,-26} icon={1}  {2}" -f $n, $exists, $iconPath)
}
Write-Output ("=== {0} with icons, {1} PROBLEMS ===" -f $ok, $bad)
