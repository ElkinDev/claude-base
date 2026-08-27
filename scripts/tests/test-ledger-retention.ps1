# Covers the retention sweep of ledger-nightly.ps1, rules A and B.
#
# It builds a throwaway tree under TEMP that copies the two real layouts, hands
# it to the script through -ScratchRoot and -ProjectsRoot, and asserts the exact
# set of survivors. The fixture is rebuilt between the dry run, the skip and the
# real run, so a phase can never read the leftovers of the previous one. Ages
# are stamped on the files, never faked with parameters, which is how the sweep
# reads them in production. Run it with:
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tests\test-ledger-retention.ps1

$ErrorActionPreference = 'Stop'

$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = Join-Path (Split-Path -Parent $here) 'ledger-nightly.ps1'
$base   = Join-Path $env:TEMP ('ledger-retention-test-' + [guid]::NewGuid().ToString('N').Substring(0, 8))

$failures = 0
$checks   = 0

function Assert-True {
    param([bool]$Condition, [string]$What)
    $script:checks++
    if ($Condition) {
        Write-Host ("  ok    " + $What)
    } else {
        Write-Host ("  FAIL  " + $What)
        $script:failures++
    }
}

function Assert-Gone {
    param([string]$Path, [string]$What)
    Assert-True (-not (Test-Path -LiteralPath $Path)) ("gone, " + $What)
}

function Assert-Kept {
    param([string]$Path, [string]$What)
    Assert-True (Test-Path -LiteralPath $Path) ("kept, " + $What)
}

function Assert-Log {
    param([string]$LogFile, [string]$Pattern)
    $text = ''
    if (Test-Path -LiteralPath $LogFile) { $text = (Get-Content -LiteralPath $LogFile -Raw) }
    $hit = $text -match [regex]::Escape($Pattern)
    Assert-True $hit ("log says '" + $Pattern + "'")
    if (-not $hit) { Write-Host ("        log was:`r`n" + $text) }
}

# One file of a known size with a known age, parents created on the way.
function New-AgedFile {
    param([string]$Path, [int]$Days, [int]$Size)
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    [IO.File]::WriteAllText($Path, ('x' * $Size), (New-Object Text.ASCIIEncoding))
    $stamp = (Get-Date).AddDays(-$Days)
    (Get-Item -LiteralPath $Path).LastWriteTime = $stamp
}

function New-AgedDir {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { New-Item -ItemType Directory -Path $Path -Force | Out-Null }
}

# Folder stamps are set last, because writing a file or creating a subfolder
# touches the parent and would undo them.
function Set-AgedDir {
    param([string]$Path, [int]$Days)
    (Get-Item -LiteralPath $Path -Force).LastWriteTime = (Get-Date).AddDays(-$Days)
}

