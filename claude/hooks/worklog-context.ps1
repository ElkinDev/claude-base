# worklog-context.ps1 - SessionStart hook. Injects the current branch's worklog task block.
# Read-only, advisory, exits 0. Cross-project (wired at user level).
$ErrorActionPreference = 'SilentlyContinue'
. "$PSScriptRoot\worklog-lib.ps1"

$c = Get-WorklogContext
if (-not $c) { exit 0 }
if (-not (Test-Path -LiteralPath $c.TasksDir)) { exit 0 }   # worklog not initialized for this repo

$key = $c.Key
if (Test-Path -LiteralPath $c.TaskFile) {
    $fm = Get-Frontmatter $c.TaskFile
    $oq = $fm['open_question']
    if ([string]::IsNullOrWhiteSpace($oq)) { $oq = '(none)' }
    $last = Get-LastEntryForKey $c.SessionsFile $key
    if ($last) { $lastStr = "$($last.commit) clean=$($last.clean) @ $($last.ts)" }
    else { $lastStr = "$($fm['commit']) clean=$($fm['clean']) @ $($fm['updated'])" }
    $block = "[worklog] $($c.Branch) ($($fm['status']))`n"
    $block += "goal: $($fm['goal'])`n"
    $block += "next: $($fm['next'])`n"
    $block += "open question: $oq`n"
    $block += "detail: tasks/$key.md   |   last: $lastStr"
} else {
    $block = "[worklog] no task file for branch '$($c.Branch)'. Run /worklog new to start one."
}

$others = @()
Get-ChildItem -LiteralPath $c.TasksDir -Filter '*.md' -File -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.BaseName -ne $key) {
        $st = (Get-Frontmatter $_.FullName)['status']
        if ('active','blocked','paused' -contains $st) { $others += "$($_.BaseName) ($st)" }
    }
}
if ($others.Count -gt 0) { $block += "`nother active: " + ($others -join ', ') }

$payload = [PSCustomObject]@{
    hookSpecificOutput = [PSCustomObject]@{
        hookEventName     = 'SessionStart'
        additionalContext = $block
    }
}
Write-Output ($payload | ConvertTo-Json -Compress -Depth 5)
exit 0
