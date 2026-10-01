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
    foreach ($name in @('CLAUDE_BASE_MISSING', 'CLAUDE_BASE_ANSWER', 'CLAUDE_BASE_WINGET', 'CLAUDE_BASE_CLAUDE_INSTALLER')) {
        Set-Item -Path "Env:$name" -Value $null
    }
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

    Write-Host "`r`nphase 7, a missing Claude Code is offered through its official installer"
    $env:CLAUDE_BASE_MISSING = 'claude'
    $env:CLAUDE_BASE_CLAUDE_INSTALLER = Join-Path $stubs 'claude-installer.ps1'
    $null = Invoke-Get @('-Dir', (Join-Path $base 'cb3'), '-NoHerdr')
    Assert-True (Test-Path -LiteralPath $claudeMark) 'the Claude Code installer ran'

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
    Clear-GetEnv
    Remove-Item -LiteralPath $herdrLog
    $out = Invoke-Get @('-Dir', $dir, '-Yes')
    Assert-Exit 0 'the run with -Yes succeeds'
    Assert-True (Test-Path -LiteralPath $herdrLog) '-Yes sets Herdr up (stub)'
} finally {
    Clear-GetEnv
    foreach ($name in @('KIT_HERDR_EXE', 'KIT_HERDR_HOTKEY', 'KIT_HERDR_CONFIG_DIR')) { Set-Item -Path "Env:$name" -Value $null }
    $env:Path = $realPath
    Close-KitSandbox $realProfile
}

Write-Host ("`r`n{0} passed, {1} failed" -f $script:passed, $script:failed)
if ($script:failed -gt 0) { exit 1 }
exit 0
