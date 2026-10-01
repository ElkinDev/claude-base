# The optional Herdr step of install.ps1 (install\herdr.ps1): the switches, the question, and each
# step against stubs. No test here reaches the real Herdr: every run that can call herdr names a stub
# through KIT_HERDR_EXE, the hotkey through KIT_HERDR_HOTKEY, and the config folder through
# KIT_HERDR_CONFIG_DIR, all inside the sandbox.
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tests\test-install-herdr.ps1

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'lib-install-test.ps1')

$realProfile = $env:USERPROFILE
$realPath    = $env:Path
$base        = New-KitSandbox 'kit-install-herdr'
$kitHome     = $script:KitHome
$kitConfig   = Join-Path $script:RepoRoot 'herdr\config.toml'
$configDir   = Join-Path $base 'appdata\herdr'
$config      = Join-Path $configDir 'config.toml'
$stubs       = Join-Path $base 'stubs'
$herdrLog    = Join-Path $base 'herdr-calls.txt'
$hotkeyMark  = Join-Path $base 'hotkey-ran.txt'
New-Item -ItemType Directory -Force -Path $stubs | Out-Null

# A herdr that records each call and the profile it was called under, and exits 0.
Set-Content -LiteralPath (Join-Path $stubs 'herdr.cmd') -Encoding ascii -Value @(
    '@echo off',
    ('echo %* ^|%CLAUDE_CONFIG_DIR%>>"' + $herdrLog + '"'),
    'echo stub herdr %*')
Set-Content -LiteralPath (Join-Path $stubs 'hotkey.ps1') -Encoding ascii -Value ("Set-Content -LiteralPath '" + $hotkeyMark + "' -Value ran; 'Created: stub hotkey'")
Set-Content -LiteralPath (Join-Path $stubs 'installer-fails.ps1') -Encoding ascii -Value "'stub installer failing'; exit 3"
# The PATH of every run in this file holds no herdr, so a slip in the code cannot reach the real one.
$env:Path = (($realPath -split ';') | Where-Object { $_ -and $_ -notmatch 'herdr' }) -join ';'

function Clear-HerdrEnv {
    foreach ($name in @('KIT_HERDR_EXE', 'KIT_HERDR_HOTKEY', 'KIT_HERDR_INSTALLER', 'KIT_HERDR_ANSWER', 'KIT_ASSUME_INTERACTIVE')) {
        Set-Item -Path "Env:$name" -Value $null
    }
    $env:KIT_HERDR_CONFIG_DIR = $configDir
}

function Use-HerdrStubs {
    Clear-HerdrEnv
    $env:KIT_HERDR_EXE    = Join-Path $stubs 'herdr.cmd'
    $env:KIT_HERDR_HOTKEY = Join-Path $stubs 'hotkey.ps1'
}

