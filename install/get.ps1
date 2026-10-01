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
#      through winget; Claude Code through npm when npm is on PATH (npm install -g, which works on
#      machines that allow no installers), else through its official installer
#      (https://claude.ai/install.ps1). A Claude Code already installed, either way, is left as it is.
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
# (a comma list of tools to treat as missing), CLAUDE_BASE_NPM (an npm instead of the one on PATH, or
# 'none' for no npm), CLAUDE_BASE_ANSWER (the answer to every question) and
# CLAUDE_BASE_ENV_KEY (a throwaway key under HKCU standing in for HKCU\Environment, the user PATH).
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
    if ($null -ne $env:CLAUDE_BASE_ANSWER) { $answer = $env:CLAUDE_BASE_ANSWER; Write-Host "$Question '$answer' (CLAUDE_BASE_ANSWER)" }
    elseif (Test-GetInteractive) { $answer = Read-Host "$Question [Y/n]" }
    else { Write-Host "$Question no (no console to ask; pass -Yes)"; return $false }
    return -not ($answer -match '^\s*(n|no)\s*$')
}

function Read-GetUserPath {
    # The user PATH as the registry holds it, unexpanded: HKCU\Environment, or the tests' key.
    $name = if ($env:CLAUDE_BASE_ENV_KEY) { $env:CLAUDE_BASE_ENV_KEY } else { 'Environment' }
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($name)
    if (-not $key) { return '' }
    try { return [string]$key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames) }
    finally { $key.Close() }
}

