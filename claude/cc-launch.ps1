# cc-launch.ps1 - what cc.cmd runs: it hands its arguments to claude-account.ps1 as if they were typed in PowerShell.
#
# The installer writes cc.cmd into <kit home>\bin and puts that folder on the user PATH. cc.cmd starts Windows
# PowerShell by its full path with -ExecutionPolicy Bypass -File on this script, so cc works in the Command Prompt
# and in any PowerShell window on a machine whose local execution policy refuses scripts, with no line in a
# PowerShell profile. It cannot start claude-account.ps1 itself with -File: powershell.exe -File reads the --
# separator as a parameter with an empty name, and cc work -- -r stops on "the parameter name '' is ambiguous".
# This script declares no parameters, so every argument reaches it as plain text, and it rebuilds the command for
# the PowerShell parser: the separator and every flag go bare, every other word goes single-quoted.
#
# This file stays ASCII. Windows PowerShell 5.1 reads a script with no BOM in the ANSI code page, so a typographic
# quote written here would be read as other characters. PowerShell also closes a single-quoted string on the four
# typographic single quotes, so each of them is doubled inside a word, built from its code point.
$quotes = @([char]0x27, [char]0x2018, [char]0x2019, [char]0x201A, [char]0x201B)
function ConvertTo-CcQuoted {
    param([string]$Text)
    $out = New-Object System.Text.StringBuilder
    foreach ($ch in $Text.ToCharArray()) {
        [void]$out.Append($ch)
        if ($quotes -contains $ch) { [void]$out.Append($ch) }
    }
    return "'" + $out.ToString() + "'"
}
$account = Join-Path $PSScriptRoot 'claude-account.ps1'
$words = @(foreach ($word in $args) {
    $text = [string]$word
    if ($text -cmatch '^(--|-[A-Za-z][A-Za-z0-9]*)$') { $text } else { ConvertTo-CcQuoted $text }
})
# A command that cannot be parsed or that throws is a failure, never exit 0.
try {
    & ([scriptblock]::Create('& ' + (ConvertTo-CcQuoted $account) + ' ' + ($words -join ' ')))
} catch {
    Write-Host "  FAILED: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
exit $LASTEXITCODE
