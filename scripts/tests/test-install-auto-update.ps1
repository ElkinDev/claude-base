# The daily kit update: install.ps1 -AutoUpdate, -NoAutoUpdate and -Unattended (install\schedule.ps1), the run the
# task starts (install\auto-update.ps1, copied to <kit home>\kit-auto-update.ps1), the install record every install
# leaves (<kit home>\.kit-install.json), get.ps1 -NoAutoUpdate, and the session-start line the run leaves
# (claude\hooks\kit-update-notice.py, whose own suite is claude\hooks\tests\test-kit-update-notice.py).
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tests\test-install-auto-update.ps1
#
# The Task Scheduler is replaced by KIT_SCHEDULER, a JSON file the registration writes the built task to, set before
# the first install, so no phase registers a real task; it starts with a task of another kit home in it, which no
# phase may touch. The user PATH is the throwaway key lib-install-test.ps1 names. The kit is cloned from this
# checkout (its committed HEAD) into a sandbox origin, and the clone the task updates is cloned from that origin, so
# an upstream commit is a commit in the sandbox. The daily run is started exactly as the task would start it, from
# the recorded command.

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
$record      = Join-Path $kitHome '.kit-install.json'
$settings    = Join-Path $kitHome 'settings.json'
$runner      = Join-Path $kitHome 'kit-auto-update.ps1'
$bin         = Join-Path $kitHome 'bin'
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
function Get-Record { return (Get-Content -LiteralPath $record -Raw | ConvertFrom-Json) }
function Get-DayLog { return (Join-Path $logs ('auto-update-' + (Get-Date).ToString('yyyy-MM-dd') + '.log')) }
function Get-LastLine { param([string]$Pattern) return [string](Get-Content -LiteralPath (Get-DayLog) | Where-Object { $_ -match $Pattern } | Select-Object -Last 1) }
function Get-LastInstallLine { return [string](Get-Content -LiteralPath (Get-DayLog) | Where-Object { $_ -match ' install[.]ps1 -Permissions ' } | Select-Object -Last 1) }

function Get-UserPath {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($env:KIT_ENV_KEY)
    if (-not $key) { return '' }
    try { return [string]$key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames) }
    finally { $key.Close() }
}

function Set-UserPath {
    param([string]$Value)
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($env:KIT_ENV_KEY)
    try { $key.SetValue('Path', $Value, [Microsoft.Win32.RegistryValueKind]::ExpandString) } finally { $key.Close() }
}

function Invoke-DailyRun {
    # The task's own command line, started the way the scheduler starts it. conhost hands back no exit code of the
    # run, so the outcome is read from the log, as a user would read it: the last line of the day's log.
    $task = Get-Task
    Start-Process -FilePath $task.Execute -ArgumentList $task.Arguments -WorkingDirectory $task.WorkingDirectory -Wait
    $day = Get-DayLog
    $script:LastRun = if (Test-Path -LiteralPath $day) { [string](Get-Content -LiteralPath $day | Select-Object -Last 1) } else { '' }
}

