# -Sync on a profile name that does not exist: the account script refuses it and builds nothing,
# while a new name with -Prepare still makes a profile and -Sync still refreshes one that exists.
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tests\test-account-sync.ps1
#
# A mistyped or stale name given to -Sync used to create an empty profile under that name, and a
# profile folder holds junctions into the default directory, so removing it by hand can reach the
# real skills and memory. Every call runs with -Prepare or fails before a session would open, and
# the whole run lives in a sandbox under TEMP with USERPROFILE pointed at it.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'lib-install-test.ps1')

$launcher = Join-Path $script:RepoRoot 'claude\claude-account.ps1'
$realProfile = $env:USERPROFILE
New-KitSandbox 'account-sync' | Out-Null
$accounts = Join-Path $env:USERPROFILE '.claude-accounts'
New-Item -ItemType Directory -Force -Path (Join-Path $env:USERPROFILE '.claude\skills') | Out-Null

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

    Write-Host 'phase 2: a new name with -Prepare still makes a profile'
    $out = Invoke-Account @('fresh', '-Prepare')
    Assert-Exit 0 'the call succeeds'
    Assert-True (Test-Path -LiteralPath (Join-Path $accounts 'fresh')) 'the profile folder exists'

    Write-Host 'phase 3: -Sync on a profile that exists is not refused'
    $out = Invoke-Account @('fresh', '-Sync', '-Prepare')
    Assert-Exit 0 'the call succeeds'
    Assert-True ($out -notmatch 'no such profile') 'no refusal is printed'

    Write-Host 'phase 4: -Sync with -Prepare on a new name makes it'
    $out = Invoke-Account @('second', '-Sync', '-Prepare')
    Assert-Exit 0 'the call succeeds'
    Assert-True (Test-Path -LiteralPath (Join-Path $accounts 'second')) 'the profile folder exists'
}
finally {
    Close-KitSandbox $realProfile
}
Write-TestResult
