# setup-hotkey.ps1 - create a global Ctrl+Alt+N shortcut that launches herdr.
# A Windows .lnk placed in the Start Menu with a Hotkey property makes the key combo global.
# Re-run to update it. Delete the .lnk to disable the hotkey.
param(
    [string]$Hotkey = "Ctrl+Alt+N",
    [string]$Target = "$PSScriptRoot\launch-herdr.cmd",
    [string]$Name   = "herdr (Ctrl+Alt+N)"
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path $Target)) { throw "Launcher not found: $Target" }
$programs = [Environment]::GetFolderPath('Programs')
$lnkPath  = Join-Path $programs "$Name.lnk"
$sh  = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut($lnkPath)
$lnk.TargetPath       = $Target
$lnk.WorkingDirectory = Split-Path $Target -Parent
$lnk.Hotkey           = $Hotkey
$lnk.WindowStyle      = 7
$lnk.Description       = "Launch herdr multiplexer"
$lnk.Save()
Write-Output "Created: $lnkPath"
Write-Output "Hotkey : $Hotkey  ->  $Target"
Write-Output "Keep the .lnk in the Start Menu for the global hotkey to stay active."
