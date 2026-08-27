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

$ErrorActionPreference = 'Continue'

$scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ledgerPy   = Join-Path $scriptDir 'ledger-day.py'
$probePy    = Join-Path $scriptDir 'usage-probe.py'
$comparePy  = Join-Path $scriptDir 'ledger-compare.py'
if ($env:CLAUDE_LEDGER_DIR) {
    $ledgerDir = $env:CLAUDE_LEDGER_DIR
} else {
    $ledgerDir = Join-Path (Split-Path -Parent $scriptDir) 'ledger'
}
$log        = Join-Path $ledgerDir 'nightly.log'
$metersLog  = Join-Path $ledgerDir 'meters-log.csv'
$metersHead = 'time,account,session,weekly_all,scoped_meter,scoped_pct'

function Write-Log {
    param([string]$Text)
    $stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    Add-Content -Path $log -Value "[$stamp] $Text" -Encoding utf8
}

if (-not (Test-Path $ledgerDir)) {
    New-Item -ItemType Directory -Path $ledgerDir -Force | Out-Null
}

$now = Get-Date
if ($now.Hour -lt 13) {
    $since = $now.Date.AddDays(-1).AddHours(18)
    $label = 'morning run, window from yesterday 18:00'
} else {
    $since = $now.Date.AddHours(8)
    $label = 'evening run, window from today 08:00'
}
$sinceText = $since.ToString('yyyy-MM-dd HH:mm')

Write-Log "start, $label, --since `"$sinceText`""

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

Write-Log "end"
exit 0
