# Crash-capture wrapper: runs any command under ProcDump so an access
# violation yields a full-memory .dmp (with NATIVE stacks via WinDbg
# `!analyze -v`) instead of faulthandler's Windows-blind dead end.
# Usage: powershell -File tools/capture_crash.ps1 -- <command...>
# (uses $args deliberately: a declared param breaks on single-dash flags)
# Dumps land in state/crashdumps/<epoch>.dmp
$argv = @($args)
if ($argv.Count -gt 0 -and $argv[0] -eq "--") { $argv = @($argv | Select-Object -Skip 1) }
$root = Split-Path -Parent $PSScriptRoot
$dir = Join-Path $root "state\crashdumps"
New-Item -ItemType Directory -Path $dir -Force | Out-Null
$exe = Join-Path $PSScriptRoot "procdump\procdump64.exe"
& $exe -accepteula -e -ma -x $dir @argv
exit $LASTEXITCODE
