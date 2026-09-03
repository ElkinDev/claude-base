#requires -Version 5.1
<#
ask-planner.ps1 - one stateless call to the planning model, for the hours it is rationed.

Reserve mode. The orchestrator pane normally is the planner. When the planning model's weekly
meter is nearly spent, the pane switches its own runtime to a cheaper model and keeps planning
and adjudication on the planning model through short calls like this one: a packet in, a plan
or a verdict out. No session, no tools, no history, one call per invocation and never a retry.

The call runs from a scratch directory made new for it and removed after it. The CLI always
loads the user-level instructions and bills them as cache creation, which the smoke measured at
2940 tokens; a project CLAUDE.md in the working directory would be added to that on every call.

usage: ask-planner.ps1 [-o <answer file>] [<packet file>]
  no packet file, or `-`, reads the packet from stdin. Windows PowerShell rejects a bare dash in
  its own argument binder before -File hands anything to a script, so a caller on that host omits
  the packet rather than writing `-`; the dash reaches this script only through -Command.
  exit 0  the answer is on stdout exactly as the model wrote it, one row is in the ledger
  exit 1  the call failed: the model's own message, or the raw output, is on stderr
  exit 2  usage: an empty packet, a packet over 65536 bytes, a packet file that cannot be read,
          a law file that is not there, an answer file that names a directory or sits under one
          that does not exist, or a ledger that cannot be opened for append. Everything the
          answer needs is checked before the call: one usage line, no call, no ledger row.

Environment, all optional and all with defaults below: PLANNER_MODEL, PLANNER_EFFORT,
PLANNER_LAW (planner-law.md beside this script), PLANNER_LEDGER, PLANNER_BIN. The model and the
effort are environment only on purpose. The planner runs at xhigh or max, and a flag would make
lowering it a typo away. Every path the tool is given or derives, the packet, the answer file, the
law and the ledger, is rooted against the directory the caller stood in, because the call itself
runs somewhere else.

Only `result` reaches stdout, written as raw utf-8 bytes so a two line answer arrives byte for
byte. Everything else the CLI reports goes to the ledger row, whose numbers are formatted with
the invariant culture: a machine with a comma decimal separator would otherwise split a cost
across two csv fields. stdout comes first, then the answer file, then the ledger row. The call is
paid for by then, so the answer reaches the caller whatever the bookkeeping does, and a failed
write is still loud: one line on stderr naming the path, exit 1, and the row goes in anyway with
an empty answer_file, since the call did happen and the ledger is what records what it cost.

Arguments are read from $args rather than a param block, so a packet path that starts with a
dash cannot bind as a switch. The POSIX twin beside this file answers the same contract.
#>
$ErrorActionPreference = 'Stop'

$limit = 65536
$header = 'time,model,effort,packet_bytes,input,cache_create,cache_read,output,' +
          'thinking,duration_ms,cost_usd,answer_file'
$invariant = [System.Globalization.CultureInfo]::InvariantCulture
$utf8 = New-Object System.Text.UTF8Encoding($false)

$model = if ($env:PLANNER_MODEL) { $env:PLANNER_MODEL } else { 'claude-fable-5-1' }
$effort = if ($env:PLANNER_EFFORT) { $env:PLANNER_EFFORT } else { 'xhigh' }
$bin = if ($env:PLANNER_BIN) { $env:PLANNER_BIN } else { 'claude' }
$law = $env:PLANNER_LAW
if (-not $law) { $law = Join-Path $PSScriptRoot 'planner-law.md' }
$ledger = $env:PLANNER_LEDGER
if (-not $ledger) { $ledger = Join-Path $env:USERPROFILE '.claude\ledger\planner-calls.csv' }

function Write-Usage {
    [Console]::Error.WriteLine(
        "usage: ask-planner.ps1 [-o <answer file>] [<packet file>] (packet 1 to $limit bytes, law file must exist)")
    exit 2
}

function Invoke-Fail([string]$text) {
    $message = ''
    if ($null -ne $text) { $message = $text.Trim() }
    if ($message -eq '') { $message = 'the cli returned nothing this tool could read' }
    [Console]::Error.WriteLine($message)
    exit 1
}

function Resolve-FullPath([string]$path) {
    # .NET keeps its own working directory, which is not the one the caller sees, so every path
    # that reaches a file API here is rooted first.
    if ([string]::IsNullOrEmpty($path)) { return $path }
    if (-not [System.IO.Path]::IsPathRooted($path)) {
        $path = Join-Path (Get-Location).ProviderPath $path
    }
    return [System.IO.Path]::GetFullPath($path)
}

