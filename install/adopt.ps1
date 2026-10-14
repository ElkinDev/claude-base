#requires -Version 5.1
<#
adopt.ps1 - where the installer's files come from, and how a run fits into a repository (F13).

Three jobs: build the source-to-target pairs a scope installs, report what the repository already
carries before anything is planned, and keep the run out of a shared history when the adopter asks
for that. The ownership rule itself is in install\lib.ps1.
#>

# ---------------------------------------------------------------- sources and directories

function Get-KitPairs {
    # What a clone carries beyond the kit is not the kit: bytecode caches, and the .bak and .new
    # files a run of this installer leaves in a source tree, would otherwise make the installed
    # set depend on whose clone it was built from. They are skipped, so it never does.
    param([string]$SourceDir, [string]$TargetDir)
    if (-not (Test-Path -LiteralPath $SourceDir)) { return @() }
    $base = (Resolve-Path -LiteralPath $SourceDir).Path
    return @(Get-ChildItem -LiteralPath $base -Recurse -File |
             Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and
                            $_.Extension -notin @('.pyc', '.bak', '.new') } | ForEach-Object {
        $rel = $_.FullName.Substring($base.Length).TrimStart('\', '/')
        [pscustomobject]@{ Source = $_.FullName; Target = (Join-Path $TargetDir $rel); Text = $null }
    })
}

function New-KitPair {
    param([string]$Source, [string]$Target, $Text = $null)
    return [pscustomobject]@{ Source = $Source; Target = $Target; Text = $Text }
}

function Test-KitPluginChannelOnly {
    <#
    Whether a plugin says it reaches a machine one way only, through `claude plugin install`. The
    installer skips those, so no skill can arrive twice, once bare and once namespaced.

    It is declared in `kit.json` at the plugin root, a file this kit owns, and not in the plugin
    manifest: `claude plugin validate --strict` fails a manifest carrying a field the format does
    not know, and a keyword would hide a load-bearing string in an open discovery vocabulary where
    no reader could tell it mattered. The sidecar sits outside `.claude-plugin/`, so the format's
    checker never reads it.
    #>
    param([string]$PluginDir)
    $sidecar = Join-Path $PluginDir 'kit.json'
    if (-not (Test-Path -LiteralPath $sidecar)) { return $false }
    try { $data = (Get-Content -LiteralPath $sidecar -Raw) | ConvertFrom-Json } catch { return $false }
    return ($data.channel -eq 'marketplace')
}

