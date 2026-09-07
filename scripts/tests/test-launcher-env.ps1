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

# The suite asserts what the launcher does with an inherited window, so it has to start from a
# known environment. A pane that already carries CLAUDE_CODE_AUTO_COMPACT_WINDOW, which is what
# the account table gives the orchestrator and the analyst, turns every "(removed)" into
# "(removed, inherited <n>)" and fails eleven assertions that are not about inheritance at all.
# Phase 9 sets the variable on purpose and clears it again, so nothing is lost by clearing here.
Remove-Item Env:\CLAUDE_CODE_AUTO_COMPACT_WINDOW -ErrorAction SilentlyContinue

# The launcher reads its seats from CLAUDE_SEATS_DIR, so every phase runs against a temporary
# directory and no assertion depends on the seats this machine happens to have installed. It
# starts empty, because a role with a chair and no file is what the early phases must see; the
# seat phases write the file when they need it.
$seatsDir = Join-Path $env:TEMP ('launcher-seats-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $seatsDir -Force | Out-Null
$env:CLAUDE_SEATS_DIR = $seatsDir
$seatFile = Join-Path $seatsDir 'orchestrator.md'
$missingSeat = Join-Path $seatsDir 'analyst.md'
$defaultBriefs = Join-Path $env:USERPROFILE '.claude\briefs'
$startLine = 'Session start: read the newest resume brief of your seat, then the state sheet, then continue with its first actions.'
$refusal = 'A seated role never continues the most recent conversation of a folder (the profiles share it); resume with -r and the picker, or -r <id>.'

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
Assert-Regex $out '(?m)^EXTRA=--no-chrome\r?$' 'nothing is forwarded but the browser the role turns off'
Assert-Match $out "`$env:CLAUDE_CODE_DISABLE_1M_CONTEXT = '1'" 'the pane command caps the context too'
Assert-Match $out 'Remove-Item Env:\CLAUDE_CODE_AUTO_COMPACT_WINDOW' 'the pane command clears the window too'
Assert-True (-not ($out -match "PANE_COMMAND=.*AUTO_COMPACT_WINDOW = '")) 'the pane command sets no window'

Write-Host "`r`nphase 2, an orchestrator is capped the same way"
$out = Get-LauncherEnv @('-ShowEnv', '-Role', 'orchestrator')
Assert-Exit 0 'the dry run exits clean'
Assert-Regex $out '(?m)^CLAUDE_ROLE=orchestrator\r?$' 'the role travels to the session'
Assert-Regex $out '(?m)^CLAUDE_CODE_DISABLE_1M_CONTEXT=1\r?$' 'the orchestrator is capped'
Assert-Regex $out '(?m)^SEAT=none\r?$' 'the seats directory is empty here, so the session takes no chair'
Assert-Regex $out ('(?m)^EXTRA=--name orchestrator-\d{4}-\d{4} --no-chrome ' + [regex]::Escape($startLine) + '\r?$') 'an explicit role names the session with the minute it started, and adds nothing else'

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
Assert-Regex $out '(?m)^EXTRA=--no-chrome\r?$' '-v is taken by -Verbose and never reaches claude'

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


# --- the seat ----------------------------------------------------------------------
# A seat is a plain markdown file naming who the session is. The launcher appends it with
# --append-system-prompt-file, which leaves the default prompt whole, and it appends it only
# for a role that has a chair: orchestrator and analyst. The temporary seats directory has been
# empty until here, which is what the phases above asserted; from here it holds an orchestrator
# seat, and never an analyst one, so both cases are real.
Set-Content -LiteralPath $seatFile -Value '# Seat: orchestrator' -Encoding ASCII
Remove-Item Env:\CLAUDE_BRIEFS_DIR -ErrorAction SilentlyContinue
Remove-Item Env:\CLAUDE_CLOSING_HOUR -ErrorAction SilentlyContinue

# The applied environment is a function inside the launcher and -ShowEnv exits before it runs,
# so the seat plan lines and Apply-Role are lifted by name and run in a child process, the way
# phase 9 lifts the cap. The plan string and the function are built separately and have drifted
# apart before, which is why the seat variables are asserted on both.
function Invoke-InWindowSeat {
    param([string]$RoleName)
    $text = Get-Content -LiteralPath $launcher -Raw
    $lifted = @()
    foreach ($anchor in @('^\$isSeat = .+$', '^\$briefsDir = .+$', '^\$closingHour = .+$', '^\$capContext = .+$')) {
        $line = ([regex]::Match($text, '(?m)' + $anchor)).Value
        if ($line -eq '') { throw "the launcher no longer holds the line this test lifts: $anchor" }
        $lifted += $line
    }
    $applyRole = ([regex]::Match($text, '(?ms)^function Apply-Role \{.*?^\}')).Value
    if ($applyRole -eq '') { throw 'the launcher no longer holds the in-window path this test lifts' }
    $probe = Join-Path $env:TEMP ('launcher-seat-' + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.ps1')
    $body = @(("`$Role = '" + $RoleName + "'"), '$Window = 0') + $lifted + @(
        $applyRole,
        'Apply-Role',
        'Write-Output ("CLAUDE_BRIEFS_DIR=" + $env:CLAUDE_BRIEFS_DIR)',
        'Write-Output ("CLAUDE_CLOSING_HOUR=" + $env:CLAUDE_CLOSING_HOUR)'
    )
    Set-Content -LiteralPath $probe -Value $body -Encoding ASCII
    try { $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $probe 2>&1 | Out-String }
    finally { Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue }
    return $out
}

Write-Host "`r`nphase 10, a seated role opens with its seat appended, named and started"
$out = Get-LauncherLiteral demo -ShowEnv -Role orchestrator
Assert-Exit 0 'the dry run exits clean'
Assert-Regex $out ('(?m)^SEAT=' + [regex]::Escape($seatFile) + '\r?$') 'the plan names the seat file it found'
Assert-Match $out ('--append-system-prompt-file ' + $seatFile) 'the seat reaches claude as an appended prompt file'
Assert-Regex $out '(?m)^FRESH=true\r?$' 'a launch with no resume flag is fresh'
Assert-Regex $out ('(?m)^START=' + [regex]::Escape($startLine) + '\r?$') 'the plan names the start line'
Assert-Regex $out ('(?m)^EXTRA=.*--no-chrome ' + [regex]::Escape($startLine) + '\r?$') 'the start line is the last element of the array the in-window path hands claude'
Assert-Regex $out ('(?m)^COMMAND=claude --name orchestrator-\d{4}-\d{4} --append-system-prompt-file ' + [regex]::Escape($seatFile) + ' --no-chrome ' + [regex]::Escape("'" + $startLine + "'") + '\r?$') 'and the command the tab and window paths run ends with it too, quoted for its spaces'
$commandLine = ([regex]::Match($out, '(?m)^COMMAND=.*$')).Value
Assert-True (([regex]::Matches($commandLine, '--name')).Count -eq 1) 'and names the session once there as well'
Assert-True (-not ($out -match 'Start line not added')) 'and nothing stands aside, so nothing is said about it'
Assert-Regex $out '(?m)^EXTRA=--name orchestrator-\d{4}-\d{4} ' 'a fresh seat launch carries the minute in its name'
$extraLine = ([regex]::Match($out, '(?m)^EXTRA=.*$')).Value
Assert-True (([regex]::Matches($extraLine, '--name')).Count -eq 1) 'the session is named once, never twice'
Assert-Regex $out ('(?m)^CLAUDE_BRIEFS_DIR=' + [regex]::Escape($defaultBriefs) + '\r?$') 'the briefs directory falls back under the user home'
Assert-Regex $out '(?m)^CLAUDE_CLOSING_HOUR=22:00\r?$' 'the closing hour falls back to 22:00'
Assert-Match $out ("`$env:CLAUDE_BRIEFS_DIR = '" + $defaultBriefs + "'") 'the pane command carries the briefs directory'
Assert-Match $out "`$env:CLAUDE_CLOSING_HOUR = '22:00'" 'the pane command carries the closing hour'
$out = Invoke-InWindowSeat 'orchestrator'
Assert-Regex $out ('(?m)^CLAUDE_BRIEFS_DIR=' + [regex]::Escape($defaultBriefs) + '\r?$') 'the in-window path exports the briefs directory too'
Assert-Regex $out '(?m)^CLAUDE_CLOSING_HOUR=22:00\r?$' 'and the closing hour too'

$out = Get-LauncherLiteral demo -ShowEnv -Role orchestrator --name mine
Assert-Exit 0 'a name of your own is accepted on a seated launch'
$extraLine = ([regex]::Match($out, '(?m)^EXTRA=.*$')).Value
Assert-True (([regex]::Matches($extraLine, '--name')).Count -eq 1) 'a name given by hand is never doubled'
Assert-Match $out '--name mine' 'and it is the one that travels'
Assert-Regex $out '(?m)^FRESH=true\r?$' 'a name of your own still leaves the launch fresh'
Assert-Regex $out '(?m)^START=none \(positional given\)\r?$' 'and its value is the last token, so the start line stands aside for it'
Assert-Match $out 'Start line not added: the last argument reads as your prompt; type the start of day yourself.' 'and the launcher says so out loud, since a plan line only the tests read is a silent drop'

Write-Host "`r`nphase 11, a seat with no file is one visible line, not a failed launch"
$out = Get-LauncherLiteral demo -ShowEnv -Role analyst
Assert-Exit 0 'a missing seat still opens a session'
Assert-Regex $out '(?m)^CLAUDE_ROLE=analyst\r?$' 'analyst is a role the launcher accepts'
Assert-Regex $out '(?m)^SEAT=none\r?$' 'no seat is claimed when the file is absent'
Assert-Match $out ('Seat file missing: ' + $missingSeat + '; the session opens without a seat.') 'the absent file is named on one line'
Assert-True (-not ($out -match 'append-system-prompt-file')) 'and nothing is appended, since claude refuses to start on a missing file'

Write-Host "`r`nphase 12, lane and research have no chair and are left alone"
$out = Get-LauncherLiteral demo -ShowEnv -Role lane
Assert-Exit 0 'the dry run exits clean'
Assert-Regex $out '(?m)^SEAT=none\r?$' 'a lane takes no seat'
Assert-True (-not ($out -match 'Seat file missing')) 'and says nothing about a file it never wanted'
Assert-Regex $out '(?m)^START=none\r?$' 'a lane gets no start line'
Assert-Regex $out '(?m)^CLAUDE_BRIEFS_DIR=\(unset\)\r?$' 'the launcher names no briefs directory for a lane'
Assert-Regex $out '(?m)^CLAUDE_CLOSING_HOUR=\(unset\)\r?$' 'nor a closing hour'
Assert-True (-not ($out -match 'CLAUDE_BRIEFS_DIR = ')) 'and the pane command exports neither'

Write-Host "`r`nphase 13, a seated role refuses to continue the last conversation of a folder"
$out = Get-LauncherLiteral demo -ShowEnv -Role orchestrator -c
Assert-Exit 1 'the launcher refuses -c and exits 1'
Assert-Match $out $refusal 'the refusal says what to do instead'
Assert-True (-not ($out -match 'CLAUDE_ROLE=')) 'and it happens before anything is planned'
$out = Get-LauncherLiteral demo -ShowEnv -Role analyst --continue
Assert-Exit 1 'the long spelling is refused too'
Assert-True (-not ($out -match 'Seat file missing')) 'and before the seat file is even looked for'
$out = Get-LauncherLiteral demo -ShowEnv -Role lane -c
Assert-Exit 0 'a lane still continues: it shares no chair'
Assert-Regex $out '(?m)^EXTRA=--name lane -c --no-chrome\r?$' 'and -c reaches claude untouched'

Write-Host "`r`nphase 14, fresh is decided token by token, never by searching the joined string"
# --no-chrome is appended to the argument string before any later check and it carries -c
# inside it, so a substring search calls a bare seat launch a resume and refuses it. The array
# is read as exact tokens instead, and a path that merely contains -c proves the difference.
$out = Get-LauncherLiteral demo -ShowEnv -Role orchestrator --add-dir C:\repo-cache
Assert-Exit 0 'an argument that merely contains the letters is not a continue'
Assert-Regex $out '(?m)^FRESH=true\r?$' 'nor a resume'
Assert-Regex $out '(?m)^START=none \(positional given\)\r?$' 'and the path it carries is the last token, so the start line stands aside for it'
$out = Get-LauncherLiteral demo -ShowEnv -Role orchestrator --resume
Assert-Exit 0 'a resume opens clean'
Assert-Regex $out '(?m)^FRESH=false\r?$' '--resume is read as a resume'
Assert-Regex $out '(?m)^START=none\r?$' 'a resume gets no start line'
Assert-True (-not ($out -match '--name')) 'and keeps the name the session already has'
Assert-Match $out ('--append-system-prompt-file ' + $seatFile) 'a resumed seat still wears its seat'
Assert-True (-not ($out -match 'Start line not added')) 'and nothing stands aside on a resume, which never had a start line'
$out = Invoke-Wrapper demo -ShowEnv -Role orchestrator -- -r
Assert-Regex $out '(?m)^FRESH=false\r?$' '-r past the separator is a resume too'
Assert-True (-not ($out -match '--name')) 'and is not renamed either'

Write-Host "`r`nphase 15, the environment names the briefs directory and the closing hour"
$env:CLAUDE_BRIEFS_DIR = 'C:\evidence\briefs'
$env:CLAUDE_CLOSING_HOUR = '21:15'
$out = Get-LauncherLiteral demo -ShowEnv -Role orchestrator
Assert-Exit 0 'the dry run exits clean'
Assert-Regex $out '(?m)^CLAUDE_BRIEFS_DIR=C:\\evidence\\briefs\r?$' 'an environment already set wins over the fallback'
Assert-Regex $out '(?m)^CLAUDE_CLOSING_HOUR=21:15\r?$' 'the closing hour too'
Assert-Match $out "`$env:CLAUDE_BRIEFS_DIR = 'C:\evidence\briefs'" 'and the pane command carries what it found'
$out = Invoke-InWindowSeat 'orchestrator'
Assert-Regex $out '(?m)^CLAUDE_BRIEFS_DIR=C:\\evidence\\briefs\r?$' 'the in-window path carries it as well'
Assert-Regex $out '(?m)^CLAUDE_CLOSING_HOUR=21:15\r?$' 'with the hour it was given'
Write-Host "`r`nphase 16, the resume spellings that carry an id, and a prompt of your own"
# claude accepts --resume=<id> as well as --resume <id>. The equals spelling is one token, so
# the exact-token test has to read its prefix or a resume looks like a fresh launch and gets a
# second name and a start line on top of the conversation it reopens.
$out = Get-LauncherLiteral demo -ShowEnv -Role orchestrator --resume=abc123
Assert-Exit 0 'the equals spelling opens clean'
Assert-Regex $out '(?m)^FRESH=false\r?$' '--resume=<id> is a resume'
Assert-Regex $out '(?m)^START=none\r?$' 'so it carries no start line'
Assert-True (-not ($out -match '--name')) 'and no second name'
$out = Get-LauncherLiteral demo -ShowEnv -Role orchestrator --continue=abc123
Assert-Exit 1 '--continue=<id> is refused the way --continue is'
Assert-Match $out $refusal 'with the same line'
# A positional the owner typed is the prompt of that session. Adding the start line after it
# would hand claude two prompts, so the launcher stands aside and says so.
$out = Get-LauncherLiteral demo -ShowEnv -Role orchestrator review
Assert-Exit 0 'a prompt of your own opens clean'
Assert-Regex $out '(?m)^FRESH=true\r?$' 'it is still a fresh launch'
Assert-Regex $out '(?m)^START=none \(positional given\)\r?$' 'but the start line stands aside, and the plan says why'
Assert-Match $out 'Start line not added: the last argument reads as your prompt; type the start of day yourself.' 'and the line is visible on the launch itself, not only in the dry run'
Assert-Regex $out ('(?m)^COMMAND=claude --name orchestrator-\d{4}-\d{4} --append-system-prompt-file ' + [regex]::Escape($seatFile) + ' review --no-chrome\r?$') 'so the only prompt in the command is the one you typed'
$out = Get-LauncherLiteral demo -ShowEnv -Role orchestrator --model opus
Assert-Exit 0 'a trailing option value opens clean'
Assert-Regex $out '(?m)^START=none \(positional given\)\r?$' 'and reads as a prompt too, since a launcher cannot know which options take a value'

Write-Host "`r`nphase 17, the browser is turned off in the array, before the seat is added"
$out = Get-LauncherLiteral demo -ShowEnv -Role research
Assert-Exit 0 'research opens clean'
Assert-True (-not ($out -match 'no-chrome')) 'research keeps the browser'
$out = Get-LauncherLiteral demo -ShowEnv -Role orchestrator --chrome
Assert-Exit 0 'asking for the browser opens clean'
Assert-True (-not ($out -match 'no-chrome')) 'a chrome flag of your own is not overridden'
Assert-Regex $out ('(?m)^COMMAND=claude --name orchestrator-\d{4}-\d{4} --append-system-prompt-file ' + [regex]::Escape($seatFile) + ' --chrome ' + [regex]::Escape("'" + $startLine + "'") + '\r?$') 'and the start line is still the last token'
# The word can appear inside a path, so the flag is looked for as an exact token: a substring
# test turns a directory called chrome-cache into a request for the browser.
$out = Get-LauncherLiteral demo -ShowEnv -Role orchestrator --add-dir C:\chrome-cache
Assert-Exit 0 'a path that merely contains the word opens clean'
Assert-Regex $out '(?m)^COMMAND=.* --no-chrome\r?$' 'a path containing chrome does not pass for a chrome flag'

Write-Host "`r`nphase 18, the short form of resume with the id attached to it"
# Verified against claude itself: `claude -rnonexistent-id -p hi` answers "--resume requires a
# valid session ID ... Provided value nonexistent-id", so the id attached to -r is parsed as the
# value of --resume, and a launcher that reads only the bare -r renames and re-prompts a resume.
$out = Get-LauncherLiteral demo -ShowEnv -Role orchestrator -rabc123
Assert-Exit 0 'the attached form opens clean'
Assert-Regex $out '(?m)^FRESH=false\r?$' '-r<id> is a resume'
Assert-Regex $out '(?m)^START=none\r?$' 'so it carries no start line'
Assert-True (-not ($out -match '--name')) 'and no second name'
Assert-True (-not ($out -match 'Start line not added')) 'and nothing stands aside, since it is not a fresh launch'

Remove-Item Env:\CLAUDE_BRIEFS_DIR -ErrorAction SilentlyContinue
Remove-Item Env:\CLAUDE_CLOSING_HOUR -ErrorAction SilentlyContinue
Remove-Item Env:\CLAUDE_SEATS_DIR -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $seatsDir -Recurse -Force -ErrorAction SilentlyContinue

Write-TestResult
