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

function Invoke-InstallFrom {
    # The installer of another kit tree, such as the copy with no git metadata that phase 11 stages.
    # The same capture as Invoke-Install: stderr merged in, the exit code kept.
    param([string]$Installer, [string[]]$Arguments = @())
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $text = ((& powershell -NoProfile -ExecutionPolicy Bypass -File $Installer @Arguments 2>&1) | Out-String)
        $script:LastExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
    return $text
}

function Read-ScaffoldSettings {
    # The .claude/settings.json of a scaffolded project: its text, the parsed object ($null when the
    # file is missing or is not json), its top level keys and every hook command, read from the object.
    param([string]$Project)
    $file = Join-Path $Project '.claude\settings.json'
    $text = if (Test-Path -LiteralPath $file -PathType Leaf) { (Get-Content -LiteralPath $file -Raw) } else { '' }
    $parsed = $null
    if ($text) { try { $parsed = ($text | ConvertFrom-Json) } catch { $parsed = $null } }
    $commands = @()
    $keys = @()
    if ($parsed) {
        $keys = @($parsed.PSObject.Properties.Name)
        if ($parsed.hooks) {
            foreach ($kind in $parsed.hooks.PSObject.Properties) {
                foreach ($group in @($kind.Value)) {
                    foreach ($hook in @($group.hooks)) { if ($hook.command) { $commands += [string]$hook.command } }
                }
            }
        }
    }
    return [pscustomobject]@{ Text = $text; Parsed = $parsed; Keys = $keys; Commands = $commands }
}

function Get-FilterCommandCount {
    # How many hook commands of a scaffolded settings.json run the gradle filter of that project.
    param($Settings)
    return @($Settings.Commands | Where-Object { $_ -like '*scripts/hooks/filter-gradle-output.py*' }).Count
}

function Get-HookNamedFiles {
    # Every file under the project that a command of its two settings files names, with the
    # project-dir variable (either spelling) filled in. A settings file that is not there gives none.
    param([string]$Project)
    $slash = [string][char]92
    $fwd   = $Project.Replace($slash, '/').TrimEnd('/')
    $found = @()
    foreach ($settingsName in @('settings.json', 'settings.local.json')) {
        $settingsFile = Join-Path $Project ".claude\$settingsName"
        if (-not (Test-Path -LiteralPath $settingsFile -PathType Leaf)) { continue }
        $settingsText = Get-Content -LiteralPath $settingsFile -Raw
        foreach ($m in [regex]::Matches($settingsText, '"command":\s*"((?:[^"\\]|\\.)*)"')) {
            $cmd = [regex]::Unescape($m.Groups[1].Value).Replace($slash, '/')
            $cmd = [regex]::Replace($cmd, '\$\{?CLAUDE_PROJECT_DIR\}?', $fwd)
            foreach ($t in [regex]::Matches($cmd, [regex]::Escape($fwd) + '/[^"''\s]+')) { $found += $t.Value }
        }
    }
    return $found
}

function Test-SameBytes {
    # Whether two files hold the same bytes. A missing file is a plain no, never a terminating error,
    # so a scaffold that wrote nothing shows as one FAIL line and not as a suite that died.
    param([string]$Left, [string]$Right)
    if (-not (Test-Path -LiteralPath $Left -PathType Leaf)) { return $false }
    if (-not (Test-Path -LiteralPath $Right -PathType Leaf)) { return $false }
    return ((Get-FileHash -LiteralPath $Left -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $Right -Algorithm SHA256).Hash)
}

function Get-LinesHolding {
    # The lines of a captured run that hold a fragment, for a pin that counts how often one was printed.
    param([string]$Text, [string]$Fragment)
    return @($Text -split "`r?`n" | Where-Object { $_.Contains($Fragment) })
}

function Set-ConsoleInputCodePage {
    # Puts the console input on a code page and returns the encoding to put back, or $null when this
    # host has no console to set. The caller says so in the transcript and runs under what it has.
    param([int]$CodePage)
    $before = [Console]::InputEncoding
    try { [Console]::InputEncoding = [Text.Encoding]::GetEncoding($CodePage) } catch { return $null }
    return $before
}