function Get-KitFullPath {
    <#
    One comparable spelling of a path. git answers with forward slashes and the shell with
    backslashes, either can carry a trailing separator, and Windows paths differ in case without
    differing at all, so two spellings of one directory have to be folded before they are compared.
    #>
    param([string]$Path)
    if (-not $Path) { return '' }
    $full = [System.IO.Path]::GetFullPath($Path.Replace('/', '\'))
    return $full.TrimEnd('\').ToLowerInvariant()
}

function Get-KitOrigin {
    <#
    Where this clone of the kit came from, as an owner/repository pair and the commit it sits on.
    A scaffolded project pins its plugin source to exactly that, so the project takes the kit at
    the version it was scaffolded from and nothing moves under it later.

    Read from git at install time and never committed: the pair is a real account name, which the
    push guard would flag in a tracked file, and it is per machine anyway. Returns $null when git
    is missing, when the repository it finds is not this tree, when there is no origin, or when
    either value does not have the shape it should. The caller then writes no plugin source at
    all and says so, rather than shipping a source that cannot resolve.

    The repository has to BE this tree. `git -C` walks up until it finds one, so a kit unpacked
    inside somebody else's clone would otherwise be read as that clone, and its private remote and
    its commit would land in a file the adopter is invited to commit.
    #>
    param([string]$Root)
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return $null }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $top = ((& git -C $Root rev-parse --show-toplevel 2>$null) | Out-String).Trim()
        $url = ((& git -C $Root remote get-url origin 2>$null) | Out-String).Trim()
        $sha = ((& git -C $Root rev-parse HEAD 2>$null) | Out-String).Trim()
    } finally { $ErrorActionPreference = $previous }
    if (-not $top) { return $null }
    if ((Get-KitFullPath $top) -ne (Get-KitFullPath $Root)) { return $null }
    if ($url -notmatch '[:/]([^/:]+)/([^/]+?)(?:\.git)?$') { return $null }
    $slug = $matches[1] + '/' + $matches[2]
    if ($sha -notmatch '^[0-9a-f]{40}$') { return $null }
    return [pscustomobject]@{ Slug = $slug; Sha = $sha }
}

function Get-KitMissingDirs {
    # Read before anything is written, so a dry run can name the folders a real run would create.
    param($Pairs)
    $seen = @{}
    $out  = @()
    foreach ($pair in $Pairs) {
        $dir = Split-Path -Parent $pair.Target
        while ($dir -and -not $seen.ContainsKey($dir) -and -not (Test-Path -LiteralPath $dir)) {
            $seen[$dir] = $true
            $out += $dir
            $dir = Split-Path -Parent $dir
        }
    }
    return @($out | Sort-Object)
}

# ---------------------------------------------------------------- project adoption

function Get-KitHookScripts {
    # The scripts project scope lands in <project>\scripts\hooks. run-logged.py on every run: the
    # scaffolded CLAUDE.md sends long tools through it. filter-gradle-output.py only with -GradleHook:
    # it is what the gradle hook of settings.gradle.json runs, and it sends gradle through the runner
    # beside it. A caller wraps the answer in @(), because one name comes back as a bare string.
    param([switch]$GradleHook)
    if ($GradleHook) { return @('run-logged.py', 'filter-gradle-output.py') }
    return @('run-logged.py')
}

function Get-KitHookScriptAction {
    <#
    What the plan will do with one project hook script: the answer of the ownership rule the plan
    itself asks (Resolve-KitAction in install\lib.ps1), so nothing said from here can disagree with
    the plan. 'write', 'same', 'refresh' and 'force' leave the kit's file in the project once the
    run is over: absent before it, byte for byte the kit's, recorded in the manifest and unedited,
    or overwritten under -Force. 'new' is a file of the adopter's own, and that includes a script
    the kit wrote and the adopter then edited, because the record no longer matches it. 'nosource'
    is a kit tree that does not carry the script: the plan has nothing to write for it.
    #>
    param([string]$Project, [string]$Template, [string]$Name, [hashtable]$Manifest = @{}, [switch]$Force)
    $source = Join-Path $Template "scripts\hooks\$Name"
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { return 'nosource' }
    return (Resolve-KitAction -Target (Join-Path $Project "scripts\hooks\$Name") -Manifest $Manifest `
        -Force:$Force -SourceHash (Get-Sha256File $source))
}

function Get-KitGradleHookRefusal {
    <#
    Why -GradleHook cannot be honoured, as one line, or $null when it can. Two checks, both made
    before the run writes or plans anything. The kit tree has to carry what the switch installs:
    the gradle settings template and both hook scripts. And each of the two scripts in the project
    has to be the kit's once the run is over, by Get-KitHookScriptAction: the hook runs the filter,
    the filter sends gradle through the runner beside it, and a runner that is not the kit's can
    report a build green that never started. The switch is an explicit request, so it is refused
    rather than half honoured, and a refusal has nothing to predict.

    The remedy it names is one that exists on a disk nothing was written to: the kit's own file,
    or -Force. It names no .new, because a refused run leaves none.
    #>
    param([string]$Project, [string]$Template, [hashtable]$Manifest = @{}, [switch]$Force)
    $names   = @(Get-KitHookScripts -GradleHook)
    $needed  = @('.claude\settings.gradle.json') + @($names | ForEach-Object { "scripts\hooks\$_" })
    $missing = @($needed | ForEach-Object { Join-Path $Template $_ } |
                 Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
    if ($missing) {
        return ('refused: -GradleHook needs ' + ($missing -join ' and ') + ', which this kit tree does not ' +
                'carry. Run the installer from a complete clone of the kit. Nothing was written.')
    }
    $own = @($names | Where-Object { (Get-KitHookScriptAction $Project $Template $_ $Manifest -Force:$Force) -eq 'new' } |
             ForEach-Object { Join-Path $Project "scripts\hooks\$_" })
    if ($own) {
        return ('refused: -GradleHook would wire a hook that runs ' + ($own -join ' and ') + ', yours and not ' +
                'the kit''s. Put the kit''s file there (from ' + (Join-Path $Template 'scripts\hooks') +
                ') and run again, or add -Force, which does that after a backup. Nothing was written.')
    }
    return $null
}
function Get-AdoptionFindings {
    <#
    What the repository already has, and what this run will do about it. Only findings are
    printed: an adopter cares about the files that already exist, not about the ones that do not.
    With the template folder in hand it also compares: a file that already holds the kit's bytes
    gets no .new beside it, and saying it would is a promise the run does not keep. It takes the
    run's own switches for the same reason: under -Force a file of the adopter's is overwritten
    after a backup, not kept, and a hook script the run installs no version of gets no .new. The
    manifest is for the two hook scripts, which are listed only when the run leaves a file there
    that is not the kit's.
    #>
    param([string]$Project, [string]$Template, [hashtable]$Manifest = @{}, [switch]$Force, [switch]$GradleHook)
    $shared    = 'kept as is, the kit version lands beside it as <name>.new'
    $same      = 'kept as is, already matches the kit'
    $never     = 'never touched by the installer'
    $forced    = 'overwritten by -Force, after a copy of yours goes to the backup'
    $forcedDir = 'under -Force a file in it that the kit writes and that differs is overwritten after a backup, the rest is kept as is'
    $unwritten = 'kept as is, this run writes no file of that name'
    $known  = @(
        @('CLAUDE.md', $shared), @('AGENTS.md', $shared), @('.claude', $shared),
        @('.cursor', $never), @('.github/copilot-instructions.md', $never), @('.husky', $never),
        @('.pre-commit-config.yaml', $never), @('lefthook.yml', $never), @('CODEOWNERS', $never),
        @('.editorconfig', $never)
    )
    $out = @()
    foreach ($item in $known) {
        $here = Join-Path $Project $item[0].Replace('/', '\')
        if (Test-Path -LiteralPath $here) {
            $note = $item[1]
            $kit  = if ($Template) { Join-Path $Template $item[0].Replace('/', '\') } else { '' }
            if ($note -eq $shared -and $kit -and (Test-Path -LiteralPath $kit -PathType Leaf) -and
                (Test-Path -LiteralPath $here -PathType Leaf) -and
                (Get-Sha256File $here) -eq (Get-Sha256File $kit)) { $note = $same }
            # -Force overwrites what the line above calls kept, and the .new it promises is never written.
            if ($Force -and $note -eq $shared -and $kit -and (Test-Path -LiteralPath $kit)) {
                $note = if (Test-Path -LiteralPath $here -PathType Leaf) { $forced } else { $forcedDir }
            }
            # A team AGENTS.md is merged by its markers, never by hand (F03 edge case 1): the note names
            # the command, which adds the kit's sections to theirs and leaves every line of theirs alone.
            # Full paths, so it runs from wherever the adopter reads it.
            if ($item[0] -eq 'AGENTS.md' -and $note -eq $shared -and $Template) {
                $tool = Join-Path (Split-Path -Parent $Template) 'scripts\agents-md.py'
                # the preflight also prints on -DryRun, which writes no .new, so the note says which run makes it
                $note += "; once a real run has written AGENTS.md.new, take the kit sections into yours: python `"$tool`" render `"$here.new`" `"$here`""
            }
            $out += [pscustomobject]@{ Item = $item[0]; Note = $note }
        }
    }
    # The two project hook scripts, each listed when it is on disk and the run does not leave the
    # kit's file there. The plan's own rule is asked (Get-KitHookScriptAction) with the run's own
    # switches, so a line says what this run does: a .new only where the plan writes one, overwritten
    # under -Force, and no .new for a script the run installs no version of (the filter on a run
    # without -GradleHook, or a script the kit tree does not carry).
    if ($Template) {
        $installs = @(Get-KitHookScripts -GradleHook:$GradleHook)
        foreach ($name in @(Get-KitHookScripts -GradleHook)) {
            $writes = $installs -contains $name
            $action = Get-KitHookScriptAction $Project $Template $name $Manifest -Force:($Force -and $writes)
            $onDisk = Test-Path -LiteralPath (Join-Path $Project "scripts\hooks\$name") -PathType Leaf
            $note   = $null
            if ($writes -and $action -eq 'new')       { $note = $shared }
            elseif ($writes -and $action -eq 'force') { $note = $forced }
            elseif ($onDisk -and ($action -eq 'new' -or $action -eq 'nosource')) { $note = $unwritten }
            if ($note) { $out += [pscustomobject]@{ Item = ('scripts/hooks/' + $name); Note = $note } }
        }
    }
    foreach ($file in @(Get-ChildItem -LiteralPath $Project -Filter 'CONTRIBUTING*' -File -ErrorAction SilentlyContinue)) {
        $out += [pscustomobject]@{ Item = $file.Name; Note = $never }
    }
    $hooks = Join-Path $Project '.git\hooks'
    if (Test-Path -LiteralPath $hooks) {
        foreach ($hook in @(Get-ChildItem -LiteralPath $hooks -File -ErrorAction SilentlyContinue |
                            Where-Object { $_.Name -notlike '*.sample' })) {
            $out += [pscustomobject]@{ Item = ".git/hooks/$($hook.Name)"; Note = $never }
        }
    }
    if (Test-Path -LiteralPath (Join-Path $Project '.git')) {
        $configured = ''
        try { $configured = (git -C $Project config --get core.hooksPath 2>$null | Select-Object -First 1) } catch { }
        if ($configured) {
            $out += [pscustomobject]@{ Item = "core.hooksPath = $configured"; Note = $never }
        }
    }
    return $out
}

# ---------------------------------------------------------------- keeping it local

function Get-GitExcludePath {
    <#
    The exclude file git actually reads. In a worktree the gitdir: pointer leads to
    .git\worktrees\<name>\, whose info\ folder git ignores: per gitrepository-layout the exclude
    file always comes from the common directory. Asking git itself is the only answer that holds
    for a plain repository, a worktree and a submodule alike, and it is the idiom the repository
    already uses in claude\hooks\precompact-autosave.py.
    #>
    param([string]$Project)
    if (-not (Test-Path -LiteralPath (Join-Path $Project '.git'))) { return $null }
    $path = $null
    try { $path = (& git -C $Project rev-parse --git-path info/exclude 2>$null | Select-Object -First 1) } catch { }
    if (-not $path) { return $null }
    $path = ([string]$path).Trim()
    if (-not $path) { return $null }
    if (-not [System.IO.Path]::IsPathRooted($path)) { $path = Join-Path $Project $path }
    return [System.IO.Path]::GetFullPath($path)
}

function Get-KitExcludeLines {
    <#
    The paths this run manages inside the project, as git patterns relative to its root, with
    everything under .claude/ folded into the single folder line .claude/.
    Derived from what the plan actually did, never from a fixed list: a switch that promises to
    hide "what the kit wrote" has to be told by the writing itself, or it drifts the moment a
    scope grows a folder. A file of the adopter's is never in here, so -LocalOnly can never hide
    their own work from them; the .new proposal beside it is.
    #>
    param([string]$Project, [string[]]$Owned)
    $prefix = $Project.TrimEnd('\', '/') + '\'
    $lines  = @()
    foreach ($path in $Owned) {
        if (-not $path.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { continue }
        $rel = $path.Substring($prefix.Length).Replace('\', '/')
        if ($rel.StartsWith('.claude/')) { $rel = '.claude/' }
        if ($lines -notcontains $rel) { $lines += $rel }
    }
    return @($lines | Sort-Object)
}

function Add-ExcludeLines {
    # Appends only the lines that are not there yet, so running it twice adds nothing the second
    # time. It never rewrites or reorders what the repository already excludes.
    param([string]$Project, [string[]]$Lines, [switch]$DryRun)
    $exclude = Get-GitExcludePath $Project
    if (-not $exclude) { return @("no git directory here, -LocalOnly skipped") }
    $existing = @()
    if (Test-Path -LiteralPath $exclude -PathType Leaf) {
        $existing = @(Get-Content -LiteralPath $exclude | ForEach-Object { $_.Trim() })
    }
    $missing = @($Lines | Where-Object { $existing -notcontains $_.Trim() })
    if (-not $missing) { return @("already excluded: $exclude is up to date") }
    if ($DryRun) { return @("would append to $exclude : " + ($missing -join ', ')) }
    $info = Split-Path -Parent $exclude
    if (-not (Test-Path -LiteralPath $info)) { New-Item -ItemType Directory -Force -Path $info | Out-Null }
    $current = ''
    if (Test-Path -LiteralPath $exclude -PathType Leaf) { $current = (Get-Content -LiteralPath $exclude -Raw) }
    if ($current -and -not $current.EndsWith("`n")) { $current += "`r`n" }
    Write-TextNoBom $exclude ($current + ($missing -join "`r`n") + "`r`n")
    return @($missing | Where-Object { -not $_.StartsWith('#') } |
             ForEach-Object { "excluded     $_ (in $exclude)" })
}
