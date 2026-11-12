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
  $t = $lnk.TargetPath
  $script = $lnk.Arguments.Trim('"').Trim()
  $wd = $lnk.WorkingDirectory
  $tOk = -not [string]::IsNullOrEmpty($t) -and (Test-Path -LiteralPath $t)
  $sOk = -not [string]::IsNullOrEmpty($script) -and (Test-Path -LiteralPath $script)
  $wOk = -not [string]::IsNullOrEmpty($wd) -and (Test-Path -LiteralPath $wd)
  if ($tOk -and $sOk -and $wOk) { $ok++ } else { $bad++ }
  Write-Output ("{0,-26} target={1} script={2} workdir={3}" -f $n, $tOk, $sOk, $wOk)
}
Write-Output ("=== {0} OK, {1} PROBLEMS ===" -f $ok, $bad)
