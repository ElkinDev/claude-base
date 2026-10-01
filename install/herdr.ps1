# herdr.ps1 - the optional Herdr step of the user-scope install, dot-sourced by install.ps1.
#
# On yes it installs Herdr with its official installer (https://herdr.dev/install.ps1) unless a herdr
# is already on PATH, follows the preview channel this kit is verified on (herdr\verified-version.txt),
# puts herdr\config.toml in place, wires the Claude Code integration and creates the Ctrl+Alt+N
# hotkey. A config.toml you already have and that differs is never touched: the kit version lands
# beside it as config.toml.new, the same rule as every other file the installer writes.
#
# Herdr is optional, so nothing here can fail the install: a step that fails prints a FAIL line
# and the next steps that depend on it are skipped.
#
# The external commands can be replaced through the environment, which is what the tests do:
#   KIT_HERDR_INSTALLER   a .ps1 run instead of the official installer
#   KIT_HERDR_EXE         the herdr to call instead of the one found on PATH
#   KIT_HERDR_CONFIG_DIR  the folder of config.toml instead of %APPDATA%\herdr
#   KIT_HERDR_HOTKEY      a .ps1 run instead of herdr\hotkey\setup-hotkey.ps1
#   KIT_HERDR_LNK_DIR     the folder of the hotkey shortcut instead of the Start Menu Programs folder
#   KIT_HERDR_ANSWER      the answer to the question, instead of reading the console
#   KIT_ASSUME_INTERACTIVE  1 or 0, instead of asking the console whether a person is there

function Test-KitInteractive {
    if ($env:KIT_ASSUME_INTERACTIVE -eq '1') { return $true }
    if ($env:KIT_ASSUME_INTERACTIVE -eq '0') { return $false }
    # A captured run (a test, a pipeline, a scheduled task) has no person to answer, and a question
    # there would hang it, so any redirection counts as unattended.
    try {
        return ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected -and
                -not [Console]::IsOutputRedirected)
    } catch { return $false }
}

function Get-KitHerdrChoice {
    # yes, no, ask (a dry run that would ask), or unattended (no console and no switch).
    param([bool]$Yes, [bool]$No, [bool]$DryRun)
    if ($Yes -and $No) { throw '-Herdr and -NoHerdr cannot both be given.' }
    if ($Yes) { return 'yes' }
    if ($No) { return 'no' }
    if ($DryRun) { return 'ask' }
    if ($null -ne $env:KIT_HERDR_ANSWER) { $answer = $env:KIT_HERDR_ANSWER }
    elseif (Test-KitInteractive) {
        $answer = Read-Host 'Add Herdr, the terminal workspace for agents, on its preview channel and set up the way this kit uses it? [Y/n]'
    } else { return 'unattended' }
    if ($answer -match '^\s*(n|no)\s*$') { return 'no' }
    return 'yes'
}

function Find-KitHerdr {
    if ($env:KIT_HERDR_EXE) { return $env:KIT_HERDR_EXE }
    $command = Get-Command herdr -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    # The installer points PATH at its active release; a session started before the install has
    # the old PATH, so look where the installer keeps the current release too.
    $current = Join-Path $env:USERPROFILE '.herdr\packages\standalone\current'
    foreach ($candidate in @((Join-Path $current 'herdr.exe'), (Join-Path $current 'bin\herdr.exe'))) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

function Update-KitSessionPath {
    # An installer writes PATH to the registry; this session still has the PATH it started with.
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
}

function Get-KitHerdrConfigPlan {
    param([string]$KitConfig, [string]$Config)
    if (-not (Test-Path -LiteralPath $Config)) { return "write $Config" }
    if ((Get-FileHash -LiteralPath $Config).Hash -eq (Get-FileHash -LiteralPath $KitConfig).Hash) {
        return "skip same $Config"
    }
    return "keep yours $Config, the kit version lands beside it as config.toml.new"
}

function Get-KitHotkeyPlan {
    # The shortcut setup-hotkey.ps1 writes. One that points at another launcher is yours: an older
    # clone whose launch-herdr.cmd you edited is never repointed by a new install.
    param([string]$Launcher)
    $dir = if ($env:KIT_HERDR_LNK_DIR) { $env:KIT_HERDR_LNK_DIR } else { [Environment]::GetFolderPath('Programs') }
    # GetFolderPath answers empty when the folder does not exist, as under a moved USERPROFILE.
    if (-not $dir) { return 'write, through herdr\hotkey\setup-hotkey.ps1 (no Start Menu Programs folder found to check)' }
    $lnk = Join-Path $dir 'herdr (Ctrl+Alt+N).lnk'
    if (-not (Test-Path -LiteralPath $lnk)) { return "write $lnk" }
    try { $target = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk).TargetPath } catch { $target = '' }
    if ($target -eq $Launcher) { return "skip same $lnk" }
    return "keep yours $lnk, it points at $target; to repoint it run herdr\hotkey\setup-hotkey.ps1 from this clone"
}

function Invoke-KitHerdrCommand {
    # Runs one external step and prints one ok or FAIL line; $script:KitHerdrOk says whether it exited 0.
    param([string]$Label, [scriptblock]$Command)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $global:LASTEXITCODE = 0
        $lines = @(& $Command 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
    } catch {
        $lines = @($_.Exception.Message)
        $code = 1
    } finally { $ErrorActionPreference = $previous }
    $last = ($lines | Where-Object { $_.Trim() } | Select-Object -Last 1)
    if ($code -eq 0) {
        $script:KitHerdrOk = $true
        Write-Output ("  ok    {0}{1}" -f $Label, $(if ($last) { ": $last" } else { '' }))
        return
    }
    $script:KitHerdrOk = $false
    Write-Output ("  FAIL  {0} (exit {1}){2}" -f $Label, $code, $(if ($last) { ": $last" } else { '' }))
}