try {
    Write-Host "`r`nphase 1, -NoHerdr adds nothing and says how to add it later"
    Clear-HerdrEnv
    $out = Invoke-Install @('-NoHerdr')
    Assert-Exit 0 'the install with -NoHerdr succeeds'
    Assert-Match $out 'herdr        not added. Add it later with: install.ps1 -Herdr' 'it says Herdr was not added'
    Assert-True (-not (Test-Path -LiteralPath $config)) 'no config.toml was written'

    Write-Host "`r`nphase 2, a captured run with no switch asks nothing and adds nothing"
    $out = Invoke-Install
    Assert-Exit 0 'the unattended install succeeds'
    Assert-Match $out 'there is no console to ask. Pass -Herdr to add it' 'it says why it did not ask'
    Assert-True (-not (Test-Path -LiteralPath $config)) 'still no config.toml'

    Write-Host "`r`nphase 3, a dry run lists the Herdr steps and writes nothing"
    $out = Invoke-Install @('-DryRun')
    Assert-Exit 0 'the dry run succeeds'
    Assert-Match $out 'a real run asks "Add Herdr? [Y/n]"' 'the dry run says it would ask'
    Assert-Match $out ('config     write ' + $config) 'the dry run plans the config write'
    Assert-True (-not (Test-Path -LiteralPath $config)) 'the dry run wrote no config.toml'

    Write-Host "`r`nphase 4, -Herdr with herdr present: channel, config, integration in the kit home, hotkey"
    Use-HerdrStubs
    $out = Invoke-Install @('-Herdr')
    Assert-Exit 0 'the install with -Herdr succeeds'
    Assert-Match $out ('  ok    already installed at ' + $env:KIT_HERDR_EXE) 'it does not reinstall a present herdr'
    $calls = if (Test-Path -LiteralPath $herdrLog) { Get-Content -LiteralPath $herdrLog -Raw } else { '' }
    Assert-Regex $calls '(?m)^channel set preview \|' 'it sets the preview channel'
    Assert-Match $calls ('integration install claude |' + $kitHome) 'the integration lands in the kit home'
    Assert-True ((Test-Path -LiteralPath $config) -and ((Get-FileHash -LiteralPath $config).Hash -eq (Get-FileHash -LiteralPath $kitConfig).Hash)) 'config.toml is the kit version'
    Assert-True (Test-Path -LiteralPath $hotkeyMark) 'the hotkey script ran'
    Assert-Match $out '  ok    hotkey Ctrl+Alt+N' 'it reports the hotkey'

    Write-Host "`r`nphase 5, a config.toml of yours that differs is kept; the kit version lands as .new"
    Set-Content -LiteralPath $config -Value 'mine = true' -Encoding ascii
    $out = Invoke-Install @('-Herdr')
    Assert-Exit 0 'the second -Herdr install succeeds'
    Assert-True ((Get-Content -LiteralPath $config -Raw).Trim() -eq 'mine = true') 'your config.toml is untouched'
    Assert-True ((Get-FileHash -LiteralPath "$config.new").Hash -eq (Get-FileHash -LiteralPath $kitConfig).Hash) 'config.toml.new is the kit version'
    Assert-Match $out 'keep yours' 'it says it kept yours'

    Write-Host "`r`nphase 6, the answer no and the answer yes"
    Use-HerdrStubs
    $env:KIT_HERDR_ANSWER = 'n'
    $out = Invoke-Install
    Assert-Match $out 'herdr        not added.' 'n means no'
    $env:KIT_HERDR_ANSWER = ' '  # Enter: an empty value would unset the variable
    Remove-Item -LiteralPath $herdrLog -ErrorAction SilentlyContinue
    $out = Invoke-Install
    Assert-Exit 0 'the install answered with Enter succeeds'
    Assert-True (Test-Path -LiteralPath $herdrLog) 'Enter means yes'

    Write-Host "`r`nphase 7, a failing Herdr installer stops the Herdr steps, never the install"
    Clear-HerdrEnv
    $env:KIT_HERDR_INSTALLER = Join-Path $stubs 'installer-fails.ps1'
    $env:KIT_HERDR_HOTKEY = Join-Path $stubs 'hotkey.ps1'
    Remove-Item -LiteralPath $hotkeyMark -ErrorAction SilentlyContinue
    $out = Invoke-Install @('-Herdr')
    Assert-Exit 0 'the install still succeeds'
    Assert-Match $out 'FAIL  the Herdr installer (exit 3)' 'it reports the installer exit'
    Assert-Match $out 'skip  the rest of the Herdr setup' 'it skips the rest'
    Assert-True (-not (Test-Path -LiteralPath $hotkeyMark)) 'no hotkey after a failed install'

    Write-Host "`r`nphase 8, -Herdr and -NoHerdr together are refused"
    Clear-HerdrEnv
    $null = Invoke-Install @('-Herdr', '-NoHerdr')
    Assert-True ($script:LastExit -ne 0) ("both switches fail the run (exit $($script:LastExit))")
} finally {
    Clear-HerdrEnv
    $env:KIT_HERDR_CONFIG_DIR = $null
    $env:Path = $realPath
    Close-KitSandbox $realProfile
}

Write-Host ("`r`n{0} passed, {1} failed" -f $script:passed, $script:failed)
if ($script:failed -gt 0) { exit 1 }
exit 0
