# -Sync on a profile name that does not exist: the account script refuses it and builds nothing,
# with or without -Prepare, while a new name with -Prepare alone still makes a profile and -Sync
# still refreshes one that exists.
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tests\test-account-sync.ps1
#
# A mistyped or stale name given to -Sync used to create an empty profile under that name, and a
# profile folder holds junctions into the default directory, so removing it by hand can reach the
# real skills and memory. The whole run lives in a sandbox under TEMP with USERPROFILE pointed at
# it, and a stub claude.cmd sits first on PATH: a script that lets a refused call through reaches
# the stub, which leaves a marker, never the real claude.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'lib-install-test.ps1')

$launcher = Join-Path $script:RepoRoot 'claude\claude-account.ps1'
$realProfile = $env:USERPROFILE
New-KitSandbox 'account-sync' | Out-Null
$accounts = Join-Path $env:USERPROFILE '.claude-accounts'
New-Item -ItemType Directory -Force -Path (Join-Path $env:USERPROFILE '.claude\skills') | Out-Null
$stubDir = Join-Path $script:Base 'stub'
$marker = Join-Path $script:Base 'claude-ran.txt'
New-Item -ItemType Directory -Force -Path $stubDir | Out-Null
Set-Content -LiteralPath (Join-Path $stubDir 'claude.cmd') -Encoding ascii -Value "@echo off`r`necho ran>>`"$marker`"`r`nexit /b 0"
$realPath = $env:PATH
$env:PATH = $stubDir + ';' + $env:PATH

function Invoke-Account {
    param([string[]]$AccountArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $launcher @AccountArgs 2>&1 | Out-String
    $script:LastExit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return $out
}

try {
    Write-Host 'phase 1: -Sync on a name with no profile'
    $out = Invoke-Account @('nosuch', '-Sync')
    Assert-Exit 1 'the call fails'
    Assert-Match $out "no such profile 'nosuch'" 'the refusal names the profile'
    Assert-Match $out 'cc nosuch -p' 'the refusal says how to create it'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $accounts 'nosuch'))) 'no profile folder was built'
    Assert-True (-not (Test-Path -LiteralPath $marker)) 'no session was started'

    Write-Host 'phase 2: a new name with -Prepare still makes a profile'
    $out = Invoke-Account @('fresh', '-Prepare')
    Assert-Exit 0 'the call succeeds'
    Assert-True (Test-Path -LiteralPath (Join-Path $accounts 'fresh')) 'the profile folder exists'

    Write-Host 'phase 3: -Sync on a profile that exists is not refused'
    $out = Invoke-Account @('fresh', '-Sync', '-Prepare')
    Assert-Exit 0 'the call succeeds'
    Assert-True ($out -notmatch 'no such profile') 'no refusal is printed'

    Write-Host 'phase 4: -Sync with -Prepare on a name with no profile is refused too'
    $out = Invoke-Account @('second', '-Prepare', '-Sync')
    Assert-Exit 1 'the call fails'
    Assert-Match $out "no such profile 'second'" 'the refusal names the profile'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $accounts 'second'))) 'no profile folder was built'
    Assert-True (-not (Test-Path -LiteralPath $marker)) 'no session was started'
}
finally {
    $env:PATH = $realPath
    Close-KitSandbox $realProfile
}
Write-TestResult
