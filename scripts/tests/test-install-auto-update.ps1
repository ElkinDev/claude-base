# The daily kit update: install.ps1 -AutoUpdate and -NoAutoUpdate (install\schedule.ps1), the run the task starts
# (install\auto-update.ps1), and the session-start line it leaves (claude\hooks\kit-update-notice.py).
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tests\test-install-auto-update.ps1
#
# The Task Scheduler is replaced by KIT_SCHEDULER, a JSON file the registration writes the built task to, set before
# the first install, so no phase registers a real task. The kit is cloned from this checkout (its committed HEAD)
# into a sandbox origin, and the clone the task updates is cloned from that origin, so an upstream commit is a
# commit in the sandbox. The daily run is started exactly as the task would start it, from the recorded command.

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
$settings    = Join-Path $kitHome 'settings.json'
$psFull      = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$env:KIT_SCHEDULER = $scheduler
$realTaskBefore = [bool](Get-ScheduledTask -TaskName 'kit-auto-update' -ErrorAction SilentlyContinue)

function Invoke-Git {
    param([string]$Repo, [string[]]$GitArgs)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { return (& git -C $Repo @GitArgs 2>&1 | ForEach-Object { "$_" }) } finally { $ErrorActionPreference = $previous }
}

function Get-Task { return (Get-Content -LiteralPath $scheduler -Raw | ConvertFrom-Json) }

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

