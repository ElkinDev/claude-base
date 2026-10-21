<#
The daily kit update, -AutoUpdate on install.ps1 and on the get.ps1 line: a scheduled task of this user, registered
with no elevation, that runs install\auto-update.ps1 from the clone every day with the options of the run that
enabled it. -NoAutoUpdate removes it. Dot-sourced by install.ps1 after install\herdr.ps1 (Get-KitPowerShell).

The task starts conhost.exe --headless, which runs Windows PowerShell on a console that has no window. A task that
starts powershell.exe itself, even with -WindowStyle Hidden, leaves a terminal window open on Windows 11, where
Windows Terminal hosts every new console: a throwaway task of each form, run unelevated on 2026-10-21, showed one
new visible Windows Terminal window for -WindowStyle Hidden and none, with no console window at all, for
conhost --headless.

The task's last run result is conhost's own, which reads 0 whatever the run did: the log and the session-start
notice carry the outcome.

KIT_SCHEDULER, for the tests: a JSON file that stands in for the Task Scheduler. A registration writes the task
there as it was built, and a removal deletes the file, so no test reaches the real scheduler.
#>

$script:KitAutoUpdateTask = 'kit-auto-update'
# Early, before a working day starts. With StartWhenAvailable a day whose trigger was missed (the machine off, or
# nobody signed in) runs as soon as the user is back.
$script:KitAutoUpdateAt = '05:00'

function Get-KitAutoUpdateArguments {
    # What conhost runs: the runner, from this clone, with the options of this run and never a question.
    param([string]$KitRoot, [string]$KitHome, [string]$Permissions, [string]$HerdrChoice, [bool]$NoHookMerge)
    $line = @('--headless', ('"' + (Get-KitPowerShell) + '"'), '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
        'Bypass', '-WindowStyle', 'Hidden', '-File', ('"' + (Join-Path $KitRoot 'install\auto-update.ps1') + '"'),
        '-Dir', ('"' + $KitRoot.TrimEnd('\') + '"'), '-KitHome', ('"' + $KitHome.TrimEnd('\') + '"'),
        '-Permissions', $Permissions)
    # The Herdr answer of this run, as a switch, so the daily run neither asks nor changes it. With no answer (no
    # console and no switch) the daily run has none either, and adds nothing.
    if ($HerdrChoice -eq 'yes') { $line += '-Herdr' } elseif ($HerdrChoice -eq 'no') { $line += '-NoHerdr' }
    if ($NoHookMerge) { $line += '-NoHookMerge' }
    return ($line -join ' ')
}

function Register-KitAutoUpdate {
    param([string]$KitRoot, [string]$KitHome, [string]$Permissions, [string]$HerdrChoice, [bool]$NoHookMerge,
        [switch]$DryRun)
    $name = $script:KitAutoUpdateTask
    if (-not (Test-Path -LiteralPath (Join-Path $KitRoot '.git'))) {
        return "auto-update  not registered: $KitRoot is not a git clone, and the daily run updates it with git pull. Install with the get.ps1 line, or from a clone."
    }
    $conhost   = Join-Path $env:SystemRoot 'System32\conhost.exe'
    $arguments = Get-KitAutoUpdateArguments $KitRoot $KitHome $Permissions $HerdrChoice $NoHookMerge
    if ($DryRun) {
        return @("auto-update  would register the daily task $name, at $($script:KitAutoUpdateAt) or at the next sign-in after a missed day:",
                 "             $conhost $arguments")
    }
    # Built inside the try: a machine without the ScheduledTasks module gets one line, after a finished install.
    try {
        $user      = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $action    = New-ScheduledTaskAction -Execute $conhost -Argument $arguments -WorkingDirectory $KitRoot
        $trigger   = New-ScheduledTaskTrigger -Daily -At $script:KitAutoUpdateAt
        # The cmdlet writes the start in UTC, which moves the run by an hour across a daylight saving change; a local
        # start with no zone keeps it at the same clock time.
        $trigger.StartBoundary = (Get-Date $script:KitAutoUpdateAt).ToString('yyyy-MM-ddTHH:mm:ss')
        $settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
        # Interactive and Limited: it runs as this user, signed in, with no elevation and no stored password.
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        $task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
            -Description "Daily update of the kit cloned at ${KitRoot}: git pull --ff-only, then install.ps1 with the options it was enabled with. Log: $KitHome\logs. Remove with install.ps1 -NoAutoUpdate."
        if ($env:KIT_SCHEDULER) {
            [ordered]@{
                TaskName           = $name
                Execute            = $task.Actions[0].Execute
                Arguments          = $task.Actions[0].Arguments
                WorkingDirectory   = $task.Actions[0].WorkingDirectory
                StartBoundary      = $task.Triggers[0].StartBoundary
                DaysInterval       = $task.Triggers[0].DaysInterval
                StartWhenAvailable = $task.Settings.StartWhenAvailable
                ExecutionTimeLimit = $task.Settings.ExecutionTimeLimit
                LogonType          = [string]$task.Principal.LogonType
                RunLevel           = [string]$task.Principal.RunLevel
                UserId             = $task.Principal.UserId
            } | ConvertTo-Json | Set-Content -LiteralPath $env:KIT_SCHEDULER -Encoding UTF8
        } else {
            Register-ScheduledTask -TaskName $name -InputObject $task -Force | Out-Null
        }
    } catch {
        $script:KitAutoUpdateFailed = $true
        return "auto-update  FAILED to register the daily task ${name}: $($_.Exception.Message)"
    }
    return @("auto-update  registered the daily task $name, at $($script:KitAutoUpdateAt) or at the next sign-in after a missed day,",
             "             with -Permissions $Permissions$(if ($HerdrChoice -eq 'yes') { ' -Herdr' } elseif ($HerdrChoice -eq 'no') { ' -NoHerdr' })$(if ($NoHookMerge) { ' -NoHookMerge' }). Log: $KitHome\logs\auto-update-<date>.log",
             "             The next session start prints one line when it updated the kit or failed. Remove it with -NoAutoUpdate.")
}

function Unregister-KitAutoUpdate {
    param([switch]$DryRun)
    $name  = $script:KitAutoUpdateTask
    $found = if ($env:KIT_SCHEDULER) { Test-Path -LiteralPath $env:KIT_SCHEDULER }
             else { [bool](Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) }
    if (-not $found) { return "auto-update  no daily task $name to remove" }
    if ($DryRun) { return "auto-update  would remove the daily task $name" }
    if ($env:KIT_SCHEDULER) { Remove-Item -LiteralPath $env:KIT_SCHEDULER -Force }
    else { Unregister-ScheduledTask -TaskName $name -Confirm:$false }
    return "auto-update  removed the daily task $name; the logs stay in the kit home's logs folder"
}
