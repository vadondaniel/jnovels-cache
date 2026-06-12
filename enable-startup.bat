@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0startup.ps1" -Action Enable
if errorlevel 1 (
  echo.
  echo Could not enable startup.
)
pause