function Test-GetPathHas {
    # Whether a PATH list holds a folder, whatever its case, a trailing backslash or a %VARIABLE% spelling.
    param([string]$List, [string]$Folder)
    $want = $Folder.TrimEnd('\')
    foreach ($entry in ($List -split ';')) {
        if ($entry -and [Environment]::ExpandEnvironmentVariables($entry).TrimEnd('\') -eq $want) { return $true }
    }
    return $false
}

function Update-GetSessionPath {
    # After an install the session keeps every folder it had, a Node from fnm lives only in the session
    # PATH, and gains the folders the installer added to the machine and user PATH, first: a new Python
    # must win over the WindowsApps python.exe Store stub already in the session, as it does in a new
    # window, where the installer put it ahead of that stub.
    $new = @()
    foreach ($list in @([Environment]::GetEnvironmentVariable('Path', 'Machine'), (Read-GetUserPath))) {
        foreach ($entry in ("$list" -split ';')) {
            if (-not $entry) { continue }
            $folder = [Environment]::ExpandEnvironmentVariables($entry)
            if (-not (Test-GetPathHas (($new -join ';') + ';' + $env:Path) $folder)) { $new += $folder }
        }
    }
    if ($new) { $env:Path = (@($new) + @($env:Path.TrimEnd(';'))) -join ';' }
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

function Send-GetSettingChange {
    # Tells Explorer the environment changed, so a window opened from it sees the new PATH.
    if ($env:CLAUDE_BASE_ENV_KEY) { return }
    try {
        Add-Type -Namespace ClaudeBaseGet -Name Native -MemberDefinition @'
[DllImport("user32.dll", CharSet = CharSet.Unicode)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
'@
        $result = [UIntPtr]::Zero
        [void][ClaudeBaseGet.Native]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$result)
    } catch { }
}

function Add-GetUserPath {
    # Appends a folder to the user PATH as written in the registry, unexpanded, keeping the value's type:
    # a REG_EXPAND_SZ Path keeps its %VARIABLE% entries. A dry run only says what it would add.
    param([string]$Folder)
    $name = if ($env:CLAUDE_BASE_ENV_KEY) { $env:CLAUDE_BASE_ENV_KEY } else { 'Environment' }
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($name)
    try {
        $raw = [string]$key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        if (Test-GetPathHas $raw $Folder) { return }
        if ($DryRun) { Write-Host "  would add $Folder to your user PATH"; return }
        $kind = if ($key.GetValueNames() -contains 'Path') { $key.GetValueKind('Path') } else { [Microsoft.Win32.RegistryValueKind]::ExpandString }
        $key.SetValue('Path', ((@($raw.TrimEnd(';'), $Folder) | Where-Object { $_ }) -join ';'), $kind)
    } finally { $key.Close() }
    Send-GetSettingChange
    Write-Host "  added $Folder to your user PATH"
}

function Add-GetClaudePath {
    # The native installer puts claude.exe in %USERPROFILE%\.local\bin and has been seen to leave that
    # folder off PATH (it prints how to add it). Add it to this session and to the user PATH once.
    $bin = Join-Path $env:USERPROFILE '.local\bin'
    if (-not (Test-Path -LiteralPath (Join-Path $bin 'claude.exe'))) { return $false }
    if (-not (Test-GetPathHas $env:Path $bin)) { $env:Path = "$env:Path;$bin" }
    Add-GetUserPath $bin
    return $true
}

function Add-GetNpmPath {
    # After npm install -g: npm puts claude.cmd in its global prefix, which can be on no PATH (a Node
    # unpacked from a zip, an .npmrc prefix). When claude.cmd is there and the session cannot see the
    # folder, add it to the session and to the user PATH. A folder the session already has, such as
    # fnm's, is left alone: it is not the user PATH's to hold.
    param([string]$Npm)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    # The prefix is the last line npm prints, and only from a run that exited 0: an npm wrapper can
    # print a warning on stdout before it.
    try {
        $lines = @(& $Npm prefix -g 2>$null)
        $prefix = if ($LASTEXITCODE -eq 0) { ("$($lines | Where-Object { "$_".Trim() } | Select-Object -Last 1)").Trim() } else { '' }
    } catch { $prefix = '' } finally { $ErrorActionPreference = $previous }
    if (-not $prefix -or -not (Test-Path -IsValid -LiteralPath $prefix) -or -not [IO.Path]::IsPathRooted($prefix)) { return }
    if (-not (Test-Path -LiteralPath (Join-Path $prefix 'claude.cmd'))) { return }
    if (Test-GetPathHas $env:Path $prefix) { return }
    $env:Path = "$($env:Path.TrimEnd(';'));$prefix"
    Add-GetUserPath $prefix
}

function Test-GetTool {
    param([string]$Tool)
    if (Test-GetMissing $Tool) { return $false }
    if ($Tool -eq 'python') { return [bool](Find-GetPython) }
    if ($Tool -eq 'claude' -and -not (Get-Command claude -ErrorAction SilentlyContinue)) { return (Add-GetClaudePath) }
    return [bool](Get-Command $Tool -ErrorAction SilentlyContinue)
}

function Get-GetPowerShell {
    # Windows PowerShell by its full path, for the installers this script starts: a machine whose PATH lost the
    # WindowsPowerShell folder still finds it (the Run box does through App Paths, a PATH lookup does not).
    if ($env:SystemRoot) {
        $full = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        if (Test-Path -LiteralPath $full) { return $full }
    }
    return 'powershell'
}

function Find-GetNpm {
    # npm.cmd, never npm.ps1, which an execution policy can block.
    if ($env:CLAUDE_BASE_NPM) { if ($env:CLAUDE_BASE_NPM -eq 'none') { return $null } return $env:CLAUDE_BASE_NPM }
    $c = Get-Command npm.cmd -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Source }
    return $null
}

function Get-GetRoute {
    # How a missing tool would be installed, as the question and the dry run name it.
    param([string]$Tool)
    if ($Tool -ne 'claude') { return 'through winget' }
    if (Find-GetNpm) { return 'with npm (npm install -g @anthropic-ai/claude-code)' }
    return 'with its official installer (https://claude.ai/install.ps1)'
}

function Install-GetTool {
    # Offers one missing tool; returns $true when it is there afterwards.
    param([string]$Tool, [string]$Name)
    if (-not (Read-GetYes ("$Name is missing. Install it {0}?" -f (Get-GetRoute $Tool)))) { return $false }
    $winget = if ($env:CLAUDE_BASE_WINGET) { $env:CLAUDE_BASE_WINGET } else { 'winget' }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $npm = if ($Tool -eq 'claude') { Find-GetNpm } else { $null }
        if ($npm) {
            & $npm install -g '@anthropic-ai/claude-code' | Out-Host
        } elseif ($Tool -eq 'claude') {
            if ($env:CLAUDE_BASE_CLAUDE_INSTALLER) {
                & (Get-GetPowerShell) -NoProfile -ExecutionPolicy Bypass -File $env:CLAUDE_BASE_CLAUDE_INSTALLER | Out-Host
            } else {
                & (Get-GetPowerShell) -NoProfile -ExecutionPolicy Bypass -Command 'irm https://claude.ai/install.ps1 | iex' | Out-Host
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
    if ($npm) { Add-GetNpmPath $npm }
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
        if ($DryRun) { Write-Host ("  would offer to install {0} {1}" -f $t.Name, (Get-GetRoute $t.Tool)); continue }
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
        & (Get-GetPowerShell) -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dir 'install.ps1') @installArgs | Out-Host
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
    # npm puts a claude.ps1 beside claude.cmd, and PowerShell picks the .ps1, which a Restricted
    # execution policy (the Windows default) refuses; claude.cmd runs under any policy.
    $found = Get-Command claude -ErrorAction SilentlyContinue | Select-Object -First 1
    $run = if ($found -and $found.Source -like '*.ps1') { 'claude.cmd' } else { 'claude' }
    Write-Host "  1. Open a new terminal and run $run, then sign in (one sign-in per account)."
    Write-Host "  2. Scaffold a project: $(Get-GetPowerShell) -ExecutionPolicy Bypass -File `"$Dir\install.ps1`" -Project <path>"
    Write-Host "  3. Several accounts on one machine: $Dir\docs\ACCOUNTS.md"
    return 0
}

$getExit = Invoke-GetMain
# Run as this file (powershell -File get.ps1, under any name), the exit code reaches the caller. Run
# through iex, a script block, or dot-sourced, it shares the caller's session or script, where exit
# would end it, so it only sets LASTEXITCODE and says how it ended. Under iex inside a script,
# MyCommand.Path names that script, so the file must also be this one: it holds the line below.
$global:LASTEXITCODE = $getExit
$getSelf = $MyInvocation.MyCommand.Path
if ($MyInvocation.InvocationName -ne '.' -and $getSelf -and (Test-Path -LiteralPath $getSelf) -and
    (Select-String -LiteralPath $getSelf -SimpleMatch -Quiet -Pattern ('claude-base get.ps1 ' + 'exit marker'))) { exit $getExit }
# claude-base get.ps1 exit marker
if ($getExit -ne 0) { Write-Host "claude-base install did not finish (code $getExit)." }
