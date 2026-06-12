@echo off
start "" wscript.exe //B "%~dp0launch-hidden.vbs" --sta "%~dp0tray.ps1"
exit /b
