<#
The daily kit update, -AutoUpdate on install.ps1 and on the get.ps1 line: a scheduled task of this user, registered
with no elevation, that runs <kit home>\kit-auto-update.ps1 (install\auto-update.ps1, which every install copies
there) against the clone every day. -NoAutoUpdate removes it. Dot-sourced by install.ps1 after install\herdr.ps1
(Get-KitPowerShell).

The task runs the copy in the kit home, not the one in the clone, so a clone that was moved or deleted is still
logged and reported instead of stopping the updates in silence. It carries the clone and the kit home and nothing
else: the daily run replays the last finished install's record (Save-KitInstallRecord, written by every install,
hand or daily), reads the permission mode from the live settings.json, and installs with -Unattended, which leaves
everything outside the kit home alone.

One task per kit home: its name ends in a short hash of the kit home's path in one spelling (full path, backslashes,
no trailing separator, case folded; Get-KitHomeKey), so C:/x/.claude and c:\x\.claude\ are one task, two kit homes
on one account keep two, and -NoAutoUpdate removes only its own, with no clone needed (get.ps1 carries the same
hash).

The task starts conhost.exe --headless, which runs Windows PowerShell on a console that has no window. A task that
starts powershell.exe itself, even with -WindowStyle Hidden, leaves a terminal window open on Windows 11, where
Windows Terminal hosts every new console: a throwaway task of each form, run unelevated on 2026-10-21, showed one
new visible Windows Terminal window for -WindowStyle Hidden and none, with no console window at all, for
conhost --headless.

The task's last run result is conhost's own, which reads 0 whatever the run did: the log and the session-start
notice carry the outcome.

KIT_SCHEDULER, for the tests: a JSON file that stands in for the Task Scheduler, an object with one member per task
name. A registration writes its task there as it was built, and a removal deletes its member, so no test reaches the
real scheduler.
#>

# Early, before a working day starts. With StartWhenAvailable a day whose trigger was missed (the machine off, or
# nobody signed in) runs as soon as the user is back.
$script:KitAutoUpdateAt = '05:00'

