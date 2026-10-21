#requires -Version 5.1
<#
auto-update.ps1 - the daily kit update, run by the scheduled task that install.ps1 -AutoUpdate registers
(install\schedule.ps1). Not meant to be run by hand, though nothing breaks if it is.

It does what the get.ps1 line does on a machine that already has the clone, with no question: git pull --ff-only in
the clone, then install.ps1 with the options of the run that enabled it. Everything goes to
<kit home>\logs\auto-update-<yyyy-MM-dd>.log, and the last 30 of those are kept. A clone with changes of its own,
or a pull that fails (no network, a branch that went its own way), is logged and nothing is installed.

A run that moved the kit, or failed, appends one JSON line to logs\auto-update-pending.jsonl; the session-start hook
kit-update-notice.py prints it once. logs\auto-update-installed.txt holds the commit the last good run installed,
so a run that pulled and then failed to install is reported as an update by the next run that succeeds.
#>
param(
    [Parameter(Mandatory = $true)][string]$Dir,
    [Parameter(Mandatory = $true)][string]$KitHome,
    [Parameter(Mandatory = $true)][ValidateSet('bypass', 'ask')][string]$Permissions,
    [switch]$Herdr,
    [switch]$NoHerdr,
    [switch]$NoHookMerge
)
$ErrorActionPreference = 'Continue'
# Never a prompt: no git credential dialog or terminal question on an unattended run.
$env:GIT_TERMINAL_PROMPT = '0'
$env:GCM_INTERACTIVE = 'never'

$logs = Join-Path $KitHome 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$started   = Get-Date
$log       = Join-Path $logs ('auto-update-' + $started.ToString('yyyy-MM-dd') + '.log')
$pending   = Join-Path $logs 'auto-update-pending.jsonl'
$installed = Join-Path $logs 'auto-update-installed.txt'

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

$flags = @('-Permissions', $Permissions)
if ($Herdr) { $flags += '-Herdr' }
if ($NoHerdr) { $flags += '-NoHerdr' }
if ($NoHookMerge) { $flags += '-NoHookMerge' }
Write-UpdateLog "start: clone $Dir, kit home $KitHome, install.ps1 $($flags -join ' ')"
Get-ChildItem -LiteralPath $logs -File | Where-Object { $_.Name -match '^auto-update-\d{4}-\d{2}-\d{2}\.log$' } |
    Sort-Object Name -Descending | Select-Object -Skip 30 | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }

if (-not (Test-Path -LiteralPath (Join-Path $Dir '.git'))) { Stop-Update "$Dir is not a git clone; nothing was pulled or installed" }
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
# The commit the last good run installed, so a run that pulled but failed to install is not lost.
$from = $before
if (Test-Path -LiteralPath $installed) {
    $kept = (Get-Content -LiteralPath $installed -TotalCount 1)
    if ($kept -match '^[0-9a-f]{40}$') { $from = $kept }
}
Write-UpdateLog "kit at $after (was $from)"

$env:KIT_HOME = $KitHome
$ps = if ($env:SystemRoot) { Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe' } else { 'powershell' }
$output = @(& $ps -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $Dir 'install.ps1') @flags 2>&1 |
    ForEach-Object { "$_" })
$code = $LASTEXITCODE
foreach ($line in $output) { Write-UpdateLog "  install: $line" }
if ($code -ne 0) { Stop-Update "install.ps1 exited $code after the pull to $($after.Substring(0, 7)); the kit home is as that run left it" }
Set-Content -LiteralPath $installed -Value $after -Encoding ASCII

if ($after -ne $from) {
    $files = @(& git -C $Dir diff --name-only $from $after 2>$null).Count
    $old = Get-KitHookScripts $from
    $new = Get-KitHookScripts $after
    $hooks = @(if ($null -ne $old -and $null -ne $new) { $new | Where-Object { $old -notcontains $_ } })
    Add-UpdateNotice ([ordered]@{ result = 'updated'; from = $from.Substring(0, 7); to = $after.Substring(0, 7);
        files = $files; hooks = $hooks })
    Write-UpdateLog "done: updated to $($after.Substring(0, 7)), $files files changed$(if ($hooks.Count) { ', new hooks: ' + ($hooks -join ', ') })"
} else {
    Write-UpdateLog "done: the kit was already up to date at $($after.Substring(0, 7))"
}
exit 0
