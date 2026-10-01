@echo off
rem cc: switch Claude Code accounts (claude-base docs/ACCOUNTS.md). The claude-base installer copies this file
rem into <kit home>\bin and puts that folder on the user PATH; the launcher sits one folder up. %~dp0 names this
rem file's folder, except when cmd found it on PATH under a quoted name, where it names the current folder; then
rem the folder of the first cc.cmd on PATH stands in. The file names no path, so it stays ASCII.
rem cmd reads the whole line before this file runs, as for any .cmd command: docs/ACCOUNTS.md says what that
rem means for a word holding & | < > ^ or %.
setlocal
set "CC_LAUNCH=%~dp0..\cc-launch.ps1"
if not exist "%CC_LAUNCH%" for %%I in (cc.cmd) do set "CC_LAUNCH=%%~dp$PATH:I..\cc-launch.ps1"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%CC_LAUNCH%" %*
exit /b %ERRORLEVEL%
