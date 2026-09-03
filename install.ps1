#requires -Version 5.1
<#
install.ps1 - install the personal Claude Code base without destroying anything you already have.

  User scope (default): copy the skills, hooks, statusline and helpers into the kit home
  (%USERPROFILE%\.claude, or $env:KIT_HOME when it is set) and render settings.json.

  Project scope (-Project <path>): scaffold a project with CLAUDE.md, CLAUDE.project.md, a
  .claude/settings.local.json and the branch hooks, plus docs/ when you pass -Sdd. It prints what
  the repository already has before it plans anything, and it never writes git hooks, CI files, or
  anything outside the paths it names in that preflight.

  Nothing is ever deleted. Every file the installer writes is recorded with its hash in
  <kit home>\.kit-manifest.json. A file that differs from that record, or that the manifest never
  heard of, belongs to you: it is backed up, left exactly as it is, and the kit version lands
  beside it as <name>.new for you to merge. -Force overwrites it, still after a backup. Every
  backup goes to <kit home>\backups\<stamp>\ and comes back with scripts\kit-restore.py.
  See docs/ADOPTION.md.

Examples:
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -DryRun
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Project C:\Repo\my-app -LocalOnly
#>
param(
    [string]$Project,
    [switch]$Sdd,
    [switch]$Force,
    # Print the full plan, one line per file, and write nothing at all.
    [switch]$DryRun,
    # Project scope: add exactly what the kit wrote to the repository's exclude file, so none of
    # it can reach the team's history by accident.
    [switch]$LocalOnly,
    # How Claude Code asks before it acts. 'bypass' is what the base ships: no permission
    # prompts, which is fast and is what most of these skills assume. 'ask' keeps the
    # normal prompts. See docs/PERMISSIONS.md before choosing.
    [ValidateSet('bypass', 'ask')]
    [string]$Permissions = 'bypass'
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
. (Join-Path $root 'install\backup.ps1')
. (Join-Path $root 'install\lib.ps1')
. (Join-Path $root 'install\adopt.ps1')

$kitHome      = Get-KitHome
$stamp        = Get-KitStamp $kitHome
$manifestPath = Join-Path $kitHome '.kit-manifest.json'
$manifest     = Read-KitManifest $manifestPath
if (Test-KitManifestBroken) {
    Write-Output "warn: $manifestPath cannot be read, so every file already on disk counts as yours."
    if ($DryRun) {
        Write-Output "      A real run would move it aside and start a fresh record."
    } else {
        Write-Output "      Moved aside to $(Move-BrokenKitManifest $manifestPath $stamp); this run starts a fresh record."
    }
    Write-Output ""
}

function Invoke-KitPlan {
    <#
    The whole plan, executed file by file. The closing block runs in a finally, so a run that dies
    on a locked file or a permission denial still records what it had already done and still prints
    the rollback command for it. A run killed from outside (Ctrl+C at the console, Stop-Process)
    never reaches this block at all: what makes that case recoverable is the backup line written
    before each change, not this finally.
    #>
    param($Pairs, [string]$Trigger)
    Start-KitBackup (Join-Path $kitHome ('backups\' + $stamp)) $Trigger
    $lines    = @()
    $finished = $false
    try {
        foreach ($dir in (Get-KitMissingDirs $Pairs)) { $lines += "create dir   $dir" }
        foreach ($pair in $Pairs) {
            $line = Set-KitFile -Target $pair.Target -SourcePath $pair.Source -SourceText $pair.Text `
                -Manifest $manifest -Force:$Force -DryRun:$DryRun
            if ($line) { $lines += $line }
        }
        $finished = $true
    } finally {
        if ($DryRun) {
            Write-Output "plan (dry run, nothing is written):"
            foreach ($line in $lines) { Write-Output "  $line" }
        } else {
            foreach ($line in $lines) { if (-not $line.StartsWith('skip same')) { Write-Output "  $line" } }
        }
        Write-KitSummary $lines $finished
        Write-KitClosing
    }
}

function Write-KitSummary {
    param([string[]]$Lines, [bool]$Finished)
    $counts = @{}
    foreach ($line in $Lines) {
        $verb = $line.Split(' ')[0]
        $counts[$verb] = 1 + [int]$counts[$verb]
    }
    if (-not $Finished) {
        Write-Output "STOPPED  this run did not finish. Everything below is what it had done by then."
    }
    Write-Output ("summary  {0} written, {1} refreshed, {2} kept with a .new beside them, {3} forced, {4} unchanged" -f
        [int]$counts['write'], [int]$counts['refresh'],
        ([int]$counts['backup+new'] + [int]$counts['kept']), [int]$counts['force'], [int]$counts['skip'])
    if ([int]$counts['draft'] -gt 0) {
        Write-Output ("         plus {0} .new draft(s) of your own, backed up before the kit version replaced them" -f
            [int]$counts['draft'])
    }
}

function Write-KitClosing {
    if ($DryRun) {
        Write-Output ""
        Write-Output "Dry run: nothing was written, no backup was needed, the manifest was not touched."
        Write-Output "Run the same command without -DryRun to apply this plan."
        return
    }
    if (Write-KitManifest $manifestPath $manifest) { Write-Output "manifest     $manifestPath" }
    $backup = Get-KitBackupFolder
    Write-Output ""
    if ($backup) {
        Write-Output "This run is recorded at $backup, with a copy of every file it replaced."
        Write-Output "Roll it back with: python `"$root\scripts\kit-restore.py`" --stamp $stamp"
    } else {
        Write-Output "Nothing changed, so there is nothing to roll back."
    }
}

# ---------------------------------------------------------------- project scope

if ($Project) {
    if (-not (Test-Path -LiteralPath $Project)) {
        if ($DryRun) { Write-Output "create dir   $Project" }
        else { New-Item -ItemType Directory -Force -Path $Project | Out-Null }
    }
    $proj = if (Test-Path -LiteralPath $Project) { (Resolve-Path -LiteralPath $Project).Path } else { $Project }
    $tpl  = Join-Path $root 'project-template'

    Write-Output "Adoption preflight: what $proj already has"
    $findings = @(Get-AdoptionFindings $proj $tpl)
    if ($findings) {
        $width = ($findings | ForEach-Object { $_.Item.Length } | Measure-Object -Maximum).Maximum
        foreach ($finding in $findings) { Write-Output ("  {0,-$width}  {1}" -f $finding.Item, $finding.Note) }
    } else {
        Write-Output "  nothing of yours is in the way; this looks like a fresh repository"
    }
    $writes = if ($Sdd) { 'CLAUDE.md, CLAUDE.project.md, .claude/ and docs/ (the -Sdd spec tree)' }
              else { 'CLAUDE.md, CLAUDE.project.md and .claude/' }
    Write-Output "  the installer writes only $writes, and deletes nothing"
    Write-Output ""

    $projClaude = Join-Path $proj '.claude'
    $pairs = @(
        (New-KitPair (Join-Path $tpl 'CLAUDE.md')         (Join-Path $proj 'CLAUDE.md')),
        (New-KitPair (Join-Path $tpl 'CLAUDE.project.md') (Join-Path $proj 'CLAUDE.project.md')),
        (New-KitPair (Join-Path $tpl '.claude\settings.local.json') (Join-Path $projClaude 'settings.local.json'))
    )
    foreach ($hook in @('branch-check.ps1', 'branch-upstream-fix.ps1')) {
        $pairs += New-KitPair (Join-Path $root "claude\hooks\$hook") (Join-Path $projClaude "hooks\$hook")
    }
    if ($Sdd) {
        $pairs += Get-KitPairs (Join-Path $root 'claude\agents') (Join-Path $projClaude 'agents')
        $pairs += Get-KitPairs (Join-Path $tpl 'docs') (Join-Path $proj 'docs')
    }

    Invoke-KitPlan $pairs "project scope install at $proj"

    if ($LocalOnly) {
        Write-Output ""
        $lines = @('# claude-base, kept local to this machine') + (Get-KitExcludeLines $proj (Get-KitOwned))
        foreach ($line in (Add-ExcludeLines -Project $proj -Lines $lines -DryRun:$DryRun)) {
            Write-Output "  $line"
        }
    }

    Write-Output ""
    Write-Output "Project scaffolded at $proj."
    Write-Output "Next: copy the closest project-template\profiles\*.md over CLAUDE.project.md and fill it."
    if ($Sdd) { Write-Output "SDD scaffolding added: .claude/agents/ + docs/. Drive it with /sdd." }
    if (-not $LocalOnly) {
        Write-Output "Keep it out of the team's history: re-run with -LocalOnly to add exactly the files"
        Write-Output "this run wrote to the repository's exclude file. Recommended for a repository you share."
    }
    Write-Output "Before you commit any of these files, check your team's policy on AI tooling in the repo."
    Write-Output "How the kit adapts to a repository that already has rules: docs/ADOPTION.md"
    return
}

# ---------------------------------------------------------------- user scope

$pairs = @()
$pairs += Get-KitPairs (Join-Path $root 'claude\skills') (Join-Path $kitHome 'skills')
$pairs += Get-KitPairs (Join-Path $root 'claude\hooks')  (Join-Path $kitHome 'hooks')
$pairs += Get-KitPairs (Join-Path $root 'claude\tools')  (Join-Path $kitHome 'tools')
$pairs += Get-KitPairs (Join-Path $root 'claude\agents') (Join-Path $kitHome 'agents')
# The account switcher and its settings merger are optional: nothing else depends on them, they
# sit unused until the cc function is wired up (see docs/ACCOUNTS.md).
foreach ($file in @('statusline.ps1', 'claude-account.ps1', 'merge-settings.py', 'CLAUDE.md')) {
    $pairs += New-KitPair (Join-Path $root "claude\$file") (Join-Path $kitHome $file)
}

# Rendered from the kit home, not from the profile: KIT_HOME moves where the files land, so it has
# to move what the hook commands inside settings.json point at, or they point at an empty tree.
# Both slash flavours appear in the file, and the backslash one is JSON-escaped.
$settingsText = (Get-Content (Join-Path $root 'claude\settings.json') -Raw)
$settingsText = $settingsText.Replace('%USERPROFILE%/.claude', $kitHome.Replace('\', '/'))
$settingsText = $settingsText.Replace('%USERPROFILE%\\.claude', $kitHome.Replace('\', '\\'))
$settingsText = $settingsText.Replace('%USERPROFILE%', ($env:USERPROFILE).Replace('\', '\\'))

# Permission mode. Two keys move together: the mode itself, and whether Claude Code
# warns you when it starts in the dangerous one.
if ($Permissions -eq 'ask') {
    $settingsText = $settingsText -replace '"defaultMode"\s*:\s*"bypassPermissions"', '"defaultMode": "default"'
    $settingsText = $settingsText -replace '"skipDangerousModePermissionPrompt"\s*:\s*true', '"skipDangerousModePermissionPrompt": false'
}
$pairs += New-KitPair $null (Join-Path $kitHome 'settings.json') $settingsText

Write-Output "Kit home: $kitHome"
Invoke-KitPlan $pairs 'user scope install'

if ($Permissions -eq 'bypass') {
    Write-Output ""
    Write-Output "  PERMISSIONS: bypass. Claude Code will act without asking for confirmation."
    Write-Output "  Re-run with -Permissions ask to keep the prompts. See docs/PERMISSIONS.md."
} else {
    Write-Output ""
    Write-Output "  PERMISSIONS: ask. Claude Code will prompt before acting."
    Write-Output "  Some skills in this base assume bypass and will stop on each step."
}
Write-Output ""
Write-Output "Done. If you use Herdr, run its Claude integration too (see herdr\README.md)."
Write-Output "Run: python scripts\doctor.py"
