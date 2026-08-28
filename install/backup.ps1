#requires -Version 5.1
<#
backup.ps1 - the backup record every write in install\lib.ps1 goes through (F13).

One run, one folder: <kit home>\backups\<stamp>\. Inside it, a copy of every file the run replaced
or kept, filed under the path it came from, and manifest.txt listing what happened in the order it
happened. Each line is appended and flushed before the write it describes, so a run that dies
halfway (a locked file, an error, a kill from outside) still leaves a folder scripts\kit-restore.py
can read and put back. The folder is created on the first line, so a run that changes nothing
leaves nothing behind.

Copies carry bytes, not Windows file attributes: a read-only file comes back writable.

Dot-source it before lib.ps1: . (Join-Path $PSScriptRoot 'install\backup.ps1')
#>

$script:KitBackupRoot    = $null
$script:KitBackupTrigger = ''
$script:KitBackupOpen    = $false

function Get-KitStamp {
    # The name of this run, never one a folder already carries: two runs inside the same second
    # would share a backup folder, and the second would copy its own version of a file over the
    # one the first had saved there.
    param([string]$KitHome)
    $base = Get-Date -Format 'yyyyMMdd-HHmmss'
    $stamp = $base; $n = 1
    while (Test-Path -LiteralPath (Join-Path $KitHome ('backups\' + $stamp))) {
        $n++
        $stamp = $base + '-' + $n
    }
    return $stamp
}

function Start-KitBackup {
    param([string]$Root, [string]$Trigger)
    $script:KitBackupRoot    = $Root
    $script:KitBackupTrigger = $Trigger
    $script:KitBackupOpen    = $false
}

function Get-MirrorPath {
    # C:\<rest of the path>\x.md becomes <backup root>\C\<rest of the path>\x.md, so a
    # backup folder is readable as a tree and a restore knows where each file came from.
    param([string]$Root, [string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    $rel  = $full -replace '^\\\\', 'UNC\'
    $rel  = $rel -replace '^([A-Za-z]):[\\/]?', '$1\'
    $rel  = $rel.TrimStart('\', '/')
    return (Join-Path $Root $rel)
}

function Add-KitBackupLine {
    # Appended and flushed before the write it describes. The folder is created here, on the
    # first line of the run.
    param([string]$Line)
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $path = Join-Path $script:KitBackupRoot 'manifest.txt'
    if (-not $script:KitBackupOpen) {
        New-Item -ItemType Directory -Force -Path $script:KitBackupRoot | Out-Null
        [System.IO.File]::AppendAllText($path, "trigger $($script:KitBackupTrigger)`r`n", $utf8)
        $script:KitBackupOpen = $true
    }
    [System.IO.File]::AppendAllText($path, "$Line`r`n", $utf8)
}

function Backup-KitFile {
    # The copy is of the bytes only. Windows attributes (read-only, hidden) are not carried over,
    # and a restore therefore gives the file back writable.
    param([string]$Path)
    $dest = Get-MirrorPath $script:KitBackupRoot $Path
    $dir  = Split-Path -Parent $dest
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Copy-Item -LiteralPath $Path -Destination $dest -Force
}

function Get-KitBackupFolder {
    if ($script:KitBackupOpen) { return $script:KitBackupRoot }
    return $null
}
