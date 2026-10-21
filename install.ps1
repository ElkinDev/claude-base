#requires -Version 5.1
<#
install.ps1 - install the personal Claude Code base without destroying anything you already have.

  User scope (default): copy the skills, hooks, statusline and helpers into the kit home
  (%USERPROFILE%\.claude, or $env:KIT_HOME when it is set) and render settings.json.

  Project scope (-Project <path>): scaffold a project with AGENTS.md, CLAUDE.md, CLAUDE.project.md, a
  .claude/settings.local.json and the branch hooks, the logged runner CLAUDE.md names
  (scripts/hooks/run-logged.py), plus docs/ when you pass -Sdd. No stack-specific hook is wired by
  default. -GradleHook opts a gradle project in: it copies scripts/hooks/filter-gradle-output.py and
  wires it as a PreToolUse hook, and it refuses, with nothing written, when a hook script in the
  project would not be the kit's after the run. The run prints what the repository already has
  before it plans anything, and it never writes git hooks, CI files, or anything outside the paths
  it names in that preflight.
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
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Project C:\Repo\my-app -GradleHook
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
    # Project scope: wire the gradle output hook and copy the filter it runs. Off by default, since a
    # hook that starts a process on every shell call serves only a gradle project. Refused when a hook
    # script in the project would not be the kit's after the run, or when the kit tree lacks one.
    [switch]$GradleHook,
    # How Claude Code asks before it acts. 'bypass' is what the base ships: no permission
    # prompts, which is fast and is what most of these skills assume. 'ask' keeps the
    # normal prompts. See docs/PERMISSIONS.md before choosing.
    [ValidateSet('bypass', 'ask')]
    [string]$Permissions = 'bypass',
    # User scope: add Herdr, set up the way this kit uses it (install\herdr.ps1), without asking.
    # With neither switch the installer asks, and Enter is yes; a run with no console asks nothing.
    [switch]$Herdr,
    [switch]$NoHerdr,
    # User scope: when your settings.json is kept with a .new beside it, the kit's new hooks are added to
    # it (after a backup, nothing of yours removed; claude\merge-hooks.py). This switch leaves it as it is.
    [switch]$NoHookMerge
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
. (Join-Path $root 'install\backup.ps1')
. (Join-Path $root 'install\lib.ps1')
. (Join-Path $root 'install\adopt.ps1')
. (Join-Path $root 'install\herdr.ps1')
if ($Herdr -and $NoHerdr) { throw '-Herdr and -NoHerdr cannot both be given.' }
if ($GradleHook -and -not $Project) {
    Write-Output 'refused: -GradleHook wires a hook into a project, so it needs -Project <path>. Nothing was written.'
    exit 1
}

$kitHome      = Get-KitHome
$stamp        = Get-KitStamp $kitHome
$manifestPath = Join-Path $kitHome '.kit-manifest.json'
$manifest     = Read-KitManifest $manifestPath
if ($GradleHook) {
    # An explicit request is answered before anything else happens: ahead of the preflight and the
    # plan, and ahead of the two writes that come before them (the project folder, and a broken
    # manifest moved aside). A refusal is one line and leaves the disk as it was, so no line of this
    # run has to predict what the hook would call. The path is spelled the way the plan spells it,
    # because the manifest is keyed by it.
    $asked   = if (Test-Path -LiteralPath $Project) { (Resolve-Path -LiteralPath $Project).Path } else { $Project }
    $refusal = Get-KitGradleHookRefusal $asked (Join-Path $root 'project-template') $manifest -Force:$Force
    if ($refusal) {
        Write-Output $refusal
        exit 1
    }
}
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
            # Your settings.json was kept: the kit's new hooks go into it here, inside the plan, so the
            # backup line comes before the write and the closing block names the rollback.
            if ($line -and $script:KitHookMergeTarget -and $pair.Target -eq $script:KitHookMergeTarget -and
                (@($line)[0] -match '^(backup\+new|kept) ')) {
                $lines += @(Invoke-KitHookMerge $pair.Target ([string]$pair.Text))
            }
        }
        $finished = $true
    } finally {
        # Kept for the one reader that comes after the plan (Get-KitGradleHookNote under -DryRun).
        $script:KitPlanLines = @($lines)
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

function Get-KitPython {
    # The first python that runs and prints, as an argument list: the WindowsApps python.exe is a
    # Store shortcut that prints nothing, so being on PATH is not enough.
    foreach ($candidate in @(@('python'), @('py', '-3'))) {
        if (-not (Get-Command $candidate[0] -ErrorAction SilentlyContinue)) { continue }
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $rest = @($candidate | Select-Object -Skip 1)
            $said = (& $candidate[0] @rest -c 'print(42)' 2>$null | Out-String).Trim()
        } catch { $said = '' } finally { $ErrorActionPreference = $previous }
        if ($said -eq '42') { return ,$candidate }
    }
    return $null
}

