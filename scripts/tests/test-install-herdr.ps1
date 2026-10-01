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
    'if not "%1 %2"=="channel show" goto run',
    'if "%HERDR_STUB_CHANNEL%"=="stderr" goto err',
    'if defined HERDR_STUB_CHANNEL (echo %HERDR_STUB_CHANNEL%) else (echo preview)',
    'exit /b 0',
    ':err',
    'echo error: unknown subcommand show 1>&2',
    'exit /b 2',
    ':run',
    'echo stub herdr %*')
Set-Content -LiteralPath (Join-Path $stubs 'hotkey.ps1') -Encoding ascii -Value ("Set-Content -LiteralPath '" + $hotkeyMark + "' -Value ran; 'Created: stub hotkey'")
$channelSeen = Join-Path $base 'installer-channel.txt'
Set-Content -LiteralPath (Join-Path $stubs 'installer-fails.ps1') -Encoding ascii -Value ("Set-Content -LiteralPath '" + $channelSeen + "' -Value `$env:HERDR_CHANNEL; 'stub installer failing'; exit 3")
$lnkDir = Join-Path $base 'startmenu'
New-Item -ItemType Directory -Force -Path $lnkDir | Out-Null
# The PATH of every run in this file holds no herdr, so a slip in the code cannot reach the real one.
$env:Path = (($realPath -split ';') | Where-Object { $_ -and $_ -notmatch 'herdr' }) -join ';'

