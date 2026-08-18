# branch-upstream-fix.ps1: SessionStart + PostToolUse(git *).
# Repoints the current feature branch upstream to origin/<branch> so a plain `git push` targets the
# feature branch, not the protected integration branch. When the integration branch is PR-only, a
# direct push to it is rejected (for example Azure DevOps TF402455); this keeps that from happening.
# Idempotent; only touches local git config. Never commits/pushes/checks out. Exits 0.
$ErrorActionPreference = 'SilentlyContinue'
try {
    $repo = (git rev-parse --show-toplevel 2>$null)
    if (-not $repo) { exit 0 }
    $branch = (git -C $repo rev-parse --abbrev-ref HEAD 2>$null)
    if (-not $branch) { exit 0 }
    if ('devel','main','master','HEAD' -contains $branch) { exit 0 }  # never touch base branches
    $want = "refs/heads/$branch"
    $have = (git -C $repo config --get "branch.$branch.merge" 2>$null)
    if ($have -ne $want) {
        git -C $repo config "branch.$branch.remote" origin 2>$null | Out-Null
        git -C $repo config "branch.$branch.merge"  $want  2>$null | Out-Null
        git -C $repo config push.default current            2>$null | Out-Null
        Write-Output "[branch-upstream-fix] '$branch' push target set to origin/$branch (was merge='$have')"
    }
}
catch { }
exit 0
