$p = Get-Process python* | Where-Object { $_.Path -like "*py316t*" }
if ($p) { $p | Select-Object Id, StartTime, Path } else { Write-Output "none" }
