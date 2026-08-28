# Project-scope test for install.ps1: what a repository that already has rules of its own gets,
# and what -LocalOnly actually hides from git (F13). User scope is scripts\tests\test-install-smoke.ps1.
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tests\test-install-project.ps1
#
# git is required: the point of most of these phases is what `git check-ignore` says, which is the
# only honest way to test an exclude file. The harness and the sandbox are in lib-install-test.ps1.

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'lib-install-test.ps1')

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "FAIL  git is not on PATH; this suite cannot check what git ignores"
    exit 1
}

function Invoke-Git {
    param([string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { return ((& git @Arguments 2>&1) | Out-String) } finally { $ErrorActionPreference = $previous }
}

$realProfile = $env:USERPROFILE
$base        = New-KitSandbox 'kit-install-project'
$company     = Join-Path $base 'company-app'
$fresh       = Join-Path $base 'fresh-app'
$main        = Join-Path $base 'main-repo'
$worktree    = Join-Path $base 'wt-feature'
$rules       = 'Company rules: every push runs the gate. Do not commit to main.'
$ownDocs     = 'The team docs index. Owned by the team, not by any tool.'

try {
    New-Item -ItemType Directory -Force -Path $company, $fresh, $main | Out-Null
    Invoke-Git @('init', '-q', $company) | Out-Null
    Invoke-Git @('init', '-q', $fresh) | Out-Null
    Invoke-Git @('init', '-q', $main) | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $company '.git\hooks'), (Join-Path $company 'docs') | Out-Null
    Set-Content -LiteralPath (Join-Path $company 'CLAUDE.md') -Value $rules -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $company 'docs\README.md') -Value $ownDocs -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $company '.git\hooks\pre-push') -Value "#!/bin/sh`nexit 0" -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $company '.pre-commit-config.yaml') -Value 'repos: []' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $main 'README.md') -Value 'the shared repository' -Encoding UTF8
    Invoke-Git @('-C', $main, 'add', '-A') | Out-Null
    Invoke-Git @('-C', $main, '-c', 'user.name=suite', '-c', 'user.email=suite-identity',
                 'commit', '-q', '-m', 'first') | Out-Null

    # ------------------------------------------------- a repository with rules ---
    Write-Host "`r`nphase 1, a repository that already has rules keeps every one of them"
    $hookBefore  = (Get-FileHash -LiteralPath (Join-Path $company '.git\hooks\pre-push') -Algorithm SHA256).Hash
    $out = Invoke-Install @('-Project', $company, '-Sdd')
    Assert-Exit 0 'the project install succeeds'
    Assert-Match $out 'Adoption preflight' 'the preflight ran before the plan'
    Assert-Regex $out 'CLAUDE\.md\s+kept as is' 'the preflight says what happens to their CLAUDE.md'
    Assert-Regex $out '\.git/hooks/pre-push\s+never touched' 'the preflight says the company hook is untouched'
    Assert-Regex $out '\.pre-commit-config\.yaml\s+never touched' 'and so is their pre-commit config'
    Assert-Match $out 'writes only CLAUDE.md, CLAUDE.project.md, .claude/ and docs/' `
        'with -Sdd the promise names docs/ too'
    Assert-True ((Get-Content -LiteralPath (Join-Path $company 'CLAUDE.md') -Raw).Contains($rules)) `
        'the company CLAUDE.md is untouched'
    Assert-True ((Get-Content -LiteralPath (Join-Path $company 'docs\README.md') -Raw).Contains($ownDocs)) `
        'the team docs index is untouched'
    Assert-True (Test-Path -LiteralPath (Join-Path $company 'CLAUDE.md.new')) 'the kit version landed as CLAUDE.md.new'
    Assert-True (Test-Path -LiteralPath (Join-Path $company 'docs\README.md.new')) 'and as docs/README.md.new'
    Assert-True ((Get-FileHash -LiteralPath (Join-Path $company '.git\hooks\pre-push') -Algorithm SHA256).Hash -eq $hookBefore) `
        'the company pre-push hook is untouched'
    Assert-True (Test-Path -LiteralPath (Join-Path $company '.claude\settings.local.json')) 'the project wiring landed'
    Assert-True (Test-Path -LiteralPath (Join-Path $company 'CLAUDE.project.md')) 'the profile to fill landed'
    Assert-Match $out "check your team's policy" 'the run tells the adopter to check the team policy'
    $stampCompany = Get-LastStamp

    # ---------------------------------------------------------------- dry run ----
    Write-Host "`r`nphase 2, -DryRun touches neither the repository nor the kit home"
    $beforeProject = Get-TreeHash $company
    $beforeHome    = Get-TreeHash $script:KitHome
    $out = Invoke-Install @('-Project', $company, '-Sdd', '-LocalOnly', '-DryRun')
    Assert-Exit 0 'the dry run succeeds'
    Assert-True ((Get-TreeHash $company) -eq $beforeProject) 'the repository, .git included, is byte for byte the one before'
    Assert-True ((Get-TreeHash $script:KitHome) -eq $beforeHome) 'and so is the kit home'
    Assert-Match $out 'would append to' 'it says which exclude lines it would add'

    # ----------------------------------------------------- -LocalOnly and -Sdd ---
    Write-Host "`r`nphase 3, -LocalOnly hides everything the run wrote, docs/ included"
    $out = Invoke-Install @('-Project', $fresh, '-Sdd', '-LocalOnly')
    Assert-Exit 0 'the install succeeds'
    Assert-True (Test-Path -LiteralPath (Join-Path $fresh 'docs\README.md')) '-Sdd scaffolded docs/'
    foreach ($path in @('CLAUDE.md', 'CLAUDE.project.md', 'docs/README.md', '.claude/settings.local.json')) {
        Assert-True (Test-GitIgnored $fresh $path) "git ignores $path"
    }
    Assert-True ((Invoke-Git @('-C', $fresh, 'status', '--short')).Trim() -eq '') `
        'git status is empty, so nothing the kit wrote can reach the team'

    Write-Host "`r`nphase 4, running -LocalOnly again adds nothing"
    $excludeFresh = Join-Path $fresh '.git\info\exclude'
    $out = Invoke-Install @('-Project', $fresh, '-Sdd', '-LocalOnly')
    Assert-Exit 0 'the second run succeeds'
    $lines = @(Get-Content -LiteralPath $excludeFresh)
    Assert-True ((@($lines | Where-Object { $_ -eq 'CLAUDE.project.md' })).Count -eq 1) 'no duplicate line'
    Assert-True ((@($lines | Where-Object { $_ -eq '.claude/' })).Count -eq 1) '.claude/ is one folder line, not one per file'
    Assert-Match $out 'already excluded' 'the run says it had nothing to add'

    Write-Host "`r`nphase 5, -LocalOnly never hides a file of the adopter's own"
    $out = Invoke-Install @('-Project', $company, '-Sdd', '-LocalOnly')
    Assert-Exit 0 'the install succeeds'
    Assert-True (-not (Test-GitIgnored $company 'CLAUDE.md')) 'their CLAUDE.md stays visible to git'
    Assert-True (-not (Test-GitIgnored $company 'docs/README.md')) 'and so does their docs index'
    Assert-True (Test-GitIgnored $company 'CLAUDE.md.new') 'the proposal beside it is hidden'
    Assert-True (Test-GitIgnored $company 'docs/README.md.new') 'and so is the one beside their docs index'
    Assert-True (Test-GitIgnored $company 'CLAUDE.project.md') 'what the kit created is hidden'
    $excludeCompany = @(Get-Content -LiteralPath (Join-Path $company '.git\info\exclude'))
    Assert-True (-not ($excludeCompany -contains '*.new')) 'the exclude names the proposals, it does not blanket *.new'

    # -------------------------------------------------------------- worktree ----
    Write-Host "`r`nphase 6, -LocalOnly works in a worktree, where info/ is in the common directory"
    Invoke-Git @('-C', $main, 'worktree', 'add', '-q', '-b', 'feature', $worktree) | Out-Null
    Assert-True (Test-Path -LiteralPath (Join-Path $worktree '.git') -PathType Leaf) 'the worktree .git is a file, not a folder'
    $out = Invoke-Install @('-Project', $worktree, '-LocalOnly')
    Assert-Exit 0 'the install into the worktree succeeds'
    Assert-Match $out 'writes only CLAUDE.md, CLAUDE.project.md and .claude/' `
        'without -Sdd the promise does not name docs/'
    Assert-True (Test-GitIgnored $worktree 'CLAUDE.project.md') 'git actually ignores what the run wrote'
    Assert-True (Test-GitIgnored $worktree '.claude/settings.local.json') 'and the project wiring with it'
    Assert-True ((Invoke-Git @('-C', $worktree, 'status', '--short')).Trim() -eq '') 'git status in the worktree is empty'
    Assert-True (Test-Path -LiteralPath (Join-Path $main '.git\info\exclude')) 'the lines went to the common directory'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $main '.git\worktrees\wt-feature\info\exclude'))) `
        'and not to the per-worktree info folder git never reads'

    # -------------------------------------------------------------- rollback ----
    Write-Host "`r`nphase 7, rolling the project install back leaves the company files alone"
    $out = Invoke-Restore @('--stamp', $stampCompany)
    Assert-Exit 0 'the rollback of a project install succeeds, with nothing skipped'
    Assert-Match $out 'kept ' 'it reports the files it never wrote as kept'
    Assert-True ((Get-Content -LiteralPath (Join-Path $company 'CLAUDE.md') -Raw).Contains($rules)) `
        'the company CLAUDE.md is still theirs'
    Assert-True ((Get-Content -LiteralPath (Join-Path $company 'docs\README.md') -Raw).Contains($ownDocs)) `
        'and so is their docs index'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $company 'CLAUDE.project.md'))) `
        'what that run created is gone'

    # ------------------------------------------ a CLAUDE.md that already matches -
    Write-Host "`r`nphase 8, a CLAUDE.md that is byte for byte the kit's is reported as such"
    $twin = Join-Path $base 'twin-app'
    New-Item -ItemType Directory -Force -Path $twin | Out-Null
    Copy-Item -LiteralPath (Join-Path $script:RepoRoot 'project-template\CLAUDE.md') `
              -Destination (Join-Path $twin 'CLAUDE.md')
    $out = Invoke-Install @('-Project', $twin, '-DryRun')
    Assert-Exit 0 'the run over an identical CLAUDE.md succeeds'
    Assert-Regex $out 'CLAUDE\.md\s+kept as is, already matches the kit' `
        'the preflight says it already matches the kit'
    Assert-True (-not ($out -match 'CLAUDE\.md\s+kept as is, the kit version lands')) `
        'and promises no .new that the run would never write'

    # ------------------------------------------- what the source folders give up -
    Write-Host "`r`nphase 9, build residue in the clone is never installed and never recorded"
    . (Join-Path $script:RepoRoot 'install\adopt.ps1')
    $src = Join-Path $base 'src-tree'
    New-Item -ItemType Directory -Force -Path (Join-Path $src 'hooks\__pycache__') | Out-Null
    Set-Content -LiteralPath (Join-Path $src 'hooks\landing.py') -Value 'print("hi")' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $src 'hooks\__pycache__\landing.cpython-311.pyc') `
                -Value 'bytecode' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $src 'hooks\landing.py.bak') -Value 'an older copy' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $src 'hooks\landing.py.new') -Value 'a proposal' -Encoding UTF8
    $names = @(Get-KitPairs $src (Join-Path $base 'target') | ForEach-Object { Split-Path -Leaf $_.Target })
    Assert-True ($names -contains 'landing.py') 'the file the kit ships is installed'
    Assert-True ($names.Count -eq 1) ('and nothing else is, whatever the clone carries: ' + ($names -join ', '))
} finally {
    if (Test-Path -LiteralPath $worktree) {
        Invoke-Git @('-C', $main, 'worktree', 'remove', '--force', $worktree) | Out-Null
    }
    Close-KitSandbox $realProfile
}

Write-TestResult