function Clear-HerdrEnv {
    foreach ($name in @('KIT_HERDR_EXE', 'KIT_HERDR_HOTKEY', 'KIT_HERDR_INSTALLER', 'KIT_HERDR_ANSWER', 'KIT_ASSUME_INTERACTIVE', 'HERDR_STUB_CHANNEL')) {
        Set-Item -Path "Env:$name" -Value $null
    }
    $env:KIT_HERDR_CONFIG_DIR = $configDir
    $env:KIT_HERDR_LNK_DIR = $lnkDir
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
    Assert-Regex $calls '(?m)^channel show \|' 'it reads the channel of the installed herdr'
    Assert-True ($calls -notmatch 'channel set') 'it never changes the channel of an installed herdr'
    Assert-Match $out '  ok    channel preview' 'it reports the preview channel'
    Assert-Match $calls ('integration install claude |' + $kitHome) 'the integration lands in the kit home'
    Assert-True ((Test-Path -LiteralPath $config) -and ((Get-FileHash -LiteralPath $config).Hash -eq (Get-FileHash -LiteralPath $kitConfig).Hash)) 'config.toml is the kit version'
    Assert-True (Test-Path -LiteralPath $hotkeyMark) 'the hotkey script ran'
    Assert-Match $out '  ok    hotkey Ctrl+Alt+N' 'it reports the hotkey'

    Write-Host "`r`nphase 4b, a herdr on stable is left on stable, and the integration follows the kit home"
    # KIT_HOME unset: the kit home is then %USERPROFILE%\.claude, the same sandbox folder, and only
    # CLAUDE_CONFIG_DIR points elsewhere, the shell of a user with a second profile.
    $env:HERDR_STUB_CHANNEL = 'stable'
    $env:CLAUDE_CONFIG_DIR = Join-Path $base 'another-profile'
    $savedKitHome = $env:KIT_HOME
    $env:KIT_HOME = $null
    Remove-Item -LiteralPath $herdrLog -ErrorAction SilentlyContinue
    try { $out = Invoke-Install @('-Herdr') } finally { $env:KIT_HOME = $savedKitHome }
    $env:CLAUDE_CONFIG_DIR = $null
    $env:HERDR_STUB_CHANNEL = $null
    Assert-Exit 0 'the install over a stable herdr succeeds'
    Assert-Match $out "your Herdr follows 'stable'; this kit is verified on preview" 'it names the channel and changes nothing'
    $calls = Get-Content -LiteralPath $herdrLog -Raw
    Assert-True ($calls -notmatch 'channel set') 'no channel set on a stable herdr'
    Assert-Match $calls ('integration install claude |' + $kitHome) 'the integration goes to the kit home, not the shell profile'

    Write-Host "`r`nphase 4d, a herdr whose channel show fails on stderr never fails the install"
    $env:HERDR_STUB_CHANNEL = 'stderr'
    try { $out = Invoke-Install @('-Herdr') } finally { $env:HERDR_STUB_CHANNEL = $null }
    Assert-Exit 0 'the install over a herdr with no channel show succeeds'
    Assert-Match $out 'could not read the channel of your Herdr' 'it says the channel could not be read'
    Assert-Match $out 'integration install claude' 'the next steps still run'

    Write-Host "`r`nphase 4c, a hotkey shortcut of yours that points elsewhere is kept; the same target is skipped"
    $lnk = Join-Path $lnkDir 'herdr (Ctrl+Alt+N).lnk'
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($lnk); $sc.TargetPath = 'C:\Windows\notepad.exe'; $sc.Save()
    Remove-Item -LiteralPath $hotkeyMark -ErrorAction SilentlyContinue
    $out = Invoke-Install @('-Herdr')
    Assert-Match $out 'keep  hotkey: keep yours' 'a shortcut pointing elsewhere is kept'
    $psFull = [IO.Path]::Combine($env:SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    Assert-Match $out ('to repoint it run: ' + $psFull + ' -ExecutionPolicy Bypass -File') 'the repoint line names PowerShell by its full path'
    Assert-True (-not (Test-Path -LiteralPath $hotkeyMark)) 'the hotkey script did not run over it'
    $sc = $shell.CreateShortcut($lnk); $sc.TargetPath = (Join-Path $script:RepoRoot 'herdr\hotkey\launch-herdr.cmd'); $sc.Save()
    $out = Invoke-Install @('-Herdr')
    Assert-Match $out 'ok    hotkey: skip same' 'a shortcut to this clone is left as it is'
    Remove-Item -LiteralPath $lnk

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
    Assert-True ((Get-Content -LiteralPath $channelSeen -Raw).Trim() -eq 'preview') 'the installer was asked for the preview channel'

    Write-Host "`r`nphase 8, -Herdr and -NoHerdr together are refused"
    Clear-HerdrEnv
    $null = Invoke-Install @('-Herdr', '-NoHerdr')
    Assert-True ($script:LastExit -ne 0) ("both switches fail the run (exit $($script:LastExit))")
    Write-Host "`r`nphase 9, a PATH that lost the WindowsPowerShell folder still runs the hotkey script, started by its full path"
    Use-HerdrStubs
    Remove-Item -LiteralPath $hotkeyMark -ErrorAction SilentlyContinue
    $ps = [IO.Path]::Combine($env:SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    $savedPath = $env:Path
    $env:Path = (($env:Path -split ';') | Where-Object { $_ -and $_ -notmatch 'WindowsPowerShell' }) -join ';'
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        $text = ((& $ps -NoProfile -ExecutionPolicy Bypass -File $script:Installer -Herdr 2>&1) | Out-String)
        $script:LastExit = $LASTEXITCODE
    } finally { $env:Path = $savedPath; $ErrorActionPreference = $previous }
    Assert-Exit 0 'the install with -Herdr succeeds with no WindowsPowerShell folder on PATH'
    Assert-True (Test-Path -LiteralPath $hotkeyMark) 'the hotkey script ran'
} finally {
    Clear-HerdrEnv
    $env:KIT_HERDR_CONFIG_DIR = $null
    $env:KIT_HERDR_LNK_DIR = $null
    $env:Path = $realPath
    Close-KitSandbox $realProfile
}

Write-Host ("`r`n{0} passed, {1} failed" -f $script:passed, $script:failed)
if ($script:failed -gt 0) { exit 1 }
exit 0
