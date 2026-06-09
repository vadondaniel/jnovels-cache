@echo off
cd /d "%~dp0"
python server.py --open
if errorlevel 1 (
  echo.
  echo Could not start JNovels Cache. Make sure Python 3 is installed.
  pause
)
