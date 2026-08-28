#requires -Version 5.1
<#
lib.ps1 - the safe-adoption engine install.ps1 runs on (F13).

The contract in one paragraph: the installer knows which files it wrote, because it records a
hash per file in the managed-file manifest at <kit home>\.kit-manifest.json. On every later run
it compares what is on disk with that record. A file it wrote and nobody touched is refreshed
(after a backup). A file that differs from the record, or that the manifest never heard of, is
somebody else's file: it is backed up, kept exactly as it is, and the kit version lands beside it
as <name>.new, which is itself governed by the same rule. Nothing is ever deleted, and every
overwrite leaves a copy under <kit home>\backups\<stamp>\ that scripts\kit-restore.py can put back.

The backup record itself (the stamp, the folder, manifest.txt, the copies) is in
install\backup.ps1, and project adoption (the preflight, the exclude file, the source pairs) is in
install\adopt.ps1. Dot-source the three in this order, which is what install.ps1 does:
. (Join-Path $PSScriptRoot 'install\backup.ps1')
. (Join-Path $PSScriptRoot 'install\lib.ps1')
. (Join-Path $PSScriptRoot 'install\adopt.ps1')
#>

$script:KitOwned          = @()
$script:KitManifestBroken = $false

# ---------------------------------------------------------------- paths and hashing

function Get-KitHome {
    # KIT_HOME lets a test (or a second engine) point the whole install somewhere else.
    # scripts\kit-restore.py reads the same variable, so the two always agree on the home.
    if ($env:KIT_HOME) { return $env:KIT_HOME }
    return (Join-Path $env:USERPROFILE '.claude')
}

