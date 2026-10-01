# get.ps1 - install claude-base on a Windows machine with one command, from PowerShell:
#
#   irm https://raw.githubusercontent.com/ElkinDev/claude-base/main/install/get.ps1 | iex
#
# With options, run it as a script block, for example to skip the Herdr question:
#
#   & ([scriptblock]::Create((irm https://raw.githubusercontent.com/ElkinDev/claude-base/main/install/get.ps1))) -NoHerdr
#
# What it does, in order:
#   1. Checks git, Python 3.8+ and Claude Code, and offers to install a missing one: git and Python
#      through winget, Claude Code through its official installer (https://claude.ai/install.ps1).
#   2. Clones the kit into -Dir (default %USERPROFILE%\claude-base), or updates a clone already there.
#   3. Runs install.ps1 at user scope. It never overwrites a file of yours: the kit version lands
#      beside it as <name>.new. It then asks whether to add Herdr, and Enter is yes.
#   4. Runs scripts\doctor.py and says what is left to do by hand (signing in to Claude Code).
#
# -Yes answers yes to every question (Herdr too, unless -NoHerdr). -DryRun installs nothing and runs
# install.ps1 -DryRun when a clone is already there. -Permissions is passed to install.ps1.
#
# The tests replace the external commands through the environment: CLAUDE_BASE_WINGET (winget),
# CLAUDE_BASE_CLAUDE_INSTALLER (a .ps1 instead of the Claude Code installer), CLAUDE_BASE_MISSING
# (a comma list of tools to treat as missing), CLAUDE_BASE_ANSWER (the answer to every question) and
# CLAUDE_BASE_USER_PATH_FILE (a file standing in for the user PATH in the registry).
param(
    [string]$Dir = $(if ($env:CLAUDE_BASE_DIR) { $env:CLAUDE_BASE_DIR } else { Join-Path $env:USERPROFILE 'claude-base' }),
    [string]$Repo = $(if ($env:CLAUDE_BASE_REPO) { $env:CLAUDE_BASE_REPO } else { 'https://github.com/ElkinDev/claude-base.git' }),
    [switch]$Herdr,
    [switch]$NoHerdr,
    [switch]$Yes,
    [switch]$DryRun,
    [ValidateSet('bypass', 'ask')]
    [string]$Permissions = 'bypass'
)

function Test-GetInteractive {
    try {
        return ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected -and
                -not [Console]::IsOutputRedirected)
    } catch { return $false }
}

function Read-GetYes {
    # True on yes. Enter is yes; with no console and no -Yes the answer is no.
    param([string]$Question)
    if ($Yes) { Write-Host "$Question yes (-Yes)"; return $true }
    if ($null -ne $env:CLAUDE_BASE_ANSWER) { $answer = $env:CLAUDE_BASE_ANSWER }
    elseif (Test-GetInteractive) { $answer = Read-Host "$Question [Y/n]" }
    else { Write-Host "$Question no (no console to ask; pass -Yes)"; return $false }
    return -not ($answer -match '^\s*(n|no)\s*$')
}

function Update-GetSessionPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
}

function Test-GetMissing {
    param([string]$Tool)
    return (@(($env:CLAUDE_BASE_MISSING -split ',') | ForEach-Object { $_.Trim() }) -contains $Tool)
}

function Find-GetPython {
    # The WindowsApps python.exe is a Store shortcut that prints nothing, so a python counts only
    # when it answers with a version of 3.8 or later.
    if (Test-GetMissing 'python') { return $null }
    foreach ($candidate in @(@('python'), @('py', '-3'))) {
        if (-not (Get-Command $candidate[0] -ErrorAction SilentlyContinue)) { continue }
        $args2 = @($candidate | Select-Object -Skip 1) + @('--version')
        $text = (& $candidate[0] @args2 2>&1 | Out-String)
        if ($text -match 'Python (\d+)\.(\d+)' -and ([int]$Matches[1] -gt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 8))) {
            return $candidate
        }
    }
    return $null
}

