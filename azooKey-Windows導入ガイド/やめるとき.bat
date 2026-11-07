@echo off
rem Undo what the setup helper did: remove azooKey from the keyboard list,
rem delete the auto-start task, and stop the engine (double-click to run).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_helper.ps1" -Uninstall
echo.
pause
