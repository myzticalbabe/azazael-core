$ws = New-Object -ComObject WScript.Shell
$dir = if ($args.Count -gt 0) { $args[0] } else { "$env:USERPROFILE\Desktop" }
$sub = Join-Path $dir 'Project Launchers'

$all = @()
# Desktop root .lnk
Get-ChildItem -LiteralPath $dir -Filter *.lnk -File | ForEach-Object { $all += $_.FullName }
# Project Launchers subfolder
if (Test-Path -LiteralPath $sub) {
  Get-ChildItem -LiteralPath $sub -Filter *.lnk -File | ForEach-Object { $all += $_.FullName }
}

Write-Output ("=== {0} shortcuts mapped ===" -f $all.Count)
$broken = 0
foreach ($p in ($all | Sort-Object)) {
  $lnk = $ws.CreateShortcut($p)
  $t = $lnk.TargetPath
  $tOk = (-not [string]::IsNullOrEmpty($t)) -and (Test-Path -LiteralPath $t)
  # extract script from args (quoted)
  $args2 = $lnk.Arguments
  $script = $args2 -replace '^"', '' -replace '"$', '' -replace '""', '"'
  $script = $script.Trim()
  $sOk = $false
  if ($script -and $script -match '\.(py|sh|bat|ps1|exe|cmd|html)$' -and (Test-Path -LiteralPath $script)) { $sOk = $true }
  $wOk = (-not [string]::IsNullOrEmpty($lnk.WorkingDirectory)) -and (Test-Path -LiteralPath $lnk.WorkingDirectory)
  $icon = $lnk.IconLocation
  $iconPath = $icon.Split(',')[0].Trim('"')
  $iOk = [string]::IsNullOrEmpty($iconPath) -or (Test-Path -LiteralPath $iconPath)
  if (-not ($tOk -and $wOk)) { $broken++ }
  $status = if ($tOk -and $wOk) { 'OK ' } else { 'BAD ' }
  Write-Output ("{0} {1}" -f $status, ([System.IO.Path]::GetFileName($p)))
  Write-Output ("      target={0} | wd={1} | icon={2}" -f $tOk, $wOk, $iOk)
  Write-Output ("      {0} {1}" -f $t, $args2)
  if ($script) { Write-Output ("      script-exists={0} : {1}" -f $sOk, $script) }
}
Write-Output ("=== {0} BROKEN ===" -f $broken)
