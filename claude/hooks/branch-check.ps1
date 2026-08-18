# branch-check.ps1: SessionStart advisory. READ-ONLY: prints a note, never mutates git. Exits 0.
$ErrorActionPreference = 'SilentlyContinue'
try {
    $branch = (git rev-parse --abbrev-ref HEAD 2>$null)
    if (-not $branch) { exit 0 }
    $repo    = (git rev-parse --show-toplevel 2>$null)
    $pointer = Join-Path $repo '.claude/.active-story'
    $activeNum = $null
    if (Test-Path $pointer) { $activeNum = (Get-Content $pointer -Raw).Trim() }
    $curNum = $null
    if ($branch -match '(\d{3,6})') { $curNum = $Matches[1] }
    $msg = $null
    if ($curNum) {
        $msg = "On story branch '$branch' (work item $curNum)."
        if ($activeNum -and $activeNum -ne $curNum) { $msg += " NOTE: last active work item was $activeNum." }
    }
    elseif ($activeNum) {
        $existing = (git branch --list "*$activeNum*" 2>$null |
                     ForEach-Object { $_.TrimStart('*','+',' ').Trim() } | Select-Object -First 1)
        $state = if ($existing) { "branch '$existing' exists" } else { 'no branch created yet' }
        $msg = "You are on '$branch', NOT a story branch. Active work item is $activeNum ($state). " +
               "ASK before switching/creating - never change git silently."
    }
    if ($msg) { Write-Output "[branch-check] $msg" }
}
catch { }
exit 0