# scratch\<project>\<session>\scratchpad and projects\<project>\<session>\subagents,
# the two shapes the sweep walks, with an old and a new case of each.
function New-Fixture {
    param([string]$Root)

    if (Test-Path -LiteralPath $Root) { Remove-Item -LiteralPath $Root -Recurse -Force }
    New-AgedDir $Root

    $scratch = Join-Path $Root 'scratch'
    $p1 = Join-Path $scratch 'C--Repo'
    $p2 = Join-Path $scratch 'C--Repo-Other'

    # A1 old and alone, both the scratchpad and the session folder go
    New-AgedFile (Join-Path $p1 'sess-old\scratchpad\a.txt') 30 100
    # A2 old but the session has a sibling, only the scratchpad goes
    New-AgedFile (Join-Path $p1 'sess-tasks\scratchpad\b.txt') 30 100
    New-AgedFile (Join-Path $p1 'sess-tasks\tasks\keep.json') 30 10
    # A3 fresh, untouched
    New-AgedFile (Join-Path $p1 'sess-new\scratchpad\c.txt') 1 10
    # A4 one old file and one fresh file, the newest one saves the folder
    New-AgedFile (Join-Path $p1 'sess-mixed\scratchpad\old.txt') 30 10
    New-AgedFile (Join-Path $p1 'sess-mixed\scratchpad\nested\fresh.txt') 1 10
    # A5 six days old, inside the seven day window
    New-AgedFile (Join-Path $p1 'sess-edge\scratchpad\edge.txt') 6 10
    # A6 empty scratchpad with old folder stamps
    New-AgedDir  (Join-Path $p2 'sess-empty\scratchpad')
    # A7 folders but no files, old folder stamps
    New-AgedDir  (Join-Path $p2 'sess-dirs\scratchpad\sub')
    # A8 old and nested, the whole tree goes
    New-AgedFile (Join-Path $p2 'sess-deep\scratchpad\nested\deep.txt') 30 100
    # A9 a folder that is not a session, never walked
    New-AgedFile (Join-Path $scratch 'bundled-skills\2.1.0\hash\skill.md') 30 10
    # A10 empty scratchpad of a session that just started, nothing written yet
    New-AgedDir  (Join-Path $p2 'sess-live\scratchpad')

    Set-AgedDir (Join-Path $p2 'sess-empty\scratchpad') 30
    Set-AgedDir (Join-Path $p2 'sess-empty') 30
    Set-AgedDir (Join-Path $p2 'sess-dirs\scratchpad\sub') 30
    Set-AgedDir (Join-Path $p2 'sess-dirs\scratchpad') 30
    Set-AgedDir (Join-Path $p2 'sess-dirs') 30

    $projects = Join-Path $Root 'projects'
    $a = Join-Path $projects 'C--Repo'
    $b = Join-Path $projects 'C--Repo-Other'

    # B1 the main transcript, a file beside the session folder, never a candidate
    New-AgedFile (Join-Path $a 'sess-a.jsonl') 30 100
    # B2 an old subagent transcript, goes
    New-AgedFile (Join-Path $a 'sess-a\subagents\agent-old.jsonl') 30 512
    # B3 its sidecar, goes with it
    New-AgedFile (Join-Path $a 'sess-a\subagents\agent-old.meta.json') 30 100
    # B4 a recent subagent transcript and its sidecar, both stay
    New-AgedFile (Join-Path $a 'sess-a\subagents\agent-new.jsonl') 2 10
    New-AgedFile (Join-Path $a 'sess-a\subagents\agent-new.meta.json') 2 10
    # B5 other per-session files, never candidates
    New-AgedFile (Join-Path $a 'sess-a\tool-results\r1.json') 30 10
    New-AgedFile (Join-Path $a 'sess-a\custom-title.json') 30 10
    # B6 the memory folder, never walked
    New-AgedFile (Join-Path $a 'memory\MEMORY.md') 30 10
    # B7 a session with no folder at all
    New-AgedFile (Join-Path $b 'sess-b.jsonl') 30 10
    # B8 the two sides of the fourteen day line
    New-AgedFile (Join-Path $b 'sess-b\subagents\agent-13d.jsonl') 13 10
    New-AgedFile (Join-Path $b 'sess-b\subagents\agent-15d.jsonl') 15 512
    # B9 a fresh sidecar of an old transcript, the pair decides, not its age
    New-AgedFile (Join-Path $b 'sess-b\subagents\agent-15d.meta.json') 1 100
    # B10 an orphan sidecar left by an earlier run, goes on its own age
    New-AgedFile (Join-Path $b 'sess-b\subagents\agent-orphan.meta.json') 30 100
    # B11 an orphan sidecar of an agent that just started, stays
    New-AgedFile (Join-Path $b 'sess-b\subagents\agent-live.meta.json') 1 10
}

function Invoke-Sweep {
    param([string]$Root, [string]$LogFile, [switch]$DryRun, [switch]$Skip)
    $callArgs = @{
        RetentionOnly = $true
        ScratchRoot   = (Join-Path $Root 'scratch')
        ProjectsRoot  = (Join-Path $Root 'projects')
        LogPath       = $LogFile
    }
    if ($DryRun) { $callArgs['RetentionDryRun'] = $true }
    if ($Skip)   { $callArgs['SkipRetention'] = $true }
    & $target @callArgs
}

Write-Host ("script under test: " + $target)
Write-Host ("fixture root:      " + $base)

if (-not (Test-Path -LiteralPath $target)) {
    Write-Host "FAIL  ledger-nightly.ps1 not found"
    exit 1
}

