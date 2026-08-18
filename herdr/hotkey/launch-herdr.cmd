@echo off
rem launch-herdr.cmd -> open herdr in its own Windows Terminal window.
rem Usage: launch-herdr.cmd [working-directory]
rem   No arg -> opens in your user profile folder. Change the default below to your main repo.
setlocal
set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=%USERPROFILE%"
wt -w herdr new-tab --title herdr -d "%TARGET%" cmd /k herdr
endlocal
