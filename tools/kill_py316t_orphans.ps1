$p = Get-Process python* | Where-Object { $_.Path -like "*py316t*" }
Write-Output ("found: " + $p.Count)
$p | Stop-Process -Force
Write-Output "killed"
