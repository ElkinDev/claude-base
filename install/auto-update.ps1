#requires -Version 5.1
<#
auto-update.ps1 - the daily kit update. Every install copies it to <kit home>\kit-auto-update.ps1, and the scheduled
task that install.ps1 -AutoUpdate registers (install\schedule.ps1) runs that copy, so a clone that was moved or
deleted is logged and reported rather than stopping the updates in silence. Not meant to be run by hand, though
nothing breaks if it is.

It replays the last install, it never makes one up. Every install that finishes, by hand or by this run, writes
<kit home>\.kit-install.json: the options it ran with and the clone commit it installed. This run pulls the clone
and runs install.ps1 only when the clone's commit differs from that record's, with -NoHookMerge as the record has it
and the permission mode read from the live settings.json (bypassPermissions is bypass, anything else, a missing or
unreadable file included, is ask). It passes -Unattended, so install.ps1 changes nothing outside the kit home: no
user PATH, no Herdr, no scheduled task. A failed install writes no record, so the next run tries it again.

It never waits on a question: git runs with GIT_TERMINAL_PROMPT=0, GCM_INTERACTIVE=never and an ssh in batch mode.
One run at a time per clone: a run holds <clone>\.git\kit-auto-update.lock open for its whole length (the system
lets go of it when the process ends, however it ends), and a run that cannot take it within
KIT_AUTO_UPDATE_LOCK_WAIT seconds (600) logs a skip line and leaves no notice.

Everything goes to <kit home>\logs\auto-update-<yyyy-MM-dd>.log, and the last 30 of those are kept. Each run starts
with a start line and ends with a done, failed or skipped line; a run the task stopped at its time limit has no end
line, and the session-start hook reports it. A missing clone, a clone with changes of its own, or a pull that fails
is logged and nothing is installed. A run that moved the kit, or failed, appends one JSON line to
logs\auto-update-pending.jsonl; the session-start hook kit-update-notice.py prints it once.
#>
param(
    [Parameter(Mandatory = $true)][string]$Dir,
    [Parameter(Mandatory = $true)][string]$KitHome
)
$ErrorActionPreference = 'Continue'
# Never a prompt: no terminal question, no credential dialog, no ssh passphrase.
$env:GIT_TERMINAL_PROMPT = '0'
$env:GCM_INTERACTIVE = 'never'
$env:GIT_SSH_COMMAND = 'ssh -o BatchMode=yes'

$logs = Join-Path $KitHome 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$started = Get-Date
$log     = Join-Path $logs ('auto-update-' + $started.ToString('yyyy-MM-dd') + '.log')
$pending = Join-Path $logs 'auto-update-pending.jsonl'
$record  = Join-Path $KitHome '.kit-install.json'

function Write-UpdateLog {
    param([string]$Line)
    Add-Content -LiteralPath $log -Value ('{0:HH:mm:ss} {1}' -f (Get-Date), $Line) -Encoding UTF8
}

function Add-UpdateNotice {
    param($Entry)
    $Entry.time = $started.ToString('yyyy-MM-dd HH:mm')
    $Entry.log  = $log
    Add-Content -LiteralPath $pending -Value ($Entry | ConvertTo-Json -Compress) -Encoding UTF8
}

function Stop-Update {
    param([string]$Why)
    Write-UpdateLog "failed: $Why"
    Add-UpdateNotice ([ordered]@{ result = 'failed'; line = $Why })
    exit 1
}

function Invoke-UpdateGit {
    # git in the clone, its output as strings in the log, the exit code in $LASTEXITCODE.
    param([string[]]$GitArgs)
    $out = @(& git -C $Dir @GitArgs 2>&1 | ForEach-Object { "$_" })
    foreach ($line in $out) { if ($line.Trim()) { Write-UpdateLog "  git: $line" } }
    return ,$out
}

function Get-KitHookScripts {
    # The hook scripts the kit's settings.json names at a commit, or $null when git cannot show it.
    param([string]$Commit)
    $text = (& git -C $Dir show "${Commit}:claude/settings.json" 2>$null) -join "`n"
    if ($LASTEXITCODE -ne 0) { return $null }
    return @([regex]::Matches($text, 'hooks[/\\]+([A-Za-z0-9_.-]+\.(?:py|ps1|sh))') |
        ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique)
}

function Read-InstallRecord {
    # The last finished install's record, or $null.
    try { return (Get-Content -LiteralPath $record -Raw -ErrorAction Stop | ConvertFrom-Json) } catch { return $null }
}

function Get-LivePermissions {
    # The mode the user has now, read from the settings.json in use: an automatic run never changes it.
    try {
        $mode = (Get-Content -LiteralPath (Join-Path $KitHome 'settings.json') -Raw -ErrorAction Stop |
            ConvertFrom-Json).permissions.defaultMode
    } catch { $mode = $null }
    if ($mode -eq 'bypassPermissions') { return 'bypass' }
    return 'ask'
}

