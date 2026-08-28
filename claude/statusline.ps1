# ============================================================
#  Claude Code status line (Windows / PowerShell 5.1)
#  model - effort - account - folder - git (branch + lines +/-) - context - weekly quota
#
#  Drop this file in ~/.claude/ and wire it up in ~/.claude/settings.json.
#  Claude Code needs a literal path there, so put your own in place of <YOUR-USER>:
#    "statusLine": { "type": "command",
#      "command": "powershell -NoProfile -ExecutionPolicy Bypass -File C:/Users/<YOUR-USER>/.claude/statusline.ps1" }
#
#  Privacy: nothing personal is baked into this file. Every path is resolved at run
#  time from $env:USERPROFILE. It reads two files on your own machine and prints to
#  your own terminal: ~/.claude.json, only to pull the local part of the account
#  e-mail for the account label, and the optional companion file described below.
# ============================================================
$ErrorActionPreference = 'SilentlyContinue'

# ---- read the JSON Claude Code sends on stdin ----
$raw = ($input | Out-String)
if ([string]::IsNullOrWhiteSpace($raw)) { try { $raw = [Console]::In.ReadToEnd() } catch {} }
try { $j = $raw | ConvertFrom-Json } catch { return }

# ---- icons by code point, so the file encoding never breaks them ----
$IDir = [char]::ConvertFromUtf32(0x1F4C1)   # folder

# One animal per model family, to tell them apart at a glance. Matched against
# display_name, which arrives as "Opus 5", "Sonnet 5", "Haiku 4.5", "Fable 5".
# Anything unrecognised falls back to the wolf.
function Get-ModelIcon($name) {
  switch -Regex ($name) {
    'fable'  { return [char]::ConvertFromUtf32(0x1F981) }   # lion
    'sonnet' { return [char]::ConvertFromUtf32(0x1F430) }   # hare
    'haiku'  { return [char]::ConvertFromUtf32(0x1F42D) }   # mouse
    default  { return [char]::ConvertFromUtf32(0x1F43A) }   # wolf, Opus included
  }
}