function Get-Pending {
    if (-not (Test-Path -LiteralPath $pending)) { return @() }
    return @(Get-Content -LiteralPath $pending -Encoding UTF8 | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
}

function Clear-Pending { if (Test-Path -LiteralPath $pending) { Remove-Item -LiteralPath $pending } }

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

function Invoke-GetScript {
    param([string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $text = ((& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $clone 'install\get.ps1') @Arguments 2>&1) | Out-String)
        $script:LastExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
    return $text
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
    Write-Host "`r`nphase 1, -AutoUpdate registers this kit home's daily task, hidden, with no option in it"
    $out = Invoke-Install @('-AutoUpdate', '-Permissions', 'ask', '-NoHerdr')
    Assert-Exit 0 'the install with -AutoUpdate succeeds'
    Assert-Match $out "auto-update  registered the daily task $taskName" 'it registers a task named for this kit home'
    $task = Get-Task
    Assert-True ($null -ne $task) 'the task is in the scheduler'
    Assert-True ($null -ne (Get-Tasks).$decoy) 'the task of another kit home is still there'
    Assert-True ($task.Execute -eq (Join-Path $env:SystemRoot 'System32\conhost.exe')) 'the task starts conhost by its full path'
    Assert-True ($task.Arguments.StartsWith('--headless "' + $psFull + '" ')) 'conhost runs Windows PowerShell headless, with no window'
    Assert-True ($task.Arguments.EndsWith('-File "' + $runner + '" -Dir "' + $clone + '" -KitHome "' + $kitHome + '"')) `
        'it runs the kit home''s runner on the clone and the kit home, with no option'
    Assert-True ((Get-FileHash -LiteralPath $runner).Hash -eq (Get-FileHash -LiteralPath (Join-Path $clone 'install\auto-update.ps1')).Hash) `
        'the kit home copy is the clone''s runner'
    Assert-Match $task.Arguments '-NonInteractive' 'it can never stop on a question'
    Assert-True ($task.WorkingDirectory -eq $kitHome) 'it starts in the kit home, not in the clone'
    Assert-True ($task.StartWhenAvailable -eq $true) 'a missed day runs at the next chance (StartWhenAvailable)'
    Assert-True ($task.DaysInterval -eq 1) 'it runs every day'
    Assert-Regex $task.StartBoundary '^\d{4}-\d{2}-\d{2}T05:00:00$' 'at 05:00 local time, with no zone'
    Assert-True ($task.LogonType -eq 'Interactive' -and $task.RunLevel -eq 'Limited') 'as the signed-in user, with no elevation'
    Assert-True ((Get-Mode) -eq 'default') 'the install itself is in ask mode'
    $rec = Get-Record
    Assert-True ($rec.commit -eq (Get-Head $clone) -and $rec.permissions -eq 'ask' -and $rec.noHookMerge -eq $false) `
        'the install left its record: the commit, the mode, no -NoHookMerge'

    Write-Host "`r`nphase 2, one task per kit home, however its path is spelled"
    . (Join-Path $clone 'install\herdr.ps1')
    . (Join-Path $clone 'install\schedule.ps1')
    foreach ($spelling in @($kitHome, $kitHome.Replace('\', '/'), ($kitHome + '\'), $kitHome.ToUpperInvariant(), ($kitHome + '\.\'))) {
        Assert-True ((Get-KitAutoUpdateName $spelling) -eq $taskName) "the name of '$spelling' is the same"
    }
    Assert-True ((Get-KitAutoUpdateName (Join-Path $base 'other\.claude')) -ne $taskName) 'another kit home has another name'
    $savedHome = $env:KIT_HOME
    $env:KIT_HOME = $kitHome.Replace('\', '/') + '/'
    try {
        $out = Invoke-Install @('-AutoUpdate', '-DryRun')
        Assert-Match $out "would register the daily task $taskName" 'install.ps1 names it the same from a forward-slash KIT_HOME'
        $out = Invoke-GetScript @('-Dir', (Join-Path $base 'no-clone'), '-NoAutoUpdate', '-DryRun')
        Assert-Match $out "would remove the daily task $taskName" 'and so does get.ps1, which carries its own copy of the hash'
    } finally { $env:KIT_HOME = $savedHome }

    Write-Host "`r`nphase 3, a dry run registers, removes and records nothing"
    $before = (Get-FileHash -LiteralPath $scheduler).Hash
    $recordBefore = (Get-FileHash -LiteralPath $record).Hash
    $out = Invoke-Install @('-AutoUpdate', '-Herdr', '-DryRun')
    Assert-Exit 0 'the dry run succeeds'
    $shown = [regex]::Match($out, 'conhost\.exe --headless[^\r\n]*').Value
    Assert-True ($shown -and $shown -notmatch 'Herdr' -and $shown -notmatch '-Permissions|-NoHookMerge') 'the task it shows carries no option'
    $out = Invoke-Install @('-NoAutoUpdate', '-DryRun')
    Assert-Match $out "auto-update  would remove the daily task $taskName" 'a dry -NoAutoUpdate says it would remove it'
    Assert-True ($out -notmatch 'plan \(dry run') 'and plans no install'
    Assert-True ((Get-FileHash -LiteralPath $scheduler).Hash -eq $before) 'the scheduler is unchanged'
    Assert-True ((Get-FileHash -LiteralPath $record).Hash -eq $recordBefore) 'the record is unchanged'

    Write-Host "`r`nphase 4, the switches are refused where they mean nothing"
    $out = Invoke-Install @('-AutoUpdate', '-NoAutoUpdate')
    Assert-True ($script:LastExit -ne 0) "both switches at once are refused (exit $($script:LastExit))"
    $out = Invoke-Install @('-Project', (Join-Path $base 'project'), '-AutoUpdate')
    Assert-Exit 1 'a project-scope run refuses -AutoUpdate'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $base 'project'))) 'and writes nothing'
    foreach ($with in @('-AutoUpdate', '-Herdr')) {
        $out = Invoke-Install @('-Unattended', $with)
        Assert-Exit 1 "-Unattended refuses $with"
        Assert-Match $out 'refused: -Unattended is the daily run''s kit-home-only install' 'with one line'
    }

    Write-Host "`r`nphase 5, a run on a clone the last install already installed installs nothing"
    $homeBefore = Get-KitHomeHash
    Invoke-DailyRun
    Assert-Regex $script:LastRun ' done: the kit is already installed at ' 'it says there is nothing to install'
    Assert-True ((Get-Content -LiteralPath (Get-DayLog) -Raw) -notmatch 'install: ') 'install.ps1 did not run'
    Assert-True ((Get-KitHomeHash) -eq $homeBefore) 'nothing in the kit home changed'
    Assert-True (@(Get-Pending).Count -eq 0) 'and no notice'

    Write-Host "`r`nphase 6, an upstream commit with a new hook: kit home only, ask kept, the runner refreshed, one notice"
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
    $installedBefore = (Get-Record).commit
    $upstream = Add-Upstream @('claude\skills\audit\SKILL.md', 'install\auto-update.ps1') 'upstream change with a hook'
    # the user took cc's folder off the PATH after installing; an unattended run must leave it off
    $pathBefore = Get-UserPath
    Assert-True ($pathBefore -like "*$bin*") 'the hand install put the kit bin folder on the user PATH'
    Set-UserPath ((($pathBefore -split ';') | Where-Object { $_ -and $_ -ne $bin }) -join ';')
    $schedulerBefore = (Get-FileHash -LiteralPath $scheduler).Hash
    Invoke-DailyRun
    Assert-Regex $script:LastRun ' done: updated to ' 'the daily run succeeds'
    Assert-True ((Get-Head $clone) -eq $upstream) 'the clone was pulled'
    $logText = Get-Content -LiteralPath (Get-DayLog) -Raw
    Assert-Match (Get-LastInstallLine) 'install.ps1 -Permissions ask -Unattended (the mode read from settings.json' 'it installs unattended, in the mode settings.json has'
    Assert-True ((Get-UserPath) -notlike "*$bin*") 'the user PATH stays as the user left it'
    Assert-Match $logText 'user PATH    left as it is (-Unattended)' 'the log says the PATH step was skipped'
    Assert-Match $logText 'herdr        left as it is (-Unattended)' 'and the Herdr step'
    Assert-True ((Get-FileHash -LiteralPath $scheduler).Hash -eq $schedulerBefore) 'and no scheduled task changed'
    Assert-True (Test-Path -LiteralPath (Join-Path $kitHome 'hooks\zz-kit-test-hook.py')) 'the new hook reached the kit home'
    Assert-Match (Get-Content -LiteralPath $settings -Raw) 'zz-kit-test-hook.py' 'and it is wired in settings.json'
    Assert-True ((Get-Mode) -eq 'default') 'an automatic run never flips ask to bypass'
    Assert-Match (Get-Content -LiteralPath $runner -Raw) '# upstream change with a hook' 'the run refreshed the kit home''s runner, itself'
    Assert-True ((Get-Record).commit -eq $upstream) 'the install it ran left the new record'
    $runs = @(Get-Pending)
    Assert-True ($runs.Count -eq 1 -and $runs[0].result -eq 'updated') 'one notice, an update'
    Assert-True ($runs[0].to -eq $upstream.Substring(0, 7) -and $runs[0].from -eq $installedBefore.Substring(0, 7)) 'it names the new commit and the installed one'
    Assert-True ($runs[0].files -eq 4) "it counts the files changed ($($runs[0].files))"
    Assert-True (@($runs[0].hooks) -contains 'zz-kit-test-hook.py' -and @($runs[0].hooks).Count -eq 1) 'it names the hook added'
    $line = Invoke-Notice
    Assert-Match $line ('now at ' + $upstream.Substring(0, 7) + ' (was ' + $installedBefore.Substring(0, 7) + '), 4 files changed') 'the session start prints the update'
    Assert-Match $line 'new hooks: zz-kit-test-hook.py' 'with the hook added'
    Assert-Match $line ('Log: ' + (Get-DayLog).Replace('\', '\\')) 'and the log path'
    Assert-True ((Invoke-Notice) -eq '') 'the next session start prints nothing'

    Write-Host "`r`nphase 7, the choices of the last hand install are the ones the next run replays"
    $out = Invoke-Install @('-Permissions', 'bypass', '-NoHerdr', '-NoHookMerge')
    Assert-True ((Get-Mode) -eq 'bypassPermissions' -and (Get-Record).noHookMerge -eq $true) 'by hand: bypass and -NoHookMerge, recorded'
    $upstream = Add-Upstream @('README.md') 'upstream change two'
    Invoke-DailyRun
    Assert-Regex $script:LastRun ' done: updated to ' 'the daily run succeeds'
    Assert-Match (Get-LastInstallLine) 'install.ps1 -Permissions bypass -Unattended -NoHookMerge (' 'it replays them'
    Assert-True ((Get-Mode) -eq 'bypassPermissions') 'bypass kept'
    $out = Invoke-Install @('-Permissions', 'ask', '-NoHerdr')
    Assert-True ((Get-Mode) -eq 'default' -and (Get-Record).noHookMerge -eq $false) 'by hand again: ask and no -NoHookMerge'
    $upstream = Add-Upstream @('README.md') 'upstream change three'
    Invoke-DailyRun
    Assert-Match (Get-LastInstallLine) 'install.ps1 -Permissions ask -Unattended (the mode' 'the next run replays those'
    Assert-True ((Get-Mode) -eq 'default') 'and ask stays ask'
    Clear-Pending

    Write-Host "`r`nphase 8, a hand update leaves its record, so the next run has nothing to install and nothing to report"
    $upstream = Add-Upstream @('README.md') 'upstream change four'
    Invoke-Git $clone @('pull', '--quiet', '--ff-only') | Out-Null
    $out = Invoke-Install @('-Permissions', 'ask', '-NoHerdr')
    Assert-True ((Get-Record).commit -eq $upstream) 'the hand update recorded the commit it installed'
    Invoke-DailyRun
    Assert-Regex $script:LastRun ' done: the kit is already installed at ' 'the daily run does not install it again'
    Assert-True (@(Get-Pending).Count -eq 0) 'and reports no change the user made'

    Write-Host "`r`nphase 8b, a hand install from another clone is not undone: the run waits for one from its own clone"
    $other = Join-Path $base 'kit-b'
    Invoke-Git $base @('clone', '--quiet', $origin, $other) | Out-Null
    $upstream = Add-Upstream @('README.md') 'upstream change for the other clone'
    Invoke-Git $other @('pull', '--quiet', '--ff-only') | Out-Null
    $script:Installer = Join-Path $other 'install.ps1'
    try { $out = Invoke-Install @('-Permissions', 'ask', '-NoHerdr') } finally { $script:Installer = Join-Path $clone 'install.ps1' }
    Assert-True ((Get-Record).clone -eq $other) 'the record names the other clone'
    $upstream = Add-Upstream @('README.md') 'upstream change after the other install'
    $cloneHead = Get-Head $clone
    $homeBefore = Get-KitHomeHash
    Invoke-DailyRun
    Assert-True ($script:LastRun.EndsWith("skipped: the last install came from $other; the daily update waits for an install from $clone")) `
        'the run says where the last install came from and waits'
    Assert-True ((Get-Head $clone) -eq $cloneHead) 'the task''s clone was not pulled'
    Assert-True ((Get-KitHomeHash) -eq $homeBefore) 'the other clone''s install stays as it is'
    $runs = @(Get-Pending)
    Assert-True ($runs.Count -eq 1 -and $runs[0].result -eq 'waiting') 'one notice'
    Assert-Match (Invoke-Notice) "paused: the last install came from $($other.Replace('\', '\\'))" 'the session start prints it'
    Invoke-DailyRun
    Assert-Match $script:LastRun 'skipped: the last install came from' 'the next run waits too'
    Assert-True (@(Get-Pending).Count -eq 0) 'and says nothing again'
    $out = Invoke-Install @('-Permissions', 'ask', '-NoHerdr')
    Invoke-DailyRun
    Assert-Regex $script:LastRun (' done: updated to ' + $upstream.Substring(0, 7)) 'after an install from its own clone it resumes by itself'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $logs 'auto-update-waiting.txt'))) 'and forgets the wait'
    Clear-Pending

    Write-Host "`r`nphase 8c, with the record lost the run never merges hooks into settings.json, and records again"
    Remove-Item -LiteralPath $record
    $upstream = Add-Upstream @('README.md') 'upstream change with no record'
    Invoke-DailyRun
    Assert-Regex $script:LastRun ' done: updated to ' 'the run installs'
    Assert-Match (Get-LastLine 'no readable install record') 'so -NoHookMerge' 'it says why'
    Assert-Match (Get-LastInstallLine) '-Unattended -NoHookMerge (' 'it passes -NoHookMerge'
    Assert-True ((Get-Record).noHookMerge -eq $true -and (Get-Record).commit -eq $upstream) 'and the install wrote the record'
    $out = Invoke-Install @('-Permissions', 'ask', '-NoHerdr')
    Clear-Pending

    Write-Host "`r`nphase 9, an install that fails after a pull is reported, and the next run tries it again"
    $installedBefore = (Get-Record).commit
    $setup = Join-Path $origin 'install.ps1'
    [IO.File]::WriteAllText($setup, ([IO.File]::ReadAllText($setup)).Replace('$root = $PSScriptRoot', 'exit 3; $root = $PSScriptRoot'))
    $broken = Add-Upstream @() 'upstream change that breaks the installer'
    Invoke-DailyRun
    Assert-Regex $script:LastRun (' failed: install\.ps1 exited 3 after the pull to ' + $broken.Substring(0, 7)) 'the failed install is the last line'
    Assert-True ((Get-Record).commit -eq $installedBefore) 'the record did not move'
    Assert-Match (Invoke-Notice) 'failed: install.ps1 exited 3' 'the session start prints the failure'
    Invoke-Git $origin @('revert', '--no-edit', 'HEAD') | Out-Null
    $fixed = Add-Upstream @('README.md') 'upstream change after the fix'
    Invoke-DailyRun
    Assert-Regex $script:LastRun (' done: updated to ' + $fixed.Substring(0, 7)) 'the next run installs'
    Assert-True ((@(Get-Pending)[0]).from -eq $installedBefore.Substring(0, 7)) 'and reports from the last commit installed'
    Clear-Pending

    Write-Host "`r`nphase 10, one run at a time: a run that finds the clone's lock held skips, with no notice"
    $upstream = Add-Upstream @('README.md') 'upstream change five'
    $lock = [IO.File]::Open((Join-Path $clone '.git\kit-auto-update.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    $env:KIT_AUTO_UPDATE_LOCK_WAIT = '3'
    $homeBefore = Get-KitHomeHash
    try { Invoke-DailyRun } finally { $lock.Dispose(); $env:KIT_AUTO_UPDATE_LOCK_WAIT = $null }
    Assert-Regex $script:LastRun ' skipped: another update of the clone at .* held its lock' 'it logs a skip line'
    Assert-True ((Get-Head $clone) -ne $upstream) 'nothing was pulled'
    Assert-True ((Get-KitHomeHash) -eq $homeBefore) 'nothing in the kit home changed'
    Assert-True (@(Get-Pending).Count -eq 0) 'and no notice'
    Assert-True ((Invoke-Notice) -eq '') 'a skip is no failure at the session start either'

    Write-Host "`r`nphase 11, never a prompt: prompts off and ssh in batch mode, unless the user has an ssh of their own"
    $code = Get-Content -LiteralPath $runner -Raw
    Assert-Match $code "`$env:GIT_TERMINAL_PROMPT = '0'" 'no terminal prompt'
    Assert-Match $code "`$env:GCM_INTERACTIVE = 'never'" 'no credential dialog'
    Assert-True ((Get-LastLine ' ssh: ') -match ' ssh: ssh -o BatchMode=yes$') 'ssh in batch mode by default'
    Invoke-Git $clone @('config', 'core.sshCommand', 'ssh -i kit-test-key') | Out-Null
    Invoke-DailyRun
    Assert-Match (Get-LastLine ' ssh: ') 'ssh: the user''s own' 'a clone with core.sshCommand keeps it'
    Invoke-Git $clone @('config', '--unset', 'core.sshCommand') | Out-Null
    $env:GIT_SSH = 'plink.exe'
    try { Invoke-DailyRun } finally { $env:GIT_SSH = $null }
    Assert-Match (Get-LastLine ' ssh: ') 'ssh: the user''s own' 'and so does a GIT_SSH such as plink'
    Invoke-DailyRun
    Assert-True ((Get-LastLine ' ssh: ') -match ' ssh: ssh -o BatchMode=yes$') 'batch mode again with neither'

    Write-Host "`r`nphase 12, a clone with changes of its own: logged, nothing pulled or installed"
    Add-Content -LiteralPath (Join-Path $clone 'README.md') -Encoding ascii -Value 'A local edit.'
    $cloneHead = Get-Head $clone
    Invoke-DailyRun
    Assert-Regex $script:LastRun ' failed: the clone at .* has changes of its own' 'the run ends on its failure line'
    Assert-True ((Get-Head $clone) -eq $cloneHead) 'nothing was pulled'
    Assert-True ((Get-KitHomeHash) -eq $homeBefore) 'nothing in the kit home changed'
    Assert-True (@(Get-Pending).Count -eq 1 -and (Get-Pending)[0].result -eq 'failed') 'one notice, a failure'
    Invoke-Git $clone @('checkout', '--', 'README.md') | Out-Null
    Clear-Pending

    Write-Host "`r`nphase 13, a pull that fails (no origin to reach): logged, nothing installed"
    Invoke-Git $clone @('remote', 'set-url', 'origin', (Join-Path $base 'no-such-origin')) | Out-Null
    Invoke-DailyRun
    Assert-Regex $script:LastRun ' failed: git pull --ff-only in ' 'the run ends on its failure line'
    Assert-True ((Get-KitHomeHash) -eq $homeBefore) 'nothing in the kit home changed'
    Assert-Match (Invoke-Notice) 'failed: git pull --ff-only in' 'the session start prints the failure'
    Invoke-Git $clone @('remote', 'set-url', 'origin', $origin) | Out-Null

    Write-Host "`r`nphase 14, a clone that was moved away: the kit home's runner still logs and reports it"
    Rename-Item -LiteralPath $clone -NewName 'kit-moved'
    try {
        Invoke-DailyRun
        Assert-Regex $script:LastRun ' failed: the clone at .* is gone or is not a git clone' 'the run ends on its failure line'
        Assert-True ((Get-KitHomeHash) -eq $homeBefore) 'nothing in the kit home changed'
        Assert-True (@(Get-Pending).Count -eq 1 -and (Get-Pending)[0].line -match 'is gone') 'the notice says the clone is gone'
    } finally { Rename-Item -LiteralPath (Join-Path $base 'kit-moved') -NewName 'kit' }
    Clear-Pending

    Write-Host "`r`nphase 15, the last 30 logs are kept"
    foreach ($day in 1..35) {
        Set-Content -LiteralPath (Join-Path $logs ('auto-update-' + (Get-Date '2020-01-01').AddDays($day - 1).ToString('yyyy-MM-dd') + '.log')) -Value 'old'
    }
    Invoke-DailyRun
    Assert-Regex $script:LastRun (' done: updated to ' + $upstream.Substring(0, 7)) 'the run after the failures pulls the waiting change'
    $kept = @(Get-ChildItem -LiteralPath $logs -File | Where-Object { $_.Name -match '^auto-update-\d{4}-\d{2}-\d{2}\.log$' } | Sort-Object Name)
    Assert-True ($kept.Count -eq 30) "30 logs are kept ($($kept.Count))"
    Assert-True ($kept[-1].Name -eq (Split-Path -Leaf (Get-DayLog))) 'today''s log is one of them'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $logs 'auto-update-2020-01-01.log'))) 'the oldest went'
    Assert-True (Test-Path -LiteralPath (Join-Path $logs 'auto-update-2020-02-04.log')) 'the newest old ones stay'
    Clear-Pending

    Write-Host "`r`nphase 16, -NoAutoUpdate removes this kit home's task, needs no clone, and changes nothing else"
    Assert-True ((Get-Mode) -eq 'default') 'the kit home is in ask mode'
    $homeBefore = Get-KitHomeHash
    $out = Invoke-GetScript @('-Dir', (Join-Path $base 'no-clone'), '-NoAutoUpdate')
    Assert-Exit 0 'get.ps1 -NoAutoUpdate with no clone succeeds'
    Assert-Match $out "auto-update  removed the daily task $taskName" 'it removed the task'
    Assert-True ($out -notmatch 'cloning|updating|Kit home:|PERMISSIONS') 'it cloned, pulled and installed nothing'
    Assert-True ((Get-Mode) -eq 'default') 'ask stays ask, though the line had the default bypass'
    Assert-True ((Get-KitHomeHash) -eq $homeBefore) 'nothing in the kit home changed'
    Assert-True ($null -eq (Get-Task)) 'its task is gone'
    Assert-True ($null -ne (Get-Tasks).$decoy) 'the task of another kit home stays'
    $out = Invoke-Install @('-NoAutoUpdate')
    Assert-Exit 0 'install.ps1 -NoAutoUpdate succeeds'
    Assert-Match $out "auto-update  no daily task $taskName" 'and finds none left'
    Assert-True ($out -notmatch 'PERMISSIONS|Kit home:') 'it ran no install'
    Assert-True ((Get-KitHomeHash) -eq $homeBefore) 'nothing in the kit home changed'
    Assert-True ([bool](Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) -eq $realTaskBefore) 'the real Task Scheduler was never touched'
} finally {
    $env:KIT_SCHEDULER = $null
    $env:KIT_AUTO_UPDATE_LOCK_WAIT = $null
    Close-KitSandbox $realProfile
}

Write-TestResult