Write-UpdateLog "start: clone $Dir, kit home $KitHome"
Get-ChildItem -LiteralPath $logs -File | Where-Object { $_.Name -match '^auto-update-\d{4}-\d{2}-\d{2}\.log$' } |
    Sort-Object Name -Descending | Select-Object -Skip 30 | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }

if (-not (Test-Path -LiteralPath (Join-Path $Dir '.git') -PathType Container)) {
    Stop-Update "the clone at $Dir is gone or is not a git clone; nothing was pulled or installed. Run the get.ps1 line with -AutoUpdate to point the task at a clone"
}
$wait = 600
if ($env:KIT_AUTO_UPDATE_LOCK_WAIT -match '^\d+$') { $wait = [int]$env:KIT_AUTO_UPDATE_LOCK_WAIT }
$lockPath = Join-Path $Dir '.git\kit-auto-update.lock'
$lock = $null
$until = (Get-Date).AddSeconds($wait)
while (-not $lock) {
    try { $lock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
    catch {
        if ((Get-Date) -ge $until) {
            Write-UpdateLog "skipped: another update of the clone at $Dir held its lock for $wait s; this run did nothing"
            exit 0
        }
        Start-Sleep -Seconds 2
    }
}
# $lock stays open until this process ends.

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Stop-Update 'git is not on PATH; nothing was pulled or installed' }
$before = ((Invoke-UpdateGit @('rev-parse', 'HEAD')) | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0) { Stop-Update "git could not read the clone at $Dir; nothing was pulled or installed" }
$changes = Invoke-UpdateGit @('status', '--porcelain', '--untracked-files=no')
if ($changes.Count -gt 0) {
    Stop-Update "the clone at $Dir has changes of its own ($($changes.Count) files); nothing was pulled or installed"
}
$pull = Invoke-UpdateGit @('pull', '--ff-only')
if ($LASTEXITCODE -ne 0) {
    $last = @($pull | Where-Object { $_.Trim() } | Select-Object -Last 1)
    Stop-Update "git pull --ff-only in $Dir failed: $(if ($last) { $last[0].Trim() } else { "exit $LASTEXITCODE" }); nothing was installed"
}
$after = ((Invoke-UpdateGit @('rev-parse', 'HEAD')) | Select-Object -Last 1)

# The last finished install, by hand or by this task. Its commit equal to the clone's: nothing to install. No record
# (a kit installed before records existed): install once, and report from the commit the clone had before the pull.
$prior = Read-InstallRecord
$from = if ($prior -and "$($prior.commit)" -match '^[0-9a-f]{40}$') { "$($prior.commit)" } else { $null }
if ($from -eq $after) {
    Write-UpdateLog "done: the kit is already installed at $($after.Substring(0, 7)); nothing to install"
    exit 0
}
if (-not $from) { $from = $before }
Write-UpdateLog "kit at $after (installed $from)"

$flags = @('-Permissions', (Get-LivePermissions), '-Unattended')
if ($prior -and $prior.noHookMerge -eq $true) { $flags += '-NoHookMerge' }
Write-UpdateLog "install.ps1 $($flags -join ' ') (the mode read from settings.json, -NoHookMerge as the last install had it)"
$env:KIT_HOME = $KitHome
$ps = if ($env:SystemRoot) { Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe' } else { 'powershell' }
$output = @(& $ps -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $Dir 'install.ps1') @flags 2>&1 |
    ForEach-Object { "$_" })
$code = $LASTEXITCODE
foreach ($line in $output) { Write-UpdateLog "  install: $line" }
if ($code -ne 0) {
    Stop-Update "install.ps1 exited $code after the pull to $($after.Substring(0, 7)); the clone is at that commit, the kit home as that run left it (its output is above in the log), and the next run tries the install again"
}

if ($after -ne $from) {
    $files = @(& git -C $Dir diff --name-only $from $after 2>$null).Count
    $old = Get-KitHookScripts $from
    $new = Get-KitHookScripts $after
    $hooks = @(if ($null -ne $old -and $null -ne $new) { $new | Where-Object { $old -notcontains $_ } })
    Add-UpdateNotice ([ordered]@{ result = 'updated'; from = $from.Substring(0, 7); to = $after.Substring(0, 7);
        files = $files; hooks = $hooks })
    Write-UpdateLog "done: updated to $($after.Substring(0, 7)), $files files changed$(if ($hooks.Count) { ', new hooks: ' + ($hooks -join ', ') })"
} else {
    Write-UpdateLog "done: installed $($after.Substring(0, 7)), the commit the clone already had"
}
exit 0
