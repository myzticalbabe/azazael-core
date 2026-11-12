$ErrorActionPreference = 'Continue'
$ws = New-Object -ComObject WScript.Shell
$root = "$env:USERPROFILE\Desktop"
$sub = Join-Path $root 'Project Launchers'

$all = @()
Get-ChildItem -LiteralPath $root -Filter *.lnk -File | ForEach-Object { $all += $_.FullName }
if (Test-Path -LiteralPath $sub) {
  Get-ChildItem -LiteralPath $sub -Filter *.lnk -File | ForEach-Object { $all += $_.FullName }
}

# these are pure launch helpers / test leftovers / viewer shortcuts — skip the noise
$skip = @('PIN_NP','PIN_TEST','Test_OpenMono','Start_OpenMono_Agent')

$results = @()
foreach ($p in ($all | Sort-Object)) {
  $name = [System.IO.Path]::GetFileNameWithoutExtension($p)
  if ($name -in $skip) { continue }
  $lnk = $ws.CreateShortcut($p)
  $t = $lnk.TargetPath
  if (-not $t -or -not (Test-Path -LiteralPath $t)) {
    $results += [pscustomobject]@{ Name=$name; Result='BROKEN-TARGET'; Detail='target missing: '+$t }
    continue
  }
  $args2 = $lnk.Arguments
  if ([string]::IsNullOrEmpty($args2)) { $args2 = ' ' }  # Start-Process rejects null/empty
  $wd = $lnk.WorkingDirectory
  if (-not $wd -or -not (Test-Path -LiteralPath $wd)) { $wd = '' }
  $proc = $null
  try {
    $splat = @{ FilePath = $t; ArgumentList = $args2; PassThru = $true; WindowStyle = 'Hidden' }
    if ($wd) { $splat.WorkingDirectory = $wd }
    $proc = Start-Process @splat
  } catch {
    $results += [pscustomobject]@{ Name=$name; Result='LAUNCH-ERR'; Detail=$_.Exception.Message }
    continue
  }
  # wait up to 6s: server/GUI stays alive, CLI exits
  $waited = 0
  while ($waited -lt 6) {
    Start-Sleep -Seconds 1
    $waited++
    $proc.Refresh()
    if ($proc.HasExited) { break }
  }
  if ($proc.HasExited) {
    $results += [pscustomobject]@{ Name=$name; Result=('EXITED code ' + $proc.ExitCode); Detail='after '+$waited+'s' }
  } else {
    $results += [pscustomobject]@{ Name=$name; Result='RUNNING'; Detail='alive after '+$waited+'s (killed)' }
    $null = taskkill /PID $proc.Id /T /F 2>$null
  }
}

Write-Output ("=== {0} shortcuts tested ===" -f $results.Count)
$results | Format-Table -AutoSize -Wrap
