$ws = New-Object -ComObject WScript.Shell

# 1. Agent Selector -> real launcher (script moved to Ultimate_Agent_Selector)
$lnk = $ws.CreateShortcut("$env:USERPROFILE\Desktop\Agent Selector.lnk")
$lnk.TargetPath = 'C:\Azazael\.venvs\py315t\Scripts\python.exe'
$lnk.Arguments = 'C:\Users\clint\Desktop\Ultimate_Agent_Selector\launcher\agent_orchestrator.py'
$lnk.WorkingDirectory = 'C:\Users\clint\Desktop\Ultimate_Agent_Selector'
$lnk.Description = 'Ultimate Agent Selector - orchestrator'
$lnk.Save()
Write-Output "Agent Selector.lnk -> $($lnk.TargetPath) $($lnk.Arguments)"

# 2. Eve_Unleashed -> exe lives on G: (not Desktop\AZAZAEL_Standalone)
$lnk = $ws.CreateShortcut("$env:USERPROFILE\Desktop\Eve_Unleashed.lnk")
$lnk.TargetPath = 'G:\AZAZAEL_Standalone\Eve_Unleashed_314t.exe'
$lnk.Arguments = ''
$lnk.WorkingDirectory = 'G:\AZAZAEL_Standalone'
# NOTE: the target is a BUILT ARTIFACT whose filename records the interpreter it
# was frozen with; 3.14t itself is retired (2026-10-19) and this is not a launch
# choice. Re-freeze it on 3.15t before promoting it as the current build.
$lnk.Description = 'Eve Unleashed (standalone build, G:)'
$lnk.Save()
Write-Output "Eve_Unleashed.lnk -> $($lnk.TargetPath)"

# 3. Switch Model -> the real picker bat (was Desktop\Llama Server\switch_model.bat)
$lnk = $ws.CreateShortcut("$env:USERPROFILE\Desktop\Switch Model.lnk")
$lnk.TargetPath = 'C:\Users\clint\Desktop\EveUnleashed_Selector\logs\llama_switch_model.bat'
$lnk.Arguments = ''
$lnk.WorkingDirectory = 'C:\Users\clint\Desktop\EveUnleashed_Selector\logs'
$lnk.Description = 'Switch Llama-Server model (numbered picker)'
$lnk.Save()
Write-Output "Switch Model.lnk -> $($lnk.TargetPath)"

# verify all three resolve
foreach ($n in @('Agent Selector','Eve_Unleashed','Switch Model')) {
  $l = $ws.CreateShortcut("$env:USERPROFILE\Desktop\$n.lnk")
  $ok = (Test-Path -LiteralPath $l.TargetPath)
  $wd = Test-Path -LiteralPath $l.WorkingDirectory
  Write-Output ("{0,-16} target={1} wd={2}" -f $n, $ok, $wd)
}
