# Launch a detached Git Bash command with no console window at all, and print its pid.
#
# Why: where Windows Terminal is the default console host, a console process started with Start-Process
# gets a Windows Terminal window even under -WindowStyle Hidden or Minimized, so every detached build or
# test run left a window on the desktop. CreateNoWindow starts the process with no console, so no window
# exists to hide.
#
# The command's output goes to the log file through bash itself (> log 2>&1 < /dev/null), never through
# .NET pipes: the PowerShell that launches a run is usually a tool shell that exits at once, and a pipe
# whose reader is gone kills the writer with SIGPIPE. Whatever exit file or marker the command writes
# works exactly as if it had been started by hand.
#
# Usage:
#   powershell -NoProfile -File scripts/gate-detach.ps1 -Command 'cd /c/work/repo && exec bash scripts/run-gate.sh tag' -Log /c/work/repo/gates/tag.launch.log
#
# -Command is a bash command line. -Log is a POSIX path (it is used inside the bash command line).
# The length bound: Git's bin/bash.exe is a launcher that exits 2 and writes nothing when its command
# line passes about 8200 characters (an argument of 8192 starts, 8270 exits 2). A command whose bash
# argument would pass 8000 characters is refused with a line naming the bound (exit 2) and nothing
# starts, so a too-long launch fails loudly instead of leaving no log at all. Put a long list in a file
# the command reads back, or in a script the command runs.
# There is no -Lockrun form here: that form wrapped a gradle lockrun script that belongs to one
# project and not to this kit; a project that has one builds its command line and passes it as -Command.
# Prints the pid; exit 2 when bash.exe is missing, the arguments are refused, or the process did not start.
# -Bash defaults to GATE_DETACH_BASH when that variable is set, else Git for Windows' bash.exe.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Command,
    [Parameter(Mandatory = $true)][string]$Log,
    [string]$Bash = $(if ($env:GATE_DETACH_BASH) { $env:GATE_DETACH_BASH } else { 'C:\Program Files\Git\bin\bash.exe' })
)
if (-not (Test-Path $Bash)) { Write-Error "bash not found at $Bash"; exit 2 }
$argLimit = 8000
function Get-BashArgs([string]$cmd, [string]$log) {
    # -l for the login environment, -c for the command. Double quotes inside the command are escaped for
    # the Windows command line that bash.exe parses.
    $inner = '( ' + $cmd + ' ) > "' + $log + '" 2>&1 < /dev/null'
    return '-lc "' + ($inner -replace '"', '\"') + '"'
}
$bashArgs = Get-BashArgs $Command $Log
if ($bashArgs.Length -gt $argLimit) { Write-Error ("the bash command line is {0} characters, above the {1} that Git's bash.exe launcher starts (it exits 2 and writes nothing above about 8200); shorten -Command or move the long part into a file or a script" -f $bashArgs.Length, $argLimit); exit 2 }
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $Bash
$psi.Arguments = $bashArgs
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.WorkingDirectory = (Get-Location).Path
$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi
try { [void]$proc.Start() } catch { Write-Error "start failed: $($_.Exception.Message)"; exit 2 }
Write-Output $proc.Id
