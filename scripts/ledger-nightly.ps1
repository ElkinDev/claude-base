# Twice-daily ledger run.
#
# Registered in Task Scheduler as two daily tasks, 08:00 and 18:00, running as
# the current user with limited rights. The window comes from the machine
# clock, never from a typed date: the morning run covers yesterday 18:00 to
# now, the evening run covers today 08:00 to now. Output and errors are
# appended to <ledger>\nightly.log with a timestamp, and the script always
# exits 0 so a failed run never leaves a red task in the scheduler; the failure
# is in the log. Every JSON step happens inside python.

$ErrorActionPreference = 'Continue'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ledgerPy  = Join-Path $scriptDir 'ledger-day.py'
if ($env:CLAUDE_LEDGER_DIR) {
    $ledgerDir = $env:CLAUDE_LEDGER_DIR
} else {
    $ledgerDir = Join-Path (Split-Path -Parent $scriptDir) 'ledger'
}
$log = Join-Path $ledgerDir 'nightly.log'

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
$outFile = Join-Path $env:TEMP "ledger-$stampFile.out"
$errFile = Join-Path $env:TEMP "ledger-$stampFile.err"
$argLine = '"{0}" --since "{1}"' -f $ledgerPy, $sinceText

try {
    $proc = Start-Process -FilePath $python -ArgumentList $argLine `
        -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $outFile -RedirectStandardError $errFile
    $code = $proc.ExitCode
} catch {
    Write-Log ("could not start " + $python + ": " + $_.Exception.Message)
    exit 0
}

foreach ($file in @($outFile, $errFile)) {
    if (Test-Path $file) {
        foreach ($line in (Get-Content -Path $file)) {
            if ($line.Trim().Length -gt 0) { Write-Log $line }
        }
        Remove-Item -Path $file -Force -ErrorAction SilentlyContinue
    }
}

Write-Log "end, python exit code $code"
exit 0