function Get-KitHomePath {
    # The kit home as a full path with backslashes and no trailing separator.
    param([string]$KitHome)
    return [IO.Path]::GetFullPath($KitHome.Replace('/', '\')).TrimEnd('\')
}

function Get-KitHomeKey {
    # One spelling per kit home, for the task name: the full path, case folded (Windows paths ignore case).
    param([string]$KitHome)
    return (Get-KitHomePath $KitHome).ToLowerInvariant()
}

function Get-KitAutoUpdateName {
    # The same hash is in install\get.ps1 (Get-GetAutoUpdateName), which removes a task with no clone at hand.
    param([string]$KitHome)
    $bytes = [Text.Encoding]::UTF8.GetBytes((Get-KitHomeKey $KitHome))
    $hash  = [Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
    return 'kit-auto-update-' + ([BitConverter]::ToString($hash).Replace('-', '').Substring(0, 8).ToLowerInvariant())
}

function Get-KitAutoUpdateArguments {
    # What conhost runs: the kit home's copy of the runner, never a question, and no option at all: the run reads
    # them from the last install's record and the live settings.json.
    param([string]$KitRoot, [string]$KitHome)
    $homePath = Get-KitHomePath $KitHome
    $line = @('--headless', ('"' + (Get-KitPowerShell) + '"'), '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
        'Bypass', '-WindowStyle', 'Hidden', '-File', ('"' + (Join-Path $homePath 'kit-auto-update.ps1') + '"'),
        '-Dir', ('"' + $KitRoot.TrimEnd('\') + '"'), '-KitHome', ('"' + $homePath + '"'))
    return ($line -join ' ')
}

function Read-KitScheduler {
    # The stand-in's tasks as an ordered map, name to task.
    $tasks = [ordered]@{}
    if (Test-Path -LiteralPath $env:KIT_SCHEDULER) {
        $read = Get-Content -LiteralPath $env:KIT_SCHEDULER -Raw | ConvertFrom-Json
        if ($read) { foreach ($member in $read.PSObject.Properties) { $tasks[$member.Name] = $member.Value } }
    }
    return $tasks
}

function Register-KitAutoUpdate {
    param([string]$KitRoot, [string]$KitHome, [switch]$DryRun)
    $name = Get-KitAutoUpdateName $KitHome
    # A folder, not a file: a git worktree's .git is a file, and the run keeps its lock inside the .git folder.
    if (-not (Test-Path -LiteralPath (Join-Path $KitRoot '.git') -PathType Container)) {
        return "auto-update  not registered: $KitRoot is not a git clone, and the daily run updates it with git pull. Install with the get.ps1 line, or from a clone."
    }
    $conhost   = Join-Path $env:SystemRoot 'System32\conhost.exe'
    $arguments = Get-KitAutoUpdateArguments $KitRoot $KitHome
    if ($DryRun) {
        return @("auto-update  would register the daily task $name, at $($script:KitAutoUpdateAt) or at the next sign-in after a missed day:",
                 "             $conhost $arguments")
    }
    # Built inside the try: a machine without the ScheduledTasks module gets one line, after a finished install.
    try {
        $user      = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $action    = New-ScheduledTaskAction -Execute $conhost -Argument $arguments -WorkingDirectory $KitHome
        $trigger   = New-ScheduledTaskTrigger -Daily -At $script:KitAutoUpdateAt
        # The cmdlet writes the start in UTC, which moves the run by an hour across a daylight saving change; a local
        # start with no zone keeps it at the same clock time.
        $trigger.StartBoundary = (Get-Date $script:KitAutoUpdateAt).ToString('yyyy-MM-ddTHH:mm:ss')
        $settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
        # Interactive and Limited: it runs as this user, signed in, with no elevation and no stored password.
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        $task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
            -Description "Daily update of the kit in $KitHome from the clone at ${KitRoot}: git pull --ff-only, then install.ps1 when the clone moved. Log: $KitHome\logs. Remove with install.ps1 -NoAutoUpdate."
        if ($env:KIT_SCHEDULER) {
            $tasks = Read-KitScheduler
            $tasks[$name] = [ordered]@{
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
            }
            $tasks | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $env:KIT_SCHEDULER -Encoding UTF8
        } else {
            Register-ScheduledTask -TaskName $name -InputObject $task -Force | Out-Null
        }
    } catch {
        $script:KitAutoUpdateFailed = $true
        return "auto-update  FAILED to register the daily task ${name}: $($_.Exception.Message)"
    }
    return @("auto-update  registered the daily task $name, at $($script:KitAutoUpdateAt) or at the next sign-in after a missed day.",
             "             It installs only when the clone moved past the last install, with that install's -NoHookMerge and the",
             "             permission mode settings.json has then, and changes nothing outside the kit home (no PATH, no Herdr).",
             "             Log: $KitHome\logs\auto-update-<date>.log. The next session start prints one line when it updated the kit or failed.",
             "             Remove it with -NoAutoUpdate.")
}

function Unregister-KitAutoUpdate {
    param([string]$KitHome, [switch]$DryRun)
    $name = Get-KitAutoUpdateName $KitHome
    if ($env:KIT_SCHEDULER) { $tasks = Read-KitScheduler; $found = $tasks.Contains($name) }
    else { $found = [bool](Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) }
    if (-not $found) { return "auto-update  no daily task $name for $KitHome to remove" }
    if ($DryRun) { return "auto-update  would remove the daily task $name" }
    if ($env:KIT_SCHEDULER) {
        $tasks.Remove($name)
        $tasks | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $env:KIT_SCHEDULER -Encoding UTF8
    } else {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
    return "auto-update  removed the daily task $name; nothing else was changed, and the logs stay in $KitHome\logs"
}

function Save-KitInstallRecord {
    <#
    What the daily run replays (install\auto-update.ps1): the options the last install that finished ran with (the
    permission mode as rendered, -NoHookMerge) and the clone commit it installed. Every install that reaches its
    end writes it, by hand or by the task, so the daily run follows the user's last choice, never the enabling run's,
    and does not reinstall what a hand update already installed.
    #>
    param([string]$KitRoot, [string]$KitHome, [string]$Permissions, [bool]$NoHookMerge)
    $commit = $null
    if (Test-Path -LiteralPath (Join-Path $KitRoot '.git')) {
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $head = [string](& git -C $KitRoot rev-parse HEAD 2>$null | Select-Object -Last 1)
            if ($LASTEXITCODE -eq 0 -and $head -match '^[0-9a-f]{40}$') { $commit = $head }
        } catch { } finally { $ErrorActionPreference = $previous }
    }
    # No time stamp: a run that changes nothing leaves the record, like every other file, byte for byte as it was.
    $text = [ordered]@{ permissions = $Permissions; noHookMerge = $NoHookMerge; commit = $commit; clone = $KitRoot } |
        ConvertTo-Json
    $path = Join-Path $KitHome '.kit-install.json'
    if ((Test-Path -LiteralPath $path) -and ((Get-Content -LiteralPath $path -Raw).Trim() -eq $text.Trim())) { return }
    Set-Content -LiteralPath $path -Value $text -Encoding UTF8
}