function Install-KitHerdr {
    param([string]$KitRoot, [string]$KitHome, [string]$Choice, [switch]$DryRun)
    Write-Output ''
    if ($Choice -eq 'no') {
        Write-Output 'herdr        not added. Add it later with: install.ps1 -Herdr'
        return
    }
    if ($Choice -eq 'unattended') {
        Write-Output 'herdr        not added: there is no console to ask. Pass -Herdr to add it, or -NoHerdr to skip the question.'
        return
    }
    $configDir = if ($env:KIT_HERDR_CONFIG_DIR) { $env:KIT_HERDR_CONFIG_DIR } else { Join-Path $env:APPDATA 'herdr' }
    $config    = Join-Path $configDir 'config.toml'
    $kitConfig = Join-Path $KitRoot 'herdr\config.toml'
    $hotkey    = if ($env:KIT_HERDR_HOTKEY) { $env:KIT_HERDR_HOTKEY } else { Join-Path $KitRoot 'herdr\hotkey\setup-hotkey.ps1' }
    $exe       = Find-KitHerdr

    if ($DryRun) {
        if ($Choice -eq 'ask') { Write-Output 'herdr        a real run asks "Add Herdr? [Y/n]" (Enter is yes); on yes:' }
        else { Write-Output 'herdr        -Herdr given; a real run does:' }
        if ($exe) { Write-Output "  install    skip, herdr is already at $exe" }
        else { Write-Output '  install    the official installer, https://herdr.dev/install.ps1' }
        Write-Output '  channel    a fresh install follows preview; an installed Herdr keeps its channel, and the run says which'
        Write-Output ('  config     ' + (Get-KitHerdrConfigPlan $kitConfig $config))
        Write-Output '  integrate  herdr integration install claude'
        Write-Output ('  hotkey     ' + (Get-KitHotkeyPlan (Join-Path $KitRoot 'herdr\hotkey\launch-herdr.cmd')))
        return
    }

    Write-Output 'herdr'
    $fresh = -not $exe
    if ($exe) {
        Write-Output "  ok    already installed at $exe"
    } else {
        # The installer's own parameter: a fresh install follows preview from the start (herdr.dev/install.ps1:3).
        $savedChannel = $env:HERDR_CHANNEL
        $env:HERDR_CHANNEL = 'preview'
        if ($env:KIT_HERDR_INSTALLER) {
            $installer = $env:KIT_HERDR_INSTALLER
            Invoke-KitHerdrCommand 'the Herdr installer' { & powershell -NoProfile -ExecutionPolicy Bypass -File $installer }
        } else {
            Invoke-KitHerdrCommand 'the Herdr installer, https://herdr.dev/install.ps1' {
                & powershell -NoProfile -ExecutionPolicy Bypass -Command 'irm https://herdr.dev/install.ps1 | iex'
            }
        }
        $env:HERDR_CHANNEL = $savedChannel
        if (-not $script:KitHerdrOk) {
            Write-Output '  skip  the rest of the Herdr setup, since Herdr did not install'
            return
        }
        Update-KitSessionPath
        $exe = Find-KitHerdr
        if (-not $exe) {
            Write-Output '  FAIL  Herdr installed, but this session cannot find herdr yet. Open a new terminal and run: install.ps1 -Herdr'
            return
        }
    }

    # An installed Herdr keeps the channel its owner chose; the run only says when it is not the
    # preview channel this kit is verified on.
    if (-not $fresh) {
        $channel = (& $exe channel show 2>$null | Out-String).Trim()
        if ($channel -eq 'preview') { Write-Output '  ok    channel preview' }
        else { Write-Output ("  note  your Herdr follows '{0}'; this kit is verified on preview. To follow it: herdr channel set preview" -f $channel) }
    }

    $plan = Get-KitHerdrConfigPlan $kitConfig $config
    try {
        if ($plan.StartsWith('write ')) {
            New-Item -ItemType Directory -Force -Path $configDir | Out-Null
            Copy-Item -LiteralPath $kitConfig -Destination $config
        } elseif ($plan.StartsWith('keep yours ')) {
            Copy-Item -LiteralPath $kitConfig -Destination "$config.new" -Force
        }
        Write-Output "  ok    config: $plan"
    } catch {
        Write-Output ("  FAIL  config: {0}" -f $_.Exception.Message)
    }

    # Herdr writes its hook into the Claude Code profile that CLAUDE_CONFIG_DIR names. It goes to the
    # kit home this run installed, whatever profile the shell happens to point at.
    $savedConfigDir = $env:CLAUDE_CONFIG_DIR
    $env:CLAUDE_CONFIG_DIR = $KitHome
    try {
        Invoke-KitHerdrCommand 'integration install claude' { & $exe integration install claude }
    } finally { $env:CLAUDE_CONFIG_DIR = $savedConfigDir }

    $launcher = Join-Path $KitRoot 'herdr\hotkey\launch-herdr.cmd'
    $hotkeyPlan = Get-KitHotkeyPlan $launcher
    if ($hotkeyPlan.StartsWith('keep yours')) { Write-Output "  keep  hotkey: $hotkeyPlan" }
    elseif ($hotkeyPlan.StartsWith('skip same')) { Write-Output "  ok    hotkey: $hotkeyPlan" }
    else { Invoke-KitHerdrCommand 'hotkey Ctrl+Alt+N' { & powershell -NoProfile -ExecutionPolicy Bypass -File $hotkey } }
}
