# PowerShell tab completion for KLINK CLI
# Place at: $PROFILE or source via . $PROFILE

$script:KLINK_COMMANDS = @(
    'status', 'insights', 'diag', 'debug', 'test', 'pams', 'llama',
    'metaworm', 'worms', 'worm', 'run', 'bibl', 'crawl', 'hydra',
    'check', 'optimize', 'scan', 'fix', 'tera', 'system', 'health',
    'klink', 'help', 'version', 'analyze', 'generate', 'export',
    'wake', 'update', 'cluster', 'report', 'find', 'history'
)

$script:KLINK_SUBCOMMANDS = @{
    'metaworm' = @('status', 'start', 'run', 'morph', 'demo', 'export')
    'debug'    = @('--full', '--save', '--syntax', '--paths', '--health', '--pams', '--llama', '--fix', '--output')
    'worm'     = @('systemz', 'processz', 'diskz', 'networxz', 'clockz', 'all')
    'bibl'     = @('--all', '--delta', '--folder', '--popup')
    'hydra'    = @('--discover', '--run', '--status', '--stages', '--popup')
}

Register-ArgumentCompleter -CommandName python -ParameterName Command -ScriptBlock {
    param($commandName, $parameterName, $wordToComplete, $commandAst, $fakeBoundParameters)
    
    $line = $commandAst.ToString()
    
    # Only complete for klink.py
    if ($line -notmatch 'klink\.py') { return }
    
    # If cursor is after a subcommand, complete subcommand options
    foreach ($cmd in $script:KLINK_SUBCOMMANDS.Keys) {
        if ($line -match "$cmd\s+$wordToComplete`$") {
            return $script:KLINK_SUBCOMMANDS[$cmd] | Where-Object { $_ -like "$wordToComplete*" } | ForEach-Object {
                [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
            }
        }
    }
    
    # Complete main commands
    return $script:KLINK_COMMANDS | Where-Object { $_ -like "$wordToComplete*" } | ForEach-Object {
        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
    }
}

Write-Host "[KLINK] PowerShell tab completion loaded ($($script:KLINK_COMMANDS.Count) commands)" -ForegroundColor Green
