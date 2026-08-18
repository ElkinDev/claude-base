# worklog-resolve.ps1 - prints the worklog paths for the current repo/branch as JSON.
# The /worklog skill runs this so the model never has to re-derive the repo-id by hand.
$ErrorActionPreference = 'SilentlyContinue'
. "$PSScriptRoot\worklog-lib.ps1"
$c = Get-WorklogContext
if (-not $c) { Write-Output '{}'; exit 0 }
Write-Output ($c | ConvertTo-Json -Compress -Depth 5)
exit 0
