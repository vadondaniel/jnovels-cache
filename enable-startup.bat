@echo off
start "" wscript.exe //B "%~dp0launch-hidden.vbs" --sta --wait "%~dp0startup.ps1" -Action Enable
exit /b
