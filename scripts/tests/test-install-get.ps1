# The one-command bootstrap, install\get.ps1: the prerequisite offers, the clone and its update, the
# refusals, the Herdr question, and the hand-off to install.ps1 and the doctor. The kit is cloned from
# this checkout (its committed HEAD), never from GitHub, into the sandbox. winget, the Claude Code
# installer, herdr and the hotkey are stubs: no test here installs anything on the machine.
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tests\test-install-get.ps1

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'lib-install-test.ps1')

$realProfile = $env:USERPROFILE
$realPath    = $env:Path
$base        = New-KitSandbox 'kit-install-get'
$kitHome     = $script:KitHome
$getScript   = Join-Path $script:RepoRoot 'install\get.ps1'
$repo        = $script:RepoRoot
$dir         = Join-Path $base 'claude-base'
$stubs       = Join-Path $base 'stubs'
$wingetLog   = Join-Path $base 'winget-calls.txt'
$claudeMark  = Join-Path $base 'claude-installer-ran.txt'
$herdrLog    = Join-Path $base 'herdr-calls.txt'
New-Item -ItemType Directory -Force -Path $stubs | Out-Null
Set-Content -LiteralPath (Join-Path $stubs 'winget.cmd') -Encoding ascii -Value @('@echo off', ('echo %*>>"' + $wingetLog + '"'))
Set-Content -LiteralPath (Join-Path $stubs 'claude-installer.ps1') -Encoding ascii -Value ("Set-Content -LiteralPath '" + $claudeMark + "' -Value ran")
Set-Content -LiteralPath (Join-Path $stubs 'herdr.cmd') -Encoding ascii -Value @('@echo off', ('echo %* ^|%CLAUDE_CONFIG_DIR%>>"' + $herdrLog + '"'))
Set-Content -LiteralPath (Join-Path $stubs 'hotkey.ps1') -Encoding ascii -Value "'Created: stub hotkey'"
$env:Path = (($realPath -split ';') | Where-Object { $_ -and $_ -notmatch 'herdr' }) -join ';'
$env:KIT_HERDR_EXE        = Join-Path $stubs 'herdr.cmd'
$env:KIT_HERDR_HOTKEY     = Join-Path $stubs 'hotkey.ps1'
$env:KIT_HERDR_CONFIG_DIR = Join-Path $base 'appdata\herdr'
$env:KIT_HERDR_LNK_DIR    = Join-Path $base 'startmenu'
# Failing stand-ins stay set for the whole suite, so no phase can reach the real winget or the real
# Claude Code installer, whatever this machine is missing.
Set-Content -LiteralPath (Join-Path $stubs 'winget-fails.cmd') -Encoding ascii -Value @('@echo off', 'echo stub winget failing', 'exit /b 1')
Set-Content -LiteralPath (Join-Path $stubs 'claude-installer-fails.ps1') -Encoding ascii -Value "'stub claude installer failing'; exit 1"
Set-Content -LiteralPath (Join-Path $stubs 'npm-fails.cmd') -Encoding ascii -Value @('@echo off', 'echo stub npm failing', 'exit /b 1')
$npmLog = Join-Path $base 'npm-calls.txt'
Set-Content -LiteralPath (Join-Path $stubs 'npm.cmd') -Encoding ascii -Value @('@echo off', ('echo %*>>"' + $npmLog + '"'))
# The user PATH lives in a throwaway key under HKCU for the whole suite, removed at the end.
$envKeyRoot = "Software\claude-base-test-$PID"
$env:CLAUDE_BASE_ENV_KEY = "$envKeyRoot\Environment"
$envKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($env:CLAUDE_BASE_ENV_KEY)
$envKey.SetValue('Path', '%USERPROFILE%\keep-me;C:\other', [Microsoft.Win32.RegistryValueKind]::ExpandString)
$envKey.Close()
function Read-TestUserPath {
    $k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($env:CLAUDE_BASE_ENV_KEY)
    try { return @{ Raw = [string]$k.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames); Kind = [string]$k.GetValueKind('Path') } }
    finally { $k.Close() }
}