try {
    Assert-True ($scheduler -like (Join-Path $env:TEMP '*')) 'the scheduler stand-in is in the sandbox, set before any install'
    Invoke-Git $base @('clone', '--quiet', $script:RepoRoot, $origin) | Out-Null
    Invoke-Git $origin @('config', 'user.email', 'suite-identity') | Out-Null
    Invoke-Git $origin @('config', 'user.name', 'kit test') | Out-Null
    Invoke-Git $base @('clone', '--quiet', $origin, $clone) | Out-Null
    Assert-True (Test-Path -LiteralPath (Join-Path $clone 'install\auto-update.ps1')) 'the clone carries this branch'
    $script:Installer = Join-Path $clone 'install.ps1'

    # ------------------------------------------------------------------------------------------------------------
    Write-Host "`r`nphase 1, -AutoUpdate registers a daily task, no elevation, that runs hidden with this run's options"
    $out = Invoke-Install @('-AutoUpdate', '-Permissions', 'ask', '-NoHerdr')
    Assert-Exit 0 'the install with -AutoUpdate succeeds'
    Assert-Match $out 'auto-update  registered the daily task kit-auto-update' 'it says it registered the task'
    $task = Get-Task
    Assert-True ($task.Execute -eq (Join-Path $env:SystemRoot 'System32\conhost.exe')) 'the task starts conhost by its full path'
    Assert-True ($task.Arguments.StartsWith('--headless "' + $psFull + '" ')) 'conhost runs Windows PowerShell headless, with no window'
    Assert-Match $task.Arguments ('-File "' + $clone + '\install\auto-update.ps1"') 'it runs the clone''s daily runner'
    Assert-Match $task.Arguments ('-Dir "' + $clone + '"') 'it names the clone'
    Assert-Match $task.Arguments ('-KitHome "' + $kitHome + '"') 'it names this kit home'
    Assert-Match $task.Arguments '-NonInteractive' 'it can never stop on a question'
    Assert-Regex $task.Arguments '-Permissions ask( |$)' 'an ask-mode install keeps ask in the task'
    Assert-True ($task.Arguments -notmatch '-Permissions bypass') 'and never names bypass'
    Assert-Regex $task.Arguments '-NoHerdr( |$)' 'the Herdr answer of the run is kept'
    Assert-True ($task.Arguments -notmatch '-NoHookMerge') 'no -NoHookMerge when the run had none'
    Assert-True ($task.StartWhenAvailable -eq $true) 'a missed day runs at the next chance (StartWhenAvailable)'
    Assert-True ($task.DaysInterval -eq 1) 'it runs every day'
    Assert-Regex $task.StartBoundary '^\d{4}-\d{2}-\d{2}T05:00:00$' 'at 05:00 local time, with no zone'
    Assert-True ($task.LogonType -eq 'Interactive' -and $task.RunLevel -eq 'Limited') 'as the signed-in user, with no elevation'
    Assert-Match (Get-Content -LiteralPath $settings -Raw) '"defaultMode": "default"' 'the install itself is in ask mode'

    Write-Host "`r`nphase 2, the task takes the options of the run that enabled it, each time"
    $out = Invoke-Install @('-AutoUpdate', '-NoHerdr', '-NoHookMerge')
    Assert-Exit 0 'a bypass install with -NoHookMerge succeeds'
    $task = Get-Task
    Assert-Regex $task.Arguments '-Permissions bypass( |$)' 'a bypass install registers bypass'
    Assert-Regex $task.Arguments '-NoHookMerge( |$)' '-NoHookMerge is kept'
    $out = Invoke-Install @('-AutoUpdate', '-Permissions', 'ask', '-NoHerdr')
    Assert-Exit 0 'enabling it again in ask mode succeeds'
    Assert-Regex (Get-Task).Arguments '-Permissions ask( |$)' 'the task is back to ask'

    Write-Host "`r`nphase 3, a dry run shows the task and registers nothing"
    $before = Get-FileHash -LiteralPath $scheduler
    $out = Invoke-Install @('-AutoUpdate', '-Herdr', '-DryRun')
    Assert-Exit 0 'the dry run succeeds'
    Assert-Match $out 'auto-update  would register the daily task kit-auto-update' 'it says what it would register'
    Assert-Regex $out 'conhost\.exe --headless .* -Herdr' 'with the Herdr answer of that run'
    $out = Invoke-Install @('-NoAutoUpdate', '-DryRun')
    Assert-Match $out 'auto-update  would remove the daily task kit-auto-update' 'a dry -NoAutoUpdate says it would remove it'
    Assert-True ((Get-FileHash -LiteralPath $scheduler).Hash -eq $before.Hash) 'the registered task is unchanged'

    Write-Host "`r`nphase 4, the switches are refused where they mean nothing"
    $out = Invoke-Install @('-AutoUpdate', '-NoAutoUpdate')
    Assert-True ($script:LastExit -ne 0) "both switches at once are refused (exit $($script:LastExit))"
    $out = Invoke-Install @('-Project', (Join-Path $base 'project'), '-AutoUpdate')
    Assert-Exit 1 'a project-scope run refuses -AutoUpdate'
    Assert-Match $out 'refused: -AutoUpdate and -NoAutoUpdate act on the user-scope install' 'with one line'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $base 'project'))) 'and writes nothing'

    Write-Host "`r`nphase 5, the daily run with nothing new upstream installs and leaves no notice"
    Invoke-DailyRun
    Assert-Regex $script:LastRun '^\d\d:\d\d:\d\d done: ' 'the daily run succeeds'
    $log = Join-Path $logs ('auto-update-' + (Get-Date).ToString('yyyy-MM-dd') + '.log')
    $logText = if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Raw } else { '' }
    Assert-Match $logText 'install.ps1 -Permissions ask -NoHerdr' 'the log names the options it ran with'
    Assert-Match $logText 'done: the kit was already up to date' 'the log says it was up to date'
    Assert-Match $logText 'install: Done.' 'install.ps1 ran and its output is in the log'
    Assert-True (@(Get-Pending).Count -eq 0) 'no notice is left for an update that changed nothing'
    $head = (Invoke-Git $clone @('rev-parse', 'HEAD') | Select-Object -Last 1)
    Assert-True ((Get-Content -LiteralPath (Join-Path $logs 'auto-update-installed.txt') -TotalCount 1) -eq $head) 'the installed commit is recorded'
    Assert-Match (Get-Content -LiteralPath $settings -Raw) '"defaultMode": "default"' 'the daily run kept ask mode'

    Write-Host "`r`nphase 6, an upstream commit with a new hook: pulled, installed, ask kept, and one notice"
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
    Add-Content -LiteralPath (Join-Path $origin 'claude\skills\audit\SKILL.md') -Encoding ascii -Value 'A line from upstream.'
    Invoke-Git $origin @('add', '-A') | Out-Null
    Invoke-Git $origin @('commit', '--quiet', '--no-verify', '-m', 'upstream change for the test') | Out-Null
    $upstream = (Invoke-Git $origin @('rev-parse', 'HEAD') | Select-Object -Last 1)
    Invoke-DailyRun
    Assert-Regex $script:LastRun ' done: updated to ' 'the daily run succeeds'
    Assert-True ((Invoke-Git $clone @('rev-parse', 'HEAD') | Select-Object -Last 1) -eq $upstream) 'the clone was pulled'
    Assert-True (Test-Path -LiteralPath (Join-Path $kitHome 'hooks\zz-kit-test-hook.py')) 'the new hook reached the kit home'
    $kitSettings = Get-Content -LiteralPath $settings -Raw
    Assert-Match $kitSettings 'zz-kit-test-hook.py' 'and it is wired in settings.json'
    Assert-Match $kitSettings '"defaultMode": "default"' 'an automatic run never flips ask to bypass'
    $runs = @(Get-Pending)
    Assert-True ($runs.Count -eq 1 -and $runs[0].result -eq 'updated') 'one notice, an update'
    Assert-True ($runs[0].to -eq $upstream.Substring(0, 7) -and $runs[0].from -eq $head.Substring(0, 7)) 'it names the new commit and the old one'
    Assert-True ($runs[0].files -eq 3) "it counts the files changed ($($runs[0].files))"
    Assert-True (@($runs[0].hooks) -contains 'zz-kit-test-hook.py' -and @($runs[0].hooks).Count -eq 1) 'it names the hook added'
    $line = Invoke-Notice
    Assert-Match $line ('now at ' + $upstream.Substring(0, 7) + ' (was ' + $head.Substring(0, 7) + '), 3 files changed') 'the session start prints the update'
    Assert-Match $line 'new hooks: zz-kit-test-hook.py' 'with the hook added'
    Assert-Match $line ('Log: ' + $log.Replace('\', '\\')) 'and the log path'
    Assert-True ((Invoke-Notice) -eq '') 'the next session start prints nothing'

    Write-Host "`r`nphase 7, a clone with changes of its own: logged, nothing pulled or installed"
    Add-Content -LiteralPath (Join-Path $origin 'README.md') -Encoding ascii -Value 'Second upstream line.'
    Invoke-Git $origin @('commit', '--quiet', '--no-verify', '-am', 'second upstream change') | Out-Null
    Add-Content -LiteralPath (Join-Path $clone 'README.md') -Encoding ascii -Value 'A local edit.'
    $homeBefore = Get-KitHomeHash
    Invoke-DailyRun
    Assert-Regex $script:LastRun ' failed: the clone at ' 'the run ends on its failure line'
    Assert-True ((Invoke-Git $clone @('rev-parse', 'HEAD') | Select-Object -Last 1) -eq $upstream) 'nothing was pulled'
    Assert-True ((Get-KitHomeHash) -eq $homeBefore) 'nothing in the kit home changed'
    $runs = @(Get-Pending)
    Assert-True ($runs.Count -eq 1 -and $runs[0].result -eq 'failed') 'one notice, a failure'
    Assert-Match $runs[0].line "the clone at $clone has changes of its own" 'it names the reason'
    Assert-Match (Get-Content -LiteralPath $log -Raw) 'failed: the clone at' 'the log has the failure line'
    Invoke-Git $clone @('checkout', '--', 'README.md') | Out-Null
    Remove-Item -LiteralPath $pending

    Write-Host "`r`nphase 8, a pull that fails (no origin to reach): logged, nothing installed"
    Invoke-Git $clone @('remote', 'set-url', 'origin', (Join-Path $base 'no-such-origin')) | Out-Null
    Invoke-DailyRun
    Assert-Regex $script:LastRun ' failed: git pull --ff-only in ' 'the run ends on its failure line'
    Assert-True ((Get-KitHomeHash) -eq $homeBefore) 'nothing in the kit home changed'
    $runs = @(Get-Pending)
    Assert-True ($runs.Count -eq 1 -and $runs[0].line -match '^git pull --ff-only in .* failed: ') 'the notice names the failed pull'
    Assert-Match (Invoke-Notice) 'failed: git pull --ff-only in' 'the session start prints the failure'
    Invoke-Git $clone @('remote', 'set-url', 'origin', $origin) | Out-Null

    Write-Host "`r`nphase 9, the last 30 logs are kept"
    foreach ($day in 1..35) {
        Set-Content -LiteralPath (Join-Path $logs ('auto-update-' + (Get-Date '2020-01-01').AddDays($day - 1).ToString('yyyy-MM-dd') + '.log')) -Value 'old'
    }
    Invoke-DailyRun
    Assert-Regex $script:LastRun ' done: updated to ' 'the run after the failures succeeds and pulls the second change'
    $kept = @(Get-ChildItem -LiteralPath $logs -File | Where-Object { $_.Name -match '^auto-update-\d{4}-\d{2}-\d{2}\.log$' } | Sort-Object Name)
    Assert-True ($kept.Count -eq 30) "30 logs are kept ($($kept.Count))"
    Assert-True ($kept[-1].Name -eq (Split-Path -Leaf $log)) 'today''s log is one of them'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $logs 'auto-update-2020-01-01.log'))) 'the oldest went'
    Assert-True (Test-Path -LiteralPath (Join-Path $logs 'auto-update-2020-02-04.log')) 'the newest old ones stay'

    Write-Host "`r`nphase 10, -NoAutoUpdate removes the task"
    $out = Invoke-Install @('-NoAutoUpdate', '-NoHerdr')
    Assert-Exit 0 'the run with -NoAutoUpdate succeeds'
    Assert-Match $out 'auto-update  removed the daily task kit-auto-update' 'it says it removed it'
    Assert-True (-not (Test-Path -LiteralPath $scheduler)) 'the task is gone'
    $out = Invoke-Install @('-NoAutoUpdate', '-NoHerdr')
    Assert-Match $out 'auto-update  no daily task kit-auto-update to remove' 'a second removal says there is none'
    Assert-True ([bool](Get-ScheduledTask -TaskName 'kit-auto-update' -ErrorAction SilentlyContinue) -eq $realTaskBefore) 'the real Task Scheduler was never touched'
} finally {
    $env:KIT_SCHEDULER = $null
    Close-KitSandbox $realProfile
}

Write-TestResult