try {
    # ---------------------------------------------------------------- dry run --
    Write-Host "`r`nphase 1, -RetentionDryRun deletes nothing"
    New-Fixture $base
    $log1 = Join-Path $base 'dry.log'
    $before = @(Get-ChildItem -LiteralPath $base -Recurse -Force | Select-Object -ExpandProperty FullName)
    Invoke-Sweep -Root $base -LogFile $log1 -DryRun
    $after = @(Get-ChildItem -LiteralPath $base -Recurse -Force |
        Where-Object { $_.FullName -ne $log1 } | Select-Object -ExpandProperty FullName)
    Assert-True ($before.Count -eq $after.Count) ("nothing removed, " + $before.Count + " entries before and after")
    Assert-Log $log1 'would delete 5 scratchpads, 4 empty session folders, 3 files, 300 B, 0 errors'
    Assert-Log $log1 'would delete 2 transcripts, 3 sidecars, 1.3 KB, 0 errors'

    # ------------------------------------------------------------ skip switch --
    Write-Host "`r`nphase 2, -SkipRetention runs no rule"
    New-Fixture $base
    $log2 = Join-Path $base 'skip.log'
    $before = @(Get-ChildItem -LiteralPath $base -Recurse -Force | Select-Object -ExpandProperty FullName)
    Invoke-Sweep -Root $base -LogFile $log2 -Skip
    $after = @(Get-ChildItem -LiteralPath $base -Recurse -Force |
        Where-Object { $_.FullName -ne $log2 } | Select-Object -ExpandProperty FullName)
    Assert-True ($before.Count -eq $after.Count) 'nothing removed'
    Assert-Log $log2 'retention | skipped, -SkipRetention'

    # --------------------------------------------------------------- real run --
    Write-Host "`r`nphase 3, the sweep deletes exactly the old entries"
    New-Fixture $base
    $log3 = Join-Path $base 'run.log'
    Invoke-Sweep -Root $base -LogFile $log3

    $s = Join-Path $base 'scratch'
    $p1 = Join-Path $s 'C--Repo'
    $p2 = Join-Path $s 'C--Repo-Other'

    Assert-Gone (Join-Path $p1 'sess-old')                   'A1 old scratchpad took its empty session folder'
    Assert-Gone (Join-Path $p1 'sess-tasks\scratchpad')      'A2 old scratchpad'
    Assert-Kept (Join-Path $p1 'sess-tasks\tasks\keep.json') 'A2 the sibling folder and its session'
    Assert-Kept (Join-Path $p1 'sess-new\scratchpad\c.txt')  'A3 fresh scratchpad'
    Assert-Kept (Join-Path $p1 'sess-mixed\scratchpad\old.txt')          'A4 old file under a folder with a fresh one'
    Assert-Kept (Join-Path $p1 'sess-mixed\scratchpad\nested\fresh.txt') 'A4 the fresh file itself'
    Assert-Kept (Join-Path $p1 'sess-edge\scratchpad\edge.txt') 'A5 six days old, inside the window'
    Assert-Gone (Join-Path $p2 'sess-empty')                 'A6 empty scratchpad with old folder stamps, and its session'
    Assert-Gone (Join-Path $p2 'sess-dirs')                  'A7 scratchpad of empty folders and its session'
    Assert-Gone (Join-Path $p2 'sess-deep')                  'A8 nested old scratchpad and its session'
    Assert-Kept (Join-Path $s 'bundled-skills\2.1.0\hash\skill.md') 'A9 a root folder that holds no sessions'
    Assert-Kept (Join-Path $p2 'sess-live\scratchpad')       'A10 empty scratchpad of a session that just started'

    $pj = Join-Path $base 'projects'
    $a = Join-Path $pj 'C--Repo'
    $b = Join-Path $pj 'C--Repo-Other'

    Assert-Kept (Join-Path $a 'sess-a.jsonl')                          'B1 the main transcript'
    Assert-Gone (Join-Path $a 'sess-a\subagents\agent-old.jsonl')      'B2 the old subagent transcript'
    Assert-Gone (Join-Path $a 'sess-a\subagents\agent-old.meta.json')  'B3 the sidecar of the deleted transcript'
    Assert-Kept (Join-Path $a 'sess-a\subagents\agent-new.jsonl')      'B4 the recent subagent transcript'
    Assert-Kept (Join-Path $a 'sess-a\subagents\agent-new.meta.json')  'B4 the sidecar of the kept transcript'
    Assert-Kept (Join-Path $a 'sess-a\tool-results\r1.json')           'B5 tool results'
    Assert-Kept (Join-Path $a 'sess-a\custom-title.json')              'B5 the session title file'
    Assert-Kept (Join-Path $a 'memory\MEMORY.md')                      'B6 the memory folder'
    Assert-Kept (Join-Path $b 'sess-b.jsonl')                          'B7 a main transcript with no session folder'
    Assert-Kept (Join-Path $b 'sess-b\subagents\agent-13d.jsonl')      'B8 thirteen days old, inside the window'
    Assert-Gone (Join-Path $b 'sess-b\subagents\agent-15d.jsonl')      'B8 fifteen days old'
    Assert-Gone (Join-Path $b 'sess-b\subagents\agent-15d.meta.json')  'B9 a fresh sidecar whose transcript went'
    Assert-Gone (Join-Path $b 'sess-b\subagents\agent-orphan.meta.json') 'B10 an old orphan sidecar'
    Assert-Kept (Join-Path $b 'sess-b\subagents\agent-live.meta.json') 'B11 a fresh orphan sidecar'

    Assert-Log $log3 'deleted 5 scratchpads, 4 empty session folders, 3 files, 300 B, 0 errors'
    Assert-Log $log3 'deleted 2 transcripts, 3 sidecars, 1.3 KB, 0 errors'

    # ------------------------------------------------------------- empty root --
    Write-Host "`r`nphase 4, missing roots are logged and never throw"
    $log4 = Join-Path $base 'missing.log'
    & $target -RetentionOnly -ScratchRoot (Join-Path $base 'no-such-scratch') `
        -ProjectsRoot (Join-Path $base 'no-such-projects') -LogPath $log4
    Assert-Log $log4 'retention A scratchpads | root not found'
    Assert-Log $log4 'retention B subagents | root not found'
} finally {
    if (Test-Path -LiteralPath $base) {
        Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ''
if ($failures -eq 0) {
    Write-Host ("PASS  " + $checks + " checks")
    exit 0
} else {
    Write-Host ("FAIL  " + $failures + " of " + $checks + " checks")
    exit 1
}