# ---- which account this session is running as ----
# Derived from CLAUDE_CONFIG_DIR, which the process inherits from Claude Code. Without
# that variable we are in ~\.claude, the default config directory.
#
# The display name and icon can come from an OPTIONAL companion file written by a
# multi-account switcher. These four constants are the only thing to touch if your
# switcher writes a different layout; everything below is generic.
$AccountsDir  = '.claude-accounts'   # folder under %USERPROFILE% holding the profiles
$AccountsFile = '.names.json'        # companion file inside it
$KeyDefault   = 'default'            # key holding the display name of ~\.claude
$KeyIcons     = 'icons'              # key holding "<profile name>": "<hex code point>"
$UsageLogDir  = ''                   # set a folder to append quota changes to usage-log.csv there
#
# Expected shape:
#     { "<KeyDefault>": "<display name for ~\.claude>",
#       "<KeyIcons>":   { "<profile name>": "<hex code point>" } }
# Without that file the script still works: it shows the profile directory name, or
# for the default directory the local part of the account e-mail.
function Get-AccountInfo {
  $cd   = $env:CLAUDE_CONFIG_DIR
  $base = Join-Path $env:USERPROFILE $AccountsDir

  # canonical target, based on where the variable points
  if ([string]::IsNullOrWhiteSpace($cd)) { $target = $KeyDefault }
  elseif ($cd.TrimEnd('\') -eq "$env:USERPROFILE\.claude-local") { $target = 'local' }
  else { $target = Split-Path $cd.TrimEnd('\') -Leaf }

  $cfg = $null
  $cfgPath = Join-Path $base $AccountsFile
  if (Test-Path $cfgPath) { try { $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json } catch {} }

  # Full name, never a one letter shortcut. This line is read out of the corner of
  # your eye, and a single letter says nothing there.
  $name = $null
  if ($target -eq $KeyDefault) {
    if ($cfg -and $cfg.$KeyDefault) { $name = [string]$cfg.$KeyDefault }
    else {
      # With no name of its own, the default directory does not say which account it is. Pull the
      # e-mail out of .claude.json with a regex, never with ConvertFrom-Json: the file
      # is ~63 KB, and PowerShell 5.1 also blows up there on keys that differ only in
      # case (c:/code and C:/code, which JSON allows and these files really contain).
      # Measured at 1.5 ms, against 27 ms for a single one of the git calls below.
      try {
        $txt = Get-Content "$env:USERPROFILE\.claude.json" -Raw
        if ($txt -match '"emailAddress"\s*:\s*"([^"@]+)') { $name = $Matches[1] }
      } catch {}
    }
  }
  if (-not $name) { $name = $target }

  # Icon stored as a hex code point, so it does not depend on the JSON file encoding.
  # With no companion file the default account gets the star, so a fresh install looks
  # deliberate instead of half configured. Any other profile falls back to a bust.
  $hex = $null
  if ($cfg -and $cfg.$KeyIcons -and $cfg.$KeyIcons.PSObject.Properties[$target]) { $hex = [string]$cfg.$KeyIcons.$target }
  if (-not $hex) { $hex = if ($target -eq $KeyDefault) { '2B50' } else { '1F464' } }
  $icon = ''
  try { $icon = [char]::ConvertFromUtf32([Convert]::ToInt32($hex, 16)) } catch {}

  return @{ name = $name; icon = $icon; isLocal = ($target -eq 'local') }
}

# ---- ANSI colour helper ----
function C([string]$code, [string]$text) { ([char]27) + '[' + $code + 'm' + $text + ([char]27) + '[0m' }

# ---- fields from the JSON ----
$model  = [string]$j.model.display_name
$effort = [string]$j.effort.level
$cwd    = [string]$j.workspace.current_dir
if (-not $cwd) { $cwd = [string]$j.cwd }
$dir    = if ($cwd) { Split-Path $cwd -Leaf } else { '' }

$pctRaw = $j.context_window.used_percentage
if ($null -eq $pctRaw) { $pct = 0 } else { $pct = [int][math]::Floor([double]$pctRaw) }
if ($pct -lt 0) { $pct = 0 } elseif ($pct -gt 100) { $pct = 100 }

# ---- git: branch, dirty marker (*), lines added/removed against HEAD ----
$branch = ''; $dirty = ''; $add = 0; $del = 0; $isRepo = $false
if ($cwd) {
  $branch = (& git -C "$cwd" rev-parse --abbrev-ref HEAD 2>$null | Select-Object -First 1)
  if ($branch) {
    $isRepo = $true
    if (& git -C "$cwd" status --porcelain 2>$null) { $dirty = '*' }
    foreach ($ln in @(& git -C "$cwd" diff HEAD --numstat 2>$null)) {
      $cols = ([string]$ln) -split "`t"
      if ($cols.Count -ge 2) {
        $a = 0; $r = 0
        [void][int]::TryParse($cols[0], [ref]$a)
        [void][int]::TryParse($cols[1], [ref]$r)
        $add += $a; $del += $r
      }
    }
  }
}

# ---- build the line ----
$segs = @()
$segs += C '95;1' ("$(Get-ModelIcon $model) $model")    # model (magenta, bold)
if ($effort) { $segs += C '96' $effort }                # effort (cyan): low/medium/high/xhigh/max

# Active account. Green for the local model, which burns no account quota at all.
$acct = Get-AccountInfo
if ($acct.name) {
  $colAcct = if ($acct.isLocal) { '92' } else { '33' }
  $segs += C $colAcct ("$($acct.icon) $($acct.name)")
}

if ($dir) { $segs += C '94' ("$IDir $dir") }            # folder (blue)
if ($isRepo) {
  $b = C '95' $branch                                   # branch (magenta)
  if ($dirty) { $b += C '93' '*' }                      # dirty (yellow)
  $segs += $b + '  ' + (C '92' "+$add") + ' ' + (C '91' "-$del")   # + green, - red
}

# ---- context bar (green <70, yellow 70-89, red 90+) ----
$w = 10
$filled = [int][math]::Floor($pct * $w / 100)
if ($filled -gt $w) { $filled = $w } elseif ($filled -lt 0) { $filled = 0 }
$barcol = if ($pct -ge 90) { '91' } elseif ($pct -ge 70) { '93' } else { '92' }
$fill  = '=' * $filled
$empty = '.' * ($w - $filled)
# The window size is the one Claude Code reports for this session (200k or 1M), so the
# number after the slash says which window the session really runs with.
$ctxSize = ''
try { $cw = $j.context_window.context_window_size; if ($cw) { $ctxSize = '/' + [int]([double]$cw / 1000) + 'k' } } catch {}
$segs += "ctx [" + (C $barcol $fill) + (C '90' $empty) + "] $pct%$ctxSize"

# ---- weekly quota: how much is LEFT, not how much you have burned ----
# rate_limits only shows up for claude.ai subscriptions, and only after the first API
# response of the session, so either window may be missing. With no data, show nothing
# rather than a misleading zero.
$used7d = $j.rate_limits.seven_day.used_percentage
if ($null -ne $used7d) {
  $left = 100 - [int][math]::Floor([double]$used7d)
  if ($left -lt 0) { $left = 0 } elseif ($left -gt 100) { $left = 100 }
  $colLeft = if ($left -le 15) { '91' } elseif ($left -le 40) { '93' } else { '92' }
  # The word "left" is not decoration. Without it "7d 100%" reads as an exhausted quota
  # when it means an untouched one, and colour alone is too weak a hint for that.
  $txt = "7d " + $left + "% left"
  # The reset time only matters once you are running low. Before that it is noise.
  $reset = $j.rate_limits.seven_day.resets_at
  if (($left -le 40) -and ($null -ne $reset)) {
    try {
      $hours = [int][math]::Ceiling(([datetimeoffset]::FromUnixTimeSeconds([int64]$reset) - [datetimeoffset]::UtcNow).TotalHours)
      if ($hours -gt 0) {
        if ($hours -ge 48) { $txt += " " + [int][math]::Floor($hours / 24) + "d" }
        else { $txt += " " + $hours + "h" }
      }
    } catch {}
  }
  $segs += C $colLeft $txt
}

# ---- 5-hour session window, same convention: what is LEFT ----
$used5h = $j.rate_limits.five_hour.used_percentage
if ($null -ne $used5h) {
  $left5 = 100 - [int][math]::Floor([double]$used5h)
  if ($left5 -lt 0) { $left5 = 0 } elseif ($left5 -gt 100) { $left5 = 100 }
  $col5 = if ($left5 -le 15) { '91' } elseif ($left5 -le 40) { '93' } else { '92' }
  $segs += C $col5 ("5h " + $left5 + "% left")
}

# ---- usage log: one CSV line per change of the quota pair, so a nightly ledger can read the
# quota without spending a request. Off unless $UsageLogDir is set above. ----
try {
  if ($UsageLogDir -and ($null -ne $used7d -or $null -ne $used5h)) {
    if (-not (Test-Path -LiteralPath $UsageLogDir)) { New-Item -ItemType Directory -Path $UsageLogDir | Out-Null }
    $logFile = Join-Path $UsageLogDir 'usage-log.csv'
    $acctName = if ($acct -and $acct.name) { $acct.name } else { 'default' }
    $ctxPct = if ($null -ne $pct) { [int]$pct } else { -1 }
    $pair = "$acctName,$([int][math]::Floor([double]($used5h -as [double]))),$([int][math]::Floor([double]($used7d -as [double])))"
    $last = if (Test-Path -LiteralPath $logFile) { Get-Content -LiteralPath $logFile -Tail 1 } else { '' }
    $lastPair = if ($last) { ($last -split ',')[1..3] -join ',' } else { '' }
    if ($lastPair -ne $pair) {
      if (-not (Test-Path -LiteralPath $logFile)) { [IO.File]::WriteAllText($logFile, "time,account,five_hour_used,seven_day_used,context_pct`n") }
      [IO.File]::AppendAllText($logFile, ((Get-Date).ToString('yyyy-MM-dd HH:mm') + ",$pair,$ctxPct`n"))
    }
  }
} catch {}

$line = ($segs -join '  ')

# ---- raw UTF-8 output, so emoji survive even when stdout is redirected ----
$bytes = [System.Text.Encoding]::UTF8.GetBytes($line)
$out = [Console]::OpenStandardOutput()
$out.Write($bytes, 0, $bytes.Length)
$out.Flush()
