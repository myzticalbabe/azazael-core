<# .SYNOPSIS
    Schedule TrainingOrchestrator as a Windows Scheduled Task (daily retraining).

.DESCRIPTION
    Creates a Windows Task Scheduler task that runs the TrainingOrchestrator
    SB3 deep RL pipeline daily. The task executes a Python command that:
    1. Warms the worm registry
    2. Creates a TrainingOrchestrator with PPO (5000 timesteps)
    3. Runs execute() once
    4. Records results to LocalMemoryStore

    The task runs as the current user, only when the user is logged on.

.PARAMETER TaskName
    Name for the scheduled task (default: "Azazael-TrainingOrchestrator")

.PARAMETER PythonPath
    Path to the Python executable (default: uses 'python' from PATH)

.PARAMETER ScriptPath
    Path to the training runner script (default: auto-detects azazael project)

.PARAMETER IntervalDaily
    Time to run daily in 24-hour format (default: "03:00" = 3 AM)

.PARAMETER Remove
    If specified, removes the scheduled task instead of creating it

.EXAMPLE
    # Schedule daily training at 3 AM
    powershell -ExecutionPolicy Bypass -File schedule_training_task.ps1

.EXAMPLE
    # Schedule at 2 AM with a custom task name
    powershell -ExecutionPolicy Bypass -File schedule_training_task.ps1 -TaskName "Azazael-RL-Training" -IntervalDaily "02:00"

.EXAMPLE
    # Remove the scheduled task
    powershell -ExecutionPolicy Bypass -File schedule_training_task.ps1 -Remove
#>

param(
    [string]$TaskName = "Azazael-TrainingOrchestrator",
    [string]$PythonPath = "python",
    [string]$ScriptPath = "",
    [string]$IntervalDaily = "03:00",
    [switch]$Remove = $false
)

# Detect project root (where this script lives)
if (-not $ScriptPath) {
    $ScriptPath = Join-Path $PSScriptRoot "run_training_orchestrator.py"
}

$ProjectRoot = Split-Path $PSScriptRoot -Parent

# ── Remove task ───────────────────────────────────────────────────────
if ($Remove) {
    Write-Host "Removing scheduled task '$TaskName'..." -ForegroundColor Yellow
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "  Removed: $TaskName" -ForegroundColor Green
    } else {
        Write-Host "  Task '$TaskName' not found." -ForegroundColor Red
    }
    exit 0
}

# ── Check if task already exists ──────────────────────────────────────
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Task '$TaskName' already exists. Use -Remove first, or choose a different -TaskName." -ForegroundColor Yellow
    Write-Host "  State: $($existing.State)" -ForegroundColor Gray
    exit 1
}

# ── Verify the runner script exists ───────────────────────────────────
if (-not (Test-Path $ScriptPath)) {
    Write-Host "Runner script not found: $ScriptPath" -ForegroundColor Red
    Write-Host "Creating it now..." -ForegroundColor Yellow
}

# ── Parse time ────────────────────────────────────────────────────────
$Hour, $Minute = $IntervalDaily -split ":"
$TimeStr = "$Hour`:$Minute"

Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host "  Azazael TrainingOrchestrator — Task Scheduler" -ForegroundColor Cyan
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host ""
Write-Host "  Task Name:    $TaskName" -ForegroundColor White
Write-Host "  Python Path:  $PythonPath" -ForegroundColor White
Write-Host "  Project Root: $ProjectRoot" -ForegroundColor White
Write-Host "  Daily Time:   $TimeStr" -ForegroundColor White
Write-Host ""

# ── Create the scheduled task action ──────────────────────────────────
# The action runs Python with the training orchestrator runner
$Action = New-ScheduledTaskAction `
    -Execute "$PythonPath" `
    -Argument "`"$ScriptPath`"" `
    -WorkingDirectory "$ProjectRoot"

# ── Trigger: daily at specified time ──────────────────────────────────
$Trigger = New-ScheduledTaskTrigger -Daily -At $TimeStr

# ── Settings ──────────────────────────────────────────────────────────
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries:$false `
    -DontStopIfGoingOnBatteries:$true `
    -StartWhenAvailable:$true `
    -RunOnlyIfNetworkAvailable:$false `
    -WakeToRun:$false `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 0

# ── Principal: current user ───────────────────────────────────────────
$CurrentUser = "$env:USERDOMAIN\$env:USERNAME"
$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel Limited

# ── Register the task ─────────────────────────────────────────────────
try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $Principal `
        -Force

    Write-Host "  Task registered successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Next run: Daily at $TimeStr" -ForegroundColor Green
    Write-Host "  To view:  Get-ScheduledTask -TaskName '$TaskName' | fl" -ForegroundColor Gray
    Write-Host "  To run immediately:" -ForegroundColor Gray
    Write-Host "    Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Gray
    Write-Host "  To remove:" -ForegroundColor Gray
    Write-Host "    Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false" -ForegroundColor Gray

    # Show the task
    $registered = Get-ScheduledTask -TaskName $TaskName
    Write-Host ""
    Write-Host "  State: $($registered.State)" -ForegroundColor Cyan
} catch {
    Write-Host "  [ERROR] Failed to register task: $_" -ForegroundColor Red
    exit 1
}
