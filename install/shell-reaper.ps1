#requires -Version 5.1
<#
install\shell-reaper.ps1 - register, or remove, the scheduled task that runs <kit home>\tools\shell-reaper.py every
10 minutes: it ends Claude Code Bash tool shells that outlived their call, which otherwise keep their agent alive
(the tool's docstring has the rules). An optional install step, run by hand after install.ps1; install.ps1 never
runs it.

  powershell -ExecutionPolicy Bypass -File .\install\shell-reaper.ps1 -DryRun     what it would register
  powershell -ExecutionPolicy Bypass -File .\install\shell-reaper.ps1             register it
  powershell -ExecutionPolicy Bypass -File .\install\shell-reaper.ps1 -Remove     remove it

It needs shell-reaper.py and msys_tree.py in <kit home>\tools, where install.ps1 copies the kit's claude\tools
folder: the first loads the second by path from beside itself (pythonw -I keeps that folder off sys.path), so it
refuses, with nothing registered, when either is missing.

The task starts pythonw.exe directly, with no powershell or conhost: pythonw has no console, and the tool starts each
child (powershell for the process table, ps.exe, taskkill) with no window, so a run opens none. It runs as this user,
signed in, with no elevation and no stored password: every shell it may end is the user's own. The trigger is daily at
00:00, repeated every 10 minutes for a day, so it survives a reboot; a tick missed while the machine slept is not
caught up (StartWhenAvailable off). A run reads three tables and ends at most 5 trees, 14 minutes in the worst case,
so the task stops a run at 15 minutes, and IgnoreNew keeps a slow run from overlapping the next tick.

One task per kit home: kit-shell-reaper- and the short hash of the kit home's path that install\schedule.ps1 gives
the daily update (Get-KitHomeKey), so a second kit home on the account keeps its own and -Remove takes only its own.
The task passes the kit home to the tool (--home), so its log (<kit home>\logs\shell-reaper.log), its stop file
(<kit home>\shell-reaper.stop: present, a run writes one line and touches nothing) and its config
(<kit home>\shell-reaper.json, optional) are the ones of the kit home it was registered for.

pythonw.exe is the one beside the python.exe on PATH, as Python itself reports it (sys.executable), or the file
-Python names.

KIT_SCHEDULER, for the tests: the same stand-in install\schedule.ps1 reads, a JSON file with one member per task
name. A registration writes its task there as it was built, and a removal deletes its member, so no test reaches the
real scheduler.
#>
param(
    # Print the task it would register, or remove, and change nothing.
    [switch]$DryRun,
    # Remove this kit home's task and do nothing else.
    [switch]$Remove,
    # The pythonw.exe the task runs; by default the one beside the python.exe on PATH.
    [string]$Python
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')
. (Join-Path $PSScriptRoot 'schedule.ps1')

function Get-ShellReaperName {
    # The hash Get-KitAutoUpdateName uses, over the same spelling of the kit home.
    param([string]$KitHome)
    $bytes = [Text.Encoding]::UTF8.GetBytes((Get-KitHomeKey $KitHome))
    $hash  = [Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
    return 'kit-shell-reaper-' + ([BitConverter]::ToString($hash).Replace('-', '').Substring(0, 8).ToLowerInvariant())
}

function Find-ShellReaperPythonw {
    # The pythonw.exe beside the python.exe on PATH, by the path Python reports for itself; $null when there is none.
    $python = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $python) { return $null }
    $exe = & $python.Source -c 'import sys; print(sys.executable)' 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $exe) { return $null }
    $pythonw = Join-Path (Split-Path -Parent ([string]$exe).Trim()) 'pythonw.exe'
    if (Test-Path -LiteralPath $pythonw -PathType Leaf) { return $pythonw }
    return $null
}

function Write-ShellReaperScheduler {
    param($Tasks)
    $Tasks | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $env:KIT_SCHEDULER -Encoding UTF8
}

if ($Remove -and $Python) {
    [Console]::Error.WriteLine('refused: -Remove takes no -Python. Nothing was changed.')
    exit 2
}
$kitHome = Get-KitHomePath (Get-KitHome)
$name    = Get-ShellReaperName $kitHome

if ($Remove) {
    if ($env:KIT_SCHEDULER) { $tasks = Read-KitScheduler; $found = $tasks.Contains($name) }
    else { $found = [bool](Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) }
    if (-not $found) { Write-Output "shell-reaper  no task $name for $kitHome to remove"; exit 0 }
    if ($DryRun) { Write-Output "shell-reaper  would remove the task $name"; exit 0 }
    if ($env:KIT_SCHEDULER) { $tasks.Remove($name); Write-ShellReaperScheduler $tasks }
    else { Unregister-ScheduledTask -TaskName $name -Confirm:$false }
    Write-Output "shell-reaper  removed the task $name; its log and any stop file or config stay in $kitHome"
    exit 0
}

$tools  = Join-Path $kitHome 'tools'
$script = Join-Path $tools 'shell-reaper.py'
foreach ($file in @($script, (Join-Path $tools 'msys_tree.py'))) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
        [Console]::Error.WriteLine("refused: no $file. shell-reaper.py and msys_tree.py are copied together into $tools by install.ps1; run it first. Nothing was registered.")
        exit 2
    }
}
if ($Python) {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        [Console]::Error.WriteLine("refused: -Python names $Python, which is not a file. Nothing was registered.")
        exit 2
    }
    $pythonw = (Resolve-Path -LiteralPath $Python).Path
} else {
    $pythonw = Find-ShellReaperPythonw
    if (-not $pythonw) {
        [Console]::Error.WriteLine('refused: no pythonw.exe beside the python.exe on PATH; name one with -Python. Nothing was registered.')
        exit 2
    }
}
$arguments = "-I `"$script`" --home `"$kitHome`""
if ($DryRun) {
    Write-Output "shell-reaper  would register the task $name, every 10 minutes, as this user with no elevation:"
    Write-Output "              $pythonw $arguments"
    exit 0
}

