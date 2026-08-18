# worklog-lib.ps1 - shared helpers for the worklog hooks. Windows PowerShell 5.1 compatible.
# Dot-sourced by worklog-context.ps1, worklog-snapshot.ps1, worklog-resolve.ps1.

function Get-WorklogContext {
    # Returns a context object for the current repo/branch, or $null when not applicable
    # (not a git repo, or on a base branch). Paths are returned whether or not they exist.
    $branch = (git rev-parse --abbrev-ref HEAD 2>$null)
    if (-not $branch) { return $null }
    if ('main','master','devel','HEAD' -contains $branch) { return $null }

    $common = (git rev-parse --git-common-dir 2>$null)
    if (-not $common) { return $null }
    if (-not [System.IO.Path]::IsPathRooted($common)) {
        $common = Join-Path (Get-Location).Path $common
    }
    try { $common = (Resolve-Path -LiteralPath $common -ErrorAction Stop).Path } catch { }
    $root = Split-Path $common -Parent
    if (-not $root) { return $null }

    $base = (Split-Path $root -Leaf).ToLower()
    $base = ($base -replace '[^a-z0-9]+','-').Trim('-')
    $md5 = [System.Security.Cryptography.MD5]::Create()
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($root.ToLower())
    $hash = (($md5.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') }) -join '').Substring(0,8)
    $repoId = "$base-$hash"

    if ($branch -match '^(\d+)') { $key = $matches[1] }
    else { $key = ($branch.ToLower() -replace '[^a-z0-9]+','-').Trim('-') }

    $worklog = Join-Path $env:USERPROFILE ".claude\worklog\$repoId"
    $tasksDir = Join-Path $worklog 'tasks'

    return [PSCustomObject]@{
        Branch       = $branch
        RepoId       = $repoId
        RepoRoot     = $root
        Key          = $key
        WorklogDir   = $worklog
        TasksDir     = $tasksDir
        SessionsFile = (Join-Path $worklog 'sessions.jsonl')
        TaskFile     = (Join-Path $tasksDir "$key.md")
    }
}

function Get-Frontmatter([string]$path) {
    # Parse flat 'key: value' frontmatter between the first two '---' lines. Returns a hashtable.
    $fm = @{}
    if (-not (Test-Path -LiteralPath $path)) { return $fm }
    $inFm = $false
    foreach ($line in (Get-Content -LiteralPath $path)) {
        if ($line.Trim() -eq '---') {
            if (-not $inFm) { $inFm = $true; continue } else { break }
        }
        if ($inFm -and $line -match '^\s*([A-Za-z_]+):\s*(.*)$') {
            $fm[$matches[1]] = $matches[2].Trim()
        }
    }
    return $fm
}

function Get-LastEntryForKey([string]$sessionsFile, [string]$key) {
    # Last sessions.jsonl entry whose id matches, by scanning the tail. $null if none.
    if (-not (Test-Path -LiteralPath $sessionsFile)) { return $null }
    $found = $null
    foreach ($line in (Get-Content -LiteralPath $sessionsFile -Tail 50)) {
        try { $o = $line | ConvertFrom-Json } catch { continue }
        if ("$($o.id)" -eq "$key") { $found = $o }
    }
    return $found
}
