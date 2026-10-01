@echo off
rem cc: switch Claude Code accounts (claude-base docs/ACCOUNTS.md). The claude-base installer copies this file
rem into <kit home>\bin and puts that folder on the user PATH. The launcher is found beside this folder, from
rem this file's own location, so the file names no path and stays ASCII whatever the user name.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\cc-launch.ps1" %*
set "CC_EXIT=%ERRORLEVEL%"
rem Started through cmd /c, which is how PowerShell and the Run box start a .cmd, cmd may have split a word
rem holding & or | off the line into a command of its own. Ending cmd here means that tail never runs. An
rem interactive Command Prompt is left open.
setlocal EnableDelayedExpansion
set "CC_LINE=!cmdcmdline!"
set "CC_REST=!CC_LINE:/c =!"
if not "!CC_LINE!"=="!CC_REST!" exit !CC_EXIT!
exit /b !CC_EXIT!
