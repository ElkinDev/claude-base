# cc-launch.ps1 - what cc.cmd runs: it hands its arguments to claude-account.ps1 as if they were typed in PowerShell.
#
# The installer writes cc.cmd into <kit home>\bin and puts that folder on the user PATH. cc.cmd starts Windows
# PowerShell by its full path with -ExecutionPolicy Bypass -File on this script, so cc works in the Command Prompt
# and in any PowerShell window, on a machine whose execution policy refuses scripts, with no line in a PowerShell
# profile. It cannot start claude-account.ps1 itself with -File: powershell.exe -File reads the -- separator as a
# parameter with an empty name, and cc work -- -r stops on "the parameter name '' is ambiguous". This script declares
# no parameters, so every argument reaches it as plain text, and it rebuilds the command for the PowerShell parser: the
# separator and every flag go bare, every other argument goes single-quoted, so a path with a space or a $ arrives
# as it was typed. PowerShell also closes a single-quoted string on the typographic quotes, so each of those is
# doubled as well.
function ConvertTo-CcQuoted {
    param([string]$Text)
    return "'" + ($Text -replace "['‘’‚‛]", '$0$0') + "'"
}
$account = Join-Path $PSScriptRoot 'claude-account.ps1'
$words = @(foreach ($word in $args) {
    $text = [string]$word
    if ($text -match '^(--|-[A-Za-z][A-Za-z0-9]*)$') { $text } else { ConvertTo-CcQuoted $text }
})
& ([scriptblock]::Create('& ' + (ConvertTo-CcQuoted $account) + ' ' + ($words -join ' ')))
exit $LASTEXITCODE
