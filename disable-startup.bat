@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0startup.ps1" -Action Disable
if errorlevel 1 (
  echo.
  echo Could not disable startup.
)
pause
