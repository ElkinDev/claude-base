# Switch between several Claude Code accounts without logging out.
#
# How it works:
#   - Each account lives in its own folder under ~\.claude-accounts\<name>, with its own
#     .credentials.json and .claude.json. The OAuth token is in there, so pointing
#     CLAUDE_CONFIG_DIR at one folder or another swaps the whole account.
#   - The variable is set with $env:, so it only lives in the window or tab this script
#     opens. NEVER use setx for this: that would break every session at once.
#   - You can have every account open at the same time, one per window.
#   - Shared assets (skills, plugins, hooks, agents, commands, and the projects folder
#     that holds session transcripts and agent memory) are linked back to the default
#     directory with directory junctions, so you do not keep N copies of them.
#   - CLAUDE.md and settings.json are brought over from the default directory, but never
#     overwrite a change you made inside a profile. Use -Sync to force it.
#
# 'default' is a reserved name meaning ~\.claude itself, the directory plain `claude`
# uses when CLAUDE_CONFIG_DIR is not set. It cannot be moved, only relabelled.
#
# Quick start:  cc ?             full help
#               cc               list the accounts
#               cc work          open that account here
#
# See docs/ACCOUNTS.md for the reasoning behind every design decision.

param(
    [Parameter(Position = 0)][string]$Account,
    [Alias("Dir")][string]$Folder = (Get-Location).Path,
    [Alias("l")][switch]$List,
    [Alias("d")][switch]$Delete,
    [Alias("r")][string]$Rename,
    [Alias("s")][switch]$Sync,
    [Alias("p")][switch]$Prepare,
    [Alias("w")][switch]$Tab,
    [Alias("n")][switch]$NoShare,
    [Alias("a")][string]$Alias,
    [Alias("i")][string]$Icon,
    [Alias("h")][switch]$Help,
    [Alias("o")][ValidateSet("orchestrator", "lane", "research")][string]$Role = "lane",
    # Both names are chosen so that neither claims a prefix claude still needs. claude's own
    # single-dash flags are single letters (-c -d -h -n -p -r -v -w, from `claude --help`), the
    # launcher forwards an unbound flag to claude in $Extra, and PowerShell binds a parameter by
    # unambiguous prefix, so a name here must not begin with a letter that reaches claude today.
    # Of those eight, -d -h -n -p -r -w are already exact aliases below and an exact match beats a
    # prefix; -v is taken by the common parameter -Verbose, since the [Parameter()] attributes make
    # this an advanced script; so -c is the only one that still travels, and no name may start
    # with c. -w being the exact alias of -Tab is what makes -Window safe, and -s being the exact
    # alias of -Sync, with no claude flag spelled -Sh, is what makes -ShowEnv safe.
    [ValidateRange(0, 2000000)][int]$Window = 0,
    [switch]$ShowEnv,
    [Alias("x")][string]$Workspace,
    # Anything after `--` is handed to claude untouched. The separator is required
    # because flags like -c would collide with -Dir and -r with -Rename, and PowerShell
    # would try to bind them here before they ever reach claude.
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Extra
)

$ErrorActionPreference = "Stop"
$DefaultDir = "$env:USERPROFILE\.claude"
$Base       = "$env:USERPROFILE\.claude-accounts"
$NamesFile  = "$Base\.names.json"

function Fail($m) { Write-Host "  FAILED: $m" -ForegroundColor Red; exit 1 }
function Ok($m)   { Write-Host "  $m" -ForegroundColor DarkGray }

# Every session is named after its role (`claude --name`). The name shows in the prompt box,
# the /resume picker and the terminal title, and it is written to the session transcript, so
# a person or a script finds "orchestrator" on any machine and in any terminal, whatever
# pane or workspace number the multiplexer gives it. Only when -Role was given: `--name` also
# renames a session reopened with -c or -r, and a plain `cc work -- -c` must not turn the
# orchestrator it resumes into "lane". Pass `-- --name x` to choose another name.
$roleGiven = $PSBoundParameters.ContainsKey("Role")
if ($roleGiven -and -not (@($Extra) -contains "--name" -or @($Extra) -contains "-n")) { $Extra = @("--name", $Role) + @($Extra | Where-Object { $_ -ne $null }) }

# Extra arguments travel inside a string when launching in a new tab or window, so
# quote the ones carrying spaces.
$extraStr = ""
if ($Extra) {
    $extraStr = " " + (($Extra | ForEach-Object {
        if ($_ -match '\s') { "'" + $_.Replace("'", "''") + "'" } else { $_ }
    }) -join " ")
}


