$ErrorActionPreference = 'Continue'
$ws = New-Object -ComObject WScript.Shell
$desktop = 'C:\Users\clint\Desktop'

$names = @(
  'EveUnleashed_Selector',
  'LlamaServer',
  'PAMS',
  'PerformanceMonitor',
  'Ultimate_Agent_Selector',
  'WSL_CrossExplorer',
  'Azazael_MetaCluster',
  'Tigger_CLI',
  'G_EveUnleashed',
  'AOA_Selector'
)

foreach ($n in $names) {
  $lnkPath = Join-Path $desktop ($n + '.lnk')
  $lnk = $ws.CreateShortcut($lnkPath)
  $target = $lnk.TargetPath
  $args   = $lnk.Arguments
  $workdir = $lnk.WorkingDirectory

  if (-not $target) {
    Write-Output ("MISSING  {0,-26} shortcut not found or empty target" -f $n)
    continue
  }
  if (-not (Test-Path -LiteralPath $target)) {
    Write-Output ("BADTGT   {0,-26} target does not exist: {1}" -f $n, $target)
    continue
  }

  # Extract the script path from the quoted arguments
  $scriptPath = $args.Trim('"').Trim()
  $scriptExists = $false
  if ($scriptPath) { $scriptExists = Test-Path -LiteralPath $scriptPath }

  Write-Output ("==== {0} ====" -f $n)
  Write-Output ("  target   : {0}" -f $target)
  Write-Output ("  args     : {0}" -f $args)
  Write-Output ("  workdir  : {0}" -f $workdir)
  Write-Output ("  scriptOK : {0}" -f $scriptExists)

  $p = $null
  try {
    $p = Start-Process -FilePath $target -ArgumentList $args -WorkingDirectory $workdir -PassThru -WindowStyle Minimized
    Write-Output ("  launched : PID {0}" -f $p.Id)
  } catch {
    Write-Output ("  LAUNCH FAILED: {0}" -f $_.Exception.Message)
    continue
  }

  # Give it a few seconds to either stay alive (server/GUI) or exit (one-shot CLI)
  $waited = 0
  $alive = $true
  while ($waited -lt 10) {
    Start-Sleep -Seconds 1
    $waited++
    $p.Refresh()
    if ($p.HasExited) { $alive = $false; break }
  }

  if ($alive) {
    Write-Output ("  RESULT   : STARTED  (process alive after {0}s, PID {1})" -f $waited, $p.Id)
  } else {
    Write-Output ("  RESULT   : EXITED   (exit code {0} after {1}s)" -f $p.ExitCode, $waited)
  }

  # Kill the process tree so we don't leave orphans / port holders
  if (-not $p.HasExited) {
    $null = taskkill /PID $p.Id /T /F 2>$null
    Write-Output ("  cleaned  : PID {0} tree terminated" -f $p.Id)
  }
  Start-Sleep -Seconds 1
}
Write-Output "=== verification complete ==="