function Get-GetUserPath {
    if ($env:CLAUDE_BASE_USER_PATH_FILE) {
        if (Test-Path -LiteralPath $env:CLAUDE_BASE_USER_PATH_FILE) { return (Get-Content -LiteralPath $env:CLAUDE_BASE_USER_PATH_FILE -Raw).Trim() }
        return ''
    }
    return [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Set-GetUserPath {
    param([string]$Value)
    if ($env:CLAUDE_BASE_USER_PATH_FILE) { Set-Content -LiteralPath $env:CLAUDE_BASE_USER_PATH_FILE -Value $Value -Encoding ascii; return }
    [Environment]::SetEnvironmentVariable('Path', $Value, 'User')
}

function Add-GetClaudePath {
    # The native installer puts claude.exe in %USERPROFILE%\.local\bin and has been seen to leave that
    # folder off PATH (it prints how to add it). Add it to this session and to the user PATH once.
    $bin = Join-Path $env:USERPROFILE '.local\bin'
    if (-not (Test-Path -LiteralPath (Join-Path $bin 'claude.exe'))) { return $false }
    if (-not (($env:Path -split ';') -contains $bin)) { $env:Path = "$env:Path;$bin" }
    $user = Get-GetUserPath
    if (-not (($user -split ';') -contains $bin)) {
        Set-GetUserPath ((@($user, $bin) | Where-Object { $_ }) -join ';')
        Write-Host "  added $bin to your user PATH"
    }
    return $true
}

function Test-GetTool {
    param([string]$Tool)
    if (Test-GetMissing $Tool) { return $false }
    if ($Tool -eq 'python') { return [bool](Find-GetPython) }
    if ($Tool -eq 'claude' -and -not (Get-Command claude -ErrorAction SilentlyContinue)) { return (Add-GetClaudePath) }
    return [bool](Get-Command $Tool -ErrorAction SilentlyContinue)
}

function Install-GetTool {
    # Offers one missing tool; returns $true when it is there afterwards.
    param([string]$Tool, [string]$Name)
    if (-not (Read-GetYes "$Name is missing. Install it?")) { return $false }
    $winget = if ($env:CLAUDE_BASE_WINGET) { $env:CLAUDE_BASE_WINGET } else { 'winget' }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($Tool -eq 'claude') {
            if ($env:CLAUDE_BASE_CLAUDE_INSTALLER) {
                & powershell -NoProfile -ExecutionPolicy Bypass -File $env:CLAUDE_BASE_CLAUDE_INSTALLER | Out-Host
            } else {
                & powershell -NoProfile -ExecutionPolicy Bypass -Command 'irm https://claude.ai/install.ps1 | iex' | Out-Host
            }
        } else {
            if (-not $env:CLAUDE_BASE_WINGET -and -not (Get-Command winget -ErrorAction SilentlyContinue)) {
                Write-Host "  winget is not available, so $Name cannot be installed from here. Install it by hand and run this again."
                return $false
            }
            $id = if ($Tool -eq 'git') { 'Git.Git' } else { 'Python.Python.3.12' }
            & $winget install --id $id --exact --source winget --accept-package-agreements --accept-source-agreements | Out-Host
        }
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
    if ($code -ne 0) { Write-Host "  the $Name installer exited $code"; return $false }
    Update-GetSessionPath
    if (Test-GetTool $Tool) { Write-Host "  ok    $Name"; return $true }
    Write-Host "  $Name installed, but this window cannot see it yet. Open a new PowerShell window and run the same command again."
    return $false
}

function Invoke-GetMain {
    Write-Host "claude-base: one-command install into $Dir"
    if ($PSVersionTable.PSVersion.Major -lt 5) { Write-Host 'Windows PowerShell 5.1 or later is required.'; return 1 }
    if ($Herdr -and $NoHerdr) { Write-Host '-Herdr and -NoHerdr cannot both be given.'; return 1 }

    $tools = @(
        @{ Tool = 'git';    Name = 'Git for Windows (git, and the Git Bash the hooks run in)' },
        @{ Tool = 'python'; Name = 'Python 3' },
        @{ Tool = 'claude'; Name = 'Claude Code' })
    foreach ($t in $tools) {
        if (Test-GetTool $t.Tool) { Write-Host ("  ok    {0}" -f $t.Name); continue }
        if ($DryRun) { Write-Host ("  would offer to install {0}" -f $t.Name); continue }
        if (-not (Install-GetTool $t.Tool $t.Name)) {
            Write-Host ("Stopped: {0} is required. Nothing of the kit was installed." -f $t.Name)
            return 1
        }
    }

    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if (Test-Path -LiteralPath (Join-Path $Dir '.git')) {
            $origin = (& git -C $Dir remote get-url origin 2>$null)
            if ("$origin".Trim() -ne $Repo) {
                Write-Host "Stopped: $Dir is a clone of $origin, not of $Repo. Pass -Dir to use another folder."
                return 1
            }
            if ($DryRun) { Write-Host "would update $Dir (git pull --ff-only)" }
            else {
                Write-Host "updating $Dir"
                & git -C $Dir pull --ff-only --quiet 2>&1 | ForEach-Object { "$_" } | Out-Host
                if ($LASTEXITCODE -ne 0) { Write-Host "Stopped: git pull in $Dir failed; it has changes of its own. Resolve them or pass -Dir."; return 1 }
            }
        } elseif ((Test-Path -LiteralPath $Dir) -and @(Get-ChildItem -LiteralPath $Dir -Force).Count -gt 0) {
            Write-Host "Stopped: $Dir exists and is not a claude-base clone. Pass -Dir to use another folder."
            return 1
        } elseif ($DryRun) {
            Write-Host "would clone $Repo into $Dir, then run install.ps1"
            return 0
        } else {
            Write-Host "cloning $Repo into $Dir"
            & git clone --quiet $Repo $Dir 2>&1 | ForEach-Object { "$_" } | Out-Host
            if ($LASTEXITCODE -ne 0) { Write-Host "Stopped: git clone of $Repo failed."; return 1 }
        }

        $installArgs = @('-Permissions', $Permissions)
        if ($DryRun) { $installArgs += '-DryRun' }
        # The question is asked here, not by install.ps1: its output goes through this script, so it
        # sees no console and would skip the question.
        if ($NoHerdr) { $installArgs += '-NoHerdr' }
        elseif ($Herdr) { $installArgs += '-Herdr' }
        elseif ($DryRun) { }
        elseif (Read-GetYes 'Add Herdr, the terminal workspace for agents, on its preview channel and set up the way this kit uses it?') { $installArgs += '-Herdr' }
        else { $installArgs += '-NoHerdr' }
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dir 'install.ps1') @installArgs | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Host "Stopped: install.ps1 exited $LASTEXITCODE."; return 1 }
        if ($DryRun) { return 0 }

        # A one-item array comes back from a function as a plain string, so wrap it again.
        $python = @(Find-GetPython)
        if ($python.Count -gt 0) {
            Write-Host ''
            $pyArgs = @($python | Select-Object -Skip 1) + @((Join-Path $Dir 'scripts\doctor.py'))
            & $python[0] @pyArgs | Out-Host
        }
    } finally { $ErrorActionPreference = $previous }

    Write-Host ''
    Write-Host 'Left to do by hand:'
    Write-Host '  1. Open a new terminal and run claude, then sign in (one sign-in per account).'
    Write-Host "  2. Scaffold a project: powershell -ExecutionPolicy Bypass -File `"$Dir\install.ps1`" -Project <path>"
    Write-Host "  3. Several accounts on one machine: $Dir\docs\ACCOUNTS.md"
    return 0
}

$getExit = Invoke-GetMain
# Run as this file (powershell -File get.ps1), the exit code reaches the caller. Run through iex, a
# script block, or dot-sourced, it shares the caller's session or script, where exit would end it,
# so it only sets LASTEXITCODE and says how it ended.
$global:LASTEXITCODE = $getExit
if ($MyInvocation.InvocationName -ne '.' -and $MyInvocation.MyCommand.Path -and
    [IO.Path]::GetFileName($MyInvocation.MyCommand.Path) -eq 'get.ps1') { exit $getExit }
if ($getExit -ne 0) { Write-Host "claude-base install did not finish (code $getExit)." }
