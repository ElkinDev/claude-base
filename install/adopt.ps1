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

function Get-AdoptionFindings {
    <#
    What the repository already has, and what the installer will do about it. Only findings are
    printed: an adopter cares about the files that already exist, not about the ones that do not.
    With the template folder in hand it also compares: a file that already holds the kit's bytes
    gets no .new beside it, and saying it would is a promise the run does not keep.
    #>
    param([string]$Project, [string]$Template)
    $shared = 'kept as is, the kit version lands beside it as <name>.new'
    $same   = 'kept as is, already matches the kit'
    $never  = 'never touched by the installer'
    $known  = @(
        @('CLAUDE.md', $shared), @('AGENTS.md', $never), @('.claude', $shared),
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
            $out += [pscustomobject]@{ Item = $item[0]; Note = $note }
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
