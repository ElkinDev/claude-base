#requires -Version 5.1
<#
install.ps1 - install the personal Claude Code base.

  User scope (default): copy skills, hooks, and the status line into %USERPROFILE%\.claude, and
  install settings.json only if none exists (an existing settings.json is backed up and a .new is
  written for you to merge, never clobbered).

  Project scope (-Project <path>): scaffold a project with CLAUDE.md, CLAUDE.project.md, a
  .claude/settings.local.json, and the branch hooks. Existing files are left untouched unless -Force.

Examples:
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Project C:\Repo\my-app
#>
param(
    [string]$Project,
    [switch]$Sdd,
    [switch]$Force,
    # How Claude Code asks before it acts. 'bypass' is what the base ships: no permission
    # prompts, which is fast and is what most of these skills assume. 'ask' keeps the
    # normal prompts. See docs/PERMISSIONS.md before choosing.
    [ValidateSet('bypass', 'ask')]
    [string]$Permissions = 'bypass'
)
$ErrorActionPreference = 'Stop'
$root  = $PSScriptRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

function Copy-Tree($src, $dst) {
    if (-not (Test-Path $src)) { return }
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Copy-Item -Path (Join-Path $src '*') -Destination $dst -Recurse -Force
}

if ($Project) {
    if (-not (Test-Path $Project)) { New-Item -ItemType Directory -Force -Path $Project | Out-Null }
    $proj = (Resolve-Path -LiteralPath $Project).Path
    $tpl  = Join-Path $root 'project-template'
    foreach ($f in @('CLAUDE.md', 'CLAUDE.project.md')) {
        $dest = Join-Path $proj $f
        if ((Test-Path $dest) -and -not $Force) { Write-Output "skip (exists): $dest" }
        else { Copy-Item (Join-Path $tpl $f) $dest -Force; Write-Output "wrote: $dest" }
    }
    $projClaude = Join-Path $proj '.claude'
    New-Item -ItemType Directory -Force -Path (Join-Path $projClaude 'hooks') | Out-Null
    $sl = Join-Path $projClaude 'settings.local.json'
    if ((Test-Path $sl) -and -not $Force) { Write-Output "skip (exists): $sl" }
    else { Copy-Item (Join-Path $tpl '.claude\settings.local.json') $sl -Force; Write-Output "wrote: $sl" }
    foreach ($h in @('branch-check.ps1', 'branch-upstream-fix.ps1')) {
        Copy-Item (Join-Path $root "claude\hooks\$h") (Join-Path $projClaude "hooks\$h") -Force
    }
    if ($Sdd) {
        New-Item -ItemType Directory -Force -Path (Join-Path $projClaude 'agents') | Out-Null
        Copy-Item (Join-Path $tpl '.claude\agents\*') (Join-Path $projClaude 'agents') -Recurse -Force
        $docsDst = Join-Path $proj 'docs'
        if ((Test-Path $docsDst) -and -not $Force) {
            Write-Output "skip (exists): $docsDst"
        } else {
            New-Item -ItemType Directory -Force -Path $docsDst | Out-Null
            Copy-Item (Join-Path $tpl 'docs\*') $docsDst -Recurse -Force
            Write-Output "wrote: $docsDst (spec structure)"
        }
        Write-Output "SDD scaffolding added: .claude/agents/ + docs/. Drive it with /sdd."
    }
    Write-Output ""
    Write-Output "Project scaffolded at $proj."
    Write-Output "Next: copy the closest project-template\profiles\*.md over CLAUDE.project.md and fill it."
    Write-Output "Keep local-only: add 'CLAUDE.project.md' and '.claude/' to .git/info/exclude if you do not want them committed."
    return
}

# ---- user scope ----
$claude = Join-Path $env:USERPROFILE '.claude'
New-Item -ItemType Directory -Force -Path $claude | Out-Null
Copy-Tree (Join-Path $root 'claude\skills') (Join-Path $claude 'skills')
Copy-Tree (Join-Path $root 'claude\hooks')  (Join-Path $claude 'hooks')
Copy-Item (Join-Path $root 'claude\statusline.ps1') (Join-Path $claude 'statusline.ps1') -Force
# Optional multi-account switcher and its settings merger. Nothing else depends on
# them; they sit unused until you wire up the cc function (see docs/ACCOUNTS.md).
Copy-Item (Join-Path $root 'claude\claude-account.ps1') (Join-Path $claude 'claude-account.ps1') -Force
Copy-Item (Join-Path $root 'claude\merge-settings.py')  (Join-Path $claude 'merge-settings.py')  -Force
Write-Output "Installed skills, hooks, statusline, and the account switcher into $claude"

# global working rules (backed up, never clobbered)
$claudeMdSrc = Join-Path $root 'claude\CLAUDE.md'
$claudeMdDst = Join-Path $claude 'CLAUDE.md'
if (Test-Path $claudeMdSrc) {
    if ((Test-Path $claudeMdDst) -and -not $Force) {
        Copy-Item $claudeMdDst "$claudeMdDst.$stamp.bak" -Force
        Copy-Item $claudeMdSrc "$claudeMdDst.new" -Force
        Write-Output "CLAUDE.md exists. Backed up to CLAUDE.md.$stamp.bak and wrote CLAUDE.md.new to merge."
    } else {
        Copy-Item $claudeMdSrc $claudeMdDst -Force
        Write-Output "Installed global CLAUDE.md into $claude"
    }
}

$settingsDst = Join-Path $claude 'settings.json'
$real        = $env:USERPROFILE.Replace('\', '\\')
$settingsSrc = (Get-Content (Join-Path $root 'claude\settings.json') -Raw).Replace('%USERPROFILE%', $real)

# Permission mode. Two keys move together: the mode itself, and whether Claude Code
# warns you when it starts in the dangerous one.
if ($Permissions -eq 'ask') {
    $settingsSrc = $settingsSrc -replace '"defaultMode"\s*:\s*"bypassPermissions"', '"defaultMode": "default"'
    $settingsSrc = $settingsSrc -replace '"skipDangerousModePermissionPrompt"\s*:\s*true', '"skipDangerousModePermissionPrompt": false'
}

# UTF-8 with NO BOM. Set-Content -Encoding utf8 adds one on PowerShell 5.1, and a BOM
# in front of JSON is rejected by Node's parser.
function Write-Json($path, $text) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

if ((Test-Path $settingsDst) -and -not $Force) {
    $bak = "$settingsDst.$stamp.bak"
    Copy-Item $settingsDst $bak -Force
    Write-Json "$settingsDst.new" $settingsSrc
    Write-Output "settings.json exists. Backed up to $bak and wrote $settingsDst.new for you to merge."
} else {
    Write-Json $settingsDst $settingsSrc
    Write-Output "Wrote $settingsDst"
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
Write-Output ""
Write-Output "Done. If you use Herdr, run its Claude integration too (see herdr\README.md)."
Write-Output "Run: python scripts\doctor.py"