function Write-TextNoBom {
    # UTF-8 with NO BOM. Set-Content -Encoding utf8 adds one on PowerShell 5.1, and a BOM in
    # front of JSON is rejected by Node's parser.
    param([string]$Path, [string]$Text)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-Sha256File {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-Sha256Text {
    param([string]$Text)
    $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return (-join ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') })) }
    finally { $sha.Dispose() }
}

# ---------------------------------------------------------------- managed-file manifest

function Read-KitManifest {
    <#
    A PowerShell hashtable is case-insensitive on string keys, which is what Windows paths need.
    Broken means anything that is not this engine's map of absolute path to hash: bad syntax, but
    equally an empty file, a file holding only a BOM, valid json that is an array (ConvertFrom-Json
    does not enumerate, so a one element array reaches here as an array whose member lookup still
    answers, and only an explicit array test catches it), a missing version or one this engine does
    not know, a "files" that is not an object, an entry whose value is not a hash, and a key that is
    not an absolute path. All of them degrade to "nothing is managed", which is the safe reading
    (every existing file then counts as somebody else's) and all of them are worth saying out loud,
    because a record that says nothing is a record that was lost. It reports that through a flag
    rather than a message, because anything written to the output stream here would join the
    returned hashtable.
    #>
    param([string]$Path)
    $map = @{}
    $script:KitManifestBroken = $false
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $map }
    try {
        $text = [System.IO.File]::ReadAllText($Path).Trim([char]0xFEFF, ' ', "`t", "`r", "`n")
        if (-not $text) { throw 'the record is empty' }
        $data = $text | ConvertFrom-Json
        if ($data -is [array] -or $data -isnot [PSCustomObject]) { throw 'the record is not an object' }
        if ($data.version -isnot [int] -or $data.version -ne 1) { throw 'not a version we know' }
        $files = $data.files
        if ($files -is [array] -or $files -isnot [PSCustomObject]) { throw 'the record has no file map' }
        foreach ($e in $files.PSObject.Properties) {
            if ($e.Value -isnot [string]) { throw 'a file is recorded without a hash' }
            # Rooted is not enough: \kit\x.md and C:x.md are both rooted and both mean a different
            # file depending on where the run starts from. The record keeps whole paths only.
            $whole = ($e.Name -match '^[A-Za-z]:[\\/]') -or ($e.Name -match '^\\\\[^\\/]')
            if (-not $whole) { throw 'a file has no absolute path' }
            $map[$e.Name] = [string]$e.Value
        }
    } catch {
        $map = @{}
        $script:KitManifestBroken = $true
    }
    return $map
}

function Test-KitManifestBroken { return $script:KitManifestBroken }

function Move-BrokenKitManifest {
    # Renamed, never deleted: the adopter can still read what was in it, and the run starts from a
    # clean record instead of guessing at half a file.
    param([string]$Path, [string]$Stamp)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $dest = "$Path.broken-$Stamp"
    Move-Item -LiteralPath $Path -Destination $dest -Force
    return $dest
}

function Test-KitManifestChanged {
    param([string]$Path, [hashtable]$Map)
    $broken = $script:KitManifestBroken
    $old = Read-KitManifest $Path
    $script:KitManifestBroken = $broken
    if ($old.Count -ne $Map.Count) { return $true }
    foreach ($key in $Map.Keys) { if ($old[$key] -ne $Map[$key]) { return $true } }
    return $false
}

function Write-KitManifest {
    # Only when the file map actually moved: rewriting it on a no-op run would change the
    # timestamp and make an idempotent re-run look like a write.
    param([string]$Path, [hashtable]$Map)
    if (-not (Test-KitManifestChanged $Path $Map)) { return $false }
    $files = [ordered]@{}
    foreach ($key in ($Map.Keys | Sort-Object)) { $files[$key] = $Map[$key] }
    $doc = [ordered]@{
        version   = 1
        installed = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss')
        files     = $files
    }
    Write-TextNoBom $Path (ConvertTo-Json $doc -Depth 5)
    return $true
}

# ---------------------------------------------------------------- the ownership rule

function Resolve-KitAction {
    param([string]$Target, [string]$SourceHash, [hashtable]$Manifest, [switch]$Force)
    if (-not (Test-Path -LiteralPath $Target -PathType Leaf)) { return 'write' }
    $current = Get-Sha256File $Target
    if ($current -eq $SourceHash) { return 'same' }
    $recorded = $Manifest[$Target]
    if ($recorded -and $recorded -eq $current) { return 'refresh' }
    if ($Force) { return 'force' }
    return 'new'
}

function Write-KitContent {
    param([string]$Target, [string]$SourcePath, $SourceText)
    $dir = Split-Path -Parent $Target
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    # An explicit null test, not truthiness: a rendered file that came out empty is still a
    # rendered file, and copying $null instead would fail on a path that does not exist.
    if ($null -ne $SourceText) { Write-TextNoBom $Target ([string]$SourceText) }
    else { Copy-Item -LiteralPath $SourcePath -Destination $Target -Force }
}

function Add-KitOwned {
    # Every path this run manages, which is what -LocalOnly excludes. A file of the adopter's is
    # never in here; the proposal beside it is.
    param([string]$Path)
    if ($script:KitOwned -notcontains $Path) { $script:KitOwned += $Path }
}

function Get-KitOwned { return @($script:KitOwned) }

function Set-KitProposal {
    <#
    The target belongs to the adopter, so it is never written. The kit version goes beside it as
    <name>.new, and that proposal obeys the same ownership rule: one the kit wrote is refreshed,
    one the adopter has been editing (docs\ADOPTION.md tells them to merge it in place) is backed
    up first and named in the output.
    #>
    param([string]$Target, [string]$SourcePath, $SourceText, [string]$SourceHash,
          [hashtable]$Manifest, [switch]$DryRun)
    $proposal = "$Target.new"
    Add-KitOwned $proposal
    $kept   = "backup+new   $Target (yours is kept, the kit version is at $proposal)"
    $action = Resolve-KitAction -Target $proposal -SourceHash $SourceHash -Manifest $Manifest
    if ($action -eq 'same') {
        # The proposal already holds the kit's bytes, so it goes back on the record too, for the
        # same reason as the file above: a lost record is repaired by the first run after it.
        if (-not $DryRun) { $Manifest[$proposal] = $SourceHash }
        return "kept         $Target (yours, the kit version is already at $proposal)"
    }
    if (-not $DryRun) {
        Backup-KitFile $Target
        Add-KitBackupLine "kept $Target"
        if ($action -eq 'write') {
            Add-KitBackupLine "new $proposal"
        } else {
            Backup-KitFile $proposal
            Add-KitBackupLine "overwritten $proposal"
        }
        Write-KitContent $proposal $SourcePath $SourceText
        $Manifest[$proposal] = $SourceHash
    }
    if ($action -eq 'new') {
        return @($kept, "draft        $proposal (your own draft is in the backup, the kit version replaced it)")
    }
    return $kept
}

function Set-KitFile {
    <#
    One file, decided and executed. Returns the plan line (or two lines when a draft of the
    adopter's was moved out of the way), or $null when the source is missing. Under -DryRun it
    returns the same lines and writes nothing at all.
    #>
    param(
        [string]$Target,
        [string]$SourcePath,
        $SourceText,
        [hashtable]$Manifest,
        [switch]$Force,
        [switch]$DryRun
    )
    if ($null -ne $SourceText) { $sourceHash = Get-Sha256Text ([string]$SourceText) }
    else { $sourceHash = Get-Sha256File $SourcePath }
    if (-not $sourceHash) { return $null }

    $action = Resolve-KitAction -Target $Target -SourceHash $sourceHash -Manifest $Manifest -Force:$Force
    if ($action -eq 'same') {
        # Byte for byte the kit's version, so it is the kit's file whatever the record says, and
        # -LocalOnly hides it. When the record has lost it (a manifest deleted, emptied or moved
        # aside as broken) this puts it back on the record. Nothing else would: the file is never
        # written again, so without this the record would stay at nothing and the rollback of the
        # run that installed it would find nothing it was allowed to remove. The backup is not
        # touched, because nothing is being written.
        if (-not $DryRun) { $Manifest[$Target] = $sourceHash }
        Add-KitOwned $Target
        return "skip same    $Target"
    }
    if ($action -eq 'new') {
        return (Set-KitProposal -Target $Target -SourcePath $SourcePath -SourceText $SourceText `
            -SourceHash $sourceHash -Manifest $Manifest -DryRun:$DryRun)
    }

    Add-KitOwned $Target
    if (-not $DryRun) {
        if ($action -eq 'write') {
            Add-KitBackupLine "created $Target"
        } else {
            Backup-KitFile $Target
            Add-KitBackupLine "overwritten $Target"
        }
        Write-KitContent $Target $SourcePath $SourceText
        $Manifest[$Target] = $sourceHash
    }
    if ($action -eq 'force') { return "force        $Target (your version is in the backup)" }
    if ($action -eq 'refresh') { return "refresh      $Target" }
    return "write        $Target"
}