function Invoke-HookWithPayload {
    # A hook script run the way the harness runs it: the payload reaches its stdin as the bytes of a
    # file. Never through a PowerShell pipe. Windows PowerShell 5.1 opens a child's stdin with the
    # console input encoding, and on code page 65001 that puts a byte order mark in front of the
    # first byte: the hook reads something that is not json, fails open, and answers nothing.
    param([string]$Script, [string]$PayloadJson)
    $stem = Join-Path $script:Base ('payload-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    [IO.File]::WriteAllBytes("$stem.json", (New-Object Text.UTF8Encoding($false)).GetBytes($PayloadJson))
    $proc = Start-Process -FilePath $script:Python -ArgumentList ('"' + $Script + '"') -NoNewWindow -Wait -PassThru `
        -RedirectStandardInput "$stem.json" -RedirectStandardOutput "$stem.out" -RedirectStandardError "$stem.err"
    $text = ''
    foreach ($part in @("$stem.out", "$stem.err")) {
        if (Test-Path -LiteralPath $part -PathType Leaf) { $text += [IO.File]::ReadAllText($part) }
    }
    return [pscustomobject]@{ Code = $proc.ExitCode; Output = $text }
}
$realProfile = $env:USERPROFILE
$base        = New-KitSandbox 'kit-install-project'
$company     = Join-Path $base 'company-app'
$fresh       = Join-Path $base 'fresh-app'
$main        = Join-Path $base 'main-repo'
$worktree    = Join-Path $base 'wt-feature'
$rules       = 'Company rules: every push runs the gate. Do not commit to main.'
$ownDocs     = 'The team docs index. Owned by the team, not by any tool.'
$agentRules  = 'Team rules for every coding agent: run the linter before a push.'

try {
    New-Item -ItemType Directory -Force -Path $company, $fresh, $main | Out-Null
    Invoke-Git @('init', '-q', $company) | Out-Null
    Invoke-Git @('init', '-q', $fresh) | Out-Null
    Invoke-Git @('init', '-q', $main) | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $company '.git\hooks'), (Join-Path $company 'docs') | Out-Null
    Set-Content -LiteralPath (Join-Path $company 'CLAUDE.md') -Value $rules -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $company 'AGENTS.md') -Value $agentRules -Encoding UTF8
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
    Assert-Regex $out 'AGENTS\.md\s+kept as is' 'and to their AGENTS.md'
    Assert-Regex $out '\.git/hooks/pre-push\s+never touched' 'the preflight says the company hook is untouched'
    Assert-Regex $out '\.pre-commit-config\.yaml\s+never touched' 'and so is their pre-commit config'
    Assert-Match $out 'writes only AGENTS.md, CLAUDE.md, CLAUDE.project.md, .claude/, scripts/hooks/ and docs/' `
        'with -Sdd the promise names the hook scripts and docs/ too'
    Assert-True ((Get-Content -LiteralPath (Join-Path $company 'CLAUDE.md') -Raw).Contains($rules)) `
        'the company CLAUDE.md is untouched'
    Assert-True ((Get-Content -LiteralPath (Join-Path $company 'docs\README.md') -Raw).Contains($ownDocs)) `
        'the team docs index is untouched'
    Assert-True (Test-Path -LiteralPath (Join-Path $company 'CLAUDE.md.new')) 'the kit version landed as CLAUDE.md.new'
    Assert-True ((Get-Content -LiteralPath (Join-Path $company 'AGENTS.md') -Raw).Contains($agentRules)) `
        'the team AGENTS.md is untouched'
    Assert-True (Test-Path -LiteralPath (Join-Path $company 'AGENTS.md.new')) 'the kit AGENTS.md landed as AGENTS.md.new'
    $named = [regex]::Match(($out -join "`n"), 'AGENTS\.md\s+kept as is.*?python "([^"]+)" render "([^"]+)" "([^"]+)"')
    Assert-True ($named.Success -and $named.Groups[3].Value -eq (Join-Path $company 'AGENTS.md')) `
        'the preflight names the command that merges the kit sections into their AGENTS.md, by full path'
    # the command it names works on the result of this very run, from another folder: the kit sections join, theirs stay
    if ($named.Success) {
        & python $named.Groups[1].Value render $named.Groups[2].Value $named.Groups[3].Value | Out-Null
        Assert-True ($LASTEXITCODE -eq 0) "the named merge command succeeds (exit $LASTEXITCODE)"
    }
    $merged = Get-Content -LiteralPath (Join-Path $company 'AGENTS.md') -Raw
    Assert-True ($merged.Contains($agentRules) -and $merged.Contains('<!-- cb:rules -->')) `
        'after the merge their AGENTS.md holds their rules and the kit sections'
    Assert-True (Test-Path -LiteralPath (Join-Path $company 'docs\README.md.new')) 'and as docs/README.md.new'
    Assert-True ((Get-FileHash -LiteralPath (Join-Path $company '.git\hooks\pre-push') -Algorithm SHA256).Hash -eq $hookBefore) `
        'the company pre-push hook is untouched'
    Assert-True (Test-Path -LiteralPath (Join-Path $company '.claude\settings.local.json')) 'the project wiring landed'
    # the branch hooks run through Git Bash, which finds a bare powershell only on PATH
    $fullPs = ([IO.Path]::Combine($env:SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')).Replace([string][char]92, '/')
    $projSettings = Get-Content -LiteralPath (Join-Path $company '.claude\settings.local.json') -Raw
    Assert-True ($projSettings -notmatch '"command": "powershell([.]exe)? ') 'no project hook starts PowerShell by its PATH name'
    Assert-Match $projSettings ('"command": "' + $fullPs + ' -NoProfile') 'the project hooks start it by its full path'
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
    Assert-Match $out 'writes only AGENTS.md, CLAUDE.md, CLAUDE.project.md, .claude/ and scripts/hooks/' `
        'without -Sdd the promise names the hook scripts and not docs/'
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

    # ------------------------------------------ where a project takes plugins from
    Write-Host "`r`nphase 10, a scaffolded project points at the clone it was scaffolded from"
    $tracked = (Get-Content -LiteralPath (Join-Path $script:RepoRoot 'project-template\.claude\settings.json') -Raw)
    Assert-Match $tracked '<owner>/<repo>' 'the tracked template still carries the placeholder'
    Assert-Match $tracked '<pinned-commit>' 'and the placeholder commit beside it'
    $scaffold = Join-Path $fresh '.claude\settings.json'
    Assert-True (Test-Path -LiteralPath $scaffold) 'the scaffolded project got a settings.json of its own'
    # Guarded, because a failure above must stay one FAIL line: reading a file that is not there
    # would be a terminating error and would take the rest of this phase with it.
    $written = if (Test-Path -LiteralPath $scaffold) { (Get-Content -LiteralPath $scaffold -Raw) } else { '' }
    $parsed  = if ($written) { ($written | ConvertFrom-Json) } else { $null }
    Assert-True (-not $written.Contains('<owner>/<repo>')) 'no placeholder survived into the copy that was written'
    Assert-True (-not $written.Contains('<pinned-commit>')) 'neither did the commit placeholder'
    $source = if ($parsed) { $parsed.extraKnownMarketplaces.'claude-base'.source } else { $null }
    Assert-Regex ([string]$source.repo) '^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$' 'the copy carries a real owner/repo pair'
    Assert-Regex ([string]$source.sha) '^[0-9a-f]{40}$' 'and the commit of the clone it came from'
    $enabled = if ($parsed) { $parsed.enabledPlugins } else { $null }
    Assert-True ($enabled.'groundwork@claude-base' -eq $true) 'the single-channel plugin is enabled for the project'
    Assert-True (-not (@($enabled.PSObject.Properties.Name) -contains 'delivery@claude-base')) `
        'and the plugins the installer also copies are not, so nothing arrives twice'
    Assert-True (-not $written.Contains('filter-gradle-output.py')) 'and a plain scaffold wires no stack hook beside the plugin source'

    # ------------------------------------- a kit tree that cannot name its source
    Write-Host "`r`nphase 11, a kit tree with no readable origin writes no plugin source at all"
    # A downloaded archive, or a clone whose remote is not called origin: the placeholders cannot
    # be filled, and a project pointed at `<owner>/<repo>` with the plugin enabled against it is
    # worse than a project with no plugin source. Staged as a copy carrying only what the project
    # scope reads, and with no .git of any kind.
    $bare = Join-Path $base 'kit-no-git'
    New-Item -ItemType Directory -Force -Path (Join-Path $bare 'claude') | Out-Null
    Copy-Item -LiteralPath (Join-Path $script:RepoRoot 'install.ps1') -Destination $bare
    Copy-Item -LiteralPath (Join-Path $script:RepoRoot 'install') -Destination $bare -Recurse
    Copy-Item -LiteralPath (Join-Path $script:RepoRoot 'project-template') -Destination $bare -Recurse
    Copy-Item -LiteralPath (Join-Path $script:RepoRoot 'claude\hooks') -Destination (Join-Path $bare 'claude') -Recurse
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $bare '.git'))) 'the staged tree carries no git metadata'
    $orphan = Join-Path $base 'orphan-app'
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = ((& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $bare 'install.ps1') `
                    -Project $orphan 2>&1) | Out-String)
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
    Assert-True ($code -eq 0) ("the run out of that tree still scaffolds the project (exit $code)")
    Assert-Match $out 'no readable git origin' 'and says why the project gets no plugin source'
    $orphanSettings = Join-Path $orphan '.claude\settings.json'
    Assert-True (Test-Path -LiteralPath $orphanSettings) 'the project still gets its settings.json'
    $orphanText = if (Test-Path -LiteralPath $orphanSettings) { (Get-Content -LiteralPath $orphanSettings -Raw) } else { '' }
    Assert-True (-not $orphanText.Contains('extraKnownMarketplaces')) 'no plugin source is written'
    Assert-True (-not $orphanText.Contains('enabledPlugins')) 'and no plugin is enabled against one'
    Assert-True (-not $orphanText.Contains('<owner>/<repo>')) 'no placeholder was left behind either'
    $orphanParsed = if ($orphanText) { ($orphanText | ConvertFrom-Json) } else { $null }
    Assert-True ($null -ne $orphanParsed) 'what was written is still valid json'
    Assert-True (-not $orphanText.Contains('filter-gradle-output.py')) 'and, a plain scaffold, it wires no stack hook'

    # The same case one level out, and the dangerous one: the kit tree unpacked inside somebody
    # else's clone. `git -C` walks up until it finds a repository, so without a guard the
    # ancestor's remote and commit are read as the kit's own and written into the scaffolded
    # project: a private slug and a foreign sha, in a file the installer invites them to commit.
    # The host here is a name no forge owns, so the fixture cannot itself trip the push guard.
    $outerRepo = Join-Path $base 'host-repo'
    New-Item -ItemType Directory -Force -Path $outerRepo | Out-Null
    Invoke-Git @('init', '-q', $outerRepo) | Out-Null
    Invoke-Git @('-C', $outerRepo, 'remote', 'add', 'origin',
                 'https://example.invalid/acme-private/secret-monorepo.git') | Out-Null
    Set-Content -LiteralPath (Join-Path $outerRepo 'README.md') -Value 'a repository of their own' -Encoding UTF8
    Invoke-Git @('-C', $outerRepo, 'add', '-A') | Out-Null
    Invoke-Git @('-C', $outerRepo, '-c', 'user.name=suite', '-c', 'user.email=suite-identity',
                 'commit', '-q', '-m', 'first') | Out-Null
    $ancestorSha = (Invoke-Git @('-C', $outerRepo, 'rev-parse', 'HEAD')).Trim()
    Assert-Regex $ancestorSha '^[0-9a-f]{40}$' 'the host repository has a commit of its own'
    Copy-Item -LiteralPath $bare -Destination (Join-Path $outerRepo 'kit') -Recurse
    $nested = Join-Path $base 'nested-app'
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = ((& powershell -NoProfile -ExecutionPolicy Bypass `
                    -File (Join-Path $outerRepo 'kit\install.ps1') -Project $nested 2>&1) | Out-String)
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
    Assert-True ($code -eq 0) ("the run out of a tree inside another repository succeeds (exit $code)")
    Assert-Match $out 'no readable git origin' 'and warns, because that repository is not the kit'
    $nestedSettings = Join-Path $nested '.claude\settings.json'
    Assert-True (Test-Path -LiteralPath $nestedSettings) 'the project still gets its settings.json'
    $nestedText = if (Test-Path -LiteralPath $nestedSettings) { (Get-Content -LiteralPath $nestedSettings -Raw) } else { '' }
    Assert-True (-not $nestedText.Contains('extraKnownMarketplaces')) 'no plugin source is written'
    Assert-True (-not $nestedText.Contains('enabledPlugins')) 'and no plugin is enabled against one'
    Assert-True (-not $nestedText.Contains('acme-private')) 'the host repository slug is nowhere in it'
    Assert-True (-not $nestedText.Contains('secret-monorepo')) 'nor the rest of that slug'
    Assert-True (-not $nestedText.Contains($ancestorSha)) 'nor the commit of the repository it sat in'

    # ------------------------------------------------ no stack hook by default --
    Write-Host "`r`nphase 12, a plain scaffold wires no stack hook, and the two settings templates stay in step"
    $bs = [string][char]92
    $kitHookDir = Join-Path $script:RepoRoot 'project-template\scripts\hooks'
    $kitRunner  = Join-Path $kitHookDir 'run-logged.py'
    $kitFilter  = Join-Path $kitHookDir 'filter-gradle-output.py'
    # The installer picks one whole template: settings.json for a plain run, settings.gradle.json with
    # -GradleHook. Nothing is cut out of a rendered file, so the two have to say the same thing
    # everywhere but in the hooks key, and these lines are what keeps them there.
    $gradleTplFile = Join-Path $script:RepoRoot 'project-template\.claude\settings.gradle.json'
    $gradleTpl     = if (Test-Path -LiteralPath $gradleTplFile -PathType Leaf) { (Get-Content -LiteralPath $gradleTplFile -Raw) } else { '' }
    Assert-True ($gradleTpl -ne '') 'ProjectScaffoldTemplatesInStepTest: the kit carries a gradle settings template beside the plain one'
    $plainObj  = $null
    $gradleObj = $null
    try { $plainObj = ($tracked | ConvertFrom-Json) } catch { $plainObj = $null }
    if ($gradleTpl) { try { $gradleObj = ($gradleTpl | ConvertFrom-Json) } catch { $gradleObj = $null } }
    Assert-True ($null -ne $plainObj -and $null -ne $gradleObj) 'ProjectScaffoldTemplatesInStepTest: both templates parse as json'
    $plainNames  = if ($plainObj) { @($plainObj.PSObject.Properties.Name) } else { @() }
    $gradleNames = if ($gradleObj) { @($gradleObj.PSObject.Properties.Name) } else { @() }
    Assert-True ($plainNames.Count -gt 0 -and -not ($plainNames -contains 'hooks')) `
        'ProjectScaffoldTemplatesInStepTest: the plain template has no hooks key'
    Assert-True (($gradleNames -join ', ') -eq ((@($plainNames) + 'hooks') -join ', ')) `
        "ProjectScaffoldTemplatesInStepTest: the gradle template holds the same keys, and hooks after them (plain: $($plainNames -join ', '); gradle: $($gradleNames -join ', '))"
    foreach ($key in $plainNames) {
        $left  = ConvertTo-Json ($plainObj.$key) -Depth 10 -Compress
        $right = if ($gradleObj) { ConvertTo-Json ($gradleObj.$key) -Depth 10 -Compress } else { '' }
        Assert-True ($left -eq $right) "ProjectScaffoldTemplatesInStepTest: the two templates agree on $key"
    }
    $gradleHookKinds = if ($gradleObj -and $gradleObj.hooks) { @($gradleObj.hooks.PSObject.Properties.Name) -join ', ' } else { '' }
    Assert-True ($gradleHookKinds -eq 'PreToolUse' -and ([regex]::Matches($gradleTpl, '"command"\s*:')).Count -eq 1 -and
                 $gradleTpl.Contains('scripts/hooks/filter-gradle-output.py')) `
        'ProjectScaffoldTemplatesInStepTest: and its hooks key is the one gradle filter hook'

    $hookApp = Join-Path $base 'hook-app'
    New-Item -ItemType Directory -Force -Path $hookApp | Out-Null
    Invoke-Git @('init', '-q', $hookApp) | Out-Null
    $out = Invoke-Install @('-Project', $hookApp)
    Assert-Exit 0 'ProjectScaffoldNoStackHookByDefaultTest: the plain project install succeeds'
    $plain = Read-ScaffoldSettings $hookApp
    Assert-True ($null -ne $plain.Parsed) 'ProjectScaffoldNoStackHookByDefaultTest: the scaffolded settings.json parses as json'
    Assert-True ($plain.Keys.Count -gt 0 -and -not ($plain.Keys -contains 'hooks')) `
        "ProjectScaffoldNoStackHookByDefaultTest: and it has no hooks key (keys: $($plain.Keys -join ', '))"
    Assert-True (-not $plain.Text.Contains('filter-gradle-output.py')) `
        'ProjectScaffoldNoStackHookByDefaultTest: nothing in it names the gradle filter'
    foreach ($settingsName in @('settings.json', 'settings.local.json')) {
        Assert-True (Test-Path -LiteralPath (Join-Path $hookApp ".claude\$settingsName") -PathType Leaf) `
            "ProjectScaffoldNoStackHookByDefaultTest: $settingsName was scaffolded"
    }
    # Every command of both settings files, reduced to the files it names under the project: each one
    # has to be there. At least two are named (the branch hooks), so the loop cannot pass on nothing.
    $namedFiles = @(Get-HookNamedFiles $hookApp)
    Assert-True ($namedFiles.Count -ge 2) "ProjectScaffoldNoStackHookByDefaultTest: the settings name files under the project ($($namedFiles.Count) found)"
    foreach ($file in @($namedFiles | Sort-Object -Unique)) {
        Assert-True (Test-Path -LiteralPath $file -PathType Leaf) "ProjectScaffoldNoStackHookByDefaultTest: a hook command names $file and it exists"
    }
    Assert-True (Test-SameBytes (Join-Path $hookApp 'scripts\hooks\run-logged.py') $kitRunner) `
        'ProjectScaffoldNoStackHookByDefaultTest: the runner CLAUDE.md names is there, byte for byte the kit file'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $hookApp 'scripts\hooks\filter-gradle-output.py'))) `
        'ProjectScaffoldNoStackHookByDefaultTest: no gradle filter is written'
    Assert-True (-not ($out -match 'filter-gradle-output')) 'ProjectScaffoldNoStackHookByDefaultTest: and no line of the run names one'

    # ------------------------------------------------------ the gradle opt-in --
    Write-Host "`r`nphase 13, -GradleHook wires the gradle hook and carries both scripts it runs"
    $gradleApp = Join-Path $base 'gradle-app'
    New-Item -ItemType Directory -Force -Path $gradleApp | Out-Null
    Invoke-Git @('init', '-q', $gradleApp) | Out-Null
    $out = Invoke-Install @('-Project', $gradleApp, '-GradleHook')
    Assert-Exit 0 'ProjectScaffoldGradleHookOptInTest: the install with -GradleHook succeeds'
    $filter = Join-Path $gradleApp 'scripts\hooks\filter-gradle-output.py'
    $runner = Join-Path $gradleApp 'scripts\hooks\run-logged.py'
    Assert-True (Test-SameBytes $filter $kitFilter) 'ProjectScaffoldGradleHookOptInTest: the scaffolded filter is byte for byte the template filter'
    Assert-True (Test-SameBytes $runner $kitRunner) 'ProjectScaffoldGradleHookOptInTest: and the runner beside it is byte for byte the template runner'
    $wired = Read-ScaffoldSettings $gradleApp
    Assert-True ($null -ne $wired.Parsed) 'ProjectScaffoldGradleHookOptInTest: the scaffolded settings.json parses as json'
    Assert-True ((Get-FilterCommandCount $wired) -eq 1) 'ProjectScaffoldGradleHookOptInTest: and one hook command runs the gradle filter of the project'
    Assert-True (($wired.Keys -join ', ') -eq ((@($plain.Keys) + 'hooks') -join ', ')) `
        "ProjectScaffoldGradleHookOptInTest: beside the hook it holds what a plain scaffold holds (keys: $($wired.Keys -join ', '))"
    $namedFiles = @(Get-HookNamedFiles $gradleApp)
    Assert-True (@($namedFiles | Where-Object { $_ -like '*/scripts/hooks/filter-gradle-output.py' }).Count -eq 1) `
        'ProjectScaffoldGradleHookOptInTest: the gradle filter is one of the files the settings name'
    foreach ($file in @($namedFiles | Sort-Object -Unique)) {
        Assert-True (Test-Path -LiteralPath $file -PathType Leaf) "ProjectScaffoldGradleHookOptInTest: a hook command names $file and it exists"
    }
    Assert-Regex $out 'scripts[\\/]hooks[\\/]filter-gradle-output\.py' 'ProjectScaffoldGradleHookOptInTest: the plan listed the gradle filter'

    # The hook itself, run the way the harness runs it: the payload on stdin, the script by its path.
    # Both payloads go in under both console code pages. Fed through a pipe, the json arrived behind a
    # byte order mark on 65001, the hook failed open, and every line about a silent hook passed on a
    # payload it had never parsed.
    $projectFwd = $gradleApp.Replace($bs, '/').TrimEnd('/')
    $lsPayload = (@{ session_id = 'suite'; cwd = $gradleApp; hook_event_name = 'PreToolUse'; tool_name = 'Bash';
                     tool_input = @{ command = 'ls' }; tool_use_id = 'toolu_suite' } | ConvertTo-Json -Depth 5 -Compress)
    $gradlePayload = (@{ session_id = 'suite'; cwd = $gradleApp; hook_event_name = 'PreToolUse'; tool_name = 'Bash';
                         tool_input = @{ command = './gradlew test' }; tool_use_id = 'toolu_suite' } | ConvertTo-Json -Depth 5 -Compress)
    foreach ($codePage in @(437, 65001)) {
        $pin = "ProjectScaffoldGradleHookOptInTest, code page $codePage"
        $before = Set-ConsoleInputCodePage $codePage
        if ($null -eq $before) { Write-Host "  note  this host has no console to put on code page $codePage, the payloads run under its own" }
        try {
            $plainCall  = Invoke-HookWithPayload $filter $lsPayload
            $gradleCall = Invoke-HookWithPayload $filter $gradlePayload
        } finally {
            if ($null -ne $before) { [Console]::InputEncoding = $before }
        }
        Assert-True ($plainCall.Code -eq 0) "${pin}: the gradle filter exits 0 on a plain ls (exit $($plainCall.Code))"
        Assert-True ($plainCall.Output -notmatch '(?i)deny') "${pin}: and prints no deny"
        Assert-True ($plainCall.Output.Trim() -eq '') "${pin}: a command that is not gradle is left alone, no output at all"
        Assert-True ($gradleCall.Code -eq 0) "${pin}: the gradle filter exits 0 on ./gradlew test (exit $($gradleCall.Code))"
        $answer = $null
        if ($gradleCall.Output.Trim()) { try { $answer = ($gradleCall.Output | ConvertFrom-Json).hookSpecificOutput } catch { $answer = $null } }
        Assert-True ($answer.permissionDecision -eq 'allow') "${pin}: a gradle command is answered with allow"
        $rewritten = ([string]$answer.updatedInput.command).Replace($bs, '/')
        Assert-True ($rewritten.ToLowerInvariant().Contains(($projectFwd + '/scripts/hooks/run-logged.py').ToLowerInvariant())) `
            "${pin}: and a command that names the run-logged.py of the project"
        Assert-True ($rewritten.Contains("'./gradlew test'")) "${pin}: which still carries the gradle call"
    }

    # A second look at either scaffold lists no hook script as kept: both are the kit's files.
    $again = Invoke-Install @('-Project', $hookApp, '-DryRun')
    Assert-Exit 0 'ProjectScaffoldPreflightNamesHookScriptsTest: a second look at a plain scaffold succeeds'
    Assert-True (-not ($again -match 'scripts/hooks/\S+\.py\s+(kept as is|overwritten)')) `
        'ProjectScaffoldPreflightNamesHookScriptsTest: a hook script that is the kit file is not listed as kept'
    $again = Invoke-Install @('-Project', $gradleApp, '-GradleHook', '-DryRun')
    Assert-Exit 0 'ProjectScaffoldPreflightNamesHookScriptsTest: a second look at a gradle scaffold succeeds'
    Assert-True (-not ($again -match 'scripts/hooks/\S+\.py\s+(kept as is|overwritten)')) `
        'ProjectScaffoldPreflightNamesHookScriptsTest: and lists neither of its two scripts as kept'
    Assert-True (-not ($again -match 'refused')) 'ProjectScaffoldGradleHookOptInTest: scripts the kit wrote do not refuse the switch'

    # The same switch from a kit tree with no origin (the copy phase 11 staged): the plugin keys are
    # left out, the hook is wired, and what is written still parses.
    $bareInstaller = Join-Path $bare 'install.ps1'
    $orphanGradle  = Join-Path $base 'orphan-gradle-app'
    $out = Invoke-InstallFrom $bareInstaller @('-Project', $orphanGradle, '-GradleHook')
    Assert-Exit 0 'ProjectScaffoldGradleHookOptInTest: a tree with no origin scaffolds with -GradleHook'
    Assert-Match $out 'no readable git origin' 'ProjectScaffoldGradleHookOptInTest: it still warns about the plugin source'
    $settings = Read-ScaffoldSettings $orphanGradle
    $leftKeys = @($gradleNames | Where-Object { $_ -notin @('extraKnownMarketplaces', 'enabledPlugins') }) -join ', '
    Assert-True ($null -ne $settings.Parsed) 'ProjectScaffoldGradleHookOptInTest: without the plugin keys the settings still parse as json'
    Assert-True ($leftKeys -ne '' -and ($settings.Keys -join ', ') -eq $leftKeys) `
        "ProjectScaffoldGradleHookOptInTest: and hold the rest of the gradle template (keys: $($settings.Keys -join ', '))"
    Assert-True ((Get-FilterCommandCount $settings) -eq 1) 'ProjectScaffoldGradleHookOptInTest: with the hook wired'

    # Owned by the kit per the record: a runner the kit wrote and nobody touched is refreshed by a later
    # kit version, never taken for the adopter's, so the switch is not refused and the hook stays wired.
    $laterRunner = Join-Path $bare 'project-template\scripts\hooks\run-logged.py'
    [IO.File]::AppendAllText($laterRunner, "`n# a later kit version`n")
    $out = Invoke-InstallFrom $bareInstaller @('-Project', $gradleApp, '-GradleHook')
    Assert-Exit 0 'ProjectScaffoldGradleHookOptInTest: a later kit version installs over a gradle scaffold'
    Assert-True (Test-SameBytes $runner $laterRunner) 'ProjectScaffoldGradleHookOptInTest: the runner the kit wrote is refreshed to it'
    Assert-True (-not ($out -match 'refused')) 'ProjectScaffoldGradleHookOptInTest: it is not taken for a file of the adopter'
    Assert-True ((Get-FilterCommandCount (Read-ScaffoldSettings $gradleApp)) -eq 1) 'ProjectScaffoldGradleHookOptInTest: and the hook stays wired'

    # ------------------------------------------------- -LocalOnly and the scripts --
    Write-Host "`r`nphase 14, -LocalOnly hides the copied hook scripts and keeps an adopter's own"
    $ownRunner = 'print("the team runner, owned by the team")'
    $localApp = Join-Path $base 'local-hooks-app'
    New-Item -ItemType Directory -Force -Path (Join-Path $localApp 'scripts\hooks') | Out-Null
    Invoke-Git @('init', '-q', $localApp) | Out-Null
    $ownRunnerFile = Join-Path $localApp 'scripts\hooks\run-logged.py'
    [IO.File]::WriteAllText($ownRunnerFile, $ownRunner, (New-Object Text.UTF8Encoding($false)))
    $ownRunnerHash = (Get-FileHash -LiteralPath $ownRunnerFile -Algorithm SHA256).Hash
    $out = Invoke-Install @('-Project', $localApp, '-LocalOnly')
    Assert-Exit 0 'the -LocalOnly install over a repository with its own runner succeeds'
    Assert-True ((Get-FileHash -LiteralPath $ownRunnerFile -Algorithm SHA256).Hash -eq $ownRunnerHash) `
        'ProjectScaffoldLocalOnlyHookScriptsTest: the adopter run-logged.py is kept byte for byte'
    $proposal = "$ownRunnerFile.new"
    Assert-True (Test-Path -LiteralPath $proposal) 'ProjectScaffoldLocalOnlyHookScriptsTest: the kit version sits beside it as run-logged.py.new'
    Assert-True (Test-SameBytes $proposal $kitRunner) 'ProjectScaffoldLocalOnlyHookScriptsTest: and holds the kit bytes'
    Assert-True (Test-GitIgnored $localApp 'scripts/hooks/run-logged.py.new') `
        'ProjectScaffoldLocalOnlyHookScriptsTest: git ignores the proposal beside the adopter runner'
    Assert-True (-not (Test-GitIgnored $localApp 'scripts/hooks/run-logged.py')) `
        'ProjectScaffoldLocalOnlyHookScriptsTest: the adopter runner stays visible to git'
    Assert-Regex $out 'scripts/hooks/run-logged\.py\s+kept as is, the kit version lands beside it' `
        'ProjectScaffoldPreflightNamesHookScriptsTest: the preflight lists the kept run-logged.py, with the .new this run writes'
    Assert-True (-not ($out -match 'this looks like a fresh repository')) `
        'ProjectScaffoldPreflightNamesHookScriptsTest: and does not call a repository with its own run-logged.py fresh'
    # a plain run wires nothing, so a runner of their own is called by no hook and no line has to warn about one
    $localSettings = Read-ScaffoldSettings $localApp
    Assert-True ($null -ne $localSettings.Parsed -and -not $localSettings.Text.Contains('filter-gradle-output.py')) `
        'ProjectScaffoldLocalOnlyHookScriptsTest: the settings beside the adopter runner parse and do not wire the gradle filter'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $localApp 'scripts\hooks\filter-gradle-output.py'))) `
        'ProjectScaffoldLocalOnlyHookScriptsTest: and no gradle filter is written beside it'
    Assert-True (-not ($out -match 'gradle output hook|is yours')) 'ProjectScaffoldLocalOnlyHookScriptsTest: no line of the run speaks of a hook it did not wire'
    # a repository with nothing of theirs in the way, scaffolded with the hook: both copies are ignored and git status is empty
    $localFresh = Join-Path $base 'local-fresh-app'
    New-Item -ItemType Directory -Force -Path $localFresh | Out-Null
    Invoke-Git @('init', '-q', $localFresh) | Out-Null
    $out = Invoke-Install @('-Project', $localFresh, '-GradleHook', '-LocalOnly')
    Assert-Exit 0 'the -LocalOnly install with -GradleHook into a fresh repository succeeds'
    foreach ($hookScript in @('filter-gradle-output.py', 'run-logged.py')) {
        Assert-True (Test-GitIgnored $localFresh "scripts/hooks/$hookScript") "ProjectScaffoldLocalOnlyHookScriptsTest: git ignores the copied $hookScript"
    }
    Assert-True ((Invoke-Git @('-C', $localFresh, 'status', '--short')).Trim() -eq '') `
        'ProjectScaffoldLocalOnlyHookScriptsTest: git status is empty after the -LocalOnly install'
    $dry = Invoke-Install @('-Project', (Join-Path $base 'dry-hooks-app'), '-DryRun')
    Assert-Regex $dry 'scripts[\\/]hooks[\\/]run-logged\.py' '-DryRun lists the logged runner in its plan'
    Assert-True (-not ($dry -match 'filter-gradle-output')) 'and, a plain run, no gradle filter'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $base 'dry-hooks-app\scripts'))) 'and writes none of them'

    # ------------------------------------------- the opt-in refuses a foreign file --
    Write-Host "`r`nphase 15, -GradleHook refuses, with nothing written, when a hook script would not be the kit's"
    # The hook runs filter-gradle-output.py, which sends gradle through the run-logged.py beside it. A
    # file of the adopter's at either name would be run by the hook, and a foreign runner can answer
    # green and never start gradle. The switch is an explicit request, so it is refused, in one line,
    # before the preflight, the plan or any write. A script the kit wrote and the adopter then edited
    # is the adopter's too: the record no longer matches it.
    $ownScript = 'print("a team script, owned by the team")'
    $editedApp = Join-Path $base 'edited-app'
    $out = Invoke-Install @('-Project', $editedApp, '-GradleHook')
    Assert-Exit 0 'ProjectScaffoldGradleHookRefusesForeignTest: the scaffold the adopter will edit installs'
    $editedRunner = Join-Path $editedApp 'scripts\hooks\run-logged.py'
    if (Test-Path -LiteralPath $editedRunner -PathType Leaf) { [IO.File]::AppendAllText($editedRunner, "`n# a line the adopter added`n") }
    foreach ($case in @(
            @{ Script = 'run-logged.py'; App = 'own-runner-app'; What = 'an own run-logged.py' },
            @{ Script = 'filter-gradle-output.py'; App = 'own-filter-app'; What = 'an own filter-gradle-output.py' },
            @{ Script = 'run-logged.py'; App = 'edited-app'; What = 'a kit-written run-logged.py the adopter edited' })) {
        $pin  = 'ProjectScaffoldGradleHookRefusesForeignTest'
        $name = $case.Script
        $what = $case.What
        $app  = Join-Path $base $case.App
        $own  = Join-Path $app "scripts\hooks\$name"
        if (-not (Test-Path -LiteralPath $own -PathType Leaf)) {
            New-Item -ItemType Directory -Force -Path (Join-Path $app 'scripts\hooks') | Out-Null
            [IO.File]::WriteAllText($own, $ownScript, (New-Object Text.UTF8Encoding($false)))
        }
        $ownHash    = (Get-FileHash -LiteralPath $own -Algorithm SHA256).Hash
        $beforeTree = Get-TreeHash $app
        $beforeHome = Get-TreeHash $script:KitHome

        $out = Invoke-Install @('-Project', $app, '-GradleHook')
        Assert-True ($script:LastExit -ne 0) "${pin}: over $what the switch is refused (exit $($script:LastExit))"
        Assert-Match $out 'refused: -GradleHook would wire a hook that runs' "${pin}: over $what the run says it is refused, and why"
        Assert-Match $out $own "${pin}: over $what the refusal names the file"
        Assert-Match $out '-Force' "${pin}: over $what it names the way out"
        Assert-True (-not ($out -match '\.new')) "${pin}: over $what it promises no .new, a refused run writes none"
        Assert-True (-not ($out -match 'Adoption preflight|summary  |plan \(dry run')) "${pin}: over $what it stops before the preflight and the plan"
        Assert-True (@($out.Trim() -split "`r?`n").Count -eq 1) "${pin}: over $what the refusal is one line"
        Assert-True ((Get-TreeHash $app) -eq $beforeTree) "${pin}: over $what the project tree is byte for byte the one before"
        Assert-True ((Get-TreeHash $script:KitHome) -eq $beforeHome) "${pin}: over $what the kit home is too"

        $dry = Invoke-Install @('-Project', $app, '-GradleHook', '-DryRun')
        Assert-True ($script:LastExit -ne 0) "${pin}: over $what -DryRun is refused the same way (exit $($script:LastExit))"
        Assert-True ($dry.Trim() -eq $out.Trim()) "${pin}: over $what -DryRun prints the same refusal"
        Assert-True ((Get-TreeHash $app) -eq $beforeTree) "${pin}: over $what -DryRun writes nothing either"

        # One more kept file for the -Force run, so its preflight line is held for a file that is not a hook script.
        $ownRules = Join-Path $app 'CLAUDE.md'
        $hadRules = Test-Path -LiteralPath $ownRules -PathType Leaf
        if (-not $hadRules) { [IO.File]::WriteAllText($ownRules, 'team rules', (New-Object Text.UTF8Encoding($false))) }
        $out = Invoke-Install @('-Project', $app, '-GradleHook', '-Force')
        Assert-Exit 0 "${pin}: over $what -Force lets the run proceed"
        if ($hadRules) {
            Assert-Regex $out 'CLAUDE\.md\s+kept as is, already matches the kit' "${pin}: over $what a CLAUDE.md that is the kit file still reads as matching under -Force"
            Assert-Regex $out '\.claude\s+under -Force a file in it' "${pin}: over $what the preflight says what -Force does inside an existing .claude"
            Assert-True (-not ($out -match '\.claude\s+kept as is, the kit version lands')) "${pin}: over $what it promises no .new for that folder"
        } else {
            Assert-Regex $out 'CLAUDE\.md\s+overwritten by -Force' "${pin}: over $what the preflight says an own CLAUDE.md is overwritten too"
            Assert-True ((Test-SameBytes $ownRules (Join-Path $script:RepoRoot 'project-template\CLAUDE.md')) -and -not (Test-Path -LiteralPath "$ownRules.new")) `
                "${pin}: over $what that CLAUDE.md is the kit file after the run, with no CLAUDE.md.new written"
        }
        Assert-True (Test-SameBytes $own (Join-Path $kitHookDir $name)) "${pin}: over $what -Force puts the kit file in its place"
        $copy = Get-BackupCopy (Get-LastStamp) $own
        Assert-True ((Test-Path -LiteralPath $copy -PathType Leaf) -and (Get-FileHash -LiteralPath $copy -Algorithm SHA256).Hash -eq $ownHash) `
            "${pin}: over $what the backup holds the adopter bytes"
        Assert-Regex $out ('scripts/hooks/' + [regex]::Escape($name) + '\s+overwritten by -Force') "${pin}: over $what the preflight says overwritten"
        Assert-True (-not ($out -match ('scripts/hooks/' + [regex]::Escape($name) + '\s+kept as is'))) "${pin}: over $what it does not call the file kept"
        Assert-True (-not (Test-Path -LiteralPath "$own.new")) "${pin}: over $what no .new is written beside the forced file"
        Assert-True ((Get-FilterCommandCount (Read-ScaffoldSettings $app)) -eq 1) "${pin}: over $what the hook is wired, to scripts that are the kit's"
    }

    # A plain run beside a filter of their own installs no file of that name: the preflight lists it
    # as kept, and promises no .new that the run would never write.
    $plainOwn = Join-Path $base 'plain-own-filter-app'
    New-Item -ItemType Directory -Force -Path (Join-Path $plainOwn 'scripts\hooks') | Out-Null
    $plainOwnFilter = Join-Path $plainOwn 'scripts\hooks\filter-gradle-output.py'
    [IO.File]::WriteAllText($plainOwnFilter, $ownScript, (New-Object Text.UTF8Encoding($false)))
    $plainOwnHash = (Get-FileHash -LiteralPath $plainOwnFilter -Algorithm SHA256).Hash
    $out = Invoke-Install @('-Project', $plainOwn)
    Assert-Exit 0 'ProjectScaffoldPreflightNamesHookScriptsTest: a plain run beside an own filter succeeds'
    Assert-Regex $out 'scripts/hooks/filter-gradle-output\.py\s+kept as is, this run writes no file of that name' `
        'ProjectScaffoldPreflightNamesHookScriptsTest: the preflight lists the kept filter-gradle-output.py and says what this run does'
    Assert-True (-not ($out -match 'this looks like a fresh repository')) `
        'ProjectScaffoldPreflightNamesHookScriptsTest: and does not call a repository with its own filter-gradle-output.py fresh'
    Assert-True (-not ($out -match 'filter-gradle-output\.py\s+kept as is, the kit version lands')) `
        'ProjectScaffoldPreflightNamesHookScriptsTest: it promises no .new for a file the run does not install'
    Assert-True (-not (Test-Path -LiteralPath "$plainOwnFilter.new")) 'ProjectScaffoldPreflightNamesHookScriptsTest: and none is written'
    Assert-True ((Get-FileHash -LiteralPath $plainOwnFilter -Algorithm SHA256).Hash -eq $plainOwnHash) `
        'ProjectScaffoldPreflightNamesHookScriptsTest: the adopter filter-gradle-output.py is kept byte for byte'
    $settings = Read-ScaffoldSettings $plainOwn
    Assert-True ($null -ne $settings.Parsed -and -not ($settings.Keys -contains 'hooks')) `
        'ProjectScaffoldNoStackHookByDefaultTest: beside an own filter the plain scaffold still wires nothing'
    # -Force changes nothing for that file: the run still installs no version of it, so it is neither
    # overwritten nor called overwritten.
    $out = Invoke-Install @('-Project', $plainOwn, '-Force')
    Assert-Exit 0 'ProjectScaffoldPreflightNamesHookScriptsTest: a plain run with -Force beside an own filter succeeds'
    Assert-Regex $out 'scripts/hooks/filter-gradle-output\.py\s+kept as is, this run writes no file of that name' `
        'ProjectScaffoldPreflightNamesHookScriptsTest: under -Force the preflight still lists that filter as kept, the run writes no file of that name'
    Assert-True ((Get-FileHash -LiteralPath $plainOwnFilter -Algorithm SHA256).Hash -eq $plainOwnHash -and -not (Test-Path -LiteralPath "$plainOwnFilter.new")) `
        'ProjectScaffoldPreflightNamesHookScriptsTest: and the adopter filter-gradle-output.py is still theirs, with no .new beside it'

    # A refused run leaves even an unreadable manifest where it is. A run that goes on moves it aside
    # before its preflight, which is the one write that comes ahead of everything else.
    $manifestFile  = Join-Path $script:KitHome '.kit-manifest.json'
    $manifestBytes = [IO.File]::ReadAllBytes($manifestFile)
    $brokenApp = Join-Path $base 'broken-record-app'
    New-Item -ItemType Directory -Force -Path (Join-Path $brokenApp 'scripts\hooks') | Out-Null
    [IO.File]::WriteAllText((Join-Path $brokenApp 'scripts\hooks\run-logged.py'), $ownScript, (New-Object Text.UTF8Encoding($false)))
    try {
        [IO.File]::WriteAllText($manifestFile, '{ not json')
        $beforeHome = Get-TreeHash $script:KitHome
        $out = Invoke-Install @('-Project', $brokenApp, '-GradleHook')
        Assert-True ($script:LastExit -ne 0) "ProjectScaffoldGradleHookRefusesForeignTest: with a manifest that cannot be read the switch is still refused (exit $($script:LastExit))"
        Assert-True (@($out.Trim() -split "`r?`n").Count -eq 1 -and $out -match 'refused: -GradleHook would wire') `
            'ProjectScaffoldGradleHookRefusesForeignTest: in one line, ahead of the warning about that manifest'
        Assert-True ((Get-TreeHash $script:KitHome) -eq $beforeHome) `
            'ProjectScaffoldGradleHookRefusesForeignTest: and the manifest is left where it is, not moved aside'
    } finally {
        [IO.File]::WriteAllBytes($manifestFile, $manifestBytes)
    }

    # ------------------------------------- what the switch needs from the kit tree --
    Write-Host "`r`nphase 16, -GradleHook needs its scripts and its template in the kit tree, and it needs -Project"
    # The round 0 failure was a hook wired to a script nobody copied. A kit tree that lost one of the
    # files the switch installs is refused the same way a foreign script is, before anything is written.
    $lacking = Join-Path $base 'kit-lacking'
    Copy-Item -LiteralPath $bare -Destination $lacking -Recurse
    $lackInstaller = Join-Path $lacking 'install.ps1'
    $caseNumber = 0
    foreach ($rel in @('scripts\hooks\filter-gradle-output.py', 'scripts\hooks\run-logged.py', '.claude\settings.gradle.json')) {
        $pin  = 'ProjectScaffoldGradleHookNeedsKitScriptsTest'
        $leaf = Split-Path -Leaf $rel
        $gone = Join-Path $lacking "project-template\$rel"
        $held = Join-Path $base "held-$leaf"
        if (Test-Path -LiteralPath $gone -PathType Leaf) { Move-Item -LiteralPath $gone -Destination $held }
        $caseNumber++
        $app = Join-Path $base "lack-app-$caseNumber"
        $beforeHome = Get-TreeHash $script:KitHome
        $out = Invoke-InstallFrom $lackInstaller @('-Project', $app, '-GradleHook')
        Assert-True ($script:LastExit -ne 0) "${pin}: a kit tree without $leaf refuses the switch (exit $($script:LastExit))"
        Assert-Regex $out ('refused: -GradleHook needs .*' + [regex]::Escape($leaf)) "${pin}: without $leaf the refusal names the file the tree lacks"
        Assert-True (-not ($out -match 'Adoption preflight|summary  ')) "${pin}: without $leaf it stops before the preflight and the plan"
        Assert-True (-not (Test-Path -LiteralPath $app)) "${pin}: without $leaf nothing is written, not even the project folder"
        Assert-True ((Get-TreeHash $script:KitHome) -eq $beforeHome) "${pin}: without $leaf the kit home is as it was"
        if ($leaf -eq 'run-logged.py') {
            # A plain run out of that tree wires nothing, so it goes on: it says what the project does not get.
            $plainLack = Join-Path $base 'lack-plain-app'
            $out = Invoke-InstallFrom $lackInstaller @('-Project', $plainLack)
            Assert-Exit 0 "${pin}: a plain run out of the tree without the runner still scaffolds"
            Assert-Regex $out 'warn: this kit tree does not carry .*run-logged\.py' "${pin}: and says the tree does not carry the runner"
            $settings = Read-ScaffoldSettings $plainLack
            Assert-True ($null -ne $settings.Parsed -and -not ($settings.Keys -contains 'hooks')) "${pin}: that plain run wires nothing"
            Assert-True (-not (Test-Path -LiteralPath (Join-Path $plainLack 'scripts'))) "${pin}: writes no scripts folder"
            Assert-True (-not ($out -match 'run-logged\.py\.new|scripts/hooks/run-logged\.py\s+kept as is')) "${pin}: and promises no file it does not write"
            foreach ($file in @(Get-HookNamedFiles $plainLack | Sort-Object -Unique)) {
                Assert-True (Test-Path -LiteralPath $file -PathType Leaf) "${pin}: a hook command of that scaffold names $file and it exists"
            }
            # The same tree beside a runner of their own: no kit runner exists to land as a .new, so none is promised.
            $lackOwn = Join-Path $base 'lack-own-app'
            New-Item -ItemType Directory -Force -Path (Join-Path $lackOwn 'scripts\hooks') | Out-Null
            $lackOwnRunner = Join-Path $lackOwn 'scripts\hooks\run-logged.py'
            [IO.File]::WriteAllText($lackOwnRunner, $ownScript, (New-Object Text.UTF8Encoding($false)))
            $out = Invoke-InstallFrom $lackInstaller @('-Project', $lackOwn)
            Assert-Exit 0 "${pin}: a plain run out of that tree beside an own runner still scaffolds"
            Assert-Regex $out 'scripts/hooks/run-logged\.py\s+kept as is, this run writes no file of that name' `
                "${pin}: its preflight lists the own runner as kept and says this run writes no file of that name"
            Assert-True (-not ($out -match 'run-logged\.py\s+kept as is, the kit version lands')) "${pin}: it promises no .new the tree cannot supply"
            Assert-True ((Get-FileHash -LiteralPath $lackOwnRunner -Algorithm SHA256).Hash -eq $plainOwnHash -and -not (Test-Path -LiteralPath "$lackOwnRunner.new")) `
                "${pin}: the own runner is kept byte for byte, with no .new beside it"
        }
        if (Test-Path -LiteralPath $held -PathType Leaf) { Move-Item -LiteralPath $held -Destination $gone }
    }

    # The switch is a project switch. Without -Project it is refused before the user scope plan, which
    # is why this call is a dry run with -NoHerdr: an installer that lost the refusal writes and asks nothing.
    $beforeHome = Get-TreeHash $script:KitHome
    $out = Invoke-Install @('-GradleHook', '-DryRun', '-NoHerdr')
    Assert-True ($script:LastExit -ne 0) "ProjectScaffoldGradleHookNeedsProjectTest: -GradleHook without -Project is refused (exit $($script:LastExit))"
    Assert-Match $out 'refused: -GradleHook' 'ProjectScaffoldGradleHookNeedsProjectTest: the run says it is refused'
    Assert-Match $out 'needs -Project' 'ProjectScaffoldGradleHookNeedsProjectTest: and what the switch needs'
    Assert-True (-not ($out -match 'Kit home:|plan \(dry run')) 'ProjectScaffoldGradleHookNeedsProjectTest: no user scope plan runs in its place'
    Assert-True ((Get-TreeHash $script:KitHome) -eq $beforeHome) 'ProjectScaffoldGradleHookNeedsProjectTest: and the kit home is as it was'

    # ------------------------------- a run that leaves the hook unwired says so --
    Write-Host "`r`nphase 17, a run that leaves the gradle hook unwired says so in one line, after its plan"
    # Nothing remembers the opt-in. A plain run over a -GradleHook scaffold renders the settings
    # without the hook, and a -GradleHook run over settings of the adopter's own keeps them and puts
    # the hook block beside them. Both used to end without a word about the hook. The line is read
    # off the result: the file after a real run, the plan's line for it under -DryRun.
    $unwired    = 'the gradle hook is no longer wired'
    $unwiredDry = 'a real run would leave the gradle hook unwired'
    $notYet     = 'the gradle hook is not wired yet'
    $notYetDry  = 'a real run would not wire the gradle hook yet'
    $hookNotes  = 'the gradle hook is no longer wired|leave the gradle hook unwired|the gradle hook is not wired yet|not wire the gradle hook yet'
    $editSettings = {
        # An edit of the adopter's own: one more key at the head of the object, the rest as rendered.
        param([string]$File)
        $text = [IO.File]::ReadAllText($File)
        [IO.File]::WriteAllText($File, ('{ "teamNote": "ours",' + $text.Substring(1)), (New-Object Text.UTF8Encoding($false)))
    }

    $pin = 'ProjectScaffoldPlainRunSaysHookUnwiredTest'
    $switchedApp = Join-Path $base 'switched-app'
    $wiringOut = Invoke-Install @('-Project', $switchedApp, '-GradleHook')
    Assert-Exit 0 "${pin}: the -GradleHook scaffold a plain run will follow installs"
    $switchedSettings = Join-Path $switchedApp '.claude\settings.json'
    Assert-True ((Get-FilterCommandCount (Read-ScaffoldSettings $switchedApp)) -eq 1) "${pin}: and its hook is wired"
    $wiringDry = Invoke-Install @('-Project', $switchedApp, '-GradleHook', '-DryRun')
    # -DryRun first: nothing is written to read back, so the line comes from the plan's refresh line.
    $beforeTree = Get-TreeHash $switchedApp
    $beforeHome = Get-TreeHash $script:KitHome
    $dry = Invoke-Install @('-Project', $switchedApp, '-DryRun')
    Assert-Exit 0 "${pin}: a plain -DryRun over that scaffold succeeds"
    $said = @(Get-LinesHolding $dry $unwiredDry)
    Assert-True ($said.Count -eq 1) "${pin}: under -DryRun one line says a real run would leave the hook unwired ($($said.Count) printed)"
    Assert-True ($said.Count -eq 1 -and $said[0].Contains('-GradleHook') -and $said[0].Contains($switchedSettings)) `
        "${pin}: and it names -GradleHook and the settings file"
    Assert-True (-not $dry.Contains($unwired)) "${pin}: a dry run does not say the hook is no longer wired, it still is"
    Assert-True ((Get-TreeHash $switchedApp) -eq $beforeTree -and (Get-TreeHash $script:KitHome) -eq $beforeHome) `
        "${pin}: and -DryRun wrote nothing, in the project or in the kit home"
    $out = Invoke-Install @('-Project', $switchedApp)
    Assert-Exit 0 "${pin}: a plain run over the -GradleHook scaffold succeeds"
    Assert-True ((Get-FilterCommandCount (Read-ScaffoldSettings $switchedApp)) -eq 0) "${pin}: it renders the settings without the hook"
    $said = @(Get-LinesHolding $out $unwired)
    Assert-True ($said.Count -eq 1) "${pin}: and one line says the gradle hook is no longer wired ($($said.Count) printed)"
    Assert-True ($said.Count -eq 1 -and $said[0].Contains('-GradleHook') -and $said[0].Contains($switchedSettings)) `
        "${pin}: which names -GradleHook, the way to wire it again, and the settings file"
    Assert-True ($out.IndexOf('summary  ') -ge 0 -and $out.IndexOf('summary  ') -lt $out.IndexOf($unwired)) "${pin}: it comes after the plan, not ahead of it"
    Assert-True (-not $out.Contains($notYet)) "${pin}: and the line of a -GradleHook run is not printed beside it"
    # The project is a plain scaffold now. Nothing changes hands, so nothing is said, by a real run or a dry one.
    $out = Invoke-Install @('-Project', $switchedApp)
    Assert-Exit 0 "${pin}: a plain run over a plain scaffold succeeds"
    Assert-True (-not ($out -match $hookNotes)) "${pin}: and prints no such line"
    $beforeTree = Get-TreeHash $hookApp
    $dry = Invoke-Install @('-Project', $hookApp, '-DryRun')
    Assert-True ($script:LastExit -eq 0 -and -not ($dry -match $hookNotes)) "${pin}: a plain -DryRun over a plain scaffold prints no such line either"
    Assert-True ((Get-TreeHash $hookApp) -eq $beforeTree) "${pin}: and writes nothing"
    # Settings of the adopter's own that still name the filter are kept by a plain run: the hook stays
    # wired and no line is due. -Force overwrites them, and then the line is due, read off the force line.
    $ownWiredApp = Join-Path $base 'own-wired-app'
    Invoke-Install @('-Project', $ownWiredApp, '-GradleHook') | Out-Null
    & $editSettings (Join-Path $ownWiredApp '.claude\settings.json')
    $ownWired = Read-ScaffoldSettings $ownWiredApp
    Assert-True (($ownWired.Keys -contains 'teamNote') -and (Get-FilterCommandCount $ownWired) -eq 1) "${pin}: wired settings the adopter edited still parse, with their key and the hook"
    $dry = Invoke-Install @('-Project', $ownWiredApp, '-DryRun')
    Assert-True ($script:LastExit -eq 0 -and -not ($dry -match $hookNotes)) "${pin}: a plain -DryRun over them, whose plan keeps the file, prints no such line"
    $out = Invoke-Install @('-Project', $ownWiredApp)
    Assert-True ($script:LastExit -eq 0 -and -not ($out -match $hookNotes)) "${pin}: nor does the plain run"
    Assert-True ((Get-FilterCommandCount (Read-ScaffoldSettings $ownWiredApp)) -eq 1) "${pin}: which keeps them, hook and all"
    $dry = Invoke-Install @('-Project', $ownWiredApp, '-Force', '-DryRun')
    $said = @(Get-LinesHolding $dry $unwiredDry)
    Assert-True ($script:LastExit -eq 0 -and $said.Count -eq 1) "${pin}: a plain -Force -DryRun over them reads the plan's force line and says it once ($($said.Count) printed)"
    $out = Invoke-Install @('-Project', $ownWiredApp, '-Force')
    $said = @(Get-LinesHolding $out $unwired)
    Assert-True ($script:LastExit -eq 0 -and $said.Count -eq 1) "${pin}: and the plain -Force run says it once ($($said.Count) printed)"
    Assert-True ((Get-FilterCommandCount (Read-ScaffoldSettings $ownWiredApp)) -eq 0) "${pin}: over settings it did overwrite"

    $pin = 'ProjectScaffoldGradleHookKeptSettingsSaysNotWiredTest'
    Assert-True (-not ($wiringOut -match $hookNotes)) "${pin}: a -GradleHook run that does wire prints no such line"
    Assert-True (-not ($wiringDry -match $hookNotes)) "${pin}: nor does a -GradleHook -DryRun over settings that are wired already"
    $dryGradleApp = Join-Path $base 'dry-gradle-app'
    $dry = Invoke-Install @('-Project', $dryGradleApp, '-GradleHook', '-DryRun')
    Assert-True ($script:LastExit -eq 0 -and -not ($dry -match $hookNotes)) "${pin}: nor a -GradleHook -DryRun into a fresh folder, whose plan writes the wired settings"
    Assert-True (-not (Test-Path -LiteralPath $dryGradleApp)) "${pin}: which writes nothing"
    $keptApp = Join-Path $base 'kept-settings-app'
    $out = Invoke-Install @('-Project', $keptApp)
    Assert-Exit 0 "${pin}: the plain scaffold whose settings the adopter will edit installs"
    $keptSettings = Join-Path $keptApp '.claude\settings.json'
    & $editSettings $keptSettings
    $edited = Read-ScaffoldSettings $keptApp
    Assert-True (($edited.Keys -contains 'teamNote') -and -not ($edited.Keys -contains 'hooks')) "${pin}: the edited settings.json parses, with the adopter key and no hook"
    $keptHash   = (Get-FileHash -LiteralPath $keptSettings -Algorithm SHA256).Hash
    $beforeTree = Get-TreeHash $keptApp
    $beforeHome = Get-TreeHash $script:KitHome
    $dry = Invoke-Install @('-Project', $keptApp, '-GradleHook', '-DryRun')
    Assert-Exit 0 "${pin}: -GradleHook -DryRun over the edited settings.json succeeds"
    $said = @(Get-LinesHolding $dry $notYetDry)
    Assert-True ($said.Count -eq 1) "${pin}: under -DryRun one line says a real run would not wire the hook yet ($($said.Count) printed)"
    Assert-True ($said.Count -eq 1 -and $said[0].Contains("$keptSettings.new")) "${pin}: and it names settings.json.new"
    Assert-True (-not $dry.Contains($notYet)) "${pin}: a dry run does not speak of a settings.json.new it did not write"
    Assert-True ((Get-TreeHash $keptApp) -eq $beforeTree -and (Get-TreeHash $script:KitHome) -eq $beforeHome) `
        "${pin}: and -DryRun wrote nothing, in the project or in the kit home"
    $out = Invoke-Install @('-Project', $keptApp, '-GradleHook')
    Assert-Exit 0 "${pin}: -GradleHook over the edited settings.json succeeds"
    Assert-True ((Get-FileHash -LiteralPath $keptSettings -Algorithm SHA256).Hash -eq $keptHash) "${pin}: the adopter settings.json is kept byte for byte"
    Assert-True ((Get-FilterCommandCount (Read-ScaffoldSettings $keptApp)) -eq 0) "${pin}: so no hook is wired by it"
    $proposalText = if (Test-Path -LiteralPath "$keptSettings.new" -PathType Leaf) { (Get-Content -LiteralPath "$keptSettings.new" -Raw) } else { '' }
    Assert-True ($proposalText.Contains('scripts/hooks/filter-gradle-output.py')) "${pin}: the kit version beside it, settings.json.new, holds the hook block"
    $said = @(Get-LinesHolding $out $notYet)
    Assert-True ($said.Count -eq 1) "${pin}: and one line says the gradle hook is not wired yet ($($said.Count) printed)"
    Assert-True ($said.Count -eq 1 -and $said[0].Contains("$keptSettings.new")) "${pin}: which names settings.json.new"
    Assert-True ($out.IndexOf('summary  ') -ge 0 -and $out.IndexOf('summary  ') -lt $out.IndexOf($notYet)) "${pin}: it comes after the plan, not ahead of it"
    Assert-True (-not $out.Contains($unwired)) "${pin}: and it does not say the hook is no longer wired, it never was"
    # A second run finds the proposal already there: the plan says kept, and the line is still due.
    $out = Invoke-Install @('-Project', $keptApp, '-GradleHook')
    $said = @(Get-LinesHolding $out $notYet)
    Assert-True ($script:LastExit -eq 0 -and $said.Count -eq 1 -and $said[0].Contains("$keptSettings.new")) `
        "${pin}: a second -GradleHook run, the proposal already beside it, says it once again ($($said.Count) printed)"
    $out = Invoke-Install @('-Project', $keptApp, '-GradleHook', '-Force')
    Assert-Exit 0 "${pin}: -GradleHook -Force over the edited settings.json succeeds"
    Assert-True ((Get-FilterCommandCount (Read-ScaffoldSettings $keptApp)) -eq 1 -and -not ($out -match $hookNotes)) `
        "${pin}: it overwrites them, the hook is wired, and no such line is printed"
} finally {
    if (Test-Path -LiteralPath $worktree) {
        Invoke-Git @('-C', $main, 'worktree', 'remove', '--force', $worktree) | Out-Null
    }
    Close-KitSandbox $realProfile
}

Write-TestResult
