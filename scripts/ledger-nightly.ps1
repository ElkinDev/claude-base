# Twice-daily ledger run.
#
# Registered in Task Scheduler as two daily tasks, 08:00 and 18:00, running as
# the current user with limited rights. The window comes from the machine
# clock, never from a typed date: the morning run covers yesterday 18:00 to
# now, the evening run covers today 08:00 to now. Output and errors are
# appended to <ledger>\nightly.log with a timestamp, and the script always
# exits 0 so a failed run never leaves a red task in the scheduler; the failure
# is in the log. Every JSON step happens inside python.
#
# Three steps per run, in order. First ledger-day.py appends the window's rows
# to ledger.md. Then usage-probe.py reads the weekly meters once and the line
# is appended to <ledger>\meters-log.csv, which is two direct HTTP reads a day,
# no model and no session. Last ledger-compare.py re-renders compare.md against
# the frozen baseline. A step that fails is logged and the next one still runs,
# because the compare is useful even when a meter read times out. A script that
# is not installed is skipped with a line in the log.
#
# The morning run then sweeps retention, once a day and after the three steps
# so nothing the ledger reads is deleted before it reads it. The same clock
# test that picks the window picks the run, hour under 13 is the morning run,
# so the scheduled task keeps its argument-free command line. Roots and ages
# come from the `retention` block of ledger-config.json, never from this file.
# -SkipRetention drops the sweep, -RetentionDryRun reports what would go without
# deleting anything, and -RetentionOnly runs the sweep alone, which is what the
# test drives; the four value parameters override the config when they are
# passed, which is how the test points the sweep at a fixture.

param(
    [switch]$RetentionOnly,
    [switch]$RetentionDryRun,
    [switch]$SkipRetention,
    [string]$ScratchRoot,
    [string]$ProjectsRoot,
    [int]$ScratchDays,
    [int]$SubagentDays,
    [string]$LogPath = ''
)

$ErrorActionPreference = 'Continue'

# Kept at script scope because inside a function $PSBoundParameters is the
# function's own, and the sweep needs to know which values were typed here.
$typed = $PSBoundParameters

$scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ledgerPy   = Join-Path $scriptDir 'ledger-day.py'
$probePy    = Join-Path $scriptDir 'usage-probe.py'
$comparePy  = Join-Path $scriptDir 'ledger-compare.py'
if ($env:CLAUDE_LEDGER_DIR) {
    $ledgerDir = $env:CLAUDE_LEDGER_DIR
} else {
    $ledgerDir = Join-Path (Split-Path -Parent $scriptDir) 'ledger'
}
$metersLog  = Join-Path $ledgerDir 'meters-log.csv'
$metersHead = 'time,account,session,weekly_all,scoped_meter,scoped_pct'