# Built inside the try: a machine without the ScheduledTasks module gets one line and exit 1.
try {
    $user    = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action  = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $tools
    $trigger = New-ScheduledTaskTrigger -Daily -At '00:00'
    $every   = New-ScheduledTaskTrigger -Once -At '00:00' -RepetitionInterval (New-TimeSpan -Minutes 10) `
        -RepetitionDuration (New-TimeSpan -Days 1)
    $every.Repetition.StopAtDurationEnd = $false
    $trigger.Repetition = $every.Repetition
    # A local start with no zone, as install\schedule.ps1 writes it: the cmdlet's UTC start moves across a DST change.
    $trigger.StartBoundary = (Get-Date '00:00').ToString('yyyy-MM-ddTHH:mm:ss')
    $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable:$false -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description "Ends Claude Code Bash tool shells that outlived their call, for the kit in $kitHome. Log: $kitHome\logs\shell-reaper.log. Silence it with $kitHome\shell-reaper.stop; remove it with install\shell-reaper.ps1 -Remove."
    if ($env:KIT_SCHEDULER) {
        $tasks = Read-KitScheduler
        $tasks[$name] = [ordered]@{
            Execute            = $task.Actions[0].Execute
            Arguments          = $task.Actions[0].Arguments
            WorkingDirectory   = $task.Actions[0].WorkingDirectory
            StartBoundary      = $task.Triggers[0].StartBoundary
            DaysInterval       = $task.Triggers[0].DaysInterval
            RepetitionInterval = $task.Triggers[0].Repetition.Interval
            RepetitionDuration = $task.Triggers[0].Repetition.Duration
            StartWhenAvailable = $task.Settings.StartWhenAvailable
            ExecutionTimeLimit = $task.Settings.ExecutionTimeLimit
            MultipleInstances  = [string]$task.Settings.MultipleInstances
            LogonType          = [string]$task.Principal.LogonType
            RunLevel           = [string]$task.Principal.RunLevel
            UserId             = $task.Principal.UserId
        }
        Write-ShellReaperScheduler $tasks
    } else {
        Register-ScheduledTask -TaskName $name -InputObject $task -Force | Out-Null
    }
} catch {
    Write-Output "shell-reaper  FAILED to register the task ${name}: $($_.Exception.Message)"
    exit 1
}
Write-Output "shell-reaper  registered the task ${name}: every 10 minutes, as this user with no elevation, it runs"
Write-Output "              $pythonw $arguments"
Write-Output "              Log: $kitHome\logs\shell-reaper.log. Silence it with $kitHome\shell-reaper.stop;"
Write-Output "              remove it with install\shell-reaper.ps1 -Remove."
exit 0
