# User-scope smoke test for install.ps1 and scripts\kit-restore.py: the safe-adoption contract
# (F13). Project scope has its own suite, scripts\tests\test-install-project.ps1.
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tests\test-install-smoke.ps1
#
# The harness, the sandbox and the guard that keeps all of this away from the real kit home are in
# scripts\tests\lib-install-test.ps1. Every phase asserts the installer's exit code.

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'lib-install-test.ps1')

$realProfile = $env:USERPROFILE
$base        = New-KitSandbox 'kit-install-smoke'
$kitHome     = $script:KitHome
$manifestPath = Join-Path $kitHome '.kit-manifest.json'
$skill       = Join-Path $kitHome 'skills\audit\SKILL.md'
$draft       = "$skill.new"

function Assert-ManifestLine {
    param([string]$Stamp, [string]$Verb, [string]$Path, [string]$What)
    Assert-Regex (Get-BackupManifest $Stamp) ('(?m)^' + $Verb + ' ' + [regex]::Escape($Path) + '\r?$') $What
}

try {
    # ------------------------------------------------------------ fresh install --
    Write-Host "`r`nphase 1, a fresh install writes the managed-file manifest"
    $out = Invoke-Install
    Assert-Exit 0 'the fresh install succeeds'
    Assert-True (Test-Path -LiteralPath $manifestPath) 'the manifest is there'
    $manifest = (Get-Content -LiteralPath $manifestPath -Raw) | ConvertFrom-Json
    $managed = @($manifest.files.PSObject.Properties)
    Assert-True ($manifest.version -eq 1) 'the manifest declares version 1'
    Assert-True ($manifest.installed -match '^\d{4}-\d{2}-\d{2}T') 'the manifest carries an iso stamp'
    Assert-True ($managed.Count -gt 20) ('the manifest records every file, ' + $managed.Count + ' of them')
    Assert-True (Test-Path -LiteralPath $skill) 'the skills landed'
    # The plugin skills reach an installer machine under their bare names, as before the move.
    Assert-True (Test-Path -LiteralPath (Join-Path $kitHome 'skills\story\SKILL.md')) `
        'a delivery plugin skill landed under its bare name'
    Assert-True (Test-Path -LiteralPath (Join-Path $kitHome 'skills\wave-orchestration\SKILL.md')) `
        'an orchestration plugin skill landed under its bare name'
    Assert-True (Test-Path -LiteralPath (Join-Path $kitHome 'settings.json')) 'settings.json landed'
    # The first three bytes, not Get-Content -Raw: the reader strips a BOM while decoding, so a
    # manifest that carried one would still read clean and the assertion could never fail.
    $head = ([System.IO.File]::ReadAllBytes($manifestPath)[0..2] -join ',')
    Assert-True ($head -ne '239,187,191') ('the manifest has no BOM, it opens with ' + $head)
    Assert-Match $out 'Run: python scripts\doctor.py' 'the run still points at the doctor'
    Assert-Match $out 'Roll it back with' 'the run printed the rollback command'
    Assert-True ((Get-Stamps).Count -eq 1) 'the run recorded one backup folder'

    # ----------------------------------------------------------------- re-run ----
    Write-Host "`r`nphase 2, re-running changes nothing"
    $before = Get-TreeHash $kitHome
    $out = Invoke-Install
    Assert-Exit 0 'the second run succeeds'
    Assert-True ((Get-TreeHash $kitHome) -eq $before) 'the tree is byte for byte the one before'
    Assert-Match $out 'Nothing changed, so there is nothing to roll back.' 'the run says it changed nothing'
    Assert-Match $out '0 written, 0 refreshed' 'the summary counts no writes'
    Assert-True ((Get-Stamps).Count -eq 1) 'no second backup folder was created'

    # ------------------------------------------- settings.json follows KIT_HOME --
    Write-Host "`r`nphase 3, settings.json is rendered from the kit home, not from the profile"
    $moved = Join-Path $base 'elsewhere\kit'
    $env:KIT_HOME = $moved
    $out = Invoke-Install
    $env:KIT_HOME = $kitHome
    Assert-Exit 0 'an install into a kit home outside the profile succeeds'
    $settings = (Get-Content -LiteralPath (Join-Path $moved 'settings.json') -Raw)
    Assert-True ($settings.Contains($moved.Replace('\', '/'))) 'the hook commands point inside that kit home'
    Assert-True ($settings.Contains($moved.Replace('\', '\\'))) 'the windows-style paths point there too'
    Assert-True (-not $settings.Contains('%USERPROFILE%')) 'no token is left unrendered'
    Assert-True (-not $settings.Contains($kitHome.Replace('\', '/'))) 'nothing points at the profile home'
    Remove-Item -LiteralPath (Join-Path $base 'elsewhere') -Recurse -Force

    # ------------------------------------------------------- a file of my own ----
    Write-Host "`r`nphase 4, a file I edited myself is kept and the kit version lands beside it"
    $mine = "the version I edited myself, with a rule my company needs"
    Set-Content -LiteralPath $skill -Value $mine -Encoding UTF8
    $out = Invoke-Install
    Assert-Exit 0 'the run succeeds'
    Assert-True ((Get-Content -LiteralPath $skill -Raw).Contains($mine)) 'my version is still there'
    Assert-True (Test-Path -LiteralPath $draft) 'the kit version is beside it as .new'
    Assert-True ((Get-Content -LiteralPath $draft -Raw) -ne (Get-Content -LiteralPath $skill -Raw)) `
        'the .new file holds the kit version, not mine'
    Assert-Match $out 'backup+new' 'the run named the file it did not overwrite'
    $stamp4 = Get-LastStamp
    Assert-True (Test-Path -LiteralPath (Get-BackupCopy $stamp4 $skill)) 'my version was copied to the backup'
    Assert-True ((Get-Content -LiteralPath (Get-BackupCopy $stamp4 $skill) -Raw).Contains($mine)) `
        'the backup holds my version'
    Assert-ManifestLine $stamp4 'kept' $skill 'the record calls my file kept, because it was not written'
    Assert-True (-not ((Get-BackupManifest $stamp4) -match ('(?m)^overwritten ' + [regex]::Escape($skill) + '\r?$'))) `
        'nothing in the record claims my file was overwritten'
    Assert-ManifestLine $stamp4 'new' $draft 'the record names the proposal it left'

    # ------------------------------------------------------------ idempotence ----
    Write-Host "`r`nphase 5, re-running over a file of mine is still a no-op"
    $before = Get-TreeHash $kitHome
    $stamps = (Get-Stamps).Count
    $out = Invoke-Install
    Assert-Exit 0 'the run succeeds'
    $out = Invoke-Install
    Assert-Exit 0 'and so does the one after it'
    Assert-True ((Get-TreeHash $kitHome) -eq $before) 'two more identical runs left the tree untouched'
    Assert-True ((Get-Stamps).Count -eq $stamps) 'neither of them grew a backup folder'
    Assert-Match $out 'kept ' 'the run still reports that my file was kept'
    Assert-Match $out 'Nothing changed, so there is nothing to roll back.' 'and that it changed nothing'

    # -------------------------------------------------------- my own .new file --
    Write-Host "`r`nphase 6, a .new draft of my own is backed up before the kit replaces it"
    $myDraft = "MY OWN DRAFT: the merge I was halfway through, do not lose this"
    Set-Content -LiteralPath $draft -Value $myDraft -Encoding UTF8
    $out = Invoke-Install
    Assert-Exit 0 'the run succeeds'
    $stamp6 = Get-LastStamp
    Assert-Match $out 'draft ' 'the run named the draft it moved out of the way'
    Assert-True (Test-Path -LiteralPath (Get-BackupCopy $stamp6 $draft)) 'my draft was copied to the backup'
    Assert-True ((Get-Content -LiteralPath (Get-BackupCopy $stamp6 $draft) -Raw).Contains($myDraft)) `
        'the backup holds my draft'
    Assert-ManifestLine $stamp6 'overwritten' $draft 'the record says the proposal was overwritten'
    $out = Invoke-Restore @('--stamp', $stamp6)
    Assert-Exit 0 'rolling that run back succeeds'
    Assert-True ((Get-Content -LiteralPath $draft -Raw).Contains($myDraft)) 'my draft is back'
    Assert-Match $out 'kept ' 'the rollback left my own file alone instead of refusing'

    # ---------------------------------------------------------------- dry run ----
    Write-Host "`r`nphase 7, -DryRun writes nothing at all"
    $before = Get-TreeHash $kitHome
    $stamps = (Get-Stamps).Count
    $out = Invoke-Install @('-DryRun')
    Assert-Exit 0 'the dry run succeeds'
    Assert-True ((Get-TreeHash $kitHome) -eq $before) 'the tree is byte for byte the one before'
    Assert-Match $out 'plan (dry run, nothing is written):' 'the plan was printed'
    Assert-Match $out 'backup+new' 'the plan names the file it would not overwrite'
    Assert-True ((Get-Stamps).Count -eq $stamps) 'no backup folder was created by the dry run'

    # ------------------------------------------------------------------ force ----
    Write-Host "`r`nphase 8, -Force overwrites my version, after backing it up"
    $out = Invoke-Install @('-Force')
    Assert-Exit 0 'the forced run succeeds'
    Assert-True (-not (Get-Content -LiteralPath $skill -Raw).Contains($mine)) 'the kit version won'
    Assert-Match $out 'force ' 'the run named the file it forced'
    $stamp8 = Get-LastStamp
    Assert-True ((Get-Content -LiteralPath (Get-BackupCopy $stamp8 $skill) -Raw).Contains($mine)) `
        'my version is in the backup'

    # ---------------------------------------------------------------- restore ----
    Write-Host "`r`nphase 9, kit-restore.py brings my version back"
    $out = Invoke-Restore @('--list')
    Assert-Exit 0 'the listing succeeds'
    Assert-Match $out $stamp8 'the backup is listed'
    Assert-Match $out 'user scope install' 'the listing says what wrote it'
    $out = Invoke-Restore @('--stamp', $stamp8, '--dry-run')
    Assert-Exit 0 'the dry run succeeds'
    Assert-True (-not (Get-Content -LiteralPath $skill -Raw).Contains($mine)) 'the dry run restored nothing'
    $out = Invoke-Restore @('--stamp', $stamp8)
    Assert-Exit 0 'the restore succeeds'
    Assert-Match $out 'restored' 'the restore reported the file'
    Assert-True ((Get-Content -LiteralPath $skill -Raw).Contains($mine)) 'my version is back'

    Write-Host "`r`nphase 10, a rollback removes what that run created"
    $statusline = Join-Path $kitHome 'statusline.ps1'
    Remove-Item -LiteralPath $statusline -Force
    $out = Invoke-Install
    Assert-Exit 0 'the run succeeds'
    Assert-True (Test-Path -LiteralPath $statusline) 'the installer put the missing file back'
    $stamp10 = Get-LastStamp
    $out = Invoke-Restore @('--stamp', $stamp10)
    Assert-Exit 0 'the rollback succeeds'
    Assert-Match $out 'removed' 'the rollback reported the removal'
    Assert-True (-not (Test-Path -LiteralPath $statusline)) 'the file the run created is gone again'
    $files = ((Get-Content -LiteralPath $manifestPath -Raw) | ConvertFrom-Json).files
    Assert-True (-not ($files.PSObject.Properties.Name -contains $statusline)) `
        'the manifest no longer claims the removed file'

    # ------------------------------------------------------- unreadable record --
    Write-Host "`r`nphase 11, an unreadable manifest degrades instead of crashing"
    Set-Content -LiteralPath $skill -Value 'mine again, so the run has something of mine to keep' -Encoding UTF8
    [System.IO.File]::WriteAllText($manifestPath, '{"version":1,"files":{',
        (New-Object System.Text.UTF8Encoding($false)))
    $out = Invoke-Install
    Assert-Exit 0 'the run survives an unreadable manifest'
    Assert-Match $out 'cannot be read' 'the run says what went wrong'
    Assert-Match $out 'Moved aside to' 'the unreadable file was kept, not deleted'
    Assert-True ((@(Get-ChildItem -LiteralPath $kitHome -Filter '.kit-manifest.json.broken-*' -Force)).Count -eq 1) `
        'the broken record sits beside the new one'
    Assert-Regex $out 'summary\s+\d+ written' 'the run still reached its summary'
    Assert-True ((Get-Content -LiteralPath $skill -Raw).Contains('mine again')) 'my file survived'
    Assert-True (Test-Path -LiteralPath $draft) 'the kit version landed beside it'

    # ------------------------------------------------------- a run that dies -----
    Write-Host "`r`nphase 12, a run that dies mid-plan still leaves a record the rollback can read"
    $onlyCopy = 'my only copy of this file, 200 lines of company rules'
    Set-Content -LiteralPath $skill -Value $onlyCopy -Encoding UTF8
    $stream = [System.IO.File]::Open((Join-Path $kitHome 'statusline.ps1'), 'Open', 'ReadWrite', 'None')
    try { $out = Invoke-Install @('-Force') } finally { $stream.Close() }
    Assert-Exit 1 'the run reports the failure'
    $stamp12 = Get-LastStamp
    Assert-True (Test-Path -LiteralPath (Join-Path (Get-StampDir $stamp12) 'manifest.txt')) `
        'the record exists even though the run never reached its end'
    Assert-ManifestLine $stamp12 'overwritten' $skill 'it names the file that was already overwritten'
    Assert-Match $out 'STOPPED' 'the run says it did not finish'
    Assert-Match $out 'Roll it back with' 'it still prints the rollback command'
    $out = Invoke-Restore @('--stamp', $stamp12)
    Assert-Exit 0 'rolling the half-finished run back succeeds'
    Assert-True ((Get-Content -LiteralPath $skill -Raw).Contains($onlyCopy)) 'my only copy is back'

    # ------------------------------------------------- one folder per run -------
    Write-Host "`r`nphase 13, two runs inside the same second never share a backup folder"
    . (Join-Path $script:RepoRoot 'install\backup.ps1')
    $taken   = Get-KitStamp $kitHome
    $takenAt = Join-Path $kitHome ('backups\' + $taken)
    New-Item -ItemType Directory -Force -Path $takenAt | Out-Null
    Set-Content -LiteralPath (Join-Path $takenAt 'manifest.txt') -Value 'trigger the run before' -Encoding UTF8
    Assert-True ((Get-KitStamp $kitHome) -ne $taken) 'a second run in the same second steps aside'
    Set-Content -LiteralPath $skill -Value 'mine once more' -Encoding UTF8
    $out = Invoke-Install
    Assert-Exit 0 'that run succeeds'
    Assert-True ((Get-Content -LiteralPath (Join-Path $takenAt 'manifest.txt') -Raw).Trim() -eq 'trigger the run before') `
        'the record of the run before is left exactly as it was'

    # ------------------------------------------- every shape of a broken record -
    Write-Host "`r`nphase 14, a record that is not a file map is broken, whatever shape it takes"
    $shapes = [ordered]@{
        'an empty file'                 = ''
        'a BOM and nothing else'        = [char]0xFEFF
        'valid json that is an array'   = '[1, 2]'
        'a files value that is a string' = '{"version":1,"files":"nope"}'
        'a hash that is not a string'   = '{"version":1,"files":{"C:\\x.md":{"sha":1}}}'
        'a record wrapped in an array'  = '[{"version":1,"files":{"C:\\x.md":"abc"}}]'
        'no version at all'             = '{"files":{"C:\\x.md":"abc"}}'
        'a version from another engine' = '{"version":2,"files":{"C:\\x.md":"abc"}}'
        'a path that is not absolute'   = '{"version":1,"files":{"skills\\x.md":"abc"}}'
        'a path with a root but no drive' = '{"version":1,"files":{"\\kit\\x.md":"abc"}}'
        'a path that is a drive and no root' = '{"version":1,"files":{"C:x.md":"abc"}}'
    }
    foreach ($shape in $shapes.GetEnumerator()) {
        Get-ChildItem -LiteralPath $kitHome -Filter '.kit-manifest.json.broken-*' -Force |
            Remove-Item -Force
        [System.IO.File]::WriteAllText($manifestPath, [string]$shape.Value,
            (New-Object System.Text.UTF8Encoding($false)))
        $out = Invoke-Install
        Assert-Exit 0 ('a run over ' + $shape.Key + ' succeeds')
        Assert-Match $out 'cannot be read' ('the run says ' + $shape.Key + ' cannot be read')
        Assert-True ((@(Get-ChildItem -LiteralPath $kitHome -Filter '.kit-manifest.json.broken-*' -Force)).Count -eq 1) `
            ('the copy of ' + $shape.Key + ' is kept beside the new record')
        # Guarded, because a failure here must stay one FAIL line: reading a manifest that is not
        # there would be a terminating error and would take the rest of the suite with it.
        $written = @()
        if (Test-Path -LiteralPath $manifestPath) {
            $written = @(((Get-Content -LiteralPath $manifestPath -Raw) | ConvertFrom-Json).files.PSObject.Properties)
        }
        Assert-True ($written.Count -gt 20) ('a fresh record was written over ' + $shape.Key)
    }
} finally {
    Close-KitSandbox $realProfile
}

Write-TestResult