if ($LogPath) {
    $log = $LogPath
    $logDir = Split-Path -Parent $log
    if ($logDir -and -not (Test-Path -LiteralPath $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
} else {
    $log = Join-Path $ledgerDir 'nightly.log'
}

function Write-Log {
    param([string]$Text)
    $stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    Add-Content -Path $log -Value "[$stamp] $Text" -Encoding utf8
}

if (-not $LogPath -and -not (Test-Path $ledgerDir)) {
    New-Item -ItemType Directory -Path $ledgerDir -Force | Out-Null
}

$now = Get-Date
if ($now.Hour -lt 13) {
    $since = $now.Date.AddDays(-1).AddHours(18)
    $label = 'morning run, window from yesterday 18:00'
    $isMorning = $true
} else {
    $since = $now.Date.AddHours(8)
    $label = 'evening run, window from today 08:00'
    $isMorning = $false
}
$sinceText = $since.ToString('yyyy-MM-dd HH:mm')

Write-Log "start, $label, --since `"$sinceText`""

# ---------------------------------------------------------------- retention --
#
# Rule A, scratchpads. A <scratch_root>/<project>/<session>/scratchpad whose
# newest file at any depth is older than scratch_days is removed whole. A
# scratchpad holding no files at all is judged by the folder stamps instead,
# its own and the session folder's, whichever is newer, so a session that has
# just started and has not written anything yet survives the morning run. The
# <session> folder goes with the scratchpad when nothing else is left inside,
# and stays when a sibling such as tasks/ survives.
#
# Rule B, subagent transcripts. Under <projects_root>/<project>/<session>/
# subagents only agent-*.jsonl is a candidate, each judged by its own mtime
# against subagent_days, and the agent-*.meta.json of the same stem goes with
# it and only with it. A sidecar whose transcript is already gone is swept on
# its own mtime, which clears what an earlier run left behind. The main
# transcript is <session>.jsonl, a file one level up, so it is never enumerated
# and never at risk; memory folders are skipped by name; tool results are left
# alone.
#
# Everything is deleted inside its own try, so a file held open by a live
# session costs one error in the count and the sweep keeps going. Neither rule
# can reach outside its root, and a folder holding a junction or a symlink is
# left untouched rather than recursed into.

# ledger-config.json beside this script, the committed example when it has not
# been copied yet, and nothing at all when neither parses.
function Read-LedgerConfig {
    $candidates = @(
        (Join-Path $scriptDir 'ledger-config.json'),
        (Join-Path $scriptDir 'ledger-config.example.json')
    )
    foreach ($path in $candidates) {
        if (Test-Path -LiteralPath $path) {
            try {
                return (Get-Content -LiteralPath $path -Raw | ConvertFrom-Json)
            } catch {
                Write-Log ("config | could not read " + $path + ": " + $_.Exception.Message)
            }
        }
    }
    return $null
}

function Format-Bytes {
    param([long]$Bytes)
    if ($Bytes -ge 1073741824) { return ('{0:N2} GB' -f ($Bytes / 1073741824)) }
    if ($Bytes -ge 1048576)    { return ('{0:N1} MB' -f ($Bytes / 1048576)) }
    if ($Bytes -ge 1024)       { return ('{0:N1} KB' -f ($Bytes / 1024)) }
    return "$Bytes B"
}

# True when the folder is a reparse point or holds one at any depth, and true
# when it cannot be read, because both mean recursive delete is not safe here.
function Test-HasLink {
    param([string]$Path)
    $link = [IO.FileAttributes]::ReparsePoint
    try {
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        if (($item.Attributes -band $link) -eq $link) { return $true }
        $kids = @(Get-ChildItem -LiteralPath $Path -Recurse -Force -Directory -ErrorAction SilentlyContinue)
        foreach ($kid in $kids) {
            if (($kid.Attributes -band $link) -eq $link) { return $true }
        }
    } catch {
        return $true
    }
    return $false
}

function Invoke-ScratchSweep {
    param([string]$Root, [datetime]$Cutoff, [bool]$DryRun)

    if ($DryRun) { $verb = 'would delete' } else { $verb = 'deleted' }

    if (-not (Test-Path -LiteralPath $Root)) {
        Write-Log "retention A scratchpads | root not found, $Root"
        return
    }
    $rootFull = (Get-Item -LiteralPath $Root -Force).FullName.TrimEnd('\', '/')

    $pads = 0
    $sessionsGone = 0
    $files = 0
    $bytes = [long]0
    $errors = 0
    $linked = 0

    $projects = @(Get-ChildItem -LiteralPath $Root -Directory -Force -ErrorAction SilentlyContinue)
    foreach ($project in $projects) {
        $sessions = @(Get-ChildItem -LiteralPath $project.FullName -Directory -Force -ErrorAction SilentlyContinue)
        foreach ($session in $sessions) {
            $pad = Join-Path $session.FullName 'scratchpad'
            if (-not (Test-Path -LiteralPath $pad -PathType Container)) { continue }
            if (-not $pad.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) { continue }

            try {
                if (Test-HasLink -Path $pad) {
                    $linked++
                    continue
                }

                $inside = @(Get-ChildItem -LiteralPath $pad -Recurse -Force -File -ErrorAction SilentlyContinue)
                $newest = $null
                foreach ($file in $inside) {
                    if ($null -eq $newest -or $file.LastWriteTime -gt $newest) { $newest = $file.LastWriteTime }
                }
                if ($null -eq $newest) {
                    # No file to date it by, so the folders date it: a session
                    # that started this morning and has written nothing yet is
                    # live, not stale.
                    $newest = (Get-Item -LiteralPath $pad -Force).LastWriteTime
                    if ($session.LastWriteTime -gt $newest) { $newest = $session.LastWriteTime }
                }
                if ($newest -ge $Cutoff) { continue }

                $size = [long]0
                foreach ($file in $inside) { $size += $file.Length }

                if (-not $DryRun) {
                    Remove-Item -LiteralPath $pad -Recurse -Force -ErrorAction Stop
                }
                $pads++
                $files += $inside.Count
                $bytes += $size
            } catch {
                $errors++
                Write-Log ("retention A | could not remove " + $pad + ": " + $_.Exception.Message)
                continue
            }

            try {
                $left = @(Get-ChildItem -LiteralPath $session.FullName -Force -ErrorAction SilentlyContinue)
                if ($DryRun) {
                    $left = @($left | Where-Object { $_.Name -ne 'scratchpad' })
                }
                if ($left.Count -eq 0) {
                    if (-not $DryRun) {
                        # Non-recursive on purpose: it throws instead of asking
                        # when something appeared in the folder since the check.
                        [IO.Directory]::Delete($session.FullName, $false)
                    }
                    $sessionsGone++
                }
            } catch {
                $errors++
                Write-Log ("retention A | could not remove " + $session.FullName + ": " + $_.Exception.Message)
            }
        }
    }

    Write-Log ("retention A scratchpads | {0} {1} scratchpads, {2} empty session folders, {3} files, {4}, {5} errors, {6} skipped for links, older than {7:yyyy-MM-dd HH:mm}" -f `
        $verb, $pads, $sessionsGone, $files, (Format-Bytes $bytes), $errors, $linked, $Cutoff)
}

