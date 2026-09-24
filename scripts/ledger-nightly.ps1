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
$trainPy    = Join-Path $scriptDir 'train-wait.py'
$errorsPy   = Join-Path $scriptDir 'tool-errors.py'
if ($env:CLAUDE_LEDGER_DIR) {
    $ledgerDir = $env:CLAUDE_LEDGER_DIR
} else {
    $ledgerDir = Join-Path (Split-Path -Parent $scriptDir) 'ledger'
}
$metersLog  = Join-Path $ledgerDir 'meters-log.csv'
$metersHead = 'time,account,session,weekly_all,scoped_meter,scoped_pct'
$errorsLog  = Join-Path $ledgerDir 'tool-errors.log'

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

# The long-path form of a local path, which Windows PowerShell 5.1 lists and deletes past 260
# characters; a UNC path or one that already carries the prefix comes back as it is.
function Get-LongPath {
    param([string]$Path)
    if ($Path.StartsWith('\\')) { return $Path }
    return '\\?\' + $Path
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
            # A scratchpad can hold build output past 260 characters (Kotlin class names). Without the
            # long-path prefix Get-ChildItem silently skips those files, so a pad could be dated by
            # what it could see, and Remove-Item fails on them.
            $padLong = Get-LongPath -Path $pad

            try {
                if (Test-HasLink -Path $padLong) {
                    $linked++
                    continue
                }

                $inside = @(Get-ChildItem -LiteralPath $padLong -Recurse -Force -File -ErrorAction SilentlyContinue)
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
                    Remove-Item -LiteralPath $padLong -Recurse -Force -ErrorAction Stop
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
    param([string]$Name, [string]$ArgLine, [switch]$KeepOutput, [int]$TimeoutMs = 0)

    $outFile = Join-Path $env:TEMP "ledger-$stampFile-$Name.out"
    $errFile = Join-Path $env:TEMP "ledger-$stampFile-$Name.err"
    $lines = @()
    $killed = $false
    try {
        $proc = Start-Process -FilePath $python -ArgumentList $ArgLine `
            -NoNewWindow -PassThru `
            -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        # Reading the handle once caches it, or ExitCode is empty after WaitForExit() on a process
        # started with -PassThru and no -Wait (PowerShell 5.1 quirk, verified 2026-09-18).
        $null = $proc.Handle
        # A step with a cap is killed at the cap and reported, so one slow reader never holds the
        # whole ledger; a step without one waits as before.
        if ($TimeoutMs -gt 0) {
            if (-not $proc.WaitForExit($TimeoutMs)) {
                try { $proc.Kill(); $proc.WaitForExit(5000) } catch {}
                Write-Log ("$Name killed after " + $TimeoutMs + " ms")
                $killed = $true
            }
        } else { $proc.WaitForExit() }
        # A killed step keeps code 124 and still hands over what it printed, so its partial
        # output and any traceback reach the log and its temp files are removed below.
        $code = if ($killed) { 124 } else { $proc.ExitCode }
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

# 3a. the train rule's numbers: trains per day, lanes per train, union gate runs per lane landed and the
# hours from a lane's last CLEAR review to its merge, over the last seven days, one row per ledger. Git and
# file reads only, no build, seconds. A window with no train prints its zeros, which is itself the news.
if (Test-Path $trainPy) {
    $train = Invoke-Step -Name 'train-wait' -ArgLine ('"{0}" --row' -f $trainPy) -KeepOutput -TimeoutMs 120000
    $trainRow = $train.Lines | Where-Object { $_ -match ' train-wait ' } | Select-Object -Last 1
    if ($train.Code -eq 0 -and $trainRow) {
        Write-Log "trains | $trainRow"
    } else {
        Write-Log "trains | no row, train-wait exit code $($train.Code)"
    }
} else {
    Write-Log 'trains | train-wait.py not found, skipped'
}

# 3b. failed tool calls by signature: tool-errors.py reads the transcripts touched in the window (the two
# largest, which are the seats; a subagent keeps its own) and prints the calls, the failed ones and the top
# four signatures of the day, from the is_error results plus the known-failure rules. A defect class surfaces
# here the same day instead of waiting for someone to read a pane. One line per session appended to the log.
if (Test-Path $errorsPy) {
    $txRoot = $ProjectsRoot
    if (-not $txRoot) { $txRoot = Join-Path (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.claude') 'projects' }
    $txFiles = @(Get-ChildItem -Path $txRoot -Filter '*.jsonl' -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -ge $since -and $_.DirectoryName -notmatch '\\subagents$' } |
        Sort-Object Length -Descending | Select-Object -First 2)
    if ($txFiles.Count -gt 0) {
        foreach ($tx in $txFiles) {
            $sid = $tx.BaseName.Substring(0, [Math]::Min(8, $tx.BaseName.Length))
            $err = Invoke-Step -Name ('tool-errors-' + $sid) -ArgLine ('"{0}" --file "{1}" --row' -f $errorsPy, $tx.FullName) -KeepOutput -TimeoutMs 120000
            $errRow = $err.Lines | Where-Object { $_ -match '^tool-errors ' } | Select-Object -Last 1
            if ($err.Code -eq 0 -and $errRow) {
                if (-not (Test-Path $errorsLog)) { New-Item -ItemType File -Path $errorsLog -Force | Out-Null }
                Add-Content -Path $errorsLog -Value ('{0} {1} {2}' -f (Get-Date).ToString('yyyy-MM-dd HH:mm'), $sid, $errRow) -Encoding utf8
                Write-Log "tool-errors | $sid $errRow"
            } else {
                Write-Log "tool-errors | $sid no line, exit code $($err.Code)"
            }
        }
    } else {
        Write-Log 'tool-errors | no transcript touched in the window'
    }
} else {
    Write-Log 'tool-errors | tool-errors.py not found, skipped'
}

# 3c. the token shape of the two largest main transcripts touched in the window (over 5 MB, which in practice
# are the seats), one line each appended to token-shape.log: where the window's tokens went by kind. A
# transcript is read once by python, never opened here.
$shapePy  = Join-Path $scriptDir 'token-shape.py'
$shapeLog = Join-Path $ledgerDir 'token-shape.log'
if (Test-Path $shapePy) {
    $shapeRoot = $ProjectsRoot
    if (-not $shapeRoot) { $shapeRoot = Join-Path (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.claude') 'projects' }
    $shapeFiles = @(Get-ChildItem -Path $shapeRoot -Filter '*.jsonl' -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -ge $since -and $_.Length -gt 5MB -and $_.DirectoryName -notmatch '\\subagents$' } |
        Sort-Object Length -Descending | Select-Object -First 2)
    if ($shapeFiles.Count -gt 0) {
        $sinceUtc = $since.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss')
        $untilUtc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss')
        foreach ($c in $shapeFiles) {
            $sid = $c.BaseName.Substring(0, [Math]::Min(8, $c.BaseName.Length))
            $shape = Invoke-Step -Name "shape-$sid" -ArgLine ('"{0}" "{1}" --since {2} --until {3} --row' -f $shapePy, $c.FullName, $sinceUtc, $untilUtc) -KeepOutput -TimeoutMs 300000
            $line = $shape.Lines | Where-Object { $_ -match '^token-shape ' } | Select-Object -Last 1
            if ($shape.Code -eq 0 -and $line) {
                [System.IO.File]::AppendAllText($shapeLog, (('{0} {1} {2}' -f (Get-Date).ToString('yyyy-MM-dd HH:mm'), $sid, $line) + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
                Write-Log "shape | $sid $line"
            } else {
                Write-Log "shape | no line for $sid, exit code $($shape.Code)"
            }
        }
    } else {
        Write-Log 'shape | no main transcript over 5 MB touched in the window'
    }
} else {
    Write-Log 'shape | token-shape.py not found, skipped'
}

# 3d. the rework shape of the window (gate runs, reds by phase, BLOCK and delta rows, lanes at three gates or
# three review rounds), one line appended to rework-shape.log. Local times, the clock the register is stamped in;
# the paths it reads come from its own config (rework-shape.py --config).
$reworkPy  = Join-Path $scriptDir 'rework-shape.py'
$reworkLog = Join-Path $ledgerDir 'rework-shape.log'
if (Test-Path $reworkPy) {
    $sinceLocal = $since.ToString('yyyy-MM-dd HH:mm')
    $untilLocal = (Get-Date).ToString('yyyy-MM-dd HH:mm')
    $rw = Invoke-Step -Name 'rework-shape' -ArgLine ('"{0}" --since "{1}" --until "{2}"' -f $reworkPy, $sinceLocal, $untilLocal) -KeepOutput -TimeoutMs 120000
    $rline = $rw.Lines | Where-Object { $_ -match '^rework-shape ' } | Select-Object -Last 1
    if ($rw.Code -eq 0 -and $rline) {
        [System.IO.File]::AppendAllText($reworkLog, (('{0} {1}' -f (Get-Date).ToString('yyyy-MM-dd HH:mm'), $rline) + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
        Write-Log "rework | $rline"
    } else {
        Write-Log ('rework | no line, exit code {0}; tail: {1}' -f $rw.Code, ((@($rw.Lines) | Select-Object -Last 3) -join ' / '))
    }
} else {
    Write-Log 'rework | rework-shape.py not found, skipped'
}

# 3e. the BLOCK rate of tooling reviews over the newest complete window, the number that keeps or reverts the
# hostile-input practice, one line per window appended to tooling-block-rate.log: the morning and evening runs
# repeat the same complete window for days, so a line whose window (the text up to the first colon) matches the
# log's last line is not appended again.
$tbrPy  = Join-Path $scriptDir 'tooling-block-rate.py'
$tbrLog = Join-Path $ledgerDir 'tooling-block-rate.log'
$tbrRoot = $env:TOOLING_BLOCK_ROOT
if (-not $tbrRoot) { $tbrRoot = $env:EVIDENCE_ROOT }
if ((Test-Path $tbrPy) -and -not $tbrRoot) {
    Write-Log 'tooling | neither TOOLING_BLOCK_ROOT nor EVIDENCE_ROOT names the folder holding reviews/, skipped'
} elseif ((Test-Path $tbrPy) -and -not ($env:TOOLING_BLOCK_ANCHOR -or $env:TOOLING_BLOCK_FIRST_DECISION)) {
    Write-Log 'tooling | no practice day set (TOOLING_BLOCK_ANCHOR or TOOLING_BLOCK_FIRST_DECISION), skipped'
} elseif (Test-Path $tbrPy) {
    $tb = Invoke-Step -Name 'tooling-block-rate' -ArgLine ('"{0}" --last-complete --root "{1}"' -f $tbrPy, $tbrRoot) -KeepOutput -TimeoutMs 120000
    $tline = $tb.Lines | Where-Object { $_ -match '^tooling-block-rate ' } | Select-Object -Last 1
    $tlast = $null
    if (Test-Path $tbrLog) { $tlast = Get-Content -Path $tbrLog -Tail 1 -ErrorAction SilentlyContinue }
    $twin = if ($tline) { ($tline -split ':')[0] } else { '' }
    if ($tb.Code -eq 0 -and $tline -and $tlast -and $tlast.Contains($twin)) {
        Write-Log "tooling | same window as the last line, not appended: $tline"
    } elseif ($tb.Code -eq 0 -and $tline) {
        [System.IO.File]::AppendAllText($tbrLog, (('{0} {1}' -f (Get-Date).ToString('yyyy-MM-dd HH:mm'), $tline) + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
        Write-Log "tooling | $tline"
    } else {
        Write-Log ('tooling | no line, exit code {0}; tail: {1}' -f $tb.Code, ((@($tb.Lines) | Select-Object -Last 3) -join ' / '))
    }
} else {
    Write-Log 'tooling | tooling-block-rate.py not found, skipped'
}

# 3f. the kit drift: the twins of this kit that trail what the machine runs, the ones owed (live change older
# than 24 h), the pairs carried by verdict and the live tools with no twin, one line per run appended to
# kit-drift.log. Exit 2 is a live folder that does not exist, never a 0 reading.
$ktdPy  = Join-Path $scriptDir 'kit-twin-drift.py'
$ktdLog = Join-Path $ledgerDir 'kit-drift.log'
if (Test-Path $ktdPy) {
    $kd = Invoke-Step -Name 'kit-twin-drift' -ArgLine ('"{0}" --row' -f $ktdPy) -KeepOutput -TimeoutMs 120000
    $kline = $kd.Lines | Where-Object { $_ -match '^kit-twin-drift ' } | Select-Object -Last 1
    if ($kd.Code -eq 0 -and $kline) {
        [System.IO.File]::AppendAllText($ktdLog, (('{0} {1}' -f (Get-Date).ToString('yyyy-MM-dd HH:mm'), $kline) + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
        Write-Log "kit | $kline"
    } else {
        Write-Log ('kit | no line, exit code {0}; tail: {1}' -f $kd.Code, ((@($kd.Lines) | Select-Object -Last 3) -join ' / '))
    }
} else {
    Write-Log 'kit | kit-twin-drift.py not found, skipped'
}

# 3g. the pulse: which adopted mechanisms of pulse.md are silent, dark or matching nothing, and which warnings of
# this log stand or recur, with a key per item that a decision row cites. It runs after the steps whose warnings it
# reads, so this run's count. Read-only, no agent, under a second; its lines start with "pulse |", never "reports |"
# or "meters |", so it never reads its own output back as a warning. Exit 1 means an input was missing or a register
# line malformed, and the block names it. The log it reads is this run's own, handed over as PULSE_LOG for these two
# steps only. Each script is the installed copy the session-start hook runs (~/.claude/tools), so the two never
# drift apart; a script that was never installed falls back to the clone's own claude/tools, one script at a time,
# so a partial install still runs both (review kit-twins-0924a r2 note 3).
function Resolve-KitTool([string]$name) {
    $installed = Join-Path (Join-Path $HOME '.claude\tools') $name
    if (Test-Path $installed) { return $installed }
    return Join-Path (Join-Path (Split-Path -Parent $scriptDir) 'claude\tools') $name
}
$pulsePy = Resolve-KitTool 'pulse.py'
if (Test-Path $pulsePy) {
    $env:PULSE_LOG = $log
    $pulse = Invoke-Step -Name 'pulse' -ArgLine ('"{0}"' -f $pulsePy) -TimeoutMs 120000
    Write-Log "pulse.py exit code $($pulse.Code)"
} else {
    Write-Log "pulse | pulse.py not found, skipped"
}

# 3h. the pulse's escalation: an item in both of the last two pulse runs, this run included, and cited by no decision
# row goes into today's owner decisions file as one auto-class row with a 21:15 deadline, so it prints among the open
# asks at the next session start. No agent, under a second; its lines start with "escalate |", which neither pulse.py
# nor the escalation reads back. Exit 1 means an input could not be read or written, and its line says which.
$escPy = Resolve-KitTool 'pulse-escalate.py'
if (Test-Path $escPy) {
    $env:PULSE_LOG = $log
    $esc = Invoke-Step -Name 'escalate' -ArgLine ('"{0}"' -f $escPy) -TimeoutMs 120000
    Write-Log "pulse-escalate.py exit code $($esc.Code)"
} else {
    Write-Log "escalate | pulse-escalate.py not found, skipped"
}
Remove-Item Env:PULSE_LOG -ErrorAction SilentlyContinue

# 4. retention, morning only, after the three steps
if ($sweepDue) {
    Invoke-RetentionSweep
} elseif ($SkipRetention) {
    Write-Log 'retention | skipped, -SkipRetention'
} else {
    Write-Log 'retention | not due, evening run'
}

# 5. the worktree sweep, morning only, and only when `worktree_sweep.repo` in ledger-config.json names a
# repository (null, the default, is no sweep). worktree-sweep.py removes the landed, clean worktrees of that
# repository, oldest first and at most `limit` a run, after writing each one's build evidence into one
# verified zip that is never replaced; it refuses the whole run with exit 3 while any *.lock.d is held under
# `lock_root` and stops at the first refusal with exit 2. Its lines start with "worktrees |". Python runs
# unbuffered (-u): its output goes to a file, and a sweep killed at the step's cap would otherwise lose every
# line it had not flushed. No agent. A dry retention run never reaches it.
if ($sweepDue -and -not $RetentionDryRun) {
    $wtConfig = Read-LedgerConfig
    $wtBlock = $null
    if ($null -ne $wtConfig) { $wtBlock = $wtConfig.worktree_sweep }
    if ($null -eq $wtBlock -or -not $wtBlock.repo) {
        Write-Log 'worktrees | no worktree_sweep.repo in ledger-config.json, skipped'
    } else {
        $wtsPy = Resolve-KitTool 'worktree-sweep.py'
        if (-not (Test-Path $wtsPy)) {
            Write-Log 'worktrees | worktree-sweep.py not found, skipped'
        } else {
            $wtLimit = 30
            if ($null -ne $wtBlock.limit) { $wtLimit = [int]$wtBlock.limit }
            # A path that ends in a backslash would escape its closing quote on the command line.
            $wtArgs = '-u "{0}" --repo "{1}" --apply --limit {2}' -f $wtsPy, ([string]$wtBlock.repo).TrimEnd('\', '/'), $wtLimit
            if ($wtBlock.lock_root) { $wtArgs += (' --lock-root "{0}"' -f ([string]$wtBlock.lock_root).TrimEnd('\', '/')) }
            if ($wtBlock.evidence_root) { $wtArgs += (' --evidence-root "{0}"' -f ([string]$wtBlock.evidence_root).TrimEnd('\', '/')) }
            $wts = Invoke-Step -Name 'worktrees' -ArgLine $wtArgs -TimeoutMs 2700000
            Write-Log "worktree-sweep.py exit code $($wts.Code)"
        }
    }
}

Write-Log "end"
exit 0