function Invoke-KitHookMerge {
    <#
    The kit's new hooks into a settings.json of yours, through claude\merge-hooks.py: additions only,
    and only into a file that already runs the kit's hooks (its docstring has the rules). What the
    kit has already offered is the merge's own record, .kit-hooks-merged.json in the kit home: it
    advances only when a merge writes or finds every offered hook there, so a -NoHookMerge run, a run
    with no Python, a refusal or a file left alone leaves it as it was and the next run offers the
    same hooks again. The previous kit render, the .new as it was before this run, only seeds a
    missing record. A dry probe runs first, so a run with nothing to write writes no backup line.
    Before a real write the file is backed up and recorded as `merged <path> <sha256 after the
    merge>` (kit-restore.py puts the copy back only while the file still has that hash; the manifest
    record of settings.json is left as it was), and the merge record is backed up as overwritten or
    created and put on the manifest, so a rollback takes it back too. Returns plan lines.
    #>
    param([string]$Target, [string]$KitText)
    $byHand = "take new hooks from $Target.new by hand"
    if ($NoHookMerge) { return "hooks        $Target (not merged, -NoHookMerge; $byHand)" }
    $python = Get-KitPython
    if (-not $python) { return "hooks        $Target (not merged, no Python that runs; $byHand)" }
    $tool = Join-Path $root 'claude\merge-hooks.py'
    $record = Join-Path $kitHome '.kit-hooks-merged.json'
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $b64 = [Convert]::ToBase64String($utf8.GetBytes($KitText))
    $exe = $python[0]
    $pre = @($python | Select-Object -Skip 1) + @('-I', $tool, '--kit-b64', $b64, '--record', $record, '--kit-home', $kitHome)
    if ($null -ne $script:KitHookMergePrevious) {
        $pre += @('--previous-b64', [Convert]::ToBase64String($utf8.GetBytes($script:KitHookMergePrevious)))
    }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $savedIo = $env:PYTHONIOENCODING
    $env:PYTHONIOENCODING = 'utf-8'
    try {
        $probe = @(& $exe @pre $Target '--dry-run' 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous; $env:PYTHONIOENCODING = $savedIo }
    if ($code -ne 0) { return "hooks        $Target (not merged: $((@($probe) + '')[0]); $byHand)" }
    $adds = @($probe | Where-Object { $_.StartsWith('would add') })
    $after = @($probe | Where-Object { $_ -match '^after-sha256 [0-9a-f]{64}$' } | ForEach-Object { $_.Split(' ')[1] })
    $recordChanges = @($probe | Where-Object { $_ -eq 'record-changes' }).Count -gt 0
    $notes = @($probe | Where-Object { -not $_.StartsWith('would add') -and -not $_.StartsWith('after-sha256') -and
                                       $_ -ne 'record-changes' } | ForEach-Object { "hooks        $_" })
    if ($adds.Count -gt 0 -and $after.Count -ne 1) {
        return "hooks        $Target (not merged: the probe gave no hash of the result; $byHand)"
    }
    $added = @($adds | ForEach-Object { "hooks+       $Target " + $_.Substring('would add'.Length).Trim() })
    if ($DryRun -or ($adds.Count -eq 0 -and -not $recordChanges)) { return @($notes + $added) }
    if ($adds.Count -gt 0) {
        Backup-KitFile $Target
        Add-KitBackupLine "merged $Target $($after[0])"
    }
    if ($recordChanges) {
        if (Test-Path -LiteralPath $record -PathType Leaf) {
            Backup-KitFile $record
            Add-KitBackupLine "overwritten $record"
        } else {
            Add-KitBackupLine "created $record"
        }
    }
    $ErrorActionPreference = 'Continue'
    $env:PYTHONIOENCODING = 'utf-8'
    try {
        $done = @(& $exe @pre $Target 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous; $env:PYTHONIOENCODING = $savedIo }
    if (Test-Path -LiteralPath $record -PathType Leaf) { $manifest[$record] = Get-Sha256File $record }
    if ($code -ne 0) { return "hooks        $Target (not merged: $((@($done) + '')[0]); $byHand)" }
    return @($notes + $added)
}

function Get-KitGradleHookNote {
    <#
    One line for a project run that would otherwise leave the gradle hook unwired without a word, or
    nothing. It remembers nothing and predicts nothing: it holds the project's settings.json as it
    was before the plan against the file as it is after it. A dry run writes nothing, so there the
    file after is what the plan's own line for it says: the rendered text behind write, refresh and
    force, the file as it was behind every other verb.
    #>
    param([string]$Settings, [string]$Before, [string]$Rendered)
    $filter = 'filter-gradle-output.py'
    if ($DryRun) {
        $verb = ''
        foreach ($line in $script:KitPlanLines) {
            if ($line.EndsWith(" $Settings") -or $line.Contains(" $Settings (")) { $verb = $line.Split(' ')[0] }
        }
        $after = if (@('write', 'refresh', 'force') -contains $verb) { $Rendered } else { $Before }
    } else {
        $after = if (Test-Path -LiteralPath $Settings -PathType Leaf) { [string](Get-Content -LiteralPath $Settings -Raw) } else { '' }
    }
    $wired = $after.Contains($filter)
    if ($Before.Contains($filter) -and -not $wired) {
        # The file named the filter and no longer does: a run without the switch refreshed or forced it.
        if ($DryRun) { return "warn: a real run would leave the gradle hook unwired: it renders $Settings without it. Add -GradleHook to keep it wired." }
        return "warn: the gradle hook is no longer wired: this run rendered $Settings without it. Run again with -GradleHook to wire it again."
    }
    if ($GradleHook -and -not $wired) {
        # The switch was given and the file in place is the adopter's own, kept, with the kit's beside it.
        if ($DryRun) { return "warn: a real run would not wire the gradle hook yet: it keeps $Settings as it is, and the hook block to merge is then in ${Settings}.new." }
        return "warn: the gradle hook is not wired yet: $Settings is kept as it is, and the hook block to merge is in ${Settings}.new."
    }
    return $null
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
    $findings = @(Get-AdoptionFindings $proj $tpl $manifest -Force:$Force -GradleHook:$GradleHook)
    if ($findings) {
        $width = ($findings | ForEach-Object { $_.Item.Length } | Measure-Object -Maximum).Maximum
        foreach ($finding in $findings) { Write-Output ("  {0,-$width}  {1}" -f $finding.Item, $finding.Note) }
    } else {
        Write-Output "  nothing of yours is in the way; this looks like a fresh repository"
    }
    $writes = if ($Sdd) { 'AGENTS.md, CLAUDE.md, CLAUDE.project.md, .claude/, scripts/hooks/ and docs/ (the -Sdd spec tree)' }
              else { 'AGENTS.md, CLAUDE.md, CLAUDE.project.md, .claude/ and scripts/hooks/' }
    Write-Output "  the installer writes only $writes, and deletes nothing"
    Write-Output ""

    $projClaude = Join-Path $proj '.claude'
    # AGENTS.md is the project's canonical rules file for every agent and CLAUDE.md imports it (F03,
    # ADR-010); one the repository already has is kept and the kit's lands beside it as AGENTS.md.new.
    $pairs = @(
        (New-KitPair (Join-Path $tpl 'AGENTS.md')         (Join-Path $proj 'AGENTS.md')),
        (New-KitPair (Join-Path $tpl 'CLAUDE.md')         (Join-Path $proj 'CLAUDE.md')),
        (New-KitPair (Join-Path $tpl 'CLAUDE.project.md') (Join-Path $proj 'CLAUDE.project.md')),
        # rendered, so its branch hooks start Windows PowerShell by its full path (Set-KitPowerShellPath)
        (New-KitPair $null (Join-Path $projClaude 'settings.local.json') (Set-KitPowerShellPath (Get-Content -LiteralPath (Join-Path $tpl '.claude\settings.local.json') -Raw)))
    )
    foreach ($hook in @('branch-check.ps1', 'branch-upstream-fix.ps1')) {
        $pairs += New-KitPair (Join-Path $root "claude\hooks\$hook") (Join-Path $projClaude "hooks\$hook")
    }
    # The logged runner goes into every project: the scaffolded CLAUDE.md sends long tools through
    # scripts/hooks/run-logged.py. The gradle filter goes only with -GradleHook, and that switch was
    # settled before anything was written, so on such a run both scripts exist in the kit tree and
    # are the kit's in the project once the plan has run. Their tests (scripts/hooks/tests/) stay in
    # the kit: the runner also reads the kit home's hooks, so it checks the kit and not a project.
    foreach ($hookScript in @(Get-KitHookScripts -GradleHook:$GradleHook)) {
        $hookSource = Join-Path $tpl "scripts\hooks\$hookScript"
        if (Test-Path -LiteralPath $hookSource -PathType Leaf) {
            $pairs += New-KitPair $hookSource (Join-Path $proj "scripts\hooks\$hookScript")
        } else {
            # Only a plain run gets here. Nothing is wired to the runner, so the run goes on and says so.
            Write-Output "warn: this kit tree does not carry $hookSource,"
            Write-Output "      so this run writes no scripts/hooks/$hookScript into the project. The scaffolded"
            Write-Output "      CLAUDE.md names that runner; scaffold from a complete clone of the kit to get it."
            Write-Output ""
        }
    }
    # The project's settings are rendered, not copied. The tracked template names the source of
    # the single-channel plugins with placeholders, because the real one is this clone and a real
    # owner and repository committed to a shared template would be a leak. Both placeholders are
    # filled from the clone the installer is running out of, pinned to the commit it sits on, so
    # the project takes the plugins at exactly the version it was scaffolded from.
    #
    # A tree that cannot name its own source, a downloaded archive or a clone whose remote is not
    # called origin, drops both keys instead of shipping the placeholders: a project pointed at a
    # source that does not exist, with a plugin enabled against it, is worse off than a project
    # with no plugin source at all. It is said out loud rather than passed over in silence.
    $origin = Get-KitOrigin $root
    # One whole template or the other, never a text cut out of one: settings.gradle.json is
    # settings.json plus the gradle hook block, and the project suite keeps the two in step.
    $settingsTemplate = if ($GradleHook) { 'settings.gradle.json' } else { 'settings.json' }
    $projSettings = (Get-Content -LiteralPath (Join-Path $tpl ".claude\$settingsTemplate") -Raw)
    if ($origin) {
        $projSettings = $projSettings.Replace('<owner>/<repo>', $origin.Slug).Replace('<pinned-commit>', $origin.Sha)
    } else {
        foreach ($key in @('extraKnownMarketplaces', 'enabledPlugins')) {
            $projSettings = [regex]::Replace($projSettings, '(?sm)^  "' + $key + '": \{.*?^  \},\r?\n', '')
        }
        Write-Output "warn: this kit tree has no readable git origin, so the scaffolded project gets"
        Write-Output "      no plugin source: extraKnownMarketplaces and enabledPlugins are left out."
        Write-Output "      Install the plugins by hand, or scaffold from a clone with an origin remote."
        Write-Output ""
    }
    $pairs += New-KitPair $null (Join-Path $projClaude 'settings.json') $projSettings
    if ($Sdd) {
        $pairs += Get-KitPairs (Join-Path $root 'claude\agents') (Join-Path $projClaude 'agents')
        $pairs += Get-KitPairs (Join-Path $tpl 'docs') (Join-Path $proj 'docs')
    }

    # Read before the plan and held against the result after it, so a run that leaves the gradle hook
    # unwired says so in one line instead of passing in silence.
    $settingsTarget = Join-Path $projClaude 'settings.json'
    $settingsBefore = if (Test-Path -LiteralPath $settingsTarget -PathType Leaf) { [string](Get-Content -LiteralPath $settingsTarget -Raw) } else { '' }
    Invoke-KitPlan $pairs "project scope install at $proj"
    $hookNote = Get-KitGradleHookNote $settingsTarget $settingsBefore $projSettings
    if ($hookNote) {
        Write-Output ""
        Write-Output $hookNote
    }

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
    if ($Sdd) { Write-Output "SDD scaffolding added: .claude/agents/ + docs/. Drive it with /sdd, or /delivery:sdd from the marketplace." }
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
# The seats are prompt, not agent definitions: they live in their own folder so nothing can
# delegate to a seat, and the launcher appends the file of the role it was given.
$pairs += Get-KitPairs (Join-Path $root 'claude\seats')  (Join-Path $kitHome 'seats')
# The skills that ship as a plugin land here too, under their bare names, so an installer machine
# sees no difference. A machine takes them through the installer or through the marketplace, never
# both at once (README, "Plugin marketplace" in INSTALL.md). Read from the folder, so adding a
# plugin needs no edit here.
#
# The exception is a plugin that declares itself single-channel: a scaffolded project enables it
# for itself (see the project scope above), so copying its skills here would put two copies of
# each on the machine, one bare and one namespaced, free to drift. The declaration is a keyword
# in the plugin's own manifest, read here rather than a list of names held in this script, and a
# keyword because the format's own checker fails --strict on a field it does not know.
$pluginRoot = Join-Path $root 'plugins'
if (Test-Path -LiteralPath $pluginRoot) {
    foreach ($plugin in (Get-ChildItem -LiteralPath $pluginRoot -Directory)) {
        if (Test-KitPluginChannelOnly $plugin.FullName) {
            Write-Output "skip plugin  $($plugin.Name) (single channel, installed with claude plugin install)"
            continue
        }
        $pairs += Get-KitPairs (Join-Path $plugin.FullName 'skills') (Join-Path $kitHome 'skills')
    }
}
# The account switcher and its settings merger are optional: nothing else depends on them. cc.cmd in
# <kit home>\bin, a folder the run puts on the user PATH, makes cc a command in any new terminal: it starts
# cc-launch.ps1 with -ExecutionPolicy Bypass, so no PowerShell profile and no policy change is needed
# (docs/ACCOUNTS.md). cc.cmd finds the launcher from its own folder, so it names no path.
foreach ($file in @('statusline.ps1', 'claude-account.ps1', 'cc-launch.ps1', 'merge-settings.py', 'CLAUDE.md', 'bin\cc.cmd')) {
    $pairs += New-KitPair (Join-Path $root "claude\$file") (Join-Path $kitHome $file)
}

# Rendered from the kit home, not from the profile: KIT_HOME moves where the files land, so it has
# to move what the hook commands inside settings.json point at, or they point at an empty tree.
# Both slash flavours appear in the file, and the backslash one is JSON-escaped.
$settingsText = (Get-Content (Join-Path $root 'claude\settings.json') -Raw)
$settingsText = $settingsText.Replace('%USERPROFILE%/.claude', $kitHome.Replace('\', '/'))
$settingsText = $settingsText.Replace('%USERPROFILE%\\.claude', $kitHome.Replace('\', '\\'))
$settingsText = $settingsText.Replace('%USERPROFILE%', ($env:USERPROFILE).Replace('\', '\\'))
# The hooks and the status line start Windows PowerShell by its full path (Set-KitPowerShellPath in install\lib.ps1).
$settingsText = Set-KitPowerShellPath $settingsText

# Permission mode. Two keys move together: the mode itself, and whether Claude Code
# warns you when it starts in the dangerous one.
if ($Permissions -eq 'ask') {
    $settingsText = $settingsText -replace '"defaultMode"\s*:\s*"bypassPermissions"', '"defaultMode": "default"'
    $settingsText = $settingsText -replace '"skipDangerousModePermissionPrompt"\s*:\s*true', '"skipDangerousModePermissionPrompt": false'
}
$pairs += New-KitPair $null (Join-Path $kitHome 'settings.json') $settingsText
$script:KitHookMergeTarget = Join-Path $kitHome 'settings.json'
# The previous kit render, read before the plan refreshes the .new: the hook merge seeds its record from it
# once, on a machine that has no record yet.
$script:KitHookMergePrevious = $null
if (Test-Path -LiteralPath "$($script:KitHookMergeTarget).new" -PathType Leaf) {
    $script:KitHookMergePrevious = [IO.File]::ReadAllText("$($script:KitHookMergeTarget).new", [Text.Encoding]::UTF8)
}

Write-Output "Kit home: $kitHome"
Invoke-KitPlan $pairs 'user scope install'
# The folder of cc.cmd goes on the user PATH once; a terminal opened after this run finds cc.
$ccPath = Add-KitUserPath (Join-Path $kitHome 'bin') -DryRun:$DryRun
if ($ccPath) {
    Write-Output ""
    Write-Output "  $ccPath"
    if (-not $DryRun) { Write-Output "  cc, the account switcher, works in any terminal opened from now on (docs/ACCOUNTS.md)." }
}

if ($Permissions -eq 'bypass') {
    Write-Output ""
    Write-Output "  PERMISSIONS: bypass. Claude Code will act without asking for confirmation."
    Write-Output "  Re-run with -Permissions ask to keep the prompts. See docs/PERMISSIONS.md."
} else {
    Write-Output ""
    Write-Output "  PERMISSIONS: ask. Claude Code will prompt before acting."
    Write-Output "  Some skills in this base assume bypass and will stop on each step."
}
Install-KitHerdr -KitRoot $root -KitHome $kitHome -DryRun:$DryRun `
    -Choice (Get-KitHerdrChoice -Yes $Herdr.IsPresent -No $NoHerdr.IsPresent -DryRun $DryRun.IsPresent)
Write-Output ""
Write-Output "Done."
Write-Output "Run: python scripts\doctor.py"