function Get-QuotedArgument([string]$value) {
    # CreateProcess takes one command line, not a list, so each word is quoted the way the C
    # runtime parses it back: doubled backslashes before a quote, and "" for an empty word.
    if ($value -eq '') { return '""' }
    if ($value -notmatch '[\s"]') { return $value }
    $escaped = [regex]::Replace($value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Get-Cell($value) {
    # One csv field. Fields are never quoted, so a comma inside one becomes a space.
    if ($null -eq $value -or $value -is [bool]) { return '' }
    if ($value -is [System.IFormattable]) {
        $text = ([System.IFormattable]$value).ToString($null, $invariant)
    } else {
        $text = [string]$value
    }
    foreach ($bad in @(',', "`r", "`n", "`t")) { $text = $text.Replace($bad, ' ') }
    return $text
}

function Read-StandardInput {
    $stream = [Console]::OpenStandardInput()
    $memory = New-Object System.IO.MemoryStream
    $buffer = New-Object byte[] 8192
    while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
        $memory.Write($buffer, 0, $read)
    }
    return $memory.ToArray()
}

# ---------------------------------------------------------------- arguments

$answerFile = ''
$packet = ''
$index = 0
while ($index -lt $args.Count) {
    $word = [string]$args[$index]
    if ($word -eq '-o') {
        if ($index + 1 -ge $args.Count) { Write-Usage }
        $answerFile = [string]$args[$index + 1]
        $index += 2
    } elseif ($word -eq '-') {
        if ($packet -ne '') { Write-Usage }
        $packet = '-'
        $index += 1
    } elseif ($word.StartsWith('-')) {
        Write-Usage
    } else {
        if ($packet -ne '') { Write-Usage }
        $packet = $word
        $index += 1
    }
}

# ---------------------------------------------------------------- the paths

# The call runs from an empty scratch folder, so every path this tool is given or derives is
# rooted here, once, against the directory the caller stood in. A relative law file would
# otherwise be looked for in the scratch folder, which is how a real call on 2026-09-03 lost it.
$law = Resolve-FullPath $law
$ledger = Resolve-FullPath $ledger
if ($answerFile -ne '') { $answerFile = Resolve-FullPath $answerFile }
# A PATH name stays a PATH name. A spelled out one is rooted, because the call runs elsewhere.
if ($bin -match '[\\/]') { $bin = Resolve-FullPath $bin }

# ---------------------------------------------------------------- the packet

if ($packet -eq '' -or $packet -eq '-') {
    $bytes = Read-StandardInput
} else {
    $full = Resolve-FullPath $packet
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { Write-Usage }
    try { $bytes = [System.IO.File]::ReadAllBytes($full) } catch { Write-Usage }
}
if ($null -eq $bytes -or $bytes.Length -eq 0 -or $bytes.Length -gt $limit) { Write-Usage }
if (-not (Test-Path -LiteralPath $law -PathType Leaf)) { Write-Usage }

# ------------------------------------------------------- where the answer goes

# Checked before the call, not after, because the call is money: a caller who mistyped -o, or a
# ledger under a path that cannot hold one, finds out for the price of nothing. The answer file's
# folder has to exist already, since a wrong -o is far likelier than a deliberate new tree, and
# the ledger is opened for append here so that a permission or a path problem is a usage error
# rather than a lost answer.
if ($answerFile -ne '') {
    if (Test-Path -LiteralPath $answerFile -PathType Container) { Write-Usage }
    $answerFolder = Split-Path -Parent $answerFile
    if ($answerFolder -ne '' -and
        -not (Test-Path -LiteralPath $answerFolder -PathType Container)) { Write-Usage }
}
$ledgerFolder = Split-Path -Parent $ledger
if ($ledgerFolder -ne '' -and -not (Test-Path -LiteralPath $ledgerFolder -PathType Container)) {
    try { New-Item -ItemType Directory -Force -Path $ledgerFolder | Out-Null } catch { Write-Usage }
}
if (Test-Path -LiteralPath $ledger -PathType Container) { Write-Usage }
try { [System.IO.File]::AppendAllText($ledger, '', $utf8) } catch { Write-Usage }

# ---------------------------------------------------------------- the call

# A fresh folder for each call, empty because it is new, and gone again in the finally below. The
# working directory has to hold no project CLAUDE.md, since the CLI would load it and bill it as
# cache creation on every call; emptying a shared folder instead would delete files this tool did
# not create and would give two calls at the same time the same directory.
$root = $env:TEMP
if (-not $root) { $root = [System.IO.Path]::GetTempPath() }
$parent = Join-Path $root 'planner-cwd'
if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
}
$scratch = Join-Path $parent ($PID.ToString() + '-' + [System.IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Force -Path $scratch | Out-Null

try {

    $callArgs = @('--print', '--model', $model, '--effort', $effort, '--tools', '',
                  '--no-session-persistence', '--permission-mode', 'dontAsk',
                  '--output-format', 'json', '--system-prompt-file', $law)

    $executable = $bin
    $resolved = Get-Command -Name $bin -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($resolved) { $executable = $resolved.Source }

    $quoted = @()
    foreach ($word in $callArgs) { $quoted += (Get-QuotedArgument ([string]$word)) }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $extension = [System.IO.Path]::GetExtension($executable)
    if ($extension -eq '.cmd' -or $extension -eq '.bat') {
        # The CLI is a batch shim on most Windows installs, and CreateProcess runs only real
        # executables. /s makes cmd strip exactly the outer pair of quotes and pass the rest on.
        $psi.FileName = $env:ComSpec
        $psi.Arguments = '/d /s /c "' + ((@((Get-QuotedArgument $executable)) + $quoted) -join ' ') + '"'
    } else {
        $psi.FileName = $executable
        $psi.Arguments = $quoted -join ' '
    }
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    # stderr stays inherited: whatever the CLI says about itself reaches the caller unchanged.
    $psi.RedirectStandardError = $false
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.WorkingDirectory = $scratch

    try {
        $process = [System.Diagnostics.Process]::Start($psi)
    } catch {
        Invoke-Fail ("cannot run " + $executable + ": " + $_.Exception.Message)
    }

    # The read is started before the packet is written. A child that answers while it is still being
    # fed would otherwise fill its own stdout pipe, stop, and never drain the rest of the packet,
    # leaving both sides waiting on a buffer the other one holds.
    $reader = $process.StandardOutput.ReadToEndAsync()
    $stdin = $process.StandardInput.BaseStream
    $stdin.Write($bytes, 0, $bytes.Length)
    $stdin.Flush()
    $stdin.Close()
    $raw = $reader.GetAwaiter().GetResult()
    $process.WaitForExit()
    $status = $process.ExitCode

    # ---------------------------------------------------------------- the answer

    $envelope = $null
    try { $envelope = ConvertFrom-Json $raw } catch { $envelope = $null }
    if (-not ($envelope -is [System.Management.Automation.PSCustomObject])) { Invoke-Fail $raw }

    $result = $envelope.result
    $failed = ($status -ne 0) -or ($envelope.subtype -ne 'success') -or ($result -isnot [string])
    if ($null -ne $envelope.is_error -and [bool]$envelope.is_error) { $failed = $true }
    if ($failed) {
        if ($result -is [string] -and $result.Trim() -ne '') { Invoke-Fail $result }
        Invoke-Fail $raw
    }

    # stdout first, then the answer file, then the row. The call is already paid for by the time any
    # of this runs, so the answer reaches the caller before any bookkeeping can fail on it.
    $answerBytes = [System.Text.Encoding]::UTF8.GetBytes($result)
    $stdout = [Console]::OpenStandardOutput()
    $stdout.Write($answerBytes, 0, $answerBytes.Length)
    $stdout.Flush()

    # The folder was there before the call and the file is not created here for the first time by
    # luck: if the write fails now, something took the place away mid call, so the row still goes in
    # with an empty answer_file and says the call happened and the answer is not on disk.
    $lost = $false
    if ($answerFile -ne '') {
        try {
            [System.IO.File]::WriteAllText($answerFile, $result, $utf8)
        } catch {
            [Console]::Error.WriteLine(
                "cannot write the answer file " + $answerFile + ": " + $_.Exception.Message)
            $answerFile = ''
            $lost = $true
        }
    }

    # ---------------------------------------------------------------- the ledger

    $usage = $envelope.usage
    $details = $null
    if ($null -ne $usage) { $details = $usage.output_tokens_details }
    $input_tokens = $null
    $cache_create = $null
    $cache_read = $null
    $output_tokens = $null
    $thinking = $null
    if ($null -ne $usage) {
        $input_tokens = $usage.input_tokens
        $cache_create = $usage.cache_creation_input_tokens
        $cache_read = $usage.cache_read_input_tokens
        $output_tokens = $usage.output_tokens
    }
    if ($null -ne $details) { $thinking = $details.thinking_tokens }

    # Built one cell at a time rather than as an array literal: a $null in a literal would collapse
    # and shift every field after it into the wrong column.
    $cells = @()
    $cells += (Get-Cell ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss', $invariant)))
    $cells += (Get-Cell $model)
    $cells += (Get-Cell $effort)
    $cells += (Get-Cell $bytes.Length)
    $cells += (Get-Cell $input_tokens)
    $cells += (Get-Cell $cache_create)
    $cells += (Get-Cell $cache_read)
    $cells += (Get-Cell $output_tokens)
    $cells += (Get-Cell $thinking)
    $cells += (Get-Cell $envelope.duration_api_ms)
    $cells += (Get-Cell $envelope.total_cost_usd)
    $cells += (Get-Cell $answerFile)

    try {
        $fresh = $true
        if (Test-Path -LiteralPath $ledger) {
            $fresh = ((Get-Item -LiteralPath $ledger).Length -eq 0)
        }
        # One newline style in both halves, so a machine that runs each of them at different hours
        # reads one file rather than two shapes of it.
        $text = ''
        if ($fresh) { $text = $header + "`n" }
        $text += ($cells -join ',') + "`n"
        [System.IO.File]::AppendAllText($ledger, $text, $utf8)
    } catch {
        Invoke-Fail ("cannot append the ledger row to " + $ledger + ": " + $_.Exception.Message)
    }

    if ($lost) { exit 1 }

    exit 0
} finally {
    # The working directory of the call is this tool's to remove, on every way out: a
    # normal exit, a failed call, or an error nothing here expected.
    Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
}
