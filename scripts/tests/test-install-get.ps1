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
# The npm stand-in answers prefix -g with NPM_STUB_PREFIX and, when NPM_STUB_WRITES is set, an install
# puts a stand-in claude.cmd there, as npm install -g does.
Set-Content -LiteralPath (Join-Path $stubs 'claude-stub.cmd') -Encoding ascii -Value @('@echo off', 'echo stub claude')
$npmStub = @('@echo off', ('echo %*>>"' + $npmLog + '"'),
    'if "%1 %2"=="prefix -g" goto prefix',
    ('if "%1"=="install" if defined NPM_STUB_WRITES copy /y "' + (Join-Path $stubs 'claude-stub.cmd') + '" "%NPM_STUB_PREFIX%\claude.cmd" >nul'),
    'exit /b 0', ':prefix', 'if defined NPM_STUB_WARN echo npm WARN config something', 'echo %NPM_STUB_PREFIX%', 'exit /b 0')
Set-Content -LiteralPath (Join-Path $stubs 'npm.cmd') -Encoding ascii -Value $npmStub
function Get-NoClaudePath {
    # This session's PATH without any folder holding a claude or an npm, so no real one is reachable.
    return (($env:Path -split ';') | Where-Object { $dir = $_; $dir -and -not (@('claude.cmd', 'claude.exe', 'claude.ps1', 'claude', 'npm.cmd', 'npm.ps1', 'npm') | Where-Object { Test-Path -LiteralPath (Join-Path $dir $_) }) }) -join ';'
}
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
    # a claude from npm: its claude.ps1 resolves first, so the last steps name claude.cmd
    $shims = Join-Path $base 'npm-shims'
    New-Item -ItemType Directory -Force -Path $shims | Out-Null
    Copy-Item -LiteralPath (Join-Path $stubs 'claude-stub.cmd') -Destination (Join-Path $shims 'claude.cmd')
    Set-Content -LiteralPath (Join-Path $shims 'claude.ps1') -Encoding ascii -Value "'stub claude ps1'"
    $savedPath = $env:Path
    $env:Path = "$shims;$env:Path"
    try { $out = Invoke-Get @('-Dir', $dir, '-NoHerdr') } finally { $env:Path = $savedPath }
    Assert-Match $out 'run claude.cmd, then sign in' 'the last steps name claude.cmd beside an npm claude.ps1'
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

    Write-Host "`r`nphase 6c, a Python just installed wins over the Store stub already in the session"
    Clear-GetEnv
    $silent = Join-Path $base 'store-stub'
    $fresh = Join-Path $base 'new-python'
    New-Item -ItemType Directory -Force -Path $silent, $fresh | Out-Null
    Set-Content -LiteralPath (Join-Path $silent 'python.cmd') -Encoding ascii -Value '@echo off'
    Set-Content -LiteralPath (Join-Path $fresh 'python.cmd') -Encoding ascii -Value @('@echo off', 'echo Python 3.12.0')
    Set-Content -LiteralPath (Join-Path $stubs 'winget-adds-python.cmd') -Encoding ascii -Value @('@echo off',
        ('reg add "HKCU\' + $env:CLAUDE_BASE_ENV_KEY + '" /v Path /t REG_EXPAND_SZ /d "' + $fresh + ';C:\other" /f >nul'))
    $env:CLAUDE_BASE_WINGET = Join-Path $stubs 'winget-adds-python.cmd'
    $env:CLAUDE_BASE_ANSWER = 'y'
    $savedPath = $env:Path
    $env:Path = "$silent;" + ((($env:Path -split ';') | Where-Object { $pathDir = $_; $pathDir -and -not (@('python.exe', 'py.exe', 'python.cmd') | Where-Object { Test-Path -LiteralPath (Join-Path $pathDir $_) }) }) -join ';')
    try { $out = Invoke-Get @('-Dir', $other, '-NoHerdr') } finally {
        $env:Path = $savedPath
        $k = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($env:CLAUDE_BASE_ENV_KEY)
        $k.SetValue('Path', '%USERPROFILE%\keep-me;C:\other', [Microsoft.Win32.RegistryValueKind]::ExpandString); $k.Close()
    }
    Assert-True ($out -notmatch 'cannot see it yet') 'the new Python is seen in this window'
    Assert-Match $out 'is not a claude-base clone' 'the run went on past the tools'

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
    $onPath = Join-Path $base 'claude-on-path'
    New-Item -ItemType Directory -Force -Path $onPath | Out-Null
    Copy-Item -LiteralPath (Join-Path $stubs 'claude-stub.cmd') -Destination (Join-Path $onPath 'claude.cmd')
    $savedPath = $env:Path
    $env:Path = "$onPath;" + (Get-NoClaudePath)
    try { $out = Invoke-Get @('-Dir', (Join-Path $base 'cb3'), '-DryRun') } finally { $env:Path = $savedPath }
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
        $k = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($env:CLAUDE_BASE_ENV_KEY)
        $k.SetValue('Path', ('%USERPROFILE%\keep-me;' + $bin + '\'), [Microsoft.Win32.RegistryValueKind]::ExpandString); $k.Close()
        $out = Invoke-Get @('-Dir', $other, '-NoHerdr')
        Assert-True ($out -notmatch 'added .* to your user PATH') 'the folder written with a trailing backslash is recognised'
    } finally {
        $env:Path = $savedPath
        Remove-Item -LiteralPath (Join-Path $bin 'claude.exe')
    }

    Write-Host "`r`nphase 7c, npm in a folder only the session has (fnm): the folder is kept, found by npm.cmd"
    Clear-GetEnv
    $sessionOnly = Join-Path $base 'fnm-session'
    New-Item -ItemType Directory -Force -Path $sessionOnly | Out-Null
    Set-Content -LiteralPath (Join-Path $sessionOnly 'npm.cmd') -Encoding ascii -Value $npmStub
    Set-Content -LiteralPath (Join-Path $sessionOnly 'npm.ps1') -Encoding ascii -Value ("Add-Content -LiteralPath '" + $npmLog + "' -Value 'npm.ps1 ran'")
    $env:CLAUDE_BASE_NPM = $null
    $env:CLAUDE_BASE_ANSWER = 'y'
    $env:NPM_STUB_WRITES = '1'
    $env:NPM_STUB_PREFIX = $sessionOnly
    $before = (Read-TestUserPath).Raw
    $savedPath = $env:Path
    $env:Path = "$sessionOnly;" + (Get-NoClaudePath)
    try { $out = Invoke-Get @('-Dir', $other, '-NoHerdr') } finally { $env:Path = $savedPath }
    $calls = if (Test-Path -LiteralPath $npmLog) { Get-Content -LiteralPath $npmLog -Raw } else { '' }
    Assert-Match $calls 'install -g @anthropic-ai/claude-code' 'the npm on PATH ran the install'
    Assert-True ($calls -notmatch 'npm.ps1 ran') 'npm.cmd ran, never npm.ps1'
    Assert-True ($out -notmatch 'cannot see it yet') 'the session still sees the folder after the install'
    Assert-Match $out '  ok    Claude Code' 'claude is found where npm put it'
    Assert-Match $out 'is not a claude-base clone' 'the run went on to the folder check'
    Assert-True ((Read-TestUserPath).Raw -eq $before) 'a folder the session has is not added to the user PATH'
    Remove-Item -LiteralPath $npmLog

    Write-Host "`r`nphase 7d, npm whose global folder is on no PATH: the folder is found and added"
    $npmPrefix = Join-Path $base 'npm-global'
    New-Item -ItemType Directory -Force -Path $npmPrefix | Out-Null
    $env:NPM_STUB_PREFIX = $npmPrefix
    $env:NPM_STUB_WARN = '1'  # a wrapper that prints a warning on stdout before the prefix
    $env:CLAUDE_BASE_NPM = Join-Path $stubs 'npm.cmd'
    $savedPath = $env:Path
    $env:Path = Get-NoClaudePath
    try { $out = Invoke-Get @('-Dir', $other, '-NoHerdr') } finally { $env:Path = $savedPath }
    Assert-Match $out "added $npmPrefix to your user PATH" 'the npm global folder is added'
    Assert-Match $out '  ok    Claude Code' 'claude is found in it'
    Assert-True ((Read-TestUserPath).Raw -eq ($before + ';' + $npmPrefix)) 'the user PATH gains that folder once'
    foreach ($name in @('NPM_STUB_WRITES', 'NPM_STUB_PREFIX', 'NPM_STUB_WARN')) { Set-Item -Path "Env:$name" -Value $null }
    Remove-Item -LiteralPath $npmLog

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
    Write-Host "`r`nphase 10, every child PowerShell starts by its full path, and a PATH that lost the WindowsPowerShell folder still installs"
    # get.ps1 also refreshes the session PATH from the registry (Update-GetSessionPath), which puts the folder back here,
    # so the run below passes on the old code too; the source checks are the pins of the full-path start.
    foreach ($file in @($getScript, (Join-Path $script:RepoRoot 'install/herdr.ps1'))) {
        Assert-True (-not (Select-String -LiteralPath $file -SimpleMatch -Pattern '& powershell ' -Quiet)) ("$(Split-Path -Leaf $file) starts no PowerShell by its PATH name")
    }
    Clear-GetEnv
    $ps = [IO.Path]::Combine($env:SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    $savedPath = $env:Path
    $env:Path = (($env:Path -split ';') | Where-Object { $_ -and $_ -notmatch 'WindowsPowerShell' }) -join ';'
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        $text = ((& $ps -NoProfile -ExecutionPolicy Bypass -File $getScript -Repo $repo -Dir (Join-Path $base 'cb6') -NoHerdr 2>&1) | Out-String)
        $script:LastExit = $LASTEXITCODE
    } finally { $env:Path = $savedPath; $ErrorActionPreference = $previous }
    Assert-Exit 0 'the install succeeds with no WindowsPowerShell folder on PATH'
    Assert-True ($text -notmatch 'Stopped: install.ps1') 'install.ps1 ran'
    Assert-Match $text 'Left to do by hand:' 'it reaches the last steps'
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
