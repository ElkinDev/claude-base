# The environment the launcher hands a session: the default cap, the research exception, the
# opt-in auto-compact window, and the flags it must forward to claude untouched (F: context
# economics).
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tests\test-launcher-env.ps1
#
# claude\claude-account.ps1 -ShowEnv prints what it would export and what it would forward, then
# exits without opening a session or touching a profile, which is the only way to assert this
# without launching Claude. Both routes are checked: the variables the in-window path sets, and
# the command string the tab and new-window paths run, since they are built separately and have
# drifted apart before.
#
# Every call below carries -ShowEnv, and never after `--`: past the separator the switch is
# forwarded to claude instead of binding, and the launcher would run for real and build a profile.

# The dry run prints CRLF lines, so every anchored pattern tolerates the carriage return.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'lib-install-test.ps1')

$launcher = Join-Path $script:RepoRoot 'claude\claude-account.ps1'

function Get-LauncherEnv {
    param([string[]]$LauncherArgs)
    $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $launcher @LauncherArgs 2>&1 | Out-String
    $script:LastExit = $LASTEXITCODE
    return $out
}

# Two routes for the forwarding cases, because how an argument is written decides how it binds.
#
# Get-LauncherLiteral takes no parameters of its own, so the tokens are written literally at the
# call site and reach powershell.exe as separate command-line words, which is what a flag typed in
# a shell is. Invoke-Wrapper is the shape a shell wrapper has: one positional parameter and $args
# for the rest, forwarded by splatting. PowerShell keeps the parameter token in $args, so `wrapper
# acct -c` still arrives at the launcher as the flag -c; an array of quoted strings built by hand
# would not, since splatted string literals arrive as values and every flag would land in $Extra
# whatever the param block says, and the test would pass while the launcher swallowed the flag.
# The wrapper parameter is named $First because no launcher flag is a prefix of it: a wrapper whose
# own parameter starts with a letter a flag prefixes swallows that flag before the launcher sees
# it, which is why the role is passed here as -Role and never as -o.
function Get-LauncherLiteral {
    # 'Continue' only inside this function: a rejected argument makes powershell.exe write to
    # stderr, and under 'Stop' the merged error record would end the suite instead of being read.
    $ErrorActionPreference = 'Continue'
    $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $launcher @args 2>&1 | Out-String
    $script:LastExit = $LASTEXITCODE
    return $out
}

function Invoke-Wrapper {
    param([string]$First)
    $forward = @()
    if ($First) { $forward += $First }
    if ($args) { $forward += $args }
    $script:LastExit = 0
    try { $out = & $launcher @forward 2>&1 | Out-String }
    catch { $script:LastExit = 1; $out = $_.Exception.Message }
    return $out
}