# --- pane role: context cap and label ---------------------------------------------
# orchestrator and lane run with the context capped at 200k (CLAUDE_CODE_DISABLE_1M_CONTEXT=1);
# research is the only uncapped role. CLAUDE_ROLE travels to the process so hooks know which
# pane they are in (the read guard denies images only to the orchestrator).
#
# -Window <tokens> is the opt-in exception. It drops the cap and lets auto-compaction
# fire at the window you name, which buys fewer compactions (each one costs a summary, a
# re-injection burst and a re-orientation) at the price of a larger floor on every turn and
# more cache breaks. Both variables are read by claude.exe, verified against 2.1.258:
#   if(process.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW){let B=kte("CLAUDE_CODE_AUTO_COMPACT_WINDOW",...
#   function zN(){return a.CLAUDE_CODE_DISABLE_1M_CONTEXT}
# and the binary says of the first: "CLAUDE_CODE_AUTO_COMPACT_WINDOW is set and takes
# precedence. Unset it to change this setting." The equivalent settings key is
# autoCompactWindow ("Auto-compact window size"), which the variable overrides.
# Without the switch nothing here changes, and an inherited window variable is left untouched.
$capContext = ($Role -ne "research") -and ($Window -le 0)
$roleEnvPs = "`$env:CLAUDE_ROLE = '$Role'; " + $(if ($capContext) { "`$env:CLAUDE_CODE_DISABLE_1M_CONTEXT = '1'" } else { "Remove-Item Env:\CLAUDE_CODE_DISABLE_1M_CONTEXT -ErrorAction SilentlyContinue" })
if ($Window -gt 0) { $roleEnvPs += "; `$env:CLAUDE_CODE_AUTO_COMPACT_WINDOW = '$Window'" }
function Apply-Role {
    $env:CLAUDE_ROLE = $Role
    if ($capContext) { $env:CLAUDE_CODE_DISABLE_1M_CONTEXT = "1" }
    else { Remove-Item Env:\CLAUDE_CODE_DISABLE_1M_CONTEXT -ErrorAction SilentlyContinue }
    if ($Window -gt 0) { $env:CLAUDE_CODE_AUTO_COMPACT_WINDOW = "$Window" }
}
# -ShowEnv prints that plan and exits, so the wiring can be asserted without opening a session.
if ($ShowEnv) {
    Write-Output "CLAUDE_ROLE=$Role"
    Write-Output ("CLAUDE_CODE_DISABLE_1M_CONTEXT=" + $(if ($capContext) { "1" } else { "(unset)" }))
    Write-Output ("CLAUDE_CODE_AUTO_COMPACT_WINDOW=" + $(if ($Window -gt 0) { "$Window" } else { "(unchanged)" }))
    Write-Output "PANE_COMMAND=$roleEnvPs"
    # What the launcher hands to claude untouched, so a test can see that an unbound flag was
    # forwarded and not swallowed by a parameter of this script.
    Write-Output ("EXTRA=" + ($Extra -join " "))
    exit 0
}
$roleTag = if ($Role -eq "lane") { "" } else { "-$Role" }
# Agents never use the browser: orchestrator and lane sessions start without the Chrome
# integration, which keeps its instructions and tools out of every context. Pass --chrome
# explicitly, or use -Role research, when a session needs the browser.
if ($Role -ne "research" -and -not ($extraStr -match "chrome")) { $extraStr += " --no-chrome" }
# -Workspace takes the id (w8) or the number the UI shows (5); the number is resolved here.
function Herdr-Workspace-Args {
    if (-not $Workspace) { return @() }
    $id = $Workspace
    if ($Workspace -match '^\d+$') {
        try {
            $ws = (herdr workspace list | ConvertFrom-Json).result.workspaces | Where-Object { [string]$_.number -eq $Workspace } | Select-Object -First 1
        } catch { Fail "could not read the Herdr workspaces." }
        if (-not $ws) { Fail "there is no Herdr workspace number $Workspace." }
        $id = $ws.workspace_id
    }
    return @("--workspace", $id)
}

# Folders shared with the default directory. Missing ones are skipped.
$Linked = @("skills", "plugins", "hooks", "agents", "commands", "projects")
# Files brought over from the default directory.
$Copied = @("CLAUDE.md", "settings.json")

# --- names, shortcuts and icons ---------------------------------------------------
# ~\.claude cannot move, because it is the directory plain `claude` uses, so its display
# name is stored here instead. Shortcuts map a short string to a target, and a target is
# either a profile name or the reserved 'default'.
function Read-Names {
    if (-not (Test-Path $NamesFile)) { return $null }
    try { return (Get-Content $NamesFile -Raw | ConvertFrom-Json) } catch { return $null }
}

