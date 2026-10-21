# The daily kit update: install.ps1 -AutoUpdate and -NoAutoUpdate (install\schedule.ps1), the run the task starts
# (install\auto-update.ps1, copied to <kit home>\kit-auto-update.ps1), and the session-start line it leaves
# (claude\hooks\kit-update-notice.py).
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tests\test-install-auto-update.ps1
#
# The Task Scheduler is replaced by KIT_SCHEDULER, a JSON file the registration writes the built task to, set before
# the first install, so no phase registers a real task; it starts with a task of another kit home in it, which no
# phase may touch. The kit is cloned from this checkout (its committed HEAD) into a sandbox origin, and the clone the
# task updates is cloned from that origin, so an upstream commit is a commit in the sandbox. The daily run is
# started exactly as the task would start it, from the recorded command.

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'lib-install-test.ps1')

$realProfile = $env:USERPROFILE
$base        = New-KitSandbox 'kit-auto-update'
$kitHome     = $script:KitHome
$origin      = Join-Path $base 'origin'
$clone       = Join-Path $base 'kit'
$scheduler   = Join-Path $base 'scheduler.json'
$logs        = Join-Path $kitHome 'logs'
$pending     = Join-Path $logs 'auto-update-pending.jsonl'
$installed   = Join-Path $logs 'auto-update-installed.txt'
$settings    = Join-Path $kitHome 'settings.json'
$runner      = Join-Path $kitHome 'kit-auto-update.ps1'
$psFull      = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$sha         = [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($kitHome.ToLowerInvariant()))
$taskName    = 'kit-auto-update-' + [BitConverter]::ToString($sha).Replace('-', '').Substring(0, 8).ToLowerInvariant()
$decoy       = 'kit-auto-update-deadbeef'
$env:KIT_SCHEDULER = $scheduler
Set-Content -LiteralPath $scheduler -Encoding UTF8 -Value ('{ "' + $decoy + '": { "Execute": "another kit home" } }')
$realTaskBefore = [bool](Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)

function Invoke-Git {
    param([string]$Repo, [string[]]$GitArgs)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { return (& git -C $Repo @GitArgs 2>&1 | ForEach-Object { "$_" }) } finally { $ErrorActionPreference = $previous }
}

function Get-Tasks { return (Get-Content -LiteralPath $scheduler -Raw | ConvertFrom-Json) }
function Get-Task { return (Get-Tasks).$taskName }
function Get-Head { param([string]$Repo) return (Invoke-Git $Repo @('rev-parse', 'HEAD') | Select-Object -Last 1) }
function Get-Mode { return ((Get-Content -LiteralPath $settings -Raw | ConvertFrom-Json).permissions.defaultMode) }

function Invoke-DailyRun {
    # The task's own command line, started the way the scheduler starts it. conhost hands back no exit code of the
    # run, so the outcome is read from the log, as a user would read it: the last line of the day's log.
    $task = Get-Task
    Start-Process -FilePath $task.Execute -ArgumentList $task.Arguments -WorkingDirectory $task.WorkingDirectory -Wait
    $day = Join-Path $logs ('auto-update-' + (Get-Date).ToString('yyyy-MM-dd') + '.log')
    $script:LastRun = if (Test-Path -LiteralPath $day) { [string](Get-Content -LiteralPath $day | Select-Object -Last 1) } else { '' }
}