function Invoke-Get {
    param([string[]]$Arguments = @())
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $text = ((& powershell -NoProfile -ExecutionPolicy Bypass -File $getScript -Repo $repo @Arguments 2>&1) | Out-String)
        $script:LastExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
    return $text
}

function Clear-GetEnv {
    foreach ($name in @('CLAUDE_BASE_MISSING', 'CLAUDE_BASE_ANSWER')) { Set-Item -Path "Env:$name" -Value $null }
    $env:CLAUDE_BASE_WINGET = Join-Path $stubs 'winget-fails.cmd'
    $env:CLAUDE_BASE_CLAUDE_INSTALLER = Join-Path $stubs 'claude-installer-fails.ps1'
    $env:CLAUDE_BASE_NPM = Join-Path $stubs 'npm-fails.cmd'
}

try {
    Write-Host "`r`nphase 1, a dry run with no clone checks the tools and installs nothing"
    Clear-GetEnv
    $out = Invoke-Get @('-Dir', $dir, '-DryRun')
    Assert-Exit 0 'the dry run succeeds'
    Assert-Match $out '  ok    Git for Windows' 'git is checked'
    Assert-Match $out '  ok    Python 3' 'python is checked'
    Assert-Match $out '  ok    Claude Code' 'claude is checked'
    Assert-Match $out "would clone $repo into $dir" 'it says it would clone'
    Assert-True (-not (Test-Path -LiteralPath $dir)) 'nothing was cloned'

    Write-Host "`r`nphase 2, a real run clones, installs, skips Herdr on -NoHerdr and runs the doctor"
    $out = Invoke-Get @('-Dir', $dir, '-NoHerdr')
    Assert-Exit 0 'the install succeeds'
    Assert-True (Test-Path -LiteralPath (Join-Path $dir '.git')) 'the kit was cloned'
    Assert-True ($out -notmatch 'RemoteException') 'git progress is not shown as an error record'
    Assert-True (Test-Path -LiteralPath (Join-Path $dir 'install\get.ps1')) 'the clone carries this branch'
    Assert-True (Test-Path -LiteralPath (Join-Path $kitHome 'skills')) 'the kit landed in the kit home'
    Assert-Match $out 'herdr        not added.' 'Herdr was not added'
    Assert-Regex $out '(?m)^(ok|warn|FAIL) +git' 'the doctor ran'
    Assert-Match $out 'Left to do by hand:' 'it ends with the steps left by hand'

    Write-Host "`r`nphase 3, a second run updates the clone it made"
    $out = Invoke-Get @('-Dir', $dir, '-NoHerdr')
    Assert-Exit 0 'the second run succeeds'
    Assert-Match $out "updating $dir" 'it pulled the clone it made'

    Write-Host "`r`nphase 4, a dry run over the clone runs install.ps1 -DryRun"
    $out = Invoke-Get @('-Dir', $dir, '-DryRun')
    Assert-Exit 0 'the dry run over the clone succeeds'
    Assert-Match $out 'plan (dry run, nothing is written):' 'install.ps1 ran as a dry run'
    Assert-Match $out 'a real run asks "Add Herdr? [Y/n]"' 'the Herdr question is shown, not asked'

    Write-Host "`r`nphase 5, a folder that is not a clone, or a clone of another repository, is refused"
    $other = Join-Path $base 'not-a-clone'
    New-Item -ItemType Directory -Force -Path $other | Out-Null
    Set-Content -LiteralPath (Join-Path $other 'notes.txt') -Value 'mine' -Encoding ascii
    $out = Invoke-Get @('-Dir', $other, '-NoHerdr')
    Assert-Exit 1 'a folder of yours is refused'
    Assert-Match $out 'is not a claude-base clone' 'it says why'
    Assert-True ((Get-Content -LiteralPath (Join-Path $other 'notes.txt') -Raw).Trim() -eq 'mine') 'your folder is untouched'
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $text = ((& powershell -NoProfile -ExecutionPolicy Bypass -File $getScript -Repo 'https://example.org/other.git' -Dir $dir -NoHerdr 2>&1) | Out-String)
    $script:LastExit = $LASTEXITCODE; $ErrorActionPreference = $previous
    Assert-Exit 1 'a clone of another origin is refused'
    Assert-Match $text 'Pass -Dir to use another folder' 'it names the way out'

    Write-Host "`r`nphase 6, a missing tool is offered through winget; no means stop"
    $env:CLAUDE_BASE_WINGET = Join-Path $stubs 'winget.cmd'
    $env:CLAUDE_BASE_MISSING = 'python'
    $env:CLAUDE_BASE_ANSWER = 'n'
    $out = Invoke-Get @('-Dir', (Join-Path $base 'cb2'), '-NoHerdr')
    Assert-Exit 1 'declining a required tool stops the run'
    Assert-Match $out 'Stopped: Python 3 is required. Nothing of the kit was installed.' 'it says nothing was installed'
    Assert-True (-not (Test-Path -LiteralPath $wingetLog)) 'winget was not called'
    $env:CLAUDE_BASE_ANSWER = 'y'
    $out = Invoke-Get @('-Dir', (Join-Path $base 'cb2'), '-NoHerdr')
    $calls = if (Test-Path -LiteralPath $wingetLog) { Get-Content -LiteralPath $wingetLog -Raw } else { '' }
    Assert-Match $calls 'install --id Python.Python.3.12 --exact --source winget' 'yes calls winget for Python'
    Assert-Match $out 'cannot see it yet' 'a tool still missing after its install asks for a new window'
    Assert-Exit 1 'and the run stops there'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $base 'cb2'))) 'nothing was cloned'

    Remove-Item -LiteralPath $wingetLog
    $env:CLAUDE_BASE_WINGET = Join-Path $stubs 'winget-fails.cmd'
    $out = Invoke-Get @('-Dir', (Join-Path $base 'cb2'), '-NoHerdr')
    Assert-Exit 1 'a failing winget stops the run'
    Assert-Match $out 'the Python 3 installer exited 1' 'it reports the winget exit'

    Write-Host "`r`nphase 6b, a folder with a space, and both Herdr switches"
    Clear-GetEnv
    $spaced = Join-Path $base 'my kit'
    $out = Invoke-Get @('-Dir', $spaced, '-NoHerdr')
    Assert-Exit 0 'a folder with a space installs'
    Assert-True (Test-Path -LiteralPath (Join-Path $spaced 'install.ps1')) 'the clone is in the folder with a space'
    $out = Invoke-Get @('-Dir', $spaced, '-Herdr', '-NoHerdr')
    Assert-Exit 1 'both Herdr switches are refused'
    Assert-Match $out 'cannot both be given' 'it says why'

    Write-Host "`r`nphase 7, a missing Claude Code is offered through its official installer when there is no npm"
    $env:CLAUDE_BASE_MISSING = 'claude'
    $env:CLAUDE_BASE_ANSWER = 'y'
    $env:CLAUDE_BASE_NPM = 'none'
    $env:CLAUDE_BASE_CLAUDE_INSTALLER = Join-Path $stubs 'claude-installer.ps1'
    $out = Invoke-Get @('-Dir', (Join-Path $base 'cb3'), '-NoHerdr')
    Assert-True (Test-Path -LiteralPath $claudeMark) 'the Claude Code installer ran'
    Assert-Match $out 'Install it with its official installer' 'the question names the installer'

    Write-Host "`r`nphase 7a, with npm on PATH a missing Claude Code is offered through npm"
    Remove-Item -LiteralPath $claudeMark
    $env:CLAUDE_BASE_NPM = Join-Path $stubs 'npm.cmd'
    $out = Invoke-Get @('-Dir', (Join-Path $base 'cb3'), '-DryRun')
    Assert-Match $out 'would offer to install Claude Code with npm (npm install -g @anthropic-ai/claude-code)' 'the dry run names the npm route'
    Assert-True (-not (Test-Path -LiteralPath $npmLog)) 'the dry run did not call npm'
    $out = Invoke-Get @('-Dir', (Join-Path $base 'cb3'), '-NoHerdr')
    Assert-Match $out 'Install it with npm' 'the question names npm'
    Assert-Match ((Get-Content -LiteralPath $npmLog -Raw)) 'install -g @anthropic-ai/claude-code' 'yes runs npm install -g'
    Assert-True (-not (Test-Path -LiteralPath $claudeMark)) 'the official installer did not run'
    Assert-Match $out 'cannot see it yet' 'a claude still missing after npm asks for a new window'
    Remove-Item -LiteralPath $npmLog
    $env:CLAUDE_BASE_ANSWER = 'n'
    $out = Invoke-Get @('-Dir', (Join-Path $base 'cb3'), '-NoHerdr')
    Assert-Exit 1 'declining Claude Code stops the run'
    Assert-True (-not (Test-Path -LiteralPath $npmLog)) 'no means npm is not called'
    Clear-GetEnv
    $out = Invoke-Get @('-Dir', (Join-Path $base 'cb3'), '-DryRun')
    Assert-Match $out '  ok    Claude Code' 'a Claude Code already installed is left as it is'
    Assert-True ($out -notmatch 'offer to install Claude Code') 'nothing is offered for it'

    Write-Host "`r`nphase 7b, a claude.exe the installer left off PATH is found and its folder added"
    Clear-GetEnv
    $bin = Join-Path $env:USERPROFILE '.local\bin'
    New-Item -ItemType Directory -Force -Path $bin | Out-Null
    Set-Content -LiteralPath (Join-Path $bin 'claude.exe') -Value 'stand-in' -Encoding ascii
    $savedPath = $env:Path
    $env:Path = (($env:Path -split ';') | Where-Object { $_ -and -not (Test-Path -LiteralPath (Join-Path $_ 'claude.cmd')) -and -not (Test-Path -LiteralPath (Join-Path $_ 'claude.exe')) -and -not (Test-Path -LiteralPath (Join-Path $_ 'claude')) }) -join ';'
    try {
        $out = Invoke-Get @('-Dir', (Join-Path $base 'cb4'), '-DryRun')
        Assert-Exit 0 'the dry run with claude only in .local\bin succeeds'
        Assert-Match $out '  ok    Claude Code' 'claude is found in .local\bin'
        Assert-Match $out "would add $bin to your user PATH" 'the dry run only says it would add the folder'
        $reg = Read-TestUserPath
        Assert-True ($reg.Raw -eq '%USERPROFILE%\keep-me;C:\other') 'the dry run left the user PATH as it was'
        # a real run up to the folder refusal of phase 5: the tool check runs, nothing is cloned
        $out = Invoke-Get @('-Dir', $other, '-NoHerdr')
        Assert-Exit 1 'the run stops at the folder that is not a clone'
        Assert-Match $out "added $bin to your user PATH" 'the real run adds the folder'
        $reg = Read-TestUserPath
        Assert-True ($reg.Raw -eq ('%USERPROFILE%\keep-me;C:\other;' + $bin)) 'the user PATH keeps its %USERPROFILE% entry unexpanded'
        Assert-True ($reg.Kind -eq 'ExpandString') 'the user PATH stays REG_EXPAND_SZ'
        $out = Invoke-Get @('-Dir', $other, '-NoHerdr')
        Assert-True ($out -notmatch 'added .* to your user PATH') 'a second run adds nothing'
        Assert-True ((Read-TestUserPath).Raw -eq $reg.Raw) 'the user PATH holds the folder once'
    } finally {
        $env:Path = $savedPath
        Remove-Item -LiteralPath (Join-Path $bin 'claude.exe')
    }

    Write-Host "`r`nphase 8, the Herdr question: n passes -NoHerdr, y and -Yes pass -Herdr"
    Clear-GetEnv
    $env:CLAUDE_BASE_ANSWER = 'n'
    $out = Invoke-Get @('-Dir', $dir)
    Assert-Exit 0 'the run answered n succeeds'
    Assert-Match $out 'herdr        not added.' 'n means no Herdr'
    $env:CLAUDE_BASE_ANSWER = 'y'
    $out = Invoke-Get @('-Dir', $dir)
    Assert-Exit 0 'the run answered y succeeds'
    Assert-Match ((Get-Content -LiteralPath $herdrLog -Raw)) 'integration install claude' 'y sets Herdr up (stub)'
    Remove-Item -LiteralPath $herdrLog
    $env:CLAUDE_BASE_ANSWER = ' '  # Enter: an empty value would unset the variable
    $out = Invoke-Get @('-Dir', $dir)
    Assert-Exit 0 'the run answered with Enter succeeds'
    Assert-True (Test-Path -LiteralPath $herdrLog) 'Enter means yes (stub)'
    Clear-GetEnv
    Remove-Item -LiteralPath $herdrLog
    $out = Invoke-Get @('-Dir', $dir, '-Yes')
    Assert-Exit 0 'the run with -Yes succeeds'
    Assert-True (Test-Path -LiteralPath $herdrLog) '-Yes sets Herdr up (stub)'

    Write-Host "`r`nphase 9, run as a script block or dot-sourced, a failing run never ends the caller"
    $caller = Join-Path $base 'caller.ps1'
    Set-Content -LiteralPath $caller -Encoding ascii -Value @(
        'param([string]$Get, [string]$Repo, [string]$Dir)',
        '& ([scriptblock]::Create((Get-Content -LiteralPath $Get -Raw))) -Repo $Repo -Dir $Dir -Herdr -NoHerdr',
        "'after the block, exit ' + `$LASTEXITCODE",
        '. $Get -Repo $Repo -Dir $Dir -Herdr -NoHerdr',
        "'after the dot-source, exit ' + `$LASTEXITCODE",
        'exit 7')
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $text = ((& powershell -NoProfile -ExecutionPolicy Bypass -File $caller -Get $getScript -Repo $repo -Dir (Join-Path $base 'cb5') 2>&1) | Out-String)
    $script:LastExit = $LASTEXITCODE; $ErrorActionPreference = $previous
    Assert-Match $text 'after the block, exit 1' 'the script block form returns to its caller with the exit code'
    Assert-Match $text 'after the dot-source, exit 1' 'the dot-sourced form returns to its caller with the exit code'
    Assert-Exit 7 'the caller ends on its own exit'
    # the one-liner itself, iex inside a caller script: there MyCommand.Path names the caller
    Set-Content -LiteralPath $caller -Encoding ascii -Value @(
        'param([string]$Get, [string]$Repo, [string]$Dir)',
        '$env:CLAUDE_BASE_REPO = $Repo; $env:CLAUDE_BASE_DIR = $Dir',
        '$env:CLAUDE_BASE_MISSING = "git"; $env:CLAUDE_BASE_ANSWER = "n"',
        'Get-Content -LiteralPath $Get -Raw | Invoke-Expression',
        "'after iex, exit ' + `$LASTEXITCODE",
        'exit 7')
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $text = ((& powershell -NoProfile -ExecutionPolicy Bypass -File $caller -Get $getScript -Repo $repo -Dir (Join-Path $base 'cb5') 2>&1) | Out-String)
    $script:LastExit = $LASTEXITCODE; $ErrorActionPreference = $previous
    Assert-Match $text 'after iex, exit 1' 'iex inside a script returns to it with the exit code'
    Assert-Exit 7 'and the script ends on its own exit'
    # run as a file under another name, the exit code still reaches the caller
    $renamed = Join-Path $base 'bootstrap.ps1'
    Copy-Item -LiteralPath $getScript -Destination $renamed
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $text = ((& powershell -NoProfile -ExecutionPolicy Bypass -File $renamed -Repo $repo -Dir (Join-Path $base 'cb5') -Herdr -NoHerdr 2>&1) | Out-String)
    $script:LastExit = $LASTEXITCODE; $ErrorActionPreference = $previous
    Assert-Exit 1 'a renamed copy run as a file passes its exit code'
} finally {
    Clear-GetEnv
    foreach ($name in @('KIT_HERDR_EXE', 'KIT_HERDR_HOTKEY', 'KIT_HERDR_CONFIG_DIR', 'KIT_HERDR_LNK_DIR', 'CLAUDE_BASE_WINGET', 'CLAUDE_BASE_CLAUDE_INSTALLER', 'CLAUDE_BASE_NPM', 'CLAUDE_BASE_ENV_KEY')) { Set-Item -Path "Env:$name" -Value $null }
    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($envKeyRoot, $false)
    $env:Path = $realPath
    Close-KitSandbox $realProfile
}

Write-Host ("`r`n{0} passed, {1} failed" -f $script:passed, $script:failed)
if ($script:failed -gt 0) { exit 1 }
exit 0
