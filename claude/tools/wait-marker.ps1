#requires -Version 5.1
<#
wait-marker.ps1 - wait for a marker file without letting the wait outlive the prompt cache.

Why 270. The prompt cache entry lives five minutes and the window slides: every call that
returns inside it renews the entry, and the next call reads the cached prefix instead of
writing it again at the 1.25 rate. One call that blocks longer than five minutes lets the
entry expire, so the turn after it pays a full context write, and a lane that waits in ten
minute sleeps pays that on every loop. 270 seconds is the five minutes minus thirty for the
round trip, which is why it is both the default and the ceiling: a caller that needs longer
calls this again on exit 3 instead of asking for one longer sleep.

usage: wait-marker.ps1 <file> [seconds]
  exit 0  the file is there, nothing printed
  exit 2  missing or non-numeric argument, usage on stderr
  exit 3  the seconds ran out, one line on stderr

The POSIX twin beside this file answers the same contract. Arguments are read from $args
rather than a param block, so a marker path that starts with a dash cannot bind as a switch.
#>
$ErrorActionPreference = 'Stop'
$ceiling = 270
$poll = 5

function Write-Usage {
    [Console]::Error.WriteLine('usage: wait-marker.ps1 <file> [seconds]')
    exit 2
}

if ($args.Count -lt 1 -or $args.Count -gt 2) { Write-Usage }

$file = [string]$args[0]
if ([string]::IsNullOrWhiteSpace($file)) { Write-Usage }

$seconds = [long]$ceiling
if ($args.Count -eq 2) {
    $raw = [string]$args[1]
    $parsed = [long]0
    # The regex rejects a sign, a decimal point and anything else non-numeric. A run of digits
    # too long to hold is a number, not a usage error, and it is over the ceiling by definition,
    # so it clamps like any other oversized wait instead of overflowing the cast.
    if ($raw -notmatch '^[0-9]+$') { Write-Usage }
    $seconds = if ([long]::TryParse($raw, [ref]$parsed)) { $parsed } else { [long]$ceiling + 1 }
}

if ($seconds -gt $ceiling) {
    [Console]::Error.WriteLine("clamped to $ceiling s: no single wait may outlive the prompt cache")
    $seconds = [long]$ceiling
}
$seconds = [int]$seconds

$elapsed = 0
while ($true) {
    if (Test-Path -LiteralPath $file) { exit 0 }
    if ($elapsed -ge $seconds) {
        [Console]::Error.WriteLine("timeout after $seconds s: $file")
        exit 3
    }
    $step = [Math]::Min($poll, $seconds - $elapsed)
    Start-Sleep -Seconds $step
    $elapsed += $step
}