function Get-Pending {
    if (-not (Test-Path -LiteralPath $pending)) { return @() }
    return @(Get-Content -LiteralPath $pending -Encoding UTF8 | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
}

function Get-KitHomeHash {
    # The kit home as the installer leaves it; the logs folder is the run's own record, so it is left out.
    $parts = @()
    foreach ($file in @(Get-ChildItem -LiteralPath $kitHome -Recurse -File -Force | Sort-Object FullName)) {
        if ($file.FullName -like (Join-Path $logs '*')) { continue }
        $parts += ($file.FullName.Substring($kitHome.Length) + ' ' + (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash)
    }
    return ($parts -join "`r`n")
}

function Invoke-Notice {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { return ((& $script:Python (Join-Path $clone 'claude\hooks\kit-update-notice.py') 2>&1) | Out-String).Trim() }
    finally { $ErrorActionPreference = $previous }
}

function Add-Upstream {
    # One commit in the sandbox origin: a line appended to each file named.
    param([string[]]$Files, [string]$Message)
    foreach ($file in $Files) { Add-Content -LiteralPath (Join-Path $origin $file) -Encoding ascii -Value "# $Message" }
    Invoke-Git $origin @('add', '-A') | Out-Null
    Invoke-Git $origin @('commit', '--quiet', '--no-verify', '-m', $Message) | Out-Null
    return (Get-Head $origin)
}

try {
    Assert-True ($scheduler -like (Join-Path $env:TEMP '*')) 'the scheduler stand-in is in the sandbox, set before any install'
    Invoke-Git $base @('clone', '--quiet', $script:RepoRoot, $origin) | Out-Null
    Invoke-Git $origin @('config', 'user.email', 'suite-identity') | Out-Null
    Invoke-Git $origin @('config', 'user.name', 'suite') | Out-Null
    Invoke-Git $base @('clone', '--quiet', $origin, $clone) | Out-Null
    Assert-True (Test-Path -LiteralPath (Join-Path $clone 'install\auto-update.ps1')) 'the clone carries this branch'
    $script:Installer = Join-Path $clone 'install.ps1'

    # ------------------------------------------------------------------------------------------------------------
    Write-Host "`r`nphase 1, -AutoUpdate registers this kit home's daily task, no elevation, hidden, running the kit home's runner"
    $out = Invoke-Install @('-AutoUpdate', '-Permissions', 'ask', '-NoHerdr')
    Assert-Exit 0 'the install with -AutoUpdate succeeds'
    Assert-Match $out "auto-update  registered the daily task $taskName" 'it registers a task named for this kit home'
    $task = Get-Task
    Assert-True ($null -ne $task) 'the task is in the scheduler'
    Assert-True ($null -ne (Get-Tasks).$decoy) 'the task of another kit home is still there'
    Assert-True ($task.Execute -eq (Join-Path $env:SystemRoot 'System32\conhost.exe')) 'the task starts conhost by its full path'
    Assert-True ($task.Arguments.StartsWith('--headless "' + $psFull + '" ')) 'conhost runs Windows PowerShell headless, with no window'
    Assert-Match $task.Arguments ('-File "' + $runner + '"') 'it runs the runner copied into the kit home'
    Assert-True ((Get-FileHash -LiteralPath $runner).Hash -eq (Get-FileHash -LiteralPath (Join-Path $clone 'install\auto-update.ps1')).Hash) `
        'the kit home copy is the clone''s runner'
    Assert-Match $task.Arguments ('-Dir "' + $clone + '"') 'it names the clone'
    Assert-Match $task.Arguments ('-KitHome "' + $kitHome + '"') 'it names this kit home'
    Assert-Match $task.Arguments '-NonInteractive' 'it can never stop on a question'
    Assert-True ($task.Arguments -notmatch '-Permissions') 'no permission mode is frozen in the task'
    Assert-True ($task.Arguments -notmatch 'Herdr') 'and no Herdr answer'
    Assert-True ($task.Arguments -notmatch '-NoHookMerge') 'no -NoHookMerge when the run had none'
    Assert-True ($task.WorkingDirectory -eq $kitHome) 'it starts in the kit home, not in the clone'
    Assert-True ($task.StartWhenAvailable -eq $true) 'a missed day runs at the next chance (StartWhenAvailable)'
    Assert-True ($task.DaysInterval -eq 1) 'it runs every day'
    Assert-Regex $task.StartBoundary '^\d{4}-\d{2}-\d{2}T05:00:00$' 'at 05:00 local time, with no zone'
    Assert-True ($task.LogonType -eq 'Interactive' -and $task.RunLevel -eq 'Limited') 'as the signed-in user, with no elevation'
    Assert-True ((Get-Mode) -eq 'default') 'the install itself is in ask mode'

    Write-Host "`r`nphase 2, -NoHookMerge is the one option the task keeps"
    $out = Invoke-Install @('-AutoUpdate', '-Permissions', 'ask', '-NoHerdr', '-NoHookMerge')
    Assert-Exit 0 'enabling it with -NoHookMerge succeeds'
    Assert-Regex (Get-Task).Arguments '-NoHookMerge$' '-NoHookMerge is kept'
    $out = Invoke-Install @('-AutoUpdate', '-Permissions', 'ask', '-NoHerdr')
    Assert-True ((Get-Task).Arguments -notmatch '-NoHookMerge') 'and dropped when the next enabling run has none'

    Write-Host "`r`nphase 3, a dry run shows the task and registers or removes nothing"
    $before = (Get-FileHash -LiteralPath $scheduler).Hash
    $out = Invoke-Install @('-AutoUpdate', '-Herdr', '-DryRun')
    Assert-Exit 0 'the dry run succeeds'
    Assert-Match $out "auto-update  would register the daily task $taskName" 'it says what it would register'
    $shown = [regex]::Match($out, 'conhost\.exe --headless[^\r\n]*').Value
    Assert-True ($shown -and $shown -notmatch 'Herdr' -and $shown -notmatch '-Permissions') 'with neither a Herdr answer nor a mode'
    $out = Invoke-Install @('-NoAutoUpdate', '-DryRun')
    Assert-Match $out "auto-update  would remove the daily task $taskName" 'a dry -NoAutoUpdate says it would remove it'
    Assert-True ($out -notmatch 'plan \(dry run') 'and plans no install'
    Assert-True ((Get-FileHash -LiteralPath $scheduler).Hash -eq $before) 'the scheduler is unchanged'

    Write-Host "`r`nphase 4, the switches are refused where they mean nothing"
    $out = Invoke-Install @('-AutoUpdate', '-NoAutoUpdate')
    Assert-True ($script:LastExit -ne 0) "both switches at once are refused (exit $($script:LastExit))"
    $out = Invoke-Install @('-Project', (Join-Path $base 'project'), '-AutoUpdate')
    Assert-Exit 1 'a project-scope run refuses -AutoUpdate'
    Assert-Match $out 'refused: -AutoUpdate and -NoAutoUpdate act on the user-scope install' 'with one line'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $base 'project'))) 'and writes nothing'

    Write-Host "`r`nphase 5, the first daily run installs once, in the mode settings.json has, and leaves no notice"
    $head = Get-Head $clone
    Invoke-DailyRun
    Assert-Regex $script:LastRun ' done: installed ' 'the first run installs the commit the clone has'
    $log = Join-Path $logs ('auto-update-' + (Get-Date).ToString('yyyy-MM-dd') + '.log')
    $logText = Get-Content -LiteralPath $log -Raw
    Assert-Match $logText 'install.ps1 -Permissions ask -NoHerdr (the mode read from settings.json' 'the mode is read from settings.json, Herdr left alone'
    Assert-Match $logText 'install: Done.' 'install.ps1 ran and its output is in the log'
    Assert-True (@(Get-Pending).Count -eq 0) 'no notice is left for a run that moved nothing'
    Assert-True ((Get-Content -LiteralPath $installed -TotalCount 1) -eq $head) 'the installed commit is recorded'
    Assert-True ((Get-Mode) -eq 'default') 'the daily run kept ask mode'

    Write-Host "`r`nphase 6, a run on an unmoved clone installs nothing"
    $homeBefore = Get-KitHomeHash
    Invoke-DailyRun
    Assert-Regex $script:LastRun ' done: the kit is already installed at ' 'it says there is nothing to install'
    Assert-True (([regex]::Matches((Get-Content -LiteralPath $log -Raw), 'install: Done\.')).Count -eq 1) 'install.ps1 did not run again'
    Assert-True ((Get-KitHomeHash) -eq $homeBefore) 'nothing in the kit home changed'

    Write-Host "`r`nphase 7, an upstream commit with a new hook: pulled, installed, ask kept, the runner refreshed, one notice"
    $helper = Join-Path $base 'add-hook.py'
    Set-Content -LiteralPath $helper -Encoding ascii -Value @'
import json, sys
path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
data["hooks"]["SessionStart"].append({"matcher": "startup", "hooks": [{"type": "command",
    "command": "python \"%USERPROFILE%/.claude/hooks/zz-kit-test-hook.py\""}]})
open(path, "w", encoding="utf-8", newline="\n").write(json.dumps(data, indent=2) + "\n")
'@
    & $script:Python $helper (Join-Path $origin 'claude\settings.json')
    Set-Content -LiteralPath (Join-Path $origin 'claude\hooks\zz-kit-test-hook.py') -Encoding ascii -Value 'pass'
    $upstream = Add-Upstream @('claude\skills\audit\SKILL.md', 'install\auto-update.ps1') 'upstream change with a hook'
    Invoke-DailyRun
    Assert-Regex $script:LastRun ' done: updated to ' 'the daily run succeeds'
    Assert-True ((Get-Head $clone) -eq $upstream) 'the clone was pulled'
    Assert-True (Test-Path -LiteralPath (Join-Path $kitHome 'hooks\zz-kit-test-hook.py')) 'the new hook reached the kit home'
    Assert-Match (Get-Content -LiteralPath $settings -Raw) 'zz-kit-test-hook.py' 'and it is wired in settings.json'
    Assert-True ((Get-Mode) -eq 'default') 'an automatic run never flips ask to bypass'
    Assert-Match (Get-Content -LiteralPath $runner -Raw) '# upstream change with a hook' 'the run refreshed the kit home''s runner, itself'
    $runs = @(Get-Pending)
    Assert-True ($runs.Count -eq 1 -and $runs[0].result -eq 'updated') 'one notice, an update'
    Assert-True ($runs[0].to -eq $upstream.Substring(0, 7) -and $runs[0].from -eq $head.Substring(0, 7)) 'it names the new commit and the installed one'
    Assert-True ($runs[0].files -eq 4) "it counts the files changed ($($runs[0].files))"
    Assert-True (@($runs[0].hooks) -contains 'zz-kit-test-hook.py' -and @($runs[0].hooks).Count -eq 1) 'it names the hook added'
    $line = Invoke-Notice
    Assert-Match $line ('now at ' + $upstream.Substring(0, 7) + ' (was ' + $head.Substring(0, 7) + '), 4 files changed') 'the session start prints the update'
    Assert-Match $line 'new hooks: zz-kit-test-hook.py' 'with the hook added'
    Assert-Match $line ('Log: ' + $log.Replace('\', '\\')) 'and the log path'
    Assert-True ((Invoke-Notice) -eq '') 'the next session start prints nothing'

    Write-Host "`r`nphase 8, a mode chosen by hand after enabling is the mode the next run keeps"
    $out = Invoke-Install @('-Permissions', 'bypass', '-NoHerdr')
    Assert-True ((Get-Mode) -eq 'bypassPermissions') 'the user chose bypass by hand'
    $upstream = Add-Upstream @('README.md') 'upstream change two'
    Invoke-DailyRun
    Assert-Regex $script:LastRun ' done: updated to ' 'the daily run succeeds'
    Assert-Match (Get-Content -LiteralPath $log -Raw) 'install.ps1 -Permissions bypass -NoHerdr' 'it ran in the mode settings.json had'
    Assert-True ((Get-Mode) -eq 'bypassPermissions') 'and kept it'
    $out = Invoke-Install @('-Permissions', 'ask', '-NoHerdr')
    Assert-True ((Get-Mode) -eq 'default') 'the user went back to ask by hand'
    $upstream = Add-Upstream @('README.md') 'upstream change three'
    Invoke-DailyRun
    Assert-True ((Get-Mode) -eq 'default') 'and the next run kept ask'
    Remove-Item -LiteralPath $pending

    Write-Host "`r`nphase 9, an install that fails after a pull is reported, and the next run tries it again"
    $installedBefore = Get-Content -LiteralPath $installed -TotalCount 1
    $setup = Join-Path $origin 'install.ps1'
    [IO.File]::WriteAllText($setup, ([IO.File]::ReadAllText($setup)).Replace('$root = $PSScriptRoot', 'exit 3; $root = $PSScriptRoot'))
    $broken = Add-Upstream @() 'upstream change that breaks the installer'
    Invoke-DailyRun
    Assert-Regex $script:LastRun (' failed: install\.ps1 exited 3 after the pull to ' + $broken.Substring(0, 7)) 'the failed install is the last line'
    Assert-True ((Get-Content -LiteralPath $installed -TotalCount 1) -eq $installedBefore) 'the installed commit did not move'
    Assert-Match (Invoke-Notice) 'failed: install.ps1 exited 3' 'the session start prints the failure'
    Invoke-Git $origin @('revert', '--no-edit', 'HEAD') | Out-Null
    $fixed = Add-Upstream @('README.md') 'upstream change after the fix'
    Invoke-DailyRun
    Assert-Regex $script:LastRun (' done: updated to ' + $fixed.Substring(0, 7)) 'the next run installs'
    Assert-True ((@(Get-Pending)[0]).from -eq $installedBefore.Substring(0, 7)) 'and reports from the last commit installed'
    Assert-True ((Get-Content -LiteralPath $installed -TotalCount 1) -eq $fixed) 'the installed commit moved'
    Remove-Item -LiteralPath $pending

    Write-Host "`r`nphase 10, a clone with changes of its own: logged, nothing pulled or installed"
    $upstream = Add-Upstream @('README.md') 'upstream change four'
    Add-Content -LiteralPath (Join-Path $clone 'README.md') -Encoding ascii -Value 'A local edit.'
    $cloneHead = Get-Head $clone
    $homeBefore = Get-KitHomeHash
    Invoke-DailyRun
    Assert-Regex $script:LastRun ' failed: the clone at .* has changes of its own' 'the run ends on its failure line'
    Assert-True ((Get-Head $clone) -eq $cloneHead) 'nothing was pulled'
    Assert-True ((Get-KitHomeHash) -eq $homeBefore) 'nothing in the kit home changed'
    $runs = @(Get-Pending)
    Assert-True ($runs.Count -eq 1 -and $runs[0].result -eq 'failed') 'one notice, a failure'
    Invoke-Git $clone @('checkout', '--', 'README.md') | Out-Null
    Remove-Item -LiteralPath $pending

    Write-Host "`r`nphase 11, a pull that fails (no origin to reach): logged, nothing installed"
    Invoke-Git $clone @('remote', 'set-url', 'origin', (Join-Path $base 'no-such-origin')) | Out-Null
    Invoke-DailyRun
    Assert-Regex $script:LastRun ' failed: git pull --ff-only in ' 'the run ends on its failure line'
    Assert-True ((Get-KitHomeHash) -eq $homeBefore) 'nothing in the kit home changed'
    Assert-Match (Invoke-Notice) 'failed: git pull --ff-only in' 'the session start prints the failure'
    Invoke-Git $clone @('remote', 'set-url', 'origin', $origin) | Out-Null

    Write-Host "`r`nphase 12, a clone that was moved away: the kit home's runner still logs and reports it"
    Rename-Item -LiteralPath $clone -NewName 'kit-moved'
    try {
        Invoke-DailyRun
        Assert-Regex $script:LastRun ' failed: the clone at .* is gone or is not a git clone' 'the run ends on its failure line'
        Assert-True ((Get-KitHomeHash) -eq $homeBefore) 'nothing in the kit home changed'
        $runs = @(Get-Pending)
        Assert-True ($runs.Count -eq 1 -and $runs[0].line -match 'is gone') 'the notice says the clone is gone'
    } finally { Rename-Item -LiteralPath (Join-Path $base 'kit-moved') -NewName 'kit' }
    Remove-Item -LiteralPath $pending

    Write-Host "`r`nphase 13, the last 30 logs are kept"
    foreach ($day in 1..35) {
        Set-Content -LiteralPath (Join-Path $logs ('auto-update-' + (Get-Date '2020-01-01').AddDays($day - 1).ToString('yyyy-MM-dd') + '.log')) -Value 'old'
    }
    Invoke-DailyRun
    Assert-Regex $script:LastRun (' done: updated to ' + $upstream.Substring(0, 7)) 'the run after the failures pulls the waiting change'
    $kept = @(Get-ChildItem -LiteralPath $logs -File | Where-Object { $_.Name -match '^auto-update-\d{4}-\d{2}-\d{2}\.log$' } | Sort-Object Name)
    Assert-True ($kept.Count -eq 30) "30 logs are kept ($($kept.Count))"
    Assert-True ($kept[-1].Name -eq (Split-Path -Leaf $log)) 'today''s log is one of them'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $logs 'auto-update-2020-01-01.log'))) 'the oldest went'
    Assert-True (Test-Path -LiteralPath (Join-Path $logs 'auto-update-2020-02-04.log')) 'the newest old ones stay'

    Write-Host "`r`nphase 14, -NoAutoUpdate removes this kit home's task and changes nothing else"
    Assert-True ((Get-Mode) -eq 'default') 'the kit home is in ask mode'
    $homeBefore = Get-KitHomeHash
    $out = Invoke-Install @('-NoAutoUpdate')
    Assert-Exit 0 'the run with -NoAutoUpdate succeeds'
    Assert-Match $out "auto-update  removed the daily task $taskName" 'it says it removed it'
    Assert-True ($out -notmatch 'PERMISSIONS|Kit home:') 'it ran no install'
    Assert-True ((Get-Mode) -eq 'default') 'ask stays ask, though the run had the default bypass'
    Assert-True ((Get-KitHomeHash) -eq $homeBefore) 'nothing in the kit home changed'
    Assert-True ($null -eq (Get-Task)) 'its task is gone'
    Assert-True ($null -ne (Get-Tasks).$decoy) 'the task of another kit home stays'
    $out = Invoke-Install @('-NoAutoUpdate')
    Assert-Match $out "auto-update  no daily task $taskName" 'a second removal says there is none'
    Assert-True ([bool](Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) -eq $realTaskBefore) 'the real Task Scheduler was never touched'
} finally {
    $env:KIT_SCHEDULER = $null
    Close-KitSandbox $realProfile
}

Write-TestResult
