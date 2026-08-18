# worklog-snapshot.ps1 - PreCompact | SessionEnd | Stop hook. Appends a mechanical git
# snapshot to sessions.jsonl. A hook is code, not the model, so it never writes the semantic
# summary (that is the /worklog save verb). Exits 0. Cross-project (wired at user level).
$ErrorActionPreference = 'SilentlyContinue'
. "$PSScriptRoot\worklog-lib.ps1"

$event = ''
try {
    if ([Console]::IsInputRedirected) {
        $raw = [Console]::In.ReadToEnd()
        if ($raw) { $event = ($raw | ConvertFrom-Json).hook_event_name }
    }
} catch { }

$c = Get-WorklogContext
if (-not $c) { exit 0 }
if (-not (Test-Path -LiteralPath $c.TaskFile)) { exit 0 }   # early bail: no task -> cheap on every Stop

$commit = (git rev-parse --short HEAD 2>$null)
$clean = [string]::IsNullOrWhiteSpace((git status --porcelain 2>$null))

# Stop is throttled: snapshot only on a material git change (new commit or changed clean state).
if ($event -eq 'Stop') {
    $last = Get-LastEntryForKey $c.SessionsFile $c.Key
    if ($last -and ("$($last.commit)" -eq "$commit") -and ("$($last.clean)".ToLower() -eq "$clean".ToLower())) {
        exit 0
    }
}

$ts = [System.DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
$entry = [PSCustomObject]@{
    ts = $ts; id = $c.Key; branch = $c.Branch; kind = 'auto'; event = $event; commit = $commit; clean = $clean
}
$json = $entry | ConvertTo-Json -Compress -Depth 5
[System.IO.File]::AppendAllText($c.SessionsFile, ($json + "`n"), (New-Object System.Text.UTF8Encoding($false)))
exit 0
