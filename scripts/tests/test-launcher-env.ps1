# The environment the launcher hands a session: the default cap, the research exception and the
# opt-in auto-compact window (F: context economics).
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tests\test-launcher-env.ps1
#
# claude\claude-account.ps1 -ShowEnv prints what it would export and exits without opening a
# session or touching a profile, which is the only way to assert this without launching Claude.
# Both routes are checked: the variables the in-window path sets, and the command string the tab
# and new-window paths run, since they are built separately and have drifted apart before.

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

Assert-True (Test-Path -LiteralPath $launcher) 'the launcher is where the tests expect it'

Write-Host "`r`nphase 1, the default is the 200k cap and no window override"
$out = Get-LauncherEnv @('-ShowEnv')
Assert-Exit 0 'the dry run exits clean'
Assert-Regex $out '(?m)^CLAUDE_ROLE=lane\r?$' 'the default role is lane'
Assert-Regex $out '(?m)^CLAUDE_CODE_DISABLE_1M_CONTEXT=1\r?$' 'the context stays capped at 200k'
Assert-Regex $out '(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=\(unchanged\)\r?$' 'the window variable is left alone'
Assert-Match $out "`$env:CLAUDE_CODE_DISABLE_1M_CONTEXT = '1'" 'the pane command caps the context too'
Assert-True (-not ($out -match 'PANE_COMMAND=.*AUTO_COMPACT_WINDOW')) 'the pane command sets no window'

Write-Host "`r`nphase 2, an orchestrator is capped the same way"
$out = Get-LauncherEnv @('-ShowEnv', '-Role', 'orchestrator')
Assert-Exit 0 'the dry run exits clean'
Assert-Regex $out '(?m)^CLAUDE_ROLE=orchestrator\r?$' 'the role travels to the session'
Assert-Regex $out '(?m)^CLAUDE_CODE_DISABLE_1M_CONTEXT=1\r?$' 'the orchestrator is capped'

Write-Host "`r`nphase 3, research is the uncapped role and still sets no window"
$out = Get-LauncherEnv @('-ShowEnv', '-Role', 'research')
Assert-Exit 0 'the dry run exits clean'
Assert-Regex $out '(?m)^CLAUDE_CODE_DISABLE_1M_CONTEXT=\(unset\)\r?$' 'research runs uncapped'
Assert-Regex $out '(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=\(unchanged\)\r?$' 'research sets no window either'

Write-Host "`r`nphase 4, -CompactWindow drops the cap and names the window"
$out = Get-LauncherEnv @('-ShowEnv', '-CompactWindow', '230000')
Assert-Exit 0 'the dry run exits clean'
Assert-Regex $out '(?m)^CLAUDE_CODE_DISABLE_1M_CONTEXT=\(unset\)\r?$' 'the cap is dropped, or the window could not grow'
Assert-Regex $out '(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=230000\r?$' 'auto-compaction fires at the window given'
Assert-Match $out "`$env:CLAUDE_CODE_AUTO_COMPACT_WINDOW = '230000'" 'the pane command carries the window'
Assert-True (-not ($out -match "PANE_COMMAND=.*CLAUDE_CODE_DISABLE_1M_CONTEXT = '1'")) 'the pane command drops the cap'

Write-Host "`r`nphase 5, the switch is opt-in: zero is off"
$out = Get-LauncherEnv @('-ShowEnv', '-CompactWindow', '0')
Assert-Exit 0 'the dry run exits clean'
Assert-Regex $out '(?m)^CLAUDE_CODE_DISABLE_1M_CONTEXT=1\r?$' 'zero leaves the default in place'
Assert-Regex $out '(?m)^CLAUDE_CODE_AUTO_COMPACT_WINDOW=\(unchanged\)\r?$' 'zero sets no window'

Write-Host "`r`nphase 6, the switch is documented where a user looks for it"
$out = Get-LauncherEnv @('-Help')
Assert-Exit 0 'the help exits clean'
Assert-Match $out '-CompactWindow' 'the help lists the switch'
Assert-Match $out '-ShowEnv' 'the help lists the dry run'

Write-TestResult