function Invoke-SubagentSweep {
    param([string]$Root, [datetime]$Cutoff, [bool]$DryRun)

    if ($DryRun) { $verb = 'would delete' } else { $verb = 'deleted' }

    if (-not (Test-Path -LiteralPath $Root)) {
        Write-Log "retention B subagents | root not found, $Root"
        return
    }

    $files = 0
    $sides = 0
    $bytes = [long]0
    $errors = 0

    $projects = @(Get-ChildItem -LiteralPath $Root -Directory -Force -ErrorAction SilentlyContinue)
    foreach ($project in $projects) {
        $sessions = @(Get-ChildItem -LiteralPath $project.FullName -Directory -Force -ErrorAction SilentlyContinue)
        foreach ($session in $sessions) {
            if ($session.Name -eq 'memory') { continue }
            $subDir = Join-Path $session.FullName 'subagents'
            if (-not (Test-Path -LiteralPath $subDir -PathType Container)) { continue }

            $all = @(Get-ChildItem -LiteralPath $subDir -File -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like 'agent-*' })
            $candidates = @($all | Where-Object { $_.Extension -eq '.jsonl' })
            $sidecars   = @($all | Where-Object { $_.Name -like '*.meta.json' })

            # The stems that had a transcript when the folder was read, so the
            # orphan pass below cannot count a sidecar its own pair just took.
            $paired = @{}
            foreach ($file in $candidates) { $paired[$file.BaseName] = $true }

            foreach ($file in $candidates) {
                if ($file.LastWriteTime -ge $Cutoff) { continue }
                try {
                    $size = $file.Length
                    if (-not $DryRun) {
                        Remove-Item -LiteralPath $file.FullName -Force -ErrorAction Stop
                    }
                    $files++
                    $bytes += $size
                } catch {
                    $errors++
                    Write-Log ("retention B | could not remove " + $file.FullName + ": " + $_.Exception.Message)
                    continue
                }

                # The sidecar goes with its transcript whatever its own age says,
                # because on its own it describes a file that no longer exists.
                $side = Join-Path $subDir ($file.BaseName + '.meta.json')
                if (Test-Path -LiteralPath $side -PathType Leaf) {
                    try {
                        $sideSize = (Get-Item -LiteralPath $side -Force).Length
                        if (-not $DryRun) {
                            Remove-Item -LiteralPath $side -Force -ErrorAction Stop
                        }
                        $sides++
                        $bytes += $sideSize
                    } catch {
                        $errors++
                        Write-Log ("retention B | could not remove " + $side + ": " + $_.Exception.Message)
                    }
                }
            }

            foreach ($file in $sidecars) {
                $stem = $file.Name -replace '\.meta\.json$', ''
                if ($paired.ContainsKey($stem)) { continue }
                if ($file.LastWriteTime -ge $Cutoff) { continue }
                try {
                    $size = $file.Length
                    if (-not $DryRun) {
                        Remove-Item -LiteralPath $file.FullName -Force -ErrorAction Stop
                    }
                    $sides++
                    $bytes += $size
                } catch {
                    $errors++
                    Write-Log ("retention B | could not remove " + $file.FullName + ": " + $_.Exception.Message)
                }
            }
        }
    }

    Write-Log ("retention B subagents | {0} {1} transcripts, {2} sidecars, {3}, {4} errors, older than {5:yyyy-MM-dd HH:mm}" -f `
        $verb, $files, $sides, (Format-Bytes $bytes), $errors, $Cutoff)
}

# The config fills what the command line did not pass, and the built-in default
# fills what the config leaves null, so a machine with no ledger-config.json
# still sweeps the two standard locations.
function Invoke-RetentionSweep {
    $scratchPath  = Join-Path ([IO.Path]::GetTempPath()) 'claude'
    $projectsPath = Join-Path (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.claude') 'projects'
    $scratchAge   = 7
    $subagentAge  = 14

    $config = Read-LedgerConfig
    if ($null -ne $config -and $null -ne $config.retention) {
        $block = $config.retention
        if ($block.scratch_root)   { $scratchPath  = [string]$block.scratch_root }
        if ($block.projects_root)  { $projectsPath = [string]$block.projects_root }
        if ($null -ne $block.scratch_days)   { $scratchAge  = [int]$block.scratch_days }
        if ($null -ne $block.subagent_days)  { $subagentAge = [int]$block.subagent_days }
    }

    if ($typed.ContainsKey('ScratchRoot'))   { $scratchPath  = $ScratchRoot }
    if ($typed.ContainsKey('ProjectsRoot'))  { $projectsPath = $ProjectsRoot }
    if ($typed.ContainsKey('ScratchDays'))   { $scratchAge   = $ScratchDays }
    if ($typed.ContainsKey('SubagentDays'))  { $subagentAge  = $SubagentDays }

    Invoke-ScratchSweep  -Root $scratchPath  -Cutoff $now.AddDays(-$scratchAge)  -DryRun ([bool]$RetentionDryRun)
    Invoke-SubagentSweep -Root $projectsPath -Cutoff $now.AddDays(-$subagentAge) -DryRun ([bool]$RetentionDryRun)
}

$sweepDue = $false
if (-not $SkipRetention) {
    if ($RetentionOnly -or $isMorning) { $sweepDue = $true }
}

if ($RetentionOnly) {
    if ($sweepDue) {
        Invoke-RetentionSweep
    } else {
        Write-Log 'retention | skipped, -SkipRetention'
    }
    Write-Log "end"
    exit 0
}

# The scheduler starts tasks without the user PATH, so python is resolved here
# and the resolved path is logged.
$python = $env:CLAUDE_LEDGER_PYTHON
if (-not $python -or -not (Test-Path $python)) {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\python.exe')
    )
    $python = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $python) {
    $found = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -ne $found) { $python = $found.Source }
}
if (-not $python) {
    Write-Log 'python was not found, set CLAUDE_LEDGER_PYTHON to its full path'
    exit 0
}

$stampFile = $now.ToString('yyyyMMdd-HHmmss')

# Runs one python script, logs whatever it printed, and hands back the exit
# code and the standard output lines so a caller can keep them.
function Invoke-Step {
    param([string]$Name, [string]$ArgLine, [switch]$KeepOutput)

    $outFile = Join-Path $env:TEMP "ledger-$stampFile-$Name.out"
    $errFile = Join-Path $env:TEMP "ledger-$stampFile-$Name.err"
    $lines = @()
    try {
        $proc = Start-Process -FilePath $python -ArgumentList $ArgLine `
            -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        $code = $proc.ExitCode
    } catch {
        Write-Log ("$Name could not start python: " + $_.Exception.Message)
        return @{ Code = 1; Lines = @() }
    }

    if (Test-Path $outFile) {
        $lines = @(Get-Content -Path $outFile | Where-Object { $_.Trim().Length -gt 0 })
        if (-not $KeepOutput) {
            foreach ($line in $lines) { Write-Log "$Name | $line" }
        }
        Remove-Item -Path $outFile -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path $errFile) {
        foreach ($line in (Get-Content -Path $errFile)) {
            if ($line.Trim().Length -gt 0) { Write-Log "$Name | $line" }
        }
        Remove-Item -Path $errFile -Force -ErrorAction SilentlyContinue
    }
    return @{ Code = $code; Lines = $lines }
}

