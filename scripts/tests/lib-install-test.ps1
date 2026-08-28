# Shared harness for the install tests (test-install-smoke.ps1, test-install-project.ps1).
#
# Everything runs against a throwaway home under TEMP. KIT_HOME and USERPROFILE are pointed at it
# before the first call and are asserted to be inside TEMP, because a bug here would write into the
# real ~/.claude. The installer is run the way a user runs it, as a child powershell, and every run
# records its exit code: a suite that reads only stdout passes happily through a crash.
#
# Dot-source it: . (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'lib-install-test.ps1')

$script:TestRoot  = $PSScriptRoot
$script:RepoRoot  = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script:Installer = Join-Path $script:RepoRoot 'install.ps1'
$script:Restorer  = Join-Path $script:RepoRoot 'scripts\kit-restore.py'
$script:Python    = 'python'
if (-not (Get-Command $script:Python -ErrorAction SilentlyContinue)) { $script:Python = 'py' }

$script:passed   = 0
$script:failed   = 0
$script:LastExit = 0
$script:KitHome  = $null
$script:Base     = $null

function Assert-True {
    param([bool]$Condition, [string]$What)
    if ($Condition) {
        $script:passed++
        Write-Host ("  ok    " + $What)
    } else {
        $script:failed++
        Write-Host ("  FAIL  " + $What)
    }
}

function Assert-Match {
    param([string]$Text, [string]$Pattern, [string]$What)
    Assert-True ($Text -match [regex]::Escape($Pattern)) $What
}

function Assert-Regex {
    param([string]$Text, [string]$Pattern, [string]$What)
    Assert-True ($Text -match $Pattern) $What
}

function Assert-Exit {
    param([int]$Expected, [string]$What)
    Assert-True ($script:LastExit -eq $Expected) ("$What (exit $($script:LastExit))")
}

function New-KitSandbox {
    # The sandbox, and the guard that keeps a bug in here away from the real kit home.
    param([string]$Prefix)
    $script:Base    = Join-Path $env:TEMP ($Prefix + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    $userRoot       = Join-Path $script:Base 'user'
    $script:KitHome = Join-Path $userRoot '.claude'
    New-Item -ItemType Directory -Force -Path $userRoot | Out-Null
    if ($script:KitHome -notlike (Join-Path $env:TEMP '*')) {
        Write-Host "FAIL  the sandbox is not under TEMP, refusing to run"
        exit 1
    }
    $env:KIT_HOME    = $script:KitHome
    $env:USERPROFILE = $userRoot
    Write-Host ("installer:    " + $script:Installer)
    Write-Host ("sandbox home: " + $script:KitHome)
    return $script:Base
}

function Get-TreeHash {
    param([string]$Root)
    if (-not (Test-Path -LiteralPath $Root)) { return '' }
    $parts = @()
    foreach ($file in @(Get-ChildItem -LiteralPath $Root -Recurse -File -Force | Sort-Object FullName)) {
        $relative = $file.FullName.Substring($Root.Length)
        $parts += ($relative + ' ' + (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash)
    }
    return ($parts -join "`r`n")
}

function Invoke-Install {
    # stderr is merged in so a crash is visible in the captured text, and the exit code is kept.
    param([string[]]$Arguments = @())
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $text = ((& powershell -NoProfile -ExecutionPolicy Bypass -File $script:Installer @Arguments 2>&1) | Out-String)
        $script:LastExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
    return $text
}

function Invoke-Restore {
    param([string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $text = ((& $script:Python $script:Restorer @Arguments 2>&1) | Out-String)
        $script:LastExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
    return $text
}

function Get-Stamps {
    $backups = Join-Path $script:KitHome 'backups'
    if (-not (Test-Path -LiteralPath $backups)) { return @() }
    return @(Get-ChildItem -LiteralPath $backups -Directory | Sort-Object Name | Select-Object -ExpandProperty Name)
}

function Get-LastStamp { return (Get-Stamps | Select-Object -Last 1) }

function Get-StampDir {
    param([string]$Stamp)
    return (Join-Path $script:KitHome ('backups\' + $Stamp))
}

function Get-BackupManifest {
    param([string]$Stamp)
    $path = Join-Path (Get-StampDir $Stamp) 'manifest.txt'
    if (-not (Test-Path -LiteralPath $path)) { return '' }
    return (Get-Content -LiteralPath $path -Raw)
}

function Get-BackupCopy {
    # Where a backup keeps its copy of an absolute path: the drive letter becomes a folder.
    param([string]$Stamp, [string]$Path)
    return (Join-Path (Get-StampDir $Stamp) ($Path -replace '^([A-Za-z]):\\', '$1\'))
}

function Test-GitIgnored {
    param([string]$Repo, [string]$Path)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & git -C $Repo check-ignore -q -- $Path 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } finally { $ErrorActionPreference = $previous }
}

function Close-KitSandbox {
    param([string]$RealProfile)
    $env:KIT_HOME    = $null
    $env:USERPROFILE = $RealProfile
    if ($script:Base -and (Test-Path -LiteralPath $script:Base)) {
        Remove-Item -LiteralPath $script:Base -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Write-TestResult {
    Write-Host ''
    Write-Host ("" + $script:passed + " passed, " + $script:failed + " failed")
    if ($script:failed -gt 0) { exit 1 }
    exit 0
}
