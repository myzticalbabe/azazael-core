# archive_graphs_to_d.ps1
# Archives the graph/map artifact sets to D: (USB stick) with robocopy, then
# writes an INDEX.md describing provenance, sizes and known defects.
# ASCII only: PowerShell 5.1 parses .ps1 as ANSI when there is no BOM, so
# non-ASCII characters here corrupt the parse.
$ErrorActionPreference = 'Continue'

$stamp   = '2026-09-11'
$arcRoot = "D:\azazael_graph_archive_$stamp"
$logPath = "C:\Azazael\azazael\logs\archive_graphs_$stamp.log"

function Log($msg) {
    $line = "{0} {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg
    Write-Output $line
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

$sets = @(
    @{ Name = 'azazael_graphify-out'; Src = 'C:\Azazael\azazael\graphify-out' },
    @{ Name = 'azazael_graphs';      Src = 'C:\Azazael\azazael\graphs' },
    @{ Name = 'desktop_atlas_Graphify_Codebase_Atlas_2026-08-27'; Src = 'C:\Users\clint\Desktop\Graphify_Codebase_Atlas_2026-08-27' }
)

New-Item -ItemType Directory -Force -Path $arcRoot | Out-Null
Log "archive root: $arcRoot"
Log ("D free before: {0:N1} GiB" -f ((Get-PSDrive D).Free / 1GB))

$rows = @()
$n = 0
foreach ($s in $sets) {
    $n++
    $dst = Join-Path $arcRoot $s.Name
    if (-not (Test-Path -LiteralPath $s.Src)) {
        Log "[$n/$($sets.Count)] SKIP missing source: $($s.Src)"
        $rows += [pscustomobject]@{ Set = $s.Name; Src = $s.Src; Status = 'MISSING'; MB = 0; Files = 0 }
        continue
    }
    $ms = Get-ChildItem -LiteralPath $s.Src -Recurse -File -Force | Measure-Object -Property Length -Sum
    $mb = [math]::Round([double]$ms.Sum / 1MB, 1)
    Log "[$n/$($sets.Count)] copying $($s.Name) ($mb MB / $($ms.Count) files)"
    Log "        $($s.Src) -> $dst"

    $out = & robocopy.exe $s.Src $dst /E /MT:16 /R:1 /W:1 /NFL /NDL /NP 2>&1
    $rc = $LASTEXITCODE
    $ok = ($rc -lt 8)          # robocopy: 0-7 success (bit flags), >=8 real failure
    $status = if ($ok) { 'OK' } else { "FAIL(rc=$rc)" }
    Log "        rc=$rc -> $status"

    $dm = Get-ChildItem -LiteralPath $dst -Recurse -File -Force | Measure-Object -Property Length -Sum
    $rows += [pscustomobject]@{
        Set    = $s.Name
        Src    = $s.Src
        Status = $status
        MB     = [math]::Round([double]$dm.Sum / 1MB, 1)
        Files  = $dm.Count
    }
    Log ("        copied: {0:N1} MB / {1} files" -f ([double]$dm.Sum / 1MB), $dm.Count)
}

Log ("D free after: {0:N1} GiB" -f ((Get-PSDrive D).Free / 1GB))

$b = New-Object System.Text.StringBuilder
[void]$b.AppendLine("# Graph / Atlas Archive - $stamp")
[void]$b.AppendLine("")
[void]$b.AppendLine("Destination: $arcRoot (removable USB stick, D:)")
[void]$b.AppendLine("")
[void]$b.AppendLine("## Contents")
[void]$b.AppendLine("")
[void]$b.AppendLine("| Set | Source | Status | Size (MB) | Files |")
[void]$b.AppendLine("|---|---|---|---:|---:|")
foreach ($r in $rows) {
    [void]$b.AppendLine("| $($r.Set) | $($r.Src) | $($r.Status) | $($r.MB) | $($r.Files) |")
}
[void]$b.AppendLine("")
[void]$b.AppendLine("## Notes")
[void]$b.AppendLine("")
[void]$b.AppendLine("- Copied with robocopy /E /MT:16 /R:1 /W:1. Exit codes 0-7 are success, >=8 is failure.")
[void]$b.AppendLine("- graphify-out/graph.graphml is 0 bytes in the source - a broken export, archived as-is.")
[void]$b.AppendLine("- graph.json (594 MB) and graph.gexf (743 MB) are the large single-file artifacts.")
[void]$b.AppendLine("- graph.nd*.js and graph.ed*.js must travel with graph.html; without them the")
[void]$b.AppendLine("  atlas viewer renders stale data chunks.")
$b.ToString() | Set-Content -LiteralPath (Join-Path $arcRoot 'INDEX.md') -Encoding UTF8
Log "wrote INDEX.md"
Log "DONE"