# 1. the window's rows
$day = Invoke-Step -Name 'day' -ArgLine ('"{0}" --since "{1}"' -f $ledgerPy, $sinceText)
Write-Log "ledger-day.py exit code $($day.Code)"

# 2. the weekly meters, one line appended
if (Test-Path $probePy) {
    $probe = Invoke-Step -Name 'probe' -ArgLine ('"{0}" --csv' -f $probePy) -KeepOutput
    $row = $probe.Lines | Where-Object { $_ -match '^\d{4}-\d{2}-\d{2} ' } | Select-Object -Last 1
    if ($probe.Code -eq 0 -and $row) {
        if (-not (Test-Path $metersLog)) {
            Set-Content -Path $metersLog -Value $metersHead -Encoding utf8
        }
        Add-Content -Path $metersLog -Value $row -Encoding utf8
        Write-Log "meters | appended $row"
    } else {
        Write-Log "meters | no line appended, probe exit code $($probe.Code)"
    }
} else {
    Write-Log "meters | usage-probe.py not found, skipped"
}

# 3. the before and after page
if (Test-Path $comparePy) {
    $compare = Invoke-Step -Name 'compare' -ArgLine ('"{0}"' -f $comparePy)
    Write-Log "ledger-compare.py exit code $($compare.Code)"
} else {
    Write-Log "compare | ledger-compare.py not found, skipped"
}

# 4. retention, morning only, after the three steps
if ($sweepDue) {
    Invoke-RetentionSweep
} elseif ($SkipRetention) {
    Write-Log 'retention | skipped, -SkipRetention'
} else {
    Write-Log 'retention | not due, evening run'
}

Write-Log "end"
exit 0