function Save-Names($obj) {
    New-Item -ItemType Directory -Force -Path $Base | Out-Null
    $j = $obj | ConvertTo-Json -Depth 6
    # No BOM on purpose: Set-Content -Encoding utf8 adds one on PowerShell 5.1, and
    # Node's JSON.parse rejects a leading BOM.
    [System.IO.File]::WriteAllText($NamesFile, $j, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-DefaultName($n) {
    if ($n -and $n.default) { return [string]$n.default }
    return "default"
}

# Follows the shortcut chain to the real target. The loop cap stops two shortcuts that
# point at each other from hanging the script.
function Resolve-Target($name, $n) {
    if (-not $name) { return $null }
    $x = $name
    for ($i = 0; $i -lt 5; $i++) {
        if ($n -and $n.shortcuts -and $n.shortcuts.PSObject.Properties[$x]) { $x = [string]$n.shortcuts.$x }
        else { break }
    }
    return $x
}

function Get-Shortcut($target, $n) {
    if (-not ($n -and $n.shortcuts)) { return $null }
    foreach ($p in $n.shortcuts.PSObject.Properties) {
        if ([string]$p.Value -eq $target) { return $p.Name }
    }
    return $null
}

# Icons are stored as hex code points, not as the emoji itself. PowerShell 5.1 and JSON
# files disagree about surrogate pairs, and the emoji would corrupt on a rewrite.
function Get-Icon($target, $n) {
    $hex = $null
    if ($n -and $n.icons -and $n.icons.PSObject.Properties[$target]) { $hex = [string]$n.icons.$target }
    if (-not $hex) { $hex = if ($target -eq "default") { "2B50" } else { "1F464" } }
    try { return [char]::ConvertFromUtf32([Convert]::ToInt32($hex, 16)) } catch { return "" }
}

# Accepts either the pasted emoji or a hand written code point, always stores hex.
function ConvertTo-IconHex($value) {
    if ($value -match "^[0-9a-fA-F]{4,6}$") { return $value.ToUpper() }
    $si = [System.Globalization.StringInfo]::new($value)
    if ($si.LengthInTextElements -lt 1) { return $null }
    return ([char]::ConvertToUtf32($value, 0)).ToString("X")
}

function Test-Junction($path) {
    if (-not (Test-Path $path)) { return $false }
    $i = Get-Item $path -Force
    return [bool]($i.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
}

function Get-AccountEmail($json, $cred) {
    if (Test-Path $json) {
        $text = Get-Content $json -Raw
        try {
            $d = $text | ConvertFrom-Json
            if ($d.oauthAccount -and $d.oauthAccount.emailAddress) { return $d.oauthAccount.emailAddress }
        } catch {
            # PowerShell 5.1 blows up when the JSON holds two keys differing only in
            # case, which happens on its own with paths like c:\ and C:\. A regex is
            # enough to show a label.
            if ($text -match '"emailAddress"\s*:\s*"([^"]+)"') { return $Matches[1] }
        }
    }
    if (Test-Path $cred) { return "signed in, e-mail not cached" }
    return "not signed in yet"
}

function Write-Row($mark, $name, $short, $text, $icon) {
    $label = $name
    if ($short) { $label = "$name ($short)" }
    if (-not $icon) { $icon = " " }
    Write-Host ("  {0} {1} {2,-20} {3}" -f $mark, $icon, $label, $text)
}

function Show-List {
    $n = Read-Names
    Write-Host ""
    Write-Host "Claude Code accounts" -ForegroundColor Cyan
    Write-Host ""
    $active = $env:CLAUDE_CONFIG_DIR

    $mark = " "
    if (-not $active) { $mark = "*" }
    $defName = Get-DefaultName $n
    Write-Row $mark $defName (Get-Shortcut "default" $n) (Get-AccountEmail "$env:USERPROFILE\.claude.json" "$DefaultDir\.credentials.json") (Get-Icon "default" $n)
    Write-Host ("  {0}   {1,-20} {2}" -f " ", "", "(~\.claude, the one plain 'claude' uses)") -ForegroundColor DarkGray

    if (Test-Path $Base) {
        Get-ChildItem $Base -Directory | ForEach-Object {
            $m = " "
            if ($active -and ($active.TrimEnd("\") -eq $_.FullName.TrimEnd("\"))) { $m = "*" }
            $q = Get-AccountEmail (Join-Path $_.FullName ".claude.json") (Join-Path $_.FullName ".credentials.json")
            Write-Row $m $_.Name (Get-Shortcut $_.Name $n) $q (Get-Icon $_.Name $n)
        }
    }
    Write-Host ""
    Write-Host "  The asterisk is the account active in THIS window. Shortcut in brackets." -ForegroundColor DarkGray
    Write-Host ""
}

function Show-Help {
    Write-Host ""
    Write-Host "cc  -  switch Claude Code accounts without logging out" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  cc                       list the accounts and which one is active"
    Write-Host "  cc <account>             open that account HERE, in this window"
    Write-Host "  cc <account> -w          open it in a separate tab or window"
    Write-Host "  cc <account> -- <args>   anything after -- goes to claude untouched"
    Write-Host "  cc ?                     this help"
    Write-Host ""
    Write-Host "Passing flags to claude" -ForegroundColor Cyan
    Write-Host "  cc work -- -c            continue that account's last session"
    Write-Host "  cc work -- -r            pick which session to resume"
    Write-Host "  The -- is required: without it -c collides with -Dir and -r with -Rename."
    Write-Host ""
    Write-Host "Options" -ForegroundColor Cyan
    Write-Host "  -Folder <path>   -Dir    where to open (defaults to the current folder)"
    Write-Host "  -List            -l      list only"
    Write-Host "  -Tab             -w      open in a separate tab or window"
    Write-Host "  -Prepare         -p      create the profile without opening a session"
    Write-Host "  -Sync            -s      bring CLAUDE.md and settings.json from the default"
    Write-Host "  -Rename <new>    -r      rename the account"
    Write-Host "  -Alias <short>   -a      set a shortcut for it (one per account)"
    Write-Host "  -Icon <emoji>    -i      icon shown in the list and the status line"
    Write-Host "  -Delete          -d      delete the profile, removing junctions first"
    Write-Host "  -NoShare         -n      create it without linking skills or memory"
    Write-Host "  -Role <role>     -o      orchestrator | lane (default) | research; the first two cap"
    Write-Host "                           the context at 200k, research runs uncapped; when given, the role"
    Write-Host "                           also names the session (claude --name), even one reopened with -c"
    Write-Host "  -Window <n>              opt in to a larger auto-compact window: drops the 200k cap and"
    Write-Host "                           sets CLAUDE_CODE_AUTO_COMPACT_WINDOW=<n>. Fewer compactions, a"
    Write-Host "                           larger floor on every turn. Off unless you pass it"
    Write-Host "  -ShowEnv                 print the variables the session would get, then exit"
    Write-Host "  -Workspace <n>   -x      with -Tab: Herdr workspace (number or id) for the new tab"
    Write-Host "  -Help            -h      this help"
    Write-Host ""
    Write-Host "Examples" -ForegroundColor Cyan
    Write-Host "  cd C:\code\my-app ; cc work     open the work account there"
    Write-Host "  cc work -- -c                   open it continuing the last session"
    Write-Host "  cc personal -a p                give it the shortcut 'p'"
    Write-Host "  cc old -d                       delete that profile"
    Write-Host ""
}

if ($Help -or ($Account -eq "?") -or ($Account -eq "help")) { Show-Help; exit 0 }

if ($List -or (-not $Account)) {
    Show-List
    if (-not $Account) { Write-Host "  cc ? for every option." -ForegroundColor DarkGray; Write-Host "" }
    exit 0
}

if ($Account -notmatch "^[a-zA-Z0-9._-]+$") { Fail "invalid name. Use letters, digits, dot, dash or underscore." }
if (-not (Test-Path $Folder)) { Fail "no such folder: $Folder" }
$Folder = (Resolve-Path $Folder).Path
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { Fail "cannot find the claude command" }

# --- resolve which account we are talking about ----------------------------------
# This must stay ABOVE every block that uses $isDefault. When it sat below, the variable
# arrived empty at the first if, the shortcut did not resolve, and instead of opening
# ~\.claude the script created a new profile named after the shortcut.
$names = Read-Names
$defaultName = Get-DefaultName $names
$target = Resolve-Target $Account $names
$isDefault = (($target -eq "default") -or ($target -eq $defaultName))

# --- set a shortcut ---------------------------------------------------------------
if ($Alias) {
    if ($Alias -notmatch "^[a-zA-Z0-9._-]+$") { Fail "invalid characters in the shortcut." }
    if ($Alias -eq $target) { Fail "a shortcut cannot be the same as its target." }
    if (Test-Path (Join-Path $Base $Alias)) { Fail "'$Alias' is already a real profile name." }
    if (-not ($isDefault -or (Test-Path (Join-Path $Base $target)))) {
        Fail "no such account '$target'. Create it first with: cc $target -p"
    }
    $canonical = if ($isDefault) { "default" } else { $target }

    if (-not $names) { $names = [pscustomobject]@{} }
    if (-not $names.PSObject.Properties["shortcuts"]) {
        $names | Add-Member -NotePropertyName shortcuts -NotePropertyValue ([pscustomobject]@{}) -Force
    }
    # One shortcut per account. Keeping the old ones would leave two names for the same
    # thing, which is exactly the confusion this is meant to prevent.
    foreach ($p in @($names.shortcuts.PSObject.Properties)) {
        if (([string]$p.Value -eq $canonical) -and ($p.Name -ne $Alias)) {
            $names.shortcuts.PSObject.Properties.Remove($p.Name)
            Ok "previous shortcut removed: $($p.Name)"
        }
    }
    $names.shortcuts | Add-Member -NotePropertyName $Alias -NotePropertyValue $canonical -Force
    Save-Names $names
    Write-Host ""
    Write-Host "Shortcut set: cc $Alias  ->  $canonical" -ForegroundColor Green
    exit 0
}

# --- set the icon -----------------------------------------------------------------
if ($Icon) {
    if (-not ($isDefault -or (Test-Path (Join-Path $Base $target)))) { Fail "no such account '$target'." }
    $hex = ConvertTo-IconHex $Icon
    if (-not $hex) { Fail "cannot read that icon. Pass an emoji or its code point, for example 1F3E0." }
    $canonical = if ($isDefault) { "default" } else { $target }

    if (-not $names) { $names = [pscustomobject]@{} }
    if (-not $names.PSObject.Properties["icons"]) {
        $names | Add-Member -NotePropertyName icons -NotePropertyValue ([pscustomobject]@{}) -Force
    }
    $names.icons | Add-Member -NotePropertyName $canonical -NotePropertyValue $hex -Force
    Save-Names $names
    Write-Host ""
    Write-Host "Icon for $canonical : $([char]::ConvertFromUtf32([Convert]::ToInt32($hex,16)))  (U+$hex)" -ForegroundColor Green
    exit 0
}

# --- rename -----------------------------------------------------------------------
if ($Rename) {
    if ($Rename -notmatch "^[a-zA-Z0-9._-]+$") { Fail "invalid characters in the new name." }
    if (Test-Path (Join-Path $Base $Rename)) { Fail "a profile named '$Rename' already exists." }

    if ($isDefault) {
        New-Item -ItemType Directory -Force -Path $Base | Out-Null
        if (-not $names) { $names = [pscustomobject]@{} }
        if ($Rename -eq "default") {
            $names.PSObject.Properties.Remove("default")
            Save-Names $names
            Write-Host "The default account is called 'default' again." -ForegroundColor Green
            exit 0
        }
        $names | Add-Member -NotePropertyName default -NotePropertyValue $Rename -Force
        Save-Names $names
        Write-Host ""
        Write-Host "The default account is now called '$Rename'." -ForegroundColor Green
        Write-Host "That is only the name you invoke it by. The folder stays ~\.claude," -ForegroundColor DarkGray
        Write-Host "because that is the one plain 'claude' uses and it cannot move." -ForegroundColor DarkGray
        exit 0
    }

    if ($Rename -eq "default") { Fail "'default' is reserved for ~\.claude." }
    $srcProfile = Join-Path $Base $target
    if (-not (Test-Path $srcProfile)) { Fail "no such profile '$target'." }
    Rename-Item -Path $srcProfile -NewName $Rename
    # Shortcuts and the icon must follow the new name.
    $moved = $false
    if ($names -and $names.shortcuts) {
        foreach ($p in @($names.shortcuts.PSObject.Properties)) {
            if ([string]$p.Value -eq $target) { $names.shortcuts | Add-Member -NotePropertyName $p.Name -NotePropertyValue $Rename -Force; $moved = $true }
        }
    }
    if ($names -and $names.icons -and $names.icons.PSObject.Properties[$target]) {
        $hex = [string]$names.icons.$target
        $names.icons.PSObject.Properties.Remove($target)
        $names.icons | Add-Member -NotePropertyName $Rename -NotePropertyValue $hex -Force
        $moved = $true
    }
    if ($moved) { Save-Names $names; Ok "shortcut and icon now point at '$Rename'" }
    Write-Host ""
    Write-Host "Profile renamed: $target -> $Rename" -ForegroundColor Green
    Write-Host "Junctions store absolute targets, so they keep pointing at the right place." -ForegroundColor DarkGray
    exit 0
}

# --- the default account launches as is, with no isolation ------------------------
if ($isDefault) {
    if ($Delete) { Fail "the default account cannot be deleted from here." }
    Write-Host ""
    Write-Host "Account '$defaultName' (~\.claude), no CLAUDE_CONFIG_DIR." -ForegroundColor Cyan
    # -Prepare creates and links a profile, and there is no profile to create here.
    # Without this guard it fell through to launching claude, the opposite of the flag.
    if ($Prepare) {
        Write-Host "  Nothing to prepare: ~\.claude already exists and is the default." -ForegroundColor DarkGray
        exit 0
    }
    if (-not $Tab) {
        Remove-Item Env:\CLAUDE_CONFIG_DIR -ErrorAction SilentlyContinue
        Apply-Role
        Set-Location $Folder
        claude @Extra
        exit 0
    }
    if ($env:HERDR_ENV -eq "1" -and (Get-Command herdr -ErrorAction SilentlyContinue)) {
        $wsArgs = Herdr-Workspace-Args
        $j = herdr tab create @wsArgs --cwd "$Folder" --label "cc-$defaultName$roleTag" --no-focus | ConvertFrom-Json
        $pane = $j.result.root_pane.pane_id
        herdr pane run $pane "Remove-Item Env:\CLAUDE_CONFIG_DIR -ErrorAction SilentlyContinue; $roleEnvPs; claude$extraStr" | Out-Null
        Write-Host "Opened in Herdr tab cc-$defaultName$roleTag (pane $pane, role $Role)." -ForegroundColor Green
        exit 0
    }
    Start-Process powershell -ArgumentList @("-NoExit", "-Command", "Set-Location '$Folder'; $roleEnvPs; claude$extraStr")
    exit 0
}

$ProfileDir = Join-Path $Base $target

# --- delete -----------------------------------------------------------------------
if ($Delete) {
    if (-not (Test-Path $ProfileDir)) { Fail "no such profile '$target'" }
    Write-Host ""
    Write-Host "About to delete profile $target at $ProfileDir" -ForegroundColor Yellow
    Write-Host "Junctions are removed first, so your shared skills and memory are safe." -ForegroundColor DarkGray
    $answer = Read-Host "  Confirm? (y/N)"
    if ($answer -ne "y") { Write-Host "  cancelled"; exit 0 }
    foreach ($nn in $Linked) {
        $l = Join-Path $ProfileDir $nn
        # rmdir on a junction removes the link only. Remove-Item -Recurse can descend
        # into it and delete the real contents on the other side, which here would be
        # your skills and your agent memory.
        if (Test-Junction $l) { cmd /c rmdir "$l" | Out-Null; Ok "junction removed: $nn" }
    }
    Remove-Item -Recurse -Force $ProfileDir
    if (-not (Test-Path "$DefaultDir\skills")) { Fail "WARNING: $DefaultDir\skills is gone. Check before continuing." }
    $cleaned = $false
    if ($names -and $names.shortcuts) {
        foreach ($p in @($names.shortcuts.PSObject.Properties)) {
            if ([string]$p.Value -eq $target) { $names.shortcuts.PSObject.Properties.Remove($p.Name); $cleaned = $true }
        }
    }
    if ($names -and $names.icons -and $names.icons.PSObject.Properties[$target]) {
        $names.icons.PSObject.Properties.Remove($target); $cleaned = $true
    }
    if ($cleaned) { Save-Names $names; Ok "orphaned shortcut and icon removed" }
    Ok "profile deleted, the default directory is untouched"
    exit 0
}

# --- create or refresh the profile ------------------------------------------------
Write-Host ""
Write-Host "Account $target" -ForegroundColor Cyan
$isNew = -not (Test-Path $ProfileDir)
New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null
if ($isNew) { Ok "profile created at $ProfileDir" }

if (-not $NoShare) {
    foreach ($nn in $Linked) {
        $src  = Join-Path $DefaultDir $nn
        $link = Join-Path $ProfileDir $nn
        if (-not (Test-Path $src)) { continue }
        if (Test-Junction $link) { continue }
        if (Test-Path $link) { Write-Host "  note: $nn exists as a real folder, not linking it" -ForegroundColor Yellow; continue }
        # mklink /J makes a directory junction and needs no administrator rights.
        cmd /c mklink /J "$link" "$src" | Out-Null
        if ($LASTEXITCODE -eq 0) { Ok "shared: $nn" } else { Write-Host "  note: could not link $nn" -ForegroundColor Yellow }
    }
}

# Bring files over from the default directory WITHOUT overwriting what you changed
# inside the profile. The stamp holds the hash of what was last copied here. If the
# profile file no longer matches that hash, something else edited it (you, or Claude
# Code writing settings on /config) and it is left alone. Modification dates do not
# work for this: a date only says which file is newer, not whether the profile drifted
# from what it was given.
$stampPath = Join-Path $ProfileDir ".sync-source.json"
$stamp = @{}
if (Test-Path $stampPath) {
    try {
        $s = Get-Content $stampPath -Raw | ConvertFrom-Json
        foreach ($p in $s.PSObject.Properties) { $stamp[$p.Name] = $p.Value }
    } catch { $stamp = @{} }
}
$stampChanged = $false
$merger = Join-Path $PSScriptRoot "merge-settings.py"

# JSON files are MERGED, never replaced: replacing drops keys Claude Code writes on its
# own, such as skipDangerousModePermissionPrompt, and then it asks to confirm bypass
# mode on every start. The helper refuses to write when the destination does not parse.
# Without Python it falls back to a plain copy and says so.
function Copy-Setting($src, $dst, $name) {
    if (($name -like "*.json") -and (Test-Path $merger) -and (Get-Command python -ErrorAction SilentlyContinue)) {
        $out = & python $merger $src $dst 2>&1
        if ($LASTEXITCODE -ne 0) { Write-Host "  $out" -ForegroundColor Yellow; return $false }
        Ok "$name : $out"
        return $true
    }
    if ($name -like "*.json") { Write-Host "  note: no python, copying $name instead of merging" -ForegroundColor Yellow }
    Copy-Item $src $dst -Force
    Ok "copied from the default: $name"
    return $true
}

foreach ($f in $Copied) {
    $src = Join-Path $DefaultDir $f
    $dst = Join-Path $ProfileDir $f
    if (-not (Test-Path $src)) { continue }
    $hSrc = (Get-FileHash $src -Algorithm SHA256).Hash

    if (-not (Test-Path $dst)) {
        if (Copy-Setting $src $dst $f) {
            $stamp[$f] = @{ source = $hSrc; dest = (Get-FileHash $dst -Algorithm SHA256).Hash }
            $stampChanged = $true
        }
        continue
    }

    $hDst = (Get-FileHash $dst -Algorithm SHA256).Hash
    $stampSrc = $null; $stampDst = $null
    if ($stamp.ContainsKey($f)) { $stampSrc = $stamp[$f].source; $stampDst = $stamp[$f].dest }

    $sourceChanged = ($stampSrc -ne $hSrc)
    $touchedHere   = ($stampDst -ne $hDst)
    if (-not $sourceChanged -and -not $touchedHere) { continue }
    # Only the profile moved: nothing new to bring, unless -Sync asks for a reset.
    if (-not $sourceChanged -and -not $Sync) { continue }

    # Merging keeps keys the profile owns, but if the profile gave a DIFFERENT value to
    # a key that also exists in the default, that one does get overwritten. Hence the
    # brake: once the profile has been touched, it takes an explicit -Sync.
    if ($touchedHere -and (-not $Sync)) {
        Write-Host "  $f changed inside this profile, leaving it alone." -ForegroundColor Yellow
        Write-Host "  To take the default's version:  cc $Account -s" -ForegroundColor DarkGray
        continue
    }
    if ($touchedHere) {
        Copy-Item $dst "$dst.bak" -Force
        Write-Host "  previous version saved as $f.bak" -ForegroundColor Yellow
    }
    if (Copy-Setting $src $dst $f) {
        $stamp[$f] = @{ source = $hSrc; dest = (Get-FileHash $dst -Algorithm SHA256).Hash }
        $stampChanged = $true
    }
}
if ($stampChanged) {
    $stampJson = [pscustomobject]$stamp | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText($stampPath, $stampJson, (New-Object System.Text.UTF8Encoding($false)))
}

# --- mark the folder trusted so the dialog does not come back every time ----------
$jsonPath = Join-Path $ProfileDir ".claude.json"
$key = $Folder -replace "\\", "/"
$d = $null
$unreadable = $false
if (Test-Path $jsonPath) {
    try { $d = Get-Content $jsonPath -Raw | ConvertFrom-Json }
    catch { $d = $null; $unreadable = $true }
}
$alreadyTrusted = $false
if ($d -and $d.projects -and $d.projects.PSObject.Properties[$key]) {
    if ($d.projects.$key.hasTrustDialogAccepted -eq $true) { $alreadyTrusted = $true }
}
# Hard rule: if the file exists and cannot be parsed, do not touch it. PowerShell 5.1
# fails on keys differing only in case (which happens on its own with c:\ and C:\
# paths), and rewriting from scratch would drop oauthAccount, signing the account out.
if ($unreadable) {
    Write-Host "  note: cannot read this profile's .claude.json, leaving it alone." -ForegroundColor Yellow
    Write-Host "  You will probably be asked to confirm the folder. Confirm and carry on." -ForegroundColor DarkGray
}
if ((-not $alreadyTrusted) -and (-not $unreadable)) {
    if ($null -eq $d) {
        $d = [pscustomobject]@{ hasCompletedOnboarding = $true; projects = [pscustomobject]@{} }
    }
    if ($null -eq $d.projects) { $d | Add-Member -NotePropertyName projects -NotePropertyValue ([pscustomobject]@{}) -Force }
    $entry = $null
    if ($d.projects.PSObject.Properties[$key]) { $entry = $d.projects.$key }
    if ($null -eq $entry) { $entry = [pscustomobject]@{} }
    $entry | Add-Member -NotePropertyName hasTrustDialogAccepted -NotePropertyValue $true -Force
    $entry | Add-Member -NotePropertyName hasClaudeMdExternalIncludesApproved -NotePropertyValue $true -Force
    $entry | Add-Member -NotePropertyName hasClaudeMdExternalIncludesWarningShown -NotePropertyValue $true -Force
    $d.projects | Add-Member -NotePropertyName $key -NotePropertyValue $entry -Force
    $json = $d | ConvertTo-Json -Depth 25
    [System.IO.File]::WriteAllText($jsonPath, $json, (New-Object System.Text.UTF8Encoding($false)))
    Ok "folder marked as trusted: $key"
}

$who = Get-AccountEmail $jsonPath (Join-Path $ProfileDir ".credentials.json")
Ok "session: $who"
if ($who -eq "not signed in yet") {
    Write-Host "  First time on this account: it will ask you to sign in. Once only." -ForegroundColor Yellow
    Write-Host "  Sign out of claude.ai in your browser first, or the OAuth flow will" -ForegroundColor Yellow
    Write-Host "  silently reuse the account already open there." -ForegroundColor Yellow
}

# --- launch -----------------------------------------------------------------------
if ($Prepare) {
    Write-Host ""
    Write-Host "Profile ready. Not opening a session because you passed -Prepare." -ForegroundColor Green
    Write-Host "  To use it:  cc $Account" -ForegroundColor DarkGray
    exit 0
}

$pre = "`$env:CLAUDE_CONFIG_DIR = '$ProfileDir'"

Write-Host ""
if (-not $Tab) {
    # This window stays on that account until you close it, including after you exit
    # claude. That is deliberate: the window becomes that account's window, and the
    # status line shows which one, so there is no way to lose track.
    Write-Host "This window is now on account $target. Use -Tab (-w) to open separately." -ForegroundColor DarkGray
    $env:CLAUDE_CONFIG_DIR = $ProfileDir
    Apply-Role
    Set-Location $Folder
    claude @Extra
    exit 0
}

if ($env:HERDR_ENV -eq "1" -and (Get-Command herdr -ErrorAction SilentlyContinue)) {
    $wsArgs = Herdr-Workspace-Args
    $j = herdr tab create @wsArgs --cwd "$Folder" --label "cc-$target$roleTag" --no-focus | ConvertFrom-Json
    $pane = $j.result.root_pane.pane_id
    herdr pane run $pane "$pre; $roleEnvPs; claude$extraStr" | Out-Null
    Write-Host "Opened in Herdr tab cc-$target$roleTag (pane $pane, role $Role)." -ForegroundColor Green
    exit 0
}

$title = "Claude Code  |  $target  |  $(Split-Path $Folder -Leaf)"
$cmd = "$pre; $roleEnvPs; Set-Location '$Folder'; " +
       "`$Host.UI.RawUI.WindowTitle = '$title'; " +
       "Write-Host 'Account: $target' -ForegroundColor Cyan; Write-Host ''; " +
       "claude$extraStr"
Start-Process powershell -ArgumentList @("-NoExit", "-Command", $cmd) -WindowStyle Normal
Write-Host "Window opened on account $target. Your other sessions are untouched." -ForegroundColor Green