# The in-window path is a function inside the launcher and -ShowEnv exits before it runs, so the
# function and the line that decides the cap are lifted out by name and run in a child process
# that inherits whatever window this one has. The pane command and this path are built separately
# and have drifted apart before, which is why both are asserted instead of one standing for both.
function Invoke-InWindowRole {
    param([string]$RoleName, [int]$WindowValue)
    $text = Get-Content -LiteralPath $launcher -Raw
    $capLine = ([regex]::Match($text, '(?m)^\$capContext = .+$')).Value
    $applyRole = ([regex]::Match($text, '(?ms)^function Apply-Role \{.*?^\}')).Value
    if (($capLine -eq '') -or ($applyRole -eq '')) { throw 'the launcher no longer holds the in-window path this test lifts' }
    $probe = Join-Path $env:TEMP ('launcher-env-' + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.ps1')
    $body = @(
        ("`$Role = '" + $RoleName + "'"),
        ("`$Window = " + $WindowValue),
        $capLine,
        $applyRole,
        'Apply-Role',
        'Write-Output ("CLAUDE_ROLE=" + $env:CLAUDE_ROLE)',
        'Write-Output ("CLAUDE_CODE_DISABLE_1M_CONTEXT=" + $env:CLAUDE_CODE_DISABLE_1M_CONTEXT)',
        'Write-Output ("CLAUDE_CODE_AUTO_COMPACT_WINDOW=" + $env:CLAUDE_CODE_AUTO_COMPACT_WINDOW)'
    )
    Set-Content -LiteralPath $probe -Value $body -Encoding ASCII
    try { $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $probe 2>&1 | Out-String }
    finally { Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue }
    return $out
}

function Assert-Forwarded {
    param([string]$Out, [string]$Extra, [string]$What)
    Assert-Match $Out ("EXTRA=" + $Extra) ("${What}: the flags reach claude")
    Assert-Regex $Out '(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=\(removed\)\r?$' "${What}: the window is not turned on"
    Assert-Regex $Out '(?m)^CLAUDE_CODE_DISABLE_1M_CONTEXT=1\r?$' "${What}: the cap still applies"
}

Assert-True (Test-Path -LiteralPath $launcher) 'the launcher is where the tests expect it'

Write-Host "`r`nphase 1, the default is the 200k cap and no window override"
$out = Get-LauncherEnv @('-ShowEnv')
Assert-Exit 0 'the dry run exits clean'
Assert-Regex $out '(?m)^CLAUDE_ROLE=lane\r?$' 'the default role is lane'
Assert-Regex $out '(?m)^CLAUDE_CODE_DISABLE_1M_CONTEXT=1\r?$' 'the context stays capped at 200k'
Assert-Regex $out '(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=\(removed\)\r?$' 'the window variable is cleared, not inherited'
Assert-Regex $out '(?m)^EXTRA=\r?$' 'nothing is forwarded when nothing was passed'
Assert-Match $out "`$env:CLAUDE_CODE_DISABLE_1M_CONTEXT = '1'" 'the pane command caps the context too'
Assert-Match $out 'Remove-Item Env:\CLAUDE_CODE_AUTO_COMPACT_WINDOW' 'the pane command clears the window too'
Assert-True (-not ($out -match "PANE_COMMAND=.*AUTO_COMPACT_WINDOW = '")) 'the pane command sets no window'

Write-Host "`r`nphase 2, an orchestrator is capped the same way"
$out = Get-LauncherEnv @('-ShowEnv', '-Role', 'orchestrator')
Assert-Exit 0 'the dry run exits clean'
Assert-Regex $out '(?m)^CLAUDE_ROLE=orchestrator\r?$' 'the role travels to the session'
Assert-Regex $out '(?m)^CLAUDE_CODE_DISABLE_1M_CONTEXT=1\r?$' 'the orchestrator is capped'
Assert-Match $out 'EXTRA=--name orchestrator' 'an explicit role also names the session'

Write-Host "`r`nphase 3, research is the uncapped role and still sets no window"
$out = Get-LauncherEnv @('-ShowEnv', '-Role', 'research')
Assert-Exit 0 'the dry run exits clean'
Assert-Regex $out '(?m)^CLAUDE_CODE_DISABLE_1M_CONTEXT=\(unset\)\r?$' 'research runs uncapped'
Assert-Regex $out '(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=\(unchanged\)\r?$' 'research sets no window either'

Write-Host "`r`nphase 4, -Window drops the cap and names the window"
$out = Get-LauncherEnv @('-ShowEnv', '-Window', '230000')
Assert-Exit 0 'the dry run exits clean'
Assert-Regex $out '(?m)^CLAUDE_CODE_DISABLE_1M_CONTEXT=\(unset\)\r?$' 'the cap is dropped, or the window could not grow'
Assert-Regex $out '(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=230000\r?$' 'auto-compaction fires at the window given'
Assert-Match $out "`$env:CLAUDE_CODE_AUTO_COMPACT_WINDOW = '230000'" 'the pane command carries the window'
Assert-True (-not ($out -match "PANE_COMMAND=.*CLAUDE_CODE_DISABLE_1M_CONTEXT = '1'")) 'the pane command drops the cap'

Write-Host "`r`nphase 5, the switch is opt-in: zero is off"
$out = Get-LauncherEnv @('-ShowEnv', '-Window', '0')
Assert-Exit 0 'the dry run exits clean'
Assert-Regex $out '(?m)^CLAUDE_CODE_DISABLE_1M_CONTEXT=1\r?$' 'zero leaves the default in place'
Assert-Regex $out '(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=\(removed\)\r?$' 'zero sets no window'

Write-Host "`r`nphase 6, the switch is documented where a user looks for it"
$out = Get-LauncherEnv @('-Help')
Assert-Exit 0 'the help exits clean'
Assert-Match $out '-Window <n>' 'the help lists the switch'
Assert-Match $out '-ShowEnv' 'the help lists the dry run'

# A parameter name here claims every unambiguous prefix of itself, and an unbound flag is handed to
# claude, so a name that prefixes one of claude's own single-letter flags (-c -d -h -n -p -r -v -w)
# would swallow it. -c is the one that still travels: -d -h -n -p -r -w are exact aliases of this
# script and an exact match beats a prefix, and -v binds the common parameter -Verbose. So `-c`
# and everything after it must arrive in EXTRA with the window and the cap untouched.
Write-Host "`r`nphase 7, an unbound flag is forwarded to claude, not swallowed"
Assert-Forwarded (Get-LauncherLiteral demo -ShowEnv -c) '-c' 'literal -c'
Assert-Forwarded (Invoke-Wrapper demo -ShowEnv -c) '-c' 'wrapper -c'
Assert-Forwarded (Get-LauncherLiteral demo -ShowEnv -c 1) '-c 1' 'literal -c with a number after it'
Assert-Forwarded (Invoke-Wrapper demo -ShowEnv -c 1) '-c 1' 'wrapper -c with a number after it'
Assert-Forwarded (Get-LauncherLiteral demo -ShowEnv -c --model opus) '-c --model opus' 'literal -c and a claude flag'
Assert-Forwarded (Invoke-Wrapper demo -ShowEnv -c --model opus) '-c --model opus' 'wrapper -c and a claude flag'
# `--` only survives the in-process route: powershell.exe -File eats the separator and the launcher
# then sees an empty parameter name, so the explicit form is asserted through the wrapper alone.
Assert-Forwarded (Invoke-Wrapper demo -ShowEnv -- -c) '-c' 'wrapper -- -c'
# -v never reaches claude, on this branch or before it: the [Parameter()] attributes make this an
# advanced script, so -v binds -Verbose by prefix. It is recorded here so the next reader does not
# read the launcher for a bug that lives in PowerShell.
$out = Get-LauncherLiteral demo -ShowEnv -v
Assert-Exit 0 '-v binds -Verbose and does not fail'
Assert-Regex $out '(?m)^EXTRA=\r?$' '-v is taken by -Verbose and never reaches claude'

Write-Host "`r`nphase 8, the window binds by prefix, and only inside its range"
$out = Get-LauncherLiteral demo -Wi 230000 -ShowEnv
Assert-Exit 0 'a prefix of the parameter name binds it'
Assert-Regex $out '(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=230000\r?$' '-Wi is an unambiguous prefix of -Window'
$out = Get-LauncherLiteral demo -w -ShowEnv
Assert-Exit 0 '-w is the exact alias of -Tab and does not reach -Window'
Assert-Regex $out '(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=\(removed\)\r?$' '-w names no window'
$out = Get-LauncherLiteral demo -Window -5 -ShowEnv
Assert-True ($script:LastExit -ne 0) 'a negative window is rejected'
Assert-True (-not ($out -match 'CLAUDE_ROLE=')) 'a rejected window opens nothing'
$out = Get-LauncherLiteral demo -Sh
Assert-Exit 0 '-Sh is an unambiguous prefix of -ShowEnv and is meant to be'
Assert-Regex $out '(?m)^CLAUDE_ROLE=lane\r?$' '-Sh prints the plan, which no claude flag needs'

# A pane opened from a pane that used the switch would otherwise inherit its window, silently and
# against the cap the role asks for, so the cap clears the variable on both paths and the dry run
# names what it found.
Write-Host "`r`nphase 9, an inherited window is cleared when the cap applies"
$env:CLAUDE_CODE_AUTO_COMPACT_WINDOW = '999999'
$out = Get-LauncherLiteral demo -ShowEnv
Assert-Exit 0 'the dry run exits clean with a window in the environment'
Assert-Regex $out '(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=\(removed, inherited 999999\)\r?$' 'the dry run names the window it would clear'
Assert-Match $out 'Remove-Item Env:\CLAUDE_CODE_AUTO_COMPACT_WINDOW' 'the pane command clears it'
$out = Get-LauncherLiteral demo -ShowEnv -Role research
Assert-Regex $out '(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=\(unchanged, inherited 999999\)\r?$' 'the uncapped role keeps it and says so'
$out = Get-LauncherLiteral demo -ShowEnv -Window 230000
Assert-Regex $out '(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=230000\r?$' 'the switch replaces it'
$out = Invoke-InWindowRole 'lane' 0
Assert-Regex $out '(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=\r?$' 'the in-window path clears it too'
Assert-Regex $out '(?m)^CLAUDE_CODE_DISABLE_1M_CONTEXT=1\r?$' 'and caps the context while it does'
$out = Invoke-InWindowRole 'lane' 230000
Assert-Regex $out '(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=230000\r?$' 'the in-window path sets the window asked for'
$out = Invoke-InWindowRole 'research' 0
Assert-Regex $out '(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=999999\r?$' 'the in-window path leaves research alone'
Remove-Item Env:\CLAUDE_CODE_AUTO_COMPACT_WINDOW -ErrorAction SilentlyContinue

Write-TestResult
